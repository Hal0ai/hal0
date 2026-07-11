#!/usr/bin/env bash
# hal0 push-dev — push-based dev deploy from THIS checkout to a remote
# editable hal0 box (e.g. CT 105), without a commit, push, or release.
#
# scripts/deploy.sh is the pull-based canonical deploy: run ON the box, it
# resets the checkout to a pushed ref. This is its inner-loop sibling: run on
# the DEV machine, it rsyncs the working tree's Python package (src/hal0) over
# the box's editable install and installs a locally-built dashboard bundle
# into the path the running server actually serves — so uncommitted work is
# live in seconds. The dashboard is read per-request, so UI-only pushes need
# no service restart at all (--ui-only).
#
# Usage (from anywhere inside the checkout):
#   HAL0_DEV_HOST=root@10.0.1.142 bash scripts/push-dev.sh [options]
#
# Options:
#   --host <ssh-host>   SSH target (or HAL0_DEV_HOST). Required.
#   --root <dir>        Remote hal0 tree (or HAL0_DEV_ROOT). Auto-detected
#                       when omitted: /opt/hal0, else the resolved
#                       /usr/lib/hal0/current — whichever contains src/hal0.
#   --ui-only           Push only the dashboard bundle (implies no restart).
#   --no-ui             Push only Python source (skip UI build + push).
#   --no-build          Push the existing ui/dist without rebuilding.
#   --no-restart        Skip the hal0-api restart after a Python push.
#   --service <name>    systemd unit to restart (default hal0-api).
#   --force             Push even if the remote tree has uncommitted tracked
#                       changes that did NOT come from a previous push-dev.
#   --dry-run           Show what rsync would transfer; change nothing.
#
# Safety: a previous push-dev leaves the remote tree dirty by design, so we
# drop a marker file ($ROOT/.hal0-push-dev.json) recording what was pushed.
# If the remote tree is dirty and there is NO marker, someone was hand-editing
# on the box — we refuse without --force (mirrors deploy.sh's guard).
#
# Scope: src/hal0 + the dashboard bundle only. ComfyUI custom nodes, config
# migrations, and dependency changes still need scripts/deploy.sh (or a real
# update) — this is a fast inner loop, not a full deploy. The box's *reported*
# version (hal0 --version) does not change: the editable install keeps its
# pyproject version. To restore the canonical state afterwards, run
# scripts/deploy.sh on the box (its git reset erases everything pushed here).

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
HOST="${HAL0_DEV_HOST:-}"
ROOT="${HAL0_DEV_ROOT:-}"
SERVICE="hal0-api"
DO_PY=1
DO_UI=1
DO_BUILD=1
DO_RESTART=1
FORCE=0
DRY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host=*)     HOST="${1#--host=}"; shift ;;
        --host)       shift; HOST="$1"; shift ;;
        --root=*)     ROOT="${1#--root=}"; shift ;;
        --root)       shift; ROOT="$1"; shift ;;
        --ui-only)    DO_PY=0; DO_RESTART=0; shift ;;
        --no-ui)      DO_UI=0; shift ;;
        --no-build)   DO_BUILD=0; shift ;;
        --no-restart) DO_RESTART=0; shift ;;
        --service=*)  SERVICE="${1#--service=}"; shift ;;
        --service)    shift; SERVICE="$1"; shift ;;
        --force)      FORCE=1; shift ;;
        --dry-run)    DRY=1; shift ;;
        -h|--help)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)            die "unknown arg: $1" ;;
    esac
done

[[ -n "$HOST" ]] || die "no target host — pass --host or set HAL0_DEV_HOST (e.g. root@<box-ip>)"
[[ "$DO_PY" -eq 1 || "$DO_UI" -eq 1 ]] || die "--ui-only and --no-ui together leave nothing to push"

# ConnectTimeout keeps a wrong IP from hanging the whole loop; BatchMode is
# deliberately NOT set so password/agent prompts still work interactively.
SSH_OPTS=(-o ConnectTimeout=5)
[[ -n "${HAL0_DEV_SSH_OPTS:-}" ]] && read -r -a extra_opts <<< "${HAL0_DEV_SSH_OPTS}" && SSH_OPTS+=("${extra_opts[@]}")

RSYNC_FLAGS=(-az --delete)
[[ "$DRY" -eq 1 ]] && RSYNC_FLAGS+=(-n -v)

# ── 1. Probe the box ──────────────────────────────────────────────────────────
step "1. Probe ${HOST}"

