"""Private App facade. Principal is established by operator transport, never tool input."""
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

URI='ui://agentbridge/run-v1.html'


def build_server(service,principal,port=15089):
    if not principal: raise ValueError('Verified principal required.')
    server=FastMCP('AgentBridge',host='127.0.0.1',port=port,streamable_http_path='/mcp')
    meta={'ui':{'resourceUri':URI}}
    readonly=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=False)
    write=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=False)

    @server.tool(meta=meta,annotations=write)
    def start_run(goal:str,source_id:str,source_commit:str,idempotency_key:str,default_agent:str='codex',writer_enabled:bool=False,session_id:str|None=None)->dict:
        """Start a server-side coordinated goal. Writer defaults off. Source must be registered."""
        data=dict(goal=goal,source_id=source_id,source_commit=source_commit,idempotency_key=idempotency_key,default_agent=default_agent,writer_enabled=writer_enabled)
        if session_id: data['session_id']=session_id
        return service.create(principal,data)

    @server.tool(meta=meta,annotations=readonly)
    def get_run(run_id:str)->dict:
        """Inspect persistent run and semantic shared transcript."""
        return service.view(principal,run_id)

    @server.tool(meta=meta,annotations=readonly)
    def open_run_view(run_id:str)->dict:
        """Open controls and transcript. Refresh/polling, not native assistant turns."""
        return service.view(principal,run_id)

    def control(run_id,action,expected_version,idempotency_key,**extra):
        return service.control(principal,run_id,action,dict(expected_version=expected_version,idempotency_key=idempotency_key,**extra))

    @server.tool(meta=meta,annotations=write)
    def pause_run(run_id:str,expected_version:int,idempotency_key:str)->dict:
        """Pause at the current atomic task boundary."""
        return control(run_id,'pause',expected_version,idempotency_key)

    @server.tool(meta=meta,annotations=write)
    def continue_run(run_id:str,expected_version:int,idempotency_key:str)->dict:
        """Resume from persisted transcript and checkpoint."""
        return control(run_id,'continue',expected_version,idempotency_key)

    @server.tool(meta=meta,annotations=write)
    def interrupt_run(run_id:str,expected_version:int,idempotency_key:str)->dict:
        """Cancel active process tree and preserve partial work; pause."""
        return control(run_id,'interrupt',expected_version,idempotency_key)

    @server.tool(meta=meta,annotations=write)
    def stop_run(run_id:str,expected_version:int,idempotency_key:str)->dict:
        """Stop permanently, cancelling active task and retaining work."""
        return control(run_id,'stop',expected_version,idempotency_key)

    @server.tool(meta=meta,annotations=write)
    def send_owner_input(run_id:str,expected_version:int,idempotency_key:str,message:str)->dict:
        """Persist owner instruction as highest-priority next-round context."""
        return control(run_id,'input',expected_version,idempotency_key,message=message)

    @server.tool(meta=meta,annotations=write)
    def approve_owner_gate(run_id:str,expected_version:int,idempotency_key:str,gate_id:str,gate_digest:str)->dict:
        """Approve only displayed exact pending action; replay/stale approval denied."""
        return control(run_id,'approve',expected_version,idempotency_key,gate_id=gate_id,gate_digest=gate_digest)

    @server.resource(URI,mime_type='text/html;profile=mcp-app',meta={'ui':{'csp':{'connectDomains':[],'resourceDomains':[]}}})
    def view()->str:
        return (Path(__file__).resolve().parents[2]/'plugins/agentbridge/dist/run-v1.html').read_text()
    return server
