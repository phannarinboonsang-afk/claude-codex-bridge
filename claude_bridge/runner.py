"""Runs Claude Code non-interactively (`claude -p`) under a strict permission profile."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import BASH_DENY, READ_DENY, Config
from .project_state import git_porcelain
from .security import PathError, sanitize, validate_project_path, validate_writer_roots
from .store import TERMINAL, Store, utcnow
from .executors import ClaudeRunner, CodexRunner

log = logging.getLogger("claude_bridge")

READ_ONLY_TOOLS = ("Read", "Grep", "Glob")
WRITER_FILE_TOOLS = ("Edit", "Write")

SYSTEM_NOTE = (
    "You were invoked through Claude Bridge by ChatGPT (via MCP). Mode: {mode}. "
    "Stay inside the working directory. Never read or print secrets, tokens, .env files or keys. "
    "Never run destructive git commands, restart services, or touch databases/Qdrant. "
    "Reply concisely; your final message is returned to ChatGPT."
)


@dataclass
class RunningTask:
    task_id: str
    request_id: str
    project: str
    mode: str
    started_at: str
    started_mono: float
    agent: str = "claude"


class TaskRegistry:
    """Admission control: at most one writer overall, no reader/writer overlap on a project."""

    def __init__(self, max_readers: int):
        self._lock = threading.Lock()
        self._running: dict[str, RunningTask] = {}
        self._max_readers = max_readers

    def try_acquire(self, t: RunningTask) -> str | None:
        with self._lock:
            writers = [r for r in self._running.values() if r.mode == "writer"]
            if t.mode == "writer":
                if writers:
                    return f"another writer task ({writers[0].task_id}) is running"
                same = [r for r in self._running.values() if r.project == t.project]
                if same:
                    return f"task {same[0].task_id} is still running on this project"
            else:
                if any(w.project == t.project for w in writers):
                    return "a writer task is running on this project"
                if sum(1 for r in self._running.values() if r.mode == "read_only") >= self._max_readers:
                    return "too many concurrent read_only tasks"
            self._running[t.task_id] = t
            return None

    def release(self, task_id: str) -> None:
        with self._lock:
            self._running.pop(task_id, None)

    def snapshot(self) -> list[RunningTask]:
        with self._lock:
            return sorted(self._running.values(), key=lambda r: r.started_mono)


def build_command(cfg: Config, mode: str) -> list[str]:
    cmd = [cfg.claude_bin, "-p", "--output-format", "json", "--restricted",
           "--permission-mode", "dontAsk", "--permission-prompts", "none",
           "--no-session-persistence", "--strict-mcp-config",
           "--max-budget-usd", str(cfg.max_budget_usd),
           "--append-system-prompt", SYSTEM_NOTE.format(mode=mode)]
    if cfg.claude_model:
        cmd += ["--model", cfg.claude_model]
    if mode == "writer":
        tools = list(READ_ONLY_TOOLS + WRITER_FILE_TOOLS) + ["Bash"]
        allowed = list(READ_ONLY_TOOLS + WRITER_FILE_TOOLS) + [f"Bash({p})" for p in cfg.writer_bash_allow]
        denied = list(READ_DENY) + [f"Bash({p})" for p in BASH_DENY]
    else:
        tools = list(READ_ONLY_TOOLS)
        allowed = list(READ_ONLY_TOOLS)
        denied = list(READ_DENY)
    cmd += ["--tools", ",".join(tools), "--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(denied)]
    return cmd


def child_env(cfg: Config) -> dict[str, str]:
    """Minimal environment: nothing from the bridge process leaks to the child except what auth needs."""
    env = {"PATH": cfg.child_path, "HOME": str(Path.home()), "LANG": "C.UTF-8", "TERM": "dumb"}
    for k in ("USER", "LOGNAME"):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


@dataclass
class ProcOutcome:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def proc_starttime(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat: stable identity for a pid (guards against pid reuse)."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
        return data.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def proc_command_hash(pid: int) -> str | None:
    try:
        return hashlib.sha256(Path(f"/proc/{pid}/cmdline").read_bytes()).hexdigest()
    except OSError:
        return None


async def run_process(cmd: list[str], cwd: Path, stdin: bytes, env: dict[str, str], timeout: float,
                      on_start=None) -> ProcOutcome:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    if on_start:
        on_start(proc.pid)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
        return ProcOutcome(proc.returncode, out, err, False)
    except asyncio.TimeoutError:
        _kill_group(proc.pid)               # whole process group of THIS task only
        await proc.wait()
        return ProcOutcome(proc.returncode, b"", b"", True)
    except asyncio.CancelledError:
        _kill_group(proc.pid)
        await asyncio.shield(proc.wait())
        raise


def _summarize(text: str, limit: int = 300) -> str:
    text = text.strip()
    first = text.split("\n\n", 1)[0].replace("\n", " ")
    return first if len(first) <= limit else first[: limit - 1] + "…"


_TEST_LINE = re.compile(r"\b\d+\s+(?:passed|failed|errors?)\b[^\n]{0,80}")


class Blocked(Exception):
    def __init__(self, code: str, msg: str, project: str | None = None):
        super().__init__(msg)
        self.code, self.msg, self.project = code, msg, project


class Bridge:
    def __init__(self, cfg: Config, store: Store | None = None):
        self.cfg = cfg
        validate_writer_roots(cfg.writer_roots, cfg.protected_write_roots)
        self.store = store or Store(cfg.db_path)
        self.registry = TaskRegistry(cfg.max_concurrent_readers)
        self.last_model: str | None = cfg.claude_model
        self.last_models = {"claude": cfg.claude_model, "codex": cfg.codex_model}
        self.executors = {"claude": ClaudeRunner(), "codex": CodexRunner()}
        self._version: str | None = None
        self._jobs: dict[str, asyncio.Task] = {}
        self.shutting_down = False
        self.recover_interrupted()

    # ---------- helpers ----------
    def _result(self, task_id: str, status: str, mode: str, project: str | None, started: str | None, **kw: Any) -> dict[str, Any]:
        r = {"task_id": task_id, "agent": "claude", "status": status, "summary": "", "result": "", "files_changed": [],
             "tests": [], "warnings": [], "mode": mode, "project": project, "started_at": started,
             "finished_at": utcnow(), "duration_ms": 0, "model": None, "cost_usd": None, "exit_code": None}
        r.update(kw)
        return r

    def _save(self, r: dict[str, Any], request_id: str) -> None:
        r["request_id"] = request_id
        self.store.save_task(r, request_id)

    def _blocked(self, task_id: str, request_id: str, mode: str, b: Blocked, started: str) -> dict[str, Any]:
        log.info("request=%s task=%s blocked code=%s", request_id, task_id, b.code)
        r = self._result(task_id, "blocked", mode, b.project, started, summary=f"blocked: {b.msg}", result="",
                         warnings=[f"{b.code}: {b.msg}"])
        self._save(r, request_id)
        return r

    async def cli_version(self) -> str | None:
        if self._version is None:
            try:
                o = await run_process([self.cfg.claude_bin, "--version"], Path("/"), b"", child_env(self.cfg), 15)
                self._version = sanitize(o.stdout).strip()[:80] or None
            except Exception:
                self._version = None
        return self._version

    def _gate(self, mode: str, project_path: str | None) -> Path:
        cfg = self.cfg
        if mode not in ("read_only", "writer"):
            raise Blocked("bad_mode", "mode must be read_only or writer")
        try:
            if mode == "writer":
                if not cfg.writer_enabled or not cfg.writer_roots:
                    raise Blocked("writer_denied", "writer mode is disabled (no explicit writer allowlist configured)", project_path)
                return validate_project_path(project_path, cfg.writer_roots)
            return validate_project_path(project_path, cfg.read_roots)
        except PathError as e:
            raise Blocked(e.code, str(e))

    # ---------- shared execution core (caller holds the registry slot) ----------
    async def _execute(self, task_id: str, prompt: str, project: Path, mode: str, timeout_s: int,
                       started: str, timeout_status: str, agent: str = "claude") -> dict[str, Any]:
        t0 = time.monotonic()
        run = self.executors[agent].execute(self, task_id, prompt, project, mode, timeout_s, started, timeout_status)
        try:
            r = await asyncio.wait_for(run, timeout_s) if agent == "codex" else await run
        except asyncio.TimeoutError:
            r = self._result(task_id, timeout_status, mode, str(project), started, agent=agent,
                             summary=f"timed out after {timeout_s}s", warnings=["timeout: task process group killed"])
        except OSError:
            if agent == "claude":
                raise
            r = self._result(task_id, "failed", mode, str(project), started, agent=agent,
                             summary="Could not start Codex CLI", warnings=["codex_spawn_failed"])
        r["agent"] = agent
        if agent == "codex": r["duration_ms"] = int((time.monotonic() - t0) * 1000)
        else: self.last_models["claude"] = self.last_model
        return r

    async def _execute_claude(self, task_id: str, prompt: str, project: Path, mode: str, timeout_s: int,
                       started: str, timeout_status: str) -> dict[str, Any]:
        t0 = time.monotonic()
        before = await git_porcelain(project) if mode == "writer" else None
        try:
            out = await run_process(build_command(self.cfg, mode), project, prompt.encode(), child_env(self.cfg), timeout_s,
                                    on_start=lambda pid: self.store.set_proc(task_id, pid, proc_starttime(pid)))
        except FileNotFoundError:
            r = self._result(task_id, "failed", mode, str(project), started, summary="Claude CLI not found",
                             warnings=["claude_not_found: configured claude binary does not exist"])
        except OSError as e:
            r = self._result(task_id, "failed", mode, str(project), started, summary="Could not start Claude CLI",
                             warnings=[f"spawn_error: {sanitize(str(e))[:200]}"])
        else:
            r = self._interpret(task_id, mode, str(project), started, out, timeout_s, timeout_status)
            if mode == "writer":
                after = await git_porcelain(project)
                if before is not None and after is not None:
                    r["files_changed"] = sorted(set(after) ^ set(before) | {p for p in after if after[p] != before.get(p)})[:200]
                elif after is None:
                    r["warnings"].append("files_changed unavailable: project is not a git repository")
        r["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return r

    def _interpret(self, task_id: str, mode: str, project: str, started: str, out: ProcOutcome, timeout_s: int,
                   timeout_status: str = "failed") -> dict[str, Any]:
        cfg = self.cfg
        if out.timed_out:
            return self._result(task_id, timeout_status, mode, project, started, summary=f"timed out after {timeout_s}s",
                                warnings=[f"timeout: Claude process killed after {timeout_s}s"])
        stdout = sanitize(out.stdout, cfg.max_output_bytes)
        stderr = sanitize(out.stderr, 20_000)
        if out.returncode != 0:
            tail = (stderr or stdout)[-2000:]
            return self._result(task_id, "failed", mode, project, started, exit_code=out.returncode,
                                summary=f"Claude CLI exited with code {out.returncode}", result=tail,
                                warnings=["command_failed"])
        warnings: list[str] = []
        try:
            data = json.loads(out.stdout.decode("utf-8", "replace"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError:
            return self._result(task_id, "completed", mode, project, started, exit_code=0,
                                summary=_summarize(stdout), result=stdout,
                                warnings=["non_json_output: CLI output was not JSON; returned raw sanitized text"])
        text = sanitize(str(data.get("result", "")), cfg.max_output_bytes)
        model = next(iter(data.get("modelUsage") or {}), None)
        if model:
            self.last_model = model
        for d in (data.get("permission_denials") or [])[:10]:
            warnings.append(f"permission_denied: {sanitize(str(d.get('tool_name')))} was blocked by policy")
        status = "failed" if data.get("is_error") else "completed"
        tests = [m.group(0).strip() for m in _TEST_LINE.finditer(text)][:5]
        return self._result(task_id, status, mode, project, started, exit_code=0, summary=_summarize(text) or "(empty result)",
                            result=text, tests=tests, warnings=warnings, model=model,
                            cost_usd=data.get("total_cost_usd"))

    def _finish(self, r: dict[str, Any], request_id: str) -> dict[str, Any]:
        r.pop("process_argv_hash", None)
        r["finished_at"] = utcnow()
        self._save(r, request_id)
        self.store.post_message(f"[{r['status']}] {r['summary']}"[:300], r["result"][:4000], r["task_id"])
        log.info("request=%s task=%s finished status=%s duration_ms=%d", request_id, r["task_id"], r["status"], r["duration_ms"])
        return r

    # ---------- synchronous (compatibility; ChatGPT should use the async tools) ----------
    async def ask_claude(self, prompt: str, project_path: str | None, mode: str, timeout: int | None,
                         request_id: str | None = None) -> dict[str, Any]:
        request_id = request_id or "r_" + uuid.uuid4().hex[:10]
        task_id = "t_" + uuid.uuid4().hex[:12]
        started = utcnow()
        log.info("request=%s task=%s ask_claude mode=%s prompt_len=%d prompt_sha=%s", request_id, task_id, mode, len(prompt),
                 hashlib.sha256(prompt.encode()).hexdigest()[:10])
        try:
            project = self._gate(mode, project_path)
        except Blocked as b:
            return self._blocked(task_id, request_id, mode, b, started)
        timeout_s = min(timeout or self.cfg.default_timeout, self.cfg.max_timeout)
        why = self.registry.try_acquire(RunningTask(task_id, request_id, str(project), mode, started, time.monotonic()))
        if why:
            return self._blocked(task_id, request_id, mode, Blocked("busy", why, str(project)), started)
        try:
            return self._finish(await self._execute(task_id, prompt, project, mode, timeout_s, started, "failed"), request_id)
        finally:
            self.registry.release(task_id)

    # ---------- asynchronous tasks ----------
    def queue_depth(self) -> int:
        running = {r.task_id for r in self.registry.snapshot()}
        return sum(1 for t in self._jobs if t not in running)

    def task_view(self, task_id: str) -> dict[str, Any]:
        r = self.store.get_task(task_id)
        if not r:
            return {"found": False, "task_id": task_id, "status": None, "message": "no matching task"}
        v = {k: r.get(k) for k in ("task_id", "request_id", "status", "mode", "project", "summary", "result", "files_changed",
                                   "tests", "warnings", "started_at", "model", "cost_usd", "duration_ms")}
        v["completed_at"] = r.get("finished_at") if r["status"] in TERMINAL else None
        v["agent"] = r.get("agent", "claude")
        v["found"] = True
        return v

    async def start_task(self, prompt: str, project_path: str | None, mode: str, timeout: int | None,
                         request_id: str | None = None, *, agent: str = "claude") -> dict[str, Any]:
        if agent not in self.executors:
            raise ValueError("agent must be claude or codex")
        request_id = request_id or "r_" + uuid.uuid4().hex[:10]
        task_id = "t_" + uuid.uuid4().hex[:12]
        log.info("request=%s task=%s start_agent_task agent=%s mode=%s prompt_len=%d prompt_sha=%s", request_id, task_id, agent, mode, len(prompt),
                 hashlib.sha256(prompt.encode()).hexdigest()[:10])
        try:
            if agent == "codex":
                if mode != "read_only": raise Blocked("codex_writer_denied", "Codex supports read_only only")
                if not self.cfg.codex_enabled: raise Blocked("codex_disabled", "Codex executor is disabled")
            project = self._gate(mode, project_path)
            if len(self._jobs) >= self.cfg.max_queue:
                raise Blocked("queue_full", f"queue is full ({self.cfg.max_queue} active/queued tasks)", str(project))
        except Blocked as b:
            r = self._blocked(task_id, request_id, mode, b, utcnow())
            r["agent"] = agent
            self._save(r, request_id)
            return {"task_id": task_id, "agent": agent, "status": "blocked", "request_id": request_id, "warnings": r["warnings"]}
        timeout_s = min(timeout or self.cfg.default_timeout, self.cfg.max_timeout)
        self._save(self._result(task_id, "queued", mode, str(project), None, finished_at=None, summary="queued", agent=agent), request_id)
        self._jobs[task_id] = asyncio.create_task(self._job(task_id, request_id, prompt, project, mode, timeout_s, agent))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cur = self.store.get_task(task_id) or {}
        return {"task_id": task_id, "agent": agent, "status": cur.get("status", "queued"), "request_id": request_id}

    async def _job(self, task_id: str, request_id: str, prompt: str, project: Path, mode: str, timeout_s: int, agent: str = "claude") -> None:
        acquired, started = False, None
        try:
            while True:
                why = self.registry.try_acquire(RunningTask(task_id, request_id, str(project), mode, utcnow(), time.monotonic(), agent))
                if why is None:
                    break
                await asyncio.sleep(0.2)
            acquired = True
            started = utcnow()
            self._save(self._result(task_id, "running", mode, str(project), started, finished_at=None, summary="running", agent=agent), request_id)
            r = await self._execute(task_id, prompt, project, mode, timeout_s, started, "timed_out", agent)
        except asyncio.CancelledError:
            if self.shutting_down:
                r = self._result(task_id, "failed", mode, str(project), started, summary="interrupted by bridge shutdown",
                                 warnings=["interrupted_by_restart: bridge was stopping; task process group was killed"])
            else:
                r = self._result(task_id, "cancelled", mode, str(project), started, summary="cancelled on request",
                                 warnings=["cancelled: task process group was killed" if acquired else "cancelled: task was still queued"])
        except Exception as e:                                    # never leave a task stuck in running
            r = self._result(task_id, "failed", mode, str(project), started, summary="internal bridge error",
                             warnings=[f"internal_error: {sanitize(type(e).__name__)}"])
            log.exception("task=%s internal error", task_id)
        finally:
            if acquired:
                self.registry.release(task_id)
        r["agent"] = agent
        try:
            self._finish(r, request_id)
        finally:
            self._jobs.pop(task_id, None)

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        job = self._jobs.get(task_id)            # only tasks started by THIS bridge instance
        if job is None:
            v = self.task_view(task_id)
            if v.get("found"):
                v["warnings"] = list(v["warnings"]) + [f"not_cancellable: task is already {v['status']}"]
            return v
        job.cancel()
        await asyncio.wait({job}, timeout=10)
        return self.task_view(task_id)

    # ---------- restart safety ----------
    async def shutdown(self) -> None:
        """Called on service stop: end our own jobs deterministically (kills only their process groups)."""
        self.shutting_down = True
        jobs = list(self._jobs.values())
        for j in jobs:
            j.cancel()
        if jobs:
            await asyncio.wait(jobs, timeout=10)

    def _kill_orphan(self, pid: int | None, starttime: str | None, agent: str = "claude", command_hash: str | None = None) -> bool:
        """Kill a leftover Claude process group from a previous bridge run, only if it is provably ours."""
        if not pid or starttime is None or proc_starttime(pid) != starttime:
            return False
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            if agent == "codex":
                parts = cmdline.split(b"\0")
                if not command_hash or hashlib.sha256(cmdline).hexdigest() != command_hash or self.cfg.codex_bin.encode() not in parts:
                    return False
            elif self.cfg.claude_bin.encode() not in cmdline or b"--restricted" not in cmdline:
                return False
            if os.getpgid(pid) != pid:
                return False
        except OSError:
            return False
        _kill_group(pid)
        return True

    def recover_interrupted(self) -> None:
        for t in self.store.active_tasks():
            killed = self._kill_orphan(t["pid"], t["starttime"], t["result"].get("agent", "claude"), t["result"].get("process_argv_hash"))
            r = t["result"]
            r.setdefault("agent", "claude")
            was = r.get("status")
            r.update(status="failed", finished_at=utcnow(), summary="interrupted by bridge restart", result="",
                     warnings=list(r.get("warnings", [])) + [
                         f"interrupted_by_restart: task was {was} when the bridge stopped"
                         + (f"; leftover {'Claude' if r['agent'] == 'claude' else 'Codex'} process group was killed" if killed else "")])
            self._finish(r, t.get("request_id") or "r_recovery")
            log.warning("task=%s marked interrupted (was %s, orphan_killed=%s)", t["task_id"], was, killed)
