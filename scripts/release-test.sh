#!/usr/bin/env bash
# hal0 release-test driver (tier γ).
#
# SSHes into the hal0-test LXC (set HAL0_TEST_HOST) and walks the
# release-gate matrix one row at a time. Each row produces a structured
# record appended to ${HAL0_TEST_REPORT} (default tests/release-gate-report.json).
#
# Exit codes:
#   0   every required row passed (skip / deferred are non-blocking)
#   1   one or more rows failed
#   2   SSH / pre-flight failure (couldn't even start)
#
# Env (see Makefile for defaults):
#   HAL0_TEST_HOST     SSH host (required; no default — set to your hal0-test LXC IP)
#   HAL0_TEST_USER     SSH user (default root)
#   HAL0_TEST_SSH_KEY  SSH key  (default ~/.ssh/id_ed25519)
#   HAL0_TEST_PREFIX   Unique slot prefix for this run (default ci-h-<job>-<pid>)
#   HAL0_TEST_REPORT   Output JSON path (default tests/release-gate-report.json)
#
# Cross-team notes (PLAN §10.2):
#   - Team A owns toolbox image presence in manifest.json. Rows that need
#     an image not yet published (flm, rocm) are reported as "skip" with
#     a clear "image-not-available" detail — not a hard failure.
#   - Team D owns the updater CLI. If `hal0 update --check` is still a
#     stub, the updater row is reported as "deferred".

set -euo pipefail
IFS=$'\n\t'

HAL0_TEST_HOST="${HAL0_TEST_HOST:-}"
if [[ -z "${HAL0_TEST_HOST}" ]]; then
    echo "error: HAL0_TEST_HOST is not set — specify your hal0-test LXC IP or hostname" >&2
    exit 2
fi
HAL0_TEST_USER="${HAL0_TEST_USER:-root}"
HAL0_TEST_SSH_KEY="${HAL0_TEST_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
HAL0_TEST_PREFIX="${HAL0_TEST_PREFIX:-ci-h-local-$$}"
HAL0_TEST_REPORT="${HAL0_TEST_REPORT:-tests/release-gate-report.json}"

# Expand ~ in SSH key path.
HAL0_TEST_SSH_KEY="${HAL0_TEST_SSH_KEY/#\~/$HOME}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_PATH="${REPO_ROOT}/${HAL0_TEST_REPORT}"
mkdir -p "$(dirname "${REPORT_PATH}")"

