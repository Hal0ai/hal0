# hal0 Voice Stack — Bring-up & Verification Design

**Date:** 2026-06-19
**Branch:** `feat/voice-stack` (worktree `/home/halo/dev/wt/voice-stack`, base `origin/main`)
**Status:** Design — awaiting user review before plan

## Problem

The hal0-web site and docs claim a full voice stack ships — STT (Moonshine/Whisper/FLM-NPU),
TTS (Kokoro), OpenAI-compatible `/v1/audio/*`, and "voice mode end-to-end, a hands-free
streaming conversation." Investigation shows the claim outruns reality:

- **Code primitives exist and are real:** `POST /v1/audio/transcriptions` and `POST /v1/audio/speech`
  in `src/hal0/api/routes/v1.py` (~L883–924), the `KokoroProvider`
  (`src/hal0/providers/kokoro.py`), the `NpuTrioRouter` (`src/hal0/dispatcher/npu_trio.py`),
  and the OmniRouter `text_to_speech` / `transcribe_audio` tools.
- **Nothing has ever been brought up or tested.** On CT105 (10.0.1.142):
  - `tts` slot — `enabled=true` but **offline** (container stopped); `kokoro-v1` image present, never proven running.
  - `stt` slot — **disabled**, FLM/NPU image **missing**; `whisper-v3:turbo` weights present, CPU `Whisper-Large-v3-Turbo` **not** downloaded.
- **No end-to-end voice orchestrator exists.** "Voice mode end-to-end" is an over-claim — no
  mic capture, no playback loop, no push-to-talk in codebase or UI. The only streaming WS
  (`/v1/audio/stream`) lives inside the **retired** Moonshine toolbox container and is **not**
  proxied through hal0-api.
- **Open WebUI ships but voice is not configured** — no `AUDIO_STT_*` / `AUDIO_TTS_*` env in
  `src/hal0/openwebui/env_writer.py`.
- **Stale claim:** the site lists "Moonshine" as the shipped STT provider; the changelog records
  Moonshine was **retired in v0.2** in favor of whisper.cpp via Lemonade.

## Goal

Bring the voice stack from "wired, never lit" to **running and verifiably tested on hermes (CT105)**,
covering the agent voice path *and* genuine hands-free voice — so the site's claims become true,
or are corrected where they cannot be.

## Non-goals

- Building a custom WebSocket voice orchestrator or exposing `/v1/audio/stream` through hal0-api
  (rejected — large build on a retired component; YAGNI). Hands-free is delivered via Open WebUI's
  built-in **Call mode**, not a native hal0 loop.
- Any new chat/agent LLM work — the agent loop (ace-saber 35B) already runs.
- Restructuring the dispatcher or slot lifecycle beyond what bring-up requires.

## Decisions (locked with owner)

| Decision | Choice |
|---|---|
| Success scope | Scope 2 (agent voice tools) **+** Scope 3 (hands-free voice mode) |
| STT backend | **CPU-first, then NPU** — prove CPU whispercpp green, then add NPU/FLM |
| TTS backend | Kokoro on CPU (`kokoro-v1`, already on disk) |
| Hands-free client | **Open WebUI built-in Call mode** pointed at hal0's `/v1` endpoints |
| NPU phase (P5) | **In scope this round** |
| Spec location | hal0 repo, this worktree |

## Architecture — five phases, each independently testable

Each phase has a hard gate; we do not advance until its gate is green.

### P1 · TTS up
- Enable + start the `tts` slot (`kokoro-v1`, image present) via `hal0-admin` MCP
  (`slot_restart` / `capability_set`), not raw SSH where avoidable.
- **Gate:** `POST /v1/audio/speech {model, input, voice}` returns real `.wav` audio bytes
  (non-empty, valid RIFF header), cold-start latency recorded.

### P2 · STT (CPU) up
- `model_pull` `Whisper-Large-v3-Turbo` (~1.62 GB); ensure whispercpp toolbox image present (pull/build).
- Create/enable `stt` slot on **device=cpu**, provider whispercpp; start.
- **Gate:** the **round-trip test** is green (see Data flow).

