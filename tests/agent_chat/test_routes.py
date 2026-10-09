import json
import pytest
from flask import Flask,g
from backend.agent_chat.routes import create_blueprint
from test_service import FakeProvider,FakeBridge
from backend.agent_chat.service import AgentChatService
from backend.agent_chat.store import AgentChatStore
from backend.agent_chat.config import AgentChatConfig,ProviderConfig

@pytest.fixture
def client(tmp_path):
    store=AgentChatStore(tmp_path/"agent.db")
    service=AgentChatService(store,FakeProvider(),lambda:FakeBridge(),AgentChatConfig(ProviderConfig("gpt-model",""),ProviderConfig("claude-model","")))
    app=Flask(__name__)
    @app.before_request
    def actor():g.rag_actor="alice"
    app.register_blueprint(create_blueprint(service))
    return app.test_client()

def test_session_and_sse(client):
    response=client.post("/api/agent/sessions",json={"chat_model":"gpt","agent":"codex"})
    assert response.status_code==201
    sid=response.json["session_id"]
    response=client.post("/api/agent/chat/stream",json={"session_id":sid,"chat_model":"gpt","agent":"codex","message":"hello"})
    assert response.status_code==200 and "text/event-stream" in response.content_type
    assert '"content": "FINAL"' in response.text and '"status": "completed"' in response.text
    persisted=client.get(f"/api/agent/sessions/{sid}/messages").json
    assert [(m["speaker_type"],m["speaker_name"]) for m in persisted] == [
        ("user","user"),("model","gpt"),("agent","codex"),("model","gpt")
    ]
    message_events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [event["type"] for event in message_events if event["type"]=="message"] == ["message"] * 4
    assert all({"message_id","session_id","speaker_type","speaker_name","content"} <= event.keys()
               for event in message_events if event["type"]=="message")
    response=client.post("/api/agent/chat/stream",json={"session_id":sid,"chat_model":"gpt","agent":"claude","message":"continue with Claude Code"})
    assert response.status_code==200 and '"status": "completed"' in response.text
    persisted=client.get(f"/api/agent/sessions/{sid}/messages").json
    assert [(m["speaker_type"],m["speaker_name"]) for m in persisted] == [
        ("user","user"),("model","gpt"),("agent","codex"),("model","gpt"),
        ("user","user"),("model","gpt"),("agent","claude-code"),("model","gpt")
    ]
    listed=client.get("/api/agent/sessions").json
    assert next(session for session in listed if session["session_id"]==sid)["agent"]=="claude"

def test_validation_and_separation(client):
    assert client.post("/api/agent/sessions",json={"chat_model":"bad","agent":"codex"}).status_code==400
    assert client.post("/api/agent/chat/stream",json={}).status_code==400
    assert client.post("/api/chat/stream",json={}).status_code==404
    sid=client.post("/api/agent/sessions",json={"chat_model":"gpt","agent":"codex"}).json["session_id"]
    assert client.post("/api/agent/chat/stream",json={"session_id":sid,"chat_model":"invalid","agent":"claude","message":"hello"}).status_code==400
