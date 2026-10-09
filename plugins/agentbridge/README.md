# Private AgentBridge App V1

Control/view surface for the server-side coordinator. Nine scoped tools; no shell.
Uses MCP Apps SDK 1.2.2, `ui://agentbridge/run-v1.html`, bundled resource with no external connections.
`npm ci --ignore-scripts && npm run build` creates the served resource.

The server entrypoint is `python -B -m backend.orchestrator --transport stdio`
or authenticated loopback `--transport mcp-http --port 15089`.
Required operator arguments: `--runtime`, `--sources`, `--bridge-url`,
`--bridge-auth-file`, `--auth-file`, `--principal`.
These are file paths/registered configuration only; never credential values.

Authenticated SSH/stdio inherits one operator-established principal. Private HTTP
maps one bearer to that principal; body actor fields are rejected. Browser view
calls host tool APIs; no bearer or provider key is delivered to the view.
Separate authenticated remote exposure/host connection must be reviewed before
real ChatGPT acceptance. No tunnel or production service is configured here.

The view refreshes/polls every 5 seconds while visible and nonterminal; failures
back off up to 30 seconds. Pagehide stops polling. Native ChatGPT assistant turns
are not required or synthesized. ChatGPT host acceptance is separate from DOM
and protocol tests. No production deployment is authorized.