### P3 · Agent voice tools
- Re-run Hermes `voice_wire` provisioning (`src/hal0/agents/hermes_provision.py`) so Hermes config
  carries `STT_OPENAI_BASE_URL` / `TTS_OPENAI_BASE_URL`.
- **Gate:** Hermes' OmniRouter `text_to_speech` and `transcribe_audio` tools route to the live slots
  and return correct results (asserted, not just 200s).

### P4 · Hands-free via Open WebUI Call mode
- Extend `src/hal0/openwebui/env_writer.py` to emit voice config:
  `AUDIO_STT_ENGINE=openai`, `AUDIO_TTS_ENGINE=openai`, the audio base URLs → `http://host.docker.internal:8080/v1`,
  STT model `Whisper-Large-v3-Turbo` (later `whisper-v3:turbo`), TTS model `kokoro-v1`, a default voice.
  Ship as a **hal0-repo PR** from this worktree; restart `hal0-openwebui`.
- **Gate:** a real spoken conversation round-trips in the browser at `:3001` Call mode
  (human-verified: speak → transcript → agent reply → spoken playback).

### P5 · NPU/FLM STT
- **Pre-flight:** check the single-XDNA-context anchor — only one NPU slot may be `enabled` at once;
  identify what currently holds NPU and coordinate (do not evict blindly).
- Pull the FLM toolbox image; flip `stt` to `device=npu` / `whisper-v3:turbo` (or ride the FLM
  trio: chat+STT+embed on one `flm serve`).
- **Gate:** round-trip test green on the NPU path; matches the marketed "NPU trio."

## Data flow — the core round-trip test (definition of "tested")

Self-contained, headless, no mic/audio hardware:

```
"the quick brown fox jumps over the lazy dog"
   → POST /v1/audio/speech (kokoro-v1)        → wav bytes
   → POST /v1/audio/transcriptions (stt slot) → transcript
   → assert fuzzy-match(transcript, original) ≥ threshold
```

Proves both halves at once and is reusable across P2 (CPU) and P5 (NPU). P3 adds an agent-tool
assertion; P4 adds one human browser Call-mode conversation.

## Error handling / failure modes designed for

- **Cold-start latency** — slots are enabled-but-offline and auto-load on first request; Kokoro/whisper
  warm-up can be slow. Test and record; set sane client timeouts (NPU STT cold path ~120s per `npu_trio.py`).
- **Audio-format rejection** — `audio.unsupported_format` → 415; assert clean error, no decoder leakage
  (`_scrub_audio_decoder_leakage`).
- **Missing `model` field** — `request.missing_model` → 400.
- **Missing toolbox image** — whispercpp (P2) / FLM (P5) image absent → pull or build before enabling.
- **Dead-end guard** — confirm nothing depends on the unexposed `/v1/audio/stream` WS.
- **NPU contention** — never enable a second NPU slot without resolving the existing anchor.

## Doc reconciliation (separate hal0-web branch off `master`)

A live session is on `docs/model-roster-benchmark`, so doc fixes branch off `master` independently.

- Fix the **stale "Moonshine" STT claim** (retired v0.2 → whisper.cpp) regardless of outcome.
- Reword **"voice mode end-to-end"** to be accurate: hands-free is delivered **via Open WebUI Call mode**,
  not a native hal0 orchestrator. Only assert it once P4 is verified.

## Coordination / safety (CLAUDE.md)

- CT105 `/opt/hal0` is a **shared host**: `wip hal0 status` + `wip hal0 claim "voice bring-up" <files>`
  before any slot/image/service change; never `checkout`/`reset`/deploy over another session.
- Prefer `hal0-admin` MCP tools (`model_pull`, `slot_create`/`slot_restart`, `capability_set`,
  `slot_status`, `model_store_probe`) over raw SSH; read-only SSH for container/systemd inspection.
- P4 code change ships as a hal0-repo PR from this worktree.
- Record memory-worthy outcomes (bring-up gotchas, NPU contention resolution, the over-claim finding)
  to hal0 Hindsight memory.

## Open questions for plan stage

- Exact whispercpp toolbox image ref / whether a build is needed on CT105.
- Open WebUI's precise voice env var names for the shipped version (verify against its docs at plan time).
- Whether P5 rides the FLM trio (shared chat+STT) or a dedicated STT-only NPU slot, given the current anchor.
