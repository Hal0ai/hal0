# test_prewire_smoke.py

> 25 nodes

## Key Concepts

- **test_prewire_smoke.py** (11 connections) — `tests/openwebui/test_prewire_smoke.py`
- **hal0_api()** (8 connections) — `tests/openwebui/test_prewire_smoke.py`
- **_StubModelsHandler** (6 connections) — `tests/openwebui/test_prewire_smoke.py`
- **openwebui_container()** (6 connections) — `tests/openwebui/test_prewire_smoke.py`
- **_free_port()** (5 connections) — `tests/openwebui/test_prewire_smoke.py`
- **stub_upstream()** (4 connections) — `tests/openwebui/test_prewire_smoke.py`
- **_docker_pull()** (3 connections) — `tests/openwebui/test_prewire_smoke.py`
- **_openwebui_bootstrap_token()** (3 connections) — `tests/openwebui/test_prewire_smoke.py`
- **test_openwebui_reads_prewired_env_and_lists_hal0_models()** (3 connections) — `tests/openwebui/test_prewire_smoke.py`
- **_docker_available()** (2 connections) — `tests/openwebui/test_prewire_smoke.py`
- **Path** (2 connections)
- **BaseHTTPRequestHandler** (1 connections)
- **.log_message()** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **.do_GET()** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **MonkeyPatch** (1 connections)
- **End-to-end CI smoke test: the prewired OpenWebUI container talks to hal0.  This** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **Return True iff `docker info` succeeds within 5 s.      `docker` may be installe** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **Bind a TCP socket to port 0 to ask the kernel for a free port.      There's a sm** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **Tiny stub responding to `GET /v1/models` with an OpenAI-shaped list.** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **Start a threaded HTTP server serving a fake `/v1/models`.      Yields ``(base_ur** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **Run a real `uvicorn hal0.api:app` against a temp HAL0_HOME.      Writes an ``ups** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **`docker pull` with a generous timeout — first run on a clean CI     runner needs** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **Launch `OPENWEBUI_IMAGE` (sha256-pinned, #79) against the prewired env.      Gen** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **Sign up the first OpenWebUI user and return its Bearer token.      Even with ``W** (1 connections) — `tests/openwebui/test_prewire_smoke.py`
- **OpenWebUI's `/api/models` advertises the model the hal0 API serves.      OpenWeb** (1 connections) — `tests/openwebui/test_prewire_smoke.py`

## Relationships

- [socket](socket.md) (1 shared connections)
- [GpuImageMode](GpuImageMode.md) (1 shared connections)
- [test_v1_chat_slot_alias.py](test_v1_chat_slot_alias.py.md) (1 shared connections)
- [test_v1_slot_alias_models.py](test_v1_slot_alias_models.py.md) (1 shared connections)
- [write_openwebui_env](write_openwebui_env.md) (1 shared connections)

## Source Files

- `tests/openwebui/test_prewire_smoke.py`

## Audit Trail

- EXTRACTED: 61 (91%)
- INFERRED: 6 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*