probe="$(ssh "${SSH_OPTS[@]}" "$HOST" bash -s -- "$ROOT" <<'EOF'
set -u
root="${1:-}"
if [[ -z "$root" ]]; then
    for cand in /opt/hal0 "$(readlink -f /usr/lib/hal0/current 2>/dev/null || true)"; do
        [[ -n "$cand" && -d "$cand/src/hal0" ]] && { root="$cand"; break; }
    done
fi
[[ -n "$root" ]] || { echo "err=no hal0 tree found (tried /opt/hal0, /usr/lib/hal0/current) — pass --root"; exit 0; }
[[ -d "$root/src/hal0" ]] || { echo "err=${root}/src/hal0 does not exist — wrong --root?"; exit 0; }

served=""
port=""
api_env="/etc/hal0/api.env"
if [[ -r "$api_env" ]]; then
    served="$(sed -n 's/^[[:space:]]*HAL0_UI_DIST=//p' "$api_env" | tail -1 | tr -d '"')"
    port="$(sed -n 's/^[[:space:]]*HAL0_PORT=//p' "$api_env" | tail -1 | tr -d '"')"
fi
[[ -z "$served" && -d /usr/lib/hal0/ui/dist ]] && served=/usr/lib/hal0/ui/dist
[[ -z "$served" ]] && served="$root/ui/dist"

dirty=0
if git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    [[ -n "$(git -C "$root" status --porcelain --untracked-files=no 2>/dev/null)" ]] && dirty=1
fi
marker=0
[[ -f "$root/.hal0-push-dev.json" ]] && marker=1

echo "root=$root"
echo "served=$(readlink -f "$served" 2>/dev/null || echo "$served")"
echo "port=${port:-8080}"
echo "dirty=$dirty"
echo "marker=$marker"
EOF
)" || die "ssh to ${HOST} failed"

probe_err="$(sed -n 's/^err=//p' <<< "$probe")"
[[ -n "$probe_err" ]] && die "$probe_err"
ROOT="$(sed -n 's/^root=//p' <<< "$probe")"
SERVED="$(sed -n 's/^served=//p' <<< "$probe")"
PORT="$(sed -n 's/^port=//p' <<< "$probe")"
REMOTE_DIRTY="$(sed -n 's/^dirty=//p' <<< "$probe")"
HAS_MARKER="$(sed -n 's/^marker=//p' <<< "$probe")"
info "remote tree: ${ROOT}"
info "served dashboard: ${SERVED} (port ${PORT})"

if [[ "$REMOTE_DIRTY" == "1" && "$HAS_MARKER" == "0" && "$FORCE" -ne 1 ]]; then
    die "remote tree has uncommitted tracked changes that did not come from push-dev — someone may be hand-editing on the box. Re-run with --force to overwrite."
fi
[[ "$REMOTE_DIRTY" == "1" && "$HAS_MARKER" == "1" ]] && info "remote dirtiness is from a previous push-dev — expected"

# ── 2. Push Python source ─────────────────────────────────────────────────────
if [[ "$DO_PY" -eq 1 ]]; then
    step "2. Push src/hal0 → ${HOST}:${ROOT}/src/hal0"
    rsync "${RSYNC_FLAGS[@]}" \
        --exclude '__pycache__/' --exclude '*.pyc' \
        -e "ssh ${SSH_OPTS[*]}" \
        "${REPO_ROOT}/src/hal0/" "${HOST}:${ROOT}/src/hal0/" \
        || die "python rsync failed"
    info "python source pushed"
else
    warn "skipping Python push (--ui-only)"
fi

# ── 3. Build + push dashboard ─────────────────────────────────────────────────
if [[ "$DO_UI" -eq 1 ]] && [[ -d "${REPO_ROOT}/ui" ]]; then
    if [[ "$DO_BUILD" -eq 1 ]]; then
        step "3. Build dashboard"
        command -v npm >/dev/null 2>&1 || die "npm not found — install Node toolchain or pass --no-build/--no-ui"
        (
            cd "${REPO_ROOT}/ui"
            # Clean like deploy.sh: stale .vite cache re-emits stale scoped CSS.
            rm -rf dist node_modules/.vite
            if [[ ! -d node_modules ]]; then
                info "node_modules absent → npm ci"
                npm ci --silent
            fi
            npm run build --silent
        ) || die "ui build failed"
    else
        step "3. Push existing ui/dist (--no-build)"
        [[ -d "${REPO_ROOT}/ui/dist" ]] || die "ui/dist does not exist — drop --no-build"
    fi
    rsync "${RSYNC_FLAGS[@]}" \
        -e "ssh ${SSH_OPTS[*]}" \
        "${REPO_ROOT}/ui/dist/" "${HOST}:${SERVED}/" \
        || die "ui rsync failed"
    info "dashboard bundle pushed → ${SERVED} (read per-request; no restart needed for UI)"
