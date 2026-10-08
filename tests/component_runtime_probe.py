"""Run only on the server, in an isolated network namespace with installed core binaries."""
import asyncio
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from proxy_manager.cores.registry import all_adapters
from proxy_manager.domain.components import normalize_component_routes
from proxy_manager.domain.model import normalize_state


def response_server(marker):
    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            self.send_response(200); self.end_headers()
            self.close_connection = False
            self.handle_one_request()

        def do_GET(self):
            payload = json.dumps({'exit': marker}).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(payload)))
            self.end_headers(); self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    return server


def wait_entry(process, port):
    deadline = time.monotonic()+8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError('Core exited during startup')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                return
        except OSError:
            time.sleep(.05)
    raise AssertionError('Core entry did not start')


def request(port, url, auth=False):
    proxy = 'http://'+('fixture-user:fixture-password@' if auth else '')+'127.0.0.1:'+str(port)
    with httpx.Client(proxy=proxy, trust_env=False, timeout=3) as client:
        return client.get(url)


def reject(port, url):
    try:
        response = request(port, url)
        assert response.status_code != 200, 'Disabled entry silently forwarded a request'
    except httpx.HTTPError:
        pass


def main():
    servers = [response_server(marker) for marker in ('A', 'B', 'DIRECT')]
    target = 'http://127.0.0.1:'+str(servers[2].server_port)+'/egress'
    root = Path(os.environ['PROXY_COMPONENT_KERNEL_ROOT'])
    try:
        for adapter_id, adapter in all_adapters().items():
            binary = root/adapter_id/'core'
            if not binary.is_file():
                print(adapter_id+': native runtime check skipped; core is not installed')
                continue
            nodes = [{'id': name, 'name': name, 'protocol':'http', 'enabled':True,
                      'endpoint':'http://127.0.0.1:'+str(servers[index].server_port)}
                     for index, name in enumerate(('a','b'))]
            groups = [{'id':name, 'name':name, 'mode':'select', 'node_ids':[name], 'selected':name}
                      for name in ('a','b')]
            state, _ = normalize_state({'nodes':nodes, 'groups':groups, 'routes':[], 'subscriptions':[],
                                       'control':{'enabled':True,'deployment':'dedicated','scope':'full',
                                                  'listen':'127.0.0.1:19090','url':'http://127.0.0.1:19090'},
                                       'proxy_entry':{'http_url':'http://127.0.0.1:17890',
                                                      'private':{'enabled':True,'listen':'127.0.0.1','port':17891,
                                                                 'username':'fixture-user','password':'fixture-password'}}})
            state['proxy_entry']['private'] = {'enabled':True,'listen':'127.0.0.1','port':17891,
                                               'username':'fixture-user','password':'fixture-password',
                                               'service_host':'127.0.0.1'}
            state['component_routes'] = normalize_component_routes([
                {'id':'plugin-a','kind':'plugin','target':'a','enabled':True},
                {'id':'mcp-b','kind':'mcp','target':'b','enabled':True},
                {'id':'plugin-direct','kind':'plugin','target':'direct','enabled':True},
                {'id':'plugin-stopped','kind':'plugin','target':'a','enabled':False},
                {'id':'mcp-private','kind':'mcp','target':'b','enabled':True,'scope':'private'},
            ])
            # A domain rule matching every fixture must not override component source policy.
            state['rule_groups'] = [{'id':'domain','name':'domain','enabled':True,'priority':1,
                                    'target':'b','domains':[{'type':'DOMAIN','payload':'127.0.0.1'}]}]
            with tempfile.TemporaryDirectory() as directory:
                config = adapter.write_config(Path(directory), adapter.render(state))
                with (Path(directory)/'core.log').open('w+') as log:
                    process = subprocess.Popen(adapter.command(binary, config), stdout=log, stderr=log)
                    try:
                        wait_entry(process, 18000)
                        if adapter_id == 'mihomo':
                            fetched=asyncio.run(adapter.fetch_runtime(state))
                            errors=adapter.verify(adapter.render(state),fetched['runtime'],fetched['proxies'],fetched['rules'])
                            assert not errors, errors
                        for port, marker in ((18000,'A'),(18001,'B'),(18002,'DIRECT')):
                            result=request(port,target); result.raise_for_status()
                            assert result.json()['exit']==marker, (adapter_id,port,result.text)
                        reject(18003,target)
                        reject(18004,target)
                        assert request(18004,target,auth=True).json()['exit']=='B'
                        state['component_routes'][0]['enabled'] = False
                        process.terminate(); process.wait(timeout=5)
                        config=adapter.write_config(Path(directory),adapter.render(state))
                        process=subprocess.Popen(adapter.command(binary,config),stdout=log,stderr=log)
                        wait_entry(process,18000); reject(18000,target)
                        assert request(18001,target).json()['exit']=='B'
                        print(adapter_id+': component A/B/DIRECT, domain precedence, disable, restart, private authentication passed')
                    except Exception:
                        log.flush(); log.seek(0); print(log.read()[-4000:]); raise
                    finally:
                        if process.poll() is None:
                            process.terminate(); process.wait(timeout=5)
    finally:
        for server in servers:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
