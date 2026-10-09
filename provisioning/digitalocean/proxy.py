#!/usr/bin/python3
"""CONNECT-only explicit proxy. Every DNS answer must be globally routable."""
import concurrent.futures
import http.server
import ipaddress
import json
import pathlib
import re
import select
import socket
import socketserver
import time

def validate_target(authority,allowlist,addresses):
    match=re.fullmatch(r'([a-z0-9.-]+):443',authority)
    if not match or match[1] not in allowlist:raise ValueError('domain_denied')
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):raise ValueError('private_or_metadata_target_denied')
    return match[1]

class PoolServer(socketserver.ThreadingMixIn,http.server.HTTPServer):
    daemon_threads=True
    def __init__(self,*args,**kwargs):
        self.slots=__import__('threading').BoundedSemaphore(32);super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):self.shutdown_request(request);return
        super().process_request(request,address)
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()

def main():
    policy=json.loads(pathlib.Path('/etc/agentbridge-worker/egress-policy.json').read_text())
    allowed=set(policy['allowed_connect_hosts'])
    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):super().setup();self.connection.settimeout(10)
        def log_message(self,*args):pass
        def do_CONNECT(self):
            upstream=None
            try:
                host=validate_target(self.path,allowed,['8.8.8.8'])
                answers=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
                addresses=list({x[4][0] for x in answers});validate_target(self.path,allowed,addresses)
                # Connect to the validated numeric address, not another DNS lookup.
                upstream=socket.create_connection((addresses[0],443),timeout=15)
                self.send_response(200);self.end_headers();self.wfile.flush()
                streams=[self.connection,upstream];deadline=time.monotonic()+600
                while time.monotonic()<deadline:
                    readable,_,_=select.select(streams,[],[],10)
                    if not readable:continue
                    for src in readable:
                        data=src.recv(65536)
                        if not data:return
                        (upstream if src is self.connection else self.connection).sendall(data)
            except Exception:
                if upstream is None:self.send_error(403,'Proxy target denied or unavailable')
            finally:
                if upstream:upstream.close()
    PoolServer(('127.0.0.1',3128),Handler).serve_forever()

if __name__=='__main__':main()
