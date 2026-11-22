# Gaming as a Service preparation

This branch contains a GameRoom CRD, the pure lifecycle decision engine and a
restricted Terraform transition worker. It is not a deployed operator. The
Kubernetes runtime adapter, event loop, worker image, release artefact pipeline
and GitOps installation/adoption are unfinished. Creating the example object
will not create a game server; do not add it to production Argo CD yet.

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
  A future controller must serialize gateway jobs and delay new game startup
  until an outstanding gateway transition has finished.

No template/example contains real credentials. The example remains Suspended
and uses a deliberately invalid repository URL. Its finalizer must only be
removed after the actual runtime adapter has completed its safe shutdown.

## GitOps and ownership

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
its Steam build. This artefact pipeline is not implemented yet. The planner
rejects unknown/incomplete releases before interrupting an existing game.

An upgrade requires a verified pre-upgrade world backup and a ready check.
Downgrading executables is not a safe automatic rollback for a world already
modified by a newer game release; an explicit world restore may also be needed.

## Inputs and activation still required

Recover the existing Hetzner, Cloudflare and edge-specific Tailscale OAuth
credentials from the password manager. Only their names/UUID mappings belong
in Git; plaintext values must be materialized by Bitwarden Secrets Manager.
Do not request new credentials until the existing ones have been located.

Before cloud workers are enabled, create restricted state Secret/Lease RBAC,
a protected encrypted backup and tested restoration of that backend, and
perform one reviewed migration of the existing local state. Preserve its
lineage and resource IDs, stop other writers, and verify a non-destructive
adoption plan. The inactive backend example and full prerequisites are owned
by LabOps `terraform/edge-gateway/README.md`. No backend migration was run.

Then finish the runtime adapter and event loop, build immutable worker/runtime
images, and test a disposable restored world and a separately named gateway
before reviewing any cutover of the active server. Retained world volumes must
not have the GameRoom as a garbage-collected owner. Never delete the namespace,
world PVC, retained IPv4 or existing gateway as implicit cleanup.

## Validation and acceptance

```sh
python3 -m unittest discover -s gaas/tests -v
python3 -m unittest discover -s docker/tests -v
kubectl --context labprod create --dry-run=server -f gaas/config/gamerooms.crd.json
```

The 30 lifecycle/worker unit tests and 8 backup tests pass locally. The CRD
passes Kubernetes server dry-run validation and has not been installed. LabOps
has 3 provider-mocked Terraform tests covering enabled/suspend/resume. Those
mocked tests do not contact cloud APIs or prove network connectivity.

The existing world was independently restored from snapshot 2a2c94a8, all 82
repository packs were checked and 3 SQLite databases passed integrity checks.
The disposable Job was removed; the production PVC was never mounted by it.
See `operations/acceptance-20261122.json` for that earlier acceptance run.

Still untested: actual VM creation/deletion, Tailscale enrollment, public UDP
routing, a second concurrent consumer, lifecycle-managed game startup and an
actual game release update. Production configuration and Terraform state have
not been changed by this preparation.
