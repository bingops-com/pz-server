# Recovery validation

`restorecheck.json` is a disposable acceptance test for the existing Project Zomboid Restic repository. It uses the production credential Secret by reference, but never mounts the production PVC and never stops or changes the active game. The restored world is written to an emptyDir and disappears with the test Pod. The test checks the complete Restic repository and verifies the integrity of the restored SQLite databases. It does not prove that a game client can join the restored world.

The manifest records the 2026-11-22 acceptance run and its selected snapshot. For another run, review the snapshot, unique Job name, repository and pinned image digest before creating it. Do not add this one-shot Job to Argo CD auto-sync. Delete only the named disposable Job after inspecting its result.

```sh
kubectl --context labprod create -f operations/restorecheck.json
kubectl --context labprod wait -n pz-server --for=condition=complete job/pz-gaas-restorecheck-20261122 --timeout=300s
kubectl --context labprod logs -n pz-server job/pz-gaas-restorecheck-20261122
```

The existing Bitwarden-backed `pz-r2-credentials` Secret is a prerequisite. Its recovery and ownership are documented in LabOps under `apps/workloads/pz-server/README.md`. No credentials or game data belong in Git.
