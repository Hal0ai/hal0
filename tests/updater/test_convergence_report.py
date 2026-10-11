"""Convergence detection — what an operator is told after an upgrade.

Two pieces, both of which have to be right or the update lies about the box:

  * ``detect_pending_ownership_migrations`` — write-free planners for the three
    deploy-window folds. False positives are as bad as false negatives here: a
    converged box that keeps reporting "pending" trains operators to ignore it.
  * ``convergence_report`` — the aggregate both ``hal0 update`` and install.sh
    print, so the two entry points cannot disagree.
"""

from __future__ import annotations

import pytest

from hal0.config.paths import slots_config_dir
from hal0.updater import updater as U


def _write_slot(name: str, body: str) -> None:
    d = slots_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(body, encoding="utf-8")


def _write_id_keyed_slot(slot_id: int, name: str, body: str = "") -> None:
    """The post-``migrate_slot_id_keying`` layout: ``<id>.toml`` with ``id =``.

    CT150 is name-keyed, so nothing about an id-keyed box reproduces there. Every
    convergence claim has to hold on both layouts or it is a claim about one box.
    """
    d = slots_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slot_id}.toml").write_text(f'id = {slot_id}\nname = "{name}"\n{body}', encoding="utf-8")


# ── ownership-fold detection ──────────────────────────────────────────────────


def test_converged_box_reports_nothing_pending(tmp_hal0_home: str) -> None:
    """An empty config tree has nothing to fold — the baseline must be quiet."""
    report = U.detect_pending_ownership_migrations()
    assert report["pending"] == []
    assert report["commands"] == []
    assert set(report["detail"]) == {"flags", "caps", "hw"}


