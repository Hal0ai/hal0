#!/usr/bin/env bash
# migrate-qwen3tts-to-slot.sh
#
# Retire the standalone hal0-qwen3tts.service and repoint the Hermes TTS
# bridge to the hal0 `tts` slot serving Qwen3 via the voice.tts capability
# selection (the deployed "selection within the tts slot" model — there is NO
# separate qwen3tts slot). The forward migration was completed on CT105
# 2026-06-29; this script remains for re-runs / fresh boxes and as the
# rollback path.
#
# SAFETY:
#   - Default mode is --dry-run. Nothing is changed unless you pass --apply.
#   - All guards must pass before any state-changing step runs.
#   - Edits to /var/lib/hal0/.hermes/ are done as the `hal0` user (never root)
#     to preserve ownership and keep the Hermes gateway online.
#
# USAGE:
#   bash scripts/migrate-qwen3tts-to-slot.sh [--dry-run] [--apply] [--rollback]
#
# FLAGS:
#   --dry-run   Print what would be done; make no changes. (DEFAULT)
#   --apply     Run the full migration after all guards pass.
#   --rollback  Revert a previously applied migration.
#
# PRECONDITIONS (enforced by guards before --apply acts):
#   1. hal0 `tts` slot is state == "ready" AND serving model "qwen3-tts"
#      (i.e. the voice.tts selection = qwen3/gpu-rocm has been applied —
#      e.g. via `hal0-voice qwen3` or the dashboard)
#   2. /v1/audio/speech via hal0 front door (:8080) returns audio (non-empty WAV)
#   3. /var/lib/hal0/.hermes/scripts/hal0-voice-tts.py contains the old URL
#      (idempotency: skip bridge edit if already pointing to :8080)
#
# ROLLBACK:
#   Reverts the bridge URL edit, re-enables and starts hal0-qwen3tts.service.
#
# PORT CONTEXT:
#   Qwen3 runs in the `tts` slot (port 8084), reached via the :8080 front
#   door — NOT on :8095. The standalone service used :8095; stopping it just
#   frees the duplicate GPU load. There is no port collision in this model.
#
# OWNERSHIP GOTCHA:
#   /var/lib/hal0/.hermes/ must stay owned by hal0:hal0. Root edits flip
#   ownership and take the Hermes gateway offline. This script uses
#   `sudo -u hal0` for all edits inside that directory.

set -euo pipefail

# --- Configuration -----------------------------------------------------------

HAL0_API="http://127.0.0.1:8080"
TTS_SLOT_API="${HAL0_API}/api/slots/tts"
EXPECTED_TTS_MODEL="qwen3-tts"
STANDALONE_UNIT="hal0-qwen3tts.service"
BRIDGE_SCRIPT="/var/lib/hal0/.hermes/scripts/hal0-voice-tts.py"
OLD_TTS_URL="http://127.0.0.1:8095/v1/audio/speech"
NEW_TTS_URL="http://127.0.0.1:8080/v1/audio/speech"

# --- Argument parsing ---------------------------------------------------------

MODE="dry-run"
for arg in "$@"; do
    case "$arg" in
        --apply)    MODE="apply" ;;
        --dry-run)  MODE="dry-run" ;;
        --rollback) MODE="rollback" ;;
        -h|--help)
            sed -n '/^# USAGE:/,/^# [A-Z]/p' "$0" | head -n -1
            exit 0
            ;;
        *)
            echo "error: unknown flag: $arg" >&2
            echo "usage: $0 [--dry-run] [--apply] [--rollback]" >&2
            exit 2
            ;;
    esac
done

# --- Helpers ------------------------------------------------------------------

log()   { echo "[$(date -Iseconds)] $*"; }
ok()    { echo "[$(date -Iseconds)] OK  $*"; }
fail()  { echo "[$(date -Iseconds)] ERR $*" >&2; }
step()  { echo; echo "==> $*"; }

dry_or_run() {
    # In dry-run mode: print the command, do not run it.
    # In apply/rollback mode: run it.
    if [ "$MODE" = "dry-run" ]; then
        echo "  [dry-run] would run: $*"
    else
        "$@"
    fi
}

# --- Guards -------------------------------------------------------------------

guard_slot_ready() {
    step "Guard 1: tts slot is READY and serving ${EXPECTED_TTS_MODEL}"
    local resp
    resp=$(curl -sf "${TTS_SLOT_API}" 2>/dev/null) || {
        fail "Could not reach hal0 API at ${TTS_SLOT_API}"
        fail "Is hal0-api running?"
        return 1
    }
    local state model
    state=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('state','?'))" 2>/dev/null) || {
        fail "Could not parse slot state from API response: $resp"
        return 1
    }
    model=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('model_id','?'))" 2>/dev/null) || model="?"
    if [ "$state" != "ready" ]; then
        fail "tts slot state is '${state}', expected 'ready'"
        fail "Apply the Qwen3 voice selection first: hal0-voice qwen3 (or set it in the dashboard)"
        return 1
    fi
    if [ "$model" != "${EXPECTED_TTS_MODEL}" ]; then
        fail "tts slot is serving '${model}', expected '${EXPECTED_TTS_MODEL}'"
        fail "Select the Qwen3 engine first: hal0-voice qwen3"
        return 1
    fi
    ok "tts slot ready, serving ${EXPECTED_TTS_MODEL}"
}

