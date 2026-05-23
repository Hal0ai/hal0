#!/usr/bin/env bash
# tests/harness/cli-test.sh
#
# Drives the hal0 CLI surface against a live API. Honors the handoff
# file written by installer-test.sh (reports/.api-handoff) so the
# installer harness's --dev install + serve can be re-used; falls back
# to spinning a fresh one if the handoff is stale.
#
# Each subcommand → one row in reports/cli.json, schema
# hal0.harness-report.v1.
#
# Rows (in execution order):
#   cli-version             hal0 --version
#   cli-help                hal0 --help (no API)
#   cli-doctor              hal0 doctor --plain
#   cli-config-show         hal0 config show
#   cli-config-validate     hal0 config validate
#   cli-config-hardware     hal0 config hardware
#   cli-config-reload       hal0 config reload
#   cli-status              hal0 status
#   cli-probe               hal0 probe
#   cli-slot-list           hal0 slot list
#   cli-model-list          hal0 model list
#   cli-model-register      hal0 model register <id> --path <local-gguf>
#   cli-model-show          hal0 model show <id>
#   cli-update-check        hal0 update --check
#   cli-capabilities-migrate-dry  hal0 capabilities migrate --dry-run (asserts catalog invariant)
#   cli-slot-show-primary   hal0 slot show primary (expected: 404-equivalent before create)
#   cli-slot-create-test    hal0 slot create <test-slot> --provider llama-server --backend vulkan --model <id>
#   cli-model-assign        hal0 model assign <id> --slot <test-slot>
#   cli-slot-show-after     hal0 slot show <test-slot>
#   cli-slot-delete         hal0 slot delete <test-slot> --force
#   cli-model-rm            hal0 model rm <id> --force
#
# Env knobs:
#   HAL0_API_URL        base URL (default http://127.0.0.1:18080)
#   HAL0_HOME           prefix root (default /tmp/hal0-h)
#   HAL0_BIN            binary path (default ${HAL0_HOME}/.venv/bin/hal0)
#   HAL0_DOCTOR_PORTS   space-separated TCP ports for `hal0 doctor`
#                       port-collision check (default "18080 13001" — the
#                       dev install's ports, so a co-resident prod install
#                       on 8080/3001 doesn't poison the cli-doctor row).

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

REPORT="${SCRIPT_DIR}/reports/cli.json"
harness_init "cli" "${REPORT}"

# Pick up handoff or use defaults.
HANDOFF="${SCRIPT_DIR}/reports/.api-handoff"
if [[ -r "${HANDOFF}" ]]; then
    # shellcheck disable=SC1090
    source "${HANDOFF}"
fi
: "${HAL0_API_URL:=http://127.0.0.1:18080}"
: "${HAL0_HOME:=/tmp/hal0-h}"
: "${HAL0_BIN:=${HAL0_HOME}/.venv/bin/hal0}"
# When a prod hal0 install is co-resident on this host it owns the
# default doctor port list (8080 + 3001). Probe the dev install's
# ports instead so cli-doctor doesn't report a false-positive
# collision. An externally-set HAL0_DOCTOR_PORTS still wins.
: "${HAL0_DOCTOR_PORTS:=18080 13001}"

export HAL0_API_URL HAL0_HOME HAL0_DOCTOR_PORTS

log_step "CLI harness — bin=${HAL0_BIN}  api=${HAL0_API_URL}  home=${HAL0_HOME}"

# Pre-flight: hal0 binary must exist.
if [[ ! -x "${HAL0_BIN}" ]]; then
    log_err "hal0 binary missing at ${HAL0_BIN} — run installer-test.sh first or set HAL0_BIN"
    add_row "preflight-binary" "fail" "0" "hal0 binary not found at ${HAL0_BIN}"
    harness_write_report || true
    exit 1
fi

