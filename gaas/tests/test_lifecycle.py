import copy
import unittest

from gaas.lifecycle import decide, request_id, shared_gateway_required, validate_terraform_plan


RELEASES = {
    version: {"runtimeImage": "example.invalid/pz@sha256:" + "a" * 64,
              "steamBuildId": build, "binarySnapshot": "a" * 64}
    for version, build in (("release-one", "100"), ("release-two", "200"))
}


def room(state="Running", version="release-one", uid="room-one", generation=1):
    return {"metadata": {"uid": uid, "generation": generation},
            "spec": {"game": "project-zomboid", "state": state, "version": version}}


def ready(version="release-one"):
    return {"worldReady": True, "running": True, "podExists": True,
            "ready": True, "version": version, "gatewayReady": True,
            "scheduledBackupSuspended": False}


class LifecycleTests(unittest.TestCase):
    def test_suspend_requires_stop_and_verified_backup(self):
        desired = room(state="Suspended", generation=2)
        live = ready()
        self.assertEqual(decide(desired, live, RELEASES).operation, "SuspendScheduledBackup")
        live["scheduledBackupSuspended"] = True
        self.assertEqual(decide(desired, live, RELEASES).operation, "Stop")
        live.update(running=False, podExists=False)
        decision = decide(desired, live, RELEASES)
        self.assertEqual(decision.operation, "BackupStoppedWorld")
        self.assertTrue(decision.needs_gateway)
        live["backup"] = {"requestId": decision.request_id, "snapshot": "snapshot-id", "verified": True}
        decision = decide(desired, live, RELEASES)
        self.assertEqual(decision.phase, "Suspended")
        self.assertFalse(decision.needs_gateway)

    def test_old_generation_backup_cannot_authorize_deletion(self):
        desired = room(state="Suspended", generation=3)
        desired["metadata"]["deletionTimestamp"] = "2026-11-22T00:00:00Z"
        live = dict(ready(), running=False, podExists=False, scheduledBackupSuspended=True)
        old = copy.deepcopy(desired)
        old["metadata"]["generation"] = 2
        live["backup"] = {"requestId": request_id(old, "delete"), "snapshot": "old", "verified": True}
        self.assertEqual(decide(desired, live, RELEASES).operation, "BackupStoppedWorld")

    def test_deletion_of_never_started_room_needs_no_backup(self):
        desired = room()
        desired["metadata"]["deletionTimestamp"] = "2026-11-22T00:00:00Z"
        observed = {"scheduledBackupSuspended": True}
        self.assertEqual(decide(desired, observed, RELEASES).phase, "Finalized")

    def test_backup_failure_holds_gateway(self):
        decision = decide(room(state="Suspended"), dict(ready(), error="backup failed"), RELEASES)
        self.assertEqual(decision.phase, "Blocked")
        self.assertTrue(decision.needs_gateway)

    def test_upgrade_does_not_install_before_backup(self):
        desired = room(version="release-two", generation=2)
        live = dict(ready(), running=False, podExists=False, scheduledBackupSuspended=True)
        decision = decide(desired, live, RELEASES)
        self.assertEqual(decision.operation, "BackupStoppedWorld")
        live["backup"] = {"requestId": decision.request_id, "snapshot": "pre-upgrade", "verified": True}
        self.assertEqual(decide(desired, live, RELEASES).operation, "InstallRelease")

    def test_invalid_release_never_interrupts_existing_game(self):
        decision = decide(room(version="unknown"), ready(), RELEASES)
        self.assertEqual(decision.phase, "Blocked")
        self.assertIsNone(decision.operation)
        self.assertTrue(decision.needs_gateway)

    def test_invalid_release_does_not_prevent_safe_suspension(self):
        desired = room(version="unknown", state="Suspended")
        self.assertEqual(decide(desired, ready(), RELEASES).operation, "SuspendScheduledBackup")

    def test_mutable_image_tag_is_rejected(self):
        catalogue = copy.deepcopy(RELEASES)
        catalogue["release-one"]["runtimeImage"] = "example.invalid/pz:latest"
        self.assertEqual(decide(room(), {}, catalogue).phase, "Blocked")

    def test_shared_gateway_survives_one_consumer_stopping(self):
        active = decide(room(uid="active"), ready(), RELEASES)
        stopped_room = room(state="Suspended", uid="stopped")
        stopped = decide(stopped_room, {"scheduledBackupSuspended": True}, RELEASES)
        self.assertTrue(shared_gateway_required([active, stopped], inventory_complete=True))
        self.assertFalse(shared_gateway_required([stopped], inventory_complete=True))

    def test_partial_inventory_cannot_delete_gateway(self):
        with self.assertRaises(ValueError):
            shared_gateway_required([], inventory_complete=False)

    def test_waits_for_existing_scheduled_backup(self):
        observed = dict(ready(), scheduledBackupActive=True)
        self.assertIsNone(decide(room(state="Suspended"), observed, RELEASES).operation)

    def test_new_world_is_restored_before_start(self):
        self.assertEqual(decide(room(), {}, RELEASES).operation, "RestoreWorld")

    def test_ready_room_is_idempotent(self):
        decision = decide(room(), ready(), RELEASES)
        self.assertEqual(decision.phase, "Running")
        self.assertIsNone(decision.operation)


def change(address, actions, before=None, after=None, resource_type=None):
    return {"address": address, "mode": "managed",
            "type": resource_type or address.split(".")[0],
            "change": {"actions": actions, "before": before, "after": after}}


class TerraformSafetyTests(unittest.TestCase):
    def test_suspend_accepts_only_exact_compute_and_key_deletions(self):
        plan = {"resource_changes": [
            change("hcloud_server.edge_gateway[0]", ["delete"], {"name": "test-edge"}),
            change("tailscale_tailnet_key.edge_gateway[0]", ["delete"]),
        ]}
        self.assertEqual(len(validate_terraform_plan(plan, enabled=False, expected_name="test-edge")), 2)

    def test_retained_address_cannot_be_deleted(self):
        plan = {"resource_changes": [change("hcloud_primary_ip.edge_gateway", ["delete"])]}
        with self.assertRaises(ValueError):
            validate_terraform_plan(plan, enabled=False, expected_name="test-edge")

    def test_replacement_is_rejected_while_consumers_use_gateway(self):
        plan = {"resource_changes": [change("hcloud_server.edge_gateway[0]", ["delete", "create"], {"name": "test-edge"}, {"name": "test-edge"})]}
        with self.assertRaises(ValueError):
            validate_terraform_plan(plan, enabled=True, expected_name="test-edge")

    def test_wrong_target_name_is_rejected(self):
        plan = {"resource_changes": [change("hcloud_server.edge_gateway[0]", ["delete"], {"name": "production"})]}
        with self.assertRaises(ValueError):
            validate_terraform_plan(plan, enabled=False, expected_name="test-edge")

    def test_summary_never_returns_sensitive_plan_values(self):
        plan = {"resource_changes": [change("hcloud_server.edge_gateway[0]", ["create"], after={"name": "test-edge", "user_data": "SECRET"})]}
        summary = validate_terraform_plan(plan, enabled=True, expected_name="test-edge")
        self.assertNotIn("SECRET", repr(summary))


if __name__ == "__main__":
    unittest.main()
