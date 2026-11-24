import copy
import json
import unittest
from unittest.mock import patch

from gaas.gateway import Gateway
from gaas.lifecycle import validate_terraform_plan
from gaas.tests.test_controller import FakeKube, RELEASE
from gaas.tests.test_lifecycle import change


def settings(drain=60):
    return {'name': 'shared', 'approved': True, 'workerImage': RELEASE['runtimeImage'],
            'credentialsSecretRef': 'cloud', 'publicKeyConfigMap': 'key',
            'resourcePolicy': 'ephemeral', 'dnsDrainSeconds': drain,
            'variables': {'retain_gateway_resources': False, 'public_dns_records': ['pz']}}


class EphemeralGatewayTests(unittest.TestCase):
    def setUp(self):
        self.kube = FakeKube()
        self.config = settings()
        self.clock = patch('gaas.gateway.time.time', return_value=1000).start()
        self.addCleanup(patch.stopall)

    def gateway(self):
        return Gateway(self.kube, 'games', self.config)

    def complete(self, gateway, phase, enabled=False, address=None):
        name = gateway.state['job']
        self.kube.objects[('job', 'games', name)]['status'] = {'conditions': [{'type': 'Complete', 'status': 'True'}]}
        self.kube.output[('games', name)] = 'GAAS_TERRAFORM_RESULT_JSON=' + json.dumps({
            'applied': True, 'enabled': enabled, 'phase': phase, 'publicIPv4': address,
            'publicHostnames': ['pz.bingops.com'] if enabled else []})

    def withdraw(self):
        gateway = self.gateway()
        gateway.reconcile(False)
        self.complete(gateway, 'withdraw', address='192.0.2.10')
        gateway.reconcile(False)
        return gateway

    def test_withdraw_drains_before_releasing_and_survives_restart(self):
        gateway = self.withdraw()
        self.assertEqual(gateway.phase, 'DrainingDNS')
        self.assertFalse(gateway.ready)
        self.assertEqual(gateway.state['drainUntil'], 1060)
        self.clock.return_value = 1059
        restarted = self.gateway()
        restarted.reconcile(False)
        self.assertNotIn('job', restarted.state)
        self.clock.return_value = 1060
        restarted.reconcile(False)
        self.assertEqual(restarted.state['jobPhase'], 'release')
        self.complete(restarted, 'release')
        restarted.reconcile(False)
        self.assertTrue(restarted.state['released'])
        count = len(self.kube.objects)
        self.gateway().reconcile(False)
        self.assertEqual(len(self.kube.objects), count)

    def test_running_request_cancels_drain_without_releasing_held_ip(self):
        gateway = self.withdraw()
        gateway.reconcile(True)
        self.assertEqual(gateway.state['jobPhase'], 'steady')
        self.assertTrue(gateway.state['requested'])
        self.complete(gateway, 'steady', True, '192.0.2.10')
        with patch('gaas.gateway.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('192.0.2.10', 0))]):
            gateway.reconcile(True)
            self.assertTrue(gateway.ready)
        self.assertEqual(gateway.state['addressHoldUntil'], 1060)

    def test_ready_waits_for_new_dns_instead_of_accepting_stale_ip(self):
        gateway = self.gateway()
        gateway.reconcile(True)
        self.complete(gateway, 'steady', True, '192.0.2.10')
        gateway.reconcile(True)
        with patch('gaas.gateway.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('192.0.2.99', 0))]):
            self.assertFalse(gateway.ready)
        with patch('gaas.gateway.socket.getaddrinfo', side_effect=OSError('not resolved')):
            self.assertFalse(gateway.ready)

    def test_pending_withdraw_is_recreated_with_its_original_inputs(self):
        gateway = self.gateway()
        gateway.reconcile(False)
        name = gateway.state['job']
        original = self.kube.objects.pop(('job', 'games', name))
        self.config['dnsDrainSeconds'] = 300
        restarted = self.gateway()
        restarted.reconcile(False)
        self.assertEqual(self.kube.get('job', name, 'games')['spec'], original['spec'])

    def test_same_direction_failure_requires_explicit_retry(self):
        gateway = self.gateway()
        gateway.reconcile(False)
        self.kube.objects[('job', 'games', gateway.state['job'])]['status'] = {'conditions': [{'type': 'Failed', 'status': 'True'}]}
        with self.assertRaises(RuntimeError):
            gateway.reconcile(False)

    def test_cancelled_failed_release_can_recover_towards_running(self):
        gateway = self.withdraw()
        self.clock.return_value = 1060
        gateway.reconcile(False)
        self.kube.objects[('job', 'games', gateway.state['job'])]['status'] = {'conditions': [{'type': 'Failed', 'status': 'True'}]}
        gateway.reconcile(True)
        gateway.reconcile(True)
        self.assertTrue(gateway.state['requested'])
        self.assertEqual(gateway.state['jobPhase'], 'steady')

    def test_suspended_backup_does_not_allocate_compute(self):
        gateway = self.gateway()
        gateway.reconcile(True, activation_allowed=False)
        self.assertNotIn('job', gateway.state)

    def test_config_changes_when_fully_released_do_not_recreate_resources(self):
        gateway = self.withdraw()
        self.clock.return_value = 1060
        gateway.reconcile(False)
        self.complete(gateway, 'release')
        gateway.reconcile(False)
        count = len(self.kube.objects)
        self.config['dnsDrainSeconds'] = 300
        restarted = self.gateway()
        restarted.reconcile(False)
        self.assertEqual(len(self.kube.objects), count)
        self.assertNotIn('job', restarted.state)

    def test_retry_cannot_shorten_the_original_cache_protection(self):
        self.config['dnsDrainSeconds'] = 300
        gateway = self.gateway()
        gateway.reconcile(False)
        self.kube.objects[('job', 'games', gateway.state['job'])]['status'] = {'conditions': [{'type': 'Failed', 'status': 'True'}]}
        self.kube.output[('games', gateway.state['job'])] = 'GAAS_DNS_DRAIN_JSON={"seconds":300}'
        self.config['dnsDrainSeconds'] = 60
        restarted = self.gateway()
        restarted.reconcile(False)
        restarted.reconcile(False)
        self.complete(restarted, 'withdraw', address='192.0.2.10')
        restarted.reconcile(False)
        self.assertGreaterEqual(restarted.state['drainUntil'], 1300)

    def test_successful_release_receipt_must_not_keep_an_ip(self):
        gateway = self.withdraw()
        self.clock.return_value = 1060
        gateway.reconcile(False)
        self.complete(gateway, 'release', address='192.0.2.10')
        with self.assertRaises(ValueError):
            gateway.reconcile(False)

    def test_other_consumer_keeps_gateway_with_no_retirement_job(self):
        gateway = self.gateway()
        gateway.reconcile(True)
        self.complete(gateway, 'steady', True, '192.0.2.10')
        gateway.reconcile(True)
        count = len(self.kube.objects)
        gateway.reconcile(True)
        self.assertEqual(len(self.kube.objects), count)
        self.assertNotIn('job', gateway.state)

    def test_already_empty_backend_does_not_wait_or_create_resources(self):
        gateway = self.gateway()
        gateway.reconcile(False)
        self.complete(gateway, 'withdraw')
        name = gateway.state['job']
        value = json.loads(self.kube.output[('games', name)].split('=', 1)[1])
        value['resourcesReleased'] = True
        self.kube.output[('games', name)] = 'GAAS_TERRAFORM_RESULT_JSON=' + json.dumps(value)
        gateway.reconcile(False)
        self.assertTrue(gateway.state['released'])
        self.assertNotIn('job', gateway.state)
        self.assertNotIn('drainUntil', gateway.state)


class EphemeralPlanTests(unittest.TestCase):
    def validate(self, resource, phase='release', enabled=False):
        return validate_terraform_plan({'resource_changes': [resource]}, enabled=enabled,
                                       expected_name='edge', resource_policy='ephemeral', phase=phase, dns_names={'pz'})

    def test_exact_ip_is_releasable_only_after_withdrawal(self):
        value = change('hcloud_primary_ip.edge_gateway[0]', ['delete'], {'name': 'edge-ipv4', 'type': 'ipv4'})
        self.assertEqual(len(self.validate(value)), 1)
        with self.assertRaises(ValueError):
            self.validate(value, 'withdraw')
        with self.assertRaises(ValueError):
            self.validate(value, 'steady', True)

    def test_foreign_ip_name_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(change('hcloud_primary_ip.edge_gateway[0]', ['delete'], {'name': 'another-ipv4', 'type': 'ipv4'}))

    def test_only_owned_unproxied_a_records_can_be_withdrawn(self):
        value = change('cloudflare_record.public_services["pz"]', ['delete'], {'name': 'pz', 'type': 'A', 'proxied': False})
        self.assertEqual(len(self.validate(value, 'withdraw')), 1)
        for field, replacement in [('name', 'other'), ('type', 'CNAME'), ('proxied', True)]:
            candidate = copy.deepcopy(value)
            candidate['change']['before'][field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(candidate, 'withdraw')

    def test_retirement_never_creates_or_replaces_resources(self):
        for actions in [['create'], ['update'], ['delete', 'create']]:
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                self.validate(change('hcloud_primary_ip.edge_gateway[0]', actions, {'name': 'edge-ipv4', 'type': 'ipv4'}))

    def test_undeclared_dns_or_other_infrastructure_is_never_deleted(self):
        for address in ['cloudflare_record.public_services["other"]', 'hcloud_volume.world']:
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.validate(change(address, ['delete'], {'name': 'other', 'type': 'A'}))

    def test_ephemeral_inventory_and_phase_are_mandatory(self):
        with self.assertRaises(ValueError):
            validate_terraform_plan({}, enabled=False, expected_name='edge', resource_policy='ephemeral')


if __name__ == '__main__':
    unittest.main()
