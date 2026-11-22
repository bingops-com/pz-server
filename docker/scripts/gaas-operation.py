#!/usr/bin/env python3
"""One idempotent, isolated game operation. Never logs credentials/game data."""
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile

DATA = Path(os.environ.get('PZ_DATA_DIR', '/data'))


def run(args, *, repository=None, extra_env=None):
    env = dict(os.environ, **(extra_env or {}))
    if repository:
        env['RESTIC_REPOSITORY'] = repository
    result = subprocess.run(args, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'Operation command failed: {Path(args[0]).name} (exit {result.returncode})')
    return result.stdout


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def check_world(root):
    worlds = root / 'Saves/Multiplayer'
    if not worlds.is_dir() or not any(worlds.iterdir()):
        raise ValueError('Restored multiplayer world is missing')
    expected_name = os.environ.get('GAAS_SERVER_NAME')
    if expected_name and not (worlds / expected_name).is_dir():
        raise ValueError('Restored world does not match the configured game server name')
    databases = list(root.rglob('*.db'))
    if not databases:
        raise ValueError('Restored player/account databases are missing')
    for database in databases:
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        try:
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Restored database integrity failed')
        finally:
            connection.close()


def steam_build(directory):
    manifest = directory / 'steamapps/appmanifest_380870.acf'
    match = re.search(r'"buildid"\s+"([0-9]+)"', manifest.read_text())
    if not match:
        raise ValueError('Steam installation manifest is invalid')
    return match.group(1)


def operate(action, identity, world_id):
    if (DATA / '.server-running').exists():
        raise ValueError('Refusing an offline operation while the server is running')
    DATA.mkdir(exist_ok=True)
    Path(os.environ.get('RESTIC_CACHE_DIR', '/tmp/restic-cache')).mkdir(parents=True, exist_ok=True)
    result = {'requestId': identity, 'action': action, 'worldId': world_id}
    if action == 'RestoreWorld':
        marker = DATA / 'Zomboid/.gaas-world.json'
        expected = {'worldId': world_id, 'repository': os.environ['RESTIC_REPOSITORY'],
                    'serverName': os.environ['GAAS_SERVER_NAME']}
        if marker.exists():
            if json.loads(marker.read_text()) != expected:
                raise ValueError('Persistent volume belongs to another world/repository')
        else:
            destination = DATA / 'Zomboid'
            if destination.exists() and any(destination.iterdir()):
                raise ValueError('Refusing to overwrite an unrecognized existing world')
            with tempfile.TemporaryDirectory(prefix='.gaas-world-', dir=DATA) as staging:
                run(['restic', 'restore', os.environ.get('RESTIC_SNAPSHOT', 'latest'),
                     '--target', staging, '--include', '/data/Zomboid', '--quiet'])
                candidate = Path(staging) / 'data/Zomboid'
                check_world(candidate)
                atomic_json(candidate / '.gaas-world.json', expected)
                if destination.exists():
                    destination.rmdir()
                candidate.rename(destination)
        check_world(DATA / 'Zomboid')
        result['worldReady'] = True
    elif action == 'InstallRelease':
        expected_build = os.environ['GAAS_STEAM_BUILD_ID']
        repository = os.environ['GAAS_BINARY_REPOSITORY']
        snapshot = os.environ['GAAS_BINARY_SNAPSHOT']
        if not re.fullmatch(r'[0-9a-f]{64}', snapshot):
            raise ValueError('Release snapshots must use their complete immutable identifier')
        marker = DATA / '.gaas-release.json'
        artifact = {'worldId': world_id, 'steamBuildId': expected_build,
                    'binaryRepository': repository, 'binarySnapshot': snapshot}
        if marker.exists():
            installed = json.loads(marker.read_text())
            if installed.get('worldId') != world_id:
                raise ValueError('Installed release belongs to another world')
            if all(installed.get(key) == value for key, value in artifact.items()):
                if steam_build(DATA / 'server') != expected_build:
                    raise ValueError('Installed binaries differ from their artifact receipt')
                result.update(artifact, version=os.environ['GAAS_VERSION'], artifactReused=True)
                atomic_json(marker, result)
                return result
        with tempfile.TemporaryDirectory(prefix='.gaas-release-', dir=DATA) as staging:
            run(['restic', 'restore', snapshot, '--target', staging,
                 '--include', '/data/server', '--quiet'], repository=repository)
            candidate = Path(staging) / 'data/server'
            if steam_build(candidate) != expected_build:
                raise ValueError('Binary artefact does not match the requested Steam build')
            current, previous = DATA / 'server', DATA / '.gaas-previous-server'
            if previous.exists() and current.exists():
                shutil.rmtree(previous)
            if current.exists():
                current.rename(previous)
            candidate.rename(current)
            result.update(artifact, version=os.environ['GAAS_VERSION'], artifactReused=False)
            atomic_json(marker, result)
            if previous.exists():
                shutil.rmtree(previous)
    elif action == 'BackupStoppedWorld':
        output = run(['/usr/local/bin/backup-server'], extra_env={'BACKUP_MODE': 'stopped'})
        matches = re.findall(r'snapshot ([0-9a-f]{8,64}) saved', output)
        if len(matches) != 1:
            raise ValueError('Backup did not return a unique snapshot receipt')
        snapshots = json.loads(run(['restic', 'snapshots', '--json', matches[0]]))
        if len(snapshots) != 1:
            raise ValueError('Backup snapshot is ambiguous')
        snapshot = snapshots[0]['id']
        with tempfile.TemporaryDirectory(prefix='.gaas-verify-', dir=DATA) as staging:
            run(['restic', 'restore', snapshot, '--target', staging,
                 '--include', '/data/Zomboid', '--quiet'])
            check_world(Path(staging) / 'data/Zomboid')
        result.update(snapshot=snapshot, verified=True)
        atomic_json(DATA / '.gaas-backup.json', result)
    else:
        raise ValueError('Unknown game operation')
    return result


if __name__ == '__main__':
    try:
        receipt = operate(os.environ['GAAS_ACTION'], os.environ['GAAS_REQUEST_ID'], os.environ['GAAS_WORLD_ID'])
    except (OSError, ValueError, RuntimeError, KeyError, sqlite3.Error):
        raise SystemExit('Game operation failed; world retained and no successful receipt emitted')
    print('GAAS_RESULT_JSON=' + json.dumps(receipt, sort_keys=True))
