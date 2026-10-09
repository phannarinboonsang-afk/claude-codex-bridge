import http.client
import http.server
import json
import socket
import threading
import time
import unittest
import pathlib
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from worker import handler_type,Denied

class Recorder:
    def __init__(self):self.calls=[]
    def dispatch(self,op,body):self.calls.append((op,body));return {'accepted':True}

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.recorder=Recorder();self.token=b'fixture-only-not-a-real-secret-123456789'
        self.server=http.server.HTTPServer(('127.0.0.1',0),handler_type(self.recorder,self.token))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=7)
    def post(self,headers,body=b'{}',path='/v1/inspect'):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=7)
        c.request('POST',path,body=body,headers=headers);r=c.getresponse();status=r.status;data=r.read();c.close();return status,json.loads(data)
    def test_missing_auth_never_dispatches(self):
        self.assertEqual(self.post({})[0],403);self.assertEqual(self.recorder.calls,[])
    def test_wrong_auth_never_dispatches(self):
        self.assertEqual(self.post({'Authorization':'Bearer wrong'})[0],403);self.assertEqual(self.recorder.calls,[])
    def test_authenticated_request_dispatches(self):
        self.assertEqual(self.post({'Authorization':'Bearer '+self.token.decode()})[0],200)
        self.assertEqual(self.recorder.calls,[('inspect',{})])
    def test_oversize_body_never_dispatches(self):
        headers={'Authorization':'Bearer '+self.token.decode(),'Content-Length':'40000'}
        self.assertEqual(self.post(headers,b'x'*40000)[0],403);self.assertEqual(self.recorder.calls,[])
    def test_partial_headers_do_not_block_indefinitely(self):
        with socket.create_connection(('127.0.0.1',self.server.server_port),timeout=7) as s:
            s.settimeout(7);start=time.monotonic();s.sendall(b'POST /v1/inspect HTTP/1.1\r\n')
            self.assertEqual(s.recv(10),b'');self.assertLess(time.monotonic()-start,6.5)
        self.assertEqual(self.post({'Authorization':'Bearer '+self.token.decode()})[0],200)

if __name__=='__main__':unittest.main()
