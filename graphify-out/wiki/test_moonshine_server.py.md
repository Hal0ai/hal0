# test_moonshine_server.py

> 14 nodes

## Key Concepts

- **test_moonshine_server.py** (9 connections) — `tests/providers/test_moonshine_server.py`
- **ModuleType** (4 connections)
- **TestClient** (4 connections)
- **_load_moonshine_server()** (3 connections) — `tests/providers/test_moonshine_server.py`
- **server_module()** (3 connections) — `tests/providers/test_moonshine_server.py`
- **client()** (3 connections) — `tests/providers/test_moonshine_server.py`
- **test_text_payload_returns_415_without_ffmpeg_argv()** (3 connections) — `tests/providers/test_moonshine_server.py`
- **test_single_byte_payload_returns_415()** (3 connections) — `tests/providers/test_moonshine_server.py`
- **test_empty_upload_still_returns_400()** (3 connections) — `tests/providers/test_moonshine_server.py`
- **test_redact_ffmpeg_argv_masks_input_path()** (2 connections) — `tests/providers/test_moonshine_server.py`
- **Unit tests for the in-container moonshine_server FastAPI app.  The server lives** (1 connections) — `tests/providers/test_moonshine_server.py`
- **Posting a text/plain payload (claimed audio/wav) must not leak ffmpeg argv.** (1 connections) — `tests/providers/test_moonshine_server.py`
- **A 1-byte file claiming audio/wav is also rejected without leaks.** (1 connections) — `tests/providers/test_moonshine_server.py`
- **The pre-existing empty-upload guard is preserved — it short-circuits     before** (1 connections) — `tests/providers/test_moonshine_server.py`

## Relationships

- [types.py](types.py.md) (1 shared connections)

## Source Files

- `tests/providers/test_moonshine_server.py`

## Audit Trail

- EXTRACTED: 41 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*