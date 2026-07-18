# Handoff: qwen3tts standalone → hal0-slot migration

**Status:** PREP ONLY — do not execute until all preconditions are verified.
**Author context:** Written from live inspection of CT105 on 2026-06-28. Read
all of §2 (preconditions) before touching anything.

---

## 1. Background

CT105 runs two TTS services that share the same underlying model
(`Qwen3-TTS-12Hz-1.7B-CustomVoice`) and the same OpenAI `/v1/audio/speech`
contract:

| Service | How started | Port | GPU | Managed by |
|---|---|---|---|---|
| `hal0-qwen3tts.service` | standalone systemd + podman | `127.0.0.1:8095` | yes (ROCm, gfx1151 native) | manual |
| `hal0-slot@qwen3tts` | hal0 `ContainerProvider` | `127.0.0.1:8095` (TOML default) | yes (ROCm, tts-qwen3 profile) | hal0-api |

The standalone service pre-dates the hal0 container-runtime slot model (PR
#972 + follow-ups) and exists because hal0's `type=tts` slot path was
previously CPU-only. Now that the hal0-native qwen3tts slot TOML
(`installer/etc-hal0/slots/qwen3tts.toml`) ships and is deployed after a
release reinstall, the standalone service can be retired — but only once the
slot is verified healthy.

The Hermes TTS bridge (`/var/lib/hal0/.hermes/scripts/hal0-voice-tts.py`)
currently reaches qwen3tts at `http://127.0.0.1:8095/v1/audio/speech` (the
standalone service). The migration repoints it to the hal0 front door
(`http://127.0.0.1:8080/v1/audio/speech`) so hal0-api dispatches TTS calls
through the managed slot, keeping the `hal0-voice {qwen3|kokoro}` switch
working.

---

## 2. Port collision — critical

**Both the standalone service and the slot TOML default to port 8095.**
They cannot both bind `127.0.0.1:8095` simultaneously. The cutover sequence
must respect this:

**Recommended approach:** Stop and disable the standalone service first, then
enable and start the hal0 slot. This creates a brief (~30–90 s) window where
no qwen3tts backend is available, during which the Hermes fallback engine
(kokoro on :8084) handles any TTS requests.

**Alternative (zero-downtime):** Change the slot TOML to a different port (e.g.
`8096`) before enabling it, let hal0 start the slot there, verify it, then stop
the standalone. Requires editing `/etc/hal0/slots/qwen3tts.toml` and updating
`QWEN3TTS_URL` accordingly. This is more complex and not recommended unless
voice continuity during the cutover is critical (Hermes already has a kokoro
fallback, so the brief window is acceptable).

The migration script (`scripts/migrate-qwen3tts-to-slot.sh`) implements the
stop-first approach. If you need zero-downtime, see the `--alt-port` notes in
that script's comments.

---

## 3. Preconditions (all must be true before running the script)

1. **hal0 ≥ the release that ships PR #972 is deployed on CT105.**
   Verify: `hal0 version` shows a release that includes the qwen3tts slot
   TOML in `installer/etc-hal0/slots/`. A fresh `hal0 update` or reinstall is
   required to drop `/etc/hal0/slots/qwen3tts.toml` onto the host.

2. **`/etc/hal0/slots/qwen3tts.toml` exists and `enabled = true`.**
   Verify: `cat /etc/hal0/slots/qwen3tts.toml | grep enabled`
   The TOML ships with `enabled = false`. Edit it (as root) to `enabled = true`
   before loading the slot. Alternatively: `hal0 slot enable qwen3tts` if that
   CLI is available.

3. **`hal0-slot@qwen3tts.service` is loaded and READY.**
   Verify: `curl -s http://127.0.0.1:8080/api/slots/qwen3tts | python3 -m json.tool`
   The state field must be `"ready"`.

4. **`/v1/audio/speech` is served through the hal0 front door for qwen3tts.**
   The migration script probes this directly (see §5 below). A `200 OK` with
   non-empty body is required.

5. **The `tts` slot (kokoro) is ready on `:8084`.**
   Hermes's fallback leg uses kokoro. The migration script checks this too.

---

## 4. Hermes bridge repoint (the one file that changes)

**File:** `/var/lib/hal0/.hermes/scripts/hal0-voice-tts.py`

**Env var:** `QWEN3TTS_URL` (read at the top of the script)

**Current default (hardcoded in script):**
```python
QWEN3_URL = os.environ.get("QWEN3TTS_URL", "http://127.0.0.1:8095/v1/audio/speech")
```

**New default after migration:**
```python
QWEN3_URL = os.environ.get("QWEN3TTS_URL", "http://127.0.0.1:8080/v1/audio/speech")
```

The script's env-var override path (`QWEN3TTS_URL`) is preserved so the URL
can still be overridden without editing the file. The migration script edits
the fallback default in the script (the `http://127.0.0.1:8095` string) to
`http://127.0.0.1:8080`.

**Ownership constraint:** `/var/lib/hal0/.hermes/` is `hal0:hal0` owned. Editing
as root flips ownership and takes the Hermes gateway offline (see the `hal0
operational gotchas` memory). The migration script runs the file edit as the
`hal0` user via `sudo -u hal0`. Never edit this file as root.

**`hal0-voice` status script:** `/usr/local/bin/hal0-voice status` also probes
`:8095` directly for the qwen3 health check line. After migration, `:8095` will
be gone (the slot may run there, but via hal0 management). The `hal0-voice`
script can optionally be updated to probe via the hal0 API instead, but this is
cosmetic — the TTS bridge itself will be correctly routed.

---

## 5. Step-by-step cutover

All steps are automated by `scripts/migrate-qwen3tts-to-slot.sh --apply`.
This section documents the manual equivalent for reference and verification.

### Phase A — Verify slot readiness (guard)

```bash
# 1. Slot exists and is READY
curl -sf http://127.0.0.1:8080/api/slots/qwen3tts \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('state')=='ready', f\"slot state={d.get('state')}\""

# 2. /v1/audio/speech returns audio via the front door
tmp=$(mktemp /tmp/tts-probe.XXXXXX.wav)
curl -sf http://127.0.0.1:8080/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-tts","input":"hal0 migration probe","voice":"Ryan","response_format":"wav"}' \
  -o "$tmp" && [ -s "$tmp" ] && echo "audio OK: $(wc -c < "$tmp") bytes"
rm -f "$tmp"

# 3. Kokoro fallback is also up
curl -sf http://127.0.0.1:8084/health | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status')=='ok', d"
```

### Phase B — Stop standalone service

```bash
systemctl stop hal0-qwen3tts.service
systemctl disable hal0-qwen3tts.service
# Verify port 8095 is free or now owned by the hal0 slot container
ss -tlnp | grep 8095
```

### Phase C — Repoint Hermes bridge

```bash
# Edit as hal0 user (NEVER as root)
sudo -u hal0 sed -i \
  's|http://127\.0\.0\.1:8095/v1/audio/speech|http://127.0.0.1:8080/v1/audio/speech|g' \
  /var/lib/hal0/.hermes/scripts/hal0-voice-tts.py

# Verify the change
grep 'QWEN3_URL\|8095\|8080' /var/lib/hal0/.hermes/scripts/hal0-voice-tts.py
```

### Phase D — Verify end-to-end

```bash
# 1. Bridge script reads new URL
grep 'QWEN3_URL' /var/lib/hal0/.hermes/scripts/hal0-voice-tts.py

# 2. Test a live TTS call through the bridge
tmp_in=$(mktemp /tmp/tts-in.XXXXXX.txt)
tmp_out=$(mktemp /tmp/tts-out.XXXXXX.wav)
echo "Migration complete. Qwen3 TTS is now running as a hal0 slot." > "$tmp_in"
sudo -u hal0 python3 /var/lib/hal0/.hermes/scripts/hal0-voice-tts.py "$tmp_in" "$tmp_out"
echo "output wav: $(wc -c < "$tmp_out") bytes"
rm -f "$tmp_in" "$tmp_out"

# 3. hal0-voice status (cosmetic — :8095 probe changes meaning after migration)
hal0-voice status
```

---

## 6. Rollback

If anything fails after Phase C, revert in reverse order:

```bash
# 1. Revert Hermes bridge
sudo -u hal0 sed -i \
  's|http://127\.0\.0\.1:8080/v1/audio/speech|http://127.0.0.1:8095/v1/audio/speech|g' \
  /var/lib/hal0/.hermes/scripts/hal0-voice-tts.py

# 2. Re-enable and start the standalone service
systemctl enable hal0-qwen3tts.service
systemctl start hal0-qwen3tts.service

# 3. Verify standalone is healthy again
curl -s http://127.0.0.1:8095/health
```

The migration script (`scripts/migrate-qwen3tts-to-slot.sh --rollback`)
automates this.

---

## 7. Post-migration cleanup (optional, after verification)

Once the slot has been running stably for several days:

- Remove `/etc/systemd/system/hal0-qwen3tts.service` (keep a backup first).
- Run `systemctl daemon-reload`.
- The `/var/lib/hal0/qwen3tts-cache/` MIOpen cache directory can be reused by
  the slot container (mount it in the slot profile if needed) or removed.
- Update `hal0-voice status` to probe the hal0 API (`/api/slots/qwen3tts`)
  instead of `:8095` directly.

---

## 8. Notes on the hal0 slot TOML

`installer/etc-hal0/slots/qwen3tts.toml` ships with:
- `port = 8095` — same port as standalone; no conflict once standalone stops.
- `enabled = false` — intentional; must be set to `true` before enabling.
- `profile = "tts-qwen3"` — expects the `tts-qwen3` profile in
  `profiles.toml` defining the ROCm container image, GPU passthrough args,
  MIOpen env, and model path binding.
- `device = "gpu-rocm"` — run on native gfx1151; do NOT override to gfx1100
  (MIOpen GEMM fallbacks are ~9.5× slower; native is ~2.1× realtime).

Verify the `tts-qwen3` profile exists: `grep -A5 'tts-qwen3' /etc/hal0/profiles.toml`

---

## 9. What was inspected (read-only; live state unchanged)

- `cat /etc/systemd/system/hal0-qwen3tts.service`
- `systemctl status hal0-qwen3tts.service --no-pager`
- `cat /var/lib/hal0/.hermes/scripts/hal0-voice-tts.py`
- `cat /var/lib/hal0/.hermes/tts_voice.conf`
- `cat /var/lib/hal0/.hermes/gateway_voice_mode.json`
- `grep "tts:" /var/lib/hal0/.hermes/config.yaml`
- `grep -r "hal0-voice-tts\|QWEN3TTS_URL" /var/lib/hal0/.hermes/` (read-only grep)
- `cat /usr/local/bin/hal0-voice`
- `curl -s http://127.0.0.1:8080/api/slots` (read-only)
- `curl -s http://127.0.0.1:8080/api/slots/qwen3tts` (read-only)
- `curl -s http://127.0.0.1:8080/api/health` (read-only)
- `curl -s http://127.0.0.1:8095/health` (read-only)
- `curl -s http://127.0.0.1:8080/v1/audio/speech ... -o /dev/null -w %{http_code}` (read-only probe)
- `cat /etc/hal0/slots/qwen3tts.toml` (NOT FOUND — slot not yet deployed)
