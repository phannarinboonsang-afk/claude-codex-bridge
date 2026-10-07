# Compatibility matrix

| Component | Supported / verified |
|---|---|
| OS | Linux for supplied systemd user installer |
| Python | 3.11+; baseline on 3.11 |
| MCP SDK | 1.30.0 in lock |
| Claude Code CLI | Install/authenticate separately as service user |
| Codex CLI | 0.160.0 security-validated; others fail closed |
| Service manager | systemd user manager |
| Listener | Loopback only |

Revalidate lock and CLI security tests before updating versions.
