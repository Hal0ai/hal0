# hal0 Voice Stack Bring-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take hal0 voice from "wired, never lit" to running and verifiably tested on hermes (CT105) — TTS (Kokoro), STT (Whisper, CPU then NPU), agent voice tools, and hands-free voice via Open WebUI Call mode.

**Architecture:** Five operational phases (P1–P5) plus a code change and a docs fix. A reusable text→speech→text round-trip test is the bar for "tested." Runtime mutations go through the `hal0-admin` MCP tools against CT105 (10.0.1.142); the one code change is to `env_writer.py` (Open WebUI voice env); docs corrections land in hal0-web on a branch off `master`.

**Tech Stack:** Python 3.12, pytest (`pytest-asyncio`, `auto` mode), httpx, FastAPI, Kokoro-82M (ONNX/CPU), Whisper (whispercpp/CPU + FLM/NPU), Open WebUI, podman containers, systemd template units (`hal0-slot@.service`).

## Global Constraints

- **Shared host:** CT105 `/opt/hal0` (10.0.1.142) is a shared dev host. Run `~/.claude/bin/wip hal0 status` then `~/.claude/bin/wip hal0 claim "voice bring-up" <files>` before ANY slot/image/service mutation. Never `git checkout`/`reset`/deploy over another session. `wip hal0 release` when done.
- **Prefer MCP over SSH:** Use `hal0-admin` MCP tools (`model_pull`, `slot_create`, `slot_restart`, `slot_status`, `slot_list`, `capability_set`, `capability_list`, `model_store_probe`, `model_list`, `npu_status`) for mutations. Use read-only `curl http://10.0.1.142:8080/api/*` and read-only SSH only for inspection.
- **Branches:** hal0 code work on `feat/voice-stack` (worktree `/home/halo/dev/wt/voice-stack`, base `origin/main`). hal0-web doc work on a NEW branch off `master` (a live session holds `docs/model-roster-benchmark`).
- **Models (exact ids):** TTS `kokoro-v1` (CPU, present). STT P2 `Whisper-Large-v3-Turbo` (whispercpp/CPU, must pull, ~1.62 GB). STT P5 `whisper-v3:turbo` (FLM/NPU, present, ~0.93 GB). Agent `ace-saber` (unchanged).
- **Tests:** `pytest -ra --strict-markers`; any new marker MUST be registered in `pyproject.toml [tool.pytest.ini_options] markers`. Live integration tests must SKIP when their env var is unset so the default `pytest tests/` pass stays green offline.
- **Round-trip bar:** normalized fuzzy match ratio ≥ **0.8** (difflib) between the spoken input text and the re-transcribed text.
- **Commit trailer (every commit):**
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## File Structure

- `tests/harness/integration/test_voice_roundtrip.py` — *create*. Pure `normalized_match()` helper (unit-tested) + a live round-trip test guarded by `HAL0_VOICE_LIVE_URL`. The reusable gate for P2 (CPU) and P5 (NPU).
- `src/hal0/openwebui/env_writer.py` — *modify*. Add Open WebUI voice (Call-mode) env keys.
- `tests/openwebui/test_env_writer.py` — *modify*. Assert the new voice keys are emitted.
- hal0-web `src/pages/index.astro` + voice docs — *modify on a separate branch*. Fix the stale "Moonshine" STT claim and reword "voice mode end-to-end".

Runtime/ops tasks (P1, P3, P4-deploy, P5) produce no repo commit; their deliverable is a verified runtime state with evidence saved under `/tmp/voice-bringup/` on the runner and a one-line note recorded to hal0 memory at the end.

---

### Task 1: Pre-flight & coordination

**Files:** none (ops). Evidence → `/tmp/voice-bringup/baseline.txt`.

**Interfaces:**
- Produces: a confirmed claim on the hal0 runtime + a baseline snapshot later tasks diff against.

- [ ] **Step 1: Claim the shared runtime**

