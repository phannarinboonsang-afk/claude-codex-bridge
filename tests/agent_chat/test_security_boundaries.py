import json
import pytest
from flask import Flask
from backend.agent_chat.routes import create_blueprint
from backend.agent_chat.providers.openai import OpenAIChatProvider
from backend.agent_chat.providers.anthropic import AnthropicChatProvider
from test_providers import Transport
from test_service import setup
from backend.agent_chat.providers.base import ProviderCancelled,ProviderTimeout
from backend.agent_chat.bridge_client import BridgeError

@pytest.mark.parametrize("provider,data",[
    (OpenAIChatProvider,{"output":[{"type":"message","content":[{"type":"output_text","text":"SYNTHETIC_PROVIDER_SENTINEL /home/synthetic/private.key"}]}]}),
    (AnthropicChatProvider,{"content":[{"type":"text","text":"SYNTHETIC_PROVIDER_SENTINEL /home/synthetic/private.key"}]})])
def test_provider_echo_secret_redacted(tmp_path,provider,data):
    p=tmp_path/"synthetic.key";p.write_text("SYNTHETIC_PROVIDER_SENTINEL");p.chmod(0o600)
    result=provider(str(p),transport_factory=lambda:Transport(data)).generate([],"fake",1)
    assert "SYNTHETIC_PROVIDER_SENTINEL" not in result and "/home/" not in result

def test_unauthenticated_agent_api_rejects(setup):
    *_,svc=setup
    app=Flask(__name__);app.register_blueprint(create_blueprint(svc))
    assert app.test_client().get("/api/agent/sessions").status_code==401

@pytest.mark.parametrize("exception,expected",[
    (ProviderTimeout(),"provider_timeout"),(ProviderCancelled(),"cancelled"),(BridgeError("bridge_timeout"),"bridge_timeout")])
def test_final_provider_lifecycle(setup,exception,expected):
    s,p,b,svc=setup;c=s.create_session("alice","gpt","codex")
    original=p.generate
    def generate(*args):
        if p.calls:raise exception
        return original(*args)
    p.generate=generate
    run=svc.begin("alice",c["session_id"],"hello","m1")
    events=list(svc.execute(run["run_id"]))
    assert s.get_run(run["run_id"])["status"]==expected
    assert not any(e["type"]=="token" for e in events)
    assert b.calls==[("codex","INSTRUCTION")]

def test_cancel_start_race_calls_bridge(setup):
    s,p,b,svc=setup;c=s.create_session("alice","gpt","codex")
    run=svc.begin("alice",c["session_id"],"hello","m1")
    original=b.start_agent_task
    def start(*args):
        s.request_cancel("alice",c["session_id"],run["run_id"])
        return original(*args)
    b.start_agent_task=start
    list(svc.execute(run["run_id"]))
    assert b.cancelled==["t_fake"] and s.get_run(run["run_id"])["status"]=="cancelled"

def test_safe_error_never_persisted_or_streamed(setup,caplog):
    s,p,b,svc=setup;c=s.create_session("alice","claude","claude")
    p.error=RuntimeError("SYNTHETIC_SECRET /home/private/auth")
    run=svc.begin("alice",c["session_id"],"hello","m1")
    events=list(svc.execute(run["run_id"]))
    text=json.dumps(events)+json.dumps(s.get_run(run["run_id"]))+caplog.text
    assert "SYNTHETIC_SECRET" not in text and "/home/private" not in text


def test_safe_text_removes_ansi_terminal_control_sequences():
    from backend.agent_chat.safety import safe_text
    assert safe_text("\x1b[31mCodex result\x1b[0m\r") == "Codex result"
    assert safe_text("\x1b]0;private title\x07visible") == "visible"
    assert safe_text("\x1b]0;private title\x1b\\visible") == "visible"
