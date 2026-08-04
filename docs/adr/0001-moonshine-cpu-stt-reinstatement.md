# ADR-0001: Reinstate Moonshine as the CPU STT engine

## Status

Accepted.

## Context

`v0.2.0` (the Lemonade migration cut) retired Moonshine STT "in favour of
`whisper.cpp`" (CHANGELOG `[v0.2.0]`, Breaking: "Moonshine STT retired in
favour of `whisper.cpp` via Lemonade"). That justification never held past
the Lemonade rollback: `whisper.cpp` was never shipped as a standalone CPU
toolbox service. The only Whisper hal0 actually runs is `whisper-v3:turbo`
inside the FastFlowLM (FLM) NPU trio — chat + transcription + embedding
coresident in one `flm serve` process on AMD XDNA hardware. `whisper.cpp` as
a general-purpose CPU service is on the PLAN.md strip list (§"Strip (gone
for good unless re-justified)", `PLAN.md:333-339`) alongside Vibevoice and
Infinity, and was never re-justified.

The practical result: any host without the XDNA NPU — CPU-only x86_64, or
Strix Halo with FastFlowLM not installed — had **zero** speech-to-text.
`voice.stt` could only ever be enabled via `backend=npu`, and the docs said
so explicitly ("there is no CPU-only STT backend in hal0").

Meanwhile TTS already solved the equivalent problem: `voice.tts` is a
single canonical `tts` slot that runs one of two engines depending on the
selected device — Kokoro-82M ONNX on CPU, or Qwen3-TTS on GPU (ROCm). No
second slot, no `provider` selection — the device alone picks the engine.

## Decision

1. **Reinstate Moonshine as hal0's CPU STT engine**, packaged as its own
   toolbox image (`ghcr.io/hal0ai/hal0-toolbox-moonshine:v1`, digest pinned
   in `manifest.json`,
   `sha256:bf07e3d5640fc1d009298be0600b62ec24185f80d7c8bed47321742fce17aa31`,
   verified anonymously pullable 2026-08-03) and a new `MoonshineProvider`.

2. **`voice.stt` becomes device-keyed, exactly like `voice.tts`**: `cpu` →
   Moonshine, `npu` → the FLM trio's Whisper. This is deliberately sound at
   exactly two engines on two devices — the device alone disambiguates
   which engine a slot runs, with no separate `provider` field needed on
   the capability selection. GPU devices resolve to **no STT engine**: hal0
   ships no GPU STT engine, and the previous behavior — falling through to
   the generic GPU branch and silently handing the `stt` slot a llama chat
   profile — was a live bug (wrong runtime family; the slot could never
   actually start the STT image). That fall-through is fixed: `(stt,
   gpu-*)` now resolves to `None` and surfaces a typed error instead of a
   wrong profile.

   **Future-work trigger:** this device-keyed rule holds only because
   exactly one engine exists per device. If a **second** CPU STT engine
   ever lands, `device` stops being enough to disambiguate, and `provider`
   must become a first-class selector in capability selection (mirroring
   how `voice.tts` would need the same treatment if a second CPU or GPU TTS
   engine appeared). Don't extend the device-keyed rule past two engines
   without making that change first.

3. **Weights are operator-staged, not registry-pulled.** Moonshine ships a
   multi-file ONNX bundle (encoder/decoder `.ort`/`.onnx` + tokenizer JSON)
   that the curated catalogue's single-file GGUF/safetensors/checkpoint
   schema can't express — the same documented open item covering Kokoro and
   Vibevoice (see the three resolution paths noted in
   `docs/reference/model-roster-benchmark.mdx`). Rather than block on a
   schema redesign, Moonshine's resolution ships now: the operator stages
   the bundle (default `/mnt/ai-models/local/moonshine`; hal0 walks the tree
   for the directory holding `encoder_model.{ort,onnx}`, so both the root
   and a `quantized/<variant>/` leaf work), and both slot spawn and
   `hal0 doctor` preflight the path — a missing or unusable bundle fails
   loudly, by name (`slot.weights_missing`), instead of starting a container
   that 500s on the first real request.

   Live validation on lxc105 (2026-08-03) sharpened three points of this
   decision, each now covered by a regression test:
   - **Only non-streaming variants are usable.** The streaming bundles ship
     the Moonshine streaming SDK's file set (`encoder.ort`, `frontend.ort`),
     which this image cannot load. The preflight names that case rather than
     accepting any `*.ort` file.
   - **Staged weights may sit outside the model store root.** A box whose
     `[models].roots` is `/var/lib/hal0/models` can legitimately stage under
     `/mnt/ai-models`; the slot mounts the resolved path (and its realpath)
     identical-path read-only so the container can read it.
   - **A registry path only wins when it is a Moonshine bundle.** Because
     the provider is self-managed, the manager's model lookup for a
     transcription slot may return an unrelated ASR artifact (it returned a
     VibeVoice `.gguf`); such a path is ignored in favour of the staged
     bundle.

4. **This supersedes the `[v0.2.0]` retirement claim.** "Moonshine STT
   retired in favour of `whisper.cpp`" is no longer accurate on either
   half: `whisper.cpp` never shipped as the CPU service that justified the
   retirement, and Moonshine is back as the CPU engine. The CHANGELOG entry
   is left as a historical record; this ADR is the record of record for why
   the decision reversed.

hal0 binds 0.0.0.0:8080 with no built-in auth by design (LAN-trust
posture); voice endpoints accept file uploads on that unauthenticated bind.
The Moonshine server also exposes a `WS /v1/audio/stream` endpoint for live
PCM16 streaming — this is **in-container only**, not routed by the hal0
dispatcher, and is not part of the public API surface.

## Consequences

- CPU-only and NPU-less hosts get real STT again, with no NPU/FastFlowLM
  dependency.
- `voice.stt` and `voice.tts` are now symmetric device-keyed switches,
  which keeps the capability-selection mental model (and its
  `profile_name_for_fit` implementation) consistent across both children
  of `voice`.
- Operators on CPU-only STT take on a manual staging step (and a doctor
  preflight to catch it) rather than a registry pull — an explicit,
  documented trade-off, not an oversight, until the curated-catalogue
  multi-file-bundle schema work lands.
- The device-keyed `stt` rule is a two-engine special case, not a general
  pattern — see the future-work trigger above. Anyone adding a third STT
  engine (a second CPU engine, or any GPU engine) must revisit
  `profile_name_for_fit` and the capability-selection schema together, not
  patch around the device switch.
- What would retire Moonshine again: a GPU or NPU STT engine that
  outperforms it on every host Moonshine currently serves, or a curated
  multi-file-bundle resolution path landing that makes the operator-staging
  step unnecessary — neither has happened yet.