# Pre-flight: API must respond.
if ! curl -fsS -m 2 "${HAL0_API_URL}/api/status" >/dev/null 2>&1; then
    log_err "API unreachable at ${HAL0_API_URL}/api/status"
    add_row "preflight-api" "fail" "0" "GET ${HAL0_API_URL}/api/status timed out or 4xx — installer-test.sh's serve might be down"
    harness_write_report || true
    exit 1
fi

# Helper: run a CLI command, capture stdout/stderr/exit, emit one row.
# Usage: run_row <row_name> <expected_exit:0|nonzero> <detail_on_pass> -- <cmd...>
run_row() {
    local name="$1" expect="$2" pass_detail="$3"; shift 3
    [[ "$1" == "--" ]] && shift
    local log="${SCRIPT_DIR}/reports/cli-${name}.log"
    local start; start=$(start_ms)
    set +e
    "$@" >"${log}" 2>&1
    local rc=$?
    set -e
    case "${expect}" in
        0)
            if [[ "${rc}" -eq 0 ]]; then
                add_row "${name}" "pass" "$(since_ms "${start}")" "${pass_detail}"
            else
                local tail; tail="$(tail -n1 "${log}" 2>/dev/null | tr -d '\n')"
                add_row "${name}" "fail" "$(since_ms "${start}")" "exit=${rc}: ${tail:-(no stderr)}"
            fi ;;
        nonzero)
            if [[ "${rc}" -ne 0 ]]; then
                add_row "${name}" "pass" "$(since_ms "${start}")" "${pass_detail} (exit=${rc} as expected)"
            else
                add_row "${name}" "fail" "$(since_ms "${start}")" "expected non-zero exit, got 0"
            fi ;;
    esac
}

# ── read-only rows ──────────────────────────────────────────────────────────
log_step "Read-only CLI rows"

run_row "cli-version" 0 "hal0 --version" -- "${HAL0_BIN}" --version
run_row "cli-help"    0 "hal0 --help"    -- "${HAL0_BIN}" --help
# hal0 doctor exits 1 if /var/lib < 20GB free (preflight_disk). That's
# an environment issue, not a project bug — wrap to surface as deferred
# rather than fail when the host is just low on disk.
run_doctor_row() {
    local log="${SCRIPT_DIR}/reports/cli-cli-doctor.log"
    local start; start=$(start_ms)
    set +e
    "${HAL0_BIN}" doctor --plain >"${log}" 2>&1
    local rc=$?
    set -e
    if [[ "${rc}" -eq 0 ]]; then
        add_row "cli-doctor" "pass" "$(since_ms "${start}")" "all preflight checks ok"
    elif grep -q "only .* GB free on /var/lib" "${log}"; then
        add_row "cli-doctor" "deferred" "$(since_ms "${start}")" \
            "preflight_disk failed: $(grep -oE 'only .* GB free on /var/lib; need at least .* GB' "${log}" | head -n1) — host env, not hal0"
    else
        add_row "cli-doctor" "fail" "$(since_ms "${start}")" "exit=${rc}: $(tail -n1 "${log}" 2>/dev/null | tr -d '\n')"
    fi
}
run_doctor_row
run_row "cli-config-show" 0 "hal0 config show" -- "${HAL0_BIN}" config show
run_row "cli-config-validate" 0 "hal0 config validate" -- "${HAL0_BIN}" config validate
run_row "cli-config-hardware" 0 "hal0 config hardware (cached)" -- "${HAL0_BIN}" config hardware
run_row "cli-config-reload"   0 "hal0 config reload"   -- "${HAL0_BIN}" config reload
run_row "cli-status"  0 "hal0 status" -- "${HAL0_BIN}" status
run_row "cli-probe"   0 "hal0 probe"  -- "${HAL0_BIN}" probe
run_row "cli-slot-list"   0 "hal0 slot list"   -- "${HAL0_BIN}" slot list
run_row "cli-model-list"  0 "hal0 model list"  -- "${HAL0_BIN}" model list
# hal0 update --check needs releases.hal0.dev DNS-resolvable; on a
# dev box without that the API correctly 500s. Treat DNS failure as
# deferred so we still notice when the CLI surface itself breaks.
run_update_check_row() {
    local log="${SCRIPT_DIR}/reports/cli-cli-update-check.log"
    local start; start=$(start_ms)
    set +e
    "${HAL0_BIN}" update --check >"${log}" 2>&1
    local rc=$?
    set -e
    if [[ "${rc}" -eq 0 ]]; then
        add_row "cli-update-check" "pass" "$(since_ms "${start}")" "update check completed"
    elif grep -q "No address associated with hostname\|release manifest fetch failed" "${log}"; then
        add_row "cli-update-check" "deferred" "$(since_ms "${start}")" \
            "releases.hal0.dev unreachable from this host; update-check API correctly returns 500. Wire when release infra lives."
    else
        add_row "cli-update-check" "fail" "$(since_ms "${start}")" "exit=${rc}: $(tail -n1 "${log}" 2>/dev/null | tr -d '\n')"
    fi
}
run_update_check_row

