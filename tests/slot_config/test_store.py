"""SlotConfigStore — invariant + behaviour tests (issue #697).

The store owns BOTH ``capabilities.toml`` selections and ``slots/*.toml``
as one reconciled truth. The locked interface:

  - ``apply(selection) -> ChangeSet`` is COMPUTE-ONLY (writes nothing),
  - ``commit(cs)`` atomically writes ``cs.after``,
  - ``revert(cs)`` restores ``cs.before``.

The drift invariant pinned here:

  - after ``commit``: disk == ``cs.after``
  - after ``revert``: disk == ``cs.before``
  - a failed mid-commit leaves disk at ``before`` — never half-reconciled.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.config import CapabilitySelection, load_capabilities_config
from hal0.slot_config import ChangeSet, SlotConfigStore, SlotSelection, write_slot_toml

# ── fixtures / helpers ────────────────────────────────────────────────────────


def _etc(home: str) -> Path:
    return Path(home) / "etc" / "hal0"


def _write_embed_slot(home: str, *, extra_lines: list[str] | None = None) -> Path:
    """A drifted vulkan/llama-server embed slot TOML (the prod bug shape)."""
    slots_dir = _etc(home) / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        'name = "embed"',
        "port = 8082",
        'backend = "vulkan"',
        'provider = "llama-server"',
        "enabled = true",
        "[model]",
        'default = "old-model"',
    ]
    if extra_lines:
        lines += extra_lines
    path = slots_dir / "embed.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_caps(home: str) -> Path:
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


def _selection(
    *,
    device: str = "npu",
    provider: str = "flm",
    model: str = "nomic-embed-text-v1.5-q8_0",
    enabled: bool = True,
    slot: str = "embed",
    child: str = "embed",
    slot_name: str = "embed",
) -> SlotSelection:
    return SlotSelection(
        slot=slot,
        child=child,
        slot_name=slot_name,
        selection=CapabilitySelection(
            device=device, provider=provider, model=model, enabled=enabled
        ),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


# ── apply() is compute-only ──────────────────────────────────────────────────


def test_apply_writes_nothing(tmp_hal0_home: str) -> None:
    slot_path = _write_embed_slot(tmp_hal0_home)
    caps_path = _write_caps(tmp_hal0_home)
    slot_bytes = slot_path.read_bytes()
    caps_bytes = caps_path.read_bytes()

    store = SlotConfigStore()
    cs = store.apply(_selection())

    assert isinstance(cs, ChangeSet)
    assert slot_path.read_bytes() == slot_bytes, "apply() must not touch slots/*.toml"
    assert caps_path.read_bytes() == caps_bytes, "apply() must not touch capabilities.toml"


def test_apply_before_matches_disk(tmp_hal0_home: str) -> None:
    slot_path = _write_embed_slot(tmp_hal0_home)
    caps_path = _write_caps(tmp_hal0_home)

    cs = SlotConfigStore().apply(_selection())

    by_path = {fs.path: fs for fs in cs.before}
    assert by_path[caps_path].data == _read_toml(caps_path)
    assert by_path[slot_path].data == _read_toml(slot_path)


# ── reconciliation content (model_meta-derived) ──────────────────────────────


def test_apply_reconciles_slot_fields_for_npu(tmp_hal0_home: str) -> None:
    """device=npu → slot TOML backend=flm (legacy token) + device=npu."""
    slot_path = _write_embed_slot(tmp_hal0_home)
    _write_caps(tmp_hal0_home)

    cs = SlotConfigStore().apply(_selection(device="npu", provider="flm"))

    after = {fs.path: fs.data for fs in cs.after}[slot_path]
    assert after is not None
    assert after["backend"] == "flm"
    assert after["device"] == "npu"
    assert after["provider"] == "flm"
    assert after["model"]["default"] == "nomic-embed-text-v1.5-q8_0"
    # Untouched fields survive the one-level merge.
    assert after["port"] == 8082
    assert after["enabled"] is True
    assert after["name"] == "embed"


def test_apply_reconciles_slot_fields_for_gpu_rocm(tmp_hal0_home: str) -> None:
    slot_path = _write_embed_slot(tmp_hal0_home)
    _write_caps(tmp_hal0_home)

    cs = SlotConfigStore().apply(
        _selection(device="gpu-rocm", provider="llama-server", model="new-model")
    )

    after = {fs.path: fs.data for fs in cs.after}[slot_path]
    assert after is not None
    assert after["backend"] == "rocm"
    assert after["device"] == "gpu-rocm"
    assert after["model"]["default"] == "new-model"


def test_apply_preserves_model_siblings_and_folds_ctx_alias(tmp_hal0_home: str) -> None:
    """[model] sibling keys survive; the legacy ctx_size alias folds (#585)."""
    slot_path = _write_embed_slot(tmp_hal0_home, extra_lines=["ctx_size = 4096"])
    _write_caps(tmp_hal0_home)

    cs = SlotConfigStore().apply(_selection(model="new-model"))

    after = {fs.path: fs.data for fs in cs.after}[slot_path]
    assert after is not None
    assert after["model"]["default"] == "new-model"
    assert after["model"]["context_size"] == 4096
    assert "ctx_size" not in after["model"]


def test_apply_updates_capabilities_selection(tmp_hal0_home: str) -> None:
    _write_embed_slot(tmp_hal0_home)
    caps_path = _write_caps(tmp_hal0_home)

    cs = SlotConfigStore().apply(_selection())

    after = {fs.path: fs.data for fs in cs.after}[caps_path]
    assert after is not None
    sel = after["selections"]["embed"]["embed"]
    assert sel["device"] == "npu"
    assert sel["provider"] == "flm"
    assert sel["enabled"] is True
    # The canonical persisted shape drops the deprecated backend alias.
    assert "backend" not in sel
    assert after["schema_version"] == 2


def test_apply_skips_slot_toml_when_disabled(tmp_hal0_home: str) -> None:
    """A disable-only change must not reconcile the slot TOML (parity with
    the pre-store orchestrator: pure disable never rewrote the slot)."""
    slot_path = _write_embed_slot(tmp_hal0_home)
    _write_caps(tmp_hal0_home)

    cs = SlotConfigStore().apply(_selection(enabled=False))

    before = {fs.path: fs.data for fs in cs.before}[slot_path]
    after = {fs.path: fs.data for fs in cs.after}[slot_path]
    assert after == before


def test_apply_skips_slot_toml_when_missing(tmp_hal0_home: str) -> None:
    """Slot TOML absent → creation stays with SlotManager.create; the store
    neither invents the file nor fails."""
    _write_caps(tmp_hal0_home)
    slot_path = _etc(tmp_hal0_home) / "slots" / "embed.toml"

    cs = SlotConfigStore().apply(_selection())

    states = {fs.path: fs.data for fs in cs.after}
    assert states[slot_path] is None
    assert not slot_path.exists()


# ── commit / revert invariants ───────────────────────────────────────────────


def test_commit_writes_after_to_disk(tmp_hal0_home: str) -> None:
    slot_path = _write_embed_slot(tmp_hal0_home)
    caps_path = _write_caps(tmp_hal0_home)

    store = SlotConfigStore()
    cs = store.apply(_selection())
    store.commit(cs)

    after = {fs.path: fs.data for fs in cs.after}
    assert _read_toml(slot_path) == after[slot_path]
    assert _read_toml(caps_path) == after[caps_path]
    # The committed selection round-trips through the canonical loader.
    sel = load_capabilities_config(caps_path).selections["embed"]["embed"]
    assert sel.device == "npu"
    assert sel.enabled is True


def test_revert_restores_before(tmp_hal0_home: str) -> None:
    slot_path = _write_embed_slot(tmp_hal0_home)
    caps_path = _write_caps(tmp_hal0_home)

    store = SlotConfigStore()
    cs = store.apply(_selection())
    store.commit(cs)
    store.revert(cs)

    before = {fs.path: fs.data for fs in cs.before}
    assert _read_toml(slot_path) == before[slot_path]
    assert _read_toml(caps_path) == before[caps_path]


def test_revert_removes_file_that_was_absent(tmp_hal0_home: str) -> None:
    """capabilities.toml absent before apply → revert removes it again."""
    _write_embed_slot(tmp_hal0_home)
    caps_path = _etc(tmp_hal0_home) / "capabilities.toml"
    assert not caps_path.exists()

    store = SlotConfigStore()
    cs = store.apply(_selection())
    store.commit(cs)
    assert caps_path.exists()

    store.revert(cs)
    assert not caps_path.exists()


def test_failed_mid_commit_leaves_disk_at_before(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drift invariant: a write failure mid-commit rolls the already-
    written files back — disk is never left half-reconciled."""
    import hal0.slot_config as slot_config_mod

    slot_path = _write_embed_slot(tmp_hal0_home)
    caps_path = _write_caps(tmp_hal0_home)
    slot_before = _read_toml(slot_path)
    caps_before = _read_toml(caps_path)

    store = SlotConfigStore()
    cs = store.apply(_selection())

    real_write = slot_config_mod.write_toml_atomic

    def _boom_on_slot(path: Path | str, data: dict[str, Any]) -> None:
        if Path(path).name == "embed.toml":
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(slot_config_mod, "write_toml_atomic", _boom_on_slot)
    with pytest.raises(OSError):
        store.commit(cs)
    monkeypatch.setattr(slot_config_mod, "write_toml_atomic", real_write)

    assert _read_toml(slot_path) == slot_before, "slot TOML must be at before"
    assert _read_toml(caps_path) == caps_before, "capabilities.toml must be rolled back"


def test_commit_then_reapply_is_noop(tmp_hal0_home: str) -> None:
    """Idempotence: once committed, re-applying the same selection yields a
    no-change ChangeSet."""
    _write_embed_slot(tmp_hal0_home)
    _write_caps(tmp_hal0_home)

    store = SlotConfigStore()
    first = store.apply(_selection())
    assert first.changed
    store.commit(first)

    second = store.apply(_selection())
    assert not second.changed
    assert [fs.data for fs in second.before] == [fs.data for fs in second.after]


# ── write_slot_toml (the single low-level write path) ────────────────────────


def test_write_slot_toml_is_atomic_and_parseable(tmp_hal0_home: str) -> None:
    path = _etc(tmp_hal0_home) / "slots" / "newslot.toml"
    write_slot_toml(path, {"name": "newslot", "port": 8090, "model": {"default": "m"}})
    data = _read_toml(path)
    assert data["name"] == "newslot"
    assert data["model"]["default"] == "m"
    # No tmpfile droppings left behind.
    leftovers = [p for p in path.parent.iterdir() if p.name != path.name]
    assert leftovers == []
