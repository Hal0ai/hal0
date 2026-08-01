#!/usr/bin/env bash
# hal0 dev-bootstrap — start hal0 services for local development
#
# Run from the repo root on hal0ai (or any dev machine):
#   bash scripts/dev-bootstrap.sh
#
# Starts:
#   - hal0-api via 'hal0 serve --reload' (or uvicorn directly if hal0 CLI
#     isn't built yet) on HAL0_PORT (default 8080)
#   - OpenWebUI via Docker on HAL0_OPENWEBUI_PORT (default 3001)
#   - UI dev server via 'cd ui && npm run dev' on port 5173
#
# Does NOT require root. Uses $REPO_ROOT/hal0-home as the data root so
# nothing touches system paths.
#
# Set HAL0_DEV_SKIP_OPENWEBUI=1 to leave OpenWebUI (and its Docker
# requirement) out — the default for Superset workspaces, where several
# worktrees run at once and one container each is more memory than most tasks
# need. See .superset/run.sh.

set -euo pipefail
IFS=$'\n\t'

# ── Colour helpers ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
    BOLD='\033[1m'; RESET='\033[0m'; DIM='\033[2m'
else
    GREEN=''; YELLOW=''; RED=''; BOLD=''; RESET=''; DIM=''
fi

info()  { printf "${GREEN}✔${RESET}  %s\n" "$*"; }
warn()  { printf "${YELLOW}!${RESET}  %s\n" "$*" >&2; }
step()  { printf "\n${BOLD}── %s${RESET}\n" "$*"; }
die()   { printf "${RED}✗${RESET}  %s\n" "$*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Dev environment ───────────────────────────────────────────────────────────
export HAL0_HOME="${HAL0_HOME:-${REPO_ROOT}/hal0-home}"
export HAL0_PORT="${HAL0_PORT:-8080}"
export HAL0_OPENWEBUI_PORT="${HAL0_OPENWEBUI_PORT:-3001}"
UI_PORT="${UI_PORT:-5173}"
SKIP_OWU="${HAL0_DEV_SKIP_OPENWEBUI:-0}"

# One container name per checkout. A constant name meant a second worktree's
# startup `docker stop` killed the first worktree's OpenWebUI, and both then
# fought over the same --env-file. .superset/ports.sh derives this from the
# workspace path; standalone runs keep the historical name.
OWU_CONTAINER="${HAL0_OWU_CONTAINER:-hal0-openwebui-dev}"

ETC_DIR="${HAL0_HOME}/etc/hal0"
VAR_DIR="${HAL0_HOME}/var/lib/hal0"
LOG_DIR="${HAL0_HOME}/var/log/hal0"

# ── Pre-flight ────────────────────────────────────────────────────────────────
step "Pre-flight"

if [[ "${SKIP_OWU}" == "1" ]]; then
    info "OpenWebUI disabled (HAL0_DEV_SKIP_OPENWEBUI=1) — Docker not required"
else
    if ! command -v docker &>/dev/null; then
        die "docker not found. Install Docker and re-run, or set HAL0_DEV_SKIP_OPENWEBUI=1."
    fi
    if ! docker info &>/dev/null 2>&1; then
        die "Docker daemon not accessible. Run: systemctl start docker"
    fi
    info "Docker OK"
fi

# Check Python venv / hal0 package
PYTHON_CMD=""
if [[ -f "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON_CMD="${REPO_ROOT}/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
else
    die "No Python found. Create a venv: python3 -m venv .venv && pip install -e .[dev]"
fi
info "Python: ${PYTHON_CMD}"

# Check UI tooling
UI_AVAILABLE=0
if [[ -d "${REPO_ROOT}/ui" ]] && command -v npm &>/dev/null; then
    UI_AVAILABLE=1
    info "UI: npm found"
else
    warn "UI: npm not found or ui/ dir missing — skipping UI dev server"
fi

# ── Layout + config ───────────────────────────────────────────────────────────
step "Dev layout"

# Use install.sh in dev mode to set up directories + config without
# touching system paths or enabling systemd units.
bash "${REPO_ROOT}/installer/install.sh" --dev

info "Dev layout ready at ${HAL0_HOME}"

# ── Cleanup on exit ───────────────────────────────────────────────────────────
API_PID=""
UI_PID=""

# Job control, so each background service becomes its own process-group leader
# and `kill -- -$pid` reaches the whole tree. Without it, killing $! only
# reaped the wrapper: `npm run dev` forks node, so the Vite server survived
# shutdown and kept holding UI_PORT — the next run then found the port busy
# (or, worse, reused a server running the previous branch's code).
set -m

# TERM the service's whole process group, then KILL whatever ignored it.
# Falls back to a bare pid kill if the group is already gone.
stop_tree() {
    local pid="$1"
    [[ -n "${pid}" ]] || return 0
    kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "${pid}" 2>/dev/null || return 0
        sleep 0.3
    done
    kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "${pid}" 2>/dev/null || true
}

cleanup() {
    printf '\n%sShutting down dev services...%s\n' "${YELLOW}" "${RESET}"
    stop_tree "${API_PID}"
    stop_tree "${UI_PID}"
    [[ "${SKIP_OWU}" != "1" ]] && docker stop "${OWU_CONTAINER}" 2>/dev/null || true
    printf '%sDone.%s\n' "${GREEN}" "${RESET}"
}
trap cleanup EXIT INT TERM

# ── Start hal0 API ────────────────────────────────────────────────────────────
step "Starting hal0 API"

mkdir -p "${LOG_DIR}"
API_LOG="${LOG_DIR}/api.log"

# Prefer hal0 serve if available, fall back to uvicorn for Phase 0
UVICORN_CMD=""
if "${PYTHON_CMD}" -c "import hal0" &>/dev/null 2>&1; then
    if [[ -f "${REPO_ROOT}/.venv/bin/hal0" ]]; then
        UVICORN_CMD="${REPO_ROOT}/.venv/bin/hal0 serve --reload"
    else
        UVICORN_CMD="${PYTHON_CMD} -m uvicorn hal0.api:app --reload --host 0.0.0.0 --port ${HAL0_PORT}"
    fi
else
    warn "hal0 package not importable — using stub (Phase 0)"
    # Just keep something alive on the port for OpenWebUI prewire testing
    UVICORN_CMD="bash -c 'while true; do echo -e \"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK\" | nc -l -p ${HAL0_PORT} -q 1 2>/dev/null || true; done'"
fi

HAL0_HOME="${HAL0_HOME}" \
    HAL0_PORT="${HAL0_PORT}" \
    bash -c "${UVICORN_CMD}" >"${API_LOG}" 2>&1 &
API_PID=$!
info "hal0-api PID ${API_PID} — log: ${API_LOG}"

# ── Start OpenWebUI ───────────────────────────────────────────────────────────
if [[ "${SKIP_OWU}" == "1" ]]; then
    step "Skipping OpenWebUI"
    info "HAL0_DEV_SKIP_OPENWEBUI=1 — unset it to start OpenWebUI for this checkout"
else
    step "Starting OpenWebUI"

    OWU_DATA="${VAR_DIR}/openwebui"
    mkdir -p "${OWU_DATA}"

    # Stop any stale dev container
    docker stop "${OWU_CONTAINER}" 2>/dev/null || true

    docker run \
        --rm \
        --name "${OWU_CONTAINER}" \
        --env-file "${ETC_DIR}/openwebui.env" \
        -v "${OWU_DATA}:/app/backend/data" \
        -p "${HAL0_OPENWEBUI_PORT}:8080" \
        --add-host "host.docker.internal:host-gateway" \
        ghcr.io/open-webui/open-webui:main \
        >/dev/null 2>&1 &

    info "OpenWebUI starting (container: ${OWU_CONTAINER})"
    warn "  OpenWebUI may take ~30s on first boot while it initialises its DB"
fi

# ── Start UI dev server ───────────────────────────────────────────────────────
if [[ "${UI_AVAILABLE}" -eq 1 ]]; then
    step "Starting UI dev server"
    UI_LOG="${LOG_DIR}/ui.log"

    (cd "${REPO_ROOT}/ui" && npm run dev -- --port "${UI_PORT}" 2>&1) \
        >"${UI_LOG}" 2>&1 &
    UI_PID=$!
    info "UI dev server PID ${UI_PID} — log: ${UI_LOG}"
else
    warn "Skipping UI dev server (npm not available)"
    UI_PORT="(not started)"
fi

# ── Print URLs ────────────────────────────────────────────────────────────────
printf '\n%s%sDev services running%s\n\n' "${GREEN}" "${BOLD}" "${RESET}"
printf '  hal0 API       %shttp://localhost:%s%s   (log: %s)\n' \
    "${BOLD}" "${HAL0_PORT}" "${RESET}" "${LOG_DIR}/api.log"
if [[ "${SKIP_OWU}" != "1" ]]; then
    printf '  OpenWebUI      %shttp://localhost:%s%s   (container: %s)\n' \
        "${BOLD}" "${HAL0_OPENWEBUI_PORT}" "${RESET}" "${OWU_CONTAINER}"
fi
if [[ "${UI_AVAILABLE}" -eq 1 ]]; then
    printf '  UI dev server  %shttp://localhost:%s%s   (log: %s)\n' \
        "${BOLD}" "${UI_PORT}" "${RESET}" "${LOG_DIR}/ui.log"
fi
printf '\n  %sCtrl-C to stop all services%s\n\n' "${DIM}" "${RESET}"

# ── Wait ─────────────────────────────────────────────────────────────────────
wait "${API_PID}" 2>/dev/null || true
