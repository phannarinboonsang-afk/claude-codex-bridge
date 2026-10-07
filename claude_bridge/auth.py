"""Optional bearer-token gate in front of the MCP endpoint (defense in depth behind the tunnel).

The token file holds either the bare token or the full header value "Bearer <token>", so the very same
file can be handed to tunnel-client via --mcp.extra-headers 'Authorization: file:/path'. The token is
never logged or returned."""
from __future__ import annotations

import hmac
import json
import logging
import stat
from pathlib import Path

log = logging.getLogger("claude_bridge")


def load_token(path: Path) -> bytes:
    st = path.stat()                     # raises FileNotFoundError -> bridge refuses to start without its token
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"auth token file {path} must not be accessible by group/others (chmod 600)")
    raw = path.read_text().strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if len(raw) < 24:
        raise ValueError("auth token is too short (need >= 24 characters)")
    return raw.encode()


class BearerAuth:
    def __init__(self, app, token: bytes):
        self.app = app
        self._token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        got = dict(scope["headers"]).get(b"authorization", b"").decode("latin1")
        if got[:7].lower() == "bearer " and hmac.compare_digest(got[7:].strip().encode(), self._token):
            return await self.app(scope, receive, send)
        client = (scope.get("client") or ("?",))[0]
        log.warning("auth rejected path=%s client=%s", scope.get("path"), client)
        body = json.dumps({"error": "unauthorized"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
