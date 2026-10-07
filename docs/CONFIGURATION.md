# Configuration reference

Create a private config from config.example. Use absolute paths; this file contains no credential values.

| Setting | Default / behavior |
|---|---|
| BRIDGE_WORKSPACE_ROOT | Source checkout |
| BRIDGE_READ_ROOTS | Workspace only when unset; path-separated |
| BRIDGE_PROTECTED_WRITE_ROOTS | Workspace protected; add operator-protected roots |
| CLAUDE_EXECUTABLE | ~/.local/bin/claude; legacy BRIDGE_CLAUDE_BIN accepted |
| CODEX_EXECUTABLE | codex; legacy BRIDGE_CODEX_BIN accepted |
| BRIDGE_LISTEN_HOST | 127.0.0.1; legacy BRIDGE_HOST accepted |
| BRIDGE_LISTEN_PORT | 5077; legacy BRIDGE_PORT accepted |
| BRIDGE_PROFILE | chatgpt; legacy BRIDGE_TOOL_PROFILE accepted |
| AUTH_HEADER_FILE | Unset; path to protected local auth file |
| BRIDGE_DATA_DIR | ~/.local/state/claude-chatgpt-bridge |
| BRIDGE_CODEX_ENABLED | 0 |
| BRIDGE_WRITER_ENABLED | 0 |

Timeout/budget settings retain implementation defaults. Environment overrides config. Use simple KEY=value lines, not shell syntax.
