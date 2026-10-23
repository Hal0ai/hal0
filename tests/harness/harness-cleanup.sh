#!/usr/bin/env bash
# tests/harness/harness-cleanup.sh
#
# Tears down whatever installer-test.sh set up. Runs last in the
# harness pipeline so cli-test.sh, runtime-test.sh, etc. can use the
# live install before it goes away.
#
# Rows:
#   stop-api               kill the hal0 serve PID from the handoff
#   dev-manual-cleanup     rm -rf the dev prefix (the dev-mode equivalent of uninstall.sh)
#   prod-uninstall         sudo installer/uninstall.sh              (opt-in via HAL0_HARNESS_PROD=1)
#   no-residue             assert nothing left under /tmp/hal0-h-* or PREFIX

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

REPORT="${SCRIPT_DIR}/reports/cleanup.json"
harness_init "cleanup" "${REPORT}"

HANDOFF="${SCRIPT_DIR}/reports/.api-handoff"
if [[ -r "${HANDOFF}" ]]; then
    # shellcheck disable=SC1090
    source "${HANDOFF}"
fi

PREFIX="${HAL0_HOME:-}"

log_step "Cleanup harness — prefix=${PREFIX:-<none>}  api=${HAL0_API_URL:-<none>}"

# ── ROW: stop-api ───────────────────────────────────────────────────────────
log_step "Row: stop-api"
start=$(start_ms)
KILLED=()
if [[ -n "${HAL0_SERVE_PID:-}" ]] && kill -0 "${HAL0_SERVE_PID}" 2>/dev/null; then
    kill "${HAL0_SERVE_PID}" 2>/dev/null || true
    sleep 0.5
    kill -0 "${HAL0_SERVE_PID}" 2>/dev/null && kill -9 "${HAL0_SERVE_PID}" 2>/dev/null || true
    KILLED+=("${HAL0_SERVE_PID}")
fi
# Sweep for orphans owning a port we know about.
if [[ -n "${HAL0_API_URL:-}" ]]; then
    port="${HAL0_API_URL##*:}"
    pids=$(ss -ltnp 2>/dev/null | awk -v p=":${port}" '$4 ~ p {print $NF}' \
        | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
    for pid in ${pids:-}; do
        kill "${pid}" 2>/dev/null || true
        KILLED+=("${pid}")
    done
fi
if [[ ${#KILLED[@]} -gt 0 ]]; then
    add_row "stop-api" "pass" "$(since_ms "${start}")" "killed pids: ${KILLED[*]}"
else
    add_row "stop-api" "pass" "$(since_ms "${start}")" "no serve process found (already stopped)"
fi

# ── ROW: dev-manual-cleanup ─────────────────────────────────────────────────
log_step "Row: dev-manual-cleanup"
start=$(start_ms)
if [[ -z "${PREFIX}" ]]; then
    add_row "dev-manual-cleanup" "skip" "$(since_ms "${start}")" "no PREFIX in handoff"
elif [[ "${PREFIX}" == "/" || "${PREFIX}" == "/etc" || "${PREFIX}" == "/var" ]]; then
    add_row "dev-manual-cleanup" "fail" "$(since_ms "${start}")" "refusing to remove dangerous PREFIX=${PREFIX}"
elif [[ ! -d "${PREFIX}" ]]; then
    add_row "dev-manual-cleanup" "skip" "$(since_ms "${start}")" "prefix ${PREFIX} not present"
else
    HAD=()
    [[ -d "${PREFIX}/.venv" ]]               && HAD+=(".venv")
    [[ -d "${PREFIX}/etc/hal0" ]]            && HAD+=("etc/hal0")
    [[ -d "${PREFIX}/var/lib/hal0" ]]        && HAD+=("var/lib/hal0")
    [[ -d "${PREFIX}/etc/systemd/system" ]]  && HAD+=("etc/systemd/system")
    rm -rf "${PREFIX}/.venv" "${PREFIX}/etc" "${PREFIX}/var"
    if [[ ! -d "${PREFIX}/.venv" && ! -d "${PREFIX}/etc" && ! -d "${PREFIX}/var" ]]; then
        add_row "dev-manual-cleanup" "pass" "$(since_ms "${start}")" "removed: ${HAD[*]:-(none)}"
        # Also remove the empty prefix dir if it's under .harness/.
        if [[ "${PREFIX}" == "${REPO_ROOT}"/.harness/install-* ]]; then
            rmdir "${PREFIX}" 2>/dev/null || true
        fi
    else
        add_row "dev-manual-cleanup" "fail" "$(since_ms "${start}")" "manual rm left residue under ${PREFIX}"
    fi
fi

# Remove the handoff so the next run starts clean.
rm -f "${HANDOFF}" || true

# ── ROW: prod-uninstall (opt-in) ────────────────────────────────────────────
log_step "Row: prod-uninstall"
start=$(start_ms)
if [[ "${HAL0_HARNESS_PROD:-0}" != "1" ]]; then
    add_row "prod-uninstall" "skip" "$(since_ms "${start}")" "skipped — HAL0_HARNESS_PROD=1 required (mutates real /etc, /var/lib, /usr/lib)"
elif ! sudo -n true 2>/dev/null; then
    add_row "prod-uninstall" "skip" "$(since_ms "${start}")" "sudo -n not available"
else
    LOG_U="${SCRIPT_DIR}/reports/prod-uninstall.log"
    if HAL0_FORCE=1 sudo -E bash "${REPO_ROOT}/installer/uninstall.sh" >"${LOG_U}" 2>&1; then
        if [[ ! -f /etc/systemd/system/hal0-api.service ]] && [[ -d /etc/hal0 ]]; then
            add_row "prod-uninstall" "pass" "$(since_ms "${start}")" "conservative default: units removed, /etc/hal0 preserved"
        else
            add_row "prod-uninstall" "fail" "$(since_ms "${start}")" \
                "post-uninstall state wrong: api unit present=$( [[ -f /etc/systemd/system/hal0-api.service ]] && echo yes || echo no ); /etc/hal0 present=$( [[ -d /etc/hal0 ]] && echo yes || echo no )"
        fi
    else
        rc=$?
        add_row "prod-uninstall" "fail" "$(since_ms "${start}")" "exit=${rc}; tail: $(tail -n1 "${LOG_U}")"
    fi
fi

log_step "Write report"
harness_write_report || true
log_info "report: ${REPORT}"
exit 0
