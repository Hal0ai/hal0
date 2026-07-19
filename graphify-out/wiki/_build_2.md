# _build

> 11 nodes

## Key Concepts

- **_build()** (11 connections) — `tests/api/test_memory_gate.py`
- **test_memory_gate.py** (6 connections) — `tests/api/test_memory_gate.py`
- **test_memory_enabled_by_default()** (3 connections) — `tests/api/test_memory_gate.py`
- **test_status_exposes_memory_enabled_as_bool()** (3 connections) — `tests/api/test_memory_gate.py`
- **FastAPI** (2 connections)
- **test_memory_disabled_when_config_says_so()** (2 connections) — `tests/api/test_memory_gate.py`
- **TestClient** (1 connections)
- **Memory gate — ``[memory].enabled`` toggles the whole subsystem.  The memory engi** (1 connections) — `tests/api/test_memory_gate.py`
- **Build a fresh app + client with ``[memory].enabled`` set (or left at     its sch** (1 connections) — `tests/api/test_memory_gate.py`
- **No hal0.toml at all → the schema default (`enabled=True`) applies.** (1 connections) — `tests/api/test_memory_gate.py`
- **/api/status always carries a boolean memory_enabled field.** (1 connections) — `tests/api/test_memory_gate.py`

## Relationships

- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [MemoryConfig](MemoryConfig.md) (1 shared connections)

## Source Files

- `tests/api/test_memory_gate.py`

## Audit Trail

- EXTRACTED: 28 (88%)
- INFERRED: 4 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*