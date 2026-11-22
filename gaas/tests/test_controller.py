import copy
import json
from pathlib import Path
import unittest

from gaas.controller import Controller, RESOURCE
from gaas.lifecycle import FINALIZER
from gaas.runtime import rendered, UID_LABEL, WORLD_LABEL, names

CHART = Path(__file__).resolve().parents[2] / 'charts/pz-server'
RELEASE = {'runtimeImage': 'example.invalid/pz@sha256:' + 'a' * 64, 'steamBuildId': '100',
           'binarySnapshot': 'b' * 64, 'binaryRepository': 's3:https://example.invalid/releases',
           'operationProtocol': 1}


def room(name='one', offset=0):
    return {'apiVersion': 'gaming.bingops.com/v1alpha1', 'kind': 'GameRoom', 'metadata': {
        'name': name, 'namespace': 'test-games', 'uid': name + '-uid', 'generation': 1,
        'resourceVersion': '1', 'finalizers': [FINALIZER]}, 'spec': {
        'game': 'project-zomboid', 'version': 'release-one', 'state': 'Running', 'gatewayRef': 'shared',
        'serverCredentialsSecretRef': 'test-server-credentials',
        'world': {'id': name, 'serverName': 'bingops', 'repository': 's3:https://example.invalid/world',
                  'credentialsSecretRef': 'test-backup-credentials'},
        'network': {'targetIP': '192.0.2.10', 'nodePorts': {'game': 30761 + offset, 'direct': 30762 + offset},
                    'publicPorts': {'game': 17261 + offset, 'direct': 17262 + offset}}}}


def kind(value):
    return {'configmap': 'configmap', RESOURCE: 'gameroom', 'statefulset': 'statefulset',
            'persistentvolumeclaim': 'persistentvolumeclaim', 'pvc': 'persistentvolumeclaim'}.get(value.lower(), value.lower())


def merge(target, value):
    for key, item in value.items():
        if isinstance(item, dict):
            merge(target.setdefault(key, {}), item)
        else:
            target[key] = copy.deepcopy(item)


