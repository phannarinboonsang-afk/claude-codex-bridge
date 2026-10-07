"""Runtime configuration. Everything is overridable by env so tests can inject temp dirs."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BRIDGE_HOME = Path(__file__).resolve().parent.parent
# The checkout workspace is protected from writer roots by default.

DEFAULT_WRITER_BASH_ALLOW = (
    "git status:*",
    "git diff:*",
    "git log:*",
    "git show:*",
    "git add:*",
    "git commit:*",
    "python3 -m pytest:*",
    "pytest:*",
)

# Always denied to Claude (belt and braces on top of the allowlist).
BASH_DENY = (
    "git reset:*",
    "git clean:*",
    "git push:*",
    "git checkout:*",
    "git restore:*",
    "git rebase:*",
    "git stash:*",
    "systemctl:*",
    "sudo:*",
    "rm:*",
    "curl:*",
    "wget:*",
    "psql:*",
    "ssh:*",
    "scp:*",
)

# Read denies given to Claude for secret-looking files (gitignore-style globs).
READ_DENY = (
    "Read(**/.env)",
    "Read(**/.env.*)",
    "Read(**/*.env)",
    "Read(**/*.env.*)",
    "Read(**/*.pem)",
    "Read(**/*.key)",
    "Read(**/*.p12)",
    "Read(**/*.pfx)",
    "Read(**/id_rsa*)",
    "Read(**/id_ed25519*)",
    "Read(**/id_ecdsa*)",
    "Read(**/*secret*)",
    "Read(**/*token*)",
    "Read(**/*credential*)",
    "Read(**/*password*)",
    "Read(**/.netrc)",
    "Read(**/.pgpass)",
    "Read(//home/*/.ssh/**)",
    "Read(//home/*/.aws/**)",
    "Read(//home/*/.gnupg/**)",
    "Read(//home/*/.claude/**)",
    "Read(//etc/**)",
    "Read(//proc/**)",
)


def _configured_path(value: str, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path.resolve()


def _paths(value: str | None, default: tuple[Path, ...], name: str) -> tuple[Path, ...]:
    if value is None:
        return default
    values = [part.strip() for part in value.split(os.pathsep) if part.strip()]
    return tuple(_configured_path(part, name) for part in values)


def _env_value(env: dict[str, str], preferred: str, legacy: str, default: str | None) -> str | None:
    if preferred in env:
        return env[preferred]
    if legacy in env:
        return env[legacy]
    return default


@dataclass(frozen=True)
class Config:
    workspace_root: Path = BRIDGE_HOME
    host: str = "127.0.0.1"
    port: int = 5077
    claude_bin: str = str(Path.home() / ".local" / "bin" / "claude")
    claude_model: str | None = None
    codex_bin: str = "codex"
    codex_model: str | None = None
    codex_enabled: bool = False
    data_dir: Path = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "claude-chatgpt-bridge"
    read_roots: tuple[Path, ...] = (BRIDGE_HOME,)
    protected_write_roots: tuple[Path, ...] = (BRIDGE_HOME,)
    writer_roots: tuple[Path, ...] = ()
    writer_enabled: bool = False
    writer_bash_allow: tuple[str, ...] = DEFAULT_WRITER_BASH_ALLOW
    default_timeout: int = 120
    max_timeout: int = 600
    max_output_bytes: int = 200_000
    max_budget_usd: float = 1.0
    max_concurrent_readers: int = 2
    max_queue: int = 20
    auth_token_file: Path | None = None
    tool_profile: str = "chatgpt"   # chatgpt = async tools (start/get/cancel) + helpers; full = + ask_claude
    child_path: str = field(default_factory=lambda: os.environ.get("PATH", f"{Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bridge.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ
        d = cls()
        workspace_root = _configured_path(
            e.get("BRIDGE_WORKSPACE_ROOT", str(d.workspace_root)), "BRIDGE_WORKSPACE_ROOT"
        )
        read_roots = _paths(e.get("BRIDGE_READ_ROOTS"), (workspace_root,), "BRIDGE_READ_ROOTS")
        if not read_roots:
            raise ValueError("BRIDGE_READ_ROOTS must contain at least one absolute path")
        protected_extra = _paths(e.get("BRIDGE_PROTECTED_WRITE_ROOTS"), (), "BRIDGE_PROTECTED_WRITE_ROOTS")
        protected_write_roots = tuple(dict.fromkeys((workspace_root, *protected_extra)))
        claude_bin = _env_value(e, "CLAUDE_EXECUTABLE", "BRIDGE_CLAUDE_BIN", d.claude_bin)
        codex_bin = _env_value(e, "CODEX_EXECUTABLE", "BRIDGE_CODEX_BIN", d.codex_bin)
        auth_file = _env_value(e, "AUTH_HEADER_FILE", "BRIDGE_AUTH_TOKEN_FILE", None)
        return cls(
            workspace_root=workspace_root,
            host=_env_value(e, "BRIDGE_LISTEN_HOST", "BRIDGE_HOST", d.host),
            port=int(_env_value(e, "BRIDGE_LISTEN_PORT", "BRIDGE_PORT", str(d.port))),
            claude_bin=claude_bin,
            claude_model=e.get("BRIDGE_CLAUDE_MODEL") or None,
            codex_bin=codex_bin,
            codex_model=e.get("BRIDGE_CODEX_MODEL") or None,
            codex_enabled=e.get("BRIDGE_CODEX_ENABLED", "0") == "1",
            data_dir=_configured_path(e.get("BRIDGE_DATA_DIR", str(d.data_dir)), "BRIDGE_DATA_DIR"),
            read_roots=read_roots,
            protected_write_roots=protected_write_roots,
            writer_roots=_paths(e.get("BRIDGE_WRITER_ROOTS"), (), "BRIDGE_WRITER_ROOTS"),
            writer_enabled=e.get("BRIDGE_WRITER_ENABLED", "0") == "1",
            default_timeout=int(e.get("BRIDGE_DEFAULT_TIMEOUT", d.default_timeout)),
            max_timeout=int(e.get("BRIDGE_MAX_TIMEOUT", d.max_timeout)),
            max_budget_usd=float(e.get("BRIDGE_MAX_BUDGET_USD", d.max_budget_usd)),
            tool_profile=_env_value(e, "BRIDGE_PROFILE", "BRIDGE_TOOL_PROFILE", d.tool_profile),
            auth_token_file=_configured_path(auth_file, "AUTH_HEADER_FILE") if auth_file else None,
        )
