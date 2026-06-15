#!/usr/bin/env bash
# tests/harness/installer-test.sh
#
# Drives the installer surface as a series of black-box scenarios. Each
# scenario lands one row in tests/harness/reports/installer.json using
# the shared hal0.harness-report.v1 schema.
#
# Scenarios:
#   dev-install        bash installer/install.sh --dev   (under tmp prefix)
#   dev-idempotent     re-run --dev install on same prefix; expect no-op
#   dev-files          assert filesystem layout
#   dev-units          assert systemd unit files were rendered (under prefix)
#   dev-api-up         start hal0 serve manually, hit /api/status
#   dev-uninstall-keep bash installer/uninstall.sh --keep-data (forced)
#   dev-uninstall-purge bash installer/uninstall.sh           (forced, full)
#   prod-no-start      sudo bash installer/install.sh --no-start
#                      (only if HAL0_HARNESS_PROD=1 — opt-in, mutates /etc)
#   tls-default        default install (no flag) — Caddy installed, the
#                      rendered Caddyfile is the ADR-0001-Child-B minimal
#                      form (TLS-only, no basicauth, no @public matcher).
#                      Opt-in via HAL0_HARNESS_PROD=1 + HAL0_HARNESS_TLS=1.
#   no-tls             --no-tls install — no Caddy, no Caddyfile, hal0-api
#                      unit binds 0.0.0.0:8080, /api/auth/status reports
#                      auth_mode=open. Opt-in via HAL0_HARNESS_PROD=1.
#
# Env knobs:
#   HAL0_HARNESS_PREFIX    tmp prefix root (default $REPO_ROOT/.harness/install-$$)
#   HAL0_HARNESS_PROD      1 to run prod-level (sudo) scenarios
#   HAL0_HARNESS_TLS       1 to run the tls-default scenario (also installs caddy)
#   HAL0_HARNESS_KEEP      1 to keep the tmp prefix after run (for debugging)
#
# Exit:
#   0   no FAIL rows (skip / deferred ok)
#   N   N FAIL rows

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

REPORT="${SCRIPT_DIR}/reports/installer.json"
harness_init "installer" "${REPORT}"

PREFIX="${HAL0_HARNESS_PREFIX:-${REPO_ROOT}/.harness/install-$$}"
KEEP="${HAL0_HARNESS_KEEP:-0}"

cleanup() {
    # NEVER kill the serve we started, NEVER remove the prefix in
    # default mode. The orchestrator (scripts/harness.sh) calls
    # harness-cleanup.sh as a final stage; downstream tiers
    # (cli-test.sh, runtime-test.sh) need the API + venv to stay up.
    # Honour HAL0_HARNESS_AUTOCLEAN=1 for standalone runs only.
    if [[ "${HAL0_HARNESS_AUTOCLEAN:-0}" -eq 1 ]]; then
        if [[ -n "${HAL0_SERVE_PID:-}" ]] && kill -0 "${HAL0_SERVE_PID}" 2>/dev/null; then
            kill "${HAL0_SERVE_PID}" 2>/dev/null || true
            wait "${HAL0_SERVE_PID}" 2>/dev/null || true
        fi
        if [[ "${KEEP}" -ne 1 && -d "${PREFIX}" ]]; then
            rm -rf "${PREFIX}"
        fi
    fi
}
trap cleanup EXIT

mkdir -p "${PREFIX}"
log_step "Installer harness — prefix=${PREFIX}"

# ── ROW: dev-install ─────────────────────────────────────────────────────────
log_step "Row: dev-install"
start=$(start_ms)
LOG="${PREFIX}/install-1.log"
# Run --dev install. Force HAL0_PREFIX so we get a clean dir under our
# tmp; suppress the hardware probe so this is hardware-independent.
if HAL0_PREFIX="${PREFIX}" HAL0_NO_PROBE=1 HAL0_PLAIN=1 HAL0_NO_HELLO=1 HAL0_NO_QR=1 \
    bash "${REPO_ROOT}/installer/install.sh" --dev >"${LOG}" 2>&1; then
    add_row "dev-install" "pass" "$(since_ms "${start}")" "installer/install.sh --dev exited 0 (log: ${LOG})"
else
    rc=$?
    add_row "dev-install" "fail" "$(since_ms "${start}")" "exit=${rc}; tail: $(tail -n1 "${LOG}" 2>/dev/null | tr -d '\n')"
fi

