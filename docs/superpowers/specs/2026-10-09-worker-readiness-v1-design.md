# Worker Readiness V1 specification

Status: owner approved in principle, subject to the auth-pairing and network
clarifications below passing public-safety, metadata, and CI validation. Once
those gates pass this specification is APPROVED_READY_FOR_IMPLEMENTATION_PLAN.
The implementation plan still requires owner approval before implementation.
Nothing described below is implemented by this document.

## 1. Purpose, baseline, and scope

Make an approved, dedicated Linux host straightforward to assess, prepare, and
validate as an isolated AgentBridge Writer Worker. Ubuntu Server 24.04 LTS on
x86-64 is the primary supported installation target. Diagnostics must run on
other platforms and explain unsupported capabilities without modifying them.

Development branch: `feature/worker-readiness-v1`.
Checkpoint: `c669e221a29d32467a4b3becbcc45890014ad3bd`.
Validated product baseline: `214919b430ed17413de195907aba62c803a87f1f`.
The baseline's 422 passing tests are historical evidence; release claims for
new code require fresh results.

The production Orchestrator remains authoritative for state, transcript,
checkpoint, idempotency, owner controls, and approval gates. The Worker owns
isolated execution and result collection. RAG is not a dependency or component.
This work creates no infrastructure, installs nothing on the development host,
and changes no production service. Paid-cloud provisioning, host architecture
experiments, live model execution, merges, and releases are outside scope.

## 2. Architecture and reuse

```text
Operator -> agentbridge_worker CLI
            |-- doctor: read-only observations and deterministic assessment
            |-- bootstrap check / prepare / enable: separate privilege stages
            |-- self-test: RemoteWorkerClient -> actual installed Worker HTTP API
            |-- containment check / test: static inspection or synthetic proof
            |-- agents check: optional, bounded authentication-status probes
            `-- connection template: references, never secret values

Orchestrator -> owner-managed encrypted tunnel -> loopback Worker API
                                                   -> contained non-root run
