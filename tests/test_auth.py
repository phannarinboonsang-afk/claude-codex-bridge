import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from claude_bridge.auth import BearerAuth, load_token

TOKEN = "unit-test-token-0123456789abcdef-XYZ"


def test_load_token_formats_and_perms(tmp_path):
    f = tmp_path / "tok"
    f.write_text(TOKEN + "\n"); f.chmod(0o600)
    assert load_token(f) == TOKEN.encode()
    f.write_text("Bearer " + TOKEN); f.chmod(0o600)
    assert load_token(f) == TOKEN.encode()
    f.chmod(0o640)
    with pytest.raises(PermissionError):
        load_token(f)
    f.chmod(0o600); f.write_text("short")
    with pytest.raises(ValueError):
        load_token(f)
    with pytest.raises(FileNotFoundError):
        load_token(tmp_path / "missing")


async def test_bearer_middleware_unit(caplog):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    transport = httpx.ASGITransport(app=BearerAuth(app, TOKEN.encode()))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.get("/mcp")).status_code == 401
        assert (await c.get("/mcp", headers={"Authorization": "Bearer wrong-token-wrong-token-wrong"})).status_code == 401
        assert (await c.get("/mcp", headers={"Authorization": TOKEN})).status_code == 401          # scheme required
        assert (await c.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200
        assert (await c.get("/mcp", headers={"Authorization": f"bearer {TOKEN}"})).status_code == 200
    assert TOKEN not in caplog.text


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_http_server_requires_token(env):
    tokf = env["tmp"] / "mcp-token"
    tokf.write_text("Bearer " + TOKEN); tokf.chmod(0o600)
    port = _free_port()
    e = {**os.environ, "BRIDGE_PORT": str(port), "BRIDGE_DATA_DIR": str(env["tmp"] / "authdata"),
         "BRIDGE_CLAUDE_BIN": str(env["fake"]), "BRIDGE_READ_ROOTS": str(env["root"]), "BRIDGE_AUTH_TOKEN_FILE": str(tokf)}
    p = subprocess.Popen([sys.executable, "-m", "claude_bridge"], env=e, cwd=Path(__file__).parent.parent,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(60):
            try:
                httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
                break
            except httpx.TransportError:
                await asyncio.sleep(0.25)
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        h = {"Accept": "application/json, text/event-stream"}
        assert httpx.post(url, json=init, headers=h).status_code == 401
        assert httpx.post(url, json=init, headers={**h, "Authorization": "Bearer nope-nope-nope-nope-nope-nope"}).status_code == 401
        async with streamablehttp_client(url, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                assert (await s.call_tool("get_claude_status", {})).isError is False
    finally:
        p.terminate()
        p.wait(10)


def test_refuses_non_loopback_bind(env):
    e = {**os.environ, "BRIDGE_HOST": "0.0.0.0", "BRIDGE_DATA_DIR": str(env["tmp"] / "d2"),
         "BRIDGE_CLAUDE_BIN": str(env["fake"]), "BRIDGE_READ_ROOTS": str(env["root"])}
    r = subprocess.run([sys.executable, "-m", "claude_bridge"], env=e, cwd=Path(__file__).parent.parent,
                       capture_output=True, timeout=30)
    assert r.returncode != 0 and b"loopback" in r.stderr


def test_refuses_to_start_with_missing_token_file(env):
    e = {**os.environ, "BRIDGE_DATA_DIR": str(env["tmp"] / "d3"), "BRIDGE_CLAUDE_BIN": str(env["fake"]),
         "BRIDGE_READ_ROOTS": str(env["root"]), "BRIDGE_AUTH_TOKEN_FILE": str(env["tmp"] / "nope")}
    r = subprocess.run([sys.executable, "-m", "claude_bridge"], env=e, cwd=Path(__file__).parent.parent,
                       capture_output=True, timeout=30)
    assert r.returncode != 0