guard_audio_speech() {
    step "Guard 2: /v1/audio/speech via hal0 front door returns audio"
    local tmp
    tmp=$(mktemp /tmp/tts-migration-probe.XXXXXX.wav)
    local http_code size
    http_code=$(curl -s -o "$tmp" -w "%{http_code}" \
        -X POST "${HAL0_API}/v1/audio/speech" \
        -H "Content-Type: application/json" \
        -d '{"model":"qwen3-tts","input":"migration probe","voice":"Ryan","response_format":"wav"}' \
        --max-time 90 2>/dev/null) || http_code=000
    size=0
    [ -f "$tmp" ] && size=$(wc -c < "$tmp" 2>/dev/null || echo 0)
    rm -f "$tmp"

    if [ "$http_code" != "200" ]; then
        fail "/v1/audio/speech returned HTTP ${http_code} (expected 200)"
        fail "The qwen3tts slot must be READY and serving before migration"
        return 1
    fi
    if [ "$size" -lt 100 ]; then
        fail "/v1/audio/speech returned ${size} bytes (expected non-empty WAV)"
        return 1
    fi
    ok "/v1/audio/speech -> HTTP 200, ${size} bytes"
}

guard_bridge_script_exists() {
    step "Guard 3: bridge script exists"
    if [ ! -f "$BRIDGE_SCRIPT" ]; then
        fail "Bridge script not found: ${BRIDGE_SCRIPT}"
        return 1
    fi
    ok "bridge script exists: ${BRIDGE_SCRIPT}"
}

run_guards() {
    local failed=0
    guard_slot_ready  || failed=1
    guard_audio_speech || failed=1
    guard_bridge_script_exists || failed=1
    if [ "$failed" -ne 0 ]; then
        echo
        fail "One or more guards failed. Fix the issues above before re-running."
        exit 1
    fi
    echo
    ok "All guards passed."
}

# --- Migration steps ----------------------------------------------------------

step_stop_standalone() {
    step "Stop and disable standalone hal0-qwen3tts.service"
    if ! systemctl is-active --quiet "$STANDALONE_UNIT" 2>/dev/null; then
        log "  ${STANDALONE_UNIT} is already stopped — skipping stop"
    else
        dry_or_run systemctl stop "$STANDALONE_UNIT"
    fi
    if ! systemctl is-enabled --quiet "$STANDALONE_UNIT" 2>/dev/null; then
        log "  ${STANDALONE_UNIT} is already disabled — skipping disable"
    else
        dry_or_run systemctl disable "$STANDALONE_UNIT"
    fi
    ok "standalone service stopped and disabled"
}

step_repoint_bridge() {
    step "Repoint Hermes TTS bridge: 8095 -> 8080"

    if ! grep -qF "$OLD_TTS_URL" "$BRIDGE_SCRIPT" 2>/dev/null; then
        log "  Bridge already points to ${NEW_TTS_URL} or URL not found — skipping (idempotent)"
        ok "bridge already updated (no-op)"
        return
    fi

    # CRITICAL: edit as hal0 user, not root.
    # Root edits flip ownership on /var/lib/hal0/.hermes/ and take the
    # Hermes gateway offline. Always use `sudo -u hal0`.
    if [ "$MODE" = "dry-run" ]; then
        echo "  [dry-run] would run as hal0 user:"
        echo "    sed -i 's|${OLD_TTS_URL}|${NEW_TTS_URL}|g' ${BRIDGE_SCRIPT}"
    else
        # Verify we can sudo to hal0 before attempting
        if ! sudo -n -u hal0 true 2>/dev/null; then
            fail "Cannot sudo -u hal0 (passwordless sudo not configured for this user)"
            fail "Add: $(whoami) ALL=(hal0) NOPASSWD: /usr/bin/sed to sudoers"
            exit 1
        fi
        sudo -u hal0 sed -i \
            "s|${OLD_TTS_URL}|${NEW_TTS_URL}|g" \
            "$BRIDGE_SCRIPT"
    fi
    ok "bridge script updated: ${OLD_TTS_URL} -> ${NEW_TTS_URL}"
}

step_verify_bridge() {
    step "Verify bridge edit"
    if grep -qF "$NEW_TTS_URL" "$BRIDGE_SCRIPT" 2>/dev/null; then
        ok "bridge now contains: ${NEW_TTS_URL}"
    elif [ "$MODE" = "dry-run" ]; then
        log "  [dry-run] would verify bridge contains: ${NEW_TTS_URL}"
    else
        fail "Bridge does NOT contain expected URL after edit!"
        fail "Check ${BRIDGE_SCRIPT} manually."
        exit 1
    fi
    if grep -qF "$OLD_TTS_URL" "$BRIDGE_SCRIPT" 2>/dev/null; then
        fail "Bridge STILL contains old URL: ${OLD_TTS_URL}"
        fail "Sed replacement may have partially failed."
        exit 1
    fi
}

