"""Small kubectl boundary: credentials and raw API errors never enter logs."""
import json
import subprocess


class Kube:
    def __init__(self, context=None):
        self.prefix = ['kubectl'] + (['--context', context] if context else [])

    def call(self, args, value=None):
        result = subprocess.run(self.prefix + args, input=json.dumps(value) if value is not None else None,
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f'Kubernetes {args[0]} failed (exit {result.returncode}); diagnostics withheld')
        return result.stdout

    def get(self, kind, name, namespace):
        output = self.call(['get', kind, name, '-n', namespace, '--ignore-not-found', '-o', 'json'])
        return json.loads(output) if output.strip() else None

    def listing(self, kind, namespace=None, labels=None):
        args = ['get', kind] + (['-n', namespace] if namespace else ['--all-namespaces'])
        if labels:
            args += ['-l', labels]
        return json.loads(self.call(args + ['-o', 'json']))['items']

    def create(self, value):
        return json.loads(self.call(['create', '-f', '-', '-o', 'json'], value))

    def replace(self, value):
        return json.loads(self.call(['replace', '-f', '-', '-o', 'json'], value))

    def patch(self, kind, name, namespace, value, status=False):
        args = ['patch', kind, name, '-n', namespace, '--type=merge', '-p', json.dumps(value)]
        if status:
            args += ['--subresource=status']
        self.call(args)

    def delete(self, kind, name, namespace):
        self.call(['delete', kind, name, '-n', namespace, '--ignore-not-found', '--wait=false'])

    def logs(self, name, namespace):
        return self.call(['logs', '-n', namespace, 'job/' + name, '--tail=300'])


def job_state(job):
    for condition in (job or {}).get('status', {}).get('conditions', []):
        if condition.get('status') == 'True':
            if condition['type'] == 'Failed':
                return 'Failed'
            if condition['type'] == 'Complete':
                return 'Complete'
    return 'Pending'


def receipt(output, marker):
    values = [json.loads(line[len(marker):]) for line in output.splitlines() if line.startswith(marker)]
    if len(values) != 1:
        raise ValueError('Successful operation did not return a unique receipt')
    return values[0]
