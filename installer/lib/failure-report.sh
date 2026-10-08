#!/usr/bin/env bash
# installer/lib/failure-report.sh
#
# Purpose: On installer failure, write one bounded, shareable
#          `hal0-install-report-<ts>.txt` an operator can attach to a bug
#          report — redacted env, `systemctl --failed`, `podman info` /
#          `podman images`, port owners, hal0 unit status, the redacted
#          api.env / hal0.toml, the hardware summary, a log tail, and the
#          hal0-api journal — instead of asking them to re-paste a
#          scrollback by hand.
# Expects: Called from install.sh's ERR trap (CURRENT_STEP, HAL0_INSTALL_LOG
#          from lib/logging.sh, ETC_DIR, and lib/ui.sh's warn()/err() are
#          already in scope by the time the trap fires). Works with none of
#          those set too — every field degrades to "unknown" rather than
#          erroring.
# Provides: hal0_write_failure_report(phase) -> prints the report path on
#           stdout and returns 0, or returns 1 if the report could not be
#           written anywhere.
# Modder notes:
#   Redaction runs in two passes over the WHOLE assembled report (#2307):
#     1. key-name pass on the structured sections (env, api.env, hal0.toml),
#        a bash mirror of src/hal0/api/_redact.py's _SENSITIVE_RE — the two
#        must be kept in sync by hand; tests/installer/test_failure_report.py
#        (test_redaction_matches_the_python_pattern) pins both against the
#        same fixture set so a drift is caught in CI;
#     2. a text pass over everything: every literal value of a sensitive key
#        (shell vars, api.env, hal0.toml, any NAME=value in the report
#        itself) is replaced wherever it appears, then secret SHAPES
#        (Bearer/Basic auth, URL credentials, NAME=value, --token flags,
#        hf_/sk-/ghp_/JWT tokens) are masked.
#   It fails closed: if the text pass cannot run, or a known literal
#   survives it, the diagnostic body is discarded and only a stub saying so
#   is written. Every diagnostic subprocess runs under `timeout`, so a wedged
#   podman/systemd cannot hang the ERR trap; a timeout is recorded in the
#   report as evidence.

# shellcheck shell=bash

[[ -n "${_HAL0_FAILURE_REPORT_SH_LOADED:-}" ]] && return 0
_HAL0_FAILURE_REPORT_SH_LOADED=1

if ! command -v warn >/dev/null 2>&1; then
    warn() { printf 'WARN  %s\n' "$*" >&2; }
fi

# Same sentinel as hal0.redaction.MASK.
_HAL0_REPORT_MASK='***REDACTED***'

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
# projection, adapted to flat env-file text). An `export ` prefix (as an
# api.env line may carry) is kept.
_hal0_report_redact_env_stream() {
    local line key prefix
    while IFS= read -r line; do
        if [[ "$line" =~ ^(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)= ]]; then
            # Copy the captures first: the sensitivity check's own =~
            # overwrites BASH_REMATCH.
            prefix="${BASH_REMATCH[1]}"
            key="${BASH_REMATCH[2]}"
            if _hal0_report_key_is_sensitive "$key"; then
                printf '%s%s=%s\n' "$prefix" "$key" "$_HAL0_REPORT_MASK"
                continue
            fi
        fi
        printf '%s\n' "$line"
    done
}

