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

from gaas.lifecycle import request_id, validate_terraform_plan


def command(arguments: list[str], workdir: Path) -> str:
    result = subprocess.run(arguments, cwd=workdir, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Terraform {arguments[1]} failed (exit {result.returncode}); private diagnostics withheld")
    return result.stdout


def validate_backend(workdir: Path, namespace: str, suffix: str) -> None:
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
        if status.get("everStarted", True) and not (
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
            apply: bool = False) -> dict:
    validate_backend(workdir, state_namespace, state_suffix)
    if not enabled:
        require_suspended_inventory(inventory(context), gateway)
    previous_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="gaas-plan-") as directory:
            plan_path = str(Path(directory) / "gateway.tfplan")
            command(["terraform", "plan", "-input=false", "-lock-timeout=60s",
                     "-var=enabled=" + str(enabled).lower(), "-out=" + plan_path], workdir)
            private_plan = json.loads(command(["terraform", "show", "-json", plan_path], workdir))
            summary = validate_terraform_plan(private_plan, enabled=enabled, expected_name=expected_name)
            if apply:
                # Recheck immediately before apply. A new room waits for this
                # serialized transition; it cannot start against a deleting VM.
                if not enabled:
                    require_suspended_inventory(inventory(context), gateway)
                command(["terraform", "apply", "-input=false", "-lock-timeout=60s", plan_path], workdir)
            return {"applied": apply, "enabled": enabled, "changes": summary}
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
    args = parser.parse_args()
    try:
        result = execute(args.workdir, enabled=args.enabled == "true", expected_name=args.expected_name,
                         state_namespace=args.state_namespace, state_suffix=args.state_suffix,
                         gateway=args.gateway, context=args.context, apply=args.apply)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
        # Messages here deliberately contain no Terraform diagnostics or state.
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
