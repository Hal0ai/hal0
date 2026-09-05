#!/usr/bin/env bash
# installer/lib/failure-report.sh
#
# Purpose: On installer failure, write one bounded, shareable
#          `hal0-install-report-<ts>.txt` an operator can attach to a bug
#          report — redacted env, port owners, hal0 unit status, a log
#          tail, and a hardware summary — instead of asking them to
#          re-paste a scrollback by hand.
# Expects: Called from install.sh's ERR trap (CURRENT_STEP, HAL0_INSTALL_LOG
#          from lib/logging.sh, and lib/ui.sh's warn()/err() are already in
#          scope by the time the trap fires). Works with none of those set
#          too — every field degrades to "unknown" rather than erroring.
# Provides: hal0_write_failure_report(phase) -> prints the report path on
#           stdout and returns 0, or returns 1 if the report could not be
#           written anywhere.
# Modder notes:
#   The redaction list here is a bash mirror of
#   src/hal0/api/_redact.py's _SENSITIVE_RE key-name pattern — the two
#   must be kept in sync by hand (there is no shared source between a
#   Python regex and a bash one); test_failure_report_redaction.py pins
#   both against the same fixture set so a drift is caught in CI.

# shellcheck shell=bash

[[ -n "${_HAL0_FAILURE_REPORT_SH_LOADED:-}" ]] && return 0
_HAL0_FAILURE_REPORT_SH_LOADED=1

if ! command -v warn >/dev/null 2>&1; then
    warn() { printf 'WARN  %s\n' "$*" >&2; }
fi

# Mirrors hal0.api._redact._SENSITIVE_RE: SECRET|TOKEN|PASSWORD|PASS|
# API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT|_KEY$|^KEY$ (case-insensitive).
_hal0_report_key_is_sensitive() {
    local key="$1"
    shopt -s nocasematch
    local hit=1
    if [[ "$key" =~ (SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT|_KEY$|^KEY$) ]]; then
        hit=0
    fi
    shopt -u nocasematch
    return $hit
}

# Redact a KEY=value env-style stream on stdin: sensitive keys keep their
# name but lose their value (matches _redact.py's {value: "***REDACTED***"}
# projection, adapted to flat env-file text).
_hal0_report_redact_env_stream() {
    local line key
    while IFS= read -r line; do
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
            key="${BASH_REMATCH[1]}"
            if _hal0_report_key_is_sensitive "$key"; then
                printf '%s=***REDACTED***\n' "$key"
                continue
            fi
        fi
        printf '%s\n' "$line"
    done
}

_hal0_report_port_line() {
    local label="$1" port="$2"
    local detail=""
    if command -v ss >/dev/null 2>&1; then
        detail="$(ss -ltnp 2>/dev/null | awk -v p=":${port}\$" '$4 ~ p {print}')"
    fi
    if [[ -n "$detail" ]]; then
        printf -- '- %s :%s occupied\n%s\n' "$label" "$port" "$(printf '%s\n' "$detail" | sed 's/^/    /')"
    else
        printf -- '- %s :%s free (or ss unavailable / needs root to show the owner)\n' "$label" "$port"
    fi
}

hal0_write_failure_report() {
    local phase="${1:-unknown}"
    local dir stamp report
    stamp="$(date -u +%Y%m%d-%H%M%S)"
    if [[ -n "${HAL0_INSTALL_LOG:-}" ]]; then
        dir="$(dirname "${HAL0_INSTALL_LOG}")"
    elif [[ "$(id -u)" -eq 0 ]]; then
        dir="/var/log/hal0"
    else
        dir="/tmp"
    fi
    mkdir -p "$dir" 2>/dev/null || dir="/tmp"
    report="${dir}/hal0-install-report-${stamp}.txt"

    {
        echo "hal0 install failure report"
        echo "Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        echo "Phase: ${phase}"
        echo ""
        echo "Privacy note"
        echo "- Environment values whose KEY name looks like a secret are redacted."
        echo "- Review before posting publicly."
        echo ""
        echo "Summary"
        echo "- hal0 version: $(_ui_read_version 2>/dev/null || echo unknown)"
        echo "- Install log: ${HAL0_INSTALL_LOG:-none}"
        echo "- Prefix: ${LIB_DIR:-${HAL0_PREFIX:-unknown}}"
        echo ""
        echo "Environment (redacted)"
        env | sort | _hal0_report_redact_env_stream
        echo ""
        echo "Port owners"
        _hal0_report_port_line "hal0-api" "${HAL0_PORT:-8080}"
        _hal0_report_port_line "hal0-openwebui" "3001"
        echo ""
        echo "hal0 systemd units"
        if command -v systemctl >/dev/null 2>&1; then
            systemctl status --no-pager -l 'hal0-api' 'hal0-openwebui' 'hal0.target' 2>&1 | sed -n '1,120p'
        else
            echo "systemctl not found (--dev / non-systemd host)"
        fi
        echo ""
        echo "Hardware probe (/etc/hal0/hardware.json)"
        if [[ -f /etc/hal0/hardware.json ]]; then
            cat /etc/hal0/hardware.json
        else
            echo "not present"
        fi
        echo ""
        echo "Install log tail (last 200 lines)"
        if [[ -n "${HAL0_INSTALL_LOG:-}" && -f "${HAL0_INSTALL_LOG}" ]]; then
            tail -n 200 "${HAL0_INSTALL_LOG}"
        else
            echo "install log unavailable"
        fi
    } >"$report" 2>&1

    if [[ -s "$report" ]]; then
        printf '%s\n' "$report"
        return 0
    fi
    return 1
}
