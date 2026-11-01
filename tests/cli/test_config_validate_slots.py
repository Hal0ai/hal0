"""`hal0 config validate` runs the typed ``SlotConfig`` load over slots/*.toml (#2720).

The daemon's reference scan (``discover.ref_slot_load_failed``) and
``hal0 doctor profiles`` ("does not load") both go through
``hal0.config.loader.load_slot_config``; validate used to check only
hal0.toml / upstreams.toml / providers.toml, so a slot file the daemon
refuses on every boot still printed "All configs pass schema validation."
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.cli import config_commands

runner = CliRunner()

_VALID_LLM = """\
[slot]
name = "brain"
type = "llm"
port = 8081

[model]
default = "some-model"
"""


@pytest.fixture
def slots_dir(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    d = home / "etc" / "hal0" / "slots"
    d.mkdir(parents=True)
    monkeypatch.setenv("HAL0_HOME", str(home))
    return d


def _validate() -> tuple[int, str]:
    result = runner.invoke(config_commands.app, ["validate"])
    return result.exit_code, result.output


def test_validate_passes_on_a_valid_slots_directory(slots_dir: Path) -> None:
    (slots_dir / "brain.toml").write_text(_VALID_LLM)

    code, out = _validate()

    assert code == 0, out
    assert "All configs pass schema validation." in out


def test_validate_passes_with_no_slots_directory(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "etc" / "hal0").mkdir(parents=True)
    monkeypatch.setenv("HAL0_HOME", str(home))

    code, out = _validate()

    assert code == 0, out


def test_validate_fails_on_unknown_tune_key_and_names_the_file(slots_dir: Path) -> None:
    (slots_dir / "brain.toml").write_text(_VALID_LLM + "\n[tune]\nnot_a_tune_key = 1\n")

    code, out = _validate()

    assert code == 1, out
    assert "brain.toml" in out
    assert "[tune].not_a_tune_key" in out, out
    assert "All configs pass" not in out


def test_validate_fails_on_unknown_model_key_like_the_issue(slots_dir: Path) -> None:
    (slots_dir / "rerank.toml").write_text(
        '[slot]\nname = "rerank"\ntype = "reranking"\nport = 8086\n\n'
        '[model]\ndefault = "some-model"\noutput_sanity = false\n'
    )

    code, out = _validate()

    assert code == 1, out
    assert "rerank.toml" in out
    assert "[model].output_sanity" in out, out


def test_validate_fails_on_out_of_range_port(slots_dir: Path) -> None:
    (slots_dir / "weird.toml").write_text(
        '[slot]\nname = "weird"\ntype = "llm"\nport = 80\n\n[model]\ndefault = "m"\n'
    )

    code, out = _validate()

    assert code == 1, out
    assert "weird.toml" in out
    assert "port" in out


def test_validate_fails_on_unparseable_slot_toml(slots_dir: Path) -> None:
    (slots_dir / "broken.toml").write_text("[slot\nname =\n")

    code, out = _validate()

    assert code == 1, out
    assert "broken.toml" in out


def test_validate_reports_every_failing_slot_but_not_the_valid_ones(slots_dir: Path) -> None:
    (slots_dir / "brain.toml").write_text(_VALID_LLM)
    (slots_dir / "a-bad.toml").write_text(_VALID_LLM + "\n[tune]\nnope = 1\n")
    (slots_dir / "z-bad.toml").write_text(_VALID_LLM + "\n[tune]\nnada = 2\n")

    code, out = _validate()

    assert code == 1, out
    assert "a-bad.toml" in out
    assert "z-bad.toml" in out
    assert "brain.toml" not in out


def test_validate_reports_slot_failure_alongside_a_bad_hal0_toml(slots_dir: Path) -> None:
    (slots_dir.parent / "hal0.toml").write_text("[meta\n")
    (slots_dir / "brain.toml").write_text(_VALID_LLM + "\n[tune]\nnope = 1\n")

    code, out = _validate()

    assert code == 1, out
    assert "hal0.toml" in out
    assert "brain.toml" in out
