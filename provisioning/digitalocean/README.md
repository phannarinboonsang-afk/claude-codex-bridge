# AgentBridge DigitalOcean writer staging package

Version: 1.0.2 (staged). Candidate: 6fb185e6a6e44a3ea54aa6a95f56211d54ce390f.

**BLOCKED FOR CREATE.** This is a reviewable staging package, not a release certified
for provisioning. Create requires the exact `release-validation-manifest.json`
produced by a passing GitHub Actions Ubuntu 24.04 workflow. The manifest binds the
package's `SHA256SUMS` fingerprint and Git commit; it is checked before any token
prompt or provider call. `release-status.json` records the current staging state and
must not be edited to bypass validation. No DigitalOcean resources were created.
No production system is accessed by static validation.

## Fixed cloud specification

Name `agentbridge-writer-v1`; Basic Regular `s-4vcpu-8gb`; Singapore `sgp1`;
image slug `ubuntu-24-04-x64`; 4 vCPU / 8 GiB / 160 GiB SSD; IPv6 off; backups and
monitoring extras off. Tags `agentbridge`, `writer`, `v1`, plus a random
deployment-specific tag. Public IPv4 is required for this initial SSH/control
design. Create checks the public size catalog and refuses unavailable configuration
or a price above $48/month or $0.07143/hour before cloud mutation.

Provider pricing: https://www.digitalocean.com/pricing/droplets
Firewall: https://docs.digitalocean.com/products/networking/firewalls/getting-started/quickstart/
API scopes: https://docs.digitalocean.com/reference/api/scopes/droplet/create/

## Owner inputs and local authentication

Prepare DigitalOcean account/MFA/billing separately. Register an existing owner
SSH public key in DigitalOcean. This package accepts only its numeric ID or MD5
fingerprint; it never creates, reads, transfers or stores a private key.

Create a local file OUTSIDE this bundle, for example `owner.json`:

```json
{"ssh_key":"YOUR_REGISTERED_KEY_ID","admin_cidr":"YOUR_PUBLIC_IPV4/32"}
```

Only these non-secret values are requested. The owner source must be a single
public IPv4 address; production private addresses and broad CIDRs are denied.
The documentation-range address in tests is never accepted by create.

On a trusted owner Linux control machine, after a reviewed release is issued:

```text
python3 /absolute/path/agentbridge-digitalocean-writer-v1.0.2/provision.py plan --owner-config /absolute/path/owner.json
python3 /absolute/path/agentbridge-digitalocean-writer-v1.0.2/create-worker.py --owner-config /absolute/path/owner.json --state /absolute/path/worker-resources.json --approve-create
```

Create requires a POSIX local persistent filesystem for directory fsync. This
Windows staging workstation cannot currently execute the guarded create path.
The scripts are Python, so there are no shell interpolation or command separators.

Use a short-lived custom-scoped DigitalOcean token. Required create scope set:
`droplet:create`, `droplet:read`, `regions:read`, `sizes:read`, `actions:read`,
`image:read`, `snapshot:read`, `vpc:read`, `ssh_key:read`, `tag:create`, `tag:read`,
`firewall:create`, `firewall:read`, plus provider-required associated dependencies.
Destruction additionally requires `droplet:delete` and `firewall:delete`; issue a
separate temporary destruction token if preferred. No broad all-access token is
required. Check current provider scope dependency UI before issuing.

The owner pastes the token ONLY into Python's local hidden getpass prompt. Do not
paste it into ChatGPT, pass it in argv, export it to the guest, put it in cloud-init,
or commit it. It exists only in the provisioning process memory and HTTPS headers.
Revoke the provisioning token afterward. Error responses never print raw provider
bodies or credentials. Resource state contains IDs/stages only, not tokens.

## Linux release validation

The workflow is intended for a canonical project with validation commands run from this package directory; GitHub discovers workflow files under the repository-
root `.github/workflows/` directory. No repository or environment secrets are
required. Push the reviewed package and let the `Linux package release validation`
workflow complete, or start it from Actions. A successful run uploads one manifest
artifact. Download it, place `release-validation-manifest.json` at this package
root, then verify `SHA256SUMS` again before using the local create command. The
manifest is deliberately outside `SHA256SUMS`; its embedded fingerprint must match
that exact checksum file. This validates static compatibility only, not runtime
containment or live Worker readiness.

## Creation and recovery

The dedicated tag-specific firewall is created before the Droplet. Inbound is SSH
22 from the one owner IPv4 only; no Worker API rule. Outbound bootstrap allows
TCP 80/443 and UDP 53. This is not domain filtering. Restrict IPv6 is also off.
The Droplet gets only a secret-free cloud-init document. Bootstrap is guest-only.

