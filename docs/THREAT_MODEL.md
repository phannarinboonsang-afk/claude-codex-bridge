# Threat model

## Protected assets

Workspace file contents, credentials and private keys, local CLI login state, task results, runtime database, and host/service identity.

## Trust boundaries

The MCP listener is loopback-only. Bearer authentication is an additional local gate when configured. Workspace roots and deny rules are enforced in Bridge code. Codex read requests pass through the Bridge broker; Codex's own sandbox is defense in depth. Executor subprocesses use minimized environments and task-scoped process groups.

## In scope

Untrusted MCP prompts, path traversal, symlink escape, secret-looking files inside an allowed root, output leakage, child process persistence after timeout/cancel, and unsafe local configuration.

## Out of scope / operator responsibilities

The host user account, OS kernel, systemd user manager, installed CLI binaries, local CLI authentication stores, and administrators are trusted. Keep host packages and CLI versions reviewed. Do not expose the listener publicly or share the service user's login. Configure only workspaces that are safe for the selected executor to inspect.

## Limits

The installer supports Linux systemd user services only. A fresh installation needs operator-supplied CLI authentication and a reviewed local config. Codex 0.160.0 is the security-validated version; other versions fail closed. Read-only behavior does not make output harmless: review agent output before acting on it.
