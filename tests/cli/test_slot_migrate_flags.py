"""``hal0 slot migrate-flags`` — the operator-run deploy-window flags fold
(spec-flags-ownership §5). Offline / filesystem-direct: it reads the on-disk
slot layout + profiles and rewrites the model registry, and is never wired into
any automatic boot/update path.

Covers the command's own wiring — the dry-run-by-default gate, the ``--apply``
backup, delegation to
:func:`hal0.config.migrations.slot_flags_fold.run_migration`, and the
divergent-share refusal surfacing as a non-zero exit — against a real fixture
tree. The fold LOGIC itself is unit-tested in
``tests/config/test_slot_flags_fold.py``.

Why this command exists (#1396): the launch-side readers of the slot flag
surface are already deleted (``providers.container`` drops
``profile_flags``/``slot_parallel``/``extra_args``; ``resolve_chat_template``
no longer consults the slot), but the migrator that folds those values onto the
bound model had no operator entry point at all — no CLI, no installer hook.
Boxes upgraded with a tuned slot silently launched without that tune and had no
supported way to recover it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import typer

from hal0.cli.slot_commands import slot_migrate_flags


def _write_slot(config_dir: Path, name: str, body: str) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    p = config_dir / f"{name}.toml"
    p.write_text(body, encoding="utf-8")
    return p


def _slot_body(*, name: str, model: str, extra_args: str, parallel: int | None = None) -> str:
    lines = [f'name = "{name}"', 'type = "llm"', "port = 8081"]
    if parallel is not None:
        lines.append(f"parallel = {parallel}")
    lines += ["[model]", f'default = "{model}"', "[server]", f'extra_args = "{extra_args}"', ""]
    return "\n".join(lines)


def test_dry_run_by_default_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    slot = _write_slot(
        paths.slots_config_dir(), "chat", _slot_body(name="chat", model="m", extra_args="-b 2048")
    )
    before = slot.read_text(encoding="utf-8")
    registry = ModelRegistry()
    registry.add(Model(id="m", path="/models/m.gguf"))

    slot_migrate_flags(apply=False, yes=True, stop_services=False)

    # Nothing written on either side, and no backup taken for a preview.
    assert slot.read_text(encoding="utf-8") == before
    assert (registry.get("m").defaults is None) or (registry.get("m").defaults.extra_args is None)
    assert not (paths.var_lib() / "backups").exists()


def test_apply_folds_slot_tune_onto_the_model_and_backs_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    slot = _write_slot(
        paths.slots_config_dir(),
        "chat",
        _slot_body(name="chat", model="m", extra_args="-b 2048", parallel=4),
    )
    registry = ModelRegistry()
    registry.add(Model(id="m", path="/models/m.gguf"))

    monkeypatch.setattr("hal0.cli.slot_commands._active_hal0_units", lambda: [])
    slot_migrate_flags(apply=True, yes=True, stop_services=False)

    folded = registry.get("m").defaults
    assert folded is not None
    # The slot's freeform tune is now materialized on the model, and the
    # inert --parallel is folded into the same text (schema: "the migrator
    # folds an effective --parallel N ... into defaults.extra_args").
    assert folded.extra_args is not None
    assert "-b 2048" in folded.extra_args
    assert "--parallel 4" in folded.extra_args

    # The slot TOML is deliberately NOT rewritten here — the fields are
    # retained for round-trip; removing the drawer controls is #1379.
    raw = tomllib.loads(slot.read_text(encoding="utf-8"))
    assert raw["model"]["default"] == "m"

    backups = list((paths.var_lib() / "backups").glob("*.tar.gz"))
    assert len(backups) == 1  # a backup is taken before the write


def test_apply_folds_chat_template_onto_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    _write_slot(
        paths.slots_config_dir(),
        "chat",
        'name = "chat"\ntype = "llm"\nport = 8081\nchat_template = "chatml"\n'
        '[model]\ndefault = "m"\n',
    )
    registry = ModelRegistry()
    registry.add(Model(id="m", path="/models/m.gguf"))

    monkeypatch.setattr("hal0.cli.slot_commands._active_hal0_units", lambda: [])
    slot_migrate_flags(apply=True, yes=True, stop_services=False)

    # resolve_chat_template reads ONLY the model now, so the slot's template
    # has to land here or it stops applying entirely.
    assert registry.get("m").defaults.chat_template == "chatml"


def test_apply_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    _write_slot(
        paths.slots_config_dir(), "chat", _slot_body(name="chat", model="m", extra_args="-b 2048")
    )
    registry = ModelRegistry()
    registry.add(Model(id="m", path="/models/m.gguf"))
    monkeypatch.setattr("hal0.cli.slot_commands._active_hal0_units", lambda: [])

    slot_migrate_flags(apply=True, yes=True, stop_services=False)
    first = registry.get("m").defaults

    slot_migrate_flags(apply=True, yes=True, stop_services=False)
    assert registry.get("m").defaults == first


def test_divergent_share_refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two slots, one model, different tunes → refuse the whole run.

    Folding what-you-can would silently pick a winner, so the planner refuses
    and the command must surface that as a non-zero exit with the offending
    model named — not a stack trace, and not a partial write.
    """
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    _write_slot(
        paths.slots_config_dir(), "a", _slot_body(name="a", model="shared", extra_args="-b 2048")
    )
    _write_slot(
        paths.slots_config_dir(), "b", _slot_body(name="b", model="shared", extra_args="-b 512")
    )
    registry = ModelRegistry()
    registry.add(Model(id="shared", path="/models/shared.gguf"))
    monkeypatch.setattr("hal0.cli.slot_commands._active_hal0_units", lambda: [])

    with pytest.raises(typer.Exit) as exc:
        slot_migrate_flags(apply=True, yes=True, stop_services=False)
    assert exc.value.exit_code == 1

    # No partial fold landed.
    assert (registry.get("shared").defaults is None) or (
        registry.get("shared").defaults.extra_args is None
    )


def test_dry_run_reports_divergence_without_raising_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview of a conflicted tree must also exit non-zero, cleanly.

    ``apply_fold_plan`` raises on refusals even for ``dry_run=True``, so the
    dry-run path needs the same guard — otherwise an operator previewing a
    conflicted box gets an unhandled RuntimeError.
    """
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    _write_slot(
        paths.slots_config_dir(), "a", _slot_body(name="a", model="shared", extra_args="-b 2048")
    )
    _write_slot(
        paths.slots_config_dir(), "b", _slot_body(name="b", model="shared", extra_args="-b 512")
    )
    registry = ModelRegistry()
    registry.add(Model(id="shared", path="/models/shared.gguf"))

    with pytest.raises(typer.Exit) as exc:
        slot_migrate_flags(apply=False, yes=True, stop_services=False)
    assert exc.value.exit_code == 1
    assert not (paths.var_lib() / "backups").exists()


def test_apply_refuses_while_hal0_units_are_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rewriting the registry under a live runtime split-brains it."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    _write_slot(
        paths.slots_config_dir(), "chat", _slot_body(name="chat", model="m", extra_args="-b 2048")
    )
    registry = ModelRegistry()
    registry.add(Model(id="m", path="/models/m.gguf"))
    monkeypatch.setattr("hal0.cli.slot_commands._active_hal0_units", lambda: ["hal0-api.service"])

    with pytest.raises(typer.Exit) as exc:
        slot_migrate_flags(apply=True, yes=True, stop_services=False)
    assert exc.value.exit_code == 1
    assert (registry.get("m").defaults is None) or (registry.get("m").defaults.extra_args is None)
