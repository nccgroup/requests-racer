import http.server
import io
import json
import threading
import time
import unittest

import requests

from core import SynchronizedSession

class TestRequestHandler(http.server.BaseHTTPRequestHandler):
    def _do_root(self):
        headers = [(k, v) for k in set(self.headers) for v in self.headers.get_all(k)]
        res = {
            'time': time.time(),
            'path': self.path,
            'headers': headers,
        }
        return res, [('X-Hello', 'World')]

    def _do_set_cookie(self):
        return {'ok': True}, [('Set-Cookie', 'hello=world')]

    def _handle_request(self):
        status_code = 200
        res = {}
        headers = []
        if self.path == '/':
            res, headers = self._do_root()
        elif self.path == '/set_cookie':
            res, headers = self._do_set_cookie()
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
        self.server_host, self.server_httpd, self.server_thread = run_test_server()

    def tearDown(self):
        self.server_httpd.shutdown()
        self.server_thread.join()
        self.server_httpd.server_close()

    def test_timing(self):
        session = SynchronizedSession()
        res1 = session.get('http://{}/'.format(self.server_host))
        time.sleep(1)
        res2 = session.post(
            'http://{}/'.format(self.server_host),
            data={'a': 'a'*(1024*1024)}
        )

        session.finish_all()

        time1 = res1.json()['time']
        time2 = res2.json()['time']

        self.assertLess(abs(time1 - time2), 0.25)

    def test_session_conversion(self):
        original_session = requests.Session()
        original_session.get('http://{}/set_cookie'.format(self.server_host))

        converted_session = SynchronizedSession.from_requests_session(original_session)
        res = converted_session.get('http://{}/'.format(self.server_host))

        converted_session.finish_all()

        self.assertIn(['Cookie', 'hello=world'], res.json()['headers'])

    def test_headers(self):
        session = SynchronizedSession()
        session.headers.update({'User-Agent': 'Test/1.0'})

        res = session.get(
            'http://{}/'.format(self.server_host),
            headers={'Cake': 'Lemon'},
        )

        session.finish_all()
        echo = res.json()['headers']

        self.assertIn(['User-Agent', 'Test/1.0'], echo)
        self.assertIn(['Cake', 'Lemon'], echo)

    def test_status_codes(self):
        session = SynchronizedSession()

        res = session.get('http://{}/'.format(self.server_host))
        self.assertEqual(res.status_code, 998)
        session.finish_all()
        self.assertEqual(res.status_code, 200)

        res = session.get('http://{}/does_not_exist'.format(self.server_host))
        self.assertEqual(res.status_code, 998)
        session.finish_all()
        self.assertEqual(res.status_code, 404)
