"""Independent AgentBridge transcript persistence."""
import os
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

TERMINAL = ("completed", "cancelled", "failed", "provider_timeout", "bridge_timeout")


class StoreError(Exception):
    def __init__(self, message="Agent chat request is invalid.", status=409):
        super().__init__(message)
        self.status = status


class AgentChatStore:
    def __init__(self, db_path=None):
        self.path = Path(db_path or os.getenv("AGENT_CHAT_DB_PATH", str(Path(__file__).resolve().parents[2] / "agent_chat.db")))
        if self.path.name == "chat_history.db" or self.path.is_symlink():
            raise StoreError("Agent chat requires separate storage.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with self.connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables & {"sessions", "messages"}:
                raise StoreError("Agent chat requires separate storage.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_chat_sessions (
                    session_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    chat_model TEXT NOT NULL CHECK(chat_model IN ('gpt','claude')),
                    agent TEXT NOT NULL CHECK(agent IN ('claude','codex')),
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_chat_messages (
                    message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_chat_sessions(session_id),
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
                    status TEXT NOT NULL, bridge_task_id TEXT, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_chat_runs (
                    run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_chat_sessions(session_id),
                    user_message_id TEXT NOT NULL UNIQUE, chat_model TEXT NOT NULL, agent TEXT NOT NULL,
                    model_config_id TEXT NOT NULL, agent_config_id TEXT NOT NULL DEFAULT 'bridge-read-only-v1',
                    bridge_task_id TEXT, status TEXT NOT NULL, phase TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, error_code TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL);
                CREATE UNIQUE INDEX IF NOT EXISTS agent_one_active_run ON agent_chat_runs(session_id)
                    WHERE status NOT IN ('completed','cancelled','failed','provider_timeout','bridge_timeout');
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(agent_chat_messages)")}
            for name in ("speaker_type", "speaker_name", "chat_model", "agent", "stage"):
                if name not in columns:
                    db.execute(f"ALTER TABLE agent_chat_messages ADD COLUMN {name} TEXT")
            db.execute("""
                UPDATE agent_chat_messages SET
                    speaker_type=CASE role WHEN 'user' THEN 'user' ELSE 'model' END,
                    speaker_name=CASE WHEN role='user' THEN 'user'
                        ELSE COALESCE((SELECT chat_model FROM agent_chat_sessions s
                                       WHERE s.session_id=agent_chat_messages.session_id),'gpt') END,
                    chat_model=(SELECT chat_model FROM agent_chat_sessions s
                                WHERE s.session_id=agent_chat_messages.session_id),
                    agent=(SELECT agent FROM agent_chat_sessions s
                           WHERE s.session_id=agent_chat_messages.session_id)
                WHERE speaker_type IS NULL OR speaker_name IS NULL
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def selection(chat_model, agent):
        if chat_model not in ("gpt", "claude") or agent not in ("claude", "codex"):
            raise StoreError("Invalid chat model or agent.", 400)

    @staticmethod
    def _role_for(speaker_type):
        return "user" if speaker_type == "user" else "assistant"

    @staticmethod
    def _validate_speaker(speaker_type, speaker_name):
        allowed = {
            "user": {"user"},
            "model": {"gpt", "claude"},
            "agent": {"codex", "claude-code"},
            "system": {"orchestrator"},
        }
        if speaker_type not in allowed or speaker_name not in allowed[speaker_type]:
            raise StoreError("Invalid transcript speaker.", 400)

    def create_session(self, owner, chat_model, agent, title="New Agent Chat"):
        self.selection(chat_model, agent)
        sid = uuid.uuid4().hex
        now = time.time()
        with self.connection() as db:
            db.execute("INSERT INTO agent_chat_sessions VALUES (?,?,?,?,?,?,?)",
                (sid, owner, str(title)[:120], chat_model, agent, now, now))
        return self.get_session(owner, sid)

    def get_session(self, owner, session_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM agent_chat_sessions WHERE session_id=? AND owner=?", (session_id, owner)).fetchone()
        if not row:
            raise StoreError("Agent chat not found.", 404)
        result = dict(row)
        result.pop("owner")
        return result

    def list_sessions(self, owner):
        with self.connection() as db:
            return [{k: r[k] for k in r.keys() if k != "owner"} for r in db.execute(
                "SELECT * FROM agent_chat_sessions WHERE owner=? ORDER BY updated_at DESC", (owner,))]

    def update_selection(self, owner, session_id, chat_model, agent):
        self.selection(chat_model, agent)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_orchestrator_lease(db, session_id)
            session = db.execute(
                "SELECT 1 FROM agent_chat_sessions WHERE session_id=? AND owner=?",
                (session_id, owner),
            ).fetchone()
            if not session:
                raise StoreError("Agent chat not found.", 404)
            active_run = db.execute(
                "SELECT 1 FROM agent_chat_runs WHERE session_id=? AND status NOT IN "
                "('completed','cancelled','failed','provider_timeout','bridge_timeout') LIMIT 1",
                (session_id,),
            ).fetchone()
            if active_run:
                raise StoreError("Selection cannot change while a run is active.")
            db.execute(
                "UPDATE agent_chat_sessions SET chat_model=?,agent=?,updated_at=? WHERE session_id=? AND owner=?",
                (chat_model, agent, time.time(), session_id, owner),
            )
        return self.get_session(owner, session_id)

    def get_messages(self, owner, session_id):
        self.get_session(owner, session_id)
        with self.connection() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM agent_chat_messages WHERE session_id=? ORDER BY created_at,rowid",
                (session_id,),
            )]

    def get_message(self, owner, session_id, message_id):
        self.get_session(owner, session_id)
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM agent_chat_messages WHERE session_id=? AND message_id=?",
                (session_id, message_id),
            ).fetchone()
        if not row:
            raise StoreError("Agent chat message not found.", 404)
        return dict(row)

    def get_message_in_session(self, session_id, message_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM agent_chat_messages WHERE session_id=? AND message_id=?",
                (session_id, message_id),
            ).fetchone()
        if not row:
            raise StoreError("Agent chat message not found.", 404)
        return dict(row)

    def append_message(
        self, session_id, speaker_type, speaker_name, content, *, message_id=None,
        status="completed", bridge_task_id=None, chat_model=None, agent=None, stage=None,
    ):
        self._validate_speaker(speaker_type, speaker_name)
        if not isinstance(content, str) or not content:
            raise StoreError("Transcript message must contain text.", 400)
        if chat_model is not None and chat_model not in ("gpt", "claude"):
            raise StoreError("Invalid chat model.", 400)
        if agent is not None and agent not in ("claude", "codex"):
            raise StoreError("Invalid agent.", 400)
        mid = message_id or uuid.uuid4().hex
        now = time.time()
        with self.connection() as db:
            session = db.execute(
                "SELECT owner,chat_model,agent FROM agent_chat_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if not session:
                raise StoreError("Agent chat not found.", 404)
            model = chat_model or session["chat_model"]
            selected_agent = agent or session["agent"]
            db.execute("""
                INSERT INTO agent_chat_messages
                    (message_id,session_id,role,content,status,bridge_task_id,created_at,
                     speaker_type,speaker_name,chat_model,agent,stage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                mid, session_id, self._role_for(speaker_type), content, status,
                bridge_task_id, now, speaker_type, speaker_name, model,
                selected_agent, stage,
            ))
            db.execute("UPDATE agent_chat_sessions SET updated_at=? WHERE session_id=?", (now, session_id))
            owner = session["owner"]
        return self.get_message(owner, session_id, mid)

    def begin_run(self, owner, session_id, content, message_id, model):
        session = self.get_session(owner, session_id)
        rid, now = uuid.uuid4().hex, time.time()
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._check_orchestrator_lease(db, session_id)
                db.execute("""
                    INSERT INTO agent_chat_messages
                        (message_id,session_id,role,content,status,bridge_task_id,created_at,
                         speaker_type,speaker_name,chat_model,agent,stage)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    message_id, session_id, "user", content, "queued", None, now,
                    "user", "user", session["chat_model"], session["agent"], "planning",
                ))
                db.execute("""INSERT INTO agent_chat_runs
                    (run_id,session_id,user_message_id,chat_model,agent,model_config_id,status,phase,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (rid, session_id, message_id, session["chat_model"], session["agent"], model, "planning", "planning", now, now))
                db.execute("UPDATE agent_chat_sessions SET updated_at=? WHERE session_id=?", (now, session_id))
        except sqlite3.IntegrityError:
            raise StoreError("A run is active or this message was already submitted.") from None
        return self.get_run(rid)

    @staticmethod
    def _check_orchestrator_lease(db, session_id):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='orchestrator_session_leases'").fetchone():
            if db.execute("SELECT 1 FROM orchestrator_session_leases WHERE session_id=?", (session_id,)).fetchone():
                raise StoreError("An orchestrator run owns this session.")

    def get_run(self, run_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM agent_chat_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise StoreError("Agent run not found.", 404)
        return dict(row)

    def update_run(self, run_id, **fields):
        allowed = {"status", "phase", "bridge_task_id", "error_code"}
        if not fields or not set(fields) <= allowed:
            raise StoreError()
        fields["updated_at"] = time.time()
        with self.connection() as db:
            db.execute("UPDATE agent_chat_runs SET " + ",".join(k + "=?" for k in fields) + " WHERE run_id=?",
                (*fields.values(), run_id))

    def request_cancel(self, owner, session_id, run_id):
        self.get_session(owner, session_id)
        run = self.get_run(run_id)
        if run["session_id"] != session_id:
            raise StoreError("Agent run not found.", 404)
        if run["status"] not in TERMINAL:
            with self.connection() as db:
                db.execute("UPDATE agent_chat_runs SET cancel_requested=1,updated_at=? WHERE run_id=?", (time.time(), run_id))
        return self.get_run(run_id)

    def complete_run(self, run_id, content):
        if not isinstance(content, str) or not content:
            raise StoreError("Transcript message must contain text.", 400)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run_row = db.execute("SELECT * FROM agent_chat_runs WHERE run_id=?", (run_id,)).fetchone()
            if not run_row:
                raise StoreError("Agent run not found.", 404)
            run = dict(run_row)
            if run["status"] in TERMINAL:
                return run["status"], None
            status = "cancelled" if run["cancel_requested"] else "completed"
            now = time.time()
            message = None
            if status == "completed":
                message_id = uuid.uuid4().hex
                db.execute("""
                    INSERT INTO agent_chat_messages
                        (message_id,session_id,role,content,status,bridge_task_id,created_at,
                         speaker_type,speaker_name,chat_model,agent,stage)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    message_id, run["session_id"], "assistant", content, "completed",
                    run["bridge_task_id"], now, "model", run["chat_model"],
                    run["chat_model"], run["agent"], "formatting",
                ))
                message = dict(db.execute(
                    "SELECT * FROM agent_chat_messages WHERE message_id=?",
                    (message_id,),
                ).fetchone())
            db.execute("""
                UPDATE agent_chat_runs SET status=?,phase=?,updated_at=?,completed_at=?
                WHERE run_id=?
            """, (status, status, now, now, run_id))
            db.execute(
                "UPDATE agent_chat_messages SET status=?,bridge_task_id=? WHERE message_id=?",
                (status, run["bridge_task_id"], run["user_message_id"]),
            )
            db.execute("UPDATE agent_chat_sessions SET updated_at=? WHERE session_id=?", (now, run["session_id"]))
        return status, message

    def finish(self, run_id, status, content=None, error_code=None):
        if status not in TERMINAL:
            raise StoreError()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = dict(db.execute("SELECT * FROM agent_chat_runs WHERE run_id=?", (run_id,)).fetchone())
            if run["status"] in TERMINAL:
                return run["status"]
            if status == "completed" and run["cancel_requested"]:
                status, content = "cancelled", None
            now = time.time()
            db.execute("UPDATE agent_chat_runs SET status=?,phase=?,error_code=?,updated_at=?,completed_at=? WHERE run_id=?",
                (status, status, error_code, now, now, run_id))
            db.execute("UPDATE agent_chat_messages SET status=?,bridge_task_id=? WHERE message_id=?",
                (status, run["bridge_task_id"], run["user_message_id"]))
            if content is not None and status == "completed":
                session = db.execute(
                    "SELECT chat_model,agent FROM agent_chat_sessions WHERE session_id=?",
                    (run["session_id"],),
                ).fetchone()
                db.execute("""
                    INSERT INTO agent_chat_messages
                        (message_id,session_id,role,content,status,bridge_task_id,created_at,
                         speaker_type,speaker_name,chat_model,agent,stage)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    uuid.uuid4().hex, run["session_id"], "assistant", content, status,
                    run["bridge_task_id"], now, "model", run["chat_model"],
                    run["chat_model"], run["agent"], "formatting",
                ))
            db.execute("UPDATE agent_chat_sessions SET updated_at=? WHERE session_id=?", (now, run["session_id"]))
            return status

    def request_cancel_internal(self, run_id):
        with self.connection() as db:
            db.execute("UPDATE agent_chat_runs SET cancel_requested=1 WHERE run_id=?", (run_id,))
