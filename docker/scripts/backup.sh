#!/usr/bin/env bash
set -euo pipefail

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"
: "${RCON_PASSWORD:?RCON_PASSWORD is required}"

cleanup() {
  rm -f /data/.backup-lock
}

trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
touch /data/.backup-lock
pz-rcon --host "${RCON_HOST:-pz-server-rcon}" --port "${RCON_PORT:-27015}" --password "${RCON_PASSWORD}" save
sleep "${SAVE_SETTLE_SECONDS:-30}"
pz-rcon --host "${RCON_HOST:-pz-server-rcon}" --port "${RCON_PORT:-27015}" --password "${RCON_PASSWORD}" quit || true
sleep "${SHUTDOWN_SETTLE_SECONDS:-30}"

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
