"""Pure reconciliation decisions, independent of Kubernetes and cloud tokens.

The runtime adapter must build observations from live resources and completed
Jobs. These decisions are not a substitute for backup/health verification.
"""
from dataclasses import dataclass
import hashlib
import re

FINALIZER = "gaming.bingops.com/preserve-world"


def release_valid(release):
    return bool(isinstance(release, dict)
                and re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', release.get('runtimeImage', ''))
                and re.fullmatch(r'[0-9]+', release.get('steamBuildId', ''))
                and re.fullmatch(r'[0-9a-f]{64}', release.get('binarySnapshot', ''))
                and release.get('binaryRepository') and release.get('operationProtocol') == 1)


@dataclass(frozen=True)
class Decision:
    phase: str
    operation: str | None
    request_id: str
    needs_gateway: bool


def request_id(room: dict, purpose: str) -> str:
    metadata = room["metadata"]
    if not metadata.get("uid"):
        raise ValueError("A Kubernetes UID is required for lifecycle operations")
    value = ":".join((metadata["uid"], str(metadata["generation"]), purpose))
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def decide(room: dict, observed: dict, releases: dict) -> Decision:
    """Return at most one operation. Failed observations never authorize deletion.

    A backup receipt is accepted only for this UID, generation and operation.
    This prevents an older nightly backup from authorizing an upgrade/deletion.
    """
    spec = room["spec"]
    metadata = room["metadata"]
    target = spec["version"]
    desired = spec.get("state", "Running")
    if desired not in {"Running", "Suspended"}:
        raise ValueError("Unsupported desired state")
    deleting = bool(metadata.get("deletionTimestamp"))
    stopping = deleting or desired == "Suspended"
    purpose = "delete" if deleting else "suspend" if stopping else "release:" + target
    identity = request_id(room, purpose)
    running = observed.get("running", False)
    world_ready = observed.get("worldReady", False)

    def result(phase, operation=None, holds=True):
        return Decision(phase, operation, identity, holds)

    if observed.get('activeOperationCount', 0) > 1:
        return result('Blocked')
    if observed.get('activeOperation'):
        if observed.get('activeOperationIsCurrent'):
            phase = {'RestoreWorld': 'Restoring', 'InstallRelease': 'Updating',
                     'BackupStoppedWorld': 'BackingUp'}[observed['activeOperation']]
            return result(phase)
        return result('WaitingForOperation')

    if observed.get("error"):
        return result("Blocked", holds=bool(running or world_ready))

    # Invalid releases must not interrupt an existing game. Shutdown/deletion
    # remains possible even if a newer requested release was invalid.
    if not stopping:
        release = releases.get(target)
        if not release_valid(release) or spec.get("game") != "project-zomboid":
            return result("Blocked", holds=bool(running))

    changing = not stopping and observed.get("version") != target
    receipt = observed.get("backup", {})
    backed_up = (
        receipt.get("requestId") == identity
        and receipt.get("verified") is True
        and bool(receipt.get("snapshot"))
    )

    # A recreated GameRoom can find an existing world without its previous
    # status.version. Treat that world as valuable data before any install.
    if stopping or (changing and world_ready):
        if observed.get("scheduledBackupActive"):
            return result("WaitingForBackup")
        if not observed.get("scheduledBackupSuspended"):
            return result("Stopping", "SuspendScheduledBackup")
        if running or observed.get("podExists"):
            return result("Stopping", "Stop")
        if world_ready and not backed_up:
            return result("BackingUp", "BackupStoppedWorld")
        if stopping:
            return result("Finalized" if deleting else "Suspended", holds=False)

    if not world_ready:
        return result("Restoring", "RestoreWorld")
    if changing:
        if observed.get("installedReleaseRequest") != identity:
            return result("Updating", "InstallRelease")
    if not observed.get("gatewayReady"):
        return result("WaitingForGateway", "EnsureGateway")
    if not running:
        return result("Starting", "Start")
    if not observed.get("ready") or observed.get("version") != target:
        return result("Starting", "VerifyRelease")
    if observed.get("scheduledBackupSuspended"):
        return result("Starting", "ResumeScheduledBackup")
    return result("Running")


def shared_gateway_required(decisions: list[Decision], inventory_complete: bool) -> bool:
    # A failed/partial list must never turn into a "zero consumers" decision.
    if not inventory_complete:
        raise ValueError("Complete GameRoom inventory required before gateway reconciliation")
    return any(decision.needs_gateway for decision in decisions)


