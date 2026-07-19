# write_openwebui_env

> 15 nodes · cohesion 0.18

## Key Concepts

- **write_openwebui_env()** (18 connections) — `src/hal0/openwebui/env_writer.py`
- **env_writer.py** (6 connections) — `src/hal0/openwebui/env_writer.py`
- **default_openwebui_env()** (4 connections) — `src/hal0/openwebui/env_writer.py`
- **_default_path()** (4 connections) — `src/hal0/openwebui/env_writer.py`
- **_load_write_env_atomic()** (3 connections) — `src/hal0/openwebui/env_writer.py`
- **main()** (3 connections) — `src/hal0/openwebui/env_writer.py`
- **Path** (3 connections)
- **test_default_openwebui_env_returns_fresh_copy()** (3 connections) — `tests/openwebui/test_env_writer.py`
- **OpenWebUI environment file writer.  write_openwebui_env() produces /etc/hal0/ope** (1 connections) — `src/hal0/openwebui/env_writer.py`
- **Resolve the default openwebui.env path without importing hal0.config.      Mirro** (1 connections) — `src/hal0/openwebui/env_writer.py`
- **Return a fresh copy of the prewired defaults.      Returns a new dict each call** (1 connections) — `src/hal0/openwebui/env_writer.py`
- **Write the OpenWebUI environment file atomically.      Args:         path:      D** (1 connections) — `src/hal0/openwebui/env_writer.py`
- **CLI entry: ``python -m hal0.openwebui.env_writer``.      Writes the prewired env** (1 connections) — `src/hal0/openwebui/env_writer.py`
- **Load ``hal0.config.env.write_env_atomic`` without triggering     ``hal0.config._** (1 connections) — `src/hal0/openwebui/env_writer.py`
- **Mutating one returned dict must not affect subsequent calls.** (1 connections) — `tests/openwebui/test_env_writer.py`

## Relationships

- [test_env_writer.py](test_env_writer.py.md) (11 shared connections)
- [write_env_atomic](write_env_atomic.md) (1 shared connections)
- [test_prewire_smoke.py](test_prewire_smoke.py.md) (1 shared connections)

## Source Files

- `src/hal0/openwebui/env_writer.py`
- `tests/openwebui/test_env_writer.py`

## Audit Trail

- EXTRACTED: 37 (73%)
- INFERRED: 14 (27%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*