Creation saves intent before POST and IDs immediately afterward. Network failure
around a POST is ambiguous; do not repeat create. Reconcile using the saved unique
deployment tag and exact IDs in DigitalOcean. No automatic broad rollback is run.
An exclusive local create-lock serializes create attempts. A stale lock after a
crash requires owner reconciliation before removal; do not blindly delete it.

Guest bootstrap installs required Ubuntu packages, a dedicated agent identity,
three service units, scoped UID egress rules, and pinned Node/Codex/Claude downloads.
Root API auth is generated inside the guest and never printed or included in
cloud-init. The synthetic runner sees no model credentials. Writers remain disabled.
Inspect guest cloud-init completion as owner before any synthetic probe; boot failure
must not be treated as readiness.

## Worker execution

API: POST `/v1/{create,start,inspect,cancel,stop,collect,cleanup}` on
`127.0.0.1:8765`. `Authorization: Bearer` requires the worker-specific secret.
See SECURITY.md for request contracts, isolation, egress and readiness gates.
No arbitrary shell/executable/PID/path/systemctl/credential endpoint exists.

The `agentbridge` account has no sudo, password login or admin key. Agent writable
storage is only the derived run worktree/runtime/artifacts. Parent directories,
policy, code, toolchain and controller state are root-owned. Agent processes run
inside a whitelisted bwrap filesystem plus a systemd run cgroup. There are no
production filesystem mounts or production credentials.

## Owner setup, synthetic proof, and later writer readiness

1. After reviewed provisioning, owner checks guest bootstrap and installed unit
   syntax. No live agents are started automatically.
2. Owner runs guest-root synthetic-only proof:
   `sudo /usr/bin/python3 /opt/agentbridge-worker/acceptance.py`.
   It never enables writers. Evidence is root-only under
   `/var/lib/agentbridge-worker/containment-evidence.json`.
3. Independently test egress (permitted and forbidden targets), toolchain CLI flags,
   source isolation, tunnel restrictions, auth, and Orchestrator reconnect/reboot
   reconciliation. These tests cannot be claimed from offline Windows tests.
4. A reviewed readiness operation must bind successful gates to current boot ID.
   No automatic readiness writer is provided: current package cannot autonomously
   enable live writes. Policy remains `writers_enabled: false`.
5. Model access later uses worker-specific, bounded-spend model credentials placed
   by the owner in guest-root `/etc/agentbridge-worker/model-auth/{codex,claude}.json`,
   directory 0700/files 0600. Never reuse production credentials. Only the fixed
   systemd LoadCredential for that selected agent is passed into its sandbox.

## Optional export and destruction

Owner may export synthetic evidence before destruction:
`sudo /usr/bin/python3 /opt/agentbridge-worker/export-evidence.py` redirected to an
owner-controlled local evidence file over the owner SSH session. This exports only
whitelisted structured synthetic proof, not raw logs, API auth, or model credentials.
Collect returns bounded text artifacts and results. Larger/binary artifacts require
a separate reviewed export; do not copy the whole guest home or `/etc`.

```text
python3 /absolute/path/agentbridge-digitalocean-writer-v1.0.2/destroy-worker.py --state /absolute/path/worker-resources.json --confirm-deployment-tag EXACT_TAG_FROM_STATE
```

Destruction checks exact IDs, name, region, size and unique deployment tag. It
deletes only that Droplet, verifies disappearance, then removes its dedicated
firewall. Shared owner SSH keys and generic tags are retained. A leftover empty
deployment tag is non-billable. Ambiguous creation requires reconciliation first.
Power off does not stop billing. Destroy the Droplet to stop its compute charge;
check the provider inventory afterward. No snapshots, volumes, reserved IPs or
other billable extras are created by these scripts.

## Offline verification and outstanding gates

`python -B -m unittest discover -s tests -v`

SHA256SUMS covers every shipped file except the checksum file itself. Python
bytecode caches are not part of the package. `offline-check.py` validates hashes,
schemas, Python syntax and static policy assertions without provisioning.
`.github/workflows/linux-release-validation.yml` runs on `ubuntu-24.04` and performs
hash/secret checks, Python/Worker and create-guard tests, YAML/cloud-init schema
validation, shell syntax/ShellCheck, and native `systemd-analyze verify`. It does not
call DigitalOcean or execute create/destroy paths. Only after every step succeeds,
the job uploads `release-validation-manifest.json`; copy that artifact into the
package root without changing any other package file. The create command checks its
fingerprint before requesting a token. Do not use production host to complete these checks.

See release-status.json and OFFLINE-RESULTS.json for staging results. No Linux
validation or release manifest is claimed until the GitHub Actions run and its
artifact exist. This workflow does not prove cgroup runtime containment, Worker
live integration, or network egress; those require later tests on the dedicated
Droplet. No live acceptance, guest startup, or cloud creation occurred during
staging.
