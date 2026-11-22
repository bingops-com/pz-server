"""Serialize gateway jobs; remember applied state across controller restarts."""
import hashlib
import json
import re

from gaas.kube import job_state, receipt


class Gateway:
    def __init__(self, kube, namespace, config):
        self.kube, self.namespace, self.config = kube, namespace, config
        if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', config.get('workerImage', '')):
            raise ValueError('Gateway worker image must be pinned by digest')
        self.state_name = 'gaas-gateway-state-' + config.get('name', 'shared')
        value = kube.get('configmap', self.state_name, namespace)
        self.state = json.loads(value['data']['state.json']) if value else {'enabled': None, 'serial': 0}
        self.digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    @property
    def ready(self):
        return self.state.get('enabled') is True and not self.state.get('job') and self.state.get('configDigest') == self.digest

    def save(self):
        data = {'state.json': json.dumps(self.state)}
        if self.kube.get('configmap', self.state_name, self.namespace):
            self.kube.patch('configmap', self.state_name, self.namespace, {'data': data})
        else:
            self.kube.create({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
                'name': self.state_name, 'namespace': self.namespace,
                'labels': {'app.kubernetes.io/managed-by': 'gaas-operator'}}, 'data': data})

    def reconcile(self, required):
        if not self.config.get('approved'):
            return
        active = self.state.get('job')
        if active:
            job = self.kube.get('job', active, self.namespace)
            if job is None:
                self.kube.create(self.job(active, self.state['requested'], self.state.get('jobConfig', self.config)))
                return
            state = job_state(job)
            if state == 'Failed':
                if self.state.get('requestedDigest') == self.digest:
                    raise RuntimeError('Gateway job failed; existing gateway state retained')
                # An explicit configuration/retryNonce change authorizes a new
                # guarded attempt, never blind retries against a failed plan.
                self.state.pop('job', None)
                self.state['serial'] += 1
                self.save()
                return
            if state != 'Complete':
                return
            value = receipt(self.kube.logs(active, self.namespace), 'GAAS_TERRAFORM_RESULT_JSON=')
            if value.get('applied') is not True or value.get('enabled') != self.state['requested']:
                raise ValueError('Gateway worker receipt mismatch')
            self.state.update(enabled=value['enabled'], serial=self.state['serial'] + 1,
                              configDigest=self.state['requestedDigest'])
            self.state.pop('job', None)
            self.state.pop('jobConfig', None)
            self.save()
        if self.state.get('enabled') is None and not required:
            return
        if self.state.get('enabled') == required and self.state.get('configDigest') == self.digest:
            return
        name = 'gaas-edge-' + hashlib.sha256((self.digest + str(required) + str(self.state['serial'])).encode()).hexdigest()[:24]
        self.state.update(job=name, requested=required, requestedDigest=self.digest, jobConfig=self.config)
        self.save()
        # A controller restart between save/create reuses this exact Job name.
        if not self.kube.get('job', name, self.namespace):
            self.kube.create(self.job(name, required))

    def job(self, name, enabled, config=None):
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
                            {'name': 'TAILSCALE_TAILNET', 'value': '-'}],
                    'envFrom': [{'secretRef': {'name': config['credentialsSecretRef']}}],
                    'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'memory': '1Gi'}},
                    'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                    'volumeMounts': [{'name': 'work', 'mountPath': '/work'},
                                     {'name': 'operator-key', 'mountPath': '/config', 'readOnly': True}]}],
                'volumes': [{'name': 'work', 'emptyDir': {}}, {'name': 'operator-key', 'configMap': {
                    'name': config['publicKeyConfigMap']}}]}}}}
