# test_model_meta.py

> 37 nodes · cohesion 0.06

## Key Concepts

- **test_model_meta.py** (16 connections) — `tests/model_meta/test_model_meta.py`
- **__init__.py** (11 connections) — `src/hal0/model_meta/__init__.py`
- **labels_of()** (6 connections) — `src/hal0/model_meta/__init__.py`
- **capability_from_filename()** (5 connections) — `src/hal0/model_meta/__init__.py`
- **model_capabilities_of()** (5 connections) — `src/hal0/model_meta/__init__.py`
- **Any** (5 connections)
- **_FakeRegistry** (4 connections) — `tests/model_meta/test_model_meta.py`
- **test_is_resolvable()** (4 connections) — `tests/model_meta/test_model_meta.py`
- **test_unknown_device_warns_and_returns_no_opinion()** (4 connections) — `tests/model_meta/test_model_meta.py`
- **test_unknown_legacy_backend_warns_and_defaults_cpu()** (4 connections) — `tests/model_meta/test_model_meta.py`
- **classify()** (3 connections) — `src/hal0/model_meta/__init__.py`
- **DeviceMeta** (3 connections) — `src/hal0/model_meta/__init__.py`
- **Any** (3 connections)
- **test_labels_of()** (3 connections) — `tests/model_meta/test_model_meta.py`
- **test_model_is_mtp_eligible()** (3 connections) — `tests/model_meta/test_model_meta.py`
- **test_profiles_literals_match_model_meta()** (3 connections) — `tests/model_meta/test_model_meta.py`
- **LogCaptureFixture** (2 connections)
- **test_capability_from_filename()** (2 connections) — `tests/model_meta/test_model_meta.py`
- **test_classify()** (2 connections) — `tests/model_meta/test_model_meta.py`
- **test_schema_reexports_alias_the_canonical_taxonomy()** (2 connections) — `tests/model_meta/test_model_meta.py`
- **model_meta — the one home for model classification + device→backend resolution.** (1 connections) — `src/hal0/model_meta/__init__.py`
- **Return the primary modality bucket for a model.      Reads the model's ``capabil** (1 connections) — `src/hal0/model_meta/__init__.py`
- **Best-effort capability token inferred from a model filename.      Returns one of** (1 connections) — `src/hal0/model_meta/__init__.py`
- **Pull the ``model.labels`` list out of a slot config dict.      Kept as the fallb** (1 connections) — `src/hal0/model_meta/__init__.py`
- **Extract the typed ``ModelCapabilities`` bools from a registry dump.      ``model** (1 connections) — `src/hal0/model_meta/__init__.py`
- *... and 12 more nodes in this community*

## Relationships

- [_reconcile_device_profile](_reconcile_device_profile.md) (3 shared connections)
- [slots.py](slots.py.md) (3 shared connections)
- [test_mtp_override.py](test_mtp_override.py.md) (3 shared connections)
- [.apply](apply.md) (2 shared connections)
- [map_backend_to_device](map_backend_to_device.md) (2 shared connections)
- [make_slot](make_slot.md) (2 shared connections)
- [RoutingHost](RoutingHost.md) (2 shared connections)
- [detect](detect.md) (1 shared connections)
- [_guess_capability](_guess_capability.md) (1 shared connections)
- [test_modality.py](test_modality.py.md) (1 shared connections)
- [die](die.md) (1 shared connections)

## Source Files

- `src/hal0/model_meta/__init__.py`
- `tests/model_meta/test_model_meta.py`

## Audit Trail

- EXTRACTED: 91 (85%)
- INFERRED: 16 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*