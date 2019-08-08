import http.server
import io
import json
import threading
import time
import unittest

from urllib.parse import urlparse, parse_qsl

import requests

from core import SynchronizedSession

class TestRequestHandler(http.server.BaseHTTPRequestHandler):
    def _do_root(self, params):
        body_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(body_len)

        res = {
            'time': time.time(),
            'path': self.path,
            'headers': self.headers.items(),
            'params': params,
            'body_length': body_len,
            'body_truncated': body[:1024].decode(),
        }
        return res, [('X-Hello', 'World')]

    def _do_set_cookie(self, params):
        return {'ok': True}, [('Set-Cookie', 'hello=world')]

    def _handle_request(self):
        status_code = 200
        res = {}
        headers = []
        url = urlparse(self.path)
        params = parse_qsl(url.query)

        if url.path == '/':
            res, headers = self._do_root(params)
        elif url.path == '/set_cookie':
            res, headers = self._do_set_cookie(params)
        else:
            status_code = 404

        self.send_response(status_code)
        for (k, v) in headers:
            self.send_header(k, v)
        self.end_headers()

        self.wfile.write(json.dumps(res).encode('utf-8', 'surrogateescape'))

    def do_GET(self):
        return self._handle_request()

    def do_POST(self):
        return self._handle_request()

    def log_message(self, *args, **kwargs):
        pass

def run_test_server():
    httpd = http.server.HTTPServer(('127.0.0.1', 0), TestRequestHandler)

    host, port = httpd.socket.getsockname()

    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()

    return '{}:{}'.format(host, port), httpd, thread

class BlackBoxTests(unittest.TestCase):
    def setUp(self):
        self.server_host, self.server_httpd, self.server_thread = \
            run_test_server()

        self.echo_endpoint = 'http://{}/'.format(self.server_host)
        self.cookie_endpoint = 'http://{}/set_cookie'.format(self.server_host)
        self.e404_endpoint = 'http://{}/doesnt_exist'.format(self.server_host)

    def tearDown(self):
        self.server_httpd.shutdown()
        self.server_thread.join()
        self.server_httpd.server_close()

    def test_timing(self):
        session = SynchronizedSession()
        res1 = session.get(self.echo_endpoint)
        time.sleep(1)
        res2 = session.post(self.echo_endpoint, data={'a': 'a'*(1024*1024)})

        session.finish_all()

        time1 = res1.json()['time']
        time2 = res2.json()['time']

        self.assertLess(abs(time1 - time2), 0.25)

    def test_session_conversion(self):
        original_session = requests.Session()
        original_session.get(self.cookie_endpoint)

        converted_session = \
            SynchronizedSession.from_requests_session(original_session)
        res = converted_session.get(self.echo_endpoint)

        converted_session.finish_all()

        self.assertIn(['Cookie', 'hello=world'], res.json()['headers'])

    def test_headers(self):
        session = SynchronizedSession()
        session.headers.update({'User-Agent': 'Test/1.0'})

        res = session.get(self.echo_endpoint, headers={'Cake': 'Lemon'})

        session.finish_all()
        echo = res.json()['headers']

        self.assertIn(['User-Agent', 'Test/1.0'], echo)
        self.assertIn(['Cake', 'Lemon'], echo)

    def test_status_codes(self):
        session = SynchronizedSession()

        res = session.get(self.echo_endpoint)
        self.assertEqual(res.status_code, 998)
        session.finish_all()
        self.assertEqual(res.status_code, 200)

        res = session.get(self.e404_endpoint)
        self.assertEqual(res.status_code, 998)
        session.finish_all()
        self.assertEqual(res.status_code, 404)

    def test_params_and_data(self):
        session = SynchronizedSession()

        params = {'muffin': 'blueberry', 'tea': 'green'}
        res = session.get(self.echo_endpoint, params=params)
        session.finish_all()
        self.assertEqual(
            set(map(tuple, res.json()['params'])),
            set(params.items())
        )

        res = session.post(self.echo_endpoint, data=params)
        session.finish_all()
        self.assertIn(
            res.json()['body_truncated'],
            {'muffin=blueberry&tea=green', 'tea=green&muffin=blueberry'}
        )

        res = session.post(self.echo_endpoint, data='a'*(1024*1024))
        session.finish_all()
        self.assertEqual(res.json()['body_length'], 1024*1024)

        # TODO: alas, our janky test server does not support chunked bodies.
        # fortunately, most web servers in the wild don't either.

        # def chunked_body():
        #     yield b'strawberry'
        #     yield b'shortcake'
        #
        # res = session.post('http://localhost:9999', data=chunked_body())
        # session.finish_all()
