import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/gaas-operation.py'
spec = importlib.util.spec_from_file_location('gaas_operation', SOURCE)
operation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operation)


def world(root):
    (root / 'Saves/Multiplayer/bingops').mkdir(parents=True)
    (root / 'db').mkdir()
    connection = sqlite3.connect(root / 'db/bingops.db')
    connection.execute('create table players (id integer)')
    connection.commit()
    connection.close()


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(operation, 'DATA', self.root).start()
        patch.dict(os.environ, {'GAAS_SERVER_NAME': 'bingops', 'RESTIC_REPOSITORY': 'test-world',
                                 'RESTIC_CACHE_DIR': str(self.root / 'cache'), 'GAAS_STEAM_BUILD_ID': '200',
                                 'GAAS_BINARY_REPOSITORY': 'test-binaries', 'GAAS_BINARY_SNAPSHOT': 'b' * 64,
                                 'GAAS_VERSION': 'release-two'}).start()

    def restore_world(self, args, **_):
        target = Path(args[args.index('--target') + 1])
        world(target / 'data/Zomboid')
        return ''

    def test_restore_is_atomic_and_can_resume_same_world(self):
        with patch.object(operation, 'run', side_effect=self.restore_world) as run:
            result = operation.operate('RestoreWorld', 'request', 'logical-world')
            self.assertTrue(result['worldReady'])
            self.assertTrue((self.root / 'Zomboid/Saves/Multiplayer/bingops').exists())
            operation.operate('RestoreWorld', 'another-request', 'logical-world')
            self.assertEqual(run.call_count, 1)
            with self.assertRaises(ValueError):
                operation.operate('RestoreWorld', 'request', 'another-world')

    def test_interrupted_restore_does_not_leave_partial_destination(self):
        def interrupted(args, **kwargs):
            self.restore_world(args, **kwargs)
            raise RuntimeError('interrupted transport')
        with patch.object(operation, 'run', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                operation.operate('RestoreWorld', 'request', 'world')
        self.assertFalse((self.root / 'Zomboid').exists())
        with patch.object(operation, 'run', side_effect=self.restore_world):
            self.assertTrue(operation.operate('RestoreWorld', 'request', 'world')['worldReady'])

    def test_wrong_binary_build_keeps_previous_installation(self):
        server = self.root / 'server'
        server.mkdir()
        (server / 'old-installation').touch()
        def restore(args, **_):
            target = Path(args[args.index('--target') + 1]) / 'data/server/steamapps'
            target.mkdir(parents=True)
            (target / 'appmanifest_380870.acf').write_text('"buildid" "100"')
            return ''
        with patch.object(operation, 'run', side_effect=restore):
            with self.assertRaises(ValueError):
                operation.operate('InstallRelease', 'request', 'world')
        self.assertTrue((server / 'old-installation').exists())

    def test_correct_binary_install_never_overwrites_world(self):
        world(self.root / 'Zomboid')
        def restore(args, **_):
            target = Path(args[args.index('--target') + 1]) / 'data/server/steamapps'
            target.mkdir(parents=True)
            (target / 'appmanifest_380870.acf').write_text('"buildid" "200"')
            return ''
        with patch.object(operation, 'run', side_effect=restore):
            value = operation.operate('InstallRelease', 'request', 'world')
        self.assertEqual(value['steamBuildId'], '200')
        operation.check_world(self.root / 'Zomboid')

    def test_live_game_prevents_any_offline_operation(self):
        (self.root / '.server-running').touch()
        with patch.object(operation, 'run') as run:
            with self.assertRaises(ValueError):
                operation.operate('RestoreWorld', 'request', 'world')
            run.assert_not_called()

    def install_artifact(self, args, **_):
        target = Path(args[args.index('--target') + 1]) / 'data/server/steamapps'
        target.mkdir(parents=True)
        (target / 'appmanifest_380870.acf').write_text('"buildid" "200"')
        return ''

    def test_same_artifact_reuses_binaries_with_a_fresh_request_receipt(self):
        with patch.object(operation, 'run', side_effect=self.install_artifact) as run:
            self.assertFalse(operation.operate('InstallRelease', 'first', 'world')['artifactReused'])
            with patch.dict(os.environ, {'GAAS_VERSION': 'runtime-update'}):
                result = operation.operate('InstallRelease', 'second', 'world')
            self.assertTrue(result['artifactReused'])
            self.assertEqual(result['requestId'], 'second')
            self.assertEqual(result['version'], 'runtime-update')
            self.assertEqual(run.call_count, 1)
            self.assertEqual(json.loads((self.root / '.gaas-release.json').read_text()), result)

    def test_different_artifact_requires_a_new_restore(self):
        with patch.object(operation, 'run', side_effect=self.install_artifact) as run:
            operation.operate('InstallRelease', 'first', 'world')
            with patch.dict(os.environ, {'GAAS_BINARY_SNAPSHOT': 'c' * 64}):
                result = operation.operate('InstallRelease', 'second', 'world')
            self.assertFalse(result['artifactReused'])
            self.assertEqual(run.call_count, 2)

    def test_matching_receipt_cannot_hide_a_wrong_installed_build(self):
        with patch.object(operation, 'run', side_effect=self.install_artifact) as run:
            operation.operate('InstallRelease', 'first', 'world')
            (self.root / 'server/steamapps/appmanifest_380870.acf').write_text('"buildid" "100"')
            with self.assertRaises(ValueError):
                operation.operate('InstallRelease', 'second', 'world')
            self.assertEqual(run.call_count, 1)

    def test_configured_server_name_must_match_restored_world(self):
        world(self.root / 'Zomboid')
        with patch.dict(os.environ, {'GAAS_SERVER_NAME': 'wrong-name'}):
            with self.assertRaises(ValueError):
                operation.check_world(self.root / 'Zomboid')

    def test_corrupt_restored_database_is_rejected(self):
        world(self.root / 'Zomboid')
        (self.root / 'Zomboid/db/bingops.db').write_text('not a sqlite database')
        with self.assertRaises(sqlite3.DatabaseError):
            operation.check_world(self.root / 'Zomboid')


if __name__ == '__main__':
    unittest.main()
