import pytest
from test_service import setup
from backend.agent_chat.bridge_client import BridgeError

def test_poll_rpc_failure_cancels_known_task(setup):
    s,p,b,svc=setup;c=s.create_session("alice","gpt","codex")
    def fail(_):raise BridgeError("bridge_timeout")
    b.get_agent_task=fail
    run=svc.begin("alice",c["session_id"],"hello","m1")
    list(svc.execute(run["run_id"]))
    assert b.cancelled==["t_fake"]
    assert s.get_run(run["run_id"])["status"]=="bridge_timeout"

def test_cancel_completion_transaction_wins(setup):
    s,p,b,svc=setup;c=s.create_session("alice","gpt","codex")
    run=svc.begin("alice",c["session_id"],"hello","m1")
    original=s.complete_run
    def race(run_id,content):
        s.request_cancel("alice",c["session_id"],run_id)
        return original(run_id,content)
    s.complete_run=race
    events=list(svc.execute(run["run_id"]))
    assert s.get_run(run["run_id"])["status"]=="cancelled"
    assert not any(e["type"]=="token" for e in events)
    messages=s.get_messages("alice",c["session_id"])
    assert [m["speaker_type"] for m in messages] == ["user","model","agent"]
    assert messages[-1]["content"] == "AGENT_RESULT"

def test_formatting_retains_user_request_with_large_result(setup):
    s,p,b,svc=setup;c=s.create_session("alice","gpt","codex")
    b.get_agent_task=lambda _:{"task_id":"t_fake","agent":"codex","status":"completed","result":"A"*18000}
    run=svc.begin("alice",c["session_id"],"USER_OUTPUT_CONSTRAINT","m1")
    list(svc.execute(run["run_id"]))
    messages=p.calls[1][1]
    assert "USER_OUTPUT_CONSTRAINT" in str(messages)
    assert any("[Codex]" in m["content"] and "A"*100 in m["content"] for m in messages)
    assert sum(len(m["content"]) for m in messages)<=18000
