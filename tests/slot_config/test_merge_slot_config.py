"""SC-11: the one shared slot-projection merge primitive.

Both the store (:meth:`SlotConfigStore._reconciled_slot`) and
:meth:`SlotManager.update_config` used to hand-roll the same "base dict +
updates dict → merged dict" mechanic. They now share
:func:`hal0.slot_config.merge_slot_config`. These tests pin the load-bearing
nuances of that primitive:

  - the one-level-deep, value-wins merge (sibling table keys survive),
  - ``None`` deletes a key (TOML has no null),
  - it is a pure merge: no context-size alias fold (ADR-0029 refuses slot
    tuning keys upstream, with ``slot.tuning_key_denied``),
  - COPY-SAFETY: the base dict is never mutated, so it is safe for the
    store's commit diff / rollback (``before`` vs ``after``).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.capabilities.config import CapabilitySelection
from hal0.config.loader import _flatten_slot_toml
from hal0.slot_config import (
    SlotConfigStore,
    SlotSelection,
    merge_slot_config,
    slot_scalar_table,
)

# ── merge_slot_config unit behaviour ─────────────────────────────────────────


def test_merge_one_level_deep_keeps_table_siblings() -> None:
    """A partial table update merges into the nested table instead of
    clobbering it — sibling keys survive."""
    base = {"model": {"default": "m"}, "image": {"default_size": "512x512"}, "idle_timeout_s": 1}
    after = merge_slot_config(base, {"image": {"default_steps": 8}})
    assert after["image"] == {"default_size": "512x512", "default_steps": 8}
    assert after["model"] == {"default": "m"}


def test_merge_none_deletes_key() -> None:
    """An explicit ``None`` in updates DELETES the key (TOML has no null).

    The old behaviour merged ``None`` into the dict and the TOML writer
    (tomli_w) raised ``TypeError: NoneType is not TOML serializable`` → 500,
    leaving no API path to clear a key once it was set.
    """
    base = {"image_pin": "ghcr.io/x/y:z", "idle_timeout_s": 1}
    after = merge_slot_config(base, {"image_pin": None})
    assert "image_pin" not in after
    assert after["idle_timeout_s"] == 1
    # base untouched (copy-safety)
    assert base["image_pin"] == "ghcr.io/x/y:z"


def test_merge_none_deletes_missing_key_is_noop() -> None:
    after = merge_slot_config({"idle_timeout_s": 1}, {"image_pin": None})
    assert after == {"idle_timeout_s": 1}


def test_merge_none_deletes_nested_key() -> None:
    """Same None-deletes rule one level deep: {"npu": {"asr": null}} removes
    the sub-key instead of poisoning the TOML writer."""
    base = {"npu": {"asr": True, "embed": True}}
    after = merge_slot_config(base, {"npu": {"asr": None}})
    assert after["npu"] == {"embed": True}
    # base sub-dict untouched (copy-safety)
    assert base["npu"]["asr"] is True


def test_merge_scalars_and_lists_replace_wholesale() -> None:
    base = {"idle_timeout_s": 1, "labels": ["a"]}
    after = merge_slot_config(base, {"idle_timeout_s": 4, "labels": ["b"]})
    assert after["idle_timeout_s"] == 4
    assert after["labels"] == ["b"]


def test_merge_has_no_context_size_alias_fold() -> None:
    """ADR-0029 removed the #585 ``ctx_size`` → ``context_size`` fold: the
    merge is a pure projection and never invents a ``context_size``. Slot
    tuning keys are refused before any merge (``reject_slot_tuning_keys`` /
    ``reconcile_slot_updates``), so the merge itself leaves them alone."""
    base = {"model": {"default": "m"}}
    after = merge_slot_config(base, {"model": {"ctx_size": 2048}})
    assert after["model"] == {"default": "m", "ctx_size": 2048}
    assert "context_size" not in after["model"]


def test_tuning_keys_are_refused_before_the_merge() -> None:
    """The write pipeline that feeds the merge refuses tuning keys."""
    from hal0.errors import BadRequest
    from hal0.slots.config_write import reconcile_slot_updates

    with pytest.raises(BadRequest) as exc:
        reconcile_slot_updates({"model": {"default": "m"}}, {"model": {"ctx_size": 2048}})
    assert exc.value.code == "slot.tuning_key_denied"


def test_merge_is_copy_safe_when_updates_carry_no_table() -> None:
    """A pure scalar update (e.g. a disable's sibling write) must NOT mutate
    the caller's base dict or its inherited tables."""
    base = {"model": {"default": "m"}, "npu": {"asr": True}, "idle_timeout_s": 1}
    snapshot = {"model": {"default": "m"}, "npu": {"asr": True}, "idle_timeout_s": 1}
    after = merge_slot_config(base, {"idle_timeout_s": 4})
    assert after["idle_timeout_s"] == 4
    assert after["model"] == {"default": "m"}
    assert base == snapshot


def test_merge_does_not_alias_base_table_on_merge_path() -> None:
    """Even when updates DO carry a sub-table, the base's table must not be
    mutated (fresh merged dict, not in-place)."""
    base = {"model": {"default": "m"}}
    after = merge_slot_config(base, {"model": {"default": "n"}})
    after["model"]["default"] = "mutated"
    assert base["model"]["default"] == "m"


# ── nested ([slot]) shape projection (#1685) ─────────────────────────────────
#
# _flatten_slot_toml treats [slot] as AUTHORITATIVE when it exists: only its
# keys become the loaded SlotConfig's scalars, and every top-level scalar
# sibling is stashed into `extra` and ignored. merge_slot_config used to
# always project scalar updates onto the ROOT regardless of shape, so a
# scalar write on a nested-shape file landed on a key nobody reads.


def test_merge_scalar_update_targets_nested_slot_table() -> None:
    """A scalar update on a nested-shape base lands in [slot], not the root."""
    base = {"slot": {"name": "embed", "device": "gpu-vulkan", "port": 8082}}
    after = merge_slot_config(base, {"device": "gpu-rocm"})
    assert after["slot"]["device"] == "gpu-rocm"
    assert "device" not in after  # never duplicated at the root


def test_merge_scalar_update_still_targets_root_on_flat_base() -> None:
    """Flat-shape bases are unaffected — scalars still merge at the root."""
    base = {"device": "gpu-vulkan", "port": 8082}
    after = merge_slot_config(base, {"device": "gpu-rocm"})
    assert after["device"] == "gpu-rocm"
    assert "slot" not in after


def test_merge_table_update_stays_at_root_on_nested_base() -> None:
    """A sibling TABLE update ([model]/[server]/[npu]) always lands at the
    root — it is never nested inside [slot] in either on-disk shape."""
    base = {
        "slot": {"name": "embed", "device": "gpu-vulkan"},
        "model": {"default": "old-model"},
    }
    after = merge_slot_config(base, {"model": {"default": "new-model"}})
    assert after["model"] == {"default": "new-model"}
    assert "model" not in after["slot"]


def test_merge_scalar_delete_on_nested_base_targets_slot_table() -> None:
    base = {"slot": {"name": "embed", "mtp": True}}
    after = merge_slot_config(base, {"mtp": None})
    assert "mtp" not in after["slot"]


def test_merge_nested_base_slot_subdict_is_copy_safe() -> None:
    """The [slot] sub-dict is copied before mutation, like [model]."""
    base = {"slot": {"name": "embed", "device": "gpu-vulkan"}}
    after = merge_slot_config(base, {"device": "gpu-rocm"})
    assert base["slot"]["device"] == "gpu-vulkan"
    assert after["slot"]["device"] == "gpu-rocm"


def test_merge_nested_shape_round_trips_through_flatten() -> None:
    """End-to-end: a scalar write survives the SAME read path the runtime
    uses (_flatten_slot_toml) — the actual regression from #1685. Before the
    fix, `_flatten_slot_toml("embed").device` would still read "gpu-vulkan"
    after this merge, because the write landed on a root key `_flatten_slot_toml`
    discards into `extra` whenever `[slot]` exists."""
    base = {
        "slot": {"name": "embed", "device": "gpu-vulkan", "port": 8082},
        "model": {"default": "nomic-embed:v1"},
    }
    after = merge_slot_config(base, {"device": "gpu-rocm"})
    flat = _flatten_slot_toml(after, "embed")
    assert flat["device"] == "gpu-rocm"


def test_slot_scalar_table_returns_slot_subdict_when_nested() -> None:
    raw = {"slot": {"name": "embed"}, "model": {"default": "m"}}
    assert slot_scalar_table(raw) is raw["slot"]


def test_slot_scalar_table_returns_root_when_flat() -> None:
    raw = {"name": "embed", "device": "gpu-vulkan"}
    assert slot_scalar_table(raw) is raw


# ── store integration: copy-safety survives commit ───────────────────────────


def _etc(home: str) -> Path:
    return Path(home) / "etc" / "hal0"


def _write_caps_disabled_embed(home: str) -> Path:
    path = _etc(home) / "capabilities.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "schema_version = 2",
                "[selections.embed.embed]",
                'device = "gpu-vulkan"',
                'provider = "llama-server"',
                'model = "old-model"',
                "enabled = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_embed_slot(home: str, extra_model_lines: list[str]) -> Path:
    slots_dir = _etc(home) / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    slot_path = slots_dir / "embed.toml"
    slot_path.write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8082",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "[model]",
                'default = "old-model"',
                *extra_model_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return slot_path


def test_store_copy_safe_disable_still_writes(tmp_hal0_home: str) -> None:
    """A disable clears ``[model].default`` through the shared copy-safe merge.
    The ChangeSet must see ``before != after`` (so commit writes), and the
    ``before`` snapshot must still hold the old model (proof the helper did
    not mutate the base, which would break the diff and the rollback)."""
    slot_path = _write_embed_slot(tmp_hal0_home, [])
    _write_caps_disabled_embed(tmp_hal0_home)

    store = SlotConfigStore()
    sel = SlotSelection(
        slot="embed",
        child="embed",
        slot_name="embed",
        selection=CapabilitySelection(
            device="gpu-vulkan",
            provider="llama-server",
            model="old-model",
            enabled=False,
        ),
    )
    cs = store.apply(sel)
    slot_change = next(fs for fs in cs.after if fs.path == slot_path)
    before_change = next(fs for fs in cs.before if fs.path == slot_path)
    assert slot_change.data is not None
    assert slot_change.data["model"] == {"default": ""}
    assert before_change.data is not None
    assert before_change.data["model"] == {"default": "old-model"}
    assert before_change.data != slot_change.data
    assert cs.changed

    store.commit(cs)
    with open(slot_path, "rb") as f:
        on_disk = tomllib.load(f)
    assert on_disk["model"] == {"default": ""}


def test_store_disable_does_not_rewrite_backend_device_model(tmp_hal0_home: str) -> None:
    """SC-1 parity: a disable selection clears ``[model].default`` and does
    NOT rewrite the backend/device/provider siblings (the store only
    reconciles those when the selection is enabled)."""
    slot_path = _write_embed_slot(tmp_hal0_home, [])
    _write_caps_disabled_embed(tmp_hal0_home)

    store = SlotConfigStore()
    sel = SlotSelection(
        slot="embed",
        child="embed",
        slot_name="embed",
        selection=CapabilitySelection(
            device="gpu-rocm",  # a DIFFERENT device than on disk
            provider="some-other",
            model="new-model",
            enabled=False,
        ),
    )
    cs = store.apply(sel)
    slot_change = next(fs for fs in cs.after if fs.path == slot_path)
    assert slot_change.data is not None
    assert slot_change.data["model"]["default"] == ""
    # A disable must NOT rewrite the siblings from the (ignored) selection —
    # note it does NOT adopt the selection's "new-model" either.
    assert slot_change.data["backend"] == "vulkan"
    assert slot_change.data["provider"] == "llama-server"