class FakeKube:
    def __init__(self):
        self.objects, self.output, self.deleted = {}, {}, []

    def get(self, resource, name, namespace):
        return copy.deepcopy(self.objects.get((kind(resource), namespace, name)))

    def create(self, obj):
        obj = copy.deepcopy(obj)
        metadata = obj['metadata']
        metadata.setdefault('generation', 1)
        metadata.setdefault('resourceVersion', '1')
        key = (kind(obj['kind']), metadata['namespace'], metadata['name'])
        if key in self.objects:
            raise RuntimeError('Already exists')
        self.objects[key] = obj
        return copy.deepcopy(obj)

    def replace(self, obj):
        key = (kind(obj['kind']), obj['metadata']['namespace'], obj['metadata']['name'])
        self.objects[key] = copy.deepcopy(obj)

    def patch(self, resource, name, namespace, value, status=False):
        obj = self.objects[(kind(resource), namespace, name)]
        merge(obj, value)
        obj['metadata']['resourceVersion'] = str(int(obj['metadata']['resourceVersion']) + 1)
        if 'spec' in value:
            obj['metadata']['generation'] += 1

    def listing(self, resource, namespace=None, labels=None):
        result = [copy.deepcopy(obj) for (k, ns, _), obj in self.objects.items()
                  if k == kind(resource) and (namespace is None or namespace == ns)]
        if labels:
            key, value = labels.split('=', 1)
            result = [obj for obj in result if obj['metadata'].get('labels', {}).get(key) == value]
        return result

    def logs(self, name, namespace):
        return self.output[(namespace, name)]

    def schedule(self):
        # Simulates Kubernetes/provider completion, not real network or game IO.
        self.objects = {key: obj for key, obj in self.objects.items() if key[0] != 'pods'}
        for key, obj in list(self.objects.items()):
            if key[0] == 'statefulset':
                obj['status'] = {'observedGeneration': obj['metadata']['generation'],
                                 'readyReplicas': obj['spec']['replicas']}
                if obj['spec']['replicas']:
                    self.objects[('pods', key[1], key[2] + '-0')] = {
                        'kind': 'Pod', 'metadata': {'name': key[2] + '-0', 'namespace': key[1],
                                                   'labels': obj['spec']['template']['metadata']['labels']},
                        'spec': {'containers': [{'name': 'pz-server'}]}}
            if key[0] != 'job' or obj.get('status'):
                continue
            container = obj['spec']['template']['spec']['containers'][0]
            env = {item['name']: item['value'] for item in container.get('env', [])}
            if 'GAAS_ACTION' in env:
                value = {'requestId': env['GAAS_REQUEST_ID'], 'action': env['GAAS_ACTION'],
                         'worldId': env['GAAS_WORLD_ID']}
                if env['GAAS_ACTION'] == 'RestoreWorld':
                    value['worldReady'] = True
                elif env['GAAS_ACTION'] == 'InstallRelease':
                    value.update(version=env['GAAS_VERSION'], steamBuildId=env['GAAS_STEAM_BUILD_ID'])
                else:
                    value.update(snapshot='c' * 64, verified=True)
                self.output[(key[1], key[2])] = 'GAAS_RESULT_JSON=' + json.dumps(value)
            else:
                self.output[(key[1], key[2])] = 'GAAS_TERRAFORM_RESULT_JSON=' + json.dumps({
                    'applied': True, 'enabled': env['GAAS_GATEWAY_ENABLED'] == 'true', 'changes': []})
            obj['status'] = {'conditions': [{'type': 'Complete', 'status': 'True'}]}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.kube = FakeKube()
        settings = {'name': 'shared', 'approved': True, 'workerImage': RELEASE['runtimeImage'],
                    'credentialsSecretRef': 'test-cloud', 'publicKeyConfigMap': 'test-public-key',
                    'variables': {'forwards': {}}}
        for offset in (0, 10):
            for i in (1, 2):
                settings['variables']['forwards'][f'{offset}-{i}'] = {
                    'protocol': 'udp', 'listen_port': 17260 + offset + i,
                    'target_port': 30760 + offset + i, 'target_ip': '192.0.2.10'}
        for name, field, value in (('gaas-release-catalog', 'releases.json', {'release-one': RELEASE}),
                                   ('gaas-gateway', 'config.json', settings)):
            self.kube.create({'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': 'test-games'},
                              'data': {field: json.dumps(value)}})
        self.controller = Controller(self.kube, 'test-games', CHART, ['test-games'], apply=True)

    def rounds(self, count=10):
        reports = []
        for _ in range(count):
            reports = self.controller.tick()
            self.kube.schedule()
        return reports

    def test_shared_gateway_full_simulated_cycle(self):
        one, two = room(), room('two', 10)
        self.kube.create(one)
        self.kube.create(two)
        self.rounds()
        for name in ('one', 'two'):
            self.assertEqual(self.kube.get(RESOURCE, name, 'test-games')['status']['phase'], 'Running')
        self.kube.patch(RESOURCE, 'one', 'test-games', {'spec': {'state': 'Suspended'}})
        self.rounds()
        state = json.loads(self.kube.get('configmap', 'gaas-gateway-state-shared', 'test-games')['data']['state.json'])
        self.assertTrue(state['enabled'])
        self.kube.patch(RESOURCE, 'two', 'test-games', {'spec': {'state': 'Suspended'}})
        self.rounds()
        state = json.loads(self.kube.get('configmap', 'gaas-gateway-state-shared', 'test-games')['data']['state.json'])
        self.assertFalse(state['enabled'])
        self.kube.patch(RESOURCE, 'one', 'test-games', {'spec': {'state': 'Running'}})
        self.rounds()
        self.assertEqual(self.kube.get(RESOURCE, 'one', 'test-games')['status']['phase'], 'Running')
        self.assertIsNotNone(self.kube.get('pvc', names(one)['volume'], 'test-games'))

    def test_world_volume_has_no_garbage_collection_owner(self):
        objects = rendered(room(), RELEASE, CHART)
        pvc = next(obj for obj in objects if obj['kind'] == 'PersistentVolumeClaim')
        self.assertNotIn('ownerReferences', pvc['metadata'])
        self.assertEqual(pvc['metadata']['labels'][WORLD_LABEL], 'one')
        self.assertTrue(all(obj['spec']['replicas'] == 0 for obj in objects if obj['kind'] == 'StatefulSet'))

    def test_existing_workload_is_not_adopted(self):
        obj = next(obj for obj in rendered(room(), RELEASE, CHART) if obj['kind'] == 'StatefulSet')
        obj['metadata']['labels'][UID_LABEL] = 'another-owner'
        self.kube.create(obj)
        self.kube.create(room())
        reports = self.rounds(1)
        self.assertTrue(any(report.get('phase') == 'Blocked' for report in reports))

    def test_dry_run_has_no_mutations(self):
        self.kube.create(room())
        before = copy.deepcopy(self.kube.objects)
        self.controller.apply = False
        self.controller.tick()
        self.assertEqual(before, self.kube.objects)

    def test_game_name_is_preserved_when_restoring_a_copy(self):
        objects = rendered(room(), RELEASE, CHART)
        server = next(obj for obj in objects if obj['kind'] == 'StatefulSet')
        env = {item['name']: item.get('value') for item in server['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['SERVER_NAME'], 'bingops')


if __name__ == '__main__':
    unittest.main()
