"""Startup persona seeding — the lifespan hook that ships hal0-brain.

The dashboard's agent-chat slide-out embodies the ``hal0-brain`` persona
(routes/board_chat), but the only writer used to be provisioning's
checkpointed ``persona_seed`` phase — boxes provisioned before a seed
existed never grew its TOML on ``hal0 update``. The lifespan now seeds
missing defaults on every API start (overwrite=False), so the post-update
restart converges old boxes while operator edits survive untouched.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hal0.api import create_app


def _personas_root(tmp_hal0_home: str) -> Path:
    # Mirrors the lifespan's paths.var_lib()-based resolution under HAL0_HOME.
    return Path(tmp_hal0_home) / "var-lib" / "hal0" / ".hermes" / "personas"


def test_lifespan_seeds_default_personas(tmp_hal0_home: str) -> None:
    """A blank box grows both seeds + the active pointer at startup."""
    root = _personas_root(tmp_hal0_home)
    assert not root.exists()

    with TestClient(create_app()):
        pass

    assert {p.stem for p in root.glob("*.toml")} == {"hermes", "hal0-brain"}
    assert (root / "active.txt").read_text(encoding="utf-8").strip() == "hermes"


def test_lifespan_stamps_hal0_managed_marker_before_seed(tmp_hal0_home: str) -> None:
    """Fresh-install: the startup seed populates HERMES_HOME before
    `hal0 agent install hermes` runs. The lifespan must stamp `.hal0-managed` —
    since the adopt/foreign path's retirement (provision inc-2) the marker's
    one live contract is the agent manager's uninstall gate
    (`_safe_to_remove_data_dir`), which refuses to rmtree a converged home
    without it. A home hal0 itself seeded must stay hal0's to remove."""
    from hal0.agents import hermes_provision as hp

    home = _personas_root(tmp_hal0_home).parent  # .../.hermes
    assert not home.exists()

    with TestClient(create_app()):
        pass

    assert (home / hp._HAL0_MANAGED_MARKER).is_file()


def test_lifespan_seed_converges_old_box_without_touching_edits(
    tmp_hal0_home: str,
) -> None:
    """The upgrade case: an old box has hermes (operator-edited) + a
    pristine retired coder, no hal0-brain — startup adds the missing
    seed, sweeps the retired one, and leaves the edit alone."""
    import dataclasses

    from hal0.agents import personas as personas_mod

    root = _personas_root(tmp_hal0_home)
    root.mkdir(parents=True)
    edited = dataclasses.replace(
        personas_mod._seed_hermes("hermes"),
        system_prompt="operator-edited prompt",
    )
    personas_mod.save_persona(edited, root=root)
    personas_mod.save_persona(personas_mod._seed_coder("hermes"), root=root)
    personas_mod.set_active("hermes", root=root)

    with TestClient(create_app()):
        pass

    assert not (root / "coder.toml").exists()  # pristine retired seed swept
    brain = personas_mod.load_persona("hal0-brain", root=root)
    assert brain.memory_namespace == "private:hermes__hal0-brain"
    assert brain.preferred_model == "hal0/brain"
    hermes = personas_mod.load_persona("hermes", root=root)
    assert hermes.system_prompt == "operator-edited prompt"