```bash
~/.claude/bin/wip hal0 status
~/.claude/bin/wip hal0 claim "voice stack bring-up (tts/stt slots, openwebui voice)" etc/hal0/slots/tts.toml etc/hal0/slots/stt.toml etc/hal0/openwebui.env
```
Expected: status shows `branch=main`, no conflicting claim; claim succeeds.

- [ ] **Step 2: Capture baseline slot / capability / model state**

```bash
mkdir -p /tmp/voice-bringup
{ echo "== slots =="; curl -s http://10.0.1.142:8080/api/slots;
  echo; echo "== capabilities =="; curl -s http://10.0.1.142:8080/api/capabilities;
  echo; echo "== models =="; curl -s http://10.0.1.142:8080/api/models; } \
  | tee /tmp/voice-bringup/baseline.txt | head -c 200
```
Expected: file written; `tts` slot `enabled=true state=offline`, `stt` slot `enabled=false`.

- [ ] **Step 3: Confirm what currently anchors the NPU (needed for P5)**

Use the `hal0-admin` MCP tool `npu_status`, and:
```bash
curl -s http://10.0.1.142:8080/api/slots | python3 -c "import sys,json; [print(s['name'], s.get('backend'), s.get('enabled'), s.get('state')) for s in json.load(sys.stdin)]"
```
Expected: note any slot with `backend=flm`/`device=npu` that is `enabled=true`. Record it in `baseline.txt`. (No mutation here.)

---

### Task 2: Round-trip verification harness

**Files:**
- Create: `tests/harness/integration/test_voice_roundtrip.py`

**Interfaces:**
- Produces: `normalized_match(a: str, b: str) -> float` (returns difflib ratio on normalized text) and a `test_voice_roundtrip_live` gate consumed by P2 and P5.

- [ ] **Step 1: Write the failing unit test for the matcher**

Create `tests/harness/integration/test_voice_roundtrip.py`:
```python
"""Voice round-trip integration gate + its pure matcher helper.

The live test (text -> /v1/audio/speech -> wav -> /v1/audio/transcriptions
-> text) is the bar for "tested" in the voice bring-up plan. It SKIPS unless
HAL0_VOICE_LIVE_URL points at a running hal0 API, so the default offline
`pytest tests/` pass stays green.
"""

from __future__ import annotations

import difflib
import os
import re

import pytest

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalized_match(a: str, b: str) -> float:
    """Return a 0..1 similarity ratio between two strings after normalizing
    case, punctuation, and whitespace."""
    def norm(s: str) -> str:
        s = _PUNCT.sub(" ", s.lower())
        return _WS.sub(" ", s).strip()

    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def test_normalized_match_ignores_case_and_punctuation() -> None:
    assert normalized_match("The quick brown fox!", "the quick brown fox") >= 0.99


def test_normalized_match_detects_divergence() -> None:
    assert normalized_match("the quick brown fox", "a totally different sentence") < 0.5
```

- [ ] **Step 2: Run the unit tests to verify they pass (matcher is pure)**

Run: `cd /home/halo/dev/wt/voice-stack && python -m pytest tests/harness/integration/test_voice_roundtrip.py -v`
Expected: 2 passed (the live test is added next and will skip).

- [ ] **Step 3: Add the guarded live round-trip test**

