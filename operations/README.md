# Recovery validation

The current broader lifecycle test is recorded in
[`gaas-acceptance-20261122.json`](gaas-acceptance-20261122.json). The older
`acceptance-20261122.json` records only the initial restore check; its initial
cloud-credential blockers have since been resolved. Declared live test inputs
and their recovery procedure are in LabOps
`apps/workloads/gaas/acceptance/README.md` on `feat/gaas-lifecycle`.

`query-server.py HOST PORT` performs a read-only Steam A2S query without
printing player names. A successful reply checks the UDP information path,
not a full game-client connection. Its query version is not the immutable
Steam build used by the release catalogue.

## Current acceptance blocker

The entire Proxmox host rebooted at 15:15 and 16:47 UTC on 2026-11-22,
interrupting both binary-export attempts and the existing PZ workload. PZ
recovered and replied to public A2S queries after the second reboot. The test
GameRooms remain Suspended and their gateway VM remains absent. No further
binary export or concurrent-game start is scheduled automatically.

Read-only inspection of the host's retained kernel journals found Machine Check
hardware-error records on its Ryzen 9 5950X at both boots and on 2026-11-21,
before this intervention. These records are a diagnostic lead, not a proven
component-level cause. No BIOS, CPU, RAM, kernel or hypervisor configuration
was changed; no stress test was run. Investigate the host before resuming the
acceptance load. The [kernel MCE documentation](https://docs.kernel.org/arch/x86/x86_64/machinecheck.html)
explains the meaning and CPU-specific decoding boundary of these records.

After host recovery, inspect the existing test backend and Argo/controller
status before retrying. Declare a new export Job name, reuse the dedicated
binary Restic repository and its uploaded packs, and remove only stale locks
after confirming that no exporter remains active. Require a successful full
integrity check and full snapshot ID before filling the immutable release
catalogue. The source Job and the owning LabOps acceptance runbook describe
the inputs. Do not initialize an empty Terraform backend or adopt production
as a workaround. The current test backend has a verified encrypted recovery
bundle; its restoration to a live backend remains a separate reviewed action.

`restorecheck.json` is a disposable acceptance test for the existing Project Zomboid Restic repository. It uses the production credential Secret by reference, but never mounts the production PVC and never stops or changes the active game. The restored world is written to an emptyDir and disappears with the test Pod. The test checks the complete Restic repository and verifies the integrity of the restored SQLite databases. It does not prove that a game client can join the restored world.

The manifest records the 2026-11-22 acceptance run and its selected snapshot. For another run, review the snapshot, unique Job name, repository and pinned image digest before creating it. Do not add this one-shot Job to Argo CD auto-sync. Delete only the named disposable Job after inspecting its result.

```sh
kubectl --context labprod create -f operations/restorecheck.json
kubectl --context labprod wait -n pz-server --for=condition=complete job/pz-gaas-restorecheck-20261122 --timeout=300s
kubectl --context labprod logs -n pz-server job/pz-gaas-restorecheck-20261122
```

The existing Bitwarden-backed `pz-r2-credentials` Secret is a prerequisite. Its recovery and ownership are documented in LabOps under `apps/workloads/pz-server/README.md`. No credentials or game data belong in Git.
