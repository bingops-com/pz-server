import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gaas.terraform_worker import command, execute, require_suspended_inventory, validate_backend
from gaas.lifecycle import request_id


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".terraform").mkdir()
        self.write_backend("kubernetes")

    def write_backend(self, kind):
        (self.root / ".terraform" / "terraform.tfstate").write_text(json.dumps({
            "backend": {"type": kind, "config": {"namespace": "gaas-state", "secret_suffix": "test"}}
        }))

    def test_no_unlocked_local_backend(self):
        self.write_backend("local")
        with self.assertRaises(ValueError):
            validate_backend(self.root, "gaas-state", "test")

    def test_wrong_state_cannot_be_used(self):
        with self.assertRaises(ValueError):
            validate_backend(self.root, "gaas-state", "production")

    def test_leftover_local_state_cannot_be_used(self):
        (self.root / "terraform.tfstate").write_text("private state")
        with self.assertRaises(ValueError):
            validate_backend(self.root, "gaas-state", "test")

    def test_provider_diagnostics_cannot_leak_secrets(self):
        with patch("gaas.terraform_worker.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "SECRET"
            run.return_value.stdout = "SECRET"
            with self.assertRaises(RuntimeError) as error:
                command(["terraform", "plan"], self.root)
            self.assertNotIn("SECRET", str(error.exception))

    def test_active_room_prevents_suspension(self):
        with self.assertRaises(ValueError):
            require_suspended_inventory({"kind": "List", "items": [{
                "metadata": {"generation": 1}, "spec": {"state": "Running"}
            }]})

    def test_other_namespace_on_same_gateway_still_prevents_suspension(self):
        with self.assertRaises(ValueError):
            require_suspended_inventory({"kind": "GameRoomList", "items": [{
                "metadata": {"generation": 1, "namespace": "another-game"},
                "spec": {"state": "Running", "gatewayRef": "shared"}
            }]})

    def test_other_gateway_does_not_hold_this_gateway(self):
        require_suspended_inventory({"kind": "GameRoomList", "items": [{
            "metadata": {"generation": 1},
            "spec": {"state": "Running", "gatewayRef": "other"}
        }]})

    def test_current_verified_receipt_allows_suspension(self):
        item = {"metadata": {"generation": 2, "uid": "room"}, "spec": {"state": "Suspended"},
                "status": {"observedGeneration": 2, "phase": "Suspended",
                           "backup": {"verified": True, "snapshot": "snapshot"}}}
        item["status"]["backup"]["requestId"] = request_id(item, "suspend")
        require_suspended_inventory({"kind": "GameRoomList", "items": [item]})

    def test_receipt_from_another_request_is_rejected(self):
        with self.assertRaises(ValueError):
            require_suspended_inventory({"kind": "GameRoomList", "items": [{
                "metadata": {"generation": 2, "uid": "room"}, "spec": {"state": "Suspended"},
                "status": {"observedGeneration": 2, "phase": "Suspended",
                           "backup": {"verified": True, "snapshot": "old", "requestId": "other"}}
            }]})

    def test_failed_inventory_is_not_an_empty_list(self):
        with self.assertRaises(ValueError):
            require_suspended_inventory({"kind": "Status", "reason": "Forbidden"})

    def test_stale_status_does_not_authorize_suspension(self):
        with self.assertRaises(ValueError):
            require_suspended_inventory({"kind": "List", "items": [{
                "metadata": {"generation": 2}, "spec": {"state": "Suspended"},
                "status": {"observedGeneration": 1, "phase": "Suspended"}
            }]})

    def test_new_consumer_between_plan_and_apply_aborts(self):
        with patch("gaas.terraform_worker.inventory", side_effect=[
                {"kind": "List", "items": []},
                {"kind": "List", "items": [{"metadata": {}, "spec": {"state": "Running"}}]}]), \
             patch("gaas.terraform_worker.command", side_effect=["", '{"resource_changes": []}']) as run:
            with self.assertRaises(ValueError):
                execute(self.root, enabled=False, expected_name="test", state_namespace="gaas-state",
                        state_suffix="test", gateway="shared", apply=True)
            self.assertFalse(any(call.args[0][1] == "apply" for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
