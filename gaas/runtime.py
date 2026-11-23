"""Project Zomboid Kubernetes adapter. Refuses adoption of existing workloads."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import yaml

from gaas.kube import job_state, receipt
from gaas.lifecycle import release_valid as image_valid

UID_LABEL = 'gaming.bingops.com/room-uid'
WORLD_LABEL = 'gaming.bingops.com/world-id'


def names(room):
    uid = room['metadata']['uid']
    release = 'gaas-' + room['metadata']['name'][:24] + '-' + uid[:8]
    world = room['spec']['world']['id']
    volume = 'gaas-world-' + world[:32] + '-' + hashlib.sha256(world.encode()).hexdigest()[:8]
    return {'release': release, 'server': release + '-pz-server', 'volume': volume}


def rendered(room, release, chart):
    if not image_valid(release):
        raise ValueError('Release catalogue entry is incomplete or mutable')
    network = room['spec']['network']
    ports = network['nodePorts']
    if ports['game'] == ports['direct'] or not all(30000 <= ports[p] <= 32767 for p in ('game', 'direct')):
        raise ValueError('Game NodePorts must be distinct and within the cluster range')
    repository = room['spec']['world']['repository']
    if not repository.startswith('s3:https://'):
        raise ValueError('Only private S3-compatible HTTPS repositories are supported')
    url = urlsplit(repository[3:])
    if url.username or url.password or url.query or url.fragment:
        raise ValueError('Repository URLs must not contain credentials or query parameters')
    parts = url.path.lstrip('/').split('/', 1)
    values = copy.deepcopy(release.get('chartValues', {}))
    values['restore'] = {'enabled': False}
    values['server'] = dict(values.get('server', {}), name=room['spec']['world']['serverName'],
                            secret={'existingSecret': room['spec']['serverCredentialsSecretRef']})
    values['service'] = {'type': 'NodePort', 'nodePorts': ports}
    values['backup'] = {'enabled': True, 'endpoint': 'https://' + url.netloc, 'bucket': parts[0],
                        'prefix': parts[1] if len(parts) > 1 else '',
                        'secret': {'existingSecret': room['spec']['world']['credentialsSecretRef']}}
    n = names(room)
    process = subprocess.run(['helm', 'template', n['release'], str(chart), '-f', '-'],
                             input=yaml.safe_dump(values), capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError('Game chart rendering failed; diagnostics withheld')
    objects = [obj for obj in yaml.safe_load_all(process.stdout) if obj]
    for obj in objects:
        obj['metadata']['namespace'] = room['metadata']['namespace']
        obj['metadata'].setdefault('labels', {})[UID_LABEL] = room['metadata']['uid']
        if obj['kind'] == 'PersistentVolumeClaim':
            obj['metadata']['name'] = n['volume']
            obj['metadata']['labels'][WORLD_LABEL] = room['spec']['world']['id']
            # World data survives deletion/recreation of the GameRoom.
            obj['metadata'].pop('ownerReferences', None)
        else:
            obj['metadata']['ownerReferences'] = [{
                'apiVersion': room['apiVersion'], 'kind': 'GameRoom',
                'name': room['metadata']['name'], 'uid': room['metadata']['uid'],
                'controller': True, 'blockOwnerDeletion': True}]
        if obj['kind'] == 'StatefulSet':
            obj['spec']['replicas'] = 0
            template = obj['spec']['template']
        elif obj['kind'] == 'CronJob':
            obj['spec']['suspend'] = True
            template = obj['spec']['jobTemplate']['spec']['template']
        else:
            continue
        template['metadata'].setdefault('labels', {})[UID_LABEL] = room['metadata']['uid']
        pod = template['spec']
        for container in pod.get('containers', []) + pod.get('initContainers', []):
            container['image'] = release['runtimeImage']
            if container['name'] == 'install-server':
                container['env'] = [env for env in container.get('env', []) if env['name'] != 'STEAM_EXPECTED_BUILD_ID']
                container['env'].append({'name': 'STEAM_EXPECTED_BUILD_ID', 'value': release['steamBuildId']})
        for volume in pod.get('volumes', []):
            if 'persistentVolumeClaim' in volume:
                volume['persistentVolumeClaim']['claimName'] = n['volume']
    return objects


class Zomboid:
    def __init__(self, kube, chart):
        self.kube, self.chart = kube, Path(chart)

    def ensure(self, room, release):
        objects = rendered(room, release, self.chart)
        # Service allocation is checked before creating a StatefulSet.
        order = {'PersistentVolumeClaim': 0, 'ConfigMap': 1, 'Service': 2, 'CronJob': 3, 'StatefulSet': 4}
        for obj in sorted(objects, key=lambda obj: order.get(obj['kind'], 10)):
            meta = obj['metadata']
            existing = self.kube.get(obj['kind'], meta['name'], meta['namespace'])
            if existing:
                if obj['kind'] == 'PersistentVolumeClaim':
                    if existing['metadata'].get('labels', {}).get(WORLD_LABEL) != room['spec']['world']['id']:
                        raise ValueError('Existing PVC belongs to another world')
                elif existing['metadata'].get('labels', {}).get(UID_LABEL) != room['metadata']['uid']:
                    raise ValueError('Refusing to adopt a workload owned by another controller')
            else:
                self.kube.create(obj)

    def job_name(self, room, action, identity):
        return 'gaas-' + room['metadata']['uid'][:8] + '-' + action.lower()[:12] + '-' + identity[:16]

    def active_jobs(self, room):
        # Generation changes do not cancel a Pod writing to the retained PVC.
        # Serialize against all offline operations owned by this room UID.
        jobs = self.kube.listing('job', room['metadata']['namespace'], UID_LABEL + '=' + room['metadata']['uid'])
        active = []
        for job in jobs:
            if job_state(job) != 'Pending':
                continue
            for container in job['spec']['template']['spec']['containers']:
                action = next((e.get('value') for e in container.get('env', []) if e['name'] == 'GAAS_ACTION'), None)
                if action in ('RestoreWorld', 'InstallRelease', 'BackupStoppedWorld'):
                    active.append((job, action))
        return active

    def observe(self, room, releases, identity, gateway_ready):
        namespace = room['metadata']['namespace']
        n = names(room)
        server = self.kube.get('statefulset', n['server'], namespace)
        cron = self.kube.get('cronjob', n['server'] + '-backup', namespace)
        if server and server['metadata'].get('labels', {}).get(UID_LABEL) != room['metadata']['uid']:
            raise ValueError('Workload ownership mismatch')
        pods = self.kube.listing('pods', namespace, UID_LABEL + '=' + room['metadata']['uid'])
        game_pods = [p for p in pods if any(c['name'] == 'pz-server' for c in p['spec']['containers'])]
        status = room.get('status', {})
        observed = {'worldReady': status.get('worldReady', False), 'version': status.get('version'),
                    'activeRelease': status.get('activeRelease'),
                    'backup': status.get('backup', {}), 'running': bool(server and server['spec']['replicas']),
                    'podExists': bool(game_pods), 'gatewayReady': gateway_ready,
                    'scheduledBackupSuspended': cron is None or cron['spec'].get('suspend', False),
                    'scheduledBackupActive': bool(cron and cron.get('status', {}).get('active')),
                    'ready': bool(server and server.get('status', {}).get('readyReplicas') == 1)}
        active = self.active_jobs(room)
        observed['activeOperationCount'] = len(active)
        if len(active) == 1:
            job, action = active[0]
            observed.update(activeOperation=action, activeOperationIsCurrent=(
                job['metadata']['name'] == self.job_name(room, action, identity)))
        # Readiness must correspond to the current StatefulSet generation.
        if server and server.get('status', {}).get('observedGeneration') != server['metadata']['generation']:
            observed['ready'] = False
        for action in ('RestoreWorld', 'InstallRelease', 'BackupStoppedWorld'):
            job = self.kube.get('job', self.job_name(room, action, identity), namespace)
            state = job_state(job)
            if state == 'Failed':
                observed['error'] = action + ' failed'
            elif state == 'Complete':
                value = receipt(self.kube.logs(job['metadata']['name'], namespace), 'GAAS_RESULT_JSON=')
                if value.get('requestId') != identity or value.get('action') != action or value.get('worldId') != room['spec']['world']['id']:
                    raise ValueError('Game operation receipt identity mismatch')
                if action == 'RestoreWorld':
                    observed['worldReady'] = value['worldReady']
                elif action == 'InstallRelease':
                    if value.get('version') != room['spec']['version']:
                        raise ValueError('Installed release differs from the requested version')
                    observed.update(version=value['version'], installedReleaseRequest=identity)
                else:
                    observed['backup'] = value
        return observed

    def perform(self, room, observed, decision, releases):
        namespace, action = room['metadata']['namespace'], decision.operation
        n = names(room)
        if action in ('SuspendScheduledBackup', 'ResumeScheduledBackup'):
            cron = self.kube.get('cronjob', n['server'] + '-backup', namespace)
            if cron:
                self.kube.patch('cronjob', n['server'] + '-backup', namespace,
                                {'spec': {'suspend': action == 'SuspendScheduledBackup'}})
            return
        if action == 'Stop':
            self.kube.patch('statefulset', n['server'], namespace, {'spec': {'replicas': 0}})
            return
        if action in ('Start', 'VerifyRelease'):
            if action == 'Start':
                if self.active_jobs(room):
                    raise ValueError('An offline operation is still using the world volume')
                objects = rendered(room, releases[room['spec']['version']], self.chart)
                for obj in objects:
                    if obj['kind'] == 'ConfigMap':
                        self.kube.patch('configmap', obj['metadata']['name'], namespace, {'data': obj['data']})
                    elif obj['kind'] == 'CronJob':
                        self.kube.patch('cronjob', obj['metadata']['name'], namespace, {'spec': obj['spec']})
                desired = next(obj for obj in objects if obj['kind'] == 'StatefulSet')
                self.kube.patch('statefulset', n['server'], namespace,
                                {'spec': {'replicas': 1, 'template': desired['spec']['template']}})
            return
        if action == 'EnsureGateway' or action is None:
            return
        if action not in ('RestoreWorld', 'InstallRelease', 'BackupStoppedWorld'):
            raise ValueError('Unsupported runtime action')
        job_name = self.job_name(room, action, decision.request_id)
        if self.kube.get('job', job_name, namespace):
            return
        if self.active_jobs(room):
            raise ValueError('An earlier offline operation is still using the world volume')
        if observed.get('podExists') or observed.get('running'):
            raise ValueError('Offline operations require a terminated game Pod')
        version = (observed.get('version') or room['spec']['version']) if action == 'BackupStoppedWorld' else room['spec']['version']
        release = releases.get(version) or (observed.get('activeRelease') if action == 'BackupStoppedWorld' else None)
        if not image_valid(release):
            raise ValueError('Runtime release for this operation is unavailable')
        world = room['spec']['world']
        env = { 'GAAS_ACTION': action, 'GAAS_REQUEST_ID': decision.request_id, 'GAAS_WORLD_ID': world['id'],
                'GAAS_SERVER_NAME': world['serverName'],
                'GAAS_VERSION': room['spec']['version'], 'GAAS_STEAM_BUILD_ID': release['steamBuildId'],
                'GAAS_BINARY_REPOSITORY': release['binaryRepository'], 'GAAS_BINARY_SNAPSHOT': release['binarySnapshot'],
                'RESTIC_REPOSITORY': world['repository'], 'RESTIC_SNAPSHOT': world.get('restoreSnapshot', 'latest'),
                'RESTIC_CACHE_DIR': '/tmp/restic-cache', 'AWS_DEFAULT_REGION': 'auto'}
        obj = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {
            'name': job_name, 'namespace': namespace, 'labels': {UID_LABEL: room['metadata']['uid']},
            'ownerReferences': [{'apiVersion': room['apiVersion'], 'kind': 'GameRoom', 'name': room['metadata']['name'],
                                 'uid': room['metadata']['uid'], 'controller': True, 'blockOwnerDeletion': True}]},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 3600, 'template': {'spec': {
                'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000,
                                    'fsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [{'name': 'operation', 'image': release['runtimeImage'],
                    'command': ['/usr/local/bin/gaas-operation'],
                    'env': [{'name': key, 'value': value} for key, value in env.items()],
                    'envFrom': [{'secretRef': {'name': world['credentialsSecretRef']}}],
                    'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'memory': '2Gi'}},
                    'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                    'volumeMounts': [{'name': 'data', 'mountPath': '/data'}]}],
                'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': n['volume']}}]}}}}
        self.kube.create(obj)
