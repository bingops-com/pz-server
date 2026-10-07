# Kubernetes migration runbook

## Compatibility boundary

The legacy VM runs Build 41 and the Kubernetes workload runs Build 42. The
world, player database and existing mod set must therefore be treated as an
immutable recovery archive, not restored directly into the active Build 42
server. The official Build 42 release notes explicitly state that Build 41
saves and mods are not compatible.

## Ownership

- `pz-server` owns the OCI image, Helm chart and game configuration.
- LabOps Terraform owns the dedicated `bingops-pz-labprod` R2 bucket.
- Bitwarden owns all plaintext passwords and the bucket-scoped R2 credentials.
- LabOps Argo CD owns the namespace, Bitwarden mappings and Helm release.
- The future public UDP gateway owns forwarding to the two Kubernetes
  NodePorts; RCON is never published.

## Required backup before cutover

Before changing or stopping the VM, create and verify a full Proxmox VM backup.
Also preserve `/home/pzuser/Zomboid`, `/opt/pzserver`, the Zomboid systemd units
and the Playit configuration in an encrypted archive. The archive contains
passwords and player data and must never be committed to Git or copied into a
chat transcript.

Record the VM backup identifier, archive checksum, creation time, encryption
key owner and tested recovery location in the operator's private inventory.
Do not retire the VM until the archive can be listed or restored and a Build 42
Restic backup has passed a disposable restore test.

## Bitwarden prerequisites

Create six distinct Bitwarden Secrets Manager entries in the existing
`labprod` project:

- Project Zomboid administrator password;
- Project Zomboid join password;
- Project Zomboid RCON password;
- R2 access key ID restricted to `bingops-pz-labprod`;
- R2 secret access key restricted to `bingops-pz-labprod`;
- a randomly generated Restic repository password.

Only the entry UUIDs belong in Git through `BitwardenSecret` mappings. Losing
the Restic password makes every repository snapshot unrecoverable, so its
Bitwarden entry is part of the disaster-recovery inputs.

## Cutover order

1. Merge and publish the OCI image.
2. Apply the reviewed Terraform plan that creates the R2 bucket.
3. Create the bucket-scoped R2 token and all Bitwarden entries.
4. Add their UUID mappings to LabOps and reconcile the workload.
5. Start a new Build 42 world with mods disabled.
6. Route the public UDP gateway to NodePorts `30261` and `30262`.
7. Verify client access, RCON isolation and the scheduled Restic snapshot.
8. Perform a disposable restore before retiring the Build 41 VM.

No step in this runbook authorizes a live apply, Argo CD sync, VM shutdown or
backup operation. Each live action requires explicit operator approval.

## Offline validation

```sh
helm lint charts/pz-server
```

```sh
helm template pz-server charts/pz-server >/dev/null
```