def validate_terraform_plan(plan: dict, *, enabled: bool, expected_name: str,
                            resource_policy: str = "retained", phase: str = "steady",
                            dns_names: set[str] | None = None) -> list[dict]:
    """Validate a private terraform-show JSON value; never return secret values.

    Automatic VM replacement (e.g. a forwarding change) is intentionally
    rejected, because it would interrupt every consumer of the shared gateway.
    Unrelated resources, IPv4/DNS deletion and all destroy-all plans are rejected.
    """
    if resource_policy not in {'retained', 'ephemeral'} or phase not in {'steady', 'withdraw', 'release'}:
        raise ValueError('Unknown gateway resource policy or transition')
    if resource_policy == 'retained' and phase != 'steady':
        raise ValueError('Retained resources cannot use a disposable transition')
    if resource_policy == 'ephemeral' and (dns_names is None or (enabled and phase != 'steady')
                                         or (not enabled and phase == 'steady')):
        raise ValueError('Ephemeral transitions require an explicit phase and owned DNS inventory')
    bases = {'hcloud_primary_ip.edge_gateway', 'hcloud_firewall.edge_gateway',
             'hcloud_ssh_key.operator', 'terraform_data.bootstrap_revision'}
    allowed = bases | {name + '[0]' for name in bases} | {
        'hcloud_server.edge_gateway[0]', 'tailscale_tailnet_key.edge_gateway[0]'}
    compute = {'hcloud_server.edge_gateway[0]', 'tailscale_tailnet_key.edge_gateway[0]'}
    names = {'hcloud_server': expected_name, 'hcloud_primary_ip': expected_name + '-ipv4',
             'hcloud_firewall': expected_name + '-firewall', 'hcloud_ssh_key': expected_name + '-operator'}
    summary = []
    for resource in plan.get('resource_changes', []):
        if resource.get('mode') == 'data':
            continue
        address, change = resource['address'], resource['change']
        actions = change['actions']
        record = re.fullmatch(r'cloudflare_record\.public_services\["([^"\\]+)"\]', address)
        if address not in allowed and not record:
            raise ValueError('Terraform plan contains an unrelated resource')
        if record and dns_names is not None and record.group(1) not in dns_names:
            raise ValueError('DNS record is outside the declared gateway names')
        if resource_policy == 'ephemeral' and phase == 'release' and actions not in (['delete'], ['no-op']):
            raise ValueError('Final release must not create or update resources')
        if resource_policy == 'ephemeral' and phase == 'withdraw' and 'create' in actions and not address.startswith('terraform_data.bootstrap_revision'):
            raise ValueError('DNS withdrawal must not allocate cloud infrastructure')
        if 'delete' in actions:
            marker = address in {'terraform_data.bootstrap_revision', 'terraform_data.bootstrap_revision[0]'}
            metadata_only = not enabled and marker and phase != 'release' and actions in (['delete', 'create'], ['create', 'delete'])
            removable = address in compute
            if resource_policy == 'ephemeral':
                removable = removable or bool(record) or (phase == 'release' and address in allowed)
            if not metadata_only and (enabled or not removable or actions != ['delete']):
                raise ValueError('Unsafe deletion or shared-gateway replacement')
        for attributes in (change.get('before'), change.get('after')):
            if not attributes:
                continue
            kind = resource.get('type', address.split('.')[0])
            if kind in names and (resource_policy == 'ephemeral' or kind == 'hcloud_server' or 'name' in attributes):
                if attributes.get('name') != names[kind]:
                    raise ValueError('Unexpected resource name in gateway plan')
            if resource_policy == 'ephemeral' and kind == 'hcloud_primary_ip' and attributes.get('type') != 'ipv4':
                raise ValueError('Only the owned primary IPv4 is disposable')
            if resource_policy == 'ephemeral' and record:
                valid_names = {record.group(1), record.group(1) + '.bingops.com'}
                if attributes.get('name') not in valid_names or attributes.get('type') != 'A' or attributes.get('proxied'):
                    raise ValueError('Unexpected disposable DNS record')
        if not enabled and resource.get('type') == 'hcloud_server' and 'create' in actions:
            raise ValueError('Suspension must not create a gateway')
        summary.append({'address': address, 'actions': actions})
    return summary
