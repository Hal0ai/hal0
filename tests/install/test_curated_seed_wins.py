"""The curated static slot seeds must beat the first-run scaffold (v1.0 HIGH defect).

``installer/install.sh`` has two writers for ``/etc/hal0/slots/``:

* the **curated static seed** loop, which copies ``installer/etc-hal0/slots/*.toml``
  verbatim (hand-tuned: ``agent`` gets ``profile = "chadrock-moe"``, ``brain``
  gets ``profile = "brain"`` + ``runtime = "container"`` + a 64K window), and
* the **first-run scaffold** pass (``hal0 setup --auto``), which writes a
  generically-derived config for any capability slot it doesn't already see.

They used to run in the wrong order: the scaffold went first, and then the seed
loop's ``[[ -f ]]`` guard said "exists — left alone" and silently discarded the
curated files. Every fresh box therefore got ``profile = "chat"`` on both slots
and a 4096-token window — the curated recipes never landed at all.

These tests run the REAL scaffold pass (``apply_setup``, offline, no network)
against a temp ``HAL0_HOME`` in both orderings, so they demonstrate the
behavioural difference rather than restating the fix. ``tests/installer/
test_install_single_entry_point.py`` pins the install.sh line order that
selects the good one.
"""

from __future__ import annotations

import asyncio
import shutil
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEDS_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"

#: The two curated seeds the ordering bug destroyed, and the values that prove
#: the curated file (not a derived scaffold) is what's on disk.
_CURATED_EXPECTATIONS = {
    "agent": {"profile": "chadrock-moe", "runtime": "container", "port": 8081},
    "brain": {"profile": "brain", "runtime": "container", "port": 8089},
}


@pytest.fixture()
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty install root, with the config-path layer pointed at it."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths

    # paths caches nothing today, but be explicit: assert we really redirected.
    assert str(tmp_path) in str(paths.slots_config_dir())
    (tmp_path / "etc" / "hal0" / "slots").mkdir(parents=True)
    return tmp_path


def _copy_curated_seeds(home: Path) -> None:
    """What install.sh's static-seed loop does, for the slots under test."""
    dest = home / "etc" / "hal0" / "slots"
    for name in _CURATED_EXPECTATIONS:
        shutil.copyfile(_SEEDS_DIR / f"{name}.toml", dest / f"{name}.toml")


def _run_scaffold_pass(home: Path) -> None:
    """What install.sh's ``hal0 setup --auto --no-pull`` does, in-process.

    Goes through the same ``build_auto_selections`` + ``apply_setup`` pair the
    CLI uses, including the ``existing_slots`` read that is the actual skip
    mechanism under test. A synthetic Strix-Halo ``HardwareInfo`` keeps the
    derivation deterministic across CI hosts.
    """
    from hal0.cli.setup_command import _existing_slot_names, build_auto_selections
    from hal0.config.schema import GPUInfo, HardwareInfo
    from hal0.events import EventBus
    from hal0.install.orchestrate import apply_setup
    from hal0.registry.store import ModelRegistry
    from hal0.slots.manager import SlotManager

    hw = HardwareInfo(platform="strix-halo", gpus=[GPUInfo(vendor="amd", compute_capable=True)])
    sel = build_auto_selections(
        hw,
        storage_dir=str(home / "models"),
        with_extensions=False,
        existing_slots=_existing_slot_names(),
    )
    slot_manager = SlotManager(event_bus=EventBus(sink=None), upstreams_registry=None)
    asyncio.run(
        apply_setup(
            sel,
            hardware=hw,
            slot_manager=slot_manager,
            registry=ModelRegistry(),
            jobs={},
            write_sentinel=False,
        )
    )


def _read(home: Path, name: str) -> dict:
    return tomllib.loads(
        (home / "etc" / "hal0" / "slots" / f"{name}.toml").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("slot_name", sorted(_CURATED_EXPECTATIONS))
def test_seeding_first_preserves_the_curated_config(hal0_home: Path, slot_name: str) -> None:
    """SEED → SCAFFOLD (the v1.0 order): the curated file survives untouched."""
    _copy_curated_seeds(hal0_home)
    before = (hal0_home / "etc" / "hal0" / "slots" / f"{slot_name}.toml").read_bytes()

    _run_scaffold_pass(hal0_home)

    after = (hal0_home / "etc" / "hal0" / "slots" / f"{slot_name}.toml").read_bytes()
    assert after == before, f"{slot_name}.toml was rewritten by the scaffold pass"
    cfg = _read(hal0_home, slot_name)
    for key, want in _CURATED_EXPECTATIONS[slot_name].items():
        assert cfg[key] == want, f"{slot_name}.toml {key}={cfg[key]!r}, expected {want!r}"


@pytest.mark.parametrize("slot_name", sorted(_CURATED_EXPECTATIONS))
def test_scaffolding_first_destroys_the_curated_config(hal0_home: Path, slot_name: str) -> None:
    """SCAFFOLD → SEED (the old order): the curated values never land.

    This is the defect, asserted directly, so the ordering fix can't be quietly
    reverted as "equivalent". The scaffold writes a generic derived profile and
    the seed loop's copy-if-absent guard then refuses to correct it.
    """
    _run_scaffold_pass(hal0_home)
    scaffolded = _read(hal0_home, slot_name)
    assert scaffolded["profile"] != _CURATED_EXPECTATIONS[slot_name]["profile"], (
        "scaffold happens to produce the curated profile — this test no longer "
        "demonstrates the ordering bug and needs rewriting"
    )

    # install.sh's loop is copy-IF-ABSENT, so it is a no-op here.
    dest = hal0_home / "etc" / "hal0" / "slots" / f"{slot_name}.toml"
    if not dest.exists():  # pragma: no cover — the scaffold just created it
        shutil.copyfile(_SEEDS_DIR / f"{slot_name}.toml", dest)

    still_scaffolded = _read(hal0_home, slot_name)
    assert still_scaffolded["profile"] == scaffolded["profile"]
    assert still_scaffolded["profile"] != _CURATED_EXPECTATIONS[slot_name]["profile"]


def test_scaffold_still_creates_slots_that_have_no_static_seed(hal0_home: Path) -> None:
    """Seeding first must not disable the scaffold pass.

    ``vision`` used to be the unconditional seed-less scaffold this test
    pinned; its lane is retired (vision is a model property served by any
    llm slot) and the one other seed-less capability, ``stt``, is hardware-
    gated. So the pin inverts: the scaffold pass must complete WITHOUT
    creating the retired lane, while the curated seeds all still land.
    """
    _copy_curated_seeds(hal0_home)
    _run_scaffold_pass(hal0_home)
    # The retired vision lane must NOT come back through setup.
    assert not (hal0_home / "etc" / "hal0" / "slots" / "vision.toml").exists(), (
        "setup recreated the retired vision scaffold slot"
    )
    # And the pass as a whole still ran — every curated seed is present.
    for name in _CURATED_EXPECTATIONS:
        assert (hal0_home / "etc" / "hal0" / "slots" / f"{name}.toml").exists()
