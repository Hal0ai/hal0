#!/bin/sh
# hal0 — Agent shim CI smoke test (opencode track-latest mitigation).
#
# Triggered by: .github/workflows/agent-shim-smoke.yml (nightly + dispatch).
#
# Why this exists:
#   ADR-0004 §3 ("Mitigation for track-latest churn"): opencode is installed
#   track-latest (no version pin), so this nightly re-runs
#   `installer/agents/opencode.sh` end-to-end against the *latest* upstream
#   opencode revision and asserts (a) the CLI binary lands and (b) an MCP
#   round-trip against hal0's memory server. If upstream opencode renames
#   the npm package or breaks the CLI, this goes red overnight instead of
#   breaking real installs the next day.
#
# Assumptions:
#   - Runs in a disposable container/box. We do not undo what we install.
#   - hal0's MCP memory server listens on /mcp/memory of hal0-api. The
#     opencode agent's own wiring (~/.config/opencode/opencode.json) is
#     written by the driver, not this script — the installer only lands the
#     CLI, which is exactly the track-latest surface we smoke here.
#
# Exit codes:
#   0  success
#   1  install failure (bootstrap / install.sh / opencode.sh)
#   2  hal0-api.service never reached active
#   3  MCP round-trip failed (memory_add OR memory_search OR canary missing)
#   4  prerequisite missing (curl, python3, systemctl)
#   5  agent installer file not found at merge time (hard fail in CI)
#   6  opencode CLI not present after install (track-latest contract broke)
#
# Usage:
#   sudo sh scripts/smoke-opencode.sh
#   sh scripts/smoke-opencode.sh --dry-run     # print plan, do nothing

set -eu

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,33p' "$0"
            exit 0
            ;;
        *) echo "smoke-opencode: unknown arg: $arg" >&2; exit 64 ;;
    esac
done

# ── output helpers ────────────────────────────────────────────────────────
log()  { printf '[smoke-opencode] %s\n' "$*"; }
err()  { printf '[smoke-opencode] ERROR: %s\n' "$*" >&2; }
die()  { code="$1"; shift; err "$*"; exit "$code"; }

# ── locate repo + agent installer ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BOOTSTRAP="${REPO_ROOT}/installer/bootstrap.sh"
INSTALLER="${REPO_ROOT}/installer/install.sh"
OPENCODE_INSTALLER="${REPO_ROOT}/installer/agents/opencode.sh"

# Canary identifies this run in the memory store so parallel CI runs can't
# false-positive each other.
RAND_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
CANARY="ci-smoke-canary ${RAND_ID}"

# MCP endpoint — hal0-api binds 127.0.0.1:8080 by default in installer.
HAL0_HOST="${HAL0_HOST:-127.0.0.1}"
HAL0_PORT="${HAL0_PORT:-8080}"
MCP_URL="http://${HAL0_HOST}:${HAL0_PORT}/mcp/memory"

# ── prerequisites ────────────────────────────────────────────────────────
need() {
    command -v "$1" >/dev/null 2>&1 || die 4 "missing prerequisite: $1"
}
need curl
need python3
need systemctl

# ── plan ─────────────────────────────────────────────────────────────────
log "plan:"
log "  repo root:           ${REPO_ROOT}"
log "  installer entry:     ${INSTALLER}"
log "  opencode installer:  ${OPENCODE_INSTALLER}"
log "  MCP endpoint:        ${MCP_URL}"
log "  canary:              ${CANARY}"

if [ "${DRY_RUN}" -eq 1 ]; then
    log "dry-run mode — exiting without side effects"
    exit 0
fi

# ── 1. Install hal0 ──────────────────────────────────────────────────────
if [ "${HAL0_SMOKE_USE_BOOTSTRAP:-0}" = "1" ]; then
    [ -x "${BOOTSTRAP}" ] || die 1 "bootstrap.sh missing or not executable at ${BOOTSTRAP}"
    log "installing hal0 via bootstrap.sh"
    sudo bash "${BOOTSTRAP}" || die 1 "bootstrap.sh failed"
else
    [ -x "${INSTALLER}" ] || die 1 "install.sh missing or not executable at ${INSTALLER}"
    log "installing hal0 via install.sh (in-tree, --no-start)"
    sudo bash "${INSTALLER}" --no-start || die 1 "install.sh failed"