```

Add `agentbridge_worker/` as a dependency-light Python package, with focused
modules for observation, assessment, configuration, preparation, activation,
self-test, containment evidence, and agent status. Use the existing
`worker/client.py` and `worker/protocol.py` contract; do not create a second
incompatible Worker API. Add schemas, generic templates, documentation, and
`tests/worker_readiness/`. Keep existing Bridge paths and defaults stable.

The present Worker implementation resides under `provisioning/digitalocean/`.
Its controller, runner, proxy, and synthetic workloads provide the behavior to
reuse, but its guest bootstrap combines installation and activation and fixes
the agent account. Portable onboarding must not invoke that bootstrap or any
provider script. Introduce a host-neutral runtime boundary under `worker/`
through a tested extraction/configuration change during implementation, retaining
existing entry points or compatibility wrappers. Preserve the validated
DigitalOcean package and hashes; do not silently change its release fingerprint.
Any necessary changes to that packaged line require an explicit versioned plan.

The configured non-root worker identity replaces the runtime's fixed agent
account. The existing protocol's derived `/srv/agentbridge/runs/<run_id>/worktree`
identity remains stable in V1, so clients need no protocol migration. This is a
generic product path, not a production-host path. Installation roots stay a
fixed, documented AgentBridge allowlist; arbitrary root destinations are not
accepted. Tool binaries remain independently pinned and verified. An unsupported
architecture cannot be enabled using x86-64 binaries.

## 3. CLI contract

The clone-and-diagnose command needs only Python 3.11+ and the standard library;
no installation of this package is necessary. Use `-B` so diagnostic imports do
not create bytecode in the checkout.

```sh
python3 -B -m agentbridge_worker doctor
python3 -B -m agentbridge_worker doctor --format json --config worker-owner.json
python3 -B -m agentbridge_worker bootstrap check --config worker-owner.json
python3 -B -m agentbridge_worker bootstrap prepare --config worker-owner.json --output worker-bundle
sudo python3 -B -m agentbridge_worker bootstrap enable --bundle worker-bundle --approve-service-activation
python3 -B -m agentbridge_worker self-test --config connection.json --approve-synthetic-execution
python3 -B -m agentbridge_worker containment check --config connection.json
sudo python3 -B -m agentbridge_worker containment test --config connection.json --approve-synthetic-execution
python3 -B -m agentbridge_worker agents check --config worker-owner.json --probe-auth
python3 -B -m agentbridge_worker connection template --format json
```

Human-readable output is the default. `--format json` emits one JSON document
on stdout and no banners. Bounded, sanitized diagnostics go to stderr. Commands
never accept a caller-supplied shell string, executable, PID, systemctl action,
or secret value. No operation enables writers implicitly.

Exit codes: 0 = PASS, 2 = DEGRADED, 3 = BLOCKED. Invalid configuration is BLOCKED
with a fixed error code, not an uncaught exception. Optional missing agents can
make the overall report DEGRADED while core host prerequisites still pass.

## 4. Doctor: observations and readiness policy

`doctor` and `bootstrap check` never use sudo, install packages, create files,
alter services, write cgroups, read model credentials, or start a Worker task.
They read the selected configuration, approved OS/kernel metadata, and file
metadata; perform bounded, fixed version queries; and make minimal
DNS/TLS connectivity checks. They do not execute agent login/status commands.
No host inventory, environment dump, IP address, username, or home path appears
in the default report. Actual configured paths may be shown only as local
operator next steps, never copied into public evidence automatically.

| Observation | V1 interpretation |
| --- | --- |
| OS/distro | Ubuntu 24.04 passes the supported-target check; other maintained Linux targets are DEGRADED/unsupported for enable; non-Linux is BLOCKED. |
| Architecture | x86-64 supported; other architectures reported explicitly and blocked for bootstrap until a verified runtime/toolchain exists. |
| Python | 3.11+ for diagnostics; Ubuntu target uses its 3.12 runtime. Older versions produce a minimal compatibility error. |
| Git | Fixed `git --version`, no repository/config inspection or Git hooks. Missing executable is BLOCKED. |
| systemd | Installed systemctl/systemd-analyze, manager reachable, and version at least 249 required. Executable presence alone is insufficient. |
| cgroups | Unified v2 mount and no mounted v1 controller hierarchy; `cpu`, `memory`, `pids` required. Read subtree/controller availability, never activate them. |
| Memory | Read total, available, and observable ancestor memory limits. Require at least 7 GiB usable total (8 GiB nominal machines) and 2 GiB available; unknown restrictive ancestor limits are BLOCKED. |
| CPU | At least four available logical CPUs; observe affinity/effective cpuset and quota where readable. Four host CPUs alone are not proof of usable capacity. |
| Disk | At least 80 GiB free on the filesystem containing the planned data root; inspect its nearest existing ancestor before preparation. |
| Permissions | Use metadata and access checks on selected staging parent and installed roots. Never create a test file. Symlink/reparse escape, unsafe owner, or ambiguous access is BLOCKED. |
| Worker port | Loopback port 8765 default. Read socket tables and probe loopback only as needed; never reserve/bind a port during doctor. Fresh-host assessment treats a listener as conflict. With explicit installed-mode connection configuration, permit only an authenticated, schema-valid response from the installed Worker; unavailable authentication or ambiguous listener identity is BLOCKED. Port availability is a snapshot, not a reservation. |
| Network | Read operational interface/route capabilities without printing addresses. Optional bounded DNS/TLS observations report direct or explicitly configured approved-path reachability separately. Missing direct Internet access is not a host-readiness blocker. Offline observations are unverified, not failed runtime enforcement. |
| Agents | Presence only in doctor. Missing optional CLI is DEGRADED, not a synthetic-host blocker. Authentication is UNKNOWN unless separately probed. |
| Identity | Doctor run as root is BLOCKED: rerun as the owner. Privilege is confined to explicit enable/runtime-admin commands. |

Installed-mode API identity validation may read the configured worker-specific
authentication file using RemoteWorkerClient's owner/mode/no-symlink checks.
It issues only inspect for an existing operator-selected registration; it never
creates a task or a persistent client journal. If no registration is available,
the port remains unverified until synthetic self-test. The identity check must
also match the dedicated systemd service executable/bind configuration in local
read-only inspection; authentication alone cannot identify the local listener.
Raw token bytes and task/transcript content are discarded. No model auth file is
read. Add an in-memory/read-only inspection transport rather than instantiating
the current journal-creating client during doctor.

Network capability inspection distinguishes the host network stack from traffic
policy. `HOST_NETWORK_CAPABLE` is YES when read-only observations find operational
loopback plus an up non-loopback interface and a configured route toward the
owner's intended egress/tunnel path; NO when a required local capability is
demonstrably absent; UNKNOWN when observations or intended-path configuration
are insufficient. This is a capability observation, not an external reachability
or runtime-containment claim. Its evidence never includes private addresses.

Optional direct connectivity observations send no credentials, honor no ambient
proxy environment values, reject private/link-local/loopback results for external
authorities, validate TLS, and follow no redirects. If an approved proxy is
explicitly configured, label that path separately; do not bypass it to obtain
a passing result. A failed direct observation reports `direct_path_unavailable`
without implying the approved runtime path will fail. Each observation times out
within five seconds; total doctor deadline is thirty seconds. `--offline`
skips outbound DNS/TLS and API probes, still reads hardware/OS/systemd/cgroup and
local network capability, and reports skipped connectivity as unverified.
Connectivity is not an egress-enforcement proof.

`HOST_READY=YES` means mandatory hardware, supported OS/architecture, local
runtime/systemd/cgroup, identity, storage, filesystem, and port prerequisites
pass, and preparation may proceed. It does not require unrestricted or direct
Internet access. `--offline` may therefore return HOST_READY=YES with DEGRADED
overall status because outbound observations are unverified. Optional agents
may remain absent. HOST_NETWORK_CAPABLE and deployment-stage connectivity are
reported separately; absent network capability requires remediation before a
stage that needs it, without concealing passing hardware/local-host checks.
HOST_READY does not mean services are installed or writers can run.
`WRITER_READY` is always NO during doctor and becomes YES only through verified
pairing, runtime containment, runtime network enforcement/reachability, agent,
source-policy, and owner-approval gates. Existing enablement is never inferred
from a prior doctor report; stage-specific prerequisites are rechecked at
activation time.

## 5. Schemas and result model

Version all documents with `schema_version: 1`. JSON schemas reject unknown
fields, wrong types, duplicate JSON keys, non-finite numbers, and oversized input.
Limit configuration/report inputs to 256 KiB. Paths are canonicalized with
component-level symlink checks; opaque error codes replace untrusted exception
messages. Report timestamps are UTC. Deterministic assessment excludes timestamps
from its digest.

### Doctor report

Required fields: `schema_version`, `kind: "doctor"`, `status`, `host_ready`,
`writer_ready: false`, `host_network_capable` (YES/NO/UNKNOWN),
`runtime_network_enforcement_proven: false`, `checks`, `missing_requirements`,
`agents`, `next_steps`, `generated_at`. Each check has `id`, `required`, `status`, `code`, a bounded
allowlisted `observed` object, and a plain remediation string. Status is
PASS/DEGRADED/BLOCKED. An unobservable required capability is BLOCKED, never PASS.
`host_ready` is a JSON boolean; human output renders HOST_READY=YES/NO.
Network observation checks identify `path: direct|approved_proxy|local_capability`
and `stage: host|enable|runtime`; runtime results cannot be inferred from host
observations. Skipped outbound checks are optional for the HOST_READY decision.

### Owner bootstrap configuration

Fields: `schema_version`, `worker_user`, `api` (fixed loopback host and validated
port), `limits` (memory, swap, tasks, CPU percentage, timeout), and
`approved_sources` (repository identity, approved HTTPS URL, approved commit),
and optional `network` (approved egress-policy file reference and approved local
proxy URL). Proxy URLs are limited to the installed loopback proxy with no URL
credentials; arbitrary endpoint overrides are not accepted. No proxy auth or
network-secret value is stored in these schemas.
`worker_user` must be an explicit validated local account name, not a shipped
username. Default sources are synthetic-only; `writers_enabled` must be false
and `active_run_limit` must be 1. Bounds follow existing Worker policy.
Nonzero swap is not enabled in V1: MemorySwapMax remains 0. No token, credential
value, private key, arbitrary command, or caller-supplied execution path is valid.

### Connection configuration

Fields: `schema_version`, `worker_url`, `worker_auth_secret_file`,
`journal_file`, `tunnel` (SSH host alias, local port, remote loopback port,
known-hosts-file reference, identity-file reference), and optional
`inspection_run_id` for read-only installed-mode doctor. Pairing validation
additionally uses private state binding `pairing_id`, `secret_generation_id`,
`installation_digest`, and `pairing_state`; this state is separate from the
secret file and cannot itself authorize an API request. References are private
local owner configuration; templates contain labelled placeholders only.
`worker_url` must be `http://127.0.0.1:<port>` behind the owner-managed encrypted
tunnel, matching RemoteWorkerClient's current restriction. Public/LAN direct
URLs, URL credentials, queries/fragments, embedded private key material, and
token fields are rejected. The CLI validates but does not open a tunnel by
default. SSH host verification is mandatory, with forwarding restricted to the
Worker loopback endpoint and no shell/agent forwarding.

