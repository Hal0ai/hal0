"""``hal0 doctor profiles`` halogen rows: is a registered ``.hgn`` checkpoint complete?

``check_halogen_weights`` is pure over a registry and a tmp tree: one row per
model whose provider is ``halogen``, graded on what the engine needs beside the
checkpoint (the ``HGN1`` magic, the ngram lookup table, ``tokenizer/``).
"""

from __future__ import annotations

import json as jsonlib
import sqlite3
from pathlib import Path

import pytest
import typer

from hal0.cli.doctor_commands import (
    _halogen_weight_rows,
    _unregistered_store_files,
    check_halogen_weights,
    doctor_profiles,
)
from hal0.config.schema import ModelsConfig
from hal0.diagnostics import DIAGNOSIS_IDS
from hal0.registry.detect import HALOGEN_VISION_SIDECAR, HGN_MAGIC
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry

CKPT = "qwen38-flash-next.hgn"
TABLE = "qwen38-flash-next-ngram.hgn"
FETCH = "hf download peonist-ai/halogen-qwen3.8-flash-next {name} --local-dir {dir}"


class _FakeRegistry:
    """The one method ``check_halogen_weights`` reads."""

    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def list(self) -> list[Model]:
        return list(self._models)


def _halogen_dir(
    root: Path,
    *,
    table: bool = True,
    tokenizer: bool = True,
    vision: bool = False,
    magic: bytes = HGN_MAGIC,
    checkpoint: bool = True,
) -> Path:
    """A halogen model directory; each flag toggles one thing the engine needs."""
    root.mkdir(parents=True, exist_ok=True)
    if checkpoint:
        (root / CKPT).write_bytes(magic + b"w" * 28)
    if table:
        (root / TABLE).write_bytes(HGN_MAGIC + b"n" * 20)
    if tokenizer:
        (root / "tokenizer").mkdir(exist_ok=True)
        (root / "tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")
    if vision:
        (root / HALOGEN_VISION_SIDECAR).write_bytes(HGN_MAGIC + b"v" * 20)
    return root


def _halogen_model(root: Path, model_id: str = "flash-next") -> Model:
    return Model(
        id=model_id,
        path=str(root / CKPT),
        provider="halogen",
        backends=["halogen"],
        capabilities=["chat"],
    )


