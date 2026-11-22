# Gaming as a Service preparation

This branch contains the GameRoom CRD, a lease-elected controller, the Project
Zomboid Kubernetes adapter, isolated restore/install/backup Jobs and a guarded
Terraform worker. It is undergoing isolated acceptance testing. Production
adoption has not happened. The example is deliberately non-runnable until a
real release catalogue and reviewed gateway configuration are supplied.
Do not add it to production Argo CD yet.

## Verified existing infrastructure

`labops-edge-01` is already owned by the LabOps Terraform stack on the local
`feat/edge-gateway` branch. Its VM ID is `169230729` (CX23, Nuremberg), retained
IPv4 ID is `154062800`, and public IPv4 is `188.245.252.46`. The stack owns the
DNS-only `pz.bingops.com` record, Hetzner firewall, Tailscale enrollment and
nftables configuration. It forwards public UDP 16261/16262 to
192.168.10.151:30261/30262 through the existing subnet router.

The VM is online in Tailscale as 100.97.24.17 with tag `edge-gateway`.
Public SSH is closed by the declared firewall. Tailscale SSH from the current
Proxmox operator identity was denied by tailnet policy. SSH is not required
for API-driven provisioning; no ACL was changed or bypassed.

The gateway's current state is still the protected local state under
`labops/terraform/edge-gateway`. The lifecycle refactor is in the isolated
LabOps branch `feat/gaas-lifecycle`. Read its owning runbook before running
Terraform. Never initialize an empty state and apply it as an adoption shortcut.

## Code boundaries

- `controller.py` polls GameRooms, acquires a Kubernetes Lease before mutations,
  runs one lifecycle operation per room, and reconciles the shared gateway.
  It is read-only unless `--apply` is explicitly passed.
- `runtime.py` renders the owned Helm resources with zero initial replicas and
  suspended backups. It refuses adoption of existing workloads and retains
  world PVCs without garbage-collection owner references. `world.serverName`
  is the original PZ save name and must be preserved when restoring a copy.
  Offline Jobs are serialized across all generations of a room: changing its
  desired state waits for an earlier restore/install/backup to terminate before
  starting another writer or a game Pod.
- `docker/scripts/gaas-operation.py` restores a world through a staging directory,
  validates restored SQLite databases, installs an immutable binary snapshot
  after checking the Steam build, and restores each shutdown backup again
  before emitting a verified receipt. No game state or credentials enter logs.

- `config/gamerooms.crd.json` defines `gaming.bingops.com/v1alpha1`, namespaced
  GameRooms with game, version, desired state, world repository and a shared
  gateway reference. Only Project Zomboid is accepted in this first schema.
- `lifecycle.py` produces one operation at a time from desired state and
  trusted runtime observations. It requires stopping and a verified backup
  receipt tied to the current UID/generation before suspension, deletion or
  release installation. An older nightly backup cannot authorize the action.
- A failed backup keeps the gateway consumer alive. A failed/partial room
  inventory cannot authorize removal of the last shared gateway.
- `terraform_worker.py` requires an initialized Kubernetes backend with Lease
  locking, an exact state namespace/suffix and an expected gateway name. It
  keeps Terraform plans/diagnostics private and rejects automatic replacement,
  deletion of retained resources and unrelated infrastructure changes. The
  default execution creates a plan only; `--apply` is explicit.
- A suspension worker lists GameRooms across all namespaces and rechecks the
  shared gateway's consumers immediately before applying the saved plan.
  The controller serializes gateway Jobs and delays game startup until an
  outstanding gateway transition has finished.

No template/example contains real credentials. The example remains Suspended
and uses a deliberately invalid repository URL. Its finalizer must only be
removed after the actual runtime adapter has completed its safe shutdown.

