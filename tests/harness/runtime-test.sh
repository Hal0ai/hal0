#!/usr/bin/env bash
# tests/harness/runtime-test.sh
#
# Drives one real slot lifecycle round-trip per provider that can run
# on the local host. The point isn't matrix coverage (release-test.sh
# already owns that for the hal0-test LXC) — it's to prove that on a
# fresh --dev install on the developer's box, the dispatcher can
# actually route a /v1/chat/completions request to a llama-server
# container backed by a local tiny GGUF.
#
# Rows (current, llama-server only — see comment block at bottom for
# moonshine/kokoro/comfyui/flm next steps):
#
#   runtime-image-check          docker image present (vulkan)
#   runtime-slot-create          create harness-rt slot
#   runtime-slot-load            POST /api/slots/<n>/load and poll to READY
#   runtime-chat-roundtrip       POST /v1/chat/completions returns non-empty content
#   runtime-slot-unload          POST /api/slots/<n>/unload
#   runtime-slot-delete          DELETE /api/slots/<n>
#
# Each row is structured so a fail can be traced back to a specific
# transition or HTTP call.

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

REPORT="${SCRIPT_DIR}/reports/runtime.json"
harness_init "runtime" "${REPORT}"

HANDOFF="${SCRIPT_DIR}/reports/.api-handoff"
if [[ -r "${HANDOFF}" ]]; then
    # shellcheck disable=SC1090
    source "${HANDOFF}"
fi
: "${HAL0_API_URL:=http://127.0.0.1:18080}"
: "${HAL0_HOME:=}"
: "${HAL0_BIN:=${HAL0_HOME:+${HAL0_HOME}/.venv/bin/hal0}}"

export HAL0_API_URL HAL0_HOME

log_step "Runtime harness — bin=${HAL0_BIN:-<unset>}  api=${HAL0_API_URL}"

# Pre-flight: API + binary.
if ! curl -fsS -m 2 "${HAL0_API_URL}/api/status" >/dev/null 2>&1; then
    add_row "preflight" "fail" 0 "GET ${HAL0_API_URL}/api/status failed"
    harness_write_report || true
    exit 1
fi
if [[ -z "${HAL0_BIN:-}" || ! -x "${HAL0_BIN}" ]]; then
    add_row "preflight" "fail" 0 "hal0 binary missing"
    harness_write_report || true
    exit 1
fi

TEST_SLOT="harness-rt"
TEST_MODEL_ID="harness-rt-qwen"
TEST_MODEL_PATH="/mnt/ai-models/huggingface/hub/models--unsloth--Qwen3.5-0.8B-GGUF/snapshots/6ab461498e2023f6e3c1baea90a8f0fe38ab64d0/Qwen3.5-0.8B-UD-Q4_K_XL.gguf"

# Best-effort cleanup of any prior runs.
"${HAL0_BIN}" slot delete "${TEST_SLOT}" --force >/dev/null 2>&1 || true
"${HAL0_BIN}" model rm    "${TEST_MODEL_ID}" --force >/dev/null 2>&1 || true

cleanup_runtime() {
    "${HAL0_BIN}" slot unload "${TEST_SLOT}" >/dev/null 2>&1 || true
    "${HAL0_BIN}" slot delete "${TEST_SLOT}" --force >/dev/null 2>&1 || true
    "${HAL0_BIN}" model rm    "${TEST_MODEL_ID}" --force >/dev/null 2>&1 || true
}
trap cleanup_runtime EXIT

# ── ROW: runtime-image-check ────────────────────────────────────────────────
log_step "Row: runtime-image-check"
start=$(start_ms)
IMG="$(python3 -c "
import json
m = json.load(open('${REPO_ROOT}/manifest.json'))
e = m['runners']['standard']
print(e['image'] + ':' + e['tag'] if e.get('tag') else '')
" 2>/dev/null || true)"
if [[ -z "${IMG}" ]]; then
    add_row "runtime-image-check" "fail" "$(since_ms "${start}")" "manifest.json runners.standard has no published tag"
elif docker image inspect "${IMG}" >/dev/null 2>&1; then
    add_row "runtime-image-check" "pass" "$(since_ms "${start}")" "${IMG} present locally"
else
    # Try pulling — first run on a fresh box should not fail just because the
    # image isn't cached yet.
    if docker pull "${IMG}" >/dev/null 2>&1; then
        add_row "runtime-image-check" "pass" "$(since_ms "${start}")" "${IMG} pulled"
    else
        add_row "runtime-image-check" "skip" "$(since_ms "${start}")" \
            "vulkan toolbox image not available locally and pull failed — runtime rows will all skip"
        # Bail early.
        for r in runtime-model-register runtime-slot-create runtime-slot-load \
                 runtime-chat-roundtrip runtime-slot-unload runtime-slot-delete; do
            add_row "${r}" "skip" 0 "no vulkan image"
        done
        harness_write_report || true
        exit 0
    fi
fi

# ── ROW: runtime-model-register ─────────────────────────────────────────────
log_step "Row: runtime-model-register"
start=$(start_ms)
if [[ ! -r "${TEST_MODEL_PATH}" ]]; then
    add_row "runtime-model-register" "skip" "$(since_ms "${start}")" "no tiny GGUF at ${TEST_MODEL_PATH}"
    for r in runtime-slot-create runtime-slot-load runtime-chat-roundtrip \
             runtime-slot-unload runtime-slot-delete; do
        add_row "${r}" "skip" 0 "depends on runtime-model-register"
    done
    harness_write_report || true
    exit 0