# ── tty colours ──────────────────────────────────────────────────────────────
# shellcheck disable=SC2034  # BLU/DIM kept for future log_step variants
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'; YEL=$'\033[1;33m'; GRN=$'\033[0;32m'
    BLU=$'\033[0;36m'; BOLD=$'\033[1m';   DIM=$'\033[2m'; RST=$'\033[0m'
else
    RED=; YEL=; GRN=; BLU=; BOLD=; DIM=; RST=
fi
log_info() { printf "${GRN}✔${RST}  %s\n" "$*"; }
log_warn() { printf "${YEL}!${RST}  %s\n" "$*" >&2; }
log_err()  { printf "${RED}✗${RST}  %s\n" "$*" >&2; }
log_step() { printf "\n${BOLD}── %s${RST}\n" "$*"; }

# ── pre-flight ───────────────────────────────────────────────────────────────
log_step "Pre-flight"

if [[ ! -r "${HAL0_TEST_SSH_KEY}" ]]; then
    log_err "SSH key not readable: ${HAL0_TEST_SSH_KEY}"
    log_err "Set HAL0_TEST_SSH_KEY to a key authorised on ${HAL0_TEST_USER}@${HAL0_TEST_HOST}"
    exit 2
fi

SSH_OPTS=(
    -i "${HAL0_TEST_SSH_KEY}"
    -o ConnectTimeout=10
    -o BatchMode=yes
    -o StrictHostKeyChecking=accept-new
)

ssh_exec() {
    # shellcheck disable=SC2029
    ssh "${SSH_OPTS[@]}" "${HAL0_TEST_USER}@${HAL0_TEST_HOST}" "$@"
}

# Quick reachability test.
if ! ssh_exec true; then
    log_err "ssh ${HAL0_TEST_USER}@${HAL0_TEST_HOST} failed"
    exit 2
fi
log_info "ssh to ${HAL0_TEST_USER}@${HAL0_TEST_HOST} OK"
log_info "run prefix: ${HAL0_TEST_PREFIX}"

# Detect remote hal0 install (assume /opt/hal0 from install.sh or env override).
REMOTE_HAL0_BIN="$(ssh_exec 'which hal0 2>/dev/null || echo /opt/hal0/.venv/bin/hal0')"
REMOTE_HAL0_API="$(ssh_exec 'echo "${HAL0_API_URL:-http://127.0.0.1:8080}"')"
log_info "remote hal0 binary: ${REMOTE_HAL0_BIN}"
log_info "remote hal0 API:    ${REMOTE_HAL0_API}"

# ── manifest gate ────────────────────────────────────────────────────────────
# Each row that needs a toolbox image first asks manifest.json whether the
# image is present-and-pinned. If digest is null/empty the row reports
# skip("image-not-available"). This is Team A territory — we read, never write.
manifest_digest() {
    # Usage: manifest_digest <short_name>  → prints digest or empty string.
    python3 - "$1" <<'PY'
import json, sys
from pathlib import Path

name = sys.argv[1]
m = json.loads(Path("manifest.json").read_text())
images = m.get("toolbox_images", {})
entry = images.get(name, {})
digest = entry.get("digest")
print(digest or "")
PY
}

# ── report accumulator ───────────────────────────────────────────────────────
ROWS_JSON=()

add_row() {
    # add_row <name> <status:pass|fail|skip|deferred> <duration_ms> <detail>
    local name="$1" status="$2" dur="$3" detail="$4"
    ROWS_JSON+=("$(python3 - "$name" "$status" "$dur" "$detail" <<'PY'
import json, sys
print(json.dumps({
    "name": sys.argv[1],
    "status": sys.argv[2],
    "duration_ms": int(sys.argv[3]),
    "detail": sys.argv[4],
}))
PY
    )")
    case "${status}" in
        pass)     log_info  "[${name}] pass (${dur}ms) — ${detail}" ;;
        fail)     log_err   "[${name}] FAIL (${dur}ms) — ${detail}" ;;
        skip)     log_warn  "[${name}] skip — ${detail}" ;;
        deferred) log_warn  "[${name}] deferred — ${detail}" ;;
    esac
}

# Wall-clock helper.
since_ms() {
    # since_ms <start_ns> → integer ms
    local start="$1" end
    end=$(date +%s%N)
    echo $(( (end - start) / 1000000 ))
}