fi

# ── 2. Run opencode agent installer (track-latest npm) ───────────────────
if [ ! -e "${OPENCODE_INSTALLER}" ]; then
    die 5 "agent installer not found: ${OPENCODE_INSTALLER}"
fi
log "running opencode agent installer"
sudo bash "${OPENCODE_INSTALLER}" || die 1 "opencode.sh failed"

# ── 2b. Assert the CLI landed (the track-latest contract) ────────────────
# npm -g may drop the binary on PATH or under ~/.opencode/bin depending on
# the install path the script took — check both.
if command -v opencode >/dev/null 2>&1; then
    log "opencode present: $(opencode --version 2>/dev/null | head -1)"
elif [ -x "${HOME}/.opencode/bin/opencode" ]; then
    log "opencode present at ~/.opencode/bin: $("${HOME}/.opencode/bin/opencode" --version 2>/dev/null | head -1)"
else
    die 6 "opencode CLI not found after install — upstream may have renamed the npm package or install path"
fi

# ── 3. Wait for hal0-api.service ─────────────────────────────────────────
log "waiting for hal0-api.service to become active"
deadline=$(( $(date +%s) + 120 ))
while [ "$(date +%s)" -lt "${deadline}" ]; do
    if systemctl is-active --quiet hal0-api.service; then
        log "hal0-api.service is active"
        break
    fi
    sleep 1
done
if ! systemctl is-active --quiet hal0-api.service; then
    err "hal0-api.service did not reach active within 120s"
    sudo systemctl status hal0-api.service --no-pager || true
    sudo journalctl -u hal0-api.service --no-pager -n 200 || true
    exit 2
fi

log "waiting for ${MCP_URL} to respond"
deadline=$(( $(date +%s) + 30 ))
while [ "$(date +%s)" -lt "${deadline}" ]; do
    if curl -fsS -o /dev/null -w '%{http_code}' "${MCP_URL}" 2>/dev/null \
            | grep -qE '^(200|400|405|415|422)$'; then
        log "MCP route responding"
        break
    fi
    sleep 1
done

# ── 4. MCP round-trip ────────────────────────────────────────────────────
call_mcp() {
    method="$1"
    params="$2"
    python3 - "$MCP_URL" "$method" "$params" <<'PY'
import json, sys, urllib.request, urllib.error, uuid

url, method, params_json = sys.argv[1], sys.argv[2], sys.argv[3]
body = json.dumps({
    "jsonrpc": "2.0",
    "id":      str(uuid.uuid4()),
    "method":  method,
    "params":  json.loads(params_json),
}).encode("utf-8")
req = urllib.request.Request(
    url,
    data=body,
    headers={"Content-Type": "application/json", "Accept": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        sys.stdout.write(resp.read().decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", "replace") if e.fp else ""
    sys.stderr.write(f"HTTP {e.code} {e.reason}: {body}\n")
    sys.exit(1)
except Exception as e:
    sys.stderr.write(f"request failed: {e}\n")
    sys.exit(1)
PY
}

log "MCP: memory_add (canary)"
ADD_PARAMS=$(python3 -c '
import json, sys
print(json.dumps({"text": sys.argv[1]}))
' "${CANARY}")
if ! ADD_RESP="$(call_mcp memory_add "${ADD_PARAMS}")"; then
    err "memory_add failed"
    exit 3
fi
log "memory_add response: ${ADD_RESP}"

log "MCP: memory_search (canary)"
SEARCH_PARAMS='{"query": "ci-smoke-canary"}'
if ! SEARCH_RESP="$(call_mcp memory_search "${SEARCH_PARAMS}")"; then
    err "memory_search failed"
    exit 3
fi
log "memory_search response: ${SEARCH_RESP}"

if ! printf '%s' "${SEARCH_RESP}" | grep -qF "${RAND_ID}"; then
    err "MCP round-trip canary not found in search results"
    err "  wrote:   ${CANARY}"
    err "  got:     ${SEARCH_RESP}"
    exit 3
fi

log "MCP round-trip OK — canary ${RAND_ID} survived the loop"
log "smoke PASSED"
exit 0