### Preparation manifest and evidence

The bundle manifest binds configuration digest, source commit/tree identity,
sorted file SHA-256 hashes, intended ownership/modes, fixed installation targets,
and `writers_enabled: false`. It contains no secrets. Runtime reports additionally
record a test session ID, created run IDs, checks, primary error code, cleanup
errors, and proof classification. Detailed local evidence uses owner-only files
and is not automatically committed or uploaded.

Worker task state retains the existing protocol states: CREATED, STARTING,
RUNNING, UNKNOWN, STOPPED, COMPLETED, FAILED, CLEANED. Readiness status and proof
classification are separate enums and must not be substituted for task states.

## 6. Bootstrap privilege lifecycle

**CHECK:** same read-only rules as doctor. Print exact required Ubuntu package
names and the proposed install/service plan. Do not execute apt or sudo. Report
missing bubblewrap, nftables, Python runtime, systemd, Git, or approved toolchain
as actionable requirements. Show the required administrator package command as
text only; no full upgrade or unrelated dependencies.

**PREPARE:** unprivileged filesystem/config generation only in a new explicit
owner-selected staging directory. Mode 0700 for the directory and private config,
0644 for public unit templates, 0755 for reviewed entry scripts. Refuse symlinks,
non-owner paths, collisions, or unexpected existing content. Repeating prepare
with identical digest is idempotent; changed configuration requires a new bundle.
Prepare validates local source/material and hashes but downloads nothing,
generates no credentials, creates no system account, and never writes `/etc`,
`/opt`, `/srv`, or systemd runtime paths.

**ENABLE:** future dedicated host only, root plus explicit
`--approve-service-activation` required. Revalidate configuration, supported OS,
controllers, package prerequisites, source/material hashes, account safety,
port, and exact destination allowlist before changing anything. Create/use the
configured non-root, no-login, no-sudo agent identity; refuse UID 0 and unsafe
existing identities. Controller state/auth remain root-only. Installation owns
only AgentBridge paths and uses explicit owner/modes. Unexpected existing files
fail closed; idempotent matching installations may be reused. Generate a new
worker-specific token locally with exclusive creation and mode 0600; print only
its file reference. Follow the pairing lifecycle in section 6.1; never reuse
production/provider/Bridge/model/SSH credentials.

