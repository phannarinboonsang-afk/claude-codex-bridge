"""SQLite persistence for tasks and the agent inbox. Only sanitized text is ever stored.
Prompts are never persisted."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL = ("completed", "failed", "blocked", "timed_out", "cancelled")
ACTIVE = ("queued", "running")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  task_id TEXT PRIMARY KEY, request_id TEXT, status TEXT, mode TEXT, project TEXT,
  started_at TEXT, finished_at TEXT, seq INTEGER, result_json TEXT, pid INTEGER, starttime TEXT);
CREATE TABLE IF NOT EXISTS messages(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, ts TEXT, task_id TEXT,
  summary TEXT, body TEXT, status TEXT);
"""


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            cols = {r["name"] for r in self._db.execute("PRAGMA table_info(tasks)")}
            for c in ("pid INTEGER", "starttime TEXT"):          # migrate Phase 1 databases
                if c.split()[0] not in cols:
                    self._db.execute(f"ALTER TABLE tasks ADD COLUMN {c}")
        try:
            db_path.chmod(0o600)
        except OSError:
            pass

    # ----- tasks -----
    def save_task(self, result: dict[str, Any], request_id: str) -> None:
        term = ",".join(f"'{s}'" for s in TERMINAL)
        with self._lock:
            self._db.execute(
                "INSERT INTO tasks(task_id,request_id,status,mode,project,started_at,finished_at,seq,result_json)"
                " VALUES(?,?,?,?,?,?,?,(SELECT COALESCE(MAX(seq),0)+1 FROM tasks),?)"
                " ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, mode=excluded.mode,"
                " project=excluded.project, started_at=excluded.started_at, finished_at=excluded.finished_at,"
                " result_json=excluded.result_json,"
                f" seq=CASE WHEN excluded.status IN ({term}) THEN (SELECT MAX(seq)+1 FROM tasks) ELSE tasks.seq END,"
                f" pid=CASE WHEN excluded.status IN ({term}) THEN NULL ELSE tasks.pid END,"
                f" starttime=CASE WHEN excluded.status IN ({term}) THEN NULL ELSE tasks.starttime END",
                (result["task_id"], request_id, result["status"], result.get("mode"), result.get("project"),
                 result.get("started_at"), result.get("finished_at"), json.dumps(result)),
            )

    def set_proc(self, task_id: str, pid: int, starttime: str | None, command_hash: str | None = None) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET pid=?, starttime=? WHERE task_id=?", (pid, starttime, task_id))
            if command_hash:
                row = self._db.execute("SELECT result_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if row:
                    result = json.loads(row["result_json"])
                    result["process_argv_hash"] = command_hash
                    self._db.execute("UPDATE tasks SET result_json=? WHERE task_id=?", (json.dumps(result), task_id))

    def active_tasks(self) -> list[dict[str, Any]]:
        q = ",".join(f"'{s}'" for s in ACTIVE)
        with self._lock:
            rows = self._db.execute(f"SELECT task_id,request_id,pid,starttime,result_json FROM tasks WHERE status IN ({q})").fetchall()
        return [{"task_id": r["task_id"], "request_id": r["request_id"], "pid": r["pid"], "starttime": r["starttime"],
                 "result": json.loads(r["result_json"])} for r in rows]

    def get_task(self, task_id: str | None = None, *, only_completed: bool = False) -> dict[str, Any] | None:
        q, args, conds = "SELECT result_json FROM tasks", [], []
        if task_id:
            conds.append("task_id=?")
            args.append(task_id)
        if only_completed:
            conds.append("status IN ('completed','failed','timed_out','cancelled')")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY seq DESC LIMIT 1"
        with self._lock:
            row = self._db.execute(q, args).fetchone()
        return json.loads(row["result_json"]) if row else None

    # ----- inbox -----
    def post_message(self, summary: str, body: str = "", task_id: str | None = None) -> dict[str, Any]:
        msg = {"id": "m_" + uuid.uuid4().hex[:12], "timestamp": utcnow(), "task_id": task_id,
               "summary": summary, "body": body, "status": "unread"}
        with self._lock:
            self._db.execute(
                "INSERT INTO messages(id,ts,task_id,summary,body,status) VALUES(?,?,?,?,?,?)",
                (msg["id"], msg["timestamp"], task_id, summary, body, "unread"),
            )
        return msg

    def get_messages(self, *, unread_only: bool = True, mark_read: bool = False, limit: int = 50,
                     task_id: str | None = None) -> list[dict[str, Any]]:
        q, args, conds = "SELECT * FROM messages", [], []
        if unread_only:
            conds.append("status='unread'")
        if task_id:
            conds.append("task_id=?")
            args.append(task_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY seq ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
            out = [{"id": r["id"], "timestamp": r["ts"], "task_id": r["task_id"], "summary": r["summary"],
                    "body": r["body"], "status": r["status"]} for r in rows]
            if mark_read and out:
                self._db.executemany("UPDATE messages SET status='read' WHERE id=?", [(m["id"],) for m in out])
                for m in out:
                    m["status_after_read"] = "read"
        return out