else
    [[ "$DO_UI" -eq 0 ]] && warn "skipping UI (--no-ui)"
fi

if [[ "$DRY" -eq 1 ]]; then
    warn "dry run — skipping marker, perms, restart, health check"
    exit 0
fi

# ── 4. Marker + perms + restart + health (single remote pass) ────────────────
step "4. Finalize on box"

local_commit="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
local_dirty=0
[[ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ]] && local_dirty=1
pushed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
pushed_by="${USER:-$(id -un)}@$(hostname)"

finalize="$(ssh "${SSH_OPTS[@]}" "$HOST" bash -s -- \
    "$ROOT" "$SERVED" "$PORT" "$SERVICE" "$DO_RESTART" \
    "$local_commit" "$local_dirty" "$pushed_at" "$pushed_by" "${HAL0_GROUP:-hal0}" <<'EOF'
set -u
root="$1"; served="$2"; port="$3"; service="$4"; do_restart="$5"
commit="$6"; dirty="$7"; at="$8"; by="$9"; grp="${10}"

# Marker: records that remote dirtiness is push-dev's doing (see header).
printf '{"commit":"%s","dirty_worktree":%s,"pushed_at":"%s","pushed_by":"%s"}\n' \
    "$commit" "$([[ "$dirty" == 1 ]] && echo true || echo false)" "$at" "$by" \
    > "$root/.hal0-push-dev.json" 2>/dev/null || true

# Fail-soft group-share re-assert (mirrors deploy.sh): rsync as root recreates
# files root-owned, which locks out the hal0 service user.
if getent group "$grp" >/dev/null 2>&1; then
    for d in "$root/src/hal0" "$served"; do
        chgrp -R "$grp" "$d" 2>/dev/null || true
        chmod -R g+rwX "$d" 2>/dev/null || true
        find "$d" -type d -exec chmod g+s {} + 2>/dev/null || true
    done
fi
chmod -R a+rX "$served" 2>/dev/null || true

if [[ "$do_restart" == "1" ]]; then
    if command -v systemctl >/dev/null 2>&1 && systemctl cat "$service" >/dev/null 2>&1; then
        systemctl restart "$service" || { echo "err=failed to restart ${service}"; exit 0; }
        echo "restarted=1"
    else
        echo "restarted=unit-missing"
    fi
else
    echo "restarted=0"
fi

ok=0
for _ in $(seq 1 15); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/api/status" 2>/dev/null || echo 000)"
    [[ "$code" == "200" ]] && { ok=1; break; }
    sleep 1
done
if [[ "$ok" -eq 1 ]]; then
    echo "health=200"
    echo "bundle=$(curl -s "http://127.0.0.1:${port}/" 2>/dev/null | grep -oE 'index-[A-Za-z0-9_-]+\.js' | head -1 || true)"
else
    echo "err=gateway did not return 200 at :${port}/api/status"
fi
EOF
)" || die "remote finalize ssh failed"

finalize_err="$(sed -n 's/^err=//p' <<< "$finalize")"
[[ -n "$finalize_err" ]] && die "$finalize_err"

restarted="$(sed -n 's/^restarted=//p' <<< "$finalize")"
case "$restarted" in
    1)            info "${SERVICE} restarted" ;;
    unit-missing) warn "${SERVICE} unit not found on box — skipped restart" ;;
    0)            [[ "$DO_PY" -eq 1 ]] && warn "restart skipped (--no-restart) — Python changes are NOT live until ${SERVICE} restarts" ;;
esac

bundle="$(sed -n 's/^bundle=//p' <<< "$finalize")"
info "gateway healthy on ${HOST} :${PORT} (serving ${bundle:-?})"

# ── 5. Verify the served bundle is the one we just built ─────────────────────
if [[ "$DO_UI" -eq 1 && -r "${REPO_ROOT}/ui/dist/index.html" ]]; then
    local_bundle="$(grep -oE 'index-[A-Za-z0-9_-]+\.js' "${REPO_ROOT}/ui/dist/index.html" | head -1 || true)"
    if [[ -n "$local_bundle" && "$bundle" == "$local_bundle" ]]; then
        info "served bundle matches local build (${local_bundle})"
    else
        warn "served bundle (${bundle:-?}) != local build (${local_bundle:-?}) — served path may be wrong"
    fi
fi

step "Done"
info "pushed ${local_commit}$([[ "$local_dirty" == 1 ]] && echo ' (dirty worktree)') from ${pushed_by}"
info "restore canonical state on the box with: bash scripts/deploy.sh"
