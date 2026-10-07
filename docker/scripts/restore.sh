#!/usr/bin/env bash
set -euo pipefail

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"

destination=/data/Zomboid
if [[ -d "${destination}" ]] && find "${destination}" -mindepth 1 -print -quit | grep -q .; then
  echo "Refusing to restore into non-empty ${destination}" >&2
  exit 1
fi

restic restore "${RESTIC_SNAPSHOT:-latest}" --target / --include /data/Zomboid
