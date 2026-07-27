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
from hal0.registry.model import Model, ModelDefaults
from hal0.registry.store import ModelRegistry
from hal0.slot_config import write_slot_toml
from hal0.slots.manager import SlotManager
from hal0.updater.updater import clear_stale_mtp_overrides


def _register(model_id: str, *, tags: list[str] | None = None, mtp: bool | None = None) -> None:
    ModelRegistry().add(
        Model(
            id=model_id,
            path=f"/tmp/{model_id}.gguf",
            capabilities=["chat"],
            tags=tags or [],
            defaults=ModelDefaults(mtp=mtp) if mtp is not None else None,
        )
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


async def _seed_pre_split_slot(sm: SlotManager, name: str, model: str, *, mtp: bool | None):
    """Create a slot carrying a slot-owned ``mtp`` the way a pre-split box has one.

    ``SlotManager.create`` now refuses MODEL-owned keys at the write seam
    (spec-hw-slot-ownership §1), so these fixtures can no longer be BORN
    through the API — which is exactly the point. The defuse paths exist to
    clean slots that predate that guard, so the fixture has to reproduce that
    on-disk state rather than mint it through a path that (correctly) forbids
    it. Create through the real path, then stamp the key onto the TOML the way
    an older hal0 left it.
    """
    await sm.create(name, _slot_cfg(name, model, mtp=None))
    if mtp is None:
        return
    path = slots_config_dir() / f"{name}.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    raw["mtp"] = mtp
    write_slot_toml(path, raw)
    sm._invalidate_cfg_cache(name)


def _on_disk_mtp(name: str):
    raw = tomllib.loads((slots_config_dir() / f"{name}.toml").read_text(encoding="utf-8"))
    slot = raw.get("slot", raw)
    return slot.get("mtp", raw.get("mtp"))


# ── swap-path defuse ──────────────────────────────────────────────────────────


async def test_swap_defuse_clears_force_on_for_ineligible_model(tmp_hal0_home: str) -> None:
    _register("plain-chat")
    sm = SlotManager()
    await _seed_pre_split_slot(sm, "s", "old-model", mtp=True)
    assert await sm._defuse_stale_mtp_on_swap("s", "plain-chat") is True
    assert _on_disk_mtp("s") is None  # absent = AUTO


async def test_swap_defuse_keeps_force_on_for_eligible_model(tmp_hal0_home: str) -> None:
    _register("tagged-model", tags=["mtp"])
    sm = SlotManager()
    await _seed_pre_split_slot(sm, "s", "old-model", mtp=True)
    assert await sm._defuse_stale_mtp_on_swap("s", "tagged-model") is False
    assert _on_disk_mtp("s") is True


async def test_swap_defuse_keeps_force_off_and_auto(tmp_hal0_home: str) -> None:
    _register("plain-chat")
    sm = SlotManager()
    await _seed_pre_split_slot(sm, "off", "old-model", mtp=False)
    await _seed_pre_split_slot(sm, "auto", "old-model", mtp=None)
    assert await sm._defuse_stale_mtp_on_swap("off", "plain-chat") is False
    assert await sm._defuse_stale_mtp_on_swap("auto", "plain-chat") is False
    assert _on_disk_mtp("off") is False
    assert _on_disk_mtp("auto") is None


async def test_swap_defuse_leaves_unresolvable_model_alone(tmp_hal0_home: str) -> None:
    # Escape hatch preserved: if the registry can't judge the model, don't touch.
    sm = SlotManager()
    await _seed_pre_split_slot(sm, "s", "old-model", mtp=True)
    assert await sm._defuse_stale_mtp_on_swap("s", "not-in-registry") is False
    assert _on_disk_mtp("s") is True


# ── §7.1a / ML-5: explicit defaults.mtp tri-state wins over the tag ───────────


async def test_swap_defuse_keeps_force_on_for_defaults_mtp_true_even_untagged(
    tmp_hal0_home: str,
) -> None:
    """An explicit ModelDefaults.mtp=True is eligible even with NO registry
    tag — the tri-state override wins over "no tag" the same way it would
    win over an eligible tag."""
    _register("explicit-mtp-model", mtp=True)
    sm = SlotManager()
    await _seed_pre_split_slot(sm, "s", "old-model", mtp=True)
    assert await sm._defuse_stale_mtp_on_swap("s", "explicit-mtp-model") is False
    assert _on_disk_mtp("s") is True


async def test_swap_defuse_clears_force_on_for_defaults_mtp_false_even_tagged(
    tmp_hal0_home: str,
) -> None:
    """An explicit ModelDefaults.mtp=False makes the model ineligible even
    though it carries the registry 'mtp' tag — the explicit override wins
    in EITHER direction."""
    _register("suppressed-mtp-model", tags=["mtp"], mtp=False)
    sm = SlotManager()
    await _seed_pre_split_slot(sm, "s", "old-model", mtp=True)
    assert await sm._defuse_stale_mtp_on_swap("s", "suppressed-mtp-model") is True
    assert _on_disk_mtp("s") is None


# ── updater migration ─────────────────────────────────────────────────────────


async def test_migration_clears_only_crash_combo(tmp_hal0_home: str) -> None:
    _register("plain-chat")
    _register("tagged-model", tags=["mtp"])
    sm = SlotManager()
    # crash combo: force-on + ineligible model → cleared
    await _seed_pre_split_slot(sm, "crash", "plain-chat", mtp=True)
    # deliberate force-on for an eligible model → kept
    await _seed_pre_split_slot(sm, "keep", "tagged-model", mtp=True)
    # force-off → harmless, kept
    await _seed_pre_split_slot(sm, "off", "plain-chat", mtp=False)
    # unresolvable model → can't judge, kept
    await _seed_pre_split_slot(sm, "unknown", "ghost-model", mtp=True)

    assert clear_stale_mtp_overrides() == 1
    assert _on_disk_mtp("crash") is None
    assert _on_disk_mtp("keep") is True
    assert _on_disk_mtp("off") is False
    assert _on_disk_mtp("unknown") is True


async def test_migration_is_idempotent(tmp_hal0_home: str) -> None:
    _register("plain-chat")
    sm = SlotManager()
    await _seed_pre_split_slot(sm, "s", "plain-chat", mtp=True)
    assert clear_stale_mtp_overrides() == 1
    assert clear_stale_mtp_overrides() == 0
