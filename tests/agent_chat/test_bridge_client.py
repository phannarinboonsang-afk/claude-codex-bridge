import json
import pytest
from backend.agent_chat.bridge_client import BridgeClient, BridgeError

class Response:
    status_code=200
    headers={"Mcp-Session-Id":"synthetic-session"}
    def __init__(self,data):self.data=data
    def json(self):return self.data
    def close(self):pass
class Transport:
    def __init__(self):self.calls=[];self.trust_env=True;self.status=200
    def post(self,url,**kw):
        self.calls.append((url,kw))
        body=kw["json"]
        if body["method"]=="initialize":data={"jsonrpc":"2.0","id":body["id"],"result":{"protocolVersion":"2025-03-26"}}
        elif body["method"]=="notifications/initialized":data={}
        else:data={"id":body["id"],"result":{"structuredContent":{"task_id":"t_test","agent":body["params"]["arguments"].get("agent","codex"),"status":"queued"}}}
        response=Response(data);response.status_code=self.status;return response
    def delete(self,*a,**kw):return Response({})
    def close(self):pass

@pytest.fixture
def auth(tmp_path):
    p=tmp_path/"auth";p.write_text("Authorization: Bearer SYNTHETIC_BRIDGE_SENTINEL");p.chmod(0o600);return str(p)

@pytest.mark.parametrize("agent",["claude","codex"])
def test_authenticated_mcp_routing(auth,agent):
    t=Transport()
    with BridgeClient(auth_file=auth,transport_factory=lambda:t) as b:
        assert b.start_agent_task(agent,"instruction",10)["task_id"]=="t_test"
        b.get_agent_task("t_test");b.cancel_agent_task("t_test")
    calls=[kw["json"] for _,kw in t.calls]
    assert [c["method"] for c in calls[:2]]==["initialize","notifications/initialized"]
    args=calls[2]["params"]["arguments"]
    assert args=={"agent":agent,"prompt":"instruction","mode":"read_only","timeout":10}
    assert all("SYNTHETIC_BRIDGE_SENTINEL" not in json.dumps(c) for c in calls)
    assert t.trust_env is False

@pytest.mark.parametrize("status",[401,403,500])
def test_http_errors_safe(auth,status):
    t=Transport();t.status=status
    with pytest.raises(BridgeError) as e:
        with BridgeClient(auth_file=auth,transport_factory=lambda:t) as b:b.get_agent_task("t_test")
    assert "SYNTHETIC" not in str(e.value)
def test_disallow_remote_target(auth):
    with pytest.raises(BridgeError):BridgeClient(url="https://example.com/mcp",auth_file=auth)

@pytest.mark.parametrize("failure",["timeout","unavailable","malformed"])
def test_transport_and_protocol_failures(auth,failure):
    import requests
    t=Transport()
    def post(*args,**kwargs):
        if failure=="timeout":raise requests.Timeout("synthetic private detail")
        if failure=="unavailable":raise requests.ConnectionError("synthetic private detail")
        return Response({})
    t.post=post
    with pytest.raises(BridgeError) as error:
        with BridgeClient(auth_file=auth,transport_factory=lambda:t):pass
    assert error.value.code=={"timeout":"bridge_timeout","unavailable":"bridge_unavailable","malformed":"bridge_protocol"}[failure]
    assert "private" not in str(error.value)