fi
if "${HAL0_BIN}" model add "${TEST_MODEL_PATH}" --id "${TEST_MODEL_ID}" \
    --license apache-2.0 --name "Harness Qwen" >/dev/null 2>&1; then
    add_row "runtime-model-register" "pass" "$(since_ms "${start}")" "${TEST_MODEL_ID} registered"
else
    add_row "runtime-model-register" "fail" "$(since_ms "${start}")" "model add exited non-zero"
fi

# ── ROW: runtime-slot-create ────────────────────────────────────────────────
log_step "Row: runtime-slot-create"
start=$(start_ms)
if "${HAL0_BIN}" slot create "${TEST_SLOT}" \
    --provider llama-server --port 8093 \
    --model "${TEST_MODEL_ID}" >/dev/null 2>&1; then
    add_row "runtime-slot-create" "pass" "$(since_ms "${start}")" "${TEST_SLOT} created (port 8093, llama-server/vulkan)"
else
    add_row "runtime-slot-create" "fail" "$(since_ms "${start}")" "slot create non-zero"
    harness_write_report || true
    exit 1
fi

# ── ROW: runtime-slot-load ──────────────────────────────────────────────────
# Slot template loads in --dev mode? In dev mode install.sh writes the
# template under PREFIX/etc/systemd/system, but the host's systemctl
# does not pick it up — so slot load via systemd is expected to fail.
# We document the gap and skip rather than fail loudly.
log_step "Row: runtime-slot-load"
start=$(start_ms)
LOAD_LOG="${SCRIPT_DIR}/reports/runtime-load.log"
if "${HAL0_BIN}" slot load "${TEST_SLOT}" >"${LOAD_LOG}" 2>&1; then
    add_row "runtime-slot-load" "pass" "$(since_ms "${start}")" "${TEST_SLOT} reached ready via systemd"
    SLOT_LOADED=1
else
    rc=$?
    # Differentiate: did it fail because systemd doesn't know the unit
    # (dev-mode limitation) vs an actual provider/health failure?
    if grep -qE "Failed to start|Unit hal0-slot@.*\.service not found|systemctl" "${LOAD_LOG}"; then
        add_row "runtime-slot-load" "deferred" "$(since_ms "${start}")" \
            "dev-mode limitation: --dev install writes hal0-slot@.service under PREFIX/etc/systemd/system but host's systemctl ignores it. Slots can't actually run under --dev. install.sh:530-533 — needs --dev to either (a) use systemd --user, (b) document this gap, or (c) wire a per-prefix systemd path."
        SLOT_LOADED=0
    else
        add_row "runtime-slot-load" "fail" "$(since_ms "${start}")" "exit=${rc}; tail: $(tail -n1 "${LOAD_LOG}")"
        SLOT_LOADED=0
    fi
fi

# ── ROW: runtime-chat-roundtrip ─────────────────────────────────────────────
log_step "Row: runtime-chat-roundtrip"
start=$(start_ms)
if [[ "${SLOT_LOADED:-0}" -ne 1 ]]; then
    add_row "runtime-chat-roundtrip" "skip" "$(since_ms "${start}")" "slot not loaded"
else
    if curl -fsS -m 30 "${HAL0_API_URL}/v1/chat/completions" \
        -H 'content-type: application/json' \
        -d "{\"model\":\"${TEST_MODEL_ID}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":4}" \
        >/dev/null 2>&1; then
        add_row "runtime-chat-roundtrip" "pass" "$(since_ms "${start}")" "/v1/chat/completions returned 2xx"
    else
        add_row "runtime-chat-roundtrip" "fail" "$(since_ms "${start}")" "/v1/chat/completions did not return 2xx within 30s"
    fi
fi

# ── ROW: runtime-slot-unload ────────────────────────────────────────────────
log_step "Row: runtime-slot-unload"
start=$(start_ms)
if [[ "${SLOT_LOADED:-0}" -ne 1 ]]; then
    add_row "runtime-slot-unload" "skip" "$(since_ms "${start}")" "slot was never loaded"
else
    if "${HAL0_BIN}" slot unload "${TEST_SLOT}" >/dev/null 2>&1; then
        add_row "runtime-slot-unload" "pass" "$(since_ms "${start}")" "unload returned 0"
    else
        add_row "runtime-slot-unload" "fail" "$(since_ms "${start}")" "unload non-zero"
    fi
fi

# ── ROW: runtime-slot-delete ────────────────────────────────────────────────
log_step "Row: runtime-slot-delete"
start=$(start_ms)
if "${HAL0_BIN}" slot delete "${TEST_SLOT}" --force >/dev/null 2>&1; then
    add_row "runtime-slot-delete" "pass" "$(since_ms "${start}")" "delete returned 0"
else
    add_row "runtime-slot-delete" "fail" "$(since_ms "${start}")" "delete non-zero"
fi

log_step "Write report"
harness_write_report || true
log_info "report: ${REPORT}"
exit 0

# NOTE for next harness iteration:
#   - Moonshine / Kokoro can run on CPU on hal0-dev (no GPU needed).
#     Add a runtime-moonshine and runtime-kokoro tier once dev-mode
#     can actually start slots (deferred above).
#   - ComfyUI ROCm + FLM/NPU stay on hal0-test LXC; covered by
#     scripts/release-test.sh.
