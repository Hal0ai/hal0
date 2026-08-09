"""Tests for the Hermes WebUI (hermex) companion provisioning module."""
from pathlib import Path

import hal0.agents.hermes_webui as hw


def test_pinned_ref_is_vetted() -> None:
    assert hw.WEBUI_PINNED_REF in hw.VETTED_HERMES_WEBUI_REFS
    assert len(hw.WEBUI_PINNED_REF) == 40  # full SHA, not a short ref


def test_ensure_env_seeds_all_defaults_on_fresh_tree(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    venv = tmp_path / "venv"
    out = hw.ensure_env(tree, venv)
    assert out.ok and out.changed
    body = (tree / ".env").read_text(encoding="utf-8")
    assert "HERMES_WEBUI_HOST=127.0.0.1" in body
    assert "HERMES_WEBUI_PORT=8787" in body
    assert f"HERMES_WEBUI_PYTHON={venv}/bin/python3" in body
    # a password was generated and is non-trivial
    pw_line = next(
        line for line in body.splitlines() if line.startswith("HERMES_WEBUI_PASSWORD=")
    )
    assert len(pw_line.split("=", 1)[1]) >= 24
    assert ((tree / ".env").stat().st_mode & 0o777) == 0o600


def test_ensure_env_preserves_operator_edits(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    existing = "# operator file\nHERMES_WEBUI_HOST=0.0.0.0\nHERMES_WEBUI_PASSWORD=operator-secret\n"
    (tree / ".env").write_text(existing, encoding="utf-8")
    out = hw.ensure_env(tree, tmp_path / "venv")
    body = (tree / ".env").read_text(encoding="utf-8")
    assert out.changed  # PORT + PYTHON were appended
    assert "HERMES_WEBUI_HOST=0.0.0.0" in body           # untouched
    assert "HERMES_WEBUI_PASSWORD=operator-secret" in body  # untouched
    assert body.startswith("# operator file")             # comments preserved
    assert "HERMES_WEBUI_PORT=8787" in body


def test_ensure_env_converges_second_run(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    hw.ensure_env(tree, tmp_path / "venv")
    second = hw.ensure_env(tree, tmp_path / "venv")
    assert second.ok and not second.changed