# Same key-name pass for TOML (`key = value`, dotted / quoted keys judged by
# their last segment).
_hal0_report_redact_toml_stream() {
    local line key lead name sep
    while IFS= read -r line; do
        if [[ "$line" =~ ^([[:space:]]*)([A-Za-z0-9_.\"\'-]+)([[:space:]]*=) ]]; then
            lead="${BASH_REMATCH[1]}" name="${BASH_REMATCH[2]}" sep="${BASH_REMATCH[3]}"
            key="${name##*.}"
            key="${key//\"/}"
            key="${key//\'/}"
            if _hal0_report_key_is_sensitive "$key"; then
                printf '%s%s%s "%s"\n' "$lead" "$name" "$sep" "$_HAL0_REPORT_MASK"
                continue
            fi
        fi
        printf '%s\n' "$line"
    done
}

# ── literal-secret harvesting (pass 2a) ─────────────────────────────────────

# Print each line of a secret value worth masking: ≥4 chars, not a plain
# boolean/null word, not a substring of the mask itself. Multi-line values
# (a PEM key in an env var) are split so every line is masked on its own.
_hal0_report_emit_secret() {
    local value="$1" line
    while IFS= read -r line; do
        line="${line%$'\r'}"
        [[ ${#line} -ge 4 ]] || continue
        case "${line,,}" in
            true | false | none | null | unset) continue ;;
        esac
        [[ "$_HAL0_REPORT_MASK" == *"$line"* ]] && continue
        printf '%s\n' "$line"
    done <<<"$value"
}

# Strip one matching pair of surrounding quotes.
_hal0_report_unquote() {
    local v="$1"
    if [[ ${#v} -ge 2 && ( "$v" == \"*\" || "$v" == \'*\' ) ]]; then
        v="${v:1:${#v}-2}"
    fi
    printf '%s' "$v"
}

# Values of every sensitive-named shell variable (exported or not — the
# installer may hold a credential in a plain shell var).
_hal0_report_harvest_shell_vars() {
    local name
    while IFS= read -r name; do
        _hal0_report_key_is_sensitive "$name" || continue
        _hal0_report_emit_secret "${!name-}"
    done < <(compgen -v)
}

# Values of sensitive keys in an env-style file (`[export ]KEY=value`).
_hal0_report_harvest_env_file() {
    local file="$1" line raw
    [[ -r "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            raw="${BASH_REMATCH[3]}"
            _hal0_report_key_is_sensitive "${BASH_REMATCH[2]}" || continue
            _hal0_report_emit_secret "$raw"
            _hal0_report_emit_secret "$(_hal0_report_unquote "$raw")"
        fi
    done <"$file"
}

# Values of sensitive keys in a TOML file (`key = "value"` / bare value).
_hal0_report_harvest_toml_file() {
    local file="$1" line key raw
    [[ -r "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^[[:space:]]*([A-Za-z0-9_.\"\'-]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
            key="${BASH_REMATCH[1]##*.}"
            raw="${BASH_REMATCH[2]}"
            key="${key//\"/}"
            key="${key//\'/}"
            _hal0_report_key_is_sensitive "$key" || continue
            if [[ "$raw" =~ ^\"([^\"]*)\" || "$raw" =~ ^\'([^\']*)\' ]]; then
                raw="${BASH_REMATCH[1]}"
            else
                raw="${raw%%#*}"
                raw="${raw%%[[:space:]]*}"
            fi
            _hal0_report_emit_secret "$raw"
        fi
    done <"$file"
}

# True if NAME=VALUE seen in free report text is worth harvesting as a
# literal. The harvested value is masked as a substring EVERYWHERE, so a
# value that is not a plausible secret would erase unrelated text: a number
# (`max_tokens=4096` turned "port 14096" into "port 1***REDACTED***"), a
# short word, or an env-var name (`key=OPENAI_API_KEY` in a structlog line
# masked that name throughout the report). The bare field name `key` is
# skipped too: in log text it names a setting, not a credential. This is
# deliberately narrower than _hal0_report_key_is_sensitive (which keeps
# _redact.py's ^KEY$); the pattern pass still masks the value on its own
# line, and the env/api.env/TOML harvests are unaffected.
_hal0_report_text_value_is_secret() {
    local name="$1" value="$2"
    [[ "${name,,}" == key ]] && return 1
    [[ ${#value} -ge 8 ]] || return 1
    [[ "$value" =~ ^[0-9]+$ ]] && return 1
    [[ "$value" =~ ^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$ ]] && return 1
    return 0
}

# Values of every `SENSITIVE_NAME=value` that appears anywhere in the
# assembled report (an echoed export, a command line), so the same value is
# also masked where it later appears bare (in a URL, a journal line). Only
# plausible secrets are harvested (_hal0_report_text_value_is_secret).
# Returns non-zero only if grep itself errored (rc 2) — no match is fine.
_hal0_report_harvest_report_text() {
    local file="$1" hits rc=0 hit name value
    hits="$(LC_ALL=C grep -oiE \
        '(^|[^A-Za-z0-9_])([A-Za-z0-9_]*(SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT)[A-Za-z0-9_]*|([A-Za-z0-9_]*_)?KEY)["'\'']?[[:space:]]*=[[:space:]]*["'\'']?[^"'\''[:space:],}&]+' \
        "$file")" || rc=$?
    [[ $rc -le 1 ]] || return 1
    while IFS= read -r hit; do
        [[ "$hit" =~ ([A-Za-z0-9_]+)[\"\']?[[:space:]]*=[[:space:]]*[\"\']?(.*)$ ]] || continue
        name="${BASH_REMATCH[1]}" value="${BASH_REMATCH[2]}"
        _hal0_report_text_value_is_secret "$name" "$value" || continue
        _hal0_report_emit_secret "$value"
    done <<<"$hits"
    return 0
}

# ── text redaction (pass 2b) ────────────────────────────────────────────────

# stdin -> stdout: replace every literal listed (one per line) in $1,
# longest first so a secret containing another is masked whole. index()
# based, so no secret is ever interpreted as a regex. Exits non-zero if the
# secrets file cannot be read.
_hal0_report_mask_literals() {
    LC_ALL=C awk -v secfile="$1" -v mask="$_HAL0_REPORT_MASK" '
        BEGIN {
            n = 0
            while ((rc = (getline s < secfile)) > 0) {
                if (s != "") sec[++n] = s
            }
            if (rc < 0) exit 3
            close(secfile)
            for (i = 2; i <= n; i++) {
                v = sec[i]; j = i - 1
                while (j > 0 && length(sec[j]) < length(v)) { sec[j + 1] = sec[j]; j-- }
                sec[j + 1] = v
            }
        }
        {
            line = $0
            for (i = 1; i <= n; i++) {
                s = sec[i]; L = length(s); out = ""
                while ((p = index(line, s)) > 0) {
                    out = out substr(line, 1, p - 1) mask
                    line = substr(line, p + L)
                }
                line = out line
            }
            print line
        }'
}

# stdin -> stdout: mask secret SHAPES (no literal known in advance). Mirrors
# hal0.redaction.LOG_SECRET_RE and extends it for a shareable file: quoted
# and unquoted NAME=value / NAME: value, Basic/token auth, URL credentials,
# --token-style flags, and well-known token prefixes. Case-insensitive.
_hal0_report_mask_patterns() {
    local m="$_HAL0_REPORT_MASK"
    local name='[A-Za-z0-9_]*(SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT)[A-Za-z0-9_]*|([A-Za-z0-9_]*_)?KEY'
    local pre="((^|[^A-Za-z0-9_])(${name})[\"']?[[:space:]]*[=:][[:space:]]*)"
    LC_ALL=C sed -E \
        -e "s#(authorization:[[:space:]]*(basic|token)[[:space:]]+)[^[:space:]'\"]+#\\1${m}#gI" \
        -e "s#(bearer[[:space:]]+)[A-Za-z0-9._~+/=-]+#\\1${m}#gI" \
        -e "s#([a-z][a-z0-9+.-]*://[^/@[:space:]:]*):[^/@[:space:]]+@#\\1:${m}@#gI" \
        -e "s#([a-z][a-z0-9+.-]*://)[^/@[:space:]:]{16,}@#\\1${m}@#gI" \
        -e "s#${pre}\"[^\"]*\"#\\1\"${m}\"#gI" \
        -e "s#${pre}'[^']*'#\\1'${m}'#gI" \
        -e "s#${pre}[^\"'[:space:],}&]+#\\1${m}#gI" \
        -e "s#(--[A-Za-z0-9-]*(secret|token|password|pass|api-key|apikey|private-key)[A-Za-z0-9-]*(=|[[:space:]]+))[^-[:space:]][^[:space:]]*#\\1${m}#gI" \
        -e "s#(client_id=)[A-Za-z0-9_.-]{16,}#\\1${m}#gI" \
        -e "s#eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}#${m}#g" \
        -e "s#(hf_|sk-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{20,}#${m}#g" \
        -e "s#AKIA[0-9A-Z]{16}#${m}#g"
}

# _hal0_report_redact_file SECRETS IN OUT WORKDIR — run the text pass and
# verify no harvested literal survived it. Any failure returns non-zero and
# the caller discards the body (fail closed).
_hal0_report_redact_file() {
    local sec="$1" in="$2" out="$3" work="$4" rc=0
    _hal0_report_mask_literals "$sec" <"$in" >"${work}/mid" || return 1
    _hal0_report_mask_patterns <"${work}/mid" >"$out" || return 1
    [[ -s "$out" ]] || return 1
    if [[ -s "$sec" ]]; then
        LC_ALL=C grep -qF -f "$sec" "$out" || rc=$?
        # 1 == no literal found (good); 0 == a literal survived; 2 == error.
        [[ $rc -eq 1 ]] || return 1
    fi
    return 0
}

# ── bounded diagnostics ─────────────────────────────────────────────────────

_hal0_report_have_timeout() { command -v timeout >/dev/null 2>&1; }

# _hal0_report_probe OUTFILE cmd... — run cmd under `timeout`, its output
# (capped at 200 lines) and a one-line verdict printed on stdout. Output goes
# through a file, not a pipe, so a grandchild that keeps the fd open cannot
# stall us after the timeout fires. Never fails.
_hal0_report_probe() {
    local tmp="$1"
    shift
    local secs="${HAL0_REPORT_PROBE_TIMEOUT:-10}" rc=0
    echo "\$ $*"
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "[$1 not found on this host]"
        return 0
    fi
    if ! _hal0_report_have_timeout; then
        echo "[timeout(1) unavailable; $1 not run so a wedged $1 cannot hang the failure trap]"
        return 0
    fi
    timeout -k 2 "$secs" "$@" </dev/null >"$tmp" 2>&1 || rc=$?
    head -n 200 "$tmp" 2>/dev/null || true
    case "$rc" in
        0) ;;
        124 | 137) echo "[timed out after ${secs}s: $*]" ;;
        *) echo "[exit ${rc}: $*]" ;;
    esac
    return 0
}

_hal0_report_port_line() {
    local label="$1" port="$2" ss_out="$3"
    local detail=""
    if [[ -s "$ss_out" ]]; then
        detail="$(awk -v p=":${port}\$" '$4 ~ p {print}' "$ss_out" 2>/dev/null || true)"
    fi
    if [[ -n "$detail" ]]; then
        printf -- '- %s :%s occupied\n%s\n' "$label" "$port" "$(printf '%s\n' "$detail" | sed 's/^/    /')"
    else
        printf -- '- %s :%s free (or ss unavailable / needs root to show the owner)\n' "$label" "$port"
    fi
}

_hal0_report_body() {
    local phase="$1" work="$2"
    local etc="${ETC_DIR:-/etc/hal0}"
    echo "hal0 install failure report"
    echo "Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "Phase: ${phase}"
    echo ""
    echo "Privacy note"
    echo "- Values of secret-named keys (SECRET/TOKEN/PASSWORD/PASS/API_KEY/...KEY) are"
    echo "  redacted, and every such value is also masked wherever else it appears"
    echo "  (log tail, journal, command lines, URLs), as are Bearer tokens and URL"
    echo "  credentials. Redaction is pattern-based: review before posting publicly."
    echo ""
    echo "Summary"
    echo "- hal0 version: $(_ui_read_version 2>/dev/null || echo unknown)"
    echo "- Install log: ${HAL0_INSTALL_LOG:-none}"
    echo "- Prefix: ${LIB_DIR:-${HAL0_PREFIX:-unknown}}"
    echo ""
    echo "Environment (redacted)"
    env | sort | _hal0_report_redact_env_stream
    echo ""
    echo "systemctl --failed"
    _hal0_report_probe "${work}/probe" systemctl --failed --no-pager
    echo ""
    echo "podman info"
    _hal0_report_probe "${work}/probe" podman info
    echo ""
    echo "podman images"
    _hal0_report_probe "${work}/probe" podman images
    echo ""
    echo "Port owners"
    _hal0_report_probe "${work}/ss" ss -ltnp >/dev/null
    _hal0_report_port_line "hal0-api" "${HAL0_PORT:-8080}" "${work}/ss"
    _hal0_report_port_line "hal0-openwebui" "3001" "${work}/ss"
    echo ""
    echo "hal0 systemd units"
    _hal0_report_probe "${work}/probe" systemctl status --no-pager -l 'hal0-api' 'hal0-openwebui' 'hal0.target'
    echo ""
    echo "api.env (redacted) — ${etc}/api.env"
    if [[ -r "${etc}/api.env" ]]; then
        _hal0_report_redact_env_stream <"${etc}/api.env"
    else
        echo "not present"
    fi
    echo ""
    echo "hal0.toml (redacted) — ${etc}/hal0.toml"
    if [[ -r "${etc}/hal0.toml" ]]; then
        _hal0_report_redact_toml_stream <"${etc}/hal0.toml"
    else
        echo "not present"
    fi
    echo ""
    echo "Hardware probe (${etc}/hardware.json)"
    if [[ -f "${etc}/hardware.json" ]]; then
        cat "${etc}/hardware.json"
    else
        echo "not present"
    fi
    echo ""
    echo "Install log tail (last 160 lines)"
    if [[ -n "${HAL0_INSTALL_LOG:-}" && -f "${HAL0_INSTALL_LOG}" ]]; then
        tail -n 160 "${HAL0_INSTALL_LOG}"
    else
        echo "install log unavailable"
    fi
    echo ""
    echo "journalctl -u hal0-api -n 100"
    _hal0_report_probe "${work}/probe" journalctl -u hal0-api -n 100 --no-pager
}

# Written instead of the body when redaction cannot be trusted.
_hal0_report_stub() {
    local phase="$1"
    echo "hal0 install failure report"
    echo "Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "Phase: ${phase}"
    echo ""
    echo "Privacy note"
    echo "- Secret redaction could not run cleanly on this host, so the diagnostic"
    echo "  body (environment, config, log tail, journal) was discarded rather than"
    echo "  written unredacted. Attach this file and describe the failure instead."
    echo "- The full install log is ${HAL0_INSTALL_LOG:-unavailable}. It is NOT redacted:"
    echo "  do not post it publicly."
}

# Runs in a subshell so its umask and temp state never leak into install.sh.
hal0_write_failure_report() (
    phase="${1:-unknown}"
    umask 077
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

    work="$(mktemp -d "${dir}/.hal0-install-report.XXXXXX" 2>/dev/null)" || work=""
    if [[ -n "$work" ]]; then
        trap 'rm -rf "$work"' EXIT
        _hal0_report_body "$phase" "$work" >"${work}/raw" 2>&1 || true
        ok=0
        {
            _hal0_report_harvest_shell_vars
            _hal0_report_harvest_env_file "${ETC_DIR:-/etc/hal0}/api.env"
            _hal0_report_harvest_toml_file "${ETC_DIR:-/etc/hal0}/hal0.toml"
        } >"${work}/secrets" 2>/dev/null || ok=1
        _hal0_report_harvest_report_text "${work}/raw" >>"${work}/secrets" 2>/dev/null || ok=1
        if [[ $ok -eq 0 ]] &&
            _hal0_report_redact_file "${work}/secrets" "${work}/raw" "${work}/out" "$work" 2>/dev/null &&
            mv -f "${work}/out" "$report" 2>/dev/null; then
            :
        else
            _hal0_report_stub "$phase" >"$report" 2>/dev/null || true
        fi
    else
        _hal0_report_stub "$phase" >"$report" 2>/dev/null || true
    fi

    if [[ -s "$report" ]]; then
        chmod 0600 "$report" 2>/dev/null || true
        printf '%s\n' "$report"
        exit 0
    fi
    exit 1
)