Enable installs the root-owned narrow controller and dedicated egress services,
reloads systemd, and starts only AgentBridge services. No API operation exposes
root shell, arbitrary files, PIDs, or systemctl. Agents execute exclusively as
the configured non-root identity. Writers remain disabled. Preserve the existing
per-run systemd controls and filesystem sandbox; no unsandboxed fallback.
ENABLE requires only stage-specific connectivity: local systemd/loopback and
all prerequisite packages/verified runtime material already present. It does
not fetch source, install packages, or demand direct model/Git/package access.
An entirely offline installation can start a disabled synthetic baseline from
a complete verified local bundle. Explicit pairing later requires the approved
encrypted owner channel; runtime tasks later require their approved egress path.
CHECK lists the missing requirements for each stage separately.
Missing verified toolchain material or unproven sandbox launch is a blocking
prerequisite, not permission to fetch random binaries or weaken policy.

Record an installation ledger for exact new files/accounts/services. Activation
failure records the primary stage and cleanup result separately and leaves
writers disabled. A reviewed uninstall plan stops only AgentBridge units and
removes only ledger-owned matching files; default rollback preserves artifacts
and operator credentials, and never deletes unrelated accounts/data.

### 6.1. Owner-mediated Worker auth pairing lifecycle

Pairing is a local owner/admin lifecycle, not a new public service or Worker API
operation. The seven existing operations and bearer contract remain unchanged.
No ChatGPT tool or Agent is permitted to export, receive, rotate, or retrieve the
Worker secret. Root-owned local pairing helpers have fixed source paths and
validated pairing IDs; the transfer uses a separately authenticated owner/admin
SSH channel, not the restricted API-forwarding identity.

**Secret generation and service copy:** use Python `secrets.token_hex(32)` backed
by the OS cryptographic random source, giving 256 random bits encoded as 64 ASCII
hex characters plus newline. Generate distinct random pairing and generation IDs
that do not derive from the secret. Store the service copy exclusively at
`/etc/agentbridge-worker/api-token`, root:root, 0600, in a root-owned 0700
configuration directory. Initial creation uses an opened validated directory
descriptor, O_CREAT|O_EXCL|O_NOFOLLOW and 0600, validates regular-file ownership,
writes/fsyncs the complete secret, and fsyncs the directory. The controller starts
only after a complete creation and root-only metadata record. On interruption,
never accept a partial secret; keep service/writers disabled and recover the
recorded generation under owner approval. Repeated ENABLE does not overwrite
a valid existing secret or declare an existing pairing complete.

**Pairing state:** persist root-only Worker state and owner-only control-side
state, each 0600 with atomic temp-file/fsync/rename updates in protected
directories. States are UNPAIRED -> EXPORT_READY -> TRANSFER_PENDING ->
LOCAL_INSTALLED -> VERIFIED -> PAIRED, with TRANSFER_UNKNOWN, EXPIRED, ROTATING,
and REVOKED failure/lifecycle states. Bind pairing ID, secret generation ID,
installation digest, creation/expiry times, intended owner UID, and confirmation
state. State records contain no token or token-derived diagnostic output.
Any non-PAIRED, contradictory, or ambiguous state sets WRITER_READY=NO.

**One-time export:** only an explicit owner pairing request may produce an
artifact. Require an existing non-root owner/admin account distinct from the
agent and proxy accounts. Store a single active artifact for the generation in
`/var/lib/agentbridge-worker-pairing/<pairing_id>/`: a 0600 `worker-auth` file
containing the service secret and a 0600 metadata file without secret values.
The separate fixed pairing parent is root-owned 0711; its random-ID child is owned by the
selected owner UID, mode 0700. Artifact metadata and authoritative state remain
separate; the authoritative ledger remains beneath the existing root-only 0700
controller-state directory. All ancestors must permit the intended traversal;
no controller-state permissions are widened for export. The artifact must never be
under a repository/worktree, shared folder, or Agent-readable location. Create
files exclusively through validated directory descriptors, reject symlinks and
unsafe existing paths, and fsync before EXPORT_READY. Maximum lifetime is fifteen
minutes and secret-file size is exactly 65 bytes. Print only pairing ID, state,
expiry, and file references. Never print the secret or serialize it in stdout,
stderr, logs, transcripts, command arguments, environment variables, Git, or CI.

**Transfer and control-side installation:** the owner pins/verifies the Worker
SSH host key and uses encrypted SSH/SFTP with normal certificate/host verification
and an owner-admin identity. Transfer file bytes through SFTP protocol directly
into an exclusively created 0600 temporary file in a validated owner-owned 0700
control directory. Never use `cat`, terminal output, clipboard, shell substitution,
debug payload logging, or a command-line token. SSH credentials are independent
of the Worker token; no private SSH key is transferred. Both artifact paths and
remote identity come from the private owner configuration, never a public IP or
shipped username. V1 requires enforceable POSIX ownership/modes at both ends;
an unverified permission model is BLOCKED, not silently accepted.

