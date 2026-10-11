"""Where the runner-system conversion runs at API startup (ADR-0028).

It runs in ``hal0 serve`` before :mod:`hal0.api` is imported, never on import:
``hal0.api`` builds its module-level app on import, and read-only CLI verbs
import modules that reach it, so a conversion there would rewrite
``/etc/hal0`` from a ``hal0 runner-images ls``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.config import paths

_OLD_TOML = (
    "[models]\nroots = []\nauto_scan_on_start = false\n\n"
    '[slots.default_images]\nvulkanfpx = "ghcr.io/hal0ai/hal0-combined:0826"\n'
)


def _old_box() -> Path:
    hal0_toml = paths.hal0_toml()
    hal0_toml.parent.mkdir(parents=True, exist_ok=True)
    hal0_toml.write_text(_OLD_TOML, encoding="utf-8")
    slots = paths.slots_config_dir()
    slots.mkdir(parents=True, exist_ok=True)
    (slots / "chat.toml").write_text(
        'name = "chat"\nport = 8081\nbinary = "rocmfpx"\n', encoding="utf-8"
    )
    return hal0_toml


def _snapshot() -> dict[str, bytes]:
    root = paths.etc()
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_create_app_converts_nothing(tmp_hal0_home: str) -> None:
    _old_box()
    before = _snapshot()
    from hal0.api import create_app

    create_app()
    assert _snapshot() == before
    assert not (paths.var_lib() / "backups").exists()


def test_read_only_cli_converts_nothing(tmp_hal0_home: str) -> None:
    from hal0.cli.runner_image_commands import app

    _old_box()
    before = _snapshot()
    CliRunner().invoke(app, ["ls"])
    assert _snapshot() == before


def test_serve_converts_before_importing_the_api(tmp_hal0_home: str, monkeypatch) -> None:
    import hal0.cli.main as cli_main

    hal0_toml = _old_box()
    seen: dict[str, object] = {}

    def _fake_run(target: str, **_kw: object) -> None:
        seen["target"] = target
        seen["toml"] = tomllib.loads(hal0_toml.read_text(encoding="utf-8"))

    monkeypatch.setattr(cli_main.uvicorn, "run", _fake_run)
    result = CliRunner().invoke(cli_main.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert seen["target"] == "hal0.api:app"
    toml = seen["toml"]
    assert isinstance(toml, dict)
    assert toml["slots"]["default_images"] == {}
    assert tomllib.loads((paths.slots_config_dir() / "chat.toml").read_text())["binary"] == (
        "standard"
    )


def test_serve_starts_even_when_the_conversion_fails(tmp_hal0_home: str, monkeypatch) -> None:
    import hal0.cli.main as cli_main
    from hal0.config.migrations import runner_system

    _old_box()

    def _boom(**_kw: object) -> None:
        raise runner_system.RunnerMigrationError("boom")

    monkeypatch.setattr(runner_system, "migrate_runner_system", _boom)
    ran: list[str] = []
    monkeypatch.setattr(cli_main.uvicorn, "run", lambda target, **_kw: ran.append(target))
    result = CliRunner().invoke(cli_main.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert ran == ["hal0.api:app"]


@pytest.mark.parametrize("marker", [True, False])
def test_serve_honours_the_restore_marker(tmp_hal0_home: str, monkeypatch, marker: bool) -> None:
    import hal0.cli.main as cli_main
    from hal0.config.migrations.runner_system import restore_marker

    hal0_toml = _old_box()
    if marker:
        restore_marker().parent.mkdir(parents=True, exist_ok=True)
        restore_marker().write_text("x\n")
    monkeypatch.setattr(cli_main.uvicorn, "run", lambda *_a, **_kw: None)
    CliRunner().invoke(cli_main.app, ["serve"])
    converted = "vulkanfpx" not in hal0_toml.read_text(encoding="utf-8")
    assert converted is (not marker)
