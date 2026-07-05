"""MTP force-on defuse — swap path + updater migration.

A slot's explicit ``mtp = true`` is the escape hatch for MTP-capable models
the eligibility heuristics miss — but pointed at a model with NO MTP heads it
makes llama-server exit at load ("context type MTP requested but model doesn't
contain MTP layers"). Two mechanisms clear exactly that crash-only combination
(and nothing else):

  - :meth:`SlotManager._defuse_stale_mtp_on_swap` — on model swap, so the
    staleness can't regenerate (the override is model-scoped debris once the
    model changes).
  - :func:`hal0.updater.updater.clear_stale_mtp_overrides` — upgrade
    migration for overrides already on disk (pre-separation binary-pill /
    stack-apply leftovers masked for months by stale baked units).
"""

from __future__ import annotations

import tomllib

from hal0.config.paths import slots_config_dir
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager
from hal0.updater.updater import clear_stale_mtp_overrides


def _register(model_id: str, *, tags: list[str] | None = None) -> None:
    ModelRegistry().add(
        Model(id=model_id, path=f"/tmp/{model_id}.gguf", capabilities=["chat"], tags=tags or [])
    )


def _slot_cfg(name: str, model: str, *, mtp: bool | None) -> dict:
    cfg: dict = {
        "name": name,
        "port": 8093,
        "type": "llm",
        "device": "gpu-vulkan",
        "profile": "vulkan",
        "provider": "llama-server",
        "enabled": True,
        "group": "custom",
        "model": {"default": model},
    }
    if mtp is not None:
        cfg["mtp"] = mtp
    return cfg


def _on_disk_mtp(name: str):
    raw = tomllib.loads((slots_config_dir() / f"{name}.toml").read_text(encoding="utf-8"))
    slot = raw.get("slot", raw)
    return slot.get("mtp", raw.get("mtp"))


# ── swap-path defuse ──────────────────────────────────────────────────────────


async def test_swap_defuse_clears_force_on_for_ineligible_model(tmp_hal0_home: str) -> None:
    _register("plain-chat")
    sm = SlotManager()
    await sm.create("s", _slot_cfg("s", "old-model", mtp=True))
    assert await sm._defuse_stale_mtp_on_swap("s", "plain-chat") is True
    assert _on_disk_mtp("s") is None  # absent = AUTO


async def test_swap_defuse_keeps_force_on_for_eligible_model(tmp_hal0_home: str) -> None:
    _register("tagged-model", tags=["mtp"])
    sm = SlotManager()
    await sm.create("s", _slot_cfg("s", "old-model", mtp=True))
    assert await sm._defuse_stale_mtp_on_swap("s", "tagged-model") is False
    assert _on_disk_mtp("s") is True


async def test_swap_defuse_keeps_force_off_and_auto(tmp_hal0_home: str) -> None:
    _register("plain-chat")
    sm = SlotManager()
    await sm.create("off", _slot_cfg("off", "old-model", mtp=False))
    await sm.create("auto", _slot_cfg("auto", "old-model", mtp=None))
    assert await sm._defuse_stale_mtp_on_swap("off", "plain-chat") is False
    assert await sm._defuse_stale_mtp_on_swap("auto", "plain-chat") is False
    assert _on_disk_mtp("off") is False
    assert _on_disk_mtp("auto") is None


async def test_swap_defuse_leaves_unresolvable_model_alone(tmp_hal0_home: str) -> None:
    # Escape hatch preserved: if the registry can't judge the model, don't touch.
    sm = SlotManager()
    await sm.create("s", _slot_cfg("s", "old-model", mtp=True))
    assert await sm._defuse_stale_mtp_on_swap("s", "not-in-registry") is False
    assert _on_disk_mtp("s") is True


# ── updater migration ─────────────────────────────────────────────────────────


async def test_migration_clears_only_crash_combo(tmp_hal0_home: str) -> None:
    _register("plain-chat")
    _register("tagged-model", tags=["mtp"])
    sm = SlotManager()
    # crash combo: force-on + ineligible model → cleared
    await sm.create("crash", _slot_cfg("crash", "plain-chat", mtp=True))
    # deliberate force-on for an eligible model → kept
    await sm.create("keep", _slot_cfg("keep", "tagged-model", mtp=True))
    # force-off → harmless, kept
    await sm.create("off", _slot_cfg("off", "plain-chat", mtp=False))
    # unresolvable model → can't judge, kept
    await sm.create("unknown", _slot_cfg("unknown", "ghost-model", mtp=True))

    assert clear_stale_mtp_overrides() == 1
    assert _on_disk_mtp("crash") is None
    assert _on_disk_mtp("keep") is True
    assert _on_disk_mtp("off") is False
    assert _on_disk_mtp("unknown") is True


async def test_migration_is_idempotent(tmp_hal0_home: str) -> None:
    _register("plain-chat")
    sm = SlotManager()
    await sm.create("s", _slot_cfg("s", "plain-chat", mtp=True))
    assert clear_stale_mtp_overrides() == 1
    assert clear_stale_mtp_overrides() == 0
