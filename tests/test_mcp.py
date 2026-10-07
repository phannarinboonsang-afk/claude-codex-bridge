"""MCP protocol tests: in-memory session (schema validation, tools) and a real Streamable HTTP server."""
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.memory import create_connected_server_and_client_session

from claude_bridge.server import build_server

CHATGPT_TOOLS = {"start_claude_task", "get_claude_task", "cancel_claude_task", "get_claude_status", "get_last_result",
                 "get_agent_messages", "get_project_state"}
TOOLS = CHATGPT_TOOLS | {"ask_claude"}   # profile "full"


async def test_initialize_and_list_tools_in_memory(bridge):
    mcp, _ = build_server(bridge.cfg, bridge)
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == TOOLS
        schema = tools["ask_claude"].inputSchema
        assert schema["required"] == ["prompt"]
        assert schema["properties"]["mode"]["enum"] == ["read_only", "writer"]
        assert schema["properties"]["mode"]["default"] == "read_only"
        assert tools["get_project_state"].annotations.readOnlyHint is True
        assert tools["get_claude_status"].annotations.readOnlyHint is True


@pytest.mark.parametrize("args", [
    {},                                                   # missing prompt
    {"prompt": ""},                                       # too short
    {"prompt": "x" * 20001},                              # too long
    {"prompt": "x", "mode": "root"},                      # bad enum
    {"prompt": "x", "timeout": 0},
    {"prompt": "x", "timeout": 601},
    {"prompt": "x", "timeout": "soon"},
    {"prompt": 123},
])
async def test_input_schema_validation(bridge, args, env):
    mcp, _ = build_server(bridge.cfg, bridge)
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        res = await c.call_tool("ask_claude", args)
        assert res.isError is True
    assert not (env["fake"].parent / "argv.json").exists()


async def test_tools_end_to_end_in_memory(bridge, env):
    mcp, _ = build_server(bridge.cfg, bridge)
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        st = (await c.call_tool("get_claude_status", {})).structuredContent
        assert st["running"] is False and st["current_task_id"] is None and st["writer_enabled"] is False
        assert st["claude_cli_version"].startswith("9.9.9")
        r = (await c.call_tool("ask_claude", {"prompt": "hi"})).structuredContent
        assert r["status"] == "completed"
        last = (await c.call_tool("get_last_result", {})).structuredContent
        assert last["task_id"] == r["task_id"]
        miss = (await c.call_tool("get_last_result", {"task_id": "t_none"})).structuredContent
        assert miss["found"] is False
        inbox = (await c.call_tool("get_agent_messages", {"mark_read": True})).structuredContent
        assert inbox["count"] == 1 and inbox["messages"][0]["task_id"] == r["task_id"]
        assert (await c.call_tool("get_agent_messages", {})).structuredContent["count"] == 0
        # writer denied via MCP
        w = (await c.call_tool("ask_claude", {"prompt": "x", "mode": "writer"})).structuredContent
        assert w["status"] == "blocked"
        # running status while a task is in flight
        t = asyncio.create_task(c.call_tool("ask_claude", {"prompt": "SLOW"}))
        await asyncio.sleep(0.7)
        st = (await c.call_tool("get_claude_status", {})).structuredContent
        assert st["running"] is True and st["mode"] == "read_only" and st["current_task_id"]
        await t


async def test_project_state_tool(bridge, env):
    root = env["root"]
    subprocess.run("git init -q && git config user.email a@b.c && git config user.name t && echo hi > f.txt "
                   "&& git add f.txt && git commit -qm first && echo more >> f.txt && echo x > new.txt && echo s > .env "
                   "&& mkdir reports && echo r > reports/r1.md",
                   shell=True, cwd=root, check=True)
    mcp, _ = build_server(bridge.cfg, bridge)
    before = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        s = (await c.call_tool("get_project_state", {"project_path": str(root)})).structuredContent
        g = s["git"]
        assert g["branch"] in ("master", "main") and len(g["head"]) == 40 and g["dirty"] is True
        assert g["counts"]["modified"] == 1 and g["counts"]["untracked"] >= 2
        assert any(f["path"] == "[hidden: secret-like name]" for f in g["changed_files"])   # .env masked
        assert s["evidence"]["reports"][0]["path"] == "r1.md"
        for bad in (str(env["outside"]), f"{root}/../outside", f"{root}/.ssh"):
            e = (await c.call_tool("get_project_state", {"project_path": bad})).structuredContent
            assert "error" in e
    after = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    assert before == after                       # no mutation
    assert not (root / ".git" / "index.lock").exists()


