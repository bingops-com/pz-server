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

The production handoff and its current lifecycle receipts are maintained in
[LabOps production gaming](https://github.com/bingops-com/labops/tree/master/apps/workloads/gaas/production).
The following isolated-test chronology records the earlier 2026-11-22 state.

## Acceptance progress and host instability

The entire Proxmox host rebooted at 15:15, 16:47 and 18:55 UTC on 2026-11-22,
interrupting acceptance Jobs and the existing PZ workload. Production PZ
recovered and replied to public A2S queries after the third reboot. Its
configuration and original gateway ownership have not changed.

The bounded binary-export retry completed before the third reboot, including
the full integrity check. Its immutable snapshot is recorded in the JSON
report and the LabOps release catalogue; do not repeat this completed export.
Both isolated worlds and binary installations were restored and verified.
The first installation and the test gateway start were explicitly resumed
using declared retry nonces after checking failed Jobs and the retained
backend. Both public test endpoints replied through one gateway; the scheduled
backup script passed on a running game. One consumer was suspended without
interrupting the other, then the last verified suspension removed the test VM
while retaining its IP/DNS. Resume reused the world/PVC and address on a new VM.
Deleting/recreating the second GameRoom retained its PVC, produced a fresh
deletion backup and passed all three SQLite checks on a read-only mount.
Both test objects finish Suspended with the test VM absent. A current encrypted
backend bundle was restored to a file and compared without changing live state.
Read the JSON report for receipts, exact IDs and remaining activation limits.

Read-only inspection of the host's retained kernel journals found Machine Check
hardware-error records on its Ryzen 9 5950X at these boots and on 2026-11-21,
before this intervention. These records are a diagnostic lead, not a proven
component-level cause. No BIOS, CPU, RAM, kernel or hypervisor configuration
was changed during that isolated run; no stress test was run. On 2026-11-23,
a separate reviewed LabOps Ansible role disabled CPU boost without rebooting
as a reversible mitigation trial; see LabOps `docs/host-stability.md`. This
does not establish the reboot cause or long-term stability. The reboot cause remains unresolved;
bounded acceptance retries do not establish host stability. The
[kernel MCE documentation](https://docs.kernel.org/arch/x86/x86_64/machinecheck.html)
explains the meaning and CPU-specific decoding boundary of these records.

After any interruption, inspect the existing test backend and Argo/controller
status before retrying. Change only the affected declared retry nonce; do not
delete backend state or overwrite a successful release receipt. A failed
binary export can reuse its dedicated Restic repository and uploaded packs;
remove only stale locks after confirming that no exporter remains active.
The source Jobs and the owning LabOps acceptance runbook describe the inputs.
Do not initialize an empty Terraform backend or adopt production
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