def test_complete_checkpoint_is_ok(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash")
    rows = check_halogen_weights(_FakeRegistry([_halogen_model(root)]))
    assert len(rows) == 1
    assert rows[0]["label"] == "flash-next"
    assert rows[0]["status"] == "ok"
    # The sidecar's absence is information, not a problem.
    assert "vision sidecar absent" in rows[0]["detail"]


def test_vision_sidecar_presence_is_reported_as_information(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash", vision=True)
    (row,) = check_halogen_weights(_FakeRegistry([_halogen_model(root)]))
    assert row["status"] == "ok"
    assert "vision sidecar present" in row["detail"]


def test_missing_lookup_table_warns_with_the_fetch_line(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash", table=False)
    (row,) = check_halogen_weights(_FakeRegistry([_halogen_model(root)]))
    assert row["status"] == "warn"
    assert TABLE in row["detail"]
    # The detail ENDS with the exact, copy-pasteable fetch line.
    assert row["detail"].endswith(FETCH.format(name=TABLE, dir=root))
    assert row["detail"].splitlines()[-1] == FETCH.format(name=TABLE, dir=root)


def test_missing_lookup_table_still_reports_the_vision_sidecar(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash", table=False, vision=True)
    (row,) = check_halogen_weights(_FakeRegistry([_halogen_model(root)]))
    assert row["status"] == "warn"
    assert "vision sidecar present" in row["detail"]
    assert row["detail"].splitlines()[-1] == FETCH.format(name=TABLE, dir=root)


def test_missing_tokenizer_fails(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash", tokenizer=False)
    (row,) = check_halogen_weights(_FakeRegistry([_halogen_model(root)]))
    assert row["status"] == "fail"
    assert "tokenizer/tokenizer.json" in row["detail"]


def test_missing_tokenizer_outranks_a_missing_table(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash", table=False, tokenizer=False)
    (row,) = check_halogen_weights(_FakeRegistry([_halogen_model(root)]))
    assert row["status"] == "fail"
    assert "tokenizer/tokenizer.json" in row["detail"]
    assert TABLE in row["detail"]


def test_missing_checkpoint_fails(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash", checkpoint=False)
    (row,) = check_halogen_weights(_FakeRegistry([_halogen_model(root)]))
    assert row["status"] == "fail"
    assert str(root / CKPT) in row["detail"]
    assert "missing" in row["detail"]


def test_non_hgn_checkpoint_fails(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash", magic=b"GGUF")
    (row,) = check_halogen_weights(_FakeRegistry([_halogen_model(root)]))
    assert row["status"] == "fail"
    assert "HGN1" in row["detail"]


def test_only_halogen_models_get_rows(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash")
    llama = Model(
        id="qwen3-4b",
        path=str(tmp_path / "qwen3-4b.gguf"),
        provider="llama-server",
        backends=["vulkan"],
    )
    rows = check_halogen_weights(_FakeRegistry([llama, _halogen_model(root)]))
    assert [r["label"] for r in rows] == ["flash-next"]


def test_a_row_without_a_stored_provider_resolves_through_its_backends(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "flash")
    legacy = Model(id="flash-next", path=str(root / CKPT), backends=["halogen"])
    assert legacy.provider is None
    rows = check_halogen_weights(_FakeRegistry([legacy]))
    assert [r["label"] for r in rows] == ["flash-next"]


def test_no_halogen_models_means_no_rows() -> None:
    assert check_halogen_weights(_FakeRegistry([])) == []


# ── wired into ``hal0 doctor profiles`` ───────────────────────────────────────


def test_doctor_profiles_json_reports_an_incomplete_halogen_checkpoint(
    tmp_hal0_home: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _halogen_dir(tmp_path / "flash", table=False)
    ModelRegistry().add(_halogen_model(root))

    with pytest.raises(typer.Exit) as exc_info:
        doctor_profiles(json_output=True)
    assert exc_info.value.exit_code == 0  # a missing table only warns

    out = jsonlib.loads(capsys.readouterr().out)
    warn = [d for d in out if d["id"] == "HAL0-HALOGEN-WEIGHTS-INCOMPLETE"]
    assert len(warn) == 1
    assert warn[0]["id"] in DIAGNOSIS_IDS
    assert warn[0]["severity"] == "warn"
    assert "flash-next" in warn[0]["summary"]


def test_doctor_profiles_exits_nonzero_for_a_failing_halogen_checkpoint(
    tmp_hal0_home: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _halogen_dir(tmp_path / "flash", tokenizer=False)
    ModelRegistry().add(_halogen_model(root))

    with pytest.raises(typer.Exit) as exc_info:
        doctor_profiles(json_output=True)
    assert exc_info.value.exit_code == 1

    out = jsonlib.loads(capsys.readouterr().out)
    fail = [d for d in out if d["id"] == "HAL0-HALOGEN-WEIGHTS-INCOMPLETE"]
    assert len(fail) == 1
    assert fail[0]["id"] in DIAGNOSIS_IDS
    assert fail[0]["severity"] == "fail"


def test_doctor_profiles_human_report_renders_a_halogen_section(
    tmp_hal0_home: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _halogen_dir(tmp_path / "flash", table=False)
    ModelRegistry().add(_halogen_model(root))

    with pytest.raises(typer.Exit):
        doctor_profiles(json_output=False)
    out = capsys.readouterr().out
    assert "Halogen checkpoints" in out
    assert "flash-next" in out
    # The fetch line is copy-pasteable: the console never wraps it, however
    # long the checkpoint's directory is.
    assert FETCH.format(name=TABLE, dir=root) in out


def test_doctor_profiles_human_report_exits_1_for_a_failing_halogen_checkpoint(
    tmp_hal0_home: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _halogen_dir(tmp_path / "flash", tokenizer=False)
    ModelRegistry().add(_halogen_model(root))

    with pytest.raises(typer.Exit) as exc_info:
        doctor_profiles(json_output=False)
    assert exc_info.value.exit_code == 1

    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "tokenizer/tokenizer.json" in out
    assert "1 halogen checkpoint(s) cannot load" in out
    assert "every slot resolves to a real profile" not in out


def test_doctor_profiles_creates_no_registry_database(
    tmp_hal0_home: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A read-only audit on a box that never registered a model must not mint a
    registry database just to find it holds no halogen rows."""
    assert not ModelRegistry().db_path.exists()

    assert _halogen_weight_rows() == []
    assert not ModelRegistry().db_path.exists()

    with pytest.raises(typer.Exit) as exc_info:
        doctor_profiles(json_output=True)
    assert exc_info.value.exit_code == 0
    assert not ModelRegistry().db_path.exists()


def test_an_unreadable_registry_is_not_a_halogen_finding(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BrokenRegistry:
        db_path = tmp_path / "hal0.db"

        def __init__(self) -> None:
            self.db_path.write_bytes(b"not a database")

        def list(self) -> list[Model]:
            raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr("hal0.registry.store.ModelRegistry", _BrokenRegistry)
    assert _halogen_weight_rows() == []


def test_a_bug_in_the_row_checks_is_not_swallowed(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _halogen_dir(tmp_path / "flash")

    class _Registry:
        db_path = tmp_path / "hal0.db"

        def __init__(self) -> None:
            self.db_path.write_bytes(b"")

        def list(self) -> list[Model]:
            return [_halogen_model(root)]

    def _boom(_models: object) -> list[dict[str, str]]:
        raise TypeError("a programming error in the row checks")

    monkeypatch.setattr("hal0.registry.store.ModelRegistry", _Registry)
    monkeypatch.setattr("hal0.cli.doctor_commands._halogen_weight_rows_for", _boom)
    with pytest.raises(TypeError, match="programming error"):
        _halogen_weight_rows()


# ── store audit: .hgn companions are not unregistered models ──────────────────


def test_unregistered_store_files_skips_halogen_companions(tmp_path: Path) -> None:
    root = _halogen_dir(tmp_path / "store" / "flash", vision=True)
    exts = {e.lower() for e in ModelsConfig().file_extensions}
    assert ".hgn" in exts  # the stock scan config picks .hgn up

    found = _unregistered_store_files(tmp_path / "store", exts, registered_paths=set())
    # Only the checkpoint is a model; the ngram table and the vision sidecar
    # carry HGN1 magic too but the engine finds them by name.
    assert found == [str(root / CKPT)]