Append to the same file:
```python
TTS_MODEL = os.environ.get("HAL0_VOICE_TTS_MODEL", "kokoro-v1")
TTS_VOICE = os.environ.get("HAL0_VOICE_TTS_VOICE", "af_heart")
STT_MODEL = os.environ.get("HAL0_VOICE_STT_MODEL", "Whisper-Large-v3-Turbo")
_LIVE_URL = os.environ.get("HAL0_VOICE_LIVE_URL")


@pytest.mark.skipif(not _LIVE_URL, reason="set HAL0_VOICE_LIVE_URL to run the live round-trip")
def test_voice_roundtrip_live() -> None:
    import httpx

    phrase = "the quick brown fox jumps over the lazy dog"
    base = _LIVE_URL.rstrip("/")
    with httpx.Client(timeout=180.0) as client:
        speech = client.post(
            f"{base}/v1/audio/speech",
            json={"model": TTS_MODEL, "input": phrase, "voice": TTS_VOICE},
        )
        assert speech.status_code == 200, speech.text
        wav = speech.content
        assert wav[:4] == b"RIFF" and len(wav) > 1000, f"not a wav: {wav[:16]!r} len={len(wav)}"

        stt = client.post(
            f"{base}/v1/audio/transcriptions",
            files={"file": ("speech.wav", wav, "audio/wav")},
            data={"model": STT_MODEL},
        )
        assert stt.status_code == 200, stt.text
        text = stt.json()["text"]

    ratio = normalized_match(phrase, text)
    assert ratio >= 0.8, f"round-trip mismatch ratio={ratio:.2f}: {text!r}"
```

- [ ] **Step 4: Verify the live test SKIPS offline**

Run: `cd /home/halo/dev/wt/voice-stack && python -m pytest tests/harness/integration/test_voice_roundtrip.py -v`
Expected: 2 passed, 1 skipped.

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/voice-stack
git add tests/harness/integration/test_voice_roundtrip.py
git commit -m "test(voice): round-trip verification harness (matcher + guarded live gate)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: P1 — Bring up the TTS slot (Kokoro)

**Files:** none (ops). Evidence → `/tmp/voice-bringup/p1-tts.wav`.

**Interfaces:**
- Consumes: claim from Task 1.
- Produces: a live `tts` slot answering `/v1/audio/speech`.

- [ ] **Step 1: Ensure the TTS slot is enabled and start it**

Use `hal0-admin` MCP: `capability_set` to enable the `voice`/`tts` capability if needed, then `slot_restart` for slot `tts`. Then poll:
```bash
curl -s http://10.0.1.142:8080/api/slots | python3 -c "import sys,json; s=[x for x in json.load(sys.stdin) if x['name']=='tts'][0]; print(s['state'], s['container_status'], s['model_id'])"
```
Expected: eventually `online ... kokoro-v1` (allow for cold container start; re-poll up to ~60s).

- [ ] **Step 2: Prove `/v1/audio/speech` returns real audio**

```bash
curl -s -X POST http://10.0.1.142:8080/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro-v1","input":"hal0 voice check one two three","voice":"af_heart"}' \
  -o /tmp/voice-bringup/p1-tts.wav
file /tmp/voice-bringup/p1-tts.wav && head -c 4 /tmp/voice-bringup/p1-tts.wav | xxd
```
Expected: a WAV/RIFF file, non-trivial size. If the voice name is rejected, list valid Kokoro voices via `curl -s http://10.0.1.142:8080/v1/models` / the slot's `/v1/audio/voices` and retry; record the working voice in `baseline.txt`.

- [ ] **Step 3: Record evidence (no commit — runtime change)**

```bash
echo "P1 TTS online; wav bytes=$(stat -c%s /tmp/voice-bringup/p1-tts.wav)" >> /tmp/voice-bringup/baseline.txt
```
Expected: line appended. Gate met: TTS half proven.

---

### Task 4: P2 — Bring up STT on CPU + round-trip green

**Files:** none (ops). Exercises Task 2's live test.

**Interfaces:**
- Consumes: live `tts` slot (Task 3), the round-trip test (Task 2).
- Produces: a live CPU `stt` slot; the round-trip test passes.

- [ ] **Step 1: Confirm/pull the CPU Whisper model**

```bash
curl -s http://10.0.1.142:8080/api/models | python3 -c "import sys,json; d=json.load(sys.stdin); print([m for m in json.dumps(d) .split() if 'Whisper' in m][:3])" 2>/dev/null
```
Then, if `Whisper-Large-v3-Turbo` is not downloaded, use `hal0-admin` MCP `model_pull` with model id `Whisper-Large-v3-Turbo` (whispercpp backend). Verify with `model_store_probe` / `model_list`.
Expected: `Whisper-Large-v3-Turbo` shows `downloaded=true`.

