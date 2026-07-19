# Provider

> 28 nodes · cohesion 0.09

## Key Concepts

- **Provider** (24 connections) — `src/hal0/providers/base.py`
- **base.py** (9 connections) — `src/hal0/providers/base.py`
- **kokoro.py** (6 connections) — `src/hal0/providers/kokoro.py`
- **Any** (5 connections)
- **KokoroInferError** (5 connections) — `src/hal0/providers/kokoro.py`
- **.container_spec()** (4 connections) — `src/hal0/providers/base.py`
- **KokoroHealthError** (4 connections) — `src/hal0/providers/kokoro.py`
- **.build_env()** (3 connections) — `src/hal0/providers/base.py`
- **.health()** (3 connections) — `src/hal0/providers/base.py`
- **.image_ref()** (3 connections) — `src/hal0/providers/base.py`
- **.infer()** (3 connections) — `src/hal0/providers/base.py`
- **.start_cmd()** (2 connections) — `src/hal0/providers/base.py`
- **ABC** (2 connections)
- **Hal0Error** (2 connections)
- **ContainerSpec** (1 connections)
- **Provider abstract base class.  A Provider encapsulates the logic for a single in** (1 connections) — `src/hal0/providers/base.py`
- **Abstract base for a hal0 inference backend.      Concrete implementations: Conta** (1 connections) — `src/hal0/providers/base.py`
- **Compute the EnvironmentFile contents for a slot.          Returns a mapping of H** (1 connections) — `src/hal0/providers/base.py`
- **Return the argv list for spawning this backend outside systemd.          Mirrors** (1 connections) — `src/hal0/providers/base.py`
- **Run a health check against the backend on *port*.          Returns {"ok": bool,** (1 connections) — `src/hal0/providers/base.py`
- **Passthrough inference against the provider's OpenAI-compatible API.          Thi** (1 connections) — `src/hal0/providers/base.py`
- **Build the ContainerSpec for this slot + model combination.          Called by Sl** (1 connections) — `src/hal0/providers/base.py`
- **Return the toolbox image reference for this Provider + slot config.          Exa** (1 connections) — `src/hal0/providers/base.py`
- **# NOTE: unit rendering is owned by the single Quadlet adapter** (1 connections) — `src/hal0/providers/base.py`
- **KokoroProvider — CPU TTS inference backend (kokoro-onnx).  Image API surface:** (1 connections) — `src/hal0/providers/kokoro.py`
- *... and 3 more nodes in this community*

## Relationships

- [Mount](Mount.md) (3 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (3 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (3 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (3 shared connections)
- [profile.py](profile.py.md) (2 shared connections)
- [LlamaServerProvider](LlamaServerProvider.md) (2 shared connections)
- [FLMInferError](FLMInferError.md) (2 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [FLMProvider](FLMProvider.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)

## Source Files

- `src/hal0/providers/base.py`
- `src/hal0/providers/kokoro.py`

## Audit Trail

- EXTRACTED: 72 (81%)
- INFERRED: 17 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*