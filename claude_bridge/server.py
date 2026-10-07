"""MCP server (Streamable HTTP) exposing the bridge tools."""
from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import Config
from .project_state import get_project_state as read_project_state
from .runner import Bridge
from .security import PathError, sanitize, validate_project_path

log = logging.getLogger("claude_bridge")

TASK_ID = Field(min_length=3, max_length=20, pattern=r"^t_[0-9a-f]{12}$", description="task_id returned by start_claude_task")


def build_server(cfg: Config, bridge: Bridge | None = None) -> tuple[FastMCP, Bridge]:
    bridge = bridge or Bridge(cfg)
    mcp = FastMCP(
        "claude-bridge",
        instructions="Bridge to a local Claude Code CLI on AGX. Default mode is read_only; writer mode is disabled unless allowlisted.",
        host=cfg.host,
        port=cfg.port,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )

    @mcp.tool(annotations=ToolAnnotations(title="Ask Claude Code", readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    async def ask_claude(
        prompt: Annotated[str, Field(min_length=1, max_length=20000, description="Instruction for Claude Code")],
        project_path: Annotated[str | None, Field(max_length=4096, description="Absolute path inside an allowed root; defaults to the bridge project")] = None,
        mode: Annotated[Literal["read_only", "writer"], Field(description="read_only (default) or writer (needs explicit server allowlist)")] = "read_only",
        timeout: Annotated[int | None, Field(ge=1, le=600, description="Seconds before the Claude process is killed")] = None,
    ) -> dict[str, Any]:
        """DEPRECATED synchronous variant (blocks until done). Prefer start_claude_task + get_claude_task."""
        return await bridge.ask_claude(prompt, project_path, mode, timeout, request_id="r_" + uuid.uuid4().hex[:10])

    @mcp.tool(annotations=ToolAnnotations(title="Start Claude task (async)", readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    async def start_claude_task(
        prompt: Annotated[str, Field(min_length=1, max_length=20000, description="Instruction for Claude Code")],
        project_path: Annotated[str | None, Field(max_length=4096, description="Absolute path inside an allowed root; defaults to the bridge project")] = None,
        mode: Annotated[Literal["read_only", "writer"], Field(description="read_only (default); writer needs explicit server allowlist")] = "read_only",
        timeout: Annotated[int | None, Field(ge=1, le=600, description="Seconds the Claude process may run before it is killed")] = None,
    ) -> dict[str, Any]:
        """Queue a Claude Code task and return immediately with a task_id. Poll get_claude_task."""
        return await bridge.start_task(prompt, project_path, mode, timeout, request_id="r_" + uuid.uuid4().hex[:10])

    @mcp.tool(annotations=ToolAnnotations(title="Get Claude task", readOnlyHint=True, openWorldHint=False))
    async def get_claude_task(task_id: Annotated[str, TASK_ID]) -> dict[str, Any]:
        """Status and result of a task: queued, running, completed, failed, blocked, timed_out or cancelled."""
        return bridge.task_view(task_id)

    @mcp.tool(annotations=ToolAnnotations(title="Cancel Claude task", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def cancel_claude_task(task_id: Annotated[str, TASK_ID]) -> dict[str, Any]:
        """Cancel a queued/running task started by this bridge. Kills only that task's own process group."""
        return await bridge.cancel_task(task_id)

    @mcp.tool(annotations=ToolAnnotations(title="Claude status", readOnlyHint=True, openWorldHint=False))
    async def get_claude_status() -> dict[str, Any]:
        """Whether Claude is currently running a task, and which."""
        import time
        running = bridge.registry.snapshot()
        cur = running[-1] if running else None
        return {
            "running": bool(running),
            "current_task_id": cur.task_id if cur else None,
            "project": cur.project if cur else None,
            "started_at": cur.started_at if cur else None,
            "elapsed_s": int(time.monotonic() - cur.started_mono) if cur else None,
            "mode": cur.mode if cur else None,
            "model": bridge.last_model,
            "model_note": "from last completed task or BRIDGE_CLAUDE_MODEL" if bridge.last_model else "not yet detected",
            "queued_count": bridge.queue_depth(),
            "running_tasks": [{"task_id": r.task_id, "project": r.project, "mode": r.mode, "started_at": r.started_at} for r in running],
            "claude_cli_version": await bridge.cli_version(),
            "writer_enabled": bridge.cfg.writer_enabled and bool(bridge.cfg.writer_roots),
        }

    @mcp.tool(annotations=ToolAnnotations(title="Start agent task (async)", readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    async def start_agent_task(
        agent: Literal["claude", "codex"],
        prompt: Annotated[str, Field(min_length=1, max_length=20000)],
        project_path: Annotated[str | None, Field(max_length=4096)] = None,
        mode: Literal["read_only", "writer"] = "read_only",
        timeout: Annotated[int | None, Field(ge=1, le=600)] = None,
    ) -> dict[str, Any]:
        """Queue a task using Claude or Codex. Codex supports read_only only. Poll get_agent_task."""
        return await bridge.start_task(prompt, project_path, mode, timeout,
                                       request_id="r_" + uuid.uuid4().hex[:10], agent=agent)

    @mcp.tool(annotations=ToolAnnotations(title="Get agent task", readOnlyHint=True, openWorldHint=False))
    async def get_agent_task(task_id: Annotated[str, TASK_ID]) -> dict[str, Any]:
        """Read status and sanitized result of either executor, including agent and completed_at."""
        return bridge.task_view(task_id)

    @mcp.tool(annotations=ToolAnnotations(title="Cancel agent task", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def cancel_agent_task(task_id: Annotated[str, TASK_ID]) -> dict[str, Any]:
        """Cancel only the selected Bridge task process group; queued tasks never launch."""
        return await bridge.cancel_task(task_id)

    @mcp.tool(annotations=ToolAnnotations(title="Agent status", readOnlyHint=True, openWorldHint=False))
    async def get_agent_status() -> dict[str, Any]:
        """Shared queue/readers and per-executor availability and last known model."""
        from pathlib import Path
        from .runner import run_process
        from .executors import codex_env
        version = None
        if cfg.codex_enabled and Path(cfg.codex_bin).is_absolute():
            try:
                outcome = await run_process([cfg.codex_bin, "--version"], Path("/"), b"", codex_env(cfg), 15)
                if outcome.returncode == 0: version = sanitize(outcome.stdout, 100).strip()
            except OSError:
                pass
        running = bridge.registry.snapshot()
        return {"running": bool(running), "queued_count": bridge.queue_depth(),
                "reader_limit": cfg.max_concurrent_readers,
                "writer_enabled": cfg.writer_enabled and bool(cfg.writer_roots),
                "agents": {"claude": {"enabled": True, "version": await bridge.cli_version(), "model": bridge.last_model},
                           "codex": {"enabled": cfg.codex_enabled, "version": version, "model": bridge.last_models["codex"], "mode": "read_only"}},
                "running_tasks": [{"task_id": r.task_id, "agent": r.agent, "project": r.project,
                                   "mode": r.mode, "started_at": r.started_at} for r in running]}

    @mcp.tool(annotations=ToolAnnotations(title="Last result", readOnlyHint=True, openWorldHint=False))
    async def get_last_result(task_id: Annotated[str | None, Field(max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")] = None) -> dict[str, Any]:
        """Structured result of a task (latest finished task if task_id is omitted)."""
        r = bridge.store.get_task(task_id, only_completed=task_id is None)
        if r: r.setdefault("agent", "claude")
        return r if r else {"found": False, "task_id": task_id, "message": "no matching task"}

    @mcp.tool(annotations=ToolAnnotations(title="Agent inbox", readOnlyHint=False, idempotentHint=True, openWorldHint=False))
    async def get_agent_messages(
        unread_only: bool = True,
        mark_read: bool = False,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        task_id: Annotated[str | None, Field(max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")] = None,
    ) -> dict[str, Any]:
        """Messages Claude left for ChatGPT. Only inbox read-state changes when mark_read=true."""
        msgs = bridge.store.get_messages(unread_only=unread_only, mark_read=mark_read, limit=limit, task_id=task_id)
        return {"count": len(msgs), "messages": msgs}

    @mcp.tool(annotations=ToolAnnotations(title="Project state", readOnlyHint=True, openWorldHint=False))
    async def get_project_state(project_path: Annotated[str, Field(min_length=1, max_length=4096)]) -> dict[str, Any]:
        """Git branch/HEAD/dirty summary and test/report/evidence file state. Read-only."""
        try:
            p = validate_project_path(project_path, bridge.cfg.read_roots)
        except PathError as e:
            return {"error": e.code, "message": str(e)}
        return await read_project_state(p)

    if cfg.tool_profile not in ("agents", "agents_full"):
        for name in ("start_agent_task", "get_agent_task", "cancel_agent_task", "get_agent_status"):
            mcp.remove_tool(name)
    if cfg.tool_profile in ("chatgpt", "agents"):      # ChatGPT-facing surface: async tools + read-only helpers, no blocking call
        mcp.remove_tool("ask_claude")
    return mcp, bridge
