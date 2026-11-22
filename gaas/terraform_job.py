"""Bootstrap a pinned gateway module inside one isolated Terraform Job."""
import json
import os
from pathlib import Path
import re
import subprocess

from gaas.terraform_worker import command, execute


def main():
    config = json.loads(os.environ['GAAS_GATEWAY_CONFIG'])
    if not re.fullmatch(r'[0-9a-f]{40}', config['sourceRevision']):
        raise ValueError('Terraform source must use an immutable commit SHA')
    if config['sourceRepository'] != 'https://github.com/bingops-com/labops.git':
        raise ValueError('Unexpected infrastructure source repository')
    if config['modulePath'] != 'terraform/edge-gateway':
        raise ValueError('Unexpected Terraform module path')
    if not config.get('approved'):
        raise ValueError('Gateway execution is not enabled')
    test = config['expectedName'].startswith('gaas-test-')
    if not test and not config.get('productionAdoptionApproved'):
        raise ValueError('Existing gateway adoption has not been approved')
    os.umask(0o077)
    root = Path('/work/source')
    root.mkdir(parents=True, exist_ok=True)
    # All subprocess output remains private: provider diagnostics can contain
    # credentials even when the module's outputs are marked non-sensitive.
    for args in (['git', 'init', '-q'], ['git', 'fetch', '--depth=1', config['sourceRepository'], config['sourceRevision']],
                 ['git', 'checkout', '--detach', 'FETCH_HEAD']):
        r = subprocess.run(args, cwd=root, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError('Cannot fetch the pinned infrastructure source')
    workdir = root / config['modulePath']
    namespace, suffix = config['stateNamespace'], config['stateSuffix']
    if config.get('allowNewState'):
        if not test:
            raise ValueError('Empty-state bootstrap is permitted only for a named test gateway')
    else:
        result = subprocess.run(['kubectl', 'get', 'secret', 'tfstate-default-' + suffix, '-n', namespace,
                                 '--ignore-not-found', '-o', 'jsonpath={.metadata.name}'], capture_output=True, text=True)
        if result.returncode or not result.stdout.strip():
            raise ValueError('Migrated state is missing; refusing empty-state adoption')
    backend = {'terraform': {'backend': {'kubernetes': {
        'namespace': namespace, 'secret_suffix': suffix, 'in_cluster_config': True}}}}
    (workdir / 'backend.tf.json').write_text(json.dumps(backend))
    variables = dict(config['variables'])
    if variables.get('name') != config['expectedName']:
        raise ValueError('Expected gateway name and module variables differ')
    variables['ssh_public_key_path'] = '/config/operator.pub'
    (workdir / 'zz-gaas.auto.tfvars.json').write_text(json.dumps(variables))
    command(['terraform', 'init', '-input=false', '-lockfile=readonly'], workdir)
    result = execute(workdir, enabled=os.environ['GAAS_GATEWAY_ENABLED'] == 'true',
                     expected_name=config['expectedName'], state_namespace=namespace,
                     state_suffix=suffix, gateway=config.get('name', 'shared'), apply=True)
    print('GAAS_TERRAFORM_RESULT_JSON=' + json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyError, OSError):
        raise SystemExit('Gateway operation failed; private diagnostics withheld')
