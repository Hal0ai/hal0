"""#2476: a fresh install's own seed set must not read as a pre-v1.0 box.

install.sh seeds ``installer/etc-hal0/slots/*.toml`` and then binds the brain
slot to the model it just pulled. That pull registers the model with no tune
text and no profile provenance, so the brain slot launches on its profile's
flags through the ``slot_profile_template`` segment
(:func:`hal0.providers.container._resolve_llama_scalars`, #1787). The
``migrate-flags`` planner used to count that as an outstanding fold, so every
fresh install warned "pre-v1.0 slot/model shape" and the first ``hal0 update``
exited 2 (#2096). These tests run the real seed copy and the real dry-run
probe the updater and install.sh print.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hal0.config import paths
from hal0.install.brain_model import BRAIN_MODEL_DEFAULT
from hal0.install.static_seeds import STATIC_SEED_SLOTS, seed_static_slots

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _register_pulled(model_id: str, *, specialty: str | None = None) -> None:
    """Register *model_id* the way a pull does: no tune text, no provenance."""
    from hal0.registry.model import Model, ModelDefaults
    from hal0.registry.store import ModelRegistry

    ModelRegistry().add(
        Model(
            id=model_id,
            name=model_id,
            path=f"/models/{model_id}.gguf",
            hf_repo=f"example/{model_id}",
            capabilities=["chat"],
            defaults=ModelDefaults(tokenizer_repo=f"example/{model_id}"),
            metadata={"specialty": specialty} if specialty else {},
        )
    )


def _slot_manager():
    """The offline SlotManager install's brain-model step builds."""
    from hal0.cli.setup_command import _build_offline_deps

    return _build_offline_deps()[0]


def _bind_brain(model_id: str = BRAIN_MODEL_DEFAULT) -> None:
    """Bind brain through install's real ``bind_brain_model`` path."""
    from hal0.install.brain_model import bind_brain_model

    assert asyncio.run(bind_brain_model(_slot_manager(), model_id)) == model_id


def _bind(slot: str, model_id: str) -> None:
    """Bind any seeded slot through the same ``SlotManager.update_config`` write."""
    asyncio.run(_slot_manager().update_config(slot, {"model": {"default": model_id}}))


def _seed() -> list[str]:
    from hal0.config.schema import HardwareInfo

    return seed_static_slots(
        installer_root=_REPO_ROOT,
        slots_dir=paths.slots_config_dir(),
        hw=HardwareInfo(),
    )


def _pending_flag_lines() -> list[str]:
    from hal0.config.migrations.slot_flags_fold import run_migration

    # Same filter as updater.detect_pending_ownership_migrations: "skip " lines
    # are converged, every other line is outstanding work.
    return [line for line in run_migration(dry_run=True) if not line.startswith("skip ")]


def test_fresh_seed_set_with_brain_bound_has_no_pending_flag_fold(tmp_hal0_home: str) -> None:
    """The exact fresh-install state: seeds copied, brain bound to its pull."""
    seeded = _seed()
    assert set(seeded) == set(STATIC_SEED_SLOTS)
    _register_pulled(BRAIN_MODEL_DEFAULT)
    _bind_brain()

    assert _pending_flag_lines() == []


def test_every_seeded_slot_bound_to_a_pulled_model_has_no_pending_flag_fold(
    tmp_hal0_home: str,
) -> None:
    """Every seed, not just brain, ships in a shape the planner calls v1.0."""
    _seed()
    for name in STATIC_SEED_SLOTS:
        model_id = f"pulled-{name}"
        _register_pulled(model_id)
        _bind(name, model_id)

    assert _pending_flag_lines() == []


def test_fresh_seed_set_reports_nothing_pending_to_the_updater(tmp_hal0_home: str) -> None:
    from hal0.updater.updater import detect_pending_ownership_migrations

    _seed()
    _register_pulled(BRAIN_MODEL_DEFAULT)
    _bind_brain()

    assert "flags" not in detect_pending_ownership_migrations()["pending"]


