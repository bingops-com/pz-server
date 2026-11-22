# Gaming as a Service: implementation contract

Status: recovery validation and backup hardening are implemented on this branch.
The GameRoom controller, CRD, Hetzner provisioning and production migration are
not implemented or deployed yet. Do not treat this branch as a working operator.

## Ownership and GitOps

- Git/Argo CD owns GameRoom desired state: game, release version, running or
  suspended state, persistent world identity and shared gateway reference.
- One operator owns the generated workloads and routes. Argo CD must not
  independently reconcile the same StatefulSet replicas or image.
- Existing production resources remain owned by their current Argo CD
  Application until an explicit adoption/cutover is reviewed.
- Deleting a GameRoom from Git must finish a verified backup before its
  finalizer releases runtime resources. World storage and backups are retained.
- A production gateway already in use must not be adopted, replaced or deleted
  without identifying its exact resource and validating its current routes.

## Shared gateway

Game processes remain in labprod. The Hetzner VM is a shared Internet gateway,
not a separate VM per game. The first active room provisions it; additional
rooms reuse it with distinct public protocol/port allocations. A room shutdown
must not remove routes belonging to another room. The last room releases the
gateway only after shutdown/backup and a reconciliation check for new consumers.
Provider labels and persisted identities must allow recovery after controller
restart and interrupted provisioning without duplicate VMs.

Provisioning is currently blocked on the existing gateway identity,
reproducible network/tunnel configuration, required credentials and ownership
of any retained public IP/DNS. Credentials belong in Bitwarden and generated
Secrets, never in Git or chat.

## Versions and game adapters

Project Zomboid is the first supported game. Other games are follow-up work.
Each adapter defines health checks, graceful shutdown, backup, restore and
version installation while the gateway lifecycle remains common.

Changing spec.version in Git must resolve to a reproducible game release,
create a pre-upgrade backup, stop cleanly, install that release and verify
startup. The currently installed Steam server build is 26486687 and its
runtime image is 0.1.3; those are different identifiers. The existing installer
downloads the moving Steam branch, so changing an image tag alone does not pin
a game version. A release catalogue and recoverable immutable installation
artifact are prerequisites for honest version pinning. Never assume that an
older executable can safely read a world upgraded by a newer release.

## Required acceptance tests

1. Retain and restore the existing world; verify player/account databases.
2. Run the new adapter on an isolated restored copy and verify game readiness.
3. Provision a disposable gateway and verify public UDP routing.
4. Add a second route/consumer and prove that the VM is reused.
5. Remove one consumer and prove that the other route remains operational.
6. Remove the last consumer, verify the final backup and destroy only the
   disposable gateway.
7. Reapply desired state and verify restoration of the same world.
8. Change the release version and verify the backup/update/health workflow.

Only item 1 is complete. The recovery Job used emptyDir, never mounted the
production PVC and has been removed. See operations/acceptance-20261122.json.
The backup regression suite covers eight offline safety cases and passes CI.
