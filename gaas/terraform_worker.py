"""Run one reviewed gateway transition with a locked, migrated remote state.

Terraform stdout, plans, state and provider diagnostics may contain credentials.
Keep them private; expose only the validated change summary and public outputs.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import re
import ipaddress

from gaas.lifecycle import request_id, validate_terraform_plan


def validate_state_identity(namespace: str, suffix: str) -> None:
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', namespace):
        raise ValueError('Invalid state namespace')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', suffix) or re.search(r'-[0-9]+$', suffix):
        raise ValueError('State suffix must be a DNS label and must not end in a numeric segment')


def command(arguments: list[str], workdir: Path) -> str:
    result = subprocess.run(arguments, cwd=workdir, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Terraform {arguments[1]} failed (exit {result.returncode}); private diagnostics withheld")
    return result.stdout


def validate_backend(workdir: Path, namespace: str, suffix: str) -> None:
    validate_state_identity(namespace, suffix)
    metadata = workdir / ".terraform" / "terraform.tfstate"
    if not metadata.is_file():
        raise ValueError("Initialize the migrated Kubernetes backend before execution")
    backend = json.loads(metadata.read_text()).get("backend", {})
    config = backend.get("config", {})
    if backend.get("type") != "kubernetes":
        raise ValueError("Gateway execution requires the Kubernetes backend and Lease locking")
    if config.get("namespace") != namespace or config.get("secret_suffix") != suffix:
        raise ValueError("Unexpected Terraform state namespace or suffix")
    if (workdir / "terraform.tfstate").exists():
        raise ValueError("Remove the retired local state from the execution directory after verified migration")


def require_suspended_inventory(document: dict, gateway: str = "shared") -> None:
    if not isinstance(document.get("items"), list) or document.get("kind") not in {"List", "GameRoomList"}:
        raise ValueError("A complete Kubernetes GameRoom List is required")
    for room in document["items"]:
        metadata, spec, status = room["metadata"], room["spec"], room.get("status", {})
        if spec.get("gatewayRef", "shared") != gateway:
            continue
        if spec.get("state", "Running") == "Running" and not metadata.get("deletionTimestamp"):
            raise ValueError("A GameRoom still needs the shared gateway")
        if status.get("observedGeneration") != metadata.get("generation"):
            raise ValueError("GameRoom reconciliation is not current")
        if status.get("phase") not in {"Suspended", "Finalized"}:
            raise ValueError("GameRoom shutdown is not complete")
        if (status.get("worldReady") or status.get("everStarted", True)) and not (
            status.get("backup", {}).get("verified") is True
            and status.get("backup", {}).get("snapshot")
            and status.get("backup", {}).get("requestId") == request_id(
                room, "delete" if metadata.get("deletionTimestamp") else "suspend")
        ):
            raise ValueError("A verified shutdown backup is missing")


def inventory(context: str | None = None) -> dict:
    args = ["kubectl"]
    if context:
        args += ["--context", context]
    args += ["get", "gamerooms.gaming.bingops.com", "--all-namespaces", "-o", "json"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError("Cannot list GameRooms; refusing gateway suspension")
    return json.loads(result.stdout)


def execute(workdir: Path, *, enabled: bool, expected_name: str, state_namespace: str,
            state_suffix: str, gateway: str = "shared", context: str | None = None,
            apply: bool = False, resource_policy: str = 'retained', phase: str = 'steady',
            dns_names: set[str] | None = None) -> dict:
    validate_backend(workdir, state_namespace, state_suffix)
    if resource_policy not in {'retained', 'ephemeral'} or phase not in {'steady', 'withdraw', 'release'}:
        raise ValueError('Invalid resource policy or gateway transition')
    if resource_policy == 'retained' and phase != 'steady':
        raise ValueError('Retained gateways use only the steady transition')
    if resource_policy == 'ephemeral' and (dns_names is None or (enabled and phase != 'steady') or (not enabled and phase == 'steady')):
        raise ValueError('An explicit ephemeral transition and DNS inventory are required')
    if not enabled:
        require_suspended_inventory(inventory(context), gateway)
    previous_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="gaas-plan-") as directory:
            plan_path = str(Path(directory) / "gateway.tfplan")
            variables = ['-var=enabled=' + str(enabled).lower()]
            empty_backend = False
            if resource_policy == 'ephemeral' and phase == 'withdraw':
                state = json.loads(command(['terraform', 'state', 'pull'], workdir))
                empty_backend = not any(r.get('instances') for r in state.get('resources', []) if r.get('mode') == 'managed')
            if resource_policy == 'ephemeral':
                variables += ['-var=retain_gateway_resources=' + str(phase == 'withdraw' and not empty_backend).lower(),
                              '-var=publish_dns=' + str(enabled).lower()]
            command(["terraform", "plan", "-input=false", "-lock-timeout=60s", *variables, "-out=" + plan_path], workdir)
            private_plan = json.loads(command(["terraform", "show", "-json", plan_path], workdir))
            summary = validate_terraform_plan(private_plan, enabled=enabled, expected_name=expected_name,
                                              resource_policy=resource_policy, phase=phase, dns_names=dns_names)
            ttl = max([0] + [300 if attrs.get('ttl', 1) == 1 else int(attrs.get('ttl', 300) or 300)
                             for r in private_plan.get('resource_changes', []) if r['address'].startswith('cloudflare_record.public_services[')
                             for attrs in [r['change'].get('before'), r['change'].get('after')] if attrs])
            if apply:
                # Recheck immediately before apply. A new room waits for this
                # serialized transition; it cannot start against a deleting VM.
                if not enabled:
                    require_suspended_inventory(inventory(context), gateway)
                if resource_policy == 'ephemeral' and phase == 'withdraw':
                    # This bounded marker survives a crash during apply. A retry
                    # must remember the TTL even if the DNS was already removed.
                    print('GAAS_DNS_DRAIN_JSON=' + json.dumps({'seconds': ttl}), flush=True)
                command(["terraform", "apply", "-input=false", "-lock-timeout=60s", plan_path], workdir)
            result = {"applied": apply, "enabled": enabled, "changes": summary,
                      'resourcePolicy': resource_policy, 'phase': phase, 'dnsCacheSeconds': ttl,
                      'resourcesReleased': phase == 'release' or empty_backend}
            if apply:
                outputs = json.loads(command(['terraform', 'output', '-json'], workdir))
                address = outputs.get('public_ipv4', {}).get('value')
                if address is not None:
                    address = str(ipaddress.IPv4Address(address))
                if enabled and resource_policy == 'ephemeral' and address is None:
                    raise ValueError('Enabled ephemeral gateway has no public IPv4 receipt')
                result.update(publicIPv4=address, publicHostnames=outputs.get('public_hostnames', {}).get('value', []))
            return result
    finally:
        os.umask(previous_umask)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--state-namespace", default="gaas-state")
    parser.add_argument("--state-suffix", required=True)
    parser.add_argument("--gateway", default="shared")
    parser.add_argument("--expected-name", required=True)
    parser.add_argument("--context")
    parser.add_argument("--enabled", choices=("true", "false"), required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument('--resource-policy', choices=['retained', 'ephemeral'], default='retained')
    parser.add_argument('--phase', choices=['steady', 'withdraw', 'release'], default='steady')
    parser.add_argument('--dns-name', action='append')
    args = parser.parse_args()
    try:
        result = execute(args.workdir, enabled=args.enabled == "true", expected_name=args.expected_name,
                         state_namespace=args.state_namespace, state_suffix=args.state_suffix,
                         gateway=args.gateway, context=args.context, apply=args.apply,
                         resource_policy=args.resource_policy, phase=args.phase,
                         dns_names=set(args.dns_name) if args.dns_name is not None else None)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
        # Messages here deliberately contain no Terraform diagnostics or state.
        parser.exit(1, str(error) + "\n")
    print('GAAS_TERRAFORM_RESULT_JSON=' + json.dumps(result))


if __name__ == "__main__":
    main()