@pytest.mark.parametrize(
    ("table", "line"),
    [
        ("[server]", 'extra_args = "-fa on --mlock"'),
        ("", "parallel = 4"),
    ],
)
def test_legacy_slot_tune_on_a_seeded_slot_is_still_pending(
    tmp_hal0_home: str, table: str, line: str
) -> None:
    """A slot that still carries its own launch tune is genuinely pre-v1.0."""
    _seed()
    _register_pulled(BRAIN_MODEL_DEFAULT)
    _bind_brain()
    path = paths.slots_config_dir() / "brain.toml"
    text = path.read_text(encoding="utf-8")
    if table:
        text += f"\n{table}\n{line}\n"
    else:
        text = text.replace('name = "brain"\n', f'name = "brain"\n{line}\n', 1)
    path.write_text(text, encoding="utf-8")

    pending = _pending_flag_lines()
    assert len(pending) == 1
    assert pending[0].startswith(f"would fold '{BRAIN_MODEL_DEFAULT}' <- profile='brain'")


# ── the template gate's other three conditions (review of #2484) ─────────────


def _set_brain_profile(name: str) -> None:
    path = paths.slots_config_dir() / "brain.toml"
    text = path.read_text(encoding="utf-8").replace('profile = "brain"', f'profile = "{name}"', 1)
    path.write_text(text, encoding="utf-8")


def test_profile_that_does_not_fit_the_slot_is_still_pending(tmp_hal0_home: str) -> None:
    """Launch drops a profile that misfits the slot, so its tune is not read.

    An operator profile with a runner-less ``vulkan`` backend hint on the brain
    slot's ``gpu-rocm`` lane is vetoed by ``profile_fits_slot``. Launch emits no
    template; folding is the only way the tune survives, so it stays pending.
    """
    from hal0.slots.profile_adopt import profile_fits_slot

    _seed()
    paths.profiles_toml().parent.mkdir(parents=True, exist_ok=True)
    paths.profiles_toml().write_text(
        '[profile.legacy]\nflags = "-fa on -b 4096 -ub 1024 --temp 0.6"\n'
        'backend = "vulkan"\nmtp = false\n',
        encoding="utf-8",
    )
    _register_pulled(BRAIN_MODEL_DEFAULT)
    _bind_brain()
    _set_brain_profile("legacy")
    from hal0.config.loader import load_slot_config

    assert not profile_fits_slot("legacy", load_slot_config("brain").model_dump(by_alias=True))

    pending = _pending_flag_lines()
    assert len(pending) == 1
    assert pending[0].startswith(f"would fold '{BRAIN_MODEL_DEFAULT}' <- profile='legacy'")


def test_specialty_model_is_still_planned(tmp_hal0_home: str) -> None:
    """A specialty launch may be degraded, which suppresses the template; the
    probe cannot tell without the runner, so it plans the fold as before."""
    _seed()
    _register_pulled(BRAIN_MODEL_DEFAULT, specialty="promptforge")
    _bind_brain()

    pending = _pending_flag_lines()
    assert len(pending) == 1
    assert pending[0].startswith(f"would fold '{BRAIN_MODEL_DEFAULT}' <- profile='brain'")


def test_profile_missing_from_the_catalog_is_not_reported_as_template(
    tmp_hal0_home: str,
) -> None:
    """A profile that does not resolve never reaches launch. The fold has no
    profile flags to add, so the model is a plain no-op skip, not a template
    skip."""
    from hal0.config.migrations.slot_flags_fold import run_migration

    _seed()
    _register_pulled(BRAIN_MODEL_DEFAULT)
    _bind_brain()
    _set_brain_profile("no-such-profile")

    lines = run_migration(dry_run=True)
    assert lines == [f"skip model '{BRAIN_MODEL_DEFAULT}': already folded (no-op)"]


def test_the_dry_run_never_writes_profiles_toml(tmp_hal0_home: str) -> None:
    """The template classifier resolves from the loaded catalog only."""
    _seed()
    _register_pulled(BRAIN_MODEL_DEFAULT)
    _bind_brain()
    assert not paths.profiles_toml().exists()

    _pending_flag_lines()

    assert not paths.profiles_toml().exists()
