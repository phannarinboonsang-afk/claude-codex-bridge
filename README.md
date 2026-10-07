# Claude and Codex Bridge

Local Streamable HTTP MCP service for read-only Claude Code and Codex CLI tasks. It binds to loopback, enforces configured workspace/read roots, and keeps credentials and task state outside source.

## Fresh-machine setup

1. Clone on Linux with Python 3.11+ and review config.example.
2. Preview scripts/install.sh --dry-run, then run scripts/install.sh. Installer creates a venv, installs locked dependencies, preserves existing config, and writes a user systemd unit.
3. Copy config.example to local config if needed; set absolute workspace/read-root and executable paths. Keep loopback binding, writer disabled, and Codex disabled until local authentication and verification.
4. Authenticate Claude Code and Codex locally as the service user. Do not provide OPENAI_API_KEY to Bridge.
5. Run scripts/verify.sh --prerequisites-only and .venv/bin/python -m pytest -q.
6. Review config/unit, then start with systemctl --user enable --now claude-chatgpt-bridge.service, then run scripts/verify.sh for authenticated localhost MCP health.

AUTH_HEADER_FILE is only a path to an operator-created protected file. Installer never creates, reads, or copies credential contents. See docs and SECURITY.md. Default profile is chatgpt; generic executor selection uses agents. Writer/Codex are disabled by default.
