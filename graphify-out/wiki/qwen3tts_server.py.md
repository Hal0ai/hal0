# qwen3tts_server.py

> 16 nodes

## Key Concepts

- **qwen3tts_server.py** (10 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **_load_model()** (4 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **_encode_audio()** (4 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **speech()** (4 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **_resolve_model_dir()** (3 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **SpeechRequest** (3 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **main()** (2 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **ndarray** (1 connections)
- **health()** (1 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **models()** (1 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **voices()** (1 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **Response** (1 connections)
- **qwen3tts-server — FastAPI wrapper around the qwen-tts package.  Implements the s** (1 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **Return a usable local model dir, or None to fall back to the HF id.      Accepts** (1 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **Load Qwen3-TTS CustomVoice onto the GPU and stash on _state.      ROCm note: tor** (1 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`
- **Encode float32 mono samples to the requested format, applying ``speed``.      Sp** (1 connections) — `packaging/toolbox/qwen3tts/qwen3tts_server.py`

## Relationships

- [BaseModel](BaseModel.md) (1 shared connections)

## Source Files

- `packaging/toolbox/qwen3tts/qwen3tts_server.py`

## Audit Trail

- EXTRACTED: 39 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*