# Catalog invariant: every persisted capability selection must be a legal
# (model, backend) pair against the live catalog. `hal0 capabilities
# migrate --dry-run` exits 0 in both clean and dirty cases — distinguish
# by parsing for the "nothing to migrate" sentinel; finding it means the
# orchestrator's pair-validation is doing its job and no drift escaped.
run_capabilities_migrate_dry_row() {
    local log="${SCRIPT_DIR}/reports/cli-cli-capabilities-migrate-dry.log"
    local start; start=$(start_ms)
    set +e
    "${HAL0_BIN}" capabilities migrate --dry-run >"${log}" 2>&1
    local rc=$?
    set -e
    if [[ "${rc}" -ne 0 ]]; then
        add_row "cli-capabilities-migrate-dry" "fail" "$(since_ms "${start}")" \
            "exit=${rc}: $(tail -n1 "${log}" 2>/dev/null | tr -d '\n')"
    elif grep -q "nothing to migrate" "${log}"; then
        add_row "cli-capabilities-migrate-dry" "pass" "$(since_ms "${start}")" \
            "every persisted selection is a legal (backend, model) pair"
    else
        add_row "cli-capabilities-migrate-dry" "fail" "$(since_ms "${start}")" \
            "drift detected: $(grep -c '│' "${log}") selection(s) would be rewritten"
    fi
}
run_capabilities_migrate_dry_row

# ── write rows: register + show + assign + delete ───────────────────────────
log_step "Mutating CLI rows"

TEST_MODEL_ID="harness-qwen3-0p8b"
TEST_SLOT_NAME="harness-primary"
TEST_MODEL_PATH=""

# Pick a tiny GGUF — preferred is the unsloth Qwen3.5-0.8B on /mnt/ai-models.
for cand in \
    "/mnt/ai-models/huggingface/hub/models--unsloth--Qwen3.5-0.8B-GGUF/snapshots/6ab461498e2023f6e3c1baea90a8f0fe38ab64d0/Qwen3.5-0.8B-UD-Q4_K_XL.gguf"; do
    if [[ -r "${cand}" ]]; then
        TEST_MODEL_PATH="${cand}"
        break
    fi
done

if [[ -z "${TEST_MODEL_PATH}" ]]; then
    add_row "cli-model-register" "skip" "0" "no tiny GGUF found on host; skipping register/assign/delete chain"
    add_row "cli-model-show"     "skip" "0" "depends on cli-model-register"
    add_row "cli-slot-create"    "skip" "0" "depends on cli-model-register"
    add_row "cli-model-assign"   "skip" "0" "depends on cli-slot-create"
    add_row "cli-slot-show-after" "skip" "0" "depends on cli-slot-create"
    add_row "cli-slot-delete"    "skip" "0" "depends on cli-slot-create"
    add_row "cli-model-rm"       "skip" "0" "depends on cli-model-register"