The worker image contains a filesystem mirror of the three providers selected
by the pinned LabOps module lock file. CI initializes a copy of that lock file
with networking disabled, without credentials or a backend. Gateway Jobs keep
`-lockfile=readonly` and never fall back to downloading other provider versions.
When the module's provider requirements/lock change, rebuild the worker with
the matching `GATEWAY_SOURCE_REVISION` before changing its immutable digest in
Git. This removes repeated provider downloads, not cloud API/network needs.
See [Terraform provider mirrors](https://developer.hashicorp.com/terraform/cli/commands/providers/mirror).

## GitOps and ownership

The proposed user interface reuses LabOps Portal and writes deterministic Git
changes, without requiring an AI for each action. See [the UX proposal](portal-design.md)
for the form, action policies, concurrency handling and activation prerequisites.
The portal integration is implemented on `bingops-com/portal` branch
`feat/game-room-portal`, with its activation procedure in `docs/gaming.md`.
It previews typed Git changes, commits start/stop requests and opens version
PRs. It is tested against simulated GitHub responses and a real image boot;
it is not deployed to the production portal. Live writes require a dedicated,
repository-restricted GitHub App, whose private key stays in Bitwarden.

Git/Argo CD will own desired GameRooms. One operator will own their generated
runtime objects, Jobs and status; Argo CD must not simultaneously force their
replicas or image back to another value. The existing production StatefulSet
remains owned by its current Argo Application. No ownership handoff has happened.

Project Zomboid and persistent worlds stay in Kubernetes. The first consumer
requires the shared Hetzner gateway; subsequent consumers reuse it. After the
last verified shutdown, Terraform can set `enabled=false`, preserving IPv4 and
DNS. The retained address still has a cost. Resume creates a new VM and a fresh
single-use Tailscale key on the same address.

The existing module replaces the VM for forwarding-table changes. The worker
explicitly rejects such replacements while consumers use it. Hot route updates
must be implemented and tested before claiming interruption-free multi-server
changes. Other game adapters are follow-up work.

## Reproducible game versions

`spec.version` is a release catalogue key, not just a container tag. The current
Steam build is 26486687 and the runtime image is 0.1.3. The existing installer
downloads a moving Steam branch at startup. A trustworthy release needs an
immutable runtime-image digest and a recoverable binary artefact/snapshot for
its Steam build. The acceptance export Job snapshots the live installation from a read-only
PVC mount into a separate encrypted Restic repository; GitHub Actions builds
separate runtime/worker tags and records their immutable digests. The planner
rejects unknown/incomplete releases before interrupting an existing game.

An upgrade requires a verified pre-upgrade world backup and a ready check.
This also applies when a recreated GameRoom has no previous version in status:
a restored or retained world gets a fresh backup before its first installation.
Downgrading executables is not a safe automatic rollback for a world already
modified by a newer game release; an explicit world restore may also be needed.

## Inputs and activation still required

The existing Hetzner, Cloudflare and edge-specific Tailscale OAuth entries
were found in Bitwarden Secrets Manager. LabOps acceptance manifests map their
UUIDs to `gaas-terraform-credentials`; no new tokens were requested and no
plaintext values belong in Git. The token and credentials are never logged.

Before cloud workers are enabled, create restricted state Secret/Lease RBAC,
a protected encrypted backup and tested restoration of that backend, and
perform one reviewed migration of the existing local state. Preserve its
lineage and resource IDs, stop other writers, and verify a non-destructive
adoption plan. The inactive backend example and full prerequisites are owned
by LabOps `terraform/edge-gateway/README.md`. No backend migration was run.

Build the immutable worker/runtime images, then test a disposable restored
world and a separately named gateway before reviewing any cutover of the active
server. The acceptance RBAC/state namespace is separate from the retained local
production Terraform state; a test run does not migrate or replace that state. Retained world volumes must
not have the GameRoom as a garbage-collected owner. Never delete the namespace,
world PVC, retained IPv4 or existing gateway as implicit cleanup.

## Validation and acceptance

```sh
python3 -m unittest discover -s gaas/tests -v
python3 -m unittest discover -s docker/tests -v
kubectl --context labprod create --dry-run=server -f gaas/config/gamerooms.crd.json
```

The lifecycle/worker/controller tests and backup/operation tests pass locally. The CRD
passes Kubernetes server dry-run validation and is installed for isolated tests. LabOps
has 3 provider-mocked Terraform tests covering enabled/suspend/resume. Those
mocked tests do not contact cloud APIs or prove network connectivity.

The existing world was independently restored from snapshot 2a2c94a8, all 82
repository packs were checked and 3 SQLite databases passed integrity checks.
The disposable Job was removed; the production PVC was never mounted by it.
See `operations/acceptance-20261122.json` for that earlier acceptance run.

The separate gateway `gaas-test-edge-20261122` was created, enrolled in Tailscale
and suspended through the guarded worker. Its VM and consumed bootstrap key
were deleted; the test DNS/IP were retained. The original gateway stayed online.
The active production PZ endpoint responds to A2S queries through its existing
gateway. These checks do not yet prove test-game UDP routing.

The immutable binary export completed with a full Restic integrity check:
Steam build `26486687`, snapshot
`d238fa72ae31f746704fb049a7a959a3eecd8d15e3b366acd5a33e8ca6d58ba7`
in the separate `gaas-releases-20261122` repository. The acceptance catalogue
pins this receipt and an immutable runtime image. Both test worlds and their
binary installations were restored and verified. The scheduled-backup script
also passed on the running second test room. See the current acceptance report
for the latest lifecycle observations rather than rerunning the completed
export.

Still in progress: lifecycle-managed game startup,
public test-game UDP routing, concurrent consumers and resume. An actual game
release update requires a second immutable Steam build; changing catalogue keys
for the same build proves redeployment only. Production workload configuration
and the original Terraform state have not been changed by this preparation.
