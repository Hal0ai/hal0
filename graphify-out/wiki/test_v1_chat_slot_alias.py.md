# test_v1_chat_slot_alias.py

> 35 nodes · cohesion 0.13

## Key Concepts

- **test_v1_chat_slot_alias.py** (19 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **_rewrite_chat_slot_alias()** (15 connections) — `src/hal0/api/routes/v1.py`
- **_FakeRequest** (11 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **_FakeSlotManager** (11 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_rewrite_flm_tag_passthrough_is_noop()** (8 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_rewrite_normalizes_direct_flm_catalog_id_to_tag()** (8 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_rewrite_translates_flm_alias_to_served_tag()** (8 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **_three_chat_slots()** (7 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **_npu_flm_slot()** (6 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **_patch_flm_id_to_tag()** (6 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **Any** (6 connections)
- **MonkeyPatch** (6 connections)
- **test_rewrite_is_noop_for_bare_model_id()** (6 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_chat_alias_is_rewritten_before_dispatch()** (5 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_rewrite_each_alias_maps_to_its_distinct_model()** (5 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_rewrite_translates_alias_to_model_id_and_body()** (5 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **_patch_alias()** (4 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_alias_map_covers_enabled_chat_slots_only()** (4 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_chat_slot_model_ids_for_dedup()** (4 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **_FakeApp** (3 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **.__init__()** (3 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_rewrite_is_noop_without_slot_manager()** (3 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **.__init__()** (2 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **.__init__()** (2 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **.iter_configs()** (2 connections) — `tests/api/test_v1_chat_slot_alias.py`
- *... and 10 more nodes in this community*

## Relationships

- [v1.py](v1.py.md) (5 shared connections)
- [lifespan](lifespan.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)
- [Upstream](Upstream.md) (1 shared connections)
- [test_auth_core.py](test_auth_core.py.md) (1 shared connections)
- [test_prewire_smoke.py](test_prewire_smoke.py.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/v1.py`
- `tests/api/test_v1_chat_slot_alias.py`

## Audit Trail

- EXTRACTED: 148 (88%)
- INFERRED: 21 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*