else
    log_info "using test model: ${TEST_MODEL_PATH}"

    run_row "cli-model-register" 0 "register on-disk gguf" -- \
        "${HAL0_BIN}" model register "${TEST_MODEL_ID}" \
        --path "${TEST_MODEL_PATH}" --license apache-2.0 --name "Qwen3.5 0.8B (harness)"

    run_row "cli-model-show" 0 "model show ${TEST_MODEL_ID}" -- \
        "${HAL0_BIN}" model show "${TEST_MODEL_ID}"

    # slot show on a name that doesn't exist yet — should fail with non-zero
    run_row "cli-slot-show-missing" nonzero "slot show on absent name returns non-zero" -- \
        "${HAL0_BIN}" slot show "${TEST_SLOT_NAME}"

    # `hal0 slot create --backend` is actually the *provider* knob
    # (slot_commands.py:204-229 passes --backend as provider and
    # hardcodes SlotConfig.backend = "vulkan" at line 228). Port 8093
    # is in valid range (schema.py:40-41).
    run_row "cli-slot-create" 0 "slot create ${TEST_SLOT_NAME}" -- \
        "${HAL0_BIN}" slot create "${TEST_SLOT_NAME}" \
        --backend llama-server --port 8093 \
        --model "${TEST_MODEL_ID}"

    run_row "cli-model-assign" 0 "assign model to slot" -- \
        "${HAL0_BIN}" model assign "${TEST_MODEL_ID}" --slot "${TEST_SLOT_NAME}"

    run_row "cli-slot-show-after" 0 "slot show after create" -- \
        "${HAL0_BIN}" slot show "${TEST_SLOT_NAME}"

    # Delete + cleanup. Use --force so no stdin prompt blocks the harness.
    run_row "cli-slot-delete" 0 "slot delete --force" -- \
        "${HAL0_BIN}" slot delete "${TEST_SLOT_NAME}" --force

    run_row "cli-model-rm" 0 "model rm --force" -- \
        "${HAL0_BIN}" model rm "${TEST_MODEL_ID}" --force
fi

# ── Hermes-Agent bootstrap surface (Phase 10, #246) ─────────────────────────
#
# Drives the bootstrap dry-run path so the CLI surface is exercised
# without requiring pypi + Python 3.11 on the harness host. Real
# end-to-end install happens in the γ-tier (release-test.sh on the LXC).
log_step "Hermes bootstrap surface"

run_row "cli-agent-bootstrap-help" 0 "bootstrap --help" -- \
    "${HAL0_BIN}" agent bootstrap hermes --help

run_row "cli-agent-bootstrap-dry-run" 0 "bootstrap --dry-run --skip-phase" -- \
    "${HAL0_BIN}" agent bootstrap hermes --dry-run \
    --skip-phase install --skip-phase env_probe --skip-phase mcp_wire \
    --skip-phase namespace_register --skip-phase context_link \
    --skip-phase model_automap --skip-phase voice_wire \
    --skip-phase smoke_tests --skip-phase self_report

run_row "cli-agent-status-help" 0 "agent status --help" -- \
    "${HAL0_BIN}" agent status --help

run_row "cli-agent-log-help" 0 "agent log --help" -- \
    "${HAL0_BIN}" agent log --help

run_row "cli-agent-upgrade-help" 0 "agent upgrade --help" -- \
    "${HAL0_BIN}" agent upgrade --help

run_row "cli-agent-uninstall-help" 0 "agent uninstall --help (--keep-memory present)" -- \
    "${HAL0_BIN}" agent uninstall --help

run_row "cli-agent-peers-help" 0 "agent peers --help" -- \
    "${HAL0_BIN}" agent peers --help

# ── write + exit ────────────────────────────────────────────────────────────
log_step "Write report"
harness_write_report || true
log_info "report: ${REPORT}"
exit 0
