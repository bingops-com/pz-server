#!/usr/bin/env bash
set -Eeuo pipefail

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"

data_dir=${PZ_DATA_DIR:-/data}
control_fifo=${CONTROL_FIFO:-${data_dir}/pz-control}
running_marker=${RUNNING_MARKER:-${data_dir}/.server-running}
backup_lock="${data_dir}/.backup-lock"
stage=lock

# Acquire exclusively. A failed/concurrent invocation must never remove another
# backup's lock or let the StatefulSet restart during that backup.
if ! (set -o noclobber; printf '%s\n' "${HOSTNAME:-backup}" > "${backup_lock}") 2>/dev/null; then
  echo "Another backup lock exists; refusing concurrent backup" >&2
  exit 1
fi
cleanup() { rm -f "${backup_lock}"; }
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'status=$?; printf "Backup failed during %s (exit=%s)\n" "$stage" "$status" >&2; exit "$status"' ERR

# Verify storage before interrupting the game. Initialize new repositories as a
# separate explicit provisioning operation; an auth/network failure is not an
# indication that the repository needs initialization.
stage=repository-preflight
mkdir -p "${RESTIC_CACHE_DIR:-/tmp/restic-cache}"
restic snapshots >/dev/null

case "${BACKUP_MODE:-running}" in
  running)
    if [[ ! -p "${control_fifo}" || ! -e "${running_marker}" ]]; then
      echo "Project Zomboid control channel is not available" >&2
      exit 1
    fi
    send_command() {
      local command=$1
      timeout "${CONTROL_TIMEOUT_SECONDS:-10}" bash -c 'printf "%s\n" "$1" > "$2"' -- "${command}" "${control_fifo}"
    }
    stage=save-command
    send_command save
    sleep "${SAVE_SETTLE_SECONDS:-30}"
    stage=quit-command
    send_command quit
    stage=waiting-for-shutdown
    shutdown_deadline=$(( $(date +%s) + ${SHUTDOWN_TIMEOUT_SECONDS:-120} ))
    while [[ -e "${running_marker}" ]] && (( $(date +%s) < shutdown_deadline )); do
      sleep 1
    done
    ;;
  stopped)
    # The lifecycle controller first scales the workload to zero and confirms
    # its Pod has terminated. This mode never sends commands to a live game.
    ;;
  *)
    echo "BACKUP_MODE must be running or stopped" >&2
    exit 1
    ;;
esac

if [[ -e "${running_marker}" ]]; then
  echo "Project Zomboid is still running; refusing inconsistent backup" >&2
  exit 1
fi
if [[ ! -d "${data_dir}/Zomboid/Saves/Multiplayer" ]]; then
  echo "Multiplayer world missing; refusing empty backup" >&2
  exit 1
fi

stage=upload
restic backup "${data_dir}/Zomboid" --tag project-zomboid --host "${RESTIC_HOST:-project-zomboid}"
stage=integrity-check
restic check --read-data-subset="${CHECK_SUBSET:-5%}"
stage=retention
# Kubernetes Job hostnames change every run. Grouping by hostname would retain
# every snapshot as a separate group and defeat the configured retention.
restic forget --tag project-zomboid --group-by paths,tags \
  --keep-daily "${KEEP_DAILY:-7}" \
  --keep-weekly "${KEEP_WEEKLY:-4}" \
  --keep-monthly "${KEEP_MONTHLY:-6}" \
  --prune
stage=post-prune-check
restic check
echo "Backup completed and checked"