def test_flags_fold_noop_skips_are_not_counted_as_pending(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """slot_flags_fold reports its no-op skips as "skip model ..." lines.

    Counting those as outstanding work would make `hal0 update` exit 2 forever
    on a fully converged box.
    """
    import hal0.config.migrations.slot_flags_fold as m

    monkeypatch.setattr(
        m,
        "run_migration",
        lambda **kw: ["skip model 'a': already folded", "skip model 'b': nothing to fold"],
    )
    report = U.detect_pending_ownership_migrations()
    assert "flags" not in report["pending"]
    assert report["detail"]["flags"]["lines"] == []


def test_flags_fold_provider_lane_registry_miss_is_not_pending(tmp_hal0_home: str) -> None:
    """#2324: a provider-lane slot (here the type=tts Kokoro fallback) launches
    without a registry row and never reads the folded tune, so its miss is not
    flags work and must not keep `hal0 update` at exit 2."""
    _write_slot(
        "tts",
        'name = "tts"\ntype = "tts"\nport = 8095\n\n[model]\ndefault = "unregistered-tts"\n\n'
        '[server]\nextra_args = "--default_voice Ryan"\n',
    )
    from hal0.config.migrations.slot_flags_fold import run_migration

    # The planner sees the binding and names it (guards against a vacuous pass).
    assert run_migration(dry_run=True) == [
        "skip model 'unregistered-tts' <- slots=['tts']: provider-lane, no registry row"
    ]
    report = U.detect_pending_ownership_migrations()
    assert "flags" not in report["pending"]
    assert report["detail"]["flags"]["error"] is None


def test_flags_fold_ordinary_registry_miss_stays_pending(tmp_hal0_home: str) -> None:
    """A llama-server slot bound to an unregistered model still has an unfolded
    tune; the remedy (register or rebind, then migrate-flags) must stay shown."""
    _write_slot(
        "chat",
        'name = "chat"\ntype = "llm"\nport = 8081\n\n[model]\ndefault = "ghost"\n\n'
        '[server]\nextra_args = "-b 2048"\n',
    )
    report = U.detect_pending_ownership_migrations()
    assert "flags" in report["pending"]
    assert report["detail"]["flags"]["lines"] == [
        "SKIP model 'ghost' <- slots=['chat']: not in registry"
    ]


def test_real_fold_work_is_reported_with_its_command(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hal0.config.migrations.hw_slot_ownership as m

    monkeypatch.setattr(
        m, "run_migration", lambda **kw: ["would fold slot 'chat': ngl=99 binary='rocmfpx'"]
    )
    report = U.detect_pending_ownership_migrations()
    assert report["pending"] == ["hw"]
    assert report["commands"] == ["hal0 slot migrate-hw --apply"]
    assert report["detail"]["hw"]["lines"][0].startswith("would fold slot 'chat'")


def test_pending_command_gains_stop_services_when_units_are_up(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1845/H8: a bare `--apply` printed while hal0 units are live fails —
    the migrate-* commands all refuse without `--stop-services` in that state
    (cli/slot_commands.py). The server is the one place that knows whether
    units are up, so it must be the one to decide the flag, not the CLI panel
    or the dashboard reprinting a stale static string.
    """
    import hal0.cli.slot_commands as slot_commands
    import hal0.config.migrations.hw_slot_ownership as m

    monkeypatch.setattr(
        m, "run_migration", lambda **kw: ["would fold slot 'chat': ngl=99 binary='rocmfpx'"]
    )
    monkeypatch.setattr(slot_commands, "active_hal0_units", lambda: ["hal0-api.service"])
    report = U.detect_pending_ownership_migrations()
    assert report["commands"] == ["hal0 slot migrate-hw --apply --stop-services"]
    assert report["detail"]["hw"]["command"] == "hal0 slot migrate-hw --apply --stop-services"


def test_pending_command_omits_stop_services_when_units_are_down(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case: no live units, so the plain `--apply` command is correct
    and must not carry a flag the operator doesn't need."""
    import hal0.cli.slot_commands as slot_commands
    import hal0.config.migrations.hw_slot_ownership as m

    monkeypatch.setattr(
        m, "run_migration", lambda **kw: ["would fold slot 'chat': ngl=99 binary='rocmfpx'"]
    )
    monkeypatch.setattr(slot_commands, "active_hal0_units", lambda: [])
    report = U.detect_pending_ownership_migrations()
    assert report["commands"] == ["hal0 slot migrate-hw --apply"]


def test_divergent_share_refusal_is_surfaced_not_swallowed(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """slot_flags_fold raises RuntimeError on divergent shares even in dry-run.

    That is the single most important thing to tell an operator: the migration
    cannot be applied at all until they resolve the conflict by hand.
    """
    import hal0.config.migrations.slot_flags_fold as m

    def _boom(**kw: object) -> list[str]:
        raise RuntimeError("slot_flags_fold refuses 1 model(s) with divergent slot overrides")

    monkeypatch.setattr(m, "run_migration", _boom)
    report = U.detect_pending_ownership_migrations()
    assert report["pending"] == ["flags"]
    assert "divergent" in report["detail"]["flags"]["error"]


def test_detection_never_writes(tmp_hal0_home: str) -> None:
    """The planners must be side-effect free — they run on a live box."""
    _write_slot("chat", 'name = "chat"\nmtp = true\n\n[model]\ndefault = "qwen3-4b"\n')
    before = (slots_config_dir() / "chat.toml").read_bytes()
    U.detect_pending_ownership_migrations()
    assert (slots_config_dir() / "chat.toml").read_bytes() == before


# ── the aggregate ─────────────────────────────────────────────────────────────


def test_report_is_converged_with_nothing_pending(tmp_hal0_home: str) -> None:
    hal0_toml = U.paths.hal0_toml()
    hal0_toml.parent.mkdir(parents=True, exist_ok=True)
    hal0_toml.write_text("[meta]\nschema_version = 2\n", encoding="utf-8")

    report = U.convergence_report()
    assert report["ownership_migrations"]["pending"] == []
    assert report["converged"] is True


# ── id-keyed boxes (#1421 / #1422) ────────────────────────────────────────────
#
# Every test above this line uses the NAME-keyed layout, which is what CT150
# happens to run. An id-keyed box (`1.toml` carrying `id = 1`) is a different
# on-disk shape, and #1421 turns the difference into live-config destruction:
# `POST /api/slots` and install.sh's static seed loop both write name-keyed
# artefacts onto an id-keyed box, and `migrate_slot_id_keying` then rewrites
# `<id>.toml` from the stale `<name>.toml` it finds later in glob order.
#
# So: convergence must be exercised against BOTH layouts, and the id-keying
# migration must stay off the auto-run path.


def test_detection_never_writes_on_an_id_keyed_box(tmp_hal0_home: str) -> None:
    _write_id_keyed_slot(1, "agent", 'mtp = true\n\n[model]\ndefault = "qwen3-4b"\n')
    before = (slots_config_dir() / "1.toml").read_bytes()
    U.detect_pending_ownership_migrations()
    assert (slots_config_dir() / "1.toml").read_bytes() == before


def test_report_is_converged_on_a_stamped_id_keyed_box(tmp_hal0_home: str) -> None:
    hal0_toml = U.paths.hal0_toml()
    hal0_toml.parent.mkdir(parents=True, exist_ok=True)
    hal0_toml.write_text("[meta]\nschema_version = 2\n", encoding="utf-8")
    _write_id_keyed_slot(1, "agent", '\n[model]\ndefault = "qwen3-4b"\n')
    _write_id_keyed_slot(2, "brain", '\n[model]\ndefault = "hal0-brain"\n')

    report = U.convergence_report()
    assert report["ownership_migrations"]["pending"] == []
    assert report["converged"] is True


# ── the id-keying migration must never be auto-run (#1421) ────────────────────


def test_id_keying_is_not_one_of_the_detected_ownership_folds() -> None:
    """``migrate_slot_id_keying`` is NOT a convergence step this code may drive.

    Per #1421 it iterates ``sorted(config_dir.glob("*.toml"))``, skips
    ``<id>.toml`` as already-migrated, then reaches a stale ``<name>.toml`` for
    the same slot and does ``write_slot_toml(config_dir / f"{slot_id}.toml",
    raw)`` — clobbering the LIVE id-keyed config with seed-shaped content.
    Chained with install.sh's name-keyed seed guard (which does not fire on an
    id-keyed box and re-seeds all ten curated names), auto-running it on update
    is a live-config-destroying change, not merely an irreversible one.

    It stays behind ``hal0 slot migrate-id-keying``, where an operator stops the
    services and reads a dry-run plan first.
    """
    modules = [module for _key, module, _cmd in U._OWNERSHIP_MIGRATIONS]
    assert not any("id_keying" in m for m in modules), modules
    assert modules == [
        "hal0.config.migrations.slot_flags_fold",
        "hal0.config.migrations.model_owned_caps",
        "hal0.config.migrations.hw_slot_ownership",
    ]


def test_no_auto_run_path_reaches_the_id_keying_migrator() -> None:
    """Neither the updater nor install.sh may import or call it.

    Checked against *code*, not prose — both files discuss the migration at
    length in comments, and that documentation is the point.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    tree = ast.parse((root / "src/hal0/updater/updater.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "migrate_id_keying" not in (node.module or ""), ast.dump(node)
            assert all("id_keying" not in a.name for a in node.names), ast.dump(node)
        if isinstance(node, ast.Import):
            assert all("migrate_id_keying" not in a.name for a in node.names), ast.dump(node)
        if isinstance(node, ast.Call):
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            assert called != "migrate_slot_id_keying", ast.dump(node)

    # install.sh has no docstrings to exempt; strip whole-line `#` comments.
    sh = [
        line
        for line in (root / "installer/install.sh").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not [line for line in sh if "id_keying" in line or "migrate-id-keying" in line]


def test_the_only_caller_is_the_operator_facing_cli() -> None:
    """Pins where it IS allowed to be driven from, so the guard has a subject."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    callers = sorted(
        p.relative_to(root).as_posix()
        for p in (root / "src").rglob("*.py")
        if "migrate_slot_id_keying(" in p.read_text(encoding="utf-8")
    )
    assert callers == ["src/hal0/cli/slot_commands.py", "src/hal0/slots/migrate_id_keying.py"]
