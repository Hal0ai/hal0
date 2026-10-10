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

import re
from pathlib import Path

import pytest

from hal0.config import paths
from hal0.install.brain_model import BRAIN_MODEL_DEFAULT
from hal0.install.static_seeds import STATIC_SEED_SLOTS, seed_static_slots

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _register_pulled(model_id: str) -> None:
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
        )
    )


def _bind(slot: str, model_id: str) -> None:
    """Stamp ``[model].default`` into a seeded slot, as install.sh's bind does."""
    path = paths.slots_config_dir() / f"{slot}.toml"
    text = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^\[model\]\s*$", text):
        text = re.sub(r"(?m)^\[model\]\s*$", f'[model]\ndefault = "{model_id}"', text, count=1)
    else:
        text += f'\n[model]\ndefault = "{model_id}"\n'
    path.write_text(text, encoding="utf-8")


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
    _bind("brain", BRAIN_MODEL_DEFAULT)

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
    _bind("brain", BRAIN_MODEL_DEFAULT)

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
    _bind("brain", BRAIN_MODEL_DEFAULT)
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
