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

## Backups and restore

The CronJob places a backup lock on the PVC, asks the running server to save and
stop through cluster-internal RCON, then sends `/data/Zomboid` to the configured
Restic repository on R2. The StatefulSet restarts automatically but waits for
the lock to disappear. This creates a short nightly outage in exchange for a
consistent filesystem backup. A six-hour stale-lock guard prevents a failed
backup node from blocking startup indefinitely. The default policy retains 7
daily, 4 weekly and 6 monthly snapshots.

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
