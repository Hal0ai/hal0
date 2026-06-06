#!/usr/bin/env bash
# hal0 runtime deploy — the canonical way to update an editable /opt/hal0
# checkout (e.g. CT 105) to origin/main.
#
# The old hand sequence was just `git fetch && git reset --hard origin/main`.
# That updates *source* but NOT the dashboard: ui/dist is gitignored, so the
# reset never touches the served bundle, and the SPA stays stale until someone
# remembers to `npm run build`. This script folds the UI build (and the service
# restart that the editable Python install needs) into one deterministic step.
#
# Usage (run on the runtime host, from anywhere inside the checkout):
#   bash scripts/deploy.sh [--ref origin/main] [--no-restart] [--no-build] [--force]
#
# Steps:
#   1. Fetch + hard-reset the working tree to the target ref
#   2. Rebuild the dashboard (clean: wipe dist + vite cache; npm ci only when
#      package-lock changed in this pull, else just rebuild)
#   3. Restart hal0-api so the editable backend picks up the new source
#   4. Health-check the gateway and report the served bundle
#
# Safety: refuses to reset over uncommitted *tracked* edits (another session may
# be mid-work on this shared tree) unless --force is given. Untracked files
# (local build artifacts, scratch) are left alone.

set -euo pipefail
IFS=$'\n\t'

# ── Colour helpers ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
    BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; YELLOW=''; GREEN=''; BOLD=''; RESET=''
fi
info()  { printf "${GREEN}✔${RESET}  %s\n" "$*"; }
warn()  { printf "${YELLOW}!${RESET}  %s\n" "$*"; }
die()   { printf "${RED}✗${RESET}  %s\n" "$*" >&2; exit 1; }
step()  { printf "\n${BOLD}── %s${RESET}\n" "$*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="origin/main"
DO_RESTART=1
DO_BUILD=1
FORCE=0
SERVICE="hal0-api"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref=*)      REF="${1#--ref=}"; shift ;;
        --ref)        shift; REF="$1"; shift ;;
        --no-restart) DO_RESTART=0; shift ;;
        --no-build)   DO_BUILD=0; shift ;;
        --force)      FORCE=1; shift ;;
        --service=*)  SERVICE="${1#--service=}"; shift ;;
        -h|--help)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)            die "unknown arg: $1" ;;
    esac
done

cd "$REPO_ROOT"

# ── 1. Sync source ────────────────────────────────────────────────────────────
step "1. Sync source → ${REF}"

remote="${REF%%/*}"
git fetch "$remote" --prune --quiet || die "git fetch ${remote} failed"

dirty_tracked="$(git status --porcelain --untracked-files=no)"
if [[ -n "$dirty_tracked" ]] && [[ "$FORCE" -ne 1 ]]; then
    printf "%s\n" "$dirty_tracked" >&2
    die "uncommitted tracked changes present — another session may be working here. Re-run with --force to discard."
fi

before="$(git rev-parse HEAD)"
# Capture the UI dependency lockfile hash before the reset so we can decide
# whether a full `npm ci` is needed (slow) or a plain rebuild suffices (fast).
lock_before="$(git rev-parse "HEAD:ui/package-lock.json" 2>/dev/null || echo none)"

git reset --hard "$REF" --quiet || die "git reset --hard ${REF} failed"
after="$(git rev-parse HEAD)"
lock_after="$(git rev-parse "HEAD:ui/package-lock.json" 2>/dev/null || echo none)"

if [[ "$before" == "$after" ]]; then
    info "already at $(git rev-parse --short HEAD) — no source change"
else
    info "$(git rev-parse --short "$before") → $(git rev-parse --short "$after")"
fi

# ── 2. Rebuild dashboard ──────────────────────────────────────────────────────
if [[ "$DO_BUILD" -eq 1 ]] && [[ -d "${REPO_ROOT}/ui" ]]; then
    step "2. Rebuild dashboard (ui/dist is gitignored — not carried by the reset)"
    command -v npm >/dev/null 2>&1 || die "npm not found — install Node toolchain or pass --no-build"
    (
        cd "${REPO_ROOT}/ui"
        # Clean: stale .vite cache re-emits stale scoped CSS across rebuilds.
        rm -rf dist node_modules/.vite
        if [[ "$lock_before" != "$lock_after" ]] || [[ ! -d node_modules ]]; then
            info "package-lock changed (or node_modules absent) → npm ci"
            npm ci --silent
        else
            info "deps unchanged → skipping npm ci"
        fi
        npm run build --silent
    ) || die "ui build failed"
    built_assets="$(cd "${REPO_ROOT}/ui/dist/assets" 2>/dev/null && printf '%s,' *.js | sed 's/,$//')"
    info "dashboard rebuilt: ${built_assets:-?}"
elif [[ "$DO_BUILD" -eq 0 ]]; then
    warn "skipping UI build (--no-build)"
fi

# ── 3. Restart service ────────────────────────────────────────────────────────
if [[ "$DO_RESTART" -eq 1 ]]; then
    step "3. Restart ${SERVICE} (editable install picks up new source on restart)"
    if command -v systemctl >/dev/null 2>&1 && systemctl cat "$SERVICE" >/dev/null 2>&1; then
        sudo systemctl restart "$SERVICE" || die "failed to restart ${SERVICE}"
        info "${SERVICE} restarted"
    else
        warn "${SERVICE} unit not found — skipping restart"
    fi
else
    warn "skipping service restart (--no-restart)"
fi

# ── 4. Health check ───────────────────────────────────────────────────────────
step "4. Health check"
port="${HAL0_PORT:-8080}"
url="http://127.0.0.1:${port}"
ok=0
for _ in $(seq 1 15); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "${url}/api/status" 2>/dev/null || echo 000)"
    if [[ "$code" == "200" ]]; then ok=1; break; fi
    sleep 1
done
if [[ "$ok" -eq 1 ]]; then
    served="$(curl -s "${url}/" 2>/dev/null | grep -oE 'index-[A-Za-z0-9_-]+\.js' | head -1 || true)"
    info "gateway healthy at ${url} (serving ${served:-?})"
    info "deploy complete @ $(git rev-parse --short HEAD)"
else
    die "gateway did not return 200 at ${url}/api/status after restart"
fi
