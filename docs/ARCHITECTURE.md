# Architecture

MCP validates requests and dispatches task operations to shared queue/store. A selected executor runs in an isolated process group. Task metadata/results persist under BRIDGE_DATA_DIR; prompt content is not persisted.

Claude and Codex share queue, SQLite persistence, configured roots, timeout/cancellation, sanitization, and process cleanup. Codex file operations pass through Bridge read broker; native sandboxing is extra defense.

Installer renders a per-user systemd unit and points it at local config. Runtime state and credentials stay in local protected storage and are never copied.