Validate the complete received length/encoding, metadata generation/installation
binding, freshness, owner, and mode before fsync and atomic installation as
`worker_auth_secret_file`. Initial installation must not overwrite an unexpected
existing destination. Rotation may replace only the previously ledger-bound
secret file. Keep token bytes inside the transfer/installation process; progress
and verification output contain fixed status codes only. Do not attach the file
to task inputs, transcript storage, artifacts, or model credential directories.

**Verify both ends and retire:** verify the Worker still serves the recorded
generation and installation over the pinned owner channel, then use the installed
control-side file through the encrypted loopback tunnel for authenticated
create/inspect of a fixed synthetic, non-executed pairing registration. Never
start an Agent for pairing. Validate the real API response/schema and generation
record, then stop/cleanup only that registration and record VERIFIED. Delete
the export secret/metadata and temporary transfer files, verify artifact absence,
and retire the pairing ID. Only after those checks and an acknowledged
owner-channel confirmation may both sides record PAIRED. Lost final confirmation
leaves control-side state TRANSFER_UNKNOWN and global WRITER_READY=NO, even if
the Worker recorded PAIRED; retry reconciles the same generation and must not
recreate a retired export. Retain only redacted
ledger metadata. File unlink is not a claim of physical SSD erasure; minimize
copies and rely on permissions plus revocation for failed generations.

**Interrupted or failed pairing:** record TRANSFER_UNKNOWN rather than success
on any lost transfer/verification/confirmation acknowledgement. A partial local
file never becomes the active secret reference. Before expiry, an explicit retry
reconciles the same pairing ID and generation and can resume/retransfer the same
artifact or verify a fully installed file; it must not invent a second successful
pairing or start any task. No secret comparison is printed. If either ledger,
remote generation, expiry, cleanup, or authenticated probe cannot be reconciled,
remain unpaired and WRITER_READY=NO. Expiry/cancellation removes only ledger-owned
artifacts/temp files and invalidates the generation before issuing a new export.
An expired artifact must not leave a copied bearer indefinitely valid: disable
the controller until the old token is revoked/replaced and a new generation is
loaded. Service-start/precheck, every API authentication decision, and subsequent
pairing operations enforce the generation's pending-pairing expiry. After the
deadline the controller rejects all requests using that unpaired generation,
including synthetic requests; wall-clock rollback cannot extend its lifetime
within a boot, and reboot requires freshness revalidation before loading it.
The controller's bounded maintenance cycle removes expired ledger-owned artifacts
within sixty seconds; failure to remove them remains a cleanup error and prevents
a new export. Failure to complete pairing never opens the writer gate. Normal
task APIs do not perform pairing or distribute secrets.

**Rotation:** explicit owner/admin operation, with writers disabled and no active
Worker run; otherwise refuse until the owner separately stops/drains it. Stop
only the dedicated controller, record ROTATING, generate a fresh secret through
exclusive 0600 staging, fsync and atomically replace the fixed service file, and
restart only that Worker controller with the new generation. There is no dual
token overlap or fallback to the old secret. Re-pair through a fresh one-time
artifact and atomically replace the ledger-bound control-side file. Interrupted
rotation leaves WRITER_READY=NO and old-token use rejected once the new generation
is loaded; if loading fails, the controller stays stopped. Successful pairing
does not automatically re-enable writers or production integration.

**Revocation and cleanup:** an explicit revoke request disables writers, stops
only the dedicated controller, marks REVOKED, retires the service secret and
ledger-bound export artifacts, and removes the control-side copy through the
owner channel. Refuse to claim control-side cleanup success when disconnected.
Revocation cannot be marked complete while registered executions remain active:
require owner-approved stop/drain of exact Worker runs and report unresolved
cleanup separately. Deleted/stale tokens must not authenticate after controller
restart; a new generation and complete pairing are required. No unrelated key,
account, credential, service, or filesystem is touched. Pairing is necessary
but insufficient for WRITER_READY: all other approved acceptance gates remain.

## 7. Synthetic Worker API self-test

`self-test` requires explicit synthetic-execution approval, an installed
synthetic-only Worker, and owner-only connection/auth configuration. Use actual
HTTP serialization/authentication, policy, durable store, and controller through
RemoteWorkerClient. Reading the authentication file is confined to request
construction; it is neither reported nor journaled. No model or repository login
is required. Test jobs reference the synthetic source and fixed profiles only.

Cover create/start/inspect/collect; duplicate create/start without duplicate
execution; cancel; stop plus later-start rejection; cleanup; rejected missing or
invalid authentication; malformed body; stale digest/spec; ordered events; and
reconciliation through a fresh client using the same durable journal. Collect
terminal results without restarting completed tasks. Intentional network-loss
fault injection belongs to deterministic contract tests, not arbitrary installed
service disruption. Self-test reports reconnect reconciliation, while CI covers
transport-loss behavior against the actual handler with controlled plumbing.

