# test_model_store_root.py

> 20 nodes

## Key Concepts

- **test_model_store_root.py** (12 connections) — `tests/config/test_model_store_root.py`
- **_Cfg** (9 connections) — `tests/config/test_model_store_root.py`
- **_Models** (4 connections) — `tests/config/test_model_store_root.py`
- **test_mount_roots_distinct_store_and_pull_root()** (3 connections) — `tests/config/test_model_store_root.py`
- **test_mount_roots_dedup_when_store_equals_pull_root()** (3 connections) — `tests/config/test_model_store_root.py`
- **test_mount_roots_dedup_nested()** (3 connections) — `tests/config/test_model_store_root.py`
- **test_mount_roots_trailing_slash_not_double_dropped()** (3 connections) — `tests/config/test_model_store_root.py`
- **.__init__()** (2 connections) — `tests/config/test_model_store_root.py`
- **test_env_var_wins()** (2 connections) — `tests/config/test_model_store_root.py`
- **test_config_store_used_when_no_env()** (2 connections) — `tests/config/test_model_store_root.py`
- **test_default_when_store_empty()** (2 connections) — `tests/config/test_model_store_root.py`
- **.__init__()** (1 connections) — `tests/config/test_model_store_root.py`
- **.effective_store()** (1 connections) — `tests/config/test_model_store_root.py`
- **test_default_when_config_unreadable()** (1 connections) — `tests/config/test_model_store_root.py`
- **test_env_is_stripped()** (1 connections) — `tests/config/test_model_store_root.py`
- **model_store_root() — thin shim over hal0.config.store.store_root().  Precedence** (1 connections) — `tests/config/test_model_store_root.py`
- **store != pull_root → BOTH roots mount so a model file under pull_root     (the e** (1 connections) — `tests/config/test_model_store_root.py`
- **store == pull_root → exactly ONE mount (no duplicate Volume).** (1 connections) — `tests/config/test_model_store_root.py`
- **A root nested under another collapses to the covering ancestor.** (1 connections) — `tests/config/test_model_store_root.py`
- **normpath collapses "/mnt/ai-models/" == "/mnt/ai-models" to one entry     (a mut** (1 connections) — `tests/config/test_model_store_root.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/config/test_model_store_root.py`

## Audit Trail

- EXTRACTED: 54 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*