- [ ] **Step 2: Ensure the whispercpp toolbox image is present**

```bash
ssh hal0 'podman images | grep -iE "whisper|whispercpp"'
```
If absent, pull the image ref hal0 expects for the whispercpp provider (check `src/hal0/providers/` + `env-vars` for `HAL0_TOOLBOX_IMAGE_*`); record the ref. Expected: image present.

- [ ] **Step 3: Configure the `stt` slot for CPU and enable it**

Use `hal0-admin` MCP: `slot_create`/config-write the `stt` slot with `type=transcription`, `device=cpu`, provider whispercpp, `model_default=Whisper-Large-v3-Turbo`, `enabled=true`; then `slot_restart` `stt`. Poll:
```bash
curl -s http://10.0.1.142:8080/api/slots | python3 -c "import sys,json; s=[x for x in json.load(sys.stdin) if x['name']=='stt'][0]; print(s['state'], s['backend'], s['model_id'], s['enabled'])"
```
Expected: `online ... Whisper-Large-v3-Turbo true` (allow cold-start; CPU whisper warm-up can be slow).

- [ ] **Step 4: Run the round-trip test against the live box**

Run:
```bash
cd /home/halo/dev/wt/voice-stack
HAL0_VOICE_LIVE_URL=http://10.0.1.142:8080 \
HAL0_VOICE_STT_MODEL=Whisper-Large-v3-Turbo \
python -m pytest tests/harness/integration/test_voice_roundtrip.py::test_voice_roundtrip_live -v
```
Expected: PASS (ratio ≥ 0.8). If it fails on format, confirm Kokoro emits WAV (not mp3); if STT returns 415, check the toolbox accepts `audio/wav`. Record ratio in `baseline.txt`.

- [ ] **Step 5: Record evidence**

```bash
echo "P2 CPU STT online; round-trip PASS" >> /tmp/voice-bringup/baseline.txt
```

---

### Task 5: P3 — Verify the agent voice tools (Hermes)

**Files:** none (ops). Evidence → `/tmp/voice-bringup/p3-agent.txt`.

**Interfaces:**
- Consumes: live `tts` + `stt` slots.
- Produces: confirmed Hermes `text_to_speech` / `transcribe_audio` OmniRouter tools.

- [ ] **Step 1: Re-run the Hermes voice_wire provisioning**

The `voice_wire` phase lives in `src/hal0/agents/hermes_provision.py`. Trigger Hermes provisioning so it discovers the now-live slots and writes `STT_OPENAI_BASE_URL` / `TTS_OPENAI_BASE_URL` into Hermes config. Inspect the rendered config:
```bash
ssh hal0 'grep -iE "stt|tts|audio|speech" $(find /opt/hal0 /etc/hal0 -name "config.y*ml" 2>/dev/null | head) 2>/dev/null' | head
```
Expected: Hermes config now carries the STT/TTS base URLs.

- [ ] **Step 2: Exercise the OmniRouter `text_to_speech` tool**

Invoke the `text_to_speech` OmniRouter tool (via the agent/omni endpoint) with input text; capture the returned audio reference. Then invoke `transcribe_audio` on the P1 wav.
```bash
curl -s -X POST http://10.0.1.142:8080/v1/audio/transcriptions \
  -F "file=@/tmp/voice-bringup/p1-tts.wav;type=audio/wav" -F "model=Whisper-Large-v3-Turbo" \
  | tee /tmp/voice-bringup/p3-agent.txt
```
Expected: transcript text returned that fuzzy-matches the P1 phrase (agent path proven end to end). Record result.

---

### Task 6: P4 — Open WebUI Call mode (code + deploy)

**Files:**
- Modify: `src/hal0/openwebui/env_writer.py`
- Modify: `tests/openwebui/test_env_writer.py`

