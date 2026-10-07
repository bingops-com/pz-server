#!/usr/bin/env bash
set -euo pipefail

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"
cleanup() {
  rm -f /data/.backup-lock
}

trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
touch /data/.backup-lock

control_fifo=${CONTROL_FIFO:-/data/pz-control}
running_marker=${RUNNING_MARKER:-/data/.server-running}

if [[ ! -p "${control_fifo}" || ! -e "${running_marker}" ]]; then
  echo "Project Zomboid control channel is not available" >&2
  exit 1
fi

send_command() {
  local command=$1
  timeout "${CONTROL_TIMEOUT_SECONDS:-10}" bash -c 'printf "%s\n" "$1" > "$2"' -- "${command}" "${control_fifo}"
}

send_command save
sleep "${SAVE_SETTLE_SECONDS:-30}"
send_command quit

shutdown_deadline=$(( $(date +%s) + ${SHUTDOWN_TIMEOUT_SECONDS:-120} ))
while [[ -e "${running_marker}" ]] && (( $(date +%s) < shutdown_deadline )); do
  sleep 2
done

if [[ -e "${running_marker}" ]]; then
  echo "Project Zomboid did not stop before the backup deadline" >&2
  exit 1
fi

if ! restic snapshots >/dev/null 2>&1; then
  restic init
fi

restic backup /data/Zomboid --tag project-zomboid
restic forget \
  --keep-daily "${KEEP_DAILY:-7}" \
  --keep-weekly "${KEEP_WEEKLY:-4}" \
  --keep-monthly "${KEEP_MONTHLY:-6}" \
  --prune
restic check --read-data-subset="${CHECK_SUBSET:-5%}"