Use random run IDs and a private session ledger. Refuse to run when unrelated
active tasks exist; never cancel them. Overall deadline is five minutes with
per-request bounds. On exit, inspect, stop if needed, and clean only recorded
test-created runs. A registered run is recorded before start; reconcile an
ambiguous create/start response before retrying. Lost connectivity returns
UNKNOWN/DISCONNECTED and preserves the recovery ledger, never DONE or cleanup
success. Resume cleanup of that same ledger on an explicit retry.

API self-test establishes protocol/lifecycle behavior, not full resource or
network enforcement. Agent-controlled result data remains untrusted.

## 8. Containment acceptance and evidence

`containment check` is read-only. Match supported unit properties, cgroup v2
controllers, and planned limits to classify each requirement STATIC_SUPPORTED
or UNSUPPORTED. This classification does not claim resource enforcement.

`containment test` is an approved privileged synthetic test on the dedicated
installed Worker. Reuse fixed workloads and the real API; the privileged verifier
reads exact test-run systemd/cgroup state independently of workload claims.
No arbitrary PID or cgroup attachment is accepted. All workloads run non-root.

| Requirement | Evidence needed for RUNTIME_PROVEN |
| --- | --- |
| Unified v2 | Actual mounts and cpu/memory/pids controllers; no legacy controller hierarchy. |
| Parent/child/grandchild | Nonzero UIDs and identical exact per-run cgroup membership, independently correlated with systemd. |
| MemoryMax | Aggregate allocation request exceeds limit, enforcement counter increases, observed peak is bounded, descendants remain contained. Normal exits after observed allocation failure are acceptable; configuration alone is insufficient. |
| MemorySwapMax | Effective memory.swap.max is 0 and actual memory-pressure evidence includes observed swap usage remaining zero. |
| TasksMax | Actual fork/thread allocation failure and pids.events increase, with bounded pids.current. |
| CPUQuota | Match cpu.max to requested quota and demonstrate throttling plus bounded measured CPU/wall ratio using the existing workload criterion. |
| RuntimeMaxSec | Task exceeds approved runtime, systemd reports timeout, and descendants are independently proven absent. |
| KillMode=control-group | Cancel and Stop terminate parent/child/grandchild and empty the exact cgroup, with a bounded grace then kill. |
| Run isolation | Different run cgroups/workspaces; B cannot start while A is active; B runs independently after A cleanup without state leakage. |
| Cleanup | Only session-owned units/workspaces disappear, exact target cgroups are empty, and unrelated test sentinel state is unchanged. |

Also inspect effective ancestor limits so a tighter parent boundary cannot be
mistaken for the requested per-run control. Test-memory bounds must fit observed
headroom; resource pressure stays within the run. No synthetic workload targets
host memory exhaustion or production data. Deadline is ten minutes overall.
Missing measurements, incompatible kernels, incomplete cleanup, or any failed
proof preclude RUNTIME_PROVEN. Keep classification and outcome separate: each
check has evidence level and PASS/FAIL/UNKNOWN outcome. Failed runtime attempts
can retain STATIC_SUPPORTED with outcome FAIL; unsupported capabilities remain
UNSUPPORTED. Never turn failure into static PASS.

Local evidence binds installation/configuration/toolchain digest, current boot
identity, unit invocation identity, session/run IDs, controls/counter deltas, and
cleanup findings. Reboot or changed runtime material invalidates prior runtime
readiness. External/agent-supplied JSON cannot promote readiness without the
privileged verifier's independently collected observations. Public CI publishes
synthetic fixture results, not private-host identities.

## 9. Agent readiness and networking

Agent status values: NOT_INSTALLED, INSTALLED_NOT_AUTHENTICATED, READY, UNKNOWN.
Missing executable is NOT_INSTALLED. Executable presence plus unverified auth
is UNKNOWN, never INSTALLED_NOT_AUTHENTICATED or READY by inference.
`agents check --probe-auth` invokes only known read-only status operations of a
supported CLI version, under the intended identity, with stdin closed, bounded
output, and a five-second timeout. Unknown CLI versions or ambiguous output
produce UNKNOWN; do not try alternative login, model, or update commands.

Codex login status and Claude authentication status need version-specific parser
tests. Allowlist only the logged-in boolean/status from stdout; discard email,
organization, paths, raw stderr, and all other output. Never inspect token files,
print environment values, invoke helpers supplied by arbitrary configuration,
or silently change authentication. If a CLI cannot provide a trustworthy
side-effect-free status under the configured execution credential path, report
UNKNOWN. Owner-local CLI auth must not imply contained-worker auth readiness.
READY means the supported CLI reports authentication for the intended identity
and configured credential flow; it is not proof of model entitlement or a live
Agent task. Those remain later real-host acceptance gates.

The API remains loopback-only, reached through a verified encrypted tunnel.
An owner-specific worker token is separate from all model/control-plane/SSH keys
and follows section 6.1 pairing. Retain dedicated proxy/egress policy: approved
Git/package/model authorities, blocking direct private-network and metadata
access by agent identity. Never add production mounts or credential copies to
satisfy connectivity/authentication.

### 9.1. Host capability versus runtime network acceptance

