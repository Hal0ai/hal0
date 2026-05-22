#!/bin/sh
# hal0 Phase 8 — Agent shim CI smoke test (pi-coder track-latest mitigation).
#
# Owner: CI-smoke Wave 1 team.
# Triggered by: .github/workflows/agent-shim-smoke.yml (nightly + dispatch).
#
# Why this exists:
#   ADR-0004 §3 ("Mitigation for track-latest churn") and PLAN.md §17
#   commit hal0 to a nightly CI smoke that re-runs
#   `installer/agents/pi-coder.sh` end-to-end against the *latest*
#   upstream pi-coder revision and asserts an MCP round-trip. If
#   upstream pi-coder changes a CLI flag or shim contract, this test
#   goes red overnight instead of breaking real installs the next day.
#
# Assumptions:
#   - Runs in a disposable container/box. We do not undo what we install.
#   - `installer/agents/pi-coder.sh` is owned by the Agent-installer team
#     and is expected to exist at merge time. This script tolerates its
#     absence at write-time (see PI_CODER_INSTALLER check) so it can
#     land in parallel with the Agent-installer Wave 1 file.
#   - hal0's MCP memory server is provisioned by pi-coder.sh and listens
#     on /mcp/memory of the hal0-api process. The exact transport is the
#     in-tree contract (see src/hal0 + ADR-0004); we just hit it with
#     curl using the documented JSON-RPC shape.
#
# Exit codes:
#   0  success
#   1  install failure (bootstrap / install.sh / pi-coder.sh)
#   2  hal0-api.service never reached active
#   3  MCP round-trip failed (memory_add OR memory_search OR canary missing)
#   4  prerequisite missing (curl, python3, systemctl)
#   5  agent installer file not found at merge time (hard fail in CI)
#
# Usage:
#   sudo sh scripts/smoke-pi-coder.sh
#   sh scripts/smoke-pi-coder.sh --dry-run     # print plan, do nothing

set -eu

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *) echo "smoke-pi-coder: unknown arg: $arg" >&2; exit 64 ;;
    esac
done

# ── output helpers ────────────────────────────────────────────────────────
log()  { printf '[smoke-pi-coder] %s\n' "$*"; }
err()  { printf '[smoke-pi-coder] ERROR: %s\n' "$*" >&2; }
die()  { code="$1"; shift; err "$*"; exit "$code"; }

# ── locate repo + agent installer ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BOOTSTRAP="${REPO_ROOT}/installer/bootstrap.sh"
INSTALLER="${REPO_ROOT}/installer/install.sh"
PI_CODER_INSTALLER="${REPO_ROOT}/installer/agents/pi-coder.sh"

# Canary string identifies this run in the federated memory store so
# parallel CI runs can't false-positive each other.
RAND_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
CANARY="ci-smoke-canary ${RAND_ID}"

# MCP endpoint — hal0-api binds 127.0.0.1:8080 by default in installer
# (HAL0_PORT override respected if the workflow sets it).
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
log "  pi-coder installer:  ${PI_CODER_INSTALLER}"
log "  MCP endpoint:        ${MCP_URL}"
log "  canary:              ${CANARY}"

if [ "${DRY_RUN}" -eq 1 ]; then
    log "dry-run mode — exiting without side effects"
    exit 0
fi

# ── 1. Install hal0 ──────────────────────────────────────────────────────
# Prefer running installer/install.sh directly (we're already inside the
# checked-out source tree; no need to round-trip through bootstrap's
# fetch+cosign path). If a workflow wants to exercise the full
# fetch+verify path it can set HAL0_SMOKE_USE_BOOTSTRAP=1.
if [ "${HAL0_SMOKE_USE_BOOTSTRAP:-0}" = "1" ]; then
    [ -x "${BOOTSTRAP}" ] || die 1 "bootstrap.sh missing or not executable at ${BOOTSTRAP}"
    log "installing hal0 via bootstrap.sh"
    sudo bash "${BOOTSTRAP}" || die 1 "bootstrap.sh failed"
else
    [ -x "${INSTALLER}" ] || die 1 "install.sh missing or not executable at ${INSTALLER}"
    log "installing hal0 via install.sh (in-tree, --no-start)"
    # --no-start lets pi-coder.sh wire its own bits before systemd boots
    # hal0-api; the agents installer is expected to do the final start.
    sudo bash "${INSTALLER}" --no-start || die 1 "install.sh failed"
fi

# ── 2. Run pi-coder agent installer ──────────────────────────────────────
if [ ! -e "${PI_CODER_INSTALLER}" ]; then
    # At merge time this must exist. If it doesn't we want a loud red
    # CI failure with an actionable hint, not a silent pass.
    die 5 "agent installer not found: ${PI_CODER_INSTALLER}
          The Agent-installer team owns this file. If this smoke test is
          running before they land it, mark this workflow as
          continue-on-error in the meantime — do not stub the file."
fi
log "running pi-coder agent installer"
sudo bash "${PI_CODER_INSTALLER}" || die 1 "pi-coder.sh failed"

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

# Give the MCP route an extra moment past systemd-active in case the
# FastAPI worker is still warming pydantic + DB. 30s should cover any
# realistic cold-boot on ubuntu-latest.
log "waiting for ${MCP_URL} to respond"
deadline=$(( $(date +%s) + 30 ))
while [ "$(date +%s)" -lt "${deadline}" ]; do
    if curl -fsS -o /dev/null -w '%{http_code}' "${MCP_URL}" 2>/dev/null \
            | grep -qE '^(200|400|405|415|422)$'; then
        # Anything other than connection-refused means the route is up.
        log "MCP route responding"
        break
    fi
    sleep 1
done

# ── 4. MCP round-trip ────────────────────────────────────────────────────
# ADR-0004 §3 requires asserting *an MCP round-trip*. We use the two
# tools every memory MCP server bundled with hal0 must expose:
# memory_add (write) and memory_search (read).
#
# JSON-RPC 2.0 over HTTP POST is the in-tree contract. If hal0's MCP
# transport diverges (SSE-only, streamable-http, etc.) the request shape
# below is the single point we'll need to update.

call_mcp() {
    # $1 = method, $2 = JSON params body (object).
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
        out = resp.read().decode("utf-8", "replace")
        sys.stdout.write(out)
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

# Assert the exact canary id we just wrote is in the search result.
if ! printf '%s' "${SEARCH_RESP}" | grep -qF "${RAND_ID}"; then
    err "MCP round-trip canary not found in search results"
    err "  wrote:   ${CANARY}"
    err "  got:     ${SEARCH_RESP}"
    exit 3
fi

log "MCP round-trip OK — canary ${RAND_ID} survived the loop"
log "smoke PASSED"
exit 0
