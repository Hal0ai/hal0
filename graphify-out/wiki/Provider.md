# Provider

> 28 nodes

## Key Concepts

- **Provider** (23 connections) — `src/hal0/providers/base.py`
- **comfyui.py** (6 connections) — `src/hal0/providers/comfyui.py`
- **kokoro.py** (6 connections) — `src/hal0/providers/kokoro.py`
- **Any** (5 connections)
- **ComfyUIHealthError** (5 connections) — `src/hal0/providers/comfyui.py`
- **KokoroInferError** (5 connections) — `src/hal0/providers/kokoro.py`
- **.container_spec()** (4 connections) — `src/hal0/providers/base.py`
- **KokoroHealthError** (4 connections) — `src/hal0/providers/kokoro.py`
- **.build_env()** (3 connections) — `src/hal0/providers/base.py`
- **.health()** (3 connections) — `src/hal0/providers/base.py`
- **.infer()** (3 connections) — `src/hal0/providers/base.py`
- **.image_ref()** (3 connections) — `src/hal0/providers/base.py`
- **.start_cmd()** (2 connections) — `src/hal0/providers/base.py`
- **ContainerSpec** (1 connections)
- **Abstract base for a hal0 inference backend.      Concrete implementations: Conta** (1 connections) — `src/hal0/providers/base.py`
- **Compute the EnvironmentFile contents for a slot.          Returns a mapping of H** (1 connections) — `src/hal0/providers/base.py`
- **Return the argv list for spawning this backend outside systemd.          Mirrors** (1 connections) — `src/hal0/providers/base.py`
- **Run a health check against the backend on *port*.          Returns {"ok": bool,** (1 connections) — `src/hal0/providers/base.py`
- **Passthrough inference against the provider's OpenAI-compatible API.          Thi** (1 connections) — `src/hal0/providers/base.py`
- **Build the ContainerSpec for this slot + model combination.          Called by Sl** (1 connections) — `src/hal0/providers/base.py`
- **Return the toolbox image reference for this Provider + slot config.          Exa** (1 connections) — `src/hal0/providers/base.py`
- **ComfyUIProvider — Stable-Diffusion-family image generation backend.  ComfyUI is** (1 connections) — `src/hal0/providers/comfyui.py`
- **ComfyUI health probe failed.** (1 connections) — `src/hal0/providers/comfyui.py`
- **# NOTE: ComfyUI's /system_stats endpoint is the closest thing to a** (1 connections) — `src/hal0/providers/comfyui.py`
- **KokoroProvider — CPU TTS inference backend (kokoro-onnx).  Image API surface:** (1 connections) — `src/hal0/providers/kokoro.py`
- *... and 3 more nodes in this community*

## Relationships

- [ComfyUIProvider](ComfyUIProvider.md) (4 shared connections)
- [KokoroProvider](KokoroProvider.md) (3 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (3 shared connections)
- [Hal0Error](Hal0Error.md) (3 shared connections)
- [Mount](Mount.md) (2 shared connections)
- [flm.py](flm.py.md) (2 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [MemoryProvider](MemoryProvider.md) (1 shared connections)
- [LlamaServerProvider](LlamaServerProvider.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [FLMProvider](FLMProvider.md) (1 shared connections)

## Source Files

- `src/hal0/providers/base.py`
- `src/hal0/providers/comfyui.py`
- `src/hal0/providers/kokoro.py`

## Audit Trail

- EXTRACTED: 70 (80%)
- INFERRED: 17 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*