# ── ROW: dev-files ───────────────────────────────────────────────────────────
log_step "Row: dev-files"
start=$(start_ms)
MISSING=()
for p in \
    ".venv/bin/hal0" \
    "etc/hal0/hal0.toml" \
    "etc/hal0/api.env" \
    "etc/hal0/upstreams.toml" \
    "etc/hal0/openwebui.env" \
    "var/lib/hal0/models" \
    "var/lib/hal0/registry" \
    "var/lib/hal0/slots" \
    "var/lib/hal0/openwebui" \
    "etc/systemd/system/hal0-api.service" \
    "etc/systemd/system/hal0-slot@.service" \
    "etc/systemd/system/hal0-openwebui.service"; do
    if [[ ! -e "${PREFIX}/${p}" ]]; then
        MISSING+=("${p}")
    fi
done
if [[ ${#MISSING[@]} -eq 0 ]]; then
    add_row "dev-files" "pass" "$(since_ms "${start}")" "all expected paths present under ${PREFIX}"
else
    add_row "dev-files" "fail" "$(since_ms "${start}")" "missing: ${MISSING[*]}"
fi

# ── ROW: dev-units ───────────────────────────────────────────────────────────
log_step "Row: dev-units"
start=$(start_ms)
API_UNIT="${PREFIX}/etc/systemd/system/hal0-api.service"
SLOT_UNIT="${PREFIX}/etc/systemd/system/hal0-slot@.service"
if [[ -f "${API_UNIT}" ]] \
    && grep -q "ExecStart" "${API_UNIT}" \
    && grep -q "${PREFIX}" "${API_UNIT}"; then
    if [[ -f "${SLOT_UNIT}" ]] && grep -q "ExecStart" "${SLOT_UNIT}"; then
        add_row "dev-units" "pass" "$(since_ms "${start}")" "api + slot template render with prefix-relative paths"
    else
        add_row "dev-units" "fail" "$(since_ms "${start}")" "slot template missing or empty ExecStart"
    fi
else
    add_row "dev-units" "fail" "$(since_ms "${start}")" "api unit missing or doesn't reference prefix ${PREFIX}"
fi

# ── ROW: dev-config-validate ────────────────────────────────────────────────
log_step "Row: dev-config-validate"
start=$(start_ms)
HAL0_BIN="${PREFIX}/.venv/bin/hal0"
if [[ -x "${HAL0_BIN}" ]]; then
    VAL_LOG="${PREFIX}/config-validate.log"
    if HAL0_HOME="${PREFIX}" "${HAL0_BIN}" config validate >"${VAL_LOG}" 2>&1; then
        add_row "dev-config-validate" "pass" "$(since_ms "${start}")" "config validate against rendered /etc/hal0 returned 0"
    else
        rc=$?
        # Surface the ImportError / traceback summary so the report has root-cause text.
        DETAIL="$(grep -oE 'ImportError: [^"]+' "${VAL_LOG}" | head -n1 || true)"
        if [[ -z "${DETAIL}" ]]; then
            DETAIL="$(tail -n1 "${VAL_LOG}" 2>/dev/null | tr -d '\n')"
        fi
        add_row "dev-config-validate" "fail" "$(since_ms "${start}")" "exit=${rc}: ${DETAIL}"
    fi
else
    add_row "dev-config-validate" "skip" "$(since_ms "${start}")" "hal0 binary not built at ${HAL0_BIN}"
fi

# ── ROW: dev-setup-sentinel ─────────────────────────────────────────────────
# Verify that `hal0 setup --auto --no-pull --no-extensions` (Task 5.1) writes
# the first-run sentinel (/var/lib/hal0/.first_run_done). A Main slot config
# (/etc/hal0/slots/chat.toml) is also expected on hardware with a supported
# GPU; on CI/VM boxes with no compatible GPU the slot creation is skipped by
# apply_setup (device/profile mismatch) but the sentinel is always written.
# The dev-install row above skips the setup block via HAL0_NO_PROBE=1; we
# exercise it explicitly here against the already-installed binary using
# HAL0_HOME so paths resolve under the tmp PREFIX (not /etc or /var/lib).
log_step "Row: dev-setup-sentinel"
start=$(start_ms)
HAL0_BIN="${PREFIX}/.venv/bin/hal0"
if [[ -x "${HAL0_BIN}" ]]; then
    SETUP_LOG="${PREFIX}/setup-auto.log"
    if HAL0_HOME="${PREFIX}" "${HAL0_BIN}" setup --auto --no-pull --no-extensions \
        --storage-dir "${PREFIX}/var-lib/hal0/models" >"${SETUP_LOG}" 2>&1; then
        SENTINEL="${PREFIX}/var-lib/hal0/.first_run_done"
        CHAT_TOML="${PREFIX}/etc/hal0/slots/chat.toml"
        if [[ -f "${SENTINEL}" ]]; then
            # Sentinel written — core requirement met. Report chat.toml status.
            if [[ -f "${CHAT_TOML}" ]]; then
                add_row "dev-setup-sentinel" "pass" "$(since_ms "${start}")" \
                    "'hal0 setup --auto --no-pull --no-extensions' wrote sentinel + chat.toml"
            else
                # No GPU on this host → slot skipped; sentinel still written.
                add_row "dev-setup-sentinel" "pass" "$(since_ms "${start}")" \
                    "sentinel written; chat.toml absent (no compatible GPU on this host — expected on CI/VM)"
            fi
        else
            add_row "dev-setup-sentinel" "fail" "$(since_ms "${start}")" \
                "setup exited 0 but sentinel missing: ${SENTINEL}"
        fi
    else
        rc=$?
        add_row "dev-setup-sentinel" "fail" "$(since_ms "${start}")" \
            "hal0 setup --auto --no-pull --no-extensions exit=${rc}; tail: $(tail -n1 "${SETUP_LOG}" 2>/dev/null | tr -d '\n')"
    fi
else
    add_row "dev-setup-sentinel" "skip" "$(since_ms "${start}")" \
        "hal0 binary not built at ${HAL0_BIN} — earlier row failed"
fi

# ── ROW: dev-idempotent ─────────────────────────────────────────────────────
log_step "Row: dev-idempotent"
start=$(start_ms)
# Snapshot mtimes of config files we expect to be left alone.
declare -A MTIMES_BEFORE
for f in etc/hal0/hal0.toml etc/hal0/api.env etc/hal0/upstreams.toml; do
    if [[ -f "${PREFIX}/${f}" ]]; then
        MTIMES_BEFORE["${f}"]="$(stat -c %Y "${PREFIX}/${f}")"
    fi
done
LOG2="${PREFIX}/install-2.log"
if HAL0_PREFIX="${PREFIX}" HAL0_NO_PROBE=1 HAL0_PLAIN=1 HAL0_NO_HELLO=1 HAL0_NO_QR=1 \
    bash "${REPO_ROOT}/installer/install.sh" --dev >"${LOG2}" 2>&1; then
    # Walk mtimes; any change to existing config = idempotency miss.
    CHANGED=()
    for f in "${!MTIMES_BEFORE[@]}"; do
        new="$(stat -c %Y "${PREFIX}/${f}" 2>/dev/null || echo 0)"
        if [[ "${MTIMES_BEFORE[$f]}" != "${new}" ]]; then
            CHANGED+=("${f}")
        fi
    done
    if [[ ${#CHANGED[@]} -eq 0 ]]; then
        add_row "dev-idempotent" "pass" "$(since_ms "${start}")" "re-run preserved config mtimes"
    else
        add_row "dev-idempotent" "fail" "$(since_ms "${start}")" "config mtimes changed on re-run: ${CHANGED[*]}"
    fi
else
    rc=$?
    add_row "dev-idempotent" "fail" "$(since_ms "${start}")" "second --dev run exit=${rc}; tail: $(tail -n1 "${LOG2}" 2>/dev/null | tr -d '\n')"
fi

# ── ROW: dev-api-up ─────────────────────────────────────────────────────────
log_step "Row: dev-api-up"
start=$(start_ms)
if [[ -x "${HAL0_BIN}" ]]; then
    # Pick a free port (default 8080 may be in use on dev box).
    API_PORT="${HAL0_HARNESS_API_PORT:-18080}"
    SERVE_LOG="${PREFIX}/serve.log"
    HAL0_HOME="${PREFIX}" "${HAL0_BIN}" serve --host 127.0.0.1 --port "${API_PORT}" \
        >"${SERVE_LOG}" 2>&1 &
    HAL0_SERVE_PID=$!
    # Poll for up to 15s.
    UP=0
    for _ in $(seq 1 30); do
        if curl -fsS -m 1 "http://127.0.0.1:${API_PORT}/api/status" >/dev/null 2>&1; then
            UP=1; break
        fi
        sleep 0.5
    done
    if [[ "${UP}" -eq 1 ]]; then
        add_row "dev-api-up" "pass" "$(since_ms "${start}")" "hal0 serve --port ${API_PORT} responded /api/status"
        # Persist the port + pid for cli-test.sh to pick up.
        printf 'HAL0_API_URL=http://127.0.0.1:%s\nHAL0_HOME=%s\nHAL0_SERVE_PID=%s\n' \
            "${API_PORT}" "${PREFIX}" "${HAL0_SERVE_PID}" > "${SCRIPT_DIR}/reports/.api-handoff"
        # Leave the server running for later tiers; trap kills on exit.
    else
        add_row "dev-api-up" "fail" "$(since_ms "${start}")" "API never became healthy on :${API_PORT}; tail: $(tail -n3 "${SERVE_LOG}" 2>/dev/null | tr '\n' ' ')"
    fi
else
    add_row "dev-api-up" "skip" "$(since_ms "${start}")" "hal0 binary missing — earlier row failed"
fi

# ── ROW: prod-no-start (opt-in) ──────────────────────────────────────────────
log_step "Row: prod-no-start"
start=$(start_ms)
if [[ "${HAL0_HARNESS_PROD:-0}" != "1" ]]; then
    add_row "prod-no-start" "skip" "$(since_ms "${start}")" "skipped — set HAL0_HARNESS_PROD=1 to exercise sudo /opt/hal0 install (mutates /etc and /var/lib)"
else
    LOG3="${PREFIX}/install-prod.log"
    if sudo -n true 2>/dev/null; then
        if HAL0_NO_PROBE=1 HAL0_PLAIN=1 HAL0_NO_HELLO=1 HAL0_NO_QR=1 \
            sudo -E bash "${REPO_ROOT}/installer/install.sh" --no-start >"${LOG3}" 2>&1; then
            # Assert: units exist, not active.
            if systemctl list-unit-files hal0-api.service --no-legend | grep -q hal0-api \
                && ! systemctl is-active --quiet hal0-api; then
                add_row "prod-no-start" "pass" "$(since_ms "${start}")" "units installed, not started"
            else
                add_row "prod-no-start" "fail" "$(since_ms "${start}")" "units missing or already-active despite --no-start"
            fi
        else
            rc=$?
            add_row "prod-no-start" "fail" "$(since_ms "${start}")" "sudo install --no-start exit=${rc}; tail: $(tail -n1 "${LOG3}")"
        fi
    else
        add_row "prod-no-start" "skip" "$(since_ms "${start}")" "sudo -n not available (passwordless sudo required)"
    fi
fi

# ── ROW: tls-default (opt-in) ───────────────────────────────────────────────
# Default install path post-ADR-0001-Child-B: Caddy installed as a dumb
# TLS terminator, rendered Caddyfile is the minimal 10-line form (no
# basicauth, no @public matcher, no per-path handle block). The grep
# assertions below are NEGATIVE — they prove the auth-edge surface is
# gone, not present.
log_step "Row: tls-default"
start=$(start_ms)
if [[ "${HAL0_HARNESS_TLS:-0}" != "1" ]]; then
    add_row "tls-default" "skip" "$(since_ms "${start}")" "skipped — set HAL0_HARNESS_TLS=1 (and HAL0_HARNESS_PROD=1) to install caddy + render Caddyfile"
elif [[ "${HAL0_HARNESS_PROD:-0}" != "1" ]]; then
    add_row "tls-default" "skip" "$(since_ms "${start}")" "tls-default is a prod-mode path; HAL0_HARNESS_PROD=1 also required"
else
    LOG_TLS="${PREFIX}/install-tls.log"
    if HAL0_PUBLIC_HOST=hal0-harness.local HAL0_TLS_EMAIL=harness@hal0.test \
       HAL0_NO_PROBE=1 HAL0_PLAIN=1 \
        sudo -E bash "${REPO_ROOT}/installer/install.sh" --no-start >"${LOG_TLS}" 2>&1; then
        # Per ADR-0001 the rendered Caddyfile must collapse to the
        # ~10-line minimal terminator: TLS + reverse_proxy 127.0.0.1:8080
        # and NOTHING else (no basicauth, no @public matcher). Grep on
        # actual directive lines (start-of-line with optional leading
        # whitespace) so a comment mentioning the historical directives
        # doesn't false-positive.
        OK=1
        FAIL_REASON=""
        if [[ ! -f /etc/hal0/Caddyfile ]]; then
            OK=0; FAIL_REASON="/etc/hal0/Caddyfile missing"
        elif ! grep -q 'reverse_proxy 127.0.0.1:8080' /etc/hal0/Caddyfile; then
            OK=0; FAIL_REASON="reverse_proxy line missing"
        elif grep -qE '^[[:space:]]*basicauth' /etc/hal0/Caddyfile; then
            OK=0; FAIL_REASON="basicauth directive still present in Caddyfile (Child B regression)"
        elif grep -qE '^[[:space:]]*@public' /etc/hal0/Caddyfile; then
            OK=0; FAIL_REASON="@public matcher still present in Caddyfile (Child B regression)"
        elif ! grep -q HAL0_AUTH_ENABLED=1 /etc/hal0/api.env; then
            OK=0; FAIL_REASON="HAL0_AUTH_ENABLED=1 not set in api.env"
        fi
        if [[ "${OK}" -eq 1 ]]; then
            add_row "tls-default" "pass" "$(since_ms "${start}")" "Caddyfile rendered as minimal TLS-only reverse proxy (no edge auth)"
        else
            add_row "tls-default" "fail" "$(since_ms "${start}")" "${FAIL_REASON}"
        fi
    else
        rc=$?
        add_row "tls-default" "fail" "$(since_ms "${start}")" "tls-default install exit=${rc}; tail: $(tail -n1 "${LOG_TLS}")"
    fi
fi

# ── ROW: no-tls (opt-in) ────────────────────────────────────────────────────
# --no-tls install: Caddy not touched, no Caddyfile, hal0-api unit binds
# 0.0.0.0:8080 instead of 127.0.0.1:8080, /api/auth/status reports
# auth_mode=open (no password set, no edge auth — full open posture for
# hosts behind an existing reverse proxy).
log_step "Row: no-tls"
start=$(start_ms)
if [[ "${HAL0_HARNESS_PROD:-0}" != "1" ]]; then
    add_row "no-tls" "skip" "$(since_ms "${start}")" "skipped — set HAL0_HARNESS_PROD=1 to exercise sudo /opt/hal0 --no-tls install"
else
    LOG_NOTLS="${PREFIX}/install-no-tls.log"
    if HAL0_NO_PROBE=1 HAL0_PLAIN=1 HAL0_NO_HELLO=1 HAL0_NO_QR=1 \
        sudo -E bash "${REPO_ROOT}/installer/install.sh" --no-tls --no-start >"${LOG_NOTLS}" 2>&1; then
        OK=1
        FAIL_REASON=""
        # The Caddy unit must NOT be installed (--no-tls skips that block).
        if [[ -f /etc/systemd/system/hal0-caddy.service ]]; then
            OK=0; FAIL_REASON="hal0-caddy.service present despite --no-tls"
        # hal0-api unit must bind 0.0.0.0:8080 (not 127.0.0.1).
        elif ! grep -q 'serve --host 0.0.0.0' /etc/systemd/system/hal0-api.service; then
            OK=0; FAIL_REASON="hal0-api ExecStart does not bind 0.0.0.0 under --no-tls"
        # api.env must NOT carry HAL0_AUTH_ENABLED=1 — --no-tls means
        # the operator is fronting hal0 with their own proxy and we
        # don't presume to flip auth on for them.
        elif grep -q '^HAL0_AUTH_ENABLED=1' /etc/hal0/api.env; then
            OK=0; FAIL_REASON="HAL0_AUTH_ENABLED=1 set in api.env despite --no-tls"
        fi
        if [[ "${OK}" -eq 1 ]]; then
            # Spin the API up briefly to hit /api/auth/status and verify
            # the open-mode envelope. Background, poll, kill — same
            # pattern as dev-api-up above.
            NOTLS_PORT="${HAL0_HARNESS_NO_TLS_PORT:-18091}"
            HAL0_HOME="${PREFIX}" HAL0_PORT="${NOTLS_PORT}" \
                "${PREFIX}/.venv/bin/hal0" serve --host 127.0.0.1 --port "${NOTLS_PORT}" \
                >"${PREFIX}/serve-no-tls.log" 2>&1 &
            NOTLS_PID=$!
            UP=0
            for _ in $(seq 1 30); do
                if curl -fsS -m 1 "http://127.0.0.1:${NOTLS_PORT}/api/auth/status" >/dev/null 2>&1; then
                    UP=1; break
                fi
                sleep 0.5
            done
            if [[ "${UP}" -eq 1 ]]; then
                STATUS_JSON="$(curl -fsS "http://127.0.0.1:${NOTLS_PORT}/api/auth/status" 2>/dev/null || echo '{}')"
                # Tolerate either jq or python for the parse — harness
                # hosts may not have jq. We grep on the raw JSON to
                # avoid the dependency.
                if echo "${STATUS_JSON}" | grep -q '"auth_mode":"open"' \
                   && echo "${STATUS_JSON}" | grep -q '"password_set":false'; then
                    add_row "no-tls" "pass" "$(since_ms "${start}")" "Caddy unit absent, hal0-api binds 0.0.0.0, /api/auth/status=open"
                else
                    add_row "no-tls" "fail" "$(since_ms "${start}")" "auth/status not in open posture: ${STATUS_JSON}"
                fi
            else
                add_row "no-tls" "fail" "$(since_ms "${start}")" "API never became healthy on :${NOTLS_PORT}; tail: $(tail -n3 "${PREFIX}/serve-no-tls.log" 2>/dev/null | tr '\n' ' ')"
            fi
            kill "${NOTLS_PID}" 2>/dev/null || true
            wait "${NOTLS_PID}" 2>/dev/null || true
        else
            add_row "no-tls" "fail" "$(since_ms "${start}")" "${FAIL_REASON}"
        fi
    else
        rc=$?
        add_row "no-tls" "fail" "$(since_ms "${start}")" "no-tls install exit=${rc}; tail: $(tail -n1 "${LOG_NOTLS}")"
    fi
fi

# ── ROW: uninstall-dev-gap ──────────────────────────────────────────────────
# installer/uninstall.sh has no --dev flag and always hardcodes /etc,
# /usr/lib, /var/lib paths (uninstall.sh:95,113,153). Calling it from
# a dev-mode harness would clobber the actual host's hal0 install. We
# record the gap and verify the manual cleanup path instead.
log_step "Row: uninstall-dev-gap"
start=$(start_ms)
add_row "uninstall-dev-gap" "deferred" "$(since_ms "${start}")" \
    "installer/uninstall.sh:95,113,153 hardcodes /etc/systemd/system, /usr/lib/hal0, /etc/hal0, /var/lib/hal0 — no --dev mode. Calling it on a dev install would touch the real host. Needs a --dev flag mirroring install.sh."

# NOTE: dev-manual-cleanup and prod-uninstall rows live in
# tests/harness/harness-cleanup.sh so cli-test.sh and runtime-test.sh
# can use the install before it's torn down.

# ── ROW: uninstall-caddy-gap ────────────────────────────────────────────────
# uninstall.sh:99 hardcodes only 3 units (api, openwebui, slot@) — the
# hal0-caddy.service installed by --auth=basic is left behind. Test
# documents the gap; no destructive verification.
log_step "Row: uninstall-caddy-gap"
start=$(start_ms)
if grep -q 'hal0-caddy' "${REPO_ROOT}/installer/uninstall.sh"; then
    add_row "uninstall-caddy-gap" "pass" "$(since_ms "${start}")" "uninstall.sh now references hal0-caddy.service"
else
    add_row "uninstall-caddy-gap" "deferred" "$(since_ms "${start}")" "installer/uninstall.sh:96-99 does not remove hal0-caddy.service; --auth=basic installs leave caddy unit behind. Add to UNIT_FILE loop."
fi

# ── ROW: dev-installer-systemd-dir-unused ───────────────────────────────────
# Historical gap: installer/systemd/ once shipped hal0-api.service +
# hal0-slot@.service but install.sh never read them (api unit written
# inline, slot template loaded from packaging/systemd/). Resolved
# 2026-05-15 by deleting installer/systemd/. The canonical systemd unit
# templates live in packaging/systemd/.
log_step "Row: dev-installer-systemd-dir-unused"
start=$(start_ms)
if [[ -d "${REPO_ROOT}/installer/systemd" ]]; then
    if grep -q "installer/systemd" "${REPO_ROOT}/installer/install.sh"; then
        add_row "dev-installer-systemd-dir-unused" "pass" "$(since_ms "${start}")" "installer/systemd/ exists and install.sh references it"
    else
        add_row "dev-installer-systemd-dir-unused" "deferred" "$(since_ms "${start}")" "installer/systemd/ shipped but never read by install.sh. Either remove installer/systemd or rewire install.sh."
    fi
else
    add_row "dev-installer-systemd-dir-unused" "pass" "$(since_ms "${start}")" "installer/systemd/ removed; systemd unit templates live in packaging/systemd/"
fi

# ── write + exit ────────────────────────────────────────────────────────────
log_step "Write report"
harness_write_report || true
log_info "report: ${REPORT}"
exit 0
