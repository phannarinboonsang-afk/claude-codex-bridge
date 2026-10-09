import sqlite3
import pytest
from backend.agent_chat.store import AgentChatStore, StoreError

def test_separate_persistence_and_owner(tmp_path):
    path=tmp_path/"agent.db";s=AgentChatStore(path)
    chat=s.create_session("alice","gpt","codex","Example")
    assert s.get_session("alice",chat["session_id"])["agent"]=="codex"
    with pytest.raises(StoreError):s.get_session("bob",chat["session_id"])
    assert s.list_sessions("bob")==[]
    s=AgentChatStore(path)
    assert s.list_sessions("alice")[0]["chat_model"]=="gpt"

def test_selection_can_change_between_runs_but_not_during_active_run(tmp_path):
    s=AgentChatStore(tmp_path/"agent.db");c=s.create_session("alice","gpt","codex")
    c=s.update_selection("alice",c["session_id"],"claude","claude")
    run=s.begin_run("alice",c["session_id"],"hello","m1","configured-model")
    with pytest.raises(StoreError):s.update_selection("alice",c["session_id"],"gpt","codex")
    with pytest.raises(StoreError):s.begin_run("alice",c["session_id"],"next","m2","model")
    s.update_run(run["run_id"],bridge_task_id="t_example",status="running")
    s.request_cancel("alice",c["session_id"],run["run_id"])
    assert s.get_run(run["run_id"])["cancel_requested"]==1
    assert s.finish(run["run_id"],"completed","answer")=="cancelled"
    c=s.update_selection("alice",c["session_id"],"gpt","codex")
    run=s.begin_run("alice",c["session_id"],"next","m3","configured-model")
    s.update_run(run["run_id"],bridge_task_id="t_example")
    s.finish(run["run_id"],"completed","answer")
    messages=s.get_messages("alice",c["session_id"])
    assert [m["role"] for m in messages]==["user","user","assistant"]
    assert messages[-1]["bridge_task_id"]=="t_example"
    assert s.get_run(run["run_id"])["model_config_id"]=="configured-model"

def test_invalid_choices_and_foreign_db_rejected(tmp_path):
    s=AgentChatStore(tmp_path/"agent.db")
    with pytest.raises(StoreError):s.create_session("alice","other","codex")
    with pytest.raises(StoreError):AgentChatStore(tmp_path/"chat_history.db")


def test_additive_migration_backfills_legacy_and_is_idempotent(tmp_path):
    path=tmp_path/"legacy.db"
    db=sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE agent_chat_sessions (
            session_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
            chat_model TEXT NOT NULL CHECK(chat_model IN ('gpt','claude')),
            agent TEXT NOT NULL CHECK(agent IN ('claude','codex')),
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE agent_chat_messages (
            message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_chat_sessions(session_id),
            role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
            status TEXT NOT NULL, bridge_task_id TEXT, created_at REAL NOT NULL);
        INSERT INTO agent_chat_sessions VALUES ('s1','alice','Old','gpt','codex',1,2);
        INSERT INTO agent_chat_messages VALUES ('m1','s1','assistant','old answer','completed','t_old',3);
    """)
    db.close()

    store=AgentChatStore(path)
    store=AgentChatStore(path)
    message=store.get_messages("alice","s1")[0]

    assert (message["message_id"],message["session_id"],message["content"]) == ("m1","s1","old answer")
    assert (message["role"],message["speaker_type"],message["speaker_name"]) == ("assistant","model","gpt")
    assert message["bridge_task_id"] == "t_old"
    assert message["chat_model"] == "gpt"
    assert message["agent"] == "codex"

def test_append_semantic_message_round_trips_metadata(tmp_path):
    store=AgentChatStore(tmp_path/"agent.db")
    session=store.create_session("alice","gpt","claude")
    message=store.append_message(
        session["session_id"],"agent","claude-code","fixed the requested issue",
        message_id="agent-message",bridge_task_id="t_agent",chat_model="gpt",
        agent="claude",stage="execution",
    )

    assert message == store.get_message("alice",session["session_id"],"agent-message")
    assert message["message_id"] == "agent-message"
    assert message["speaker_type"] == "agent"
    assert message["speaker_name"] == "claude-code"
    assert message["content"] == "fixed the requested issue"
    assert message["created_at"] > 0
    assert message["role"] == "assistant"


def test_complete_run_atomically_persists_final_model_message(tmp_path):
    store=AgentChatStore(tmp_path/"agent.db")
    session=store.create_session("alice","gpt","codex")
    run=store.begin_run("alice",session["session_id"],"hello","u1","gpt-model")

    status,message=store.complete_run(run["run_id"],"final answer")

    assert status == "completed"
    assert message["speaker_type"] == "model"
    assert message["speaker_name"] == "gpt"
    assert message["content"] == "final answer"
    assert [m["speaker_type"] for m in store.get_messages("alice",session["session_id"])] == ["user","model"]
