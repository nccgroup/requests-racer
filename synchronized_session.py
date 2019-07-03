import socket 
import sys
import threading
import time
import traceback

from urllib3.poolmanager import PoolManager, proxy_from_url
from urllib3.response import HTTPResponse
from urllib3.util import parse_url
from urllib3.util import Timeout as TimeoutSauce
from urllib3.util.retry import Retry
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
    ProxyError, RetryError, InvalidSchema, InvalidURL,
)
from requests.models import Response
from requests.structures import CaseInsensitiveDict
from requests.sessions import Session
from requests.utils import get_encoding_from_headers

from http.client import _CS_REQ_SENT

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
        (l[(chunk_size+1)*i : (chunk_size+1)*(i+1)]
         if (i < bigger_chunks)
         else l[chunk_size*i+bigger_chunks : chunk_size*(i+1)+bigger_chunks])
        for i in range(num_chunks)
    ]

class SynchronizedAdapter(HTTPAdapter):
    def __init__(self, num_threads=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._pending_requests = []
        self.num_threads = num_threads

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        """Sends PreparedRequest object. Returns Response object.
        :param request: The :class:`PreparedRequest <PreparedRequest>` being sent.
        :param stream: (optional) Whether to stream the request content.
        :param timeout: (optional) How long to wait for the server to send
            data before giving up, as a float, or a :ref:`(connect timeout,
            read timeout) <timeouts>` tuple.
        :type timeout: float or tuple or urllib3 Timeout object
        :param verify: (optional) Either a boolean, in which case it controls whether
            we verify the server's TLS certificate, or a string, in which case it
            must be a path to a CA bundle to use
        :param cert: (optional) Any user-provided SSL certificate to be trusted.
        :param proxies: (optional) The proxies dictionary to apply to the request.
        :rtype: requests.Response
        """

        # TODO{aleksejs}: ensure no pooling happens here
        try:
            conn = self.get_connection(request.url, proxies)
        except LocationValueError as e:
            raise InvalidURL(e, request=request)

        self.cert_verify(conn, request.url, verify, cert)
        url = self.request_url(request, proxies)
        self.add_headers(request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)

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

        # this is what we will return for now. this object will later be overwritten
        # after the request is finished.
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
                    # at this point, *no* information has been sent to the server---it is buffered
                    # and will only be sent after we call low_conn.endheaders().
                    # however, this request has no body, so calling low_conn.endheaders() would
                    # actually cause the request to be processed by the server immediately.
                    # endheaders() is the only public way to get the connection into a 'sent' state,
                    # which is the only state in which you are allowed to read the response.
                    # so we dig into the internals of the HTTPConnection object to get it into the
                    # 'sent' state and send *most* of its internal buffer without sending the two
                    # final newlines.

                    # this follows HTTPConnection._send_output
                    msg = b"\r\n".join(low_conn._buffer)
                    del low_conn._buffer[:]
                    low_conn.send(msg)

                    low_conn._HTTPConnection__state = 'Request-sent'

                    self._pending_requests.append((request, low_conn, b'\r\n\r\n', response))
                else:
                    # some body, can end headers now
                    low_conn.endheaders()

                    body = request.body
                    if isinstance(body, str):
                        body = body.encode('utf-8')

                    if 'Content-Length' in request.headers:
                        # single body
                        low_conn.send(body[:-3])
                        self._pending_requests.append((request, low_conn, body[-3:], response))
                    else:
                        # chunked body
                        for i in request.body:
                            low_conn.send(hex(len(i))[2:].encode('utf-8'))
                            low_conn.send(b'\r\n')
                            low_conn.send(i)
                            low_conn.send(b'\r\n')
                        self._pending_requests.append((request, low_conn, b'0\r\n\r\n', response))
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
        """Same as requests.adapters.HTTPAdapter.build_response, but writes into a
        provided requests.Response object instead of creating a new one.
        """
        # Fallback to None if there's no status_code, for whatever reason.
        response.status_code = getattr(urllib3_resp, 'status', None)

        # Make headers case-insensitive.
        response.headers = CaseInsensitiveDict(getattr(urllib3_resp, 'headers', {}))

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
                self.build_exception_response_into(response, request, sys.exc_info())

    def _process_responses(self, requests):
        for request, conn, _, response in requests:
            if (response.status_code == 999) or (response.status_code == 998):
                # skip processing the response if we failed to finish the request
                # in the first place.
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
                self.build_exception_response_into(response, request, sys.exc_info())
            else:
                # HACK: we re-initialize the response that we originally handed out
                # to the user because there are a bunch of properties that cache various
                # things and cleaning those up would be too much of a hassle.
                response.__init__()
                self.build_response_into(response, request, urllib3_reponse)

            # TODO closing connection

    def finish_all(self, timeout=None):
        num_threads = self.num_threads
        # if the number of threads was not specified by the user,
        # or if they asked for more threads than we have requests, use one thread per request.
        if (num_threads is None) or (num_threads > len(self._pending_requests)):
            num_threads = len(self._pending_requests)

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
    def __init__(self, num_threads=None):
        super().__init__()

        self.adapter = SynchronizedAdapter(num_threads)
        self.mount('http://', self.adapter)
        self.mount('https://', self.adapter)

    def finish_all(self, *args, **kwargs):
        self.adapter.finish_all(*args, **kwargs)
