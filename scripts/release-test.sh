#!/usr/bin/env bash
# shellcheck disable=SC2016  # single-quoted ssh_exec blocks expand on the remote LXC, not locally — intentional throughout
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
#   - The updater row is check-only by design: a headless
#     `hal0 update --rollback` proceeds WITHOUT confirmation
#     (src/hal0/cli/update_commands.py::update) and would revert the very
#     install this gate is exercising.
#
# CLI contract (issue #2050): the slot rows speak the CURRENT CLI —
# `slot create NAME --type <llm|transcription|tts|…> --hardware
# <vulkan|rocm|cpu> -m MODEL` then `slot load NAME`
# (src/hal0/cli/slot_commands.py::slot_create / ::slot_load; load blocks
# until the server-side state machine converges, so a 0 exit == ready).
# Models are discovered from the box's own registry (`hal0 model list
# --json` → /api/models rows carry `type` + `installed`) rather than
# hardcoding ids the box may not have.

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
# `slot delete` needs --force: over a non-tty ssh channel the typer confirm
# prompt cannot be answered and aborts (slot_commands.py::slot_delete).
# Seeded slots we merely loaded (flm) are unloaded, never deleted.
CREATED_SLOTS=()
LOADED_SEEDED_SLOTS=()
# shellcheck disable=SC2329  # invoked via the EXIT trap below
cleanup() {
    if [[ ${#CREATED_SLOTS[@]} -eq 0 && ${#LOADED_SEEDED_SLOTS[@]} -eq 0 ]]; then return; fi
    log_step "Cleanup"
    if [[ ${#LOADED_SEEDED_SLOTS[@]} -gt 0 ]]; then
        for slot in "${LOADED_SEEDED_SLOTS[@]}"; do
            ssh_exec "${REMOTE_HAL0_BIN} slot unload ${slot} 2>/dev/null || true" || true
            log_info "unloaded seeded ${slot}"
        done
    fi
    if [[ ${#CREATED_SLOTS[@]} -gt 0 ]]; then
        for slot in "${CREATED_SLOTS[@]}"; do
            ssh_exec "${REMOTE_HAL0_BIN} slot unload ${slot} 2>/dev/null || true" || true
            ssh_exec "${REMOTE_HAL0_BIN} slot delete ${slot} --force 2>/dev/null || true" || true
            log_info "cleaned up ${slot}"
        done
    fi
}
trap cleanup EXIT

# Track + create a unique slot on the LXC.
# Current create contract (slot_commands.py::slot_create): positional NAME,
# --type (dispatcher slot type), --hardware (vulkan|rocm|cpu), -m/--model
# (required). Provider + runtime profile are inferred from type/hardware
# (e.g. tts+cpu → kokoro, transcription+cpu → moonshine); there is no
# --no-start (creation never starts a slot — loading is `slot load`).
remote_slot_create() {
    # remote_slot_create <suffix> <type> <hardware> <model_id>
    local slot="${HAL0_TEST_PREFIX}-$1" type="$2" hardware="$3" model="$4"
    CREATED_SLOTS+=("${slot}")
    ssh_exec "${REMOTE_HAL0_BIN} slot create ${slot} --type ${type} --hardware ${hardware} -m '${model}'" \
        >/dev/null 2>&1 || true
    echo "${slot}"
}

# First installed registry model of the given dispatcher type, or empty.
# `hal0 model list --json` emits the raw /api/models aggregate; each row
# carries `type` (services/models_service.py::dispatch_type — llm |
# embedding | reranking | transcription | tts | image) and `installed`.
remote_model_for_type() {
    # -c (not a heredoc): the JSON arrives on the pipe, so stdin must stay
    # attached to it rather than being overridden by a heredoc program.
    ssh_exec "${REMOTE_HAL0_BIN} model list --json 2>/dev/null" 2>/dev/null \
        | python3 -c '
import json, sys

want = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
models = data.get("models", []) if isinstance(data, dict) else data
for m in models:
    if m.get("installed") and m.get("type") == want:
        print(m.get("id", ""))
        break
' "$1"
}

# Assigned model of an existing slot (empty if the slot is absent or is a
# model-less grey seed). `hal0 slot list --json` emits the raw /api/slots
# array (slot_commands.py::slot_list).
remote_slot_model() {
    ssh_exec "${REMOTE_HAL0_BIN} slot list --json 2>/dev/null" 2>/dev/null \
        | python3 -c '
import json, sys

name = sys.argv[1]
try:
    slots = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
for s in slots if isinstance(slots, list) else []:
    if s.get("name") == name:
        print(s.get("model") or s.get("model_id") or "")
        break
' "$1"
}

# ── ROW: Vulkan baseline ─────────────────────────────────────────────────────
log_step "Row: vulkan baseline"
start=$(date +%s%N)
DIGEST="$(manifest_digest vulkan || true)"
MODEL="$(remote_model_for_type llm || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "vulkan" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.vulkan.digest] is null — Team A pending)"
elif [[ -z "${MODEL}" ]]; then
    add_row "vulkan" "skip" "$(since_ms "${start}")" "no installed llm model in the registry — pull one (hal0 model pull) or register a staged gguf (hal0 model add)"
else
    SLOT="$(remote_slot_create vulkan llm vulkan "${MODEL}")"
    # Auth: any /v1 call needs the admin bearer when HAL0_ADMIN_KEY is set;
    # source it from api.env the same way the unit's EnvironmentFile does.
    if ssh_exec "${REMOTE_HAL0_BIN} slot load ${SLOT}" >/dev/null 2>&1 \
        && ssh_exec "[ -r /etc/hal0/api.env ] && . /etc/hal0/api.env; \
            curl -fsS -m 60 ${REMOTE_HAL0_API}/v1/chat/completions \
            \${HAL0_ADMIN_KEY:+-H \"Authorization: Bearer \${HAL0_ADMIN_KEY}\"} \
            -H 'content-type: application/json' \
            -d '{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":4}' \
            >/dev/null"; then
        add_row "vulkan" "pass" "$(since_ms "${start}")" "chat/completions answered on a gpu-vulkan slot serving ${MODEL}"
    else
        add_row "vulkan" "fail" "$(since_ms "${start}")" "slot load or chat/completions smoke failed — check journalctl -u hal0-slot@${SLOT}"
    fi
fi

# ── ROW: ROCm ────────────────────────────────────────────────────────────────
log_step "Row: rocm"
start=$(date +%s%N)
DIGEST="$(manifest_digest rocm || true)"
MODEL="$(remote_model_for_type llm || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "rocm" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.rocm.digest] is null — Team A pending)"
elif ! ssh_exec "test -e /dev/kfd"; then
    # gpu-rocm llama.cpp slots refuse loudly on a kfd-less host
    # (providers/_gpu.py::require_kfd_for_gpu_slot) — that refusal is
    # correct behaviour, not a release regression, so this is a skip.
    add_row "rocm" "skip" "$(since_ms "${start}")" "no /dev/kfd on ${HAL0_TEST_HOST} — gpu-rocm slots correctly refuse without an ROCm compute node"
elif [[ -z "${MODEL}" ]]; then
    add_row "rocm" "skip" "$(since_ms "${start}")" "no installed llm model in the registry — pull one (hal0 model pull) or register a staged gguf (hal0 model add)"
else
    SLOT="$(remote_slot_create rocm llm rocm "${MODEL}")"
    if ssh_exec "${REMOTE_HAL0_BIN} slot load ${SLOT}" >/dev/null 2>&1; then
        add_row "rocm" "pass" "$(since_ms "${start}")" "slot reached ready on the gpu-rocm backend serving ${MODEL} (readiness includes the #1922 output-sanity probe)"
    else
        add_row "rocm" "fail" "$(since_ms "${start}")" "rocm slot failed to reach ready — check journalctl -u hal0-slot@${SLOT}"
    fi
fi

# ── ROW: NPU (flm) ───────────────────────────────────────────────────────────
log_step "Row: flm (NPU)"
start=$(date +%s%N)
DIGEST="$(manifest_digest flm || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "flm" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.flm.digest] is null — Team A marked FLM as a stretch)"
elif ! ssh_exec "test -e /dev/accel/accel0"; then
    add_row "flm" "skip" "$(since_ms "${start}")" "npu-not-present (/dev/accel/accel0 missing on ${HAL0_TEST_HOST})"
else
    # `slot create --hardware` only speaks vulkan|rocm|cpu — npu has no
    # v0.1 hardware token (slot_commands.py::SlotHardware), so this row
    # exercises the SEEDED flm slot (installer/etc-hal0/slots/flm.toml,
    # device=npu). The seed ships model-less (grey tile, #1369); a box
    # with no FLM model assigned skips rather than fails.
    FLM_MODEL="$(remote_slot_model flm || true)"
    if [[ -z "${FLM_MODEL}" ]]; then
        add_row "flm" "skip" "$(since_ms "${start}")" "seeded flm slot absent or model-less (grey seed, #1369) — assign an FLM model to it first"
    else
        LOADED_SEEDED_SLOTS+=("flm")
        if ssh_exec "${REMOTE_HAL0_BIN} slot load flm" >/dev/null 2>&1; then
            add_row "flm" "pass" "$(since_ms "${start}")" "seeded flm slot (device=npu) reached ready serving ${FLM_MODEL}"
        else
            add_row "flm" "fail" "$(since_ms "${start}")" "FLM slot failed to load; check /sys/class/accel and the xdna driver"
        fi
    fi
fi

# ── ROW: STT (moonshine) ─────────────────────────────────────────────────────
log_step "Row: moonshine (STT)"
start=$(date +%s%N)
DIGEST="$(manifest_digest moonshine || true)"
MODEL="$(remote_model_for_type transcription || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "moonshine" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.moonshine.digest] is null)"
elif [[ -z "${MODEL}" ]]; then
    add_row "moonshine" "skip" "$(since_ms "${start}")" "no installed transcription model in the registry — pull/register a moonshine model first"
else
    # A transcription+cpu slot infers the moonshine provider + profile
    # (slot_commands.py::slot_create help; install/profile_derive.py).
    SLOT="$(remote_slot_create moonshine transcription cpu "${MODEL}")"
    # Generate a 1s 440Hz sine WAV on the remote, post it to
    # /v1/audio/transcriptions. The `model` form field is REQUIRED by the
    # gateway (v1.py::audio_transcriptions, require_model=True) and routes
    # to the slot bound to that model id; the moonshine child itself does
    # not validate the field, so the registry id we just bound is correct.
    # Auth header is required on any box with HAL0_ADMIN_KEY set
    # (unauthenticated is 401); sourced from api.env like an operator would.
    if ssh_exec "${REMOTE_HAL0_BIN} slot load ${SLOT}" >/dev/null 2>&1 \
        && ssh_exec '
        set -e
        TMP=$(mktemp -d)
        python3 -c "
import wave, math, struct
with wave.open(\"$TMP/t.wav\",\"wb\") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b\"\".join(struct.pack(\"<h\", int(32000*math.sin(2*math.pi*440*i/16000))) for i in range(16000)))
"
        set +u; [ -r /etc/hal0/api.env ] && . /etc/hal0/api.env; set -u
        curl -fsS -m 60 '"${REMOTE_HAL0_API}"'/v1/audio/transcriptions \
            ${HAL0_ADMIN_KEY:+-H "Authorization: Bearer $HAL0_ADMIN_KEY"} \
            -F file=@$TMP/t.wav -F model='"'${MODEL}'"' \
            -o $TMP/out.json
        rm -rf $TMP
    '; then
        add_row "moonshine" "pass" "$(since_ms "${start}")" "transcription slot loaded (${MODEL}) and /v1/audio/transcriptions answered for a 1s sine WAV"
    else
        add_row "moonshine" "fail" "$(since_ms "${start}")" "slot load or audio/transcriptions smoke failed — check journalctl -u hal0-slot@${SLOT}"
    fi
fi

# ── ROW: TTS (kokoro) ────────────────────────────────────────────────────────
log_step "Row: kokoro (TTS)"
start=$(date +%s%N)
DIGEST="$(manifest_digest kokoro || true)"
MODEL="$(remote_model_for_type tts || true)"
if [[ -z "${DIGEST}" ]]; then
    add_row "kokoro" "skip" "$(since_ms "${start}")" "image-not-available (manifest.json[toolbox_images.kokoro.digest] is null)"
elif [[ -z "${MODEL}" ]]; then
    add_row "kokoro" "skip" "$(since_ms "${start}")" "no installed tts model in the registry — pull/register a kokoro model first"
else
    # A tts+cpu slot infers the kokoro profile (install/profile_derive.py:
    # cpu + tts → kokoro). The gateway requires `model` in the body
    # (v1.py::audio_speech) and routes it to the tts slot bound to that id.
    # `voice` is optional (the kokoro server falls back to its default
    # voice); response_format must be requested as wav explicitly — the
    # kokoro server's default is mp3, which would fail the RIFF check.
    SLOT="$(remote_slot_create kokoro tts cpu "${MODEL}")"
    if ssh_exec "${REMOTE_HAL0_BIN} slot load ${SLOT}" >/dev/null 2>&1 \
        && ssh_exec '
        set -e
        TMP=$(mktemp -d)
        set +u; [ -r /etc/hal0/api.env ] && . /etc/hal0/api.env; set -u
        curl -fsS -m 60 '"${REMOTE_HAL0_API}"'/v1/audio/speech \
            ${HAL0_ADMIN_KEY:+-H "Authorization: Bearer $HAL0_ADMIN_KEY"} \
            -H "content-type: application/json" \
            -d "{\"model\":\"'"${MODEL}"'\",\"input\":\"hello hal0\",\"response_format\":\"wav\"}" \
            -o $TMP/out.wav
        # Non-empty WAV check: RIFF header + > 1KB body.
        head -c 4 "$TMP/out.wav" | grep -q RIFF
        test "$(wc -c < "$TMP/out.wav")" -gt 1024
        rm -rf $TMP
    '; then
        add_row "kokoro" "pass" "$(since_ms "${start}")" "tts slot loaded (${MODEL}) and audio/speech returned a non-empty RIFF WAV (>1KiB)"
    else
        add_row "kokoro" "fail" "$(since_ms "${start}")" "slot load or audio/speech smoke failed — check journalctl -u hal0-slot@${SLOT}"
    fi
fi

# ── ROW: updater (check-only) ────────────────────────────────────────────────
log_step "Row: updater (check)"
start=$(date +%s%N)
# Check-only, deliberately. The old apply/rollback round-trip is retired:
#   - a headless `hal0 update --rollback` proceeds WITHOUT confirmation
#     (update_commands.py::update — no TTY means no prompt) and would
#     genuinely revert the install tree this gate is exercising;
#   - `--channel nightly` PERSISTS a channel change on the box;
#   - the old HAL0_UPDATE_MANIFEST_URL "safety net" never existed in the
#     product (the updater reads HAL0_RELEASES_URL, and the check runs in
#     the daemon via GET /api/updates/check, so a CLI-side env var never
#     reached it anyway).
# `hal0 update --check` is a real, side-effect-free verb; exit 0 means the
# daemon answered /api/updates/check.
if ssh_exec "${REMOTE_HAL0_BIN} update --check" >/dev/null 2>&1; then
    add_row "updater" "pass" "$(since_ms "${start}")" "update --check completed (daemon answered /api/updates/check)"
else
    add_row "updater" "fail" "$(since_ms "${start}")" "hal0 update --check failed — daemon down or /api/updates/check errored"
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
