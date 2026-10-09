"""Operator entrypoint for the isolated coordinator."""
import argparse
import json
import os
from pathlib import Path
from ..agent_chat.config import AgentChatConfig
from ..agent_chat.providers.credentials import read_provider_key
from ..agent_chat.provider_gateway import ProcessProviderGateway
from ..agent_chat.store import AgentChatStore
from .app import create_app
from worker.adapter import RemoteWorkerAdapter
from .context import Coordinator
from .runtime import Service
from .store import OrchestratorStore
from .worker import Worker


def build_service(root,sources_file,bridge_url,bridge_auth_file):
    root=Path(root)
    resolved=root.resolve()
    if root.is_symlink() or str(resolved)!=str(root.absolute()) or resolved.is_relative_to(Path(__file__).resolve().parents[2]) or resolved.is_relative_to('/etc'):
        raise ValueError('Dedicated runtime root required.')
    root.mkdir(mode=0o700,parents=True,exist_ok=True); root.chmod(0o700)
    sources=json.loads(Path(sources_file).read_text())
    if not isinstance(sources,dict) or not sources: raise ValueError('Registered sources required.')
    config=AgentChatConfig.from_env()
    if config.openai.model!='gpt-6-luna': raise ValueError('Approved coordinator model required.')
    store=OrchestratorStore(AgentChatStore(root/'agent-chat.db'))
    coordinator=Coordinator(ProcessProviderGateway(),config)
    worker=Worker(store,coordinator,lambda:RemoteWorkerAdapter(bridge_url,bridge_auth_file,root/'remote-worker.db'))
    return Service(store,worker.run_until_boundary,sources)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',required=True); parser.add_argument('--sources',required=True)
    parser.add_argument('--worker-url',required=True); parser.add_argument('--worker-auth-file',required=True)
    parser.add_argument('--auth-file',required=True); parser.add_argument('--principal',required=True)
    parser.add_argument('--port',type=int,default=15088)
    parser.add_argument('--transport',choices=('http','stdio','mcp-http'),default='http')
    args=parser.parse_args()
    if args.port not in (15088,15089): raise ValueError('Candidate port required.')
    token=read_provider_key(args.auth_file)
    service=build_service(args.runtime,args.sources,args.worker_url,args.worker_auth_file)
    service.recover_active_runs()
    try:
        if args.transport=='http':
            app=create_app(service,{token:args.principal})
            app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False)
        else:
            from .mcp_server import build_server
            server=build_server(service,args.principal,args.port)
            if args.transport=='stdio': server.run(transport='stdio')
            else:
                import uvicorn
                from .transport_auth import BearerAuth
                uvicorn.run(BearerAuth(server.streamable_http_app(),token),host='127.0.0.1',port=args.port,log_level='warning')
    finally: service.close()


if __name__=='__main__': main()
