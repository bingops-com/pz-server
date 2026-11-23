# Project Zomboid Helm chart

This chart runs one Project Zomboid Build 42 server, exposes its two UDP ports
through fixed NodePorts, keeps RCON private, and stores both the Steam server
installation and game state on one retained PVC.

## Secrets

The chart never creates plaintext Secrets. `server.secret.existingSecret` must
contain `ADMIN_PASSWORD`, `SERVER_PASSWORD`, and `RCON_PASSWORD`.

When backups are enabled, `backup.secret.existingSecret` must contain
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `RESTIC_PASSWORD`. In LabOps,
both Secrets are materialized by the Bitwarden Secrets Manager operator.

## Persistent data

The server is started with `-cachedir=/data/Zomboid`, so the world, player
accounts and the configuration rendered from this chart all live on the PVC.
Without that flag the game writes under the passwd home of its user, inside
the container: the world is lost with the pod, the rendered configuration
(join password included) is ignored and backups contain nothing. Verify after
a rollout:

```sh
kubectl --context labprod exec -n pz-server pz-server-pz-server-0 -c pz-server -- ls /data/Zomboid/Saves/Multiplayer
```

## Backups and restore

The CronJob places a backup lock on the PVC, asks the running server to save and
stop through a named console pipe on the shared volume, then sends
`/data/Zomboid` to the configured Restic repository on R2. The StatefulSet
restarts automatically but waits for the lock to disappear. This creates a
short nightly outage in exchange for a consistent filesystem backup. A
six-hour stale-lock guard prevents a failed backup node from blocking startup
indefinitely. The default policy retains 7 daily, 4 weekly and 6 monthly
snapshots. Snapshot retention is grouped by paths and tags rather than the
ephemeral Kubernetes Job hostname. Backups use a stable Restic host identity.

The repository must already be initialized. Storage access is checked before
stopping the game; an authentication or network error never triggers repository
initialization. Console timeouts report the failing stage. The backup lock is
acquired exclusively so concurrent manual jobs cannot remove another backup's
lock.

For lifecycle-managed shutdown, first scale the StatefulSet to zero and confirm
its Pod has terminated, then run the backup with `BACKUP_MODE=stopped`. This
mode refuses to upload while a running marker remains. Never delete the PVC or
external gateway on the basis of a failed backup. A controller must also stop
Argo CD from immediately recreating a deliberately suspended workload by giving
one controller clear ownership of its runtime resources.

Run the offline backup safety checks with:

```sh
python3 -m unittest discover -s docker/tests -v
```

Restore is deliberately disabled by default. Before restoring, scale the
StatefulSet and backup CronJob down, ensure `/data/Zomboid` is empty, then set
`restore.enabled=true` with a new non-empty `restore.id`. The restore script
refuses to overwrite a non-empty data directory.

## Build 41 migration

Keep a complete, immutable backup of the old VM before migration. Build 41
saves and mods are not automatically compatible with Build 42, so the chart
starts a new Build 42 world and ships the migrated mod catalogue disabled.
Enable only mods explicitly verified for Build 42.

The old archive remains the recovery source for configuration or player data;
it must not be restored directly over the active Build 42 world.

## Validation

```sh
helm lint charts/pz-server
```

```sh
helm template pz-server charts/pz-server >/dev/null
```

`replicaCount` defaults to 1. During the GitOps ownership handoff it can be set to 0 while retaining the original StatefulSet and PVC as a rollback source. Set the legacy Service type to ClusterIP before assigning its public NodePorts to the GameRoom workload.

For a retained legacy rollback, set `image.digest` to the verified runtime digest
and `steam.expectedBuildId` to the installed Steam build. The installer then
checks the local manifest and avoids a moving-branch download. Both are optional;
without them the rendered legacy workload remains unchanged. GameRoom rendering
keeps its catalogue's immutable image/build authoritative.
