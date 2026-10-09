import json
import pytest
from backend.agent_chat.service import AgentChatService
from backend.agent_chat.store import AgentChatStore
from backend.agent_chat.config import AgentChatConfig, ProviderConfig
from backend.agent_chat.providers.base import ProviderError, ProviderTimeout

class FakeProvider:
    def __init__(self):self.calls=[];self.error=None;self.errors={}
    def generate(self,selector,messages,config,timeout,cancel):
        self.calls.append((selector,messages,config.model))
        call_index=len(self.calls)-1
        if call_index in self.errors:raise self.errors[call_index]
        if self.error:raise self.error
        return json.dumps({"agent_instruction":"INSTRUCTION","user_message":"I will ask the selected agent."}) if call_index % 2 == 0 else "FINAL"
class FakeBridge:
    def __init__(self):self.calls=[];self.cancelled=[];self.status="completed"
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def start_agent_task(self,agent,prompt,timeout):
        self.calls.append((agent,prompt));return {"task_id":"t_fake","agent":agent,"status":"queued"}
    def get_agent_task(self,task_id):return {"task_id":task_id,"status":self.status,"agent":self.calls[-1][0],"result":"AGENT_RESULT"}
    def cancel_agent_task(self,task_id):self.cancelled.append(task_id);return {"status":"cancelled"}

@pytest.fixture
def setup(tmp_path):
    store=AgentChatStore(tmp_path/"agent.db");provider=FakeProvider();bridge=FakeBridge()
    cfg=AgentChatConfig(ProviderConfig("gpt-model",""),ProviderConfig("claude-model",""))
    service=AgentChatService(store,provider,lambda:bridge,cfg,sleep=lambda _:None)
    return store,provider,bridge,service

@pytest.mark.parametrize("model",["gpt","claude"])
@pytest.mark.parametrize("agent",["claude","codex"])
def test_four_pairs(setup,model,agent):
    s,p,b,svc=setup;c=s.create_session("alice",model,agent)
    run=svc.begin("alice",c["session_id"],"hello","m1");events=list(svc.execute(run["run_id"]))
    assert s.get_run(run["run_id"])["status"]=="completed"
    assert [x[0] for x in p.calls]==[model,model]
    assert b.calls==[(agent,"INSTRUCTION")]
    assert s.get_messages("alice",c["session_id"])[-1]["content"]=="FINAL"
    assert events[-1]["type"]=="done"

@pytest.mark.parametrize("error,status",[(ProviderError(),"failed"),(ProviderTimeout(),"provider_timeout")])
def test_provider_failure_no_fallback(setup,error,status):
    s,p,b,svc=setup;p.error=error;c=s.create_session("alice","gpt","codex")
    run=svc.begin("alice",c["session_id"],"hello","m1");list(svc.execute(run["run_id"]))
    assert s.get_run(run["run_id"])["status"]==status and not b.calls

def test_cancel_before_execution(setup):
    s,p,b,svc=setup;c=s.create_session("alice","gpt","codex")
    run=svc.begin("alice",c["session_id"],"hello","m1");svc.cancel("alice",c["session_id"],run["run_id"])
    list(svc.execute(run["run_id"]))
    assert s.get_run(run["run_id"])["status"]=="cancelled" and not p.calls

def test_cancel_actual_bridge(setup):
    s,p,b,svc=setup;c=s.create_session("alice","gpt","codex")
    run=svc.begin("alice",c["session_id"],"hello","m1")
    s.update_run(run["run_id"],bridge_task_id="t_fake",status="running")
    svc.cancel("alice",c["session_id"],run["run_id"])
    assert b.cancelled==["t_fake"]

def test_context_isolated_and_bounded(setup):
    s,p,b,svc=setup
    a=s.create_session("alice","gpt","codex");other=s.create_session("alice","claude","claude")
    r=svc.begin("alice",other["session_id"],"OTHER_PRIVATE_MARKER","other");list(svc.execute(r["run_id"]))
    p.calls=[]
    r=svc.begin("alice",a["session_id"],"A"*18000,"a");list(svc.execute(r["run_id"]))
    assert all(sum(len(m["content"]) for m in call[1])<=18000 for call in p.calls)
    assert "OTHER_PRIVATE_MARKER" not in str(p.calls)


