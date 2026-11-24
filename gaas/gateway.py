"""Serialize gateway jobs; remember applied state across controller restarts."""
import hashlib
import json
import re
import socket
import time

from gaas.kube import job_state, receipt


class Gateway:
    def __init__(self, kube, namespace, config):
        self.kube, self.namespace, self.config = kube, namespace, config
        if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', config.get('workerImage', '')):
            raise ValueError('Gateway worker image must be pinned by digest')
        self.state_name = 'gaas-gateway-state-' + config.get('name', 'shared')
        value = kube.get('configmap', self.state_name, namespace)
        self.state = json.loads(value['data']['state.json']) if value else {'enabled': None, 'serial': 0}
        if config.get('resourcePolicy', 'retained') not in {'retained', 'ephemeral'}:
            raise ValueError('Unknown gateway resource policy')
        seconds = config.get('dnsDrainSeconds', 300)
        if config.get('resourcePolicy') == 'ephemeral' and (config.get('variables', {}).get('retain_gateway_resources') is not False or type(seconds) is not int or not 60 <= seconds <= 86400):
            raise ValueError('Ephemeral resources and a safe DNS drain must be explicit')
        self.digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    @property
    def ephemeral(self):
        return self.config.get('resourcePolicy', 'retained') == 'ephemeral'

    @property
    def phase(self):
        if self.state.get('job'):
            return {'withdraw': 'WithdrawingDNS', 'release': 'ReleasingResources'}.get(self.state.get('jobPhase'), 'PreparingGateway')
        if self.state.get('drainUntil'):
            return 'DrainingDNS'
        if self.state.get('enabled'):
            return 'Ready' if self.ready else 'WaitingForDNS'
        return 'Released' if self.state.get('released') else 'Suspended'

    @property
    def ready(self):
        if not (self.state.get('enabled') is True and not self.state.get('job') and self.state.get('configDigest') == self.digest):
            return False
        if not self.ephemeral:
            return True
        address, hosts = self.state.get('publicIPv4'), self.state.get('publicHostnames')
        if not address or not hosts:
            return False
        try:
            return all(address in {item[4][0] for item in socket.getaddrinfo(host, None, socket.AF_INET)} for host in hosts)
        except OSError:
            return False

    def save(self):
        data = {'state.json': json.dumps(self.state)}
        if self.kube.get('configmap', self.state_name, self.namespace):
            self.kube.patch('configmap', self.state_name, self.namespace, {'data': data})
        else:
            self.kube.create({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
                'name': self.state_name, 'namespace': self.namespace,
                'labels': {'app.kubernetes.io/managed-by': 'gaas-operator'}}, 'data': data})

    def reconcile(self, required, activation_allowed=True):
        if not self.config.get('approved'):
            return
        active = self.state.get('job')
        if active:
            job = self.kube.get('job', active, self.namespace)
            if job is None:
                self.kube.create(self.job(active, self.state['requested'], self.state.get('jobConfig', self.config), self.state.get('jobPhase', 'steady')))
                return
            status = job_state(job)
            if status == 'Failed':
                if self.state.get('requestedDigest') == self.digest and self.state.get('requested') == required:
                    raise RuntimeError('Gateway job failed; existing gateway state retained')
                # A changed desired direction can recover a cancelled transition;
                # same-direction failures still need an explicit config retry.
                self.state.pop('job', None)
                if self.state.get('jobPhase') == 'withdraw':
                    lines = self.kube.logs(active, self.namespace).splitlines()
                    bounds = [json.loads(line.split('=', 1)[1])['seconds'] for line in lines if line.startswith('GAAS_DNS_DRAIN_JSON=')]
                    if len(bounds) > 1 or any(type(v) is not int or not 0 <= v <= 86400 for v in bounds):
                        raise ValueError('Invalid cached-DNS bound from failed withdrawal')
                    self.state['drainFloor'] = max([self.state.get('drainFloor', 0), self.state.get('jobConfig', {}).get('dnsDrainSeconds', 300), *bounds])
                self.state.pop('jobConfig', None)
                self.state['serial'] += 1
                self.save()
                return
            if status != 'Complete':
                return
            value = receipt(self.kube.logs(active, self.namespace), 'GAAS_TERRAFORM_RESULT_JSON=')
            phase = self.state.get('jobPhase', 'steady')
            if (value.get('applied') is not True or value.get('enabled') != self.state['requested']
                    or (phase != 'steady' and value.get('phase') != phase)):
                raise ValueError('Gateway worker receipt mismatch')
            if phase == 'release' and (value.get('publicIPv4') is not None or value.get('publicHostnames')):
                raise ValueError('Release receipt still contains an endpoint')
            original = self.state.get('jobConfig', self.config)
            released = phase == 'release' or (phase == 'withdraw' and value.get('resourcesReleased') is True)
            if released and (value.get('publicIPv4') is not None or value.get('publicHostnames')):
                raise ValueError('Empty-backend receipt still contains an endpoint')
            self.state.update(enabled=value['enabled'], serial=self.state['serial'] + 1,
                              configDigest=self.state['requestedDigest'], released=released,
                              publicIPv4=value.get('publicIPv4'), publicHostnames=value.get('publicHostnames', []))
            self.state['drainFloor'] = max(self.state.get('drainFloor', 0), value.get('dnsCacheSeconds', 0))
            if phase == 'withdraw' and not released:
                deadline = time.time() + max(original.get('dnsDrainSeconds', 300), self.state.get('drainFloor', 0))
                self.state['addressHoldUntil'] = max(deadline, self.state.get('addressHoldUntil', 0))
                self.state['drainUntil'] = self.state['addressHoldUntil']
            else:
                self.state.pop('drainUntil', None)
                if released:
                    self.state.pop('addressHoldUntil', None)
                    self.state.pop('drainFloor', None)
            self.state.pop('job', None)
            self.state.pop('jobConfig', None)
            self.save()
        # A backup for a changed suspended object must retain existing infra,
        # but never allocate a new VM when no room actually requests Running.
        if required and not activation_allowed and not self.state.get('enabled'):
            return
        if self.ephemeral and not required:
            if self.state.get('released'):
                if self.state.get('configDigest') != self.digest:
                    self.state['configDigest'] = self.digest
                    self.save()
                return
            if self.state.get('drainUntil'):
                if time.time() < self.state['drainUntil']:
                    return
                phase = 'release'
            else:
                phase = 'withdraw'
        else:
            phase = 'steady'
            if self.state.get('enabled') is None and not required:
                return
            if self.state.get('enabled') == required and self.state.get('configDigest') == self.digest:
                return
        name = 'gaas-edge-' + hashlib.sha256((self.digest + str(required) + phase + str(self.state['serial'])).encode()).hexdigest()[:24]
        if phase == 'withdraw':
            self.state['drainFloor'] = max(self.state.get('drainFloor', 0), self.config.get('dnsDrainSeconds', 300))
        self.state.update(job=name, requested=required, requestedDigest=self.digest, jobConfig=self.config, jobPhase=phase)
        self.save()
        if not self.kube.get('job', name, self.namespace):
            self.kube.create(self.job(name, required, phase=phase))

    def job(self, name, enabled, config=None, phase='steady'):
        config = config or self.config
        return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {
            'name': name, 'namespace': self.namespace,
            'labels': {'app.kubernetes.io/managed-by': 'gaas-operator', 'gaming.bingops.com/gateway': config.get('name', 'shared')}},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 1800, 'template': {'spec': {
                'restartPolicy': 'Never', 'serviceAccountName': 'gaas-terraform',
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000,
                                    'fsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [{'name': 'terraform', 'image': config['workerImage'],
                    'command': ['python3', '-m', 'gaas.terraform_job'],
                    'env': [{'name': 'GAAS_GATEWAY_CONFIG', 'value': json.dumps(config)},
                            {'name': 'GAAS_GATEWAY_ENABLED', 'value': str(enabled).lower()},
                            {'name': 'TAILSCALE_TAILNET', 'value': '-'},
                            {'name': 'GAAS_GATEWAY_PHASE', 'value': phase}],
                    'envFrom': [{'secretRef': {'name': config['credentialsSecretRef']}}],
                    'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'memory': '1Gi'}},
                    'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                    'volumeMounts': [{'name': 'work', 'mountPath': '/work'},
                                     {'name': 'operator-key', 'mountPath': '/config', 'readOnly': True}]}],
                'volumes': [{'name': 'work', 'emptyDir': {}}, {'name': 'operator-key', 'configMap': {
                    'name': config['publicKeyConfigMap']}}]}}}}
