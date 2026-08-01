#!/usr/bin/env bash
# Superset workspace run — the Run button. Restartable from the UI.
#
# Thin wrapper: it assigns this workspace's ports and then hands off to
# scripts/dev-bootstrap.sh, which owns "start hal0 for local development".
# Keeping one owner means a change to the dev stack lands in one place
# (CONTRIBUTING rule 11 — find the owner before adding a parallel).
#
# Knobs:
#   HAL0_DEV_SKIP_OPENWEBUI=0   also start OpenWebUI (Docker) for this
#                               workspace. Default is 1 — one OpenWebUI
#                               container per concurrent workspace is a lot of
#                               memory for something most tasks don't touch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=ports.sh
source "${SCRIPT_DIR}/ports.sh"
cd "${HAL0_WORKSPACE_PATH}"

export HAL0_DEV_SKIP_OPENWEBUI="${HAL0_DEV_SKIP_OPENWEBUI:-1}"

# Recorded so teardown can stop a run that outlived its terminal. `exec` below
# keeps this PID, so the pidfile stays accurate for dev-bootstrap.sh too.
printf '%s\n' "$$" > "${SCRIPT_DIR}/run.pid"
trap 'rm -f "${SCRIPT_DIR}/run.pid"' EXIT

exec bash scripts/dev-bootstrap.sh
