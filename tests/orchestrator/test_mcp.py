import asyncio
from .test_store import setup
from backend.orchestrator.runtime import Service
from backend.orchestrator.mcp_server import build_server


def test_private_tool_and_view_contract(setup):
    store,shared,sid=setup
    service=Service(store,lambda rid:None,{'fixture':'a'*40})
    server=build_server(service,'owner')
    tools=asyncio.run(server.list_tools())
    assert {t.name for t in tools}=={'start_run','get_run','open_run_view','pause_run','continue_run','interrupt_run','stop_run','send_owner_input','approve_owner_gate'}
    assert all(t.meta['ui']['resourceUri']=='ui://agentbridge/run-v1.html' for t in tools)
    resources=asyncio.run(server.list_resources())
    assert str(resources[0].uri)=='ui://agentbridge/run-v1.html'
    service.close()