step_verify_e2e() {
    step "End-to-end verification: TTS bridge call via hal0 slot"
    if [ "$MODE" = "dry-run" ]; then
        echo "  [dry-run] would run bridge script as hal0 user and verify WAV output"
        return
    fi
    local tmp_in tmp_out
    tmp_in=$(mktemp /tmp/tts-migrate-in.XXXXXX.txt)
    tmp_out=$(mktemp /tmp/tts-migrate-out.XXXXXX.wav)
    echo "Migration complete. Qwen3 TTS is now running as a hal0 slot." > "$tmp_in"
    if sudo -u hal0 python3 "$BRIDGE_SCRIPT" "$tmp_in" "$tmp_out" 2>&1; then
        local size
        size=$(wc -c < "$tmp_out" 2>/dev/null || echo 0)
        ok "bridge call succeeded: ${size} bytes written to ${tmp_out}"
    else
        fail "bridge call failed — check stderr above"
        rm -f "$tmp_in" "$tmp_out"
        exit 1
    fi
    rm -f "$tmp_in" "$tmp_out"
}

# --- Rollback steps -----------------------------------------------------------

step_rollback_bridge() {
    step "Rollback: revert bridge URL 8080 -> 8095"
    if ! grep -qF "$NEW_TTS_URL" "$BRIDGE_SCRIPT" 2>/dev/null; then
        log "  Bridge already contains ${OLD_TTS_URL} or neither URL found — skipping"
        ok "bridge rollback: no-op"
        return
    fi
    if [ "$MODE" = "dry-run" ]; then
        echo "  [dry-run] would run as hal0 user:"
        echo "    sed -i 's|${NEW_TTS_URL}|${OLD_TTS_URL}|g' ${BRIDGE_SCRIPT}"
    else
        if ! sudo -n -u hal0 true 2>/dev/null; then
            fail "Cannot sudo -u hal0"
            exit 1
        fi
        sudo -u hal0 sed -i \
            "s|${NEW_TTS_URL}|${OLD_TTS_URL}|g" \
            "$BRIDGE_SCRIPT"
    fi
    ok "bridge reverted: ${NEW_TTS_URL} -> ${OLD_TTS_URL}"
}

step_rollback_standalone() {
    step "Rollback: re-enable and start standalone hal0-qwen3tts.service"
    dry_or_run systemctl enable "$STANDALONE_UNIT"
    dry_or_run systemctl start "$STANDALONE_UNIT"
    if [ "$MODE" != "dry-run" ]; then
        local tries=0
        while [ "$tries" -lt 12 ]; do
            local code
            code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
                http://127.0.0.1:8095/health 2>/dev/null) || code=000
            [ "$code" = "200" ] && { ok "standalone back online: :8095/health = 200"; return; }
            tries=$((tries + 1))
            sleep 5
        done
        fail "standalone did not come back online within 60s — check journalctl -u ${STANDALONE_UNIT}"
        exit 1
    fi
    ok "standalone re-enabled and started (dry-run)"
}

# --- Main entry points --------------------------------------------------------

do_apply() {
    log "Mode: APPLY (live changes will be made)"
    echo

    run_guards
    step_stop_standalone
    step_repoint_bridge
    step_verify_bridge
    step_verify_e2e

    echo
    ok "Migration complete."
    log "  Standalone hal0-qwen3tts.service is stopped and disabled."
    log "  Hermes bridge now routes qwen3 TTS through hal0 slot via :8080."
    log "  The 'hal0-voice' switch continues to work (reads tts_voice.conf)."
    log "  Run 'hal0-voice status' to confirm :8095 is now served by the slot."
    log "  See handoffs/qwen3tts-standalone-to-slot-migration-2026-06-28.md §7 for post-migration cleanup."
}

do_dry_run() {
    log "Mode: DRY-RUN (no changes will be made)"
    echo

    # Guards still run to report precondition status.
    run_guards
    step_stop_standalone
    step_repoint_bridge
    step_verify_bridge
    step_verify_e2e

    echo
    log "Dry-run complete. Re-run with --apply to make changes."
}

do_rollback() {
    log "Mode: ROLLBACK"
    echo

    step_rollback_bridge
    step_rollback_standalone

    echo
    ok "Rollback complete."
    log "  Standalone hal0-qwen3tts.service re-enabled and started."
    log "  Hermes bridge reverted to ${OLD_TTS_URL}."
    log "  Check: curl -s http://127.0.0.1:8095/health"
}

# --- Dispatch -----------------------------------------------------------------

case "$MODE" in
    apply)    do_apply ;;
    dry-run)  do_dry_run ;;
    rollback) do_rollback ;;
esac
