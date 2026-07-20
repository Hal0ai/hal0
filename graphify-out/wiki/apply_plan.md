# apply_plan

> 14 nodes · cohesion 0.14

## Key Concepts

- **apply_plan()** (10 connections) — `src/hal0/api/_settings_apply.py`
- **test_apply_plan_accepts_tuple_input()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_apply_plan_collapses_to_one_service_bucket_per_service()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_apply_plan_empty_input_returns_empty_buckets()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_apply_plan_output_is_deterministically_sorted()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_apply_plan_partitions_immediate_service_and_manual()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_apply_plan_unknown_keys_segregated()** (3 connections) — `tests/api/test_settings_apply.py`
- **Partition a set of touched keys into the three apply classes.      Args:** (1 connections) — `src/hal0/api/_settings_apply.py`
- **A heterogeneous input lands in the right three buckets. The     partition is the** (1 connections) — `tests/api/test_settings_apply.py`
- **Keys the registry has no class for land in ``unknown`` rather     than being sil** (1 connections) — `tests/api/test_settings_apply.py`
- **Two calls with the same input (different ordering) return     byte-identical res** (1 connections) — `tests/api/test_settings_apply.py`
- **Callers may pass a tuple (e.g. the keys enumerated from a     dict's ``keys()``** (1 connections) — `tests/api/test_settings_apply.py`
- **A empty PATCH (no keys touched) returns the empty-bucket     shape — the route n** (1 connections) — `tests/api/test_settings_apply.py`
- **Two keys both needing ``slots`` bounced land in the same     ``slots`` bucket —** (1 connections) — `tests/api/test_settings_apply.py`

## Relationships

- [test_settings_apply.py](test_settings_apply.py.md) (6 shared connections)
- [_settings_apply.py](_settings_apply.py.md) (2 shared connections)
- [settings.py](settings.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/_settings_apply.py`
- `tests/api/test_settings_apply.py`

## Audit Trail

- EXTRACTED: 22 (63%)
- INFERRED: 13 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*