# ---------- real Streamable HTTP ----------
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_streamable_http_initialize_list_call(env):
    port = _free_port()
    e = {**os.environ, "BRIDGE_PORT": str(port), "BRIDGE_DATA_DIR": str(env["tmp"] / "httpdata"),
         "BRIDGE_CLAUDE_BIN": str(env["fake"]), "BRIDGE_READ_ROOTS": str(env["root"])}
    p = subprocess.Popen([sys.executable, "-m", "claude_bridge"], env=e, cwd=Path(__file__).parent.parent,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(60):
            try:
                httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
                break
            except httpx.TransportError:
                await asyncio.sleep(0.25)
        async with streamablehttp_client(url) as (r, w, _):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                assert init.serverInfo.name == "claude-bridge"
                assert {t.name for t in (await s.list_tools()).tools} == CHATGPT_TOOLS
                started = await s.call_tool("start_claude_task", {"prompt": "SLOW http"})
                tid = started.structuredContent["task_id"]
        # client disconnected; task must survive and be pollable from a brand-new connection
        async with streamablehttp_client(url) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                for _ in range(40):
                    v = (await s.call_tool("get_claude_task", {"task_id": tid})).structuredContent
                    if v["status"] == "completed":
                        break
                    await asyncio.sleep(0.5)
                assert v["status"] == "completed" and "bridge working" in v["result"]
        # DNS-rebinding protection: foreign Host header is rejected
        bad = httpx.post(url, headers={"Host": "evil.example.com", "Accept": "application/json, text/event-stream"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert bad.status_code in (400, 421)
    finally:
        p.terminate()
        p.wait(10)


CLIENT = r"""
import asyncio, sys
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client(sys.argv[1]) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("start_claude_task", {"prompt": "SPAWN abrupt", "timeout": 60})
            print(res.structuredContent["task_id"], flush=True)
            await asyncio.sleep(3600)          # parent kills us with SIGKILL: no clean MCP shutdown
asyncio.run(main())
"""


async def test_http_abrupt_client_kill_then_cancel_over_http(env):
    """Task survives a SIGKILLed MCP client; cancel over HTTP kills only its own tree; outcome persisted in SQLite."""
    import sqlite3
    from conftest import alive, pids
    port = _free_port()
    data = env["tmp"] / "httpdata2"
    e = {**os.environ, "BRIDGE_PORT": str(port), "BRIDGE_DATA_DIR": str(data),
         "BRIDGE_CLAUDE_BIN": str(env["fake"]), "BRIDGE_READ_ROOTS": str(env["root"])}
    p = subprocess.Popen([sys.executable, "-m", "claude_bridge"], env=e, cwd=Path(__file__).parent.parent,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bystander = subprocess.Popen(["sleep", "60"])
    client = None
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(60):
            try:
                httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
                break
            except httpx.TransportError:
                await asyncio.sleep(0.25)
        client = subprocess.Popen([sys.executable, "-c", CLIENT, url], stdout=subprocess.PIPE, text=True)
        tid = (await asyncio.to_thread(client.stdout.readline)).strip()
        assert tid.startswith("t_")
        for _ in range(40):
            if pids(env) and pids(env, "children.log"):
                break
            await asyncio.sleep(0.25)
        client.kill()                            # abrupt disconnect
        client.wait(10)
        await asyncio.sleep(1.5)
        claude_pid, child_pid = pids(env)[0], pids(env, "children.log")[0]
        assert alive(claude_pid) and alive(child_pid)
        async with streamablehttp_client(url) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                v = (await s.call_tool("get_claude_task", {"task_id": tid})).structuredContent
                assert v["status"] == "running"
                c = (await s.call_tool("cancel_claude_task", {"task_id": tid})).structuredContent
                assert c["status"] == "cancelled" and c["completed_at"]
        await asyncio.sleep(0.5)
        assert not alive(claude_pid) and not alive(child_pid)   # the task's whole tree is gone
        assert bystander.poll() is None and p.poll() is None    # unrelated process and the bridge survive
        db = sqlite3.connect(f"file:{data / 'bridge.db'}?mode=ro", uri=True)
        status, pid = db.execute("SELECT status, pid FROM tasks WHERE task_id=?", (tid,)).fetchone()
        db.close()
        assert status == "cancelled" and pid is None
    finally:
        for proc in (client, bystander):
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait()
        p.terminate()
        p.wait(10)