HOST_NETWORK_CAPABLE is the section 4 host observation. It never implies
RUNTIME_NETWORK_ENFORCEMENT_PROVEN. Direct Internet access is optional for doctor
and not a universal ENABLE prerequisite. CHECK/PREPARE need no outbound traffic;
ENABLE with complete local prerequisites needs only local loopback/systemd.
Pairing needs its separately verified encrypted owner channel. No phase silently
disables a proxy or firewall, broadens an allowlist, or adds direct access.

After installation, approved synthetic network acceptance must run under the
actual non-root agent identity and per-run sandbox using the configured proxy
and enforced egress rules. Validate required approved Git, package, and model
destinations through that path, with bounded protocol-level probes and TLS
verification. No model request, account credential, or paid inference is needed
for this transport check. A package/Git/model destination that only works through
an unauthorized direct route fails the runtime gate, even if doctor saw it.
If a destination requires authenticated protocol readiness beyond safe probes,
record that extra check as UNKNOWN until the later approved agent/source test.

Require independent privileged observation of the installed policy and matching
deny/allow counters plus attempted workload connections. Demonstrate denial of
direct egress bypass, unauthorized external destinations, metadata/link-local,
private/LAN targets, and production-service destination classes, for applicable
IPv4/IPv6 paths. Use controlled synthetic targets where needed; a timeout to an
unreachable address alone is not enforcement evidence. Do not access production
services to obtain proof. Demonstrate approved-proxy success from the same
workload boundary; host-root/owner connectivity is insufficient. The proxy must
retain exact authority allowlisting, DNS/private-address rejection, and no
unapproved relay path.

Report `runtime_network_enforcement_proven` and `runtime_reachability_proven`
separately, with evidence classification and PASS/FAIL/UNKNOWN outcome per check.
Only complete independent enforcement evidence sets
RUNTIME_NETWORK_ENFORCEMENT_PROVEN=YES. WRITER_READY remains NO until that result,
required destination reachability through the approved path, and all existing
pairing/containment/agent/source/owner gates pass. Offline or preinstallation
doctor results cannot promote either runtime network field. CI uses deterministic
fixtures/contract tests; actual host proof remains pending.

## 10. Failure semantics and security invariants

- Every unknown required observation blocks activation; unavailable Workers do
  not become completed tasks. Durable start intent forbids blind launch retries.
- No broad sudoers/polkit rule, arbitrary root shell, or unrestricted systemctl.
- Prepared bundles, registration specs, and connection configs are untrusted
  inputs until schema, bounds, owner/mode, path, and digest checks pass.
- No secret output, traceback/environment dump, credential-content scan, or raw
  subprocess error echo. Reports use fixed stage/code plus approved remediation.
- Timeout, partial install, primary probe failure, and rollback/cleanup failure
  remain separate fields. Cleanup failure cannot mask the primary failure.
- A file-level scan and a Git metadata gate protect all future public commits.
  Neither substitutes for semantic review of examples, fixtures, and artifacts.
- Bridge remains read-only by default; one active writer at most, disabled until
  later explicit owner enablement. This onboarding work does not grant that gate.
- Existing public history, main, and v1.0.0 remain unchanged.

## 11. Public Git metadata gate

Immediate repository-local identity is the verified account's GitHub noreply
name/email. Do not change global identity or rewrite published commits.
The existing checkpoint contains an internal-domain author/committer identity;
do not reproduce its value in this public specification. Sanitizing it would
change the commit SHA and require rewriting both published branch tips and
coordinating downstream references. Preserve history unless the owner separately
determines material disclosure requires a reviewed rewrite.

Implement a metadata check alongside `scripts/public_safety_check.py` in the
later approved development phase. Check both effective next-commit identities
(`git var`, including environment overrides) and author/committer fields for
every commit in the explicit range `c669e221a29d32467a4b3becbcc45890014ad3bd..HEAD`.
Use an exact approved public identity allowlist, initially
`phannarinboonsang-afk` with
`335046212+phannarinboonsang-afk@users.noreply.github.com`.
Reject unapproved identities, nonpublic/internal domain suffixes (including
`.local`, `.internal`, `.lan`, `.corp`), malformed addresses, and local/domain
username or machine-name forms. Additional safe identities require reviewed
policy changes. Error output reports commit SHA, field, and violation code, not
the rejected identity. Legacy findings are reported separately; they do not
waive validation of new commits or require an automatic force push. Check before
commit, inspect the resulting commit before push, and run range checking in CI
with sufficient history. GitHub merge metadata is outside scope because no merge
is authorized.

## 12. Test matrix and CI

