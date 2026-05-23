#!/bin/sh
# hal0 — install.sh v0.1.x detection smoke test (PR-21).
#
# Verifies the detection clause added at the top of install.sh per
# lemonade-adoption-plan §9. The test never lets install.sh proceed
# past the detection block — that would require root, systemd, and a
# clean machine. Instead it short-circuits each scenario at the
# detection check.
#
# Strategy: extract the detection block via sed, source it in a
# subshell with mocked v0.1.x paths, and assert behaviour:
#
#   1. Both v0.1.x markers present → block exits non-zero + prints the
#      backup/wipe message.
#   2. Only /etc/hal0/slots/*.toml (no lemonade config) → SAME refusal.
#   3. /etc/hal0/slots/*.toml AND /var/lib/hal0/lemonade/config.json
#      present (partial v0.2 box) → no refusal.
#   4. No /etc/hal0/slots/*.toml (fresh box) → no refusal.
#   5. HAL0_SKIP_V01_DETECT=1 → no refusal even with v0.1.x markers
#      (CI + dev escape hatch).
#
# Usage:
#   sh tests/installer/test_v01_detect.sh
#
# Exit 0 on pass, non-zero w/ message on fail.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALLER="${REPO_ROOT}/installer/install.sh"

log()  { printf '[test_v01_detect] %s\n' "$*"; }
fail() { printf '[test_v01_detect] FAIL: %s\n' "$*" >&2; exit 1; }

[ -r "${INSTALLER}" ] || fail "missing ${INSTALLER}"

# ── 1. syntax sanity ─────────────────────────────────────────────────
log "syntax-check install.sh"
bash -n "${INSTALLER}" || fail "install.sh failed bash -n"

# ── 2. extract the detection block ───────────────────────────────────
# We grep for the named bounds so a rename of the surrounding sections
# does not silently start matching the wrong region.
log "extract detection block from install.sh"
WORKDIR="$(mktemp -d -t hal0-v01-detect-XXXXXX)"
trap 'rm -rf "${WORKDIR}"' EXIT

awk '
    /^# ── v0\.1\.x state detection ──/ { capturing = 1 }
    capturing { print }
    capturing && /^fi$/ {
        # The second `fi` closes the outer HAL0_SKIP_V01_DETECT guard.
        fi_count++
        if (fi_count == 2) { exit }
    }
' "${INSTALLER}" > "${WORKDIR}/block.sh"

[ -s "${WORKDIR}/block.sh" ] || fail "detection block not found in install.sh"

# Sanity: block must reference both fingerprint paths.
grep -q '/etc/hal0/slots' "${WORKDIR}/block.sh" \
    || fail "detection block missing /etc/hal0/slots/* check"
grep -q '/var/lib/hal0/lemonade/config.json' "${WORKDIR}/block.sh" \
    || fail "detection block missing lemonade config.json check"
grep -q 'HAL0_SKIP_V01_DETECT' "${WORKDIR}/block.sh" \
    || fail "detection block missing HAL0_SKIP_V01_DETECT escape hatch"

# ── 3. exercise the block with a swapped path prefix ─────────────────
# The block hard-codes /etc/hal0 + /var/lib/hal0/lemonade. We rewrite
# them to point into the WORKDIR so we can simulate each scenario
# without touching the live filesystem.
sed -e "s|/etc/hal0/slots|${WORKDIR}/etc/hal0/slots|g" \
    -e "s|/var/lib/hal0/lemonade/config.json|${WORKDIR}/var/lib/hal0/lemonade/config.json|g" \
    "${WORKDIR}/block.sh" > "${WORKDIR}/block-mocked.sh"

run_block() {
    # Run in a fresh subshell so HAL0_SKIP_V01_DETECT can be scoped per
    # scenario, and so we can capture exit + stderr.
    output_file="$1"
    skip="$2"
    (
        HAL0_SKIP_V01_DETECT="${skip}"
        export HAL0_SKIP_V01_DETECT
        bash "${WORKDIR}/block-mocked.sh"
    ) >"${WORKDIR}/stdout" 2>"${output_file}"
    return $?
}

reset_state() {
    rm -rf "${WORKDIR}/etc" "${WORKDIR}/var"
}

# Scenario 1: both v0.1.x markers (slot toml present, lemonade absent).
log "scenario 1: v0.1.x slot present, lemonade absent → refusal"
reset_state
mkdir -p "${WORKDIR}/etc/hal0/slots"
touch "${WORKDIR}/etc/hal0/slots/primary.toml"
if run_block "${WORKDIR}/stderr1" ""; then
    fail "scenario 1: expected non-zero exit, got 0"
fi
grep -q "hal0 v0.1.x detected" "${WORKDIR}/stderr1" \
    || fail "scenario 1: refusal message missing 'hal0 v0.1.x detected'"
grep -q "tar czf hal0-v0.1-backup" "${WORKDIR}/stderr1" \
    || fail "scenario 1: refusal message missing backup instructions"
grep -q "rm -rf /etc/hal0 /var/lib/hal0 /opt/hal0" "${WORKDIR}/stderr1" \
    || fail "scenario 1: refusal message missing wipe instructions"

# Scenario 2: partial v0.2 box (slot toml + lemonade config) → no refusal.
log "scenario 2: slot present + lemonade present → no refusal"
reset_state
mkdir -p "${WORKDIR}/etc/hal0/slots" "${WORKDIR}/var/lib/hal0/lemonade"
touch "${WORKDIR}/etc/hal0/slots/primary.toml"
touch "${WORKDIR}/var/lib/hal0/lemonade/config.json"
if ! run_block "${WORKDIR}/stderr2" ""; then
    cat "${WORKDIR}/stderr2" >&2
    fail "scenario 2: expected zero exit (lemonade present overrides), got non-zero"
fi

# Scenario 3: fresh box (no slot toml) → no refusal.
log "scenario 3: fresh box → no refusal"
reset_state
if ! run_block "${WORKDIR}/stderr3" ""; then
    cat "${WORKDIR}/stderr3" >&2
    fail "scenario 3: expected zero exit on fresh box, got non-zero"
fi

# Scenario 4: escape hatch — HAL0_SKIP_V01_DETECT=1 with v0.1.x markers.
log "scenario 4: HAL0_SKIP_V01_DETECT=1 bypasses refusal"
reset_state
mkdir -p "${WORKDIR}/etc/hal0/slots"
touch "${WORKDIR}/etc/hal0/slots/primary.toml"
if ! run_block "${WORKDIR}/stderr4" "1"; then
    cat "${WORKDIR}/stderr4" >&2
    fail "scenario 4: HAL0_SKIP_V01_DETECT=1 must skip refusal"
fi

log "OK — all 4 scenarios passed"
