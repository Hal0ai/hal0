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


def test_migration_sequence_runs_engine_pass_by_default_but_not_at_boot() -> None:
    """The memory-engine venv convergence pass must ride the shared sequence
    on both real upgrade paths (default kwarg — install.sh passes none) while
    the boot-time safety net opts out: a boot-time pip is an unbounded
    network op and the first start of a newer engine triggers its one-way DB
    migration, neither of which belongs outside an operator-visible update
    (the ``skip_image_retag``/``repair_hermes_venv`` posture, one step
    further).

    Since the 2026-08-30 component-updates spec, the engine pass rides in
    via the component catalog (:func:`hal0.components.runner.converge_components`,
    ``engine=upgrade_memory_engine_venv``) rather than a direct
    ``upgrade_memory_engine(...)`` call inline in the sequence — the
    catalog also covers openwebui/runner-images/hermes convergence in one
    best-effort pass. ``updater.memory_engine_upgrade_failed`` is now
    logged from inside :func:`hal0.memory.engine_upgrade.upgrade_memory_engine`
    itself, not from ``run_post_activation_migrations``, but stays in
    :data:`updater._NON_FATAL_MIGRATION_FAILURE_EVENTS` for the stderr-relay
    scan; the pass's own genuine-bug isolation now logs the new
    ``updater.components_converge_failed`` event instead.
    """
    import inspect

    from hal0.updater import updater

    seq = inspect.signature(updater.run_post_activation_migrations)
    assert seq.parameters["upgrade_memory_engine_venv"].default is True

    boot_src = inspect.getsource(updater.check_outstanding_migrations)
    assert "upgrade_memory_engine_venv=False" in boot_src
    assert "converge_companions=False" in boot_src

    seq_src = inspect.getsource(updater.run_post_activation_migrations)
    assert "converge_components(" in seq_src
    assert "engine=upgrade_memory_engine_venv" in seq_src
    assert "updater.components_converge_failed" in seq_src
    assert "updater.components_converge_failed" in updater._NON_FATAL_MIGRATION_FAILURE_EVENTS, (
        "a swallowed component-convergence failure must surface in commit()'s convergence report"
    )
    assert "updater.memory_engine_upgrade_failed" in updater._NON_FATAL_MIGRATION_FAILURE_EVENTS, (
        "a swallowed engine-pass failure must surface in commit()'s convergence report"
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
