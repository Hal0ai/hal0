#!/usr/bin/env bash
# Superset workspace teardown — runs when Superset deletes the workspace.
#
# Three jobs, in order:
#   1. Stop anything this workspace started (dev servers, its OpenWebUI
#      container) so nothing outlives the worktree holding a port.
#   2. Refuse to proceed if the worktree still holds work that exists nowhere
#      else. A non-zero exit here surfaces as an error toast with a
#      "Delete Anyway" button, so this is a speed bump the operator can
#      override — not a lock.
#   3. Remove the rebuildable, gitignored artifacts (.venv, node_modules,
#      hal0-home, caches) so `git worktree remove` doesn't trip over untracked
#      files and the disk actually comes back.
#
# It never deletes tracked source and never touches anything outside the
# workspace path.
#
# Knobs:
#   HAL0_TEARDOWN_FORCE=1      skip the unpushed-work check
#   HAL0_TEARDOWN_KEEP_DEPS=1  stop services, keep .venv / node_modules

set -euo pipefail

if [[ -t 1 ]]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
    BOLD='\033[1m'; RESET='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; BOLD=''; RESET=''
fi

info() { printf "${GREEN}✔${RESET}  %s\n" "$*"; }
warn() { printf "${YELLOW}!${RESET}  %s\n" "$*" >&2; }
step() { printf "\n${BOLD}── %s${RESET}\n" "$*"; }
die()  { printf "${RED}✗${RESET}  %s\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=ports.sh
source "${SCRIPT_DIR}/ports.sh"

WS="${HAL0_WORKSPACE_PATH}"

# Guard: everything below deletes paths under $WS. Prove $WS is a hal0 checkout
# before believing it, so a bad SUPERSET_WORKSPACE_PATH can't aim rm at $HOME.
[[ -n "${WS}" && "${WS}" != "/" ]]            || die "refusing to tear down '${WS}'"
[[ -f "${WS}/pyproject.toml" ]]               || die "refusing: ${WS} has no pyproject.toml"
[[ -d "${WS}/.superset" && -d "${WS}/src" ]]  || die "refusing: ${WS} is not a hal0 checkout"

cd "${WS}"

# ── 1. Stop what this workspace started ──────────────────────────────────────
step "Stopping services"

if [[ -f "${SCRIPT_DIR}/run.pid" ]]; then
    RUN_PID="$(cat "${SCRIPT_DIR}/run.pid")"
    if [[ "${RUN_PID}" =~ ^[0-9]+$ ]] && kill -0 "${RUN_PID}" 2>/dev/null; then
        # TERM lets dev-bootstrap.sh's own EXIT trap reap uvicorn + vite.
        kill -TERM "${RUN_PID}" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "${RUN_PID}" 2>/dev/null || break
            sleep 0.5
        done
        kill -KILL "${RUN_PID}" 2>/dev/null || true
        info "stopped run.sh (pid ${RUN_PID})"
    fi
    rm -f "${SCRIPT_DIR}/run.pid"
fi

if command -v docker >/dev/null 2>&1; then
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "${HAL0_OWU_CONTAINER}"; then
        docker rm -f "${HAL0_OWU_CONTAINER}" >/dev/null 2>&1 || true
        info "removed container ${HAL0_OWU_CONTAINER}"
    fi
fi

# ── 2. Don't silently drop work that exists nowhere else ─────────────────────
step "Checking for unsaved work"

if [[ "${HAL0_TEARDOWN_FORCE:-0}" == "1" ]]; then
    warn "HAL0_TEARDOWN_FORCE=1 — skipping the unpushed-work check"
else
    DIRTY="$(git status --porcelain --untracked-files=no 2>/dev/null || true)"

    # "Reachable from HEAD but from no remote ref" is the exact question, and
    # it doesn't care what the remotes are named. hal0 has no `origin`: its
    # remotes are `github` (PR target) and `hal0` (deploy target), so an
    # `origin/main` fallback would have reported a clean slate for every
    # branch and silently discarded the work.
    if [[ -z "$(git remote 2>/dev/null)" ]]; then
        UNPUSHED="$(git log --oneline -n 20 HEAD 2>/dev/null || true)"
        [[ -n "${UNPUSHED}" ]] && warn "no git remotes configured — treating all history as local-only"
    else
        UNPUSHED="$(git log --oneline -n 20 HEAD --not --remotes 2>/dev/null || true)"
    fi

    if [[ -n "${DIRTY}" || -n "${UNPUSHED}" ]]; then
        printf '\n'
        [[ -n "${DIRTY}" ]]    && { printf '  Uncommitted changes:\n'; printf '%s\n' "${DIRTY}" | sed 's/^/    /'; }
        [[ -n "${UNPUSHED}" ]] && { printf '  Commits on no remote (first 20):\n'; printf '%s\n' "${UNPUSHED}" | sed 's/^/    /'; }
        printf '\n'
        die "$(cat <<EOF
this workspace still holds work that is not on a remote.
   Push it, or choose "Delete Anyway" to discard it.
   Services are already stopped; nothing has been deleted.
EOF
)"
    fi
    info "nothing unpushed"
fi

# ── 3. Reclaim disk ──────────────────────────────────────────────────────────
step "Removing workspace artifacts"

if [[ "${HAL0_TEARDOWN_KEEP_DEPS:-0}" == "1" ]]; then
    warn "HAL0_TEARDOWN_KEEP_DEPS=1 — keeping .venv and node_modules"
    ARTIFACTS=(hal0-home ui/test-results ui/playwright-report .pytest_cache .ruff_cache .mypy_cache)
else
    ARTIFACTS=(hal0-home .venv ui/node_modules ui/dist ui/test-results ui/playwright-report
               .pytest_cache .ruff_cache .mypy_cache)
fi

for rel in "${ARTIFACTS[@]}"; do
    target="${WS}/${rel}"
    # Re-assert containment per path: a symlinked artifact dir must not let rm
    # escape the workspace.
    resolved="$(hal0_realpath "${target}" 2>/dev/null || true)"
    [[ -e "${target}" ]] || continue
    if [[ -z "${resolved}" || "${resolved}" != "${WS}/"* ]]; then
        warn "skipping ${rel} — resolves outside the workspace (${resolved:-unresolvable})"
        continue
    fi
    rm -rf "${target}"
    info "removed ${rel}"
done

rm -f "${SCRIPT_DIR}/workspace.env"

step "Teardown complete"
printf '  %s is ready for removal\n\n' "${WS}"