**Interfaces:**
- Consumes: live `tts` + `stt` slots.
- Produces: `env_writer` emits Open WebUI voice (Call-mode) env keys; browser Call mode works.

- [ ] **Step 1: Write the failing test for the new voice keys**

Add to `tests/openwebui/test_env_writer.py`:
```python
def test_write_openwebui_env_includes_voice_callmode_keys(tmp_path: Path) -> None:
    """Open WebUI Call mode is prewired to hal0's /v1 audio endpoints."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target)
    env = _parse_env(target.read_text())

    assert env["AUDIO_STT_ENGINE"] == "openai"
    assert env["AUDIO_TTS_ENGINE"] == "openai"
    assert env["AUDIO_STT_OPENAI_API_BASE_URL"] == "http://host.docker.internal:8080/v1"
    assert env["AUDIO_TTS_OPENAI_API_BASE_URL"] == "http://host.docker.internal:8080/v1"
    assert env["AUDIO_STT_MODEL"] == "Whisper-Large-v3-Turbo"
    assert env["AUDIO_TTS_MODEL"] == "kokoro-v1"
    assert env["AUDIO_TTS_VOICE"]  # a non-empty default voice
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/halo/dev/wt/voice-stack && python -m pytest tests/openwebui/test_env_writer.py::test_write_openwebui_env_includes_voice_callmode_keys -v`
Expected: FAIL with `KeyError: 'AUDIO_STT_ENGINE'`.

- [ ] **Step 3: Add the voice keys to `_DEFAULT_OPENWEBUI_ENV`**

In `src/hal0/openwebui/env_writer.py`, extend the `_DEFAULT_OPENWEBUI_ENV` dict (keep keys sorted, matching the existing style) with:
```python
    # Voice / Call mode — point Open WebUI's STT+TTS at hal0's own /v1 audio
    # endpoints (Call mode in the browser does mic capture + playback; hal0
    # provides the engines). API key is a placeholder — hal0 ignores auth.
    "AUDIO_STT_ENGINE": "openai",
    "AUDIO_STT_OPENAI_API_BASE_URL": "http://host.docker.internal:8080/v1",
    "AUDIO_STT_OPENAI_API_KEY": "sk-hal0-local",
    "AUDIO_STT_MODEL": "Whisper-Large-v3-Turbo",
    "AUDIO_TTS_ENGINE": "openai",
    "AUDIO_TTS_OPENAI_API_BASE_URL": "http://host.docker.internal:8080/v1",
    "AUDIO_TTS_OPENAI_API_KEY": "sk-hal0-local",
    "AUDIO_TTS_MODEL": "kokoro-v1",
    "AUDIO_TTS_VOICE": "af_heart",
```
Also update the module docstring's "Prewired variables" list to mention the voice block.

- [ ] **Step 4: Run the full env_writer test module**

Run: `cd /home/halo/dev/wt/voice-stack && python -m pytest tests/openwebui/ -v`
Expected: all pass (existing key-set test still passes — it asserts specific keys, not exclusivity; verify it does not assert an exact-length key set, and if it does, update it to include the new keys).

- [ ] **Step 5: Commit the code change**

```bash
cd /home/halo/dev/wt/voice-stack
git add src/hal0/openwebui/env_writer.py tests/openwebui/test_env_writer.py
git commit -m "feat(openwebui): prewire Call mode to hal0 /v1 audio (STT/TTS)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Deploy the env to CT105 and restart Open WebUI**

Regenerate `/etc/hal0/openwebui.env` on the box from the new writer (run `python -m hal0.openwebui.env_writer` on the deployed code, or `config_write` the voice keys), then:
```bash
ssh hal0 'grep -E "AUDIO_(STT|TTS)" /etc/hal0/openwebui.env'
ssh hal0 'systemctl restart hal0-openwebui && sleep 3 && systemctl is-active hal0-openwebui'
```
Expected: voice keys present; service `active`. (Deploying the code change to the runtime follows the normal hal0 deploy path; coordinate per Global Constraints.)

- [ ] **Step 7: Human verification of Call mode**

Open `http://10.0.1.142:3001`, start a chat, click the Call/headphone control, speak a sentence. Confirm: live transcript appears (STT), the agent replies, and the reply is spoken back (TTS).
Expected: a full spoken round-trip in the browser. Record pass/fail in `/tmp/voice-bringup/baseline.txt`.

