"""SC-11: the one shared slot-projection merge primitive.

Both the store (:meth:`SlotConfigStore._reconciled_slot`) and
:meth:`SlotManager.update_config` used to hand-roll the same "base dict +
updates dict → merged dict with the ``ctx_size`` alias folded" mechanic.
They now share :func:`hal0.slot_config.merge_slot_config`. These tests pin
the load-bearing nuances of that primitive:

  - the one-level-deep, value-wins merge (sibling ``[model]`` keys survive),
  - the ``ctx_size`` → ``context_size`` fold (fresh alias wins, alias
    dropped),
  - COPY-SAFETY: the base dict is never mutated, so it is safe for the
    store's commit diff / rollback (``before`` vs ``after``).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from hal0.capabilities.config import CapabilitySelection
from hal0.slot_config import (
    SlotConfigStore,
    SlotSelection,
    merge_slot_config,
)

# ── merge_slot_config unit behaviour ─────────────────────────────────────────


def test_merge_one_level_deep_keeps_model_siblings() -> None:
    """A partial ``{"model": {...}}`` update merges into the nested table,
    not clobber it — ``[model].default`` survives a ctx-only write."""
    base = {"model": {"default": "m", "context_size": 4096}, "enabled": True}
    after = merge_slot_config(base, {"model": {"context_size": 8192}})
    assert after["model"] == {"default": "m", "context_size": 8192}


def test_merge_none_deletes_key() -> None:
    """An explicit ``None`` in updates DELETES the key (TOML has no null).

    This is what lets ``PUT /config {"mtp": null}`` reset a slot to MTP AUTO:
    the old behaviour merged ``mtp=None`` into the dict and the TOML writer
    (tomli_w) raised ``TypeError: NoneType is not TOML serializable`` → 500,
    leaving no API path back to Auto once an override existed.
    """
    base = {"mtp": True, "enabled": True}
    after = merge_slot_config(base, {"mtp": None})
    assert "mtp" not in after
    assert after["enabled"] is True
    # base untouched (copy-safety)
    assert base["mtp"] is True


def test_merge_none_deletes_missing_key_is_noop() -> None:
    after = merge_slot_config({"enabled": True}, {"mtp": None})
    assert "mtp" not in after
    assert after == {"enabled": True}


def test_merge_none_deletes_nested_key() -> None:
    """Same None-deletes rule one level deep: {"server": {"extra_args": null}}
    removes the sub-key instead of poisoning the TOML writer."""
    base = {"server": {"extra_args": "-b 512", "env": {"X": "1"}}}
    after = merge_slot_config(base, {"server": {"extra_args": None}})
    assert after["server"] == {"env": {"X": "1"}}
    # base sub-dict untouched (copy-safety)
    assert base["server"]["extra_args"] == "-b 512"


def test_merge_scalars_and_lists_replace_wholesale() -> None:
    base = {"workers": 1, "labels": ["a"]}
    after = merge_slot_config(base, {"workers": 4, "labels": ["b"]})
    assert after["workers"] == 4
    assert after["labels"] == ["b"]


def test_merge_folds_ctx_size_alias() -> None:
    """Fresh ``ctx_size`` wins over a stale ``context_size`` seed, then the
    alias is dropped so exactly one key survives (#585)."""
    base = {"model": {"default": "m", "context_size": 4096}}
    after = merge_slot_config(base, {"model": {"ctx_size": 32768}})
    assert after["model"] == {"default": "m", "context_size": 32768}
    assert "ctx_size" not in after["model"]


def test_merge_folds_ctx_size_keeps_default_sibling() -> None:
    """manager.py dropped-sibling scenario: ``{"model": {"ctx_size": N}}``
    must keep ``[model].default``."""
    base = {"model": {"default": "keep-me"}}
    after = merge_slot_config(base, {"model": {"ctx_size": 2048}})
    assert after["model"]["default"] == "keep-me"
    assert after["model"]["context_size"] == 2048


def test_merge_is_copy_safe_when_updates_carry_no_model() -> None:
    """The load-bearing nuance: a pure non-model update (e.g. a disable) that
    still triggers the ctx_size fold on the inherited base ``[model]`` must
    NOT mutate the caller's base dict. Proves the fold copies before pop."""
    base = {"model": {"default": "m", "ctx_size": 4096}, "enabled": True}
    after = merge_slot_config(base, {"enabled": False})
    # The returned copy folded the alias …
    assert after["model"]["context_size"] == 4096
    assert "ctx_size" not in after["model"]
    assert after["enabled"] is False
    # … while the caller's base is untouched (still the legacy alias).
    assert base["model"] == {"default": "m", "ctx_size": 4096}
    assert base["enabled"] is True


def test_merge_does_not_alias_base_model_on_merge_path() -> None:
    """Even when updates DO carry a model sub-table, the base's model dict
    must not be mutated (fresh merged dict, not in-place)."""
    base = {"model": {"default": "m", "context_size": 4096}}
    after = merge_slot_config(base, {"model": {"context_size": 8192}})
    after["model"]["default"] = "mutated"
    assert base["model"]["default"] == "m"


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
                "enabled = true",
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
    """A pure disable carries NO ``[model]`` update, yet the slot's inherited
    ``[model]`` still gets its ctx_size folded — via the shared copy-safe
    helper. The ChangeSet must see ``before != after`` (so commit writes) and
    the folded ``context_size`` must land on disk. A naive extraction that
    reused the in-place ``_normalize_ctx_key`` would fold ``before`` too and
    could break the diff — this pins that it does not."""
    slot_path = _write_embed_slot(tmp_hal0_home, ["ctx_size = 4096"])
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
    # Disable flips enabled=false and folds the inherited ctx_size alias.
    assert slot_change.data["enabled"] is False
    assert slot_change.data["model"]["context_size"] == 4096
    assert "ctx_size" not in slot_change.data["model"]
    # The before snapshot must remain the un-folded legacy shape (proof the
    # shared helper did not mutate the base), so before != after → commit writes.
    assert before_change.data is not None
    assert before_change.data["model"].get("ctx_size") == 4096
    assert before_change.data != slot_change.data
    assert cs.changed

    store.commit(cs)
    with open(slot_path, "rb") as f:
        on_disk = tomllib.load(f)
    assert on_disk["enabled"] is False
    assert on_disk["model"]["context_size"] == 4096
    assert "ctx_size" not in on_disk["model"]


def test_store_disable_does_not_rewrite_backend_device_model(tmp_hal0_home: str) -> None:
    """SC-1 parity: a disable selection produces ``enabled=False`` and does
    NOT rewrite backend/device/model siblings (the store only reconciles those
    when the selection is enabled)."""
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
    assert slot_change.data["enabled"] is False
    # A disable must NOT rewrite the siblings from the (ignored) selection.
    assert slot_change.data["backend"] == "vulkan"
    assert slot_change.data["provider"] == "llama-server"
    assert slot_change.data["model"]["default"] == "old-model"