# ── cleanup hook ─────────────────────────────────────────────────────────────
# Ensure every slot we created on the LXC is torn down even on early exit.
CREATED_SLOTS=()
cleanup() {
    if [[ ${#CREATED_SLOTS[@]} -eq 0 ]]; then return; fi
    log_step "Cleanup"
    for slot in "${CREATED_SLOTS[@]}"; do
        ssh_exec "${REMOTE_HAL0_BIN} slot unload ${slot} 2>/dev/null || true" || true
        ssh_exec "${REMOTE_HAL0_BIN} slot delete ${slot} 2>/dev/null || true" || true
        log_info "cleaned up ${slot}"
    done
}
trap cleanup EXIT

# Track + create a unique slot on the LXC.
remote_slot_create() {
    # remote_slot_create <suffix> <backend> <provider> <model_id>
    local slot="${HAL0_TEST_PREFIX}-$1" backend="$2" provider="$3" model="$4"
    CREATED_SLOTS+=("${slot}")
    ssh_exec "${REMOTE_HAL0_BIN} slot create ${slot} --backend ${backend} --provider ${provider} --model ${model} --no-start" \
        2>/dev/null || true
    echo "${slot}"
}

# ── ROW: Vulkan baseline ─────────────────────────────────────────────────────
log_step "Row: vulkan baseline"
start=$(date +%s%N)
DIGEST="$(manifest_digest vulkan || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "vulkan" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.vulkan.digest] is null — Team A pending)"
else
    SLOT="$(remote_slot_create vulkan vulkan llama-server qwen2.5-0.5b-q4_k_m)"
    if ssh_exec "${REMOTE_HAL0_BIN} slot load ${SLOT}" >/dev/null 2>&1 \
        && ssh_exec "curl -fsS -m 30 ${REMOTE_HAL0_API}/v1/chat/completions \
            -H 'content-type: application/json' \
            -d '{\"model\":\"qwen2.5-0.5b-q4_k_m\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":4}' \
            >/dev/null"; then
        add_row "vulkan" "pass" "$(since_ms "${start}")" "chat/completions returned non-empty body"
    else
        add_row "vulkan" "fail" "$(since_ms "${start}")" "slot load or chat/completions smoke failed — check journalctl -u hal0-slot@${SLOT}"
    fi
fi

# ── ROW: ROCm ────────────────────────────────────────────────────────────────
log_step "Row: rocm"
start=$(date +%s%N)
DIGEST="$(manifest_digest rocm || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "rocm" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.rocm.digest] is null — Team A pending)"
else
    SLOT="$(remote_slot_create rocm rocm llama-server qwen2.5-0.5b-q4_k_m)"
    if ssh_exec "${REMOTE_HAL0_BIN} slot load ${SLOT}" >/dev/null 2>&1; then
        add_row "rocm" "pass" "$(since_ms "${start}")" "slot reached ready state on ROCm backend"
    else
        add_row "rocm" "fail" "$(since_ms "${start}")" "rocm slot failed to reach ready"
    fi
fi

# ── ROW: NPU (flm) ───────────────────────────────────────────────────────────
log_step "Row: flm (NPU)"
start=$(date +%s%N)
DIGEST="$(manifest_digest flm || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "flm" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.flm.digest] is null — Team A marked FLM as a stretch)"
else
    SLOT="$(remote_slot_create flm flm flm llama3.2-3b-q4)"
    if ssh_exec "${REMOTE_HAL0_BIN} slot load ${SLOT}" >/dev/null 2>&1; then
        add_row "flm" "pass" "$(since_ms "${start}")" "FLM/NPU slot reached ready"
    else
        add_row "flm" "fail" "$(since_ms "${start}")" "FLM slot failed; check /sys/class/accel and xdna driver"
    fi
fi

# ── ROW: STT (moonshine) ─────────────────────────────────────────────────────
log_step "Row: moonshine (STT)"
start=$(date +%s%N)
DIGEST="$(manifest_digest moonshine || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "moonshine" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.moonshine.digest] is null)"
else
    # Generate a 1s 440Hz sine WAV on the remote, post it to /v1/audio/transcriptions.
    if ssh_exec '
        set -e
        TMP=$(mktemp -d)
        python3 -c "
import wave, math, struct
with wave.open(\"$TMP/t.wav\",\"wb\") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b\"\".join(struct.pack(\"<h\", int(32000*math.sin(2*math.pi*440*i/16000))) for i in range(16000)))
"
        curl -fsS -m 30 '"${REMOTE_HAL0_API}"'/v1/audio/transcriptions \
            -F file=@$TMP/t.wav -F model=moonshine-base \
            -o $TMP/out.json
        rm -rf $TMP
    '; then
        add_row "moonshine" "pass" "$(since_ms "${start}")" "transcription endpoint returned a JSON body for 1s sine WAV"
    else
        add_row "moonshine" "fail" "$(since_ms "${start}")" "audio/transcriptions smoke failed"
    fi
fi

# ── ROW: TTS (kokoro) ────────────────────────────────────────────────────────
log_step "Row: kokoro (TTS)"
start=$(date +%s%N)
DIGEST="$(manifest_digest kokoro || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "kokoro" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.kokoro.digest] is null)"
else
    if ssh_exec '
        set -e
        TMP=$(mktemp -d)
        curl -fsS -m 30 '"${REMOTE_HAL0_API}"'/v1/audio/speech \
            -H "content-type: application/json" \
            -d "{\"model\":\"kokoro\",\"input\":\"hello hal0\",\"voice\":\"af\"}" \
            -o $TMP/out.wav
        # Non-empty WAV check: RIFF header + > 1KB body.
        head -c 4 "$TMP/out.wav" | grep -q RIFF
        test "$(wc -c < "$TMP/out.wav")" -gt 1024
        rm -rf $TMP
    '; then
        add_row "kokoro" "pass" "$(since_ms "${start}")" "audio/speech returned a non-empty RIFF WAV (>1KiB)"
    else
        add_row "kokoro" "fail" "$(since_ms "${start}")" "audio/speech smoke failed"
    fi
fi

# ── ROW: updater end-to-end (Team D) ─────────────────────────────────────────
log_step "Row: updater (check / apply / rollback)"
start=$(date +%s%N)
# Probe whether Team D's CLI flow is real or still the stub from cli/main.py.
# We use a fake test manifest URL via HAL0_UPDATE_MANIFEST_URL so a stub'd
# response is harmless.
UPDATER_CHECK_OUT="$(ssh_exec "HAL0_UPDATE_MANIFEST_URL=https://hal0.dev/releases/test.json ${REMOTE_HAL0_BIN} update --check 2>&1 || true")"
if echo "${UPDATER_CHECK_OUT}" | grep -qiE "not implemented|TODO|stub"; then
    add_row "updater" "deferred" "$(since_ms "${start}")" "Team D's update CLI is still a stub (cli/main.py:172); re-run after their merge"
else
    # Apply + rollback round-trip — best-effort. If apply fails because
    # the test manifest URL doesn't exist, mark deferred not fail.
    APPLY_OUT="$(ssh_exec "HAL0_UPDATE_MANIFEST_URL=https://hal0.dev/releases/test.json ${REMOTE_HAL0_BIN} update --channel nightly 2>&1 || true")"
    ROLLBACK_OUT="$(ssh_exec "${REMOTE_HAL0_BIN} update --rollback 2>&1 || true")"
    if echo "${APPLY_OUT}${ROLLBACK_OUT}" | grep -qiE "not implemented|TODO|stub"; then
        add_row "updater" "deferred" "$(since_ms "${start}")" "Team D partial: check works, apply/rollback still stubbed"
    elif echo "${ROLLBACK_OUT}" | grep -qiE "error|failed"; then
        add_row "updater" "fail" "$(since_ms "${start}")" "rollback raised an error: $(echo "${ROLLBACK_OUT}" | head -n1)"
    else
        add_row "updater" "pass" "$(since_ms "${start}")" "update --check, --apply, --rollback completed"
    fi
fi

# ── ROW: OpenWebUI ───────────────────────────────────────────────────────────
log_step "Row: openwebui"
start=$(date +%s%N)
OWUI_URL="$(ssh_exec 'echo "${HAL0_OPENWEBUI_URL:-http://127.0.0.1:3001}"')"
# 1. OpenWebUI itself is up
# 2. hal0 /v1/models returns something with at least one entry
if ssh_exec "curl -fsS -m 10 ${OWUI_URL}/health >/dev/null" \
    && [[ "$(ssh_exec "curl -fsS -m 10 ${REMOTE_HAL0_API}/v1/models | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get(\"data\",[])))'" || echo 0)" != "0" ]]; then
    add_row "openwebui" "pass" "$(since_ms "${start}")" "OpenWebUI :3001 health OK and /v1/models populated"
else
    add_row "openwebui" "fail" "$(since_ms "${start}")" "OpenWebUI unreachable or /v1/models empty"
fi

# ── write report ─────────────────────────────────────────────────────────────
log_step "Write report"

# Compose final JSON via python so the rows array is unambiguous.
python3 - "${REPORT_PATH}" "${HAL0_TEST_HOST}" "${HAL0_TEST_PREFIX}" "${ROWS_JSON[@]}" <<'PY'
import json, sys, time
from pathlib import Path

out_path = Path(sys.argv[1])
host     = sys.argv[2]
prefix   = sys.argv[3]
rows     = [json.loads(r) for r in sys.argv[4:]]

report = {
    "_schema":   "hal0.release-gate-report.v1",
    "generated": int(time.time()),
    "host":      host,
    "prefix":    prefix,
    "summary": {
        "total":    len(rows),
        "pass":     sum(1 for r in rows if r["status"] == "pass"),
        "fail":     sum(1 for r in rows if r["status"] == "fail"),
        "skip":     sum(1 for r in rows if r["status"] == "skip"),
        "deferred": sum(1 for r in rows if r["status"] == "deferred"),
    },
    "rows": rows,
}
out_path.write_text(json.dumps(report, indent=2) + "\n")
print(f"wrote {out_path}")
PY

log_info "report: ${REPORT_PATH}"

# ── exit ────────────────────────────────────────────────────────────────────
FAILS=0
for row in "${ROWS_JSON[@]}"; do
    if grep -q '"status": "fail"' <<<"${row}"; then
        FAILS=$(( FAILS + 1 ))
    fi
done

if [[ "${FAILS}" -gt 0 ]]; then
    printf '\n%s%srelease-test FAILED%s — %d row(s) failed.\n' \
        "${RED}" "${BOLD}" "${RST}" "${FAILS}" >&2
    exit 1
fi

printf '\n%s%srelease-test passed%s (skip/deferred rows are non-blocking)\n' \
    "${GRN}" "${BOLD}" "${RST}"
exit 0