---

### Task 7: P5 — STT on the NPU (FLM)

**Files:** none (ops). Re-exercises Task 2's live test on the NPU path.

**Interfaces:**
- Consumes: round-trip test; baseline NPU-anchor note (Task 1 Step 3).
- Produces: `stt` answering via FLM on the XDNA NPU.

- [ ] **Step 1: Resolve the single-NPU-context constraint**

Using the Task 1 NPU note + `npu_status` MCP: decide the path —
  - **(a) FLM trio:** if an FLM chat anchor is (or will be) the enabled NPU slot, ride it — chat+STT+embed on one `flm serve`; the `stt-npu` shadow routes via `NpuTrioRouter`. OR
  - **(b) dedicated STT-only NPU slot:** only if no chat anchor needs the NPU.
Only one NPU slot may be `enabled=true`. Do NOT evict an in-use anchor without coordinating (`wip hal0 status`). Record the chosen path.

- [ ] **Step 2: Ensure the FLM toolbox image is present**

```bash
ssh hal0 'podman images | grep -iE "flm|fastflowlm"'
```
If absent, `model_pull`/pull the FLM toolbox image ref hal0 expects (check `providers-profiles-devices` / `HAL0_TOOLBOX_IMAGE_*`). Expected: image present.

- [ ] **Step 3: Flip STT to the NPU model and restart**

Use `hal0-admin` MCP to set the `stt` slot (or the trio shadow) to `device=npu`, `model=whisper-v3:turbo`, per the chosen path; `slot_restart`. Poll until `online`. Expected: `stt` reports `backend=flm`, model `whisper-v3:turbo`, `online`.

- [ ] **Step 4: Run the round-trip on the NPU path**

Run:
```bash
cd /home/halo/dev/wt/voice-stack
HAL0_VOICE_LIVE_URL=http://10.0.1.142:8080 \
HAL0_VOICE_STT_MODEL=whisper-v3:turbo \
python -m pytest tests/harness/integration/test_voice_roundtrip.py::test_voice_roundtrip_live -v
```
Expected: PASS (allow up to 120s cold NPU warm-up per `npu_trio.py`). Record ratio + the NPU path used in `baseline.txt`.

---

### Task 8: Doc reconciliation (hal0-web, branch off master)

**Files (hal0-web repo `/home/halo/dev/hal0-web`):**
- Modify: `src/pages/index.astro` (Moonshine claim; "voice mode end-to-end" wording)
- Modify: voice docs surfaced in Task analysis (e.g. `src/content/docs/docs/voice-stt-tts.mdx`, `providers-profiles-devices.mdx`, `hardware-matrix.mdx`) where "Moonshine" is listed as the shipped STT provider.

**Interfaces:**
- Consumes: verified runtime reality from P1–P7.
- Produces: site claims that match reality.

- [ ] **Step 1: Create an isolated branch off master**

```bash
cd /home/halo/dev/hal0-web && git fetch origin --prune
git worktree add -b docs/voice-claims-fix /home/halo/dev/wt/voice-claims origin/master
```
Expected: worktree created (avoids the live `docs/model-roster-benchmark` session).

- [ ] **Step 2: Fix the stale Moonshine STT claim**

In the hal0-web voice claims (per the audit: `index.astro` provider list, `providers-profiles-devices.mdx`, `hardware-matrix.mdx`, `voice-stt-tts.mdx`), replace "Moonshine" as the *shipped* STT provider with the actual shipped path: **whisper.cpp (CPU) + FLM/Whisper on NPU**. Keep any historical changelog references intact.
Expected: no remaining text presents Moonshine as the current STT engine.

