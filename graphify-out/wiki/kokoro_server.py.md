# kokoro_server.py

> 18 nodes

## Key Concepts

- **kokoro_server.py** (11 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **_load_model()** (5 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **_encode_audio()** (4 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **speech()** (4 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **_download()** (3 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **_resolve_paths()** (3 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **SpeechRequest** (3 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **main()** (2 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **ndarray** (1 connections)
- **health()** (1 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **models()** (1 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **voices()** (1 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **Response** (1 connections)
- **kokoro-server — FastAPI wrapper around kokoro-onnx.  Implements the contract the** (1 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **Stream-download a URL to a path; small helper so we don't pull     in requests/h** (1 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **Locate kokoro-v*.onnx and voices-*.bin under model_path, if present.** (1 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **Load Kokoro ONNX and stash on _state.** (1 connections) — `packaging/toolbox/kokoro/kokoro_server.py`
- **Encode float32 mono samples to the requested format.** (1 connections) — `packaging/toolbox/kokoro/kokoro_server.py`

## Relationships

- [BaseModel](BaseModel.md) (1 shared connections)

## Source Files

- `packaging/toolbox/kokoro/kokoro_server.py`

## Audit Trail

- EXTRACTED: 45 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*