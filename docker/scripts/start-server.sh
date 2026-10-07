#!/usr/bin/env bash
set -euo pipefail

: "${SERVER_NAME:?SERVER_NAME is required}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD is required}"
: "${SERVER_PASSWORD:?SERVER_PASSWORD is required}"
: "${RCON_PASSWORD:?RCON_PASSWORD is required}"

# The game keeps saves, accounts and configuration under its cache directory.
# It derives the default from the passwd home of its user (/home/pz), not from
# $HOME, so without -cachedir it writes inside the container: nothing reaches
# the volume, the files written below are ignored and backups stay empty.
cache_dir="${HOME}/Zomboid"
config_dir="${cache_dir}/Server"

while [[ -e /data/.backup-lock ]]; do
  lock_age=$(( $(date +%s) - $(stat -c %Y /data/.backup-lock) ))
  if (( lock_age > ${BACKUP_LOCK_MAX_AGE:-21600} )); then
    echo "Removing stale backup lock (${lock_age}s old)" >&2
    rm -f /data/.backup-lock
    break
  fi
  echo "Backup in progress; delaying server startup"
  sleep 10
done

mkdir -p "${config_dir}"
install -m 0644 /config/server.ini "${config_dir}/${SERVER_NAME}.ini"
install -m 0644 /config/SandboxVars.lua "${config_dir}/${SERVER_NAME}_SandboxVars.lua"

if [[ -s "${config_dir}/${SERVER_NAME}.ini" ]] && [[ -n "$(tail -c 1 "${config_dir}/${SERVER_NAME}.ini")" ]]; then
  printf '\n' >> "${config_dir}/${SERVER_NAME}.ini"
fi
sed -i '/^Password=/d;/^RCONPassword=/d' "${config_dir}/${SERVER_NAME}.ini"
printf 'Password=%s\nRCONPassword=%s\n' "${SERVER_PASSWORD}" "${RCON_PASSWORD}" >> "${config_dir}/${SERVER_NAME}.ini"

control_fifo=/data/pz-control
running_marker=/data/.server-running
rm -f "${control_fifo}"
mkfifo "${control_fifo}"
exec 3<>"${control_fifo}"

cleanup_runtime() {
  rm -f "${running_marker}"
}

trap cleanup_runtime EXIT

shutdown_server() {
  trap - TERM INT
  printf 'save\n' >&3 || true
  sleep "${SHUTDOWN_SAVE_SECONDS:-15}"
  printf 'quit\n' >&3 || true
  if [[ -n "${server_pid}" ]]; then
    wait "${server_pid}" || true
  fi
}

server_pid=""
trap shutdown_server TERM INT
"${SERVER_DIR}/start-server.sh" -cachedir="${cache_dir}" -servername "${SERVER_NAME}" -adminpassword "${ADMIN_PASSWORD}" <"${control_fifo}" &
server_pid=$!
touch "${running_marker}"
wait "${server_pid}"
