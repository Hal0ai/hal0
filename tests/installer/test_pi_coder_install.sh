#!/bin/sh
# hal0 — pi-coder installer smoke stub.
#
# Static-only checks (no install side effects):
#   1. installer/agents/pi-coder.sh parses (bash -n).
#   2. scripts/fork-pi-mono.sh parses (sh -n).
#   3. pi-coder.sh references the Hal0ai/pi-mono fork at least once.
#   4. pi-coder.sh does not still reference the legacy badlogic/pi-mono
#      GitHub URL (the npm package name `pi-mono` is allowed and ignored).
#
# TODO: nightly-CI hookup pending — wire into
# .github/workflows/agent-shim-smoke.yml alongside scripts/smoke-pi-coder.sh
# once the full-stack smoke is green on main.
#
# Usage:
#   sh tests/installer/test_pi_coder_install.sh
#
# Exit 0 on pass, non-zero w/ message on fail.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALLER="${REPO_ROOT}/installer/agents/pi-coder.sh"
FORK_SYNC="${REPO_ROOT}/scripts/fork-pi-mono.sh"

log()  { printf '[test_pi_coder_install] %s\n' "$*"; }
fail() { printf '[test_pi_coder_install] FAIL: %s\n' "$*" >&2; exit 1; }

[ -r "$INSTALLER" ] || fail "missing $INSTALLER"
[ -r "$FORK_SYNC" ] || fail "missing $FORK_SYNC"

log "syntax-check pi-coder.sh"
bash -n "$INSTALLER" || fail "pi-coder.sh failed bash -n"

log "syntax-check fork-pi-mono.sh"
sh -n "$FORK_SYNC" || fail "fork-pi-mono.sh failed sh -n"

log "assert pi-coder.sh references Hal0ai/pi-mono fork"
grep -q 'Hal0ai/pi-mono' "$INSTALLER" \
    || fail "pi-coder.sh does not mention Hal0ai/pi-mono"

log "assert no lingering github.com/badlogic/pi-mono URLs"
if grep -q 'github\.com/badlogic/pi-mono' "$INSTALLER"; then
    fail "pi-coder.sh still references github.com/badlogic/pi-mono"
fi

log "assert npm install line uses upstream-renamed package name"
grep -q '@earendil-works/pi-coding-agent' "$INSTALLER" \
    || fail "pi-coder.sh missing @earendil-works/pi-coding-agent npm package reference"

log "assert no lingering 'npm install -g pi-mono' invocation"
if grep -q 'npm install -g pi-mono\b' "$INSTALLER"; then
    fail "pi-coder.sh still tries to npm install -g pi-mono (renamed upstream)"
fi

log "PASS"
exit 0
