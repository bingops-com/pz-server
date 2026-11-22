import copy
import unittest

from gaas.gateway import Gateway
from gaas.tests.test_controller import FakeKube, RELEASE


class GatewayRecoveryTests(unittest.TestCase):
    def test_restart_recreates_pending_job_with_original_inputs(self):
        kube = FakeKube()
        original = {'name': 'shared', 'approved': True, 'workerImage': RELEASE['runtimeImage'],
                    'credentialsSecretRef': 'original-secret', 'publicKeyConfigMap': 'original-key'}
        gateway = Gateway(kube, 'test-games', original)
        gateway.reconcile(True)
        job_name = gateway.state['job']
        old_job = kube.objects.pop(('job', 'test-games', job_name))
        changed = dict(original, workerImage='example.invalid/new@sha256:' + 'd' * 64,
                       credentialsSecretRef='new-secret', publicKeyConfigMap='new-key')
        restarted = Gateway(kube, 'test-games', changed)
        restarted.reconcile(False)
        recreated = kube.get('job', job_name, 'test-games')
        self.assertEqual(recreated['spec'], old_job['spec'])
        self.assertFalse(restarted.ready)

    def test_completed_old_enable_is_followed_by_requested_suspend(self):
        kube = FakeKube()
        settings = {'name': 'shared', 'approved': True, 'workerImage': RELEASE['runtimeImage'],
                    'credentialsSecretRef': 'cloud', 'publicKeyConfigMap': 'key'}
        gateway = Gateway(kube, 'test-games', settings)
        gateway.reconcile(True)
        kube.schedule()
        restarted = Gateway(kube, 'test-games', copy.deepcopy(settings))
        restarted.reconcile(False)
        self.assertIs(restarted.state['enabled'], True)
        self.assertIs(restarted.state['requested'], False)
        self.assertFalse(restarted.ready)
        job = kube.get('job', restarted.state['job'], 'test-games')
        env = {v['name']: v['value'] for v in job['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['GAAS_GATEWAY_ENABLED'], 'false')


if __name__ == '__main__':
    unittest.main()