@pytest.mark.parametrize("agent,speaker", [("codex","codex"),("claude","claude-code")])
def test_shared_transcript_persists_and_streams_agents_before_gpt(setup,agent,speaker):
    s,p,b,svc=setup
    c=s.create_session("alice","gpt",agent)
    run=svc.begin("alice",c["session_id"],"inspect backend","user-1")
    events=list(svc.execute(run["run_id"]))

    messages=s.get_messages("alice",c["session_id"])
    assert [(m["speaker_type"],m["speaker_name"]) for m in messages] == [
        ("user","user"),("model","gpt"),("agent",speaker),("model","gpt")
    ]
    assert [m["content"] for m in messages] == [
        "inspect backend","I will ask the selected agent.","AGENT_RESULT","FINAL"
    ]
    streamed=[e for e in events if e["type"]=="message"]
    assert [e["message_id"] for e in streamed] == [m["message_id"] for m in messages]
    assert [e["speaker_name"] for e in streamed] == ["user","gpt",speaker,"gpt"]
    assert [e["content"] for e in streamed][2] == "AGENT_RESULT"


def test_cross_agent_turn_uses_shared_transcript_context(setup):
    s,p,b,svc=setup
    current=s.create_session("alice","gpt","codex")
    first=svc.begin("alice",current["session_id"],"inspect backend","first-user")
    list(svc.execute(first["run_id"]))
    updated=s.update_selection("alice",current["session_id"],"gpt","claude")
    second=svc.begin("alice",updated["session_id"],"continue the earlier finding","second-user")
    list(svc.execute(second["run_id"]))

    planning=[call for call in p.calls if call[0]=="gpt"][2][1]
    assert "stateless" in planning[0]["content"].lower()
    assert any(message["content"]=="[Codex]\nAGENT_RESULT" for message in planning)
    assert [message["speaker_name"] for message in s.get_messages("alice",current["session_id"])] == [
        "user","gpt","codex","gpt","user","gpt","claude-code","gpt"
    ]

def test_agent_result_survives_gpt_formatting_error(setup):
    s,p,b,svc=setup
    p.errors={1:ProviderError()}
    c=s.create_session("alice","gpt","codex")
    run=svc.begin("alice",c["session_id"],"inspect backend","user-1")
    events=list(svc.execute(run["run_id"]))

    messages=s.get_messages("alice",c["session_id"])
    assert [(m["speaker_type"],m["speaker_name"]) for m in messages] == [
        ("user","user"),("model","gpt"),("agent","codex"),("model","gpt")
    ]
    assert messages[2]["content"] == "AGENT_RESULT"
    assert messages[3]["status"] == "failed"
    assert "AGENT_RESULT" not in messages[3]["content"]
    assert any(e["type"]=="error" and e.get("stage")=="formatting" for e in events)

def test_follow_up_context_includes_prior_agent_and_is_session_scoped(setup):
    s,p,b,svc=setup
    current=s.create_session("alice","gpt","codex")
    other=s.create_session("alice","gpt","claude")
    other_run=svc.begin("alice",other["session_id"],"other session","other-user")
    list(svc.execute(other_run["run_id"]))
    s.append_message(other["session_id"],"agent","claude-code","OTHER_SESSION_MARKER")
    first=svc.begin("alice",current["session_id"],"inspect backend","first-user")
    list(svc.execute(first["run_id"]))
    p.calls=[]
    follow=svc.begin("alice",current["session_id"],"fix finding two","follow-user")
    list(svc.execute(follow["run_id"]))

    planning_context=str(p.calls[0][1])
    assert "AGENT_RESULT" in planning_context
    assert "Codex" in planning_context or "codex" in planning_context
    assert "OTHER_SESSION_MARKER" not in planning_context
