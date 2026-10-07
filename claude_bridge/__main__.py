from __future__ import annotations

import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager

import uvicorn

from .auth import BearerAuth, load_token
from .config import Config
from .security import sanitize
from .server import build_server


class _Sanitizing(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize(record.getMessage())
        record.args = ()
        return True


def main() -> None:
    cfg = Config.from_env()
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers = [logging.handlers.RotatingFileHandler(cfg.log_dir / "bridge.log", maxBytes=2_000_000, backupCount=3),
                logging.StreamHandler(sys.stderr)]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(_Sanitizing())
        root.addHandler(h)
    if cfg.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"refusing to bind {cfg.host}: the bridge must listen on loopback only")
    mcp, bridge = build_server(cfg)
    app = mcp.streamable_http_app()
    inner_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner_lifespan(a):
            yield
            await bridge.shutdown()          # mark/kill our own tasks before the process exits

    app.router.lifespan_context = lifespan
    if cfg.auth_token_file:
        app.add_middleware(BearerAuth, token=load_token(cfg.auth_token_file))
    log = logging.getLogger("claude_bridge")
    log.info("starting on http://%s:%s/mcp profile=%s auth=%s writer_enabled=%s", cfg.host, cfg.port, cfg.tool_profile,
             "enabled" if cfg.auth_token_file else "none", cfg.writer_enabled)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
