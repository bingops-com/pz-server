# Gaming as a Service

The GameRoom CRD, lease-elected controller and Project Zomboid adapter are
installed in the production `gaming` namespace. Git/Argo CD own desired rooms;
the controller owns generated workloads and verified offline operations.
The example remains deliberately non-runnable without a real release catalogue
and reviewed gateway configuration.

## Production ownership and recovery

The [LabOps production runbook](https://github.com/bingops-com/labops/tree/master/apps/workloads/gaas/production)
is the deployment and recovery source of truth. Its dated acceptance receipt
records observed lifecycle results and remaining limits. The original gateway
state was migrated with lineage/IDs preserved into `gaming-state`, suffix
`edge-gateway`, and its local execution copy was retired after encrypted
backup/restore comparison. Never initialize an empty backend over retained
resources or resume the retired local writer.

The shared gateway is named `labops-edge-01`. IPv4 `188.245.252.46`, primary IP
`154062800` and `pz.bingops.com` remain retained across VM removal/recreation.
UDP 16261/16262 forward to 192.168.10.151:30261/30262. VM and Tailscale node
identities change on resume; their initial IDs are historical adoption facts.
SSH is not required for API provisioning. Existing firewall and Tailscale
access boundaries remain in force.

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

LabOps Portal has a gaming page with current Git/Argo/GameRoom status. Without
a GitHub App it links to the owner GitHub form for Running/Suspended changes;
the workflow records typed intent directly in Git without AI or cloud/Kubernetes
credentials. A dedicated repository-restricted App remains optional for
integrated Portal buttons and version PRs; its private key belongs in Bitwarden.
See the Portal repository's `docs/gaming.md` for authentication and write policies.

Argo CD must not simultaneously force generated replicas or images. The legacy
production Application is retained at zero replicas with a ClusterIP Service,
no scheduled backup and immutable runtime/Steam-build rollback pins. Its original
PVC stays retained; the GameRoom uses a separate retained PVC. Follow the owning
runbook before rollback so newer gameplay is not silently discarded.

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

`spec.version` selects an immutable release catalogue entry, including a runtime
image digest and full binary snapshot for one Steam build. Production starts
with `pz-steam-26486687-r1`; the catalogue in LabOps owns its exact artifact IDs.
The controller rejects unknown/incomplete releases before interrupting a game.
The init container verifies the expected installed Steam build instead of
following a moving Steam branch. The legacy chart keeps that behavior optional
so unchanged installations retain their previous rendered configuration.

An upgrade requires a verified pre-upgrade world backup and a ready check.
Binary installation receipts bind the world ID, repository, full snapshot and
Steam build. A later request can reuse that exact artifact after checking the
installed build, while issuing a receipt for the new request/version. This
allows binaries to be preloaded into a separate retained PVC before cutover;
it never skips the fresh world backup. Different or legacy artifact receipts
require a restore. A receipt/build mismatch blocks the operation.
This also applies when a recreated GameRoom has no previous version in status:
a restored or retained world gets a fresh backup before its first installation.
Downgrading executables is not a safe automatic rollback for a world already
modified by a newer game release; an explicit world restore may also be needed.

## External inputs and reconstruction

Existing Hetzner, Cloudflare and edge-specific Tailscale OAuth entries are
mapped from Bitwarden to `gaas-terraform-credentials`; game/R2 Secrets use the
same established recovery owner. No plaintext value belongs in Git or logs.
The LabOps runbook owns Secret mappings, namespace/RBAC bootstrap, encrypted
backend recovery, source/image pins and the dependency order. Restore and
verify the backend before enabling production workers. Isolated acceptance
uses its own backend and retained test resources; it must never adopt the
production state. World PVCs have no garbage-collection owner reference.

## Validation and acceptance

```sh
python3 -m unittest discover -s gaas/tests -v
python3 -m unittest discover -s docker/tests -v
kubectl --context labprod create --dry-run=server -f gaas/config/gamerooms.crd.json
```

The lifecycle/worker/controller tests and backup/operation tests pass locally. The CRD
passes Kubernetes server dry-run validation and is installed in production. LabOps
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

The isolated acceptance run verified both public UDP endpoints through one
gateway VM, suspension of one consumer without interrupting the other, verified
last-consumer shutdown and VM deletion, and resume with a new VM on the retained
address and unchanged world PVC. It also deleted/recreated the second GameRoom:
the finalizer produced a fresh backup, generated workloads were collected and
the retained world passed all three SQLite checks through a read-only mount.
The final Git intent is both rooms Suspended with no test gateway VM. The latest
test backend was encrypted, restored to a file and compared without modifying
the live backend. Full observations are in `operations/gaas-acceptance-20261122.json`.

An actual game release update still requires a second immutable Steam build;
changing catalogue keys for the same build proves redeployment only. Portal
App writes, production ownership/state handoff and the host reboot cause remain
unresolved. Production workload configuration and the original Terraform state
have not been changed by this preparation.


## Optional fully ephemeral gateway

Gateway configuration can select `resourcePolicy: ephemeral` with
`variables.retain_gateway_resources: false`, a DNS-only TTL of 60 seconds and
`dnsDrainSeconds` at least the prior record cache lifetime. Start with 300
seconds when migrating a legacy Auto-TTL endpoint. The policy belongs to the
shared gateway, not to each GameRoom. Retained remains the backward-compatible
default. Module source/image digests must be promoted together after tests.

After the last current verified shutdown, the operator first withdraws DNS and
removes compute while holding its address. Its ConfigMap persists the drain
floor/deadline and original worker inputs across restarts. Only then does a
separate guarded worker release IPv4, firewall, cloud SSH key and marker. World
PVCs, encrypted saves and the protected backend remain. Release rechecks all
namespaces for new consumers immediately before apply; no unrelated deletion or
running-gateway replacement is permitted. Same-direction failures require an
explicit config retry; a changed desired direction can recover a cancelled
transition. Failed withdrawals retain the bounded pre-apply DNS TTL marker.

A Running request during draining cancels release and reuses the held address.
After full release a later Running request creates a fresh VM/address/key and
updates every declared game hostname. DNS must resolve to the new address
before the game starts. A suspended object's backup alone cannot allocate a
new VM, and an already-empty backend cannot allocate idle resources merely
for draining. `status.gatewayPhase` distinguishes withdrawal, DNS draining,
resource release and readiness; game Suspended alone does not mean the DNS
cache drain has finished. Existing IP favorites can become stale; actual game
client behavior still requires a client test in addition to public A2S probes.
