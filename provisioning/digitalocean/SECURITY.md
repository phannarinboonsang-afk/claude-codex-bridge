# Architecture and security boundaries

ChatGPT App -> existing Orchestrator -> authenticated worker channel -> Worker API
-> root-controlled per-run systemd service -> non-root filesystem sandbox -> CLI.
The Orchestrator owns state, transcript, GPT coordination, checkpoints, owner
input, Pause/Continue/Interrupt/Stop and WAITING_FOR_OWNER. RAG is not involved.

The worker has no deploy/merge/force-push/production restart endpoint. Such requests
are owner-gated by the Orchestrator. No outputs are automatically applied.

## API contract

`create` takes exactly `{spec,idempotency_key}`. Spec fields:
`run_id,repository,base_commit,agent,profile,task,limits`. Root policy resolves
repository identity. Registration derives worktree and binds source URL, toolchain
manifest digest and spec SHA256. Requests cannot supply worktree, command, PID,
credential or executable. Other operations take exactly `{run_id,spec_digest}`.

Profiles are fixed `codex-task`, `claude-task` or synthetic tree/memory/pids/cpu/
timeout. Tasks are bounded data sent to the fixed CLI stdin, not shell commands.
Agents themselves can execute project commands inside their non-root sandbox;
that capability is not an arbitrary privileged or ChatGPT shell endpoint.

SQLite WAL/FULL synchronization provides immutable specs, idempotent create,
durable ordered events and task states. One STARTING/RUNNING/UNKNOWN reservation
is allowed. No start replay after non-CREATED state. API restart changes in-flight
state to UNKNOWN; inspect cannot call absent/not-started units COMPLETED. Successful
loaded executed units can reach COMPLETED, not semantic DONE. The Orchestrator
must independently accept returned results before its own terminal state.

## Filesystem/source

Root controls `/var/lib/agentbridge-worker`, policy, API auth and `/opt` code. Agents
write only `/srv/agentbridge/runs/<32hex>/{worktree,runtime,artifacts}`. Bwrap includes
only system runtime, its fixed runner/toolchain and that run's directories. Home,
controller state, other runs and production paths are absent. Symlink/path traversal
is rejected at root-owned anchor boundaries. Cleanup requires stopped/complete/failed
state and an empty exact systemd run boundary; no generic rm path is accepted.

Approved HTTPS repository URL and exact 40hex base commit are independently cloned
and checked out as the agent user with clean Git environment. Private-source
credential support is not released; public approved repositories only initially.
Source acquisition and checkout happen inside the same per-run cgroup and bwrap
sandbox before launching the selected agent; timeout/stop includes Git descendants.

## Containment

Guest must have exclusively unified cgroup2, cpu/memory/pids and systemd >=249.
Actual runtime units derive bounded limits and enforce MemoryMax, MemorySwapMax=0,
TasksMax, CPUQuota, RuntimeMaxSec, KillMode=control-group and non-root identity.
No delegated direct root subtree is used. Systemd is the single cgroup manager.
Stop must leave MainPID=0, inactive unit, and no tasks under the exact run subtree.
Empty-boundary proof is required before filesystem/unit cleanup.

Synthetic acceptance checks actual membership, controller counters, non-root UID,
memory requested aggregate/counter/peak/contained descendants, PID allocation
failure/counter, measured CPU usage plus throttling, timeout, descendant stop,
serial A/B isolation and exact cleanup. Memory test does not require child death.
No source repository or model credential is required by this proof.

## Network

DigitalOcean firewall has SSH-only inbound and no API port. Worker API binds only
loopback. Cloud firewall outbound rules are coarse ports, never claimed as domain
filtering. Guest nft rules allow agent UID only to local CONNECT proxy 3128,
rejecting all other output including IPv4/IPv6 metadata/private/production targets.
Rules persist via a guest-only dedicated oneshot service; no global nft flush.

Proxy accepts only exact approved hostnames on TCP443 and requires all DNS answers
to be global. It connects to the validated numeric IP to prevent second-resolution
rebinding. No arbitrary HTTP forwarding, wildcard hostname or private destination.
This is a candidate CONNECT host allowlist, not TLS payload/URL inspection: shared
CDN routing, client proxy compatibility and domain-fronting risks require review
and real negative tests before enabling writers. No enforcement claim is made yet.
Package endpoint sets must be adapted explicitly to the approved project.

## Encrypted channel/authentication lifecycle

The owner configures a dedicated `agentbridge-tunnel` guest user using only a public
ed25519 key with configure-tunnel.py. Authorized key permits forwarding only to
127.0.0.1:8765, no command/session/PTY/agent forwarding. sshd also permits only local
forwarding and MaxSessions=0. Guest ssh reload is owner setup only, never production host action.
The Orchestrator later opens a dedicated SSH -N local-forward with pinned host key,
ExitOnForwardFailure, ServerAliveInterval/CountMax; no accept-new bypass. Its private
key is generated/stored only on the authorized control side, never in this bundle,
guest, worktree or chat. Preparing this connection does not modify production host in this phase.

Worker API token is generated guest-root only, file0600. Owner securely transfers it
into the Orchestrator's worker-specific secret store, without printing it in chat or
transcripts. Root replaces/reloads it on rotation; revoke tunnel key independently.
Never reuse Bridge bearer or production model credentials. The owner controls
worker-specific model auth and spend separately. Logs must not contain tokens.

## Explicit limitations before release

No Linux systemd/cloud-init/bwrap/nft runtime was available during Windows staging.
HTTP is single-threaded with a 5s accepted-connection timeout; a malicious permitted
control client can still delay other requests. Exclusive local lock serializes create.
Collect bounds total serialized JSON result, including escaping and small text artifacts.
Fixed pytest/npm test/build steps run in the same sandbox after the CLI. Actual project
dependency compatibility and toolchain CLI flags, proxy compatibility, current-boot readiness,
Orchestrator protocol adapter and failure/reconnect behavior need integration proof.
These limitations keep release BLOCKED and writer policy false.
