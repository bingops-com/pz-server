"""Backup safety regressions; uses real Bash/FIFOs, no cluster or credentials."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        (self.data / "Zomboid" / "Saves" / "Multiplayer" / "test").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        restic = self.bin / "restic"
        restic.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
            "if sys.argv[1] == os.environ.get('FAIL_RESTIC_COMMAND'): sys.exit(1)\n"
        )
        restic.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ["PATH"],
                        PZ_DATA_DIR=str(self.data), CALL_LOG=str(self.log),
                        RESTIC_REPOSITORY="test-only", RESTIC_PASSWORD="test-only",
                        AWS_ACCESS_KEY_ID="test-only", AWS_SECRET_ACCESS_KEY="test-only",
                        RESTIC_CACHE_DIR=str(self.root / "cache"),
                        BACKUP_MODE="stopped", SAVE_SETTLE_SECONDS="0",
                        SHUTDOWN_TIMEOUT_SECONDS="2", CONTROL_TIMEOUT_SECONDS="1")

    def run_backup(self, **overrides):
        return subprocess.run(["bash", str(SCRIPT)], env=dict(self.env, **overrides),
                              text=True, capture_output=True, timeout=8)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_stopped_backup_checks_before_retention_and_uses_stable_identity(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual([c[0] for c in calls], ["snapshots", "backup", "check", "forget", "check"])
        self.assertIn("project-zomboid", calls[1])
        self.assertIn("--host", calls[1])
        self.assertIn("paths,tags", calls[3])
        self.assertFalse((self.data / ".backup-lock").exists())

    def test_existing_lock_is_preserved(self):
        lock = self.data / ".backup-lock"
        lock.write_text("other-owner")
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(lock.read_text(), "other-owner")
        self.assertEqual(self.calls(), [])

    def test_storage_failure_never_sends_shutdown_or_initializes_repository(self):
        result = self.run_backup(BACKUP_MODE="running", FAIL_RESTIC_COMMAND="snapshots")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repository-preflight", result.stderr)
        self.assertEqual(self.calls(), [["snapshots"]])
        self.assertFalse((self.data / ".backup-lock").exists())

    def test_stopped_mode_refuses_running_game(self):
        (self.data / ".server-running").touch()
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [["snapshots"]])

    def test_empty_world_is_not_uploaded(self):
        (self.data / "Zomboid" / "Saves" / "Multiplayer" / "test").rmdir()
        (self.data / "Zomboid" / "Saves" / "Multiplayer").rmdir()
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [["snapshots"]])

    def test_integrity_failure_prevents_pruning(self):
        result = self.run_backup(FAIL_RESTIC_COMMAND="check")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("forget", [c[0] for c in self.calls()])

    def test_live_backup_saves_and_waits_for_game_exit(self):
        fifo = self.data / "pz-control"
        marker = self.data / ".server-running"
        os.mkfifo(fifo)
        marker.touch()
        descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, descriptor)
        commands = []
        stop = threading.Event()
        self.addCleanup(stop.set)

        def game():
            pending = b""
            while not stop.is_set():
                try:
                    pending += os.read(descriptor, 4096)
                except BlockingIOError:
                    time.sleep(0.01)
                    continue
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    commands.append(line.decode())
                    if line == b"quit":
                        marker.unlink()
                        return

        thread = threading.Thread(target=game, daemon=True)
        thread.start()
        try:
            result = self.run_backup(BACKUP_MODE="running")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(commands, ["save", "quit"])
            self.assertFalse(marker.exists())
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_unread_console_fifo_reports_timeout_and_does_not_upload(self):
        os.mkfifo(self.data / "pz-control")
        (self.data / ".server-running").touch()
        result = self.run_backup(BACKUP_MODE="running")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn("save-command", result.stderr)
        self.assertEqual(self.calls(), [["snapshots"]])
        self.assertFalse((self.data / ".backup-lock").exists())

if __name__ == "__main__":
    unittest.main()
