#!/usr/bin/env bash
set -euo pipefail

mkdir -p "${SERVER_DIR}"

steam_args=(
  +force_install_dir "${SERVER_DIR}"
  +login anonymous
  +app_update 380870
)

if [[ -n "${STEAM_BETA:-}" ]]; then
  steam_args+=(-beta "${STEAM_BETA}")
fi

if [[ "${STEAM_VALIDATE:-false}" == "true" ]]; then
  steam_args+=(validate)
fi

steam_args+=(+quit)
"${STEAMCMD_DIR}/steamcmd.sh" "${steam_args[@]}"