- [ ] **Step 3: Reword "voice mode end-to-end"**

In `index.astro`, change the "Voice mode end-to-end" copy so it describes the *actual* mechanism: hands-free voice is delivered through **Open WebUI's built-in Call mode** wired to hal0's `/v1/audio/*` endpoints — not a native hal0 streaming orchestrator. Only assert it as shipped now that P4 is verified.
Expected: copy is accurate and verifiable.

- [ ] **Step 4: Build, commit, PR**

```bash
cd /home/halo/dev/wt/voice-claims && npm run build 2>&1 | tail -5
git add -A && git commit -m "docs: correct voice claims — whisper not moonshine, call-mode is the hands-free path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
gh pr create --base master --title "docs: correct voice stack claims to match reality" --body "Fixes stale Moonshine STT claim (retired v0.2 -> whisper.cpp) and rewords 'voice mode end-to-end' to reflect that hands-free runs via Open WebUI Call mode. Verified against live CT105 bring-up.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```
Expected: build green; PR opened.

---

### Task 9: Finalize — PR, memory, release claims

**Files:** none beyond pushing `feat/voice-stack`.

- [ ] **Step 1: Run the offline test suite for the changed code**

Run: `cd /home/halo/dev/wt/voice-stack && python -m pytest tests/openwebui/ tests/harness/integration/test_voice_roundtrip.py -v`
Expected: all pass (live test skips offline).

- [ ] **Step 2: Push and open the hal0 PR**

```bash
cd /home/halo/dev/wt/voice-stack && git push -u origin feat/voice-stack
gh pr create --base main --title "feat(voice): bring up + verify TTS/STT stack; Open WebUI Call mode" --body "Lights the previously-dormant voice stack on CT105 and proves it: Kokoro TTS, Whisper STT (CPU then NPU), Hermes voice tools, and hands-free via Open WebUI Call mode. Adds a reusable round-trip test and prewires Open WebUI Call mode. See docs/superpowers/specs/2026-06-19-voice-stack-bringup-design.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```
Expected: PR opened against `main`.

- [ ] **Step 3: Record memory-worthy outcomes to hal0 Hindsight**

Via the `hal0-memory` skill / `memory_add`, record (atomic facts, with the why): (1) the over-claim finding (voice shipped-but-never-lit; no native orchestrator; Moonshine retired); (2) the NPU contention resolution chosen in P5; (3) any bring-up gotchas (cold-start latency, image refs, voice name). Tag `repo:hal0 topic:voice kind:incident|decision|gotcha`, dataset `shared`.

- [ ] **Step 4: Release the runtime claim**

```bash
~/.claude/bin/wip hal0 release
```
Expected: claim released. Bring-up complete.

---

## Self-Review

**Spec coverage:** P1 (TTS) → Task 3; P2 (CPU STT) → Task 4; P3 (agent tools) → Task 5; P4 (Open WebUI Call mode) → Task 6; P5 (NPU) → Task 7. Round-trip test definition → Task 2. Doc reconciliation (Moonshine + voice-mode wording) → Task 8. Coordination/safety constraints → Global Constraints + Task 1 + Task 9. Memory capture → Task 9. All spec sections mapped.

**Placeholder scan:** Image refs (whispercpp/FLM) and Open WebUI's exact voice-var spelling for the shipped version are resolved *in-task* with a concrete discovery command followed by a concrete action — not deferred as "TBD". Kokoro voice name has a concrete default (`af_heart`) plus a fallback-discovery step.

**Type consistency:** `normalized_match(a, b) -> float` is defined once (Task 2) and reused by name in Tasks 2/4/7 via the same test file. Env keys (`AUDIO_STT_*`/`AUDIO_TTS_*`) are identical between the implementation (Task 6 Step 3) and its test (Task 6 Step 1). Model ids (`kokoro-v1`, `Whisper-Large-v3-Turbo`, `whisper-v3:turbo`) are used verbatim and consistently across tasks and the env defaults.