| Area | Required deterministic cases |
| --- | --- |
| Doctor | Ready Ubuntu host; insufficient total/available memory; CPU/disk deficit; missing Git/systemd; manager unavailable; v1/hybrid; missing controller; parent limits; root invocation; port conflict; permission/symlink denial; offline/DNS/TLS/timeout; unsupported architecture/OS. |
| Host/runtime network distinction | Offline and unavailable direct Internet can preserve HOST_READY=YES; missing approved runtime path leaves WRITER_READY=NO; proxy-only host is valid; complete local ENABLE needs no Internet; stage-specific connectivity failures remain separate; host-root DNS/TLS cannot promote runtime proof; deny timeout without enforcement evidence is UNKNOWN; bypass/private/metadata denial and approved-path success require actual workload-boundary evidence. |
| Schemas/redaction | Unknown/duplicate keys, invalid types/limits/URLs, secret fields, oversized input; hostile subprocess output cannot disclose a planted secret or private path. |
| Agents | Each missing CLI; explicit unauthenticated/authenticated statuses; unsupported version, malformed output, timeout, credential-scope mismatch => UNKNOWN; no auth-file inspection or model calls. |
| Bootstrap | CHECK has zero writes; PREPARE touches only new staging; dry-run/manifest determinism; repeat identical bundle; collision/symlink/hash mismatch; root/approval requirements; unexpected existing identity/file; partial failure and scoped rollback. |
| Auth pairing | Exclusive generation and owner/mode enforcement; unsafe export account/path denied; partial transfer never installed; lost acknowledgement and retry reconcile same generation; expiry/wall-clock rollback/reboot cannot preserve stale token validity; failed API verification/cleanup leaves WRITER_READY=NO; successful encrypted pairing retires artifact; rotation rejects old token with no overlap; interrupted rotation fails closed; revocation with unreachable control copy reports incomplete cleanup; no secret appears in output/logs/arguments/Git/fixture reports. |
| Self-test | Actual handler/policy/store/client HTTP contract; lifecycle operations; duplicate start; stale spec; auth/malformed rejection; disconnect/reconnect; completed-result retrieval; session-only cleanup; unresolved cleanup remains visible. |
| Containment | Static-only never runtime; independently verified successful fixture; forged/missing/stale evidence rejected; normal exit with memory enforcement accepted; absent enforcement rejected; stop/timeout descendant leak; run isolation and cleanup; primary failure survives cleanup failure. |
| Metadata | Safe author/committer; unsafe author or committer; environment override; internal-domain/local-machine identity; malformed email; range excludes only fixed checkpoint ancestry; new bad commit is rejected with redacted output. |
| Compatibility | Existing Bridge, Orchestrator, Shared Chat, frontend/App, remote adapter, and Worker/provisioning tests remain green. |

Separate observation adapters from pure assessment to test unavailable host
conditions without modifying the CI host. Subprocess adapters use fixed argv,
timeouts, minimal environments, and sanitized parsers. Mutating unit/account
tests use temporary execution plumbing; no product test-only bypass in the
runtime. Synthetic HTTP integration uses the real handler, not a mocked client.

Use existing Ubuntu 24.04 GitHub Actions with no secrets or provider calls. Add
readiness tests, module import/CLI smoke tests, JSON schema checks, public-file
and metadata safety, and systemd-analyze verification of generated generic units.
Retain existing Bridge/Coordinator/Interfaces/static package jobs. CLI smoke
tests expect accurate runner readiness, not an artificial HOST_READY=YES.
CI does not activate Worker services or claim installed-host containment,
network enforcement, live-agent authentication, or autonomous E2E PASS.

## 13. Operator flow and rollout

1. Clone the canonical repository and check out an approved commit on
   `feature/worker-readiness-v1`.
2. Run `python3 -B -m agentbridge_worker doctor`. Receive HOST_READY=YES/NO,
   precise missing requirement codes, and the next safe command. If ready,
   show bootstrap CHECK and config-template steps, never an automatic ENABLE.
3. Supply private owner configuration, CHECK, and PREPARE the reviewed bundle.
4. On a future approved dedicated Linux host, separately approve privileged
   installation/service activation. Writers remain disabled after ENABLE.
5. Complete owner-mediated encrypted auth pairing and retire its one-time export.
   Run installed API self-test and runtime containment/network acceptance through
   the approved egress path, then configure and verify agent authentication and
   approved source policy. No direct Internet requirement substitutes for this.
6. Validate the encrypted connection with an isolated Orchestrator instance.
   Real-host and live autonomous acceptance remain separate future milestones.
7. Final production configuration/deployment/restart requires a new specific
   owner approval. No step here performs it.

After spec review, write an implementation plan and obtain its review. Only
then implement on the readiness branch, run fresh regressions/public gates,
push normally, and observe CI. Do not merge, release, deploy, create paid
resources, or resume host architecture experiments.

## 14. Reference and review notes

Systemd controls must be validated against the target Ubuntu version, not only
the latest documentation. Runtime measurements and controller counters remain
the acceptance criteria. Primary upstream references:

- [systemd resource-control source](https://github.com/systemd/systemd/blob/v255/man/systemd.resource-control.xml)
- [systemd service source](https://github.com/systemd/systemd/blob/v255/man/systemd.service.xml)
- [systemd kill source](https://github.com/systemd/systemd/blob/v255/man/systemd.kill.xml)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- Codex status parsing is grounded in the verified pinned CLI's local help and
  source during implementation; unrecognized output remains UNKNOWN.

Self-review: privilege stages are separate, all writer gates remain closed,
static and runtime evidence are distinct, every required negative path is in
the test matrix, and no public document contains private host identities or
credential values. The two owner-required pairing/network clarifications must
pass the documented public-safety/metadata checks and branch CI before the
specification is ready for a separately reviewed implementation plan.
