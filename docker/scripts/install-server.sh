#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${STEAM_EXPECTED_BUILD_ID:-}" ]]; then
  python3 - <<'PY'
import os
from pathlib import Path
import re
manifest = Path(os.environ['SERVER_DIR']) / 'steamapps/appmanifest_380870.acf'
if not manifest.is_file():
    raise SystemExit('Pinned game installation missing')
match = re.search(r'"buildid"\s+"([0-9]+)"', manifest.read_text())
if not match or match.group(1) != os.environ['STEAM_EXPECTED_BUILD_ID']:
    raise SystemExit('Installed Steam build differs from the requested release')
print('Pinned Steam installation verified; no moving-branch download')
PY
  exit 0
fi

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
