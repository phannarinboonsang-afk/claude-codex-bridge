# AgentBridge integration staging

The existing Bridge CLI, MCP, configuration, installer and read-only defaults remain at their original paths. New independent backend packages retain `backend.agent_chat` and `backend.orchestrator` imports. The isolated Angular chat workspace is under `agent_chat/ui`; the MCP App is under `plugins/agentbridge`.

ChatGPT App -> Orchestrator -> GPT coordinator -> authenticated execution boundary. Shared transcript, checkpoints, owner input, Pause/Continue/Interrupt/Stop and approval gates belong to the Orchestrator. Local Bridge remains read-only by default. Writers belong exclusively on a dedicated external Worker with a loopback API reached through an authenticated encrypted tunnel.

Worker runtime/provisioning is packaged under `provisioning/digitalocean`; `worker/protocol.py` describes its seven operations. No rejected production-host containment implementation or evidence is included. Provisioning remains blocked without a matching Linux release-validation manifest. No manifest is supplied here.

## Remote execution boundary

`worker/client.py` implements the seven authenticated Worker operations over a loopback tunnel endpoint. Its private durable journal binds immutable requests and records start intent before transmission. Lost responses require inspection; unknown state never implies completion or an automatic second launch. `worker/adapter.py` translates Orchestrator steps to per-task immutable Worker registrations. Codex and Claude profiles are fixed, and the worker-specific authentication file is distinct from coordinator/model credentials.

Each successful task produces a local commit checkpoint. The next task binds the approved repository, original base commit, predecessor run ID and derived predecessor commit. The previous completed worktree is mounted read-only inside the next sandbox, then independently cloned and checked out at that exact commit. Parent completion and empty containment are prerequisites. No production mount, push, merge or deployment occurs. Older result rereads cannot rewind the current scope checkpoint.

Interrupt cancels the active task; Stop records a terminal scope and stops its tasks; Pause stops scheduling at a checkpoint boundary. Synthetic tests use the shipped HTTP handler, policy, durable store and controller; privileged execution plumbing is temporary synthetic test infrastructure. Actual runtime containment and egress acceptance remain required on the dedicated worker before writers are enabled.

No retrieval subsystem, databases, documents or historical production state is included.

## Bridge compatibility

Existing Bridge Python modules, shell scripts, CLI/API and configuration match the public Git blobs byte-for-byte. The public v1.0.0 tag remains unchanged.
