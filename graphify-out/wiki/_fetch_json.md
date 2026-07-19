# _fetch_json

> 12 nodes

## Key Concepts

- **_fetch_json()** (9 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_preview()** (8 connections) — `src/hal0/api/routes/comfyui.py`
- **_comfyui_base_url()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **_get_client()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_render_cancel()** (6 connections) — `src/hal0/api/routes/comfyui.py`
- **_build_client()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- **AsyncClient** (2 connections)
- **Timeout** (1 connections)
- **Base URL of the operational ComfyUI container's HTTP API.      Defaults to loopb** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **GET ``path`` from ComfyUI and return parsed JSON, or None if unreachable.      F** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Cancel current and queued renders.      Issues POST /queue (clear: true) and POS** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Proxy the latest output image from the ComfyUI history.      Queries /history fo** (1 connections) — `src/hal0/api/routes/comfyui.py`

## Relationships

- [comfyui.py](comfyui.py.md) (7 shared connections)
- [comfyui_switchover](comfyui_switchover.md) (5 shared connections)
- [Any](Any.md) (3 shared connections)
- [_probe_comfyui](_probe_comfyui.md) (2 shared connections)
- [._post](_post.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/comfyui.py`

## Audit Trail

- EXTRACTED: 45 (94%)
- INFERRED: 3 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*