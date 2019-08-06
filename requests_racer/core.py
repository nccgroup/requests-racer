# Requests-Racer, a Python library for synchronizing HTTP requests.
# Copyright (C) 2019 Aleksejs Popovs, NCC Group
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

# This file contains modified parts of code from the Requests library,
# in particular the file requests/adapters.py as of commit
# 7fd9267b3bab1d45f5e4ac0953629c5531ecbc55. The Requests library is
# available at https://github.com/psf/requests.
# The Requests code (but not the modifications present in Requests-Racer)
# is distributed under the following license:
#
# [BEGIN REQUESTS LICENSE]
# Copyright 2018 Kenneth Reitz
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
# [END REQUESTS LICENSE]

import socket
import sys
import threading
import time
import traceback

from urllib3.poolmanager import PoolManager
from urllib3.response import HTTPResponse
from urllib3.util import Timeout as TimeoutSauce
from urllib3.exceptions import ClosedPoolError
from urllib3.exceptions import ConnectTimeoutError
from urllib3.exceptions import HTTPError as _HTTPError
from urllib3.exceptions import MaxRetryError
from urllib3.exceptions import NewConnectionError
from urllib3.exceptions import ProxyError as _ProxyError
from urllib3.exceptions import ProtocolError
from urllib3.exceptions import ReadTimeoutError
from urllib3.exceptions import SSLError as _SSLError
from urllib3.exceptions import ResponseError
from urllib3.exceptions import LocationValueError

from requests.adapters import DEFAULT_POOL_TIMEOUT, HTTPAdapter
from requests.cookies import extract_cookies_to_jar
from requests.exceptions import (
    ConnectionError, ConnectTimeout, ReadTimeout, SSLError,
    ProxyError, RetryError, InvalidURL,
)
from requests.models import Response
from requests.structures import CaseInsensitiveDict
from requests.sessions import Session
from requests.utils import get_encoding_from_headers


def chunk(l, num_chunks):
    """
    Splits l into num_chunks evenly-sized chunks.

    >>> chunk([1, 2, 3, 4, 5, 6], 4)
    [[1, 2], [3, 4], [5], [6]]
    """
    chunk_size = len(l) // num_chunks
    # bigger_chunks is the number of initial chunks that will have to have
    # size (chunk_size + 1)
    bigger_chunks = len(l) % num_chunks

    return [
        (l[(chunk_size+1)*i:(chunk_size+1)*(i+1)]
         if (i < bigger_chunks)
         else l[chunk_size*i+bigger_chunks:chunk_size*(i+1)+bigger_chunks])
        for i in range(num_chunks)
    ]


class SynchronizedAdapter(HTTPAdapter):
    """A custom adapter for Requests that lets the user submit multiple
    requests that will be processed by their destination servers at
    approximately the same time.

    This is accomplished by sending most, but not all, of the request when
    `.send()` is called, and then exposing a `.finish_all()` method that
    finishes all of the pending requests. This relies on the fact that most web
    servers only start processing a request after it has been received
    completely.

    The fact that `.send()` does not finish the request breaks Requests'
    expectations somewhat, because it expects `.send()` to return the server's
    response. `SynchronizedAdapter` instead returns a dummy response object
    with status code 998. This object is then updated with the actual response
    after `.finish_all()` is called.
    """
    def __init__(self, num_threads=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._pending_requests = []
        self.num_threads = num_threads

    def send(self, request, stream=False, timeout=None, verify=True, cert=None,
             proxies=None):
        """Follows `requests.HTTPAdapter.send()`, but does not send the last
        couple of bytes, instead storing them in `self._pending_requests`.
        """

        # TODO: ensure no pooling happens here
        try:
            conn = self.get_connection(request.url, proxies)
        except LocationValueError as e:
            raise InvalidURL(e, request=request)

        self.cert_verify(conn, request.url, verify, cert)
        url = self.request_url(request, proxies)
        self.add_headers(request, stream=stream, timeout=timeout,
                         verify=verify, cert=cert, proxies=proxies)

        if isinstance(timeout, tuple):
            try:
                connect, read = timeout
                timeout = TimeoutSauce(connect=connect, read=read)
            except ValueError as e:
                # this may raise a string formatting error.
                err = ("Invalid timeout {}. Pass a (connect, read) "
                       "timeout tuple, or a single float to set "
                       "both timeouts to the same value".format(timeout))
                raise ValueError(err)
        elif isinstance(timeout, TimeoutSauce):
            pass
        else:
            timeout = TimeoutSauce(connect=timeout, read=timeout)

        # this is what we will return for now. this object will later be
        # overwritten after the request is finished.
        response = Response()
        self.build_dummy_response_into(response, request)

        try:
            # Send the request.
            if hasattr(conn, 'proxy_pool'):
                conn = conn.proxy_pool

            low_conn = conn._get_conn(timeout=DEFAULT_POOL_TIMEOUT)

            try:
                low_conn.putrequest(request.method,
                                    url,
                                    skip_accept_encoding=True)

                for header, value in request.headers.items():
                    low_conn.putheader(header, value)

                if request.body is None:
                    # no body

                    # MASSIVE HACK ALERT:
                    # at this point, *no* information has been sent to the
                    # server---it is buffered and will only be sent after we
                    # call low_conn.endheaders(). however, this request has no
                    # body, so calling low_conn.endheaders() would actually
                    # cause the request to be processed by the server
                    # immediately. endheaders() is the only public way to get
                    # the connection into a 'sent' state, which is the only
                    # state in which you are allowed to read the response. so
                    # we dig into the internals of the HTTPConnection object to
                    # get it into the 'sent' state and send *most* of its
                    # internal buffer without sending the two final newlines.

                    # this follows HTTPConnection._send_output
                    msg = b"\r\n".join(low_conn._buffer)
                    del low_conn._buffer[:]
                    low_conn.send(msg)

                    low_conn._HTTPConnection__state = 'Request-sent'

                    self._pending_requests.append(
                        (request, low_conn, b'\r\n\r\n', response)
                    )
                else:
                    # some body, can end headers now
                    low_conn.endheaders()

                    body = request.body
                    if isinstance(body, str):
                        body = body.encode('utf-8')

                    if 'Content-Length' in request.headers:
                        # single body
                        low_conn.send(body[:-3])
                        self._pending_requests.append(
                            (request, low_conn, body[-3:], response)
                        )
                    else:
                        # chunked body
                        for i in request.body:
                            low_conn.send(hex(len(i))[2:].encode('utf-8'))
                            low_conn.send(b'\r\n')
                            low_conn.send(i)
                            low_conn.send(b'\r\n')
                        self._pending_requests.append(
                            (request, low_conn, b'0\r\n\r\n', response)
                        )
            except:
                # If we hit any problems here, clean up the connection.
                # Then, reraise so that we can handle the actual exception.
                low_conn.close()
                raise

        except (ProtocolError, socket.error) as err:
            raise ConnectionError(err, request=request)

        except MaxRetryError as e:
            if isinstance(e.reason, ConnectTimeoutError):
                # TODO: Remove this in 3.0.0: see #2811
                if not isinstance(e.reason, NewConnectionError):
                    raise ConnectTimeout(e, request=request)

            if isinstance(e.reason, ResponseError):
                raise RetryError(e, request=request)

            if isinstance(e.reason, _ProxyError):
                raise ProxyError(e, request=request)

            if isinstance(e.reason, _SSLError):
                # This branch is for urllib3 v1.22 and later.
                raise SSLError(e, request=request)

            raise ConnectionError(e, request=request)

        except ClosedPoolError as e:
            raise ConnectionError(e, request=request)

        except _ProxyError as e:
            raise ProxyError(e)

        except (_SSLError, _HTTPError) as e:
            if isinstance(e, _SSLError):
                # This branch is for urllib3 versions earlier than v1.22
                raise SSLError(e, request=request)
            elif isinstance(e, ReadTimeoutError):
                raise ReadTimeout(e, request=request)
            else:
                raise

        return response

    def build_response_into(self, response, req, urllib3_resp):
        """Same as requests.adapters.HTTPAdapter.build_response, but writes
        into a provided requests.Response object instead of creating a new one.
        """
        # Fallback to None if there's no status_code, for whatever reason.
        response.status_code = getattr(urllib3_resp, 'status', None)

        # Make headers case-insensitive.
        response.headers = CaseInsensitiveDict(
            getattr(urllib3_resp, 'headers', {})
        )

        # Set encoding.
        response.encoding = get_encoding_from_headers(response.headers)
        response.raw = urllib3_resp
        response.reason = response.raw.reason

        if isinstance(req.url, bytes):
            response.url = req.url.decode('utf-8')
        else:
            response.url = req.url

        # Add new cookies from the server.
        extract_cookies_to_jar(response.cookies, req, urllib3_resp)

        # Give the Response some context.
        response.request = req
        response.connection = self

    def build_dummy_response_into(self, response, req):
        response.status_code = 998
        response.encoding = 'UTF-8'
        response.reason = 'Request Not Finished'

        response._content = b'''This is a dummy response.
You should not use responses from synchronized requests before calling
the .finish_all() method of SynchronizedAdapter or SynchronizedSession.'''
        response._content_consumed = True

        response.request = req
        response.connection = self

    def build_exception_response_into(self, response, req, exc_info):
        response.status_code = 999
        response.encoding = 'UTF-8'
        response.reason = 'Python Exception'

        exception = ''.join(traceback.format_exception(*exc_info))
        response._content = '''Exception occurred.
An exception occurred while Requests-Racer was trying to finish this
request. Here's what we know:

{}'''.format(exception).encode('utf-8')
        response._content_consumed = True

        response.request = req
        response.connection = self

    def _finish_requests(self, requests):
        for request, conn, rest, response in requests:
            try:
                conn.send(rest)
            except:
                # HACK: see below.
                response.__init__()
                self.build_exception_response_into(
                    response, request, sys.exc_info()
                )

    def _process_responses(self, requests):
        for request, conn, _, response in requests:
            if response.status_code == 999:
                # skip processing the response if we failed to finish the
                # request in the first place.
                continue

            try:
                raw_response = conn.getresponse()
                urllib3_reponse = HTTPResponse.from_httplib(
                    raw_response,
                    # pool=conn, # TODO?
                    connection=conn,
                    preload_content=False,
                    decode_content=False
                )
            except:
                # HACK: see below.
                response.__init__()
                self.build_exception_response_into(
                    response, request, sys.exc_info()
                )
            else:
                # HACK: we re-initialize the response that we originally handed
                # out to the user because there are a bunch of properties that
                # cache various things and cleaning those up would be too much
                # of a hassle.
                response.__init__()
                self.build_response_into(response, request, urllib3_reponse)

            # TODO closing connection

    def finish_all(self, timeout=None):
        """Finishes all of the pending requests.

        This function does not return anything. To access the responses, use
        the response object that was originally returned when making the
        request.
        """
        num_threads = len(self._pending_requests)
        # if the user has given a specific number of threads, use that unless
        # it's higher than we need.
        if self.num_threads is not None:
            num_threads = min(num_threads, self.num_threads)

        chunks = chunk(self._pending_requests, num_threads)

        # HACK: sleeping for a little before sending the requests seems to help
        # synchronize the requests a little better. why? no idea.
        time.sleep(1)

        # first, finish all the requests
        finish_threads = [
            threading.Thread(target=self._finish_requests, args=(chunk,))
            for chunk in chunks
        ]
        for thread in finish_threads:
            thread.start()
        for thread in finish_threads:
            thread.join(timeout)

        # now, process the responses
        resp_threads = [
            threading.Thread(target=self._process_responses, args=(chunk,))
            for chunk in chunks
        ]
        for thread in resp_threads:
            thread.start()
        for thread in resp_threads:
            thread.join(timeout)

        # all done here
        self._pending_requests = []


class SynchronizedSession(Session):
    """A version of the Requests Session class that automatically creates a
    `SynchronizedAdapter`, mounts it for HTTP[S], and exposes its
    `finish_all()` method.
    """
    def __init__(self, num_threads=None):
        super().__init__()

        self.adapter = SynchronizedAdapter(num_threads)
        self.mount('http://', self.adapter)
        self.mount('https://', self.adapter)

    def finish_all(self, *args, **kwargs):
        """See `SynchronizedAdapter.finish_all()`."""
        self.adapter.finish_all(*args, **kwargs)

    @classmethod
    def from_requests_session(cls, other):
        """Creates a `SynchronizedSession` from the provided `requests.Session`
        object. Does not modify the original object, but does not perform a
        deep copy either, so modifications to the returned
        `SynchronizedSession` might affect the original session object as well,
        and vice versa.
        """
        # this is a moderate HACK:
        # we use __getstate__() and __setstate__(), intended to help pickle
        # sessions, to get all of the state of the provided session and add
        # it to the new one, throwing away the adapters since we don't want
        # those to be overwritten.
        # the output of __getstate__() is supposed to be an opaque blob that
        # you aren't really supposed to inspect or modify, so this relies on
        # specifics of the implementation of requests.Session that are not
        # guaranteed to be stable.
        session_state = other.__getstate__()

        if 'adapters' in session_state:
            del session_state['adapters']

        new_session = cls()
        new_session.__setstate__(session_state)

        return new_session
