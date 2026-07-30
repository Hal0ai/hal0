"""GH #1475: install.sh's repair/upgrade-in-place path must run the SAME
post-activation migration sequence ``Updater.commit()`` (self-update) runs.

Before this, install.sh's venv-python block called only two of the five
passes commit() runs (``ensure_seed_profiles``, ``clear_stale_mtp_overrides``)
directly — a box upgraded by re-running install.sh kept a stale
``meta.schema_version``, stale runner-image pins, and unsanitised
``defaults.extra_args`` that ``hal0 update`` would have fixed. Static text
checks (no systemd/podman harness exists for install.sh — see
test_seed_order.py for the same pattern).
"""

from __future__ import annotations

from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def test_install_sh_calls_the_shared_migration_sequence() -> None:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    assert "from hal0.updater.updater import run_post_activation_migrations" in text, (
        "install.sh no longer calls the shared run_post_activation_migrations() "
        "sequence — it must run the same 5 post-activation passes "
        "Updater.commit() runs, not a hand-picked subset."
    )


def test_install_sh_no_longer_hand_picks_a_migration_subset() -> None:
    """The old two-heredoc block imported ensure_seed_profiles and
    clear_stale_mtp_overrides directly, skipping _maybe_run_config_migrations,
    retag_stale_slot_images, and sanitize_model_extra_args entirely. Once
    the shared sequence is wired in, those two passes should only be
    reachable through it (no standalone re-introduction of the old subset)."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    assert "from hal0.updater.updater import ensure_seed_profiles" not in text
    assert "from hal0.updater.updater import clear_stale_mtp_overrides" not in text
