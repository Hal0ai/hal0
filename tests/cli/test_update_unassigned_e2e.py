"""End to end: a runner-system conversion that unassigns a slot's model reaches `hal0 update` (#2694, #2696).

#2711 tested every link on its own, each fed a hand-written dict. This file
chains the REAL links on a synthetic upgrade fixture, so a break between two
of them (a renamed key, a dropped field) fails here:

    migrate_runner_system (real, on a name-keyed slot TOML bound to a fork-only
    quant)  ->  run_post_activation_migrations  ->  PostActivationResult
    ->  the post-swap child script's JSON envelope
    ->  _run_post_activation_migrations_after_swap  ->  the convergence dict
    ->  `hal0 update` (typer CliRunner)  ->  the printed output.

Not covered (needs a live API server and slot manager): the HTTP hop between
`Updater.commit()` and the CLI, and `commit()` itself, which assembles the
convergence dict inline. That dict is rebuilt below with the keys `commit()`
emits (`updater.py`, the `post_activation_migrations` literal), so the
`unassigned_models` key is the one contract the test shares with it.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from typer.testing import CliRunner

from hal0.cli import update_commands as uc
from hal0.cli.main import app
from hal0.config import paths
from hal0.slot_config import no_model_assigned_message
from hal0.updater import updater as updater_module
from hal0.updater.updater import (
    PostActivationResult,
    _run_post_activation_migrations_after_swap,
    run_post_activation_migrations,
)

runner = CliRunner()

FORK_MODEL_ID = "hal0-brain-sft-q8-rocmfpx"
SLOT_REMEDY = "hal0 slot edit brain --model <id>"


def _gguf(tensor_types: list[int]) -> bytes:
    """A minimal GGUF v3: one string KV, then one tensor info per type."""

    def _s(text: str) -> bytes:
        raw = text.encode()
        return struct.pack("<Q", len(raw)) + raw

    out = b"GGUF" + struct.pack("<I", 3)
    out += struct.pack("<Q", len(tensor_types)) + struct.pack("<Q", 1)
    out += _s("general.architecture") + struct.pack("<I", 8) + _s("llama")
    for i, ttype in enumerate(tensor_types):
        out += _s(f"blk.{i}.weight") + struct.pack("<I", 2)
        out += struct.pack("<QQ", 64, 64) + struct.pack("<I", ttype) + struct.pack("<Q", 0)
    return out + b"\x00" * 64


@pytest.fixture
def fork_only_brain(tmp_hal0_home: str, tmp_path: Path) -> Path:
    """The ct150 shape: slot ``brain`` bound to a fork-only (ROCmFP8) build."""
    model = tmp_path / FORK_MODEL_ID / "model.gguf"
    model.parent.mkdir()
    model.write_bytes(_gguf([0, 12, 103]))
    db = paths.db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE model (id TEXT PRIMARY KEY, path TEXT, quant TEXT, "
        "hf_filename TEXT, preferred_runner TEXT)"
    )
    conn.execute(
        "INSERT INTO model VALUES (?, ?, ?, ?, ?)", (FORK_MODEL_ID, str(model), "Q8_0", None, None)
    )
    conn.commit()
    conn.close()
    slot_toml = paths.slots_config_dir() / "brain.toml"
    slot_toml.parent.mkdir(parents=True, exist_ok=True)
    slot_toml.write_text(
        tomli_w.dumps(
            {
                "name": "brain",
                "port": 8090,
                "binary": "standard",
                "model": {"default": FORK_MODEL_ID},
            }
        ),
        encoding="utf-8",
    )
    return slot_toml


@pytest.fixture
def _only_runner_system_is_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every post-activation pass except the runner-system conversion."""
    from hal0.config.migrations.profile_centric_apply import ConversionReport

    monkeypatch.setattr(
        "hal0.config.migrations.profile_centric_apply.migrate_profile_centric",
        lambda **_kw: ConversionReport(dry_run=False),
    )
    monkeypatch.setattr(
        "hal0.updater.updater._maybe_run_config_migrations", lambda *_a, **_kw: (1, 2)
    )
    monkeypatch.setattr(
        "hal0.registry.toml_retirement.retire_registry_toml", lambda **_kw: {"outcome": "absent"}
    )
    monkeypatch.setattr("hal0.updater.updater.relabel_stale_vulkan_slots", lambda **_kw: 0)
    monkeypatch.setattr("hal0.updater.updater.repair_hermes_mcp_client", lambda **_kw: False)
    monkeypatch.setattr("hal0.components.runner.converge_components", lambda **_kw: {})
    monkeypatch.setattr("hal0.updater.updater.rerender_slot_units", lambda **_kw: None)


def _migration_envelope_through_child_script(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    """Run the real post-swap helper; its subprocess executes the real child script in-process."""
    import structlog

    captured: dict[str, Any] = {}

    class _Completed:
        returncode = 0
        stderr = ""
        stdout = ""

    def _fake_run(cmd, *a, **k):
        captured["cmd"] = list(cmd)
        script, payload = cmd[2], cmd[3]
        monkeypatch.setattr(structlog, "configure", lambda **_k: None)
        monkeypatch.setattr("sys.argv", ["-c", payload])
        capsys.readouterr()
        exec(script, {})  # the script under test is our own literal
        done = _Completed()
        done.stdout = capsys.readouterr().out
        return done

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)
    return asyncio.run(_run_post_activation_migrations_after_swap(1, job_id="j-2694"))


def test_real_conversion_unassigns_and_the_result_carries_it(
    fork_only_brain: Path, _only_runner_system_is_real: None
) -> None:
    """Links 1-2: migrate_runner_system -> PostActivationResult, no stubs on the report."""
    result = run_post_activation_migrations()

    assert isinstance(result, PostActivationResult)
    assert [(u["slot"], u["model"]) for u in result.unassigned_models] == [("brain", FORK_MODEL_ID)]
    assert SLOT_REMEDY in result.unassigned_models[0]["remedy"]
    # The conversion really did what ADR-0028 §7 says on disk.
    import tomllib

    assert tomllib.loads(fork_only_brain.read_text())["model"]["default"] == ""
    # Unassigning is not a blocker (it must not flip `converged`).
    assert result.conversion_blockers == []


def _convergence_as_commit_builds_it(migration_result: dict[str, Any]) -> dict[str, Any]:
    """The `convergence` dict `Updater.commit()` returns (updater.py, `post_activation_migrations`)."""
    blockers = list(migration_result.get("conversion_blockers") or [])
    return {
        "post_activation_migrations": {
            "ok": True,
            "from": migration_result["from"],
            "to": migration_result["to"],
            "error": None,
            "pass_warnings": list(migration_result.get("pass_warnings") or []),
            "conversion_blockers": blockers,
            "unassigned_models": list(migration_result.get("unassigned_models") or []),
        },
        "converged": not blockers,
    }


def _run_update(
    monkeypatch: pytest.MonkeyPatch, convergence: dict[str, Any], slots: list[dict[str, Any]]
):
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    monkeypatch.setattr(uc, "_interactive", lambda: False)
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)

    def fake_get(path: str, **kwargs: object) -> dict:
        if path == "/api/updates/slot-drift":
            return {"count": len(slots), "slots": slots}
        if path == "/api/updates/components":
            return {"components": [], "pending": 0}
        return {
            "current": "0.0.0",
            "latest": "9.9.9",
            "channel": "stable",
            "update_available": True,
            "manifest": {},
        }

    def fake_post(path: str, *, json: object = None, **kwargs: object) -> dict:
        if path == "/api/updates/prepare":
            return {"id": "p1", "state": "queued"}
        if path == "/api/updates/commit":
            return {"id": "c1", "state": "queued"}
        return {"restarted": [], "skipped_busy": [], "failed": []}

    def fake_poll(
        job_id: str, *, terminal: tuple = ("applied", "failed"), **kwargs: object
    ) -> dict:
        if "prepared" in terminal:
            return {"id": job_id, "state": "prepared", "resolved_version": "9.9.9", "notes": {}}
        return {"id": job_id, "state": "applied", "convergence": convergence}

    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc, "api_post", fake_post)
    monkeypatch.setattr(uc, "_poll_job", fake_poll)
    return runner.invoke(app, ["update", "--no-restart-slots"])


def _flat(output: str) -> str:
    return " ".join(output.replace("│", " ").split())


def _image_drift() -> list[dict[str, str]]:
    return [{"key": "image", "running": "x:0824", "rendered": "x:0826"}]


def test_unassigned_warning_and_remedy_reach_the_update_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fork_only_brain: Path,
    _only_runner_system_is_real: None,
) -> None:
    """(a) + (b): real conversion output, through the child envelope, into `hal0 update`."""
    migration = _migration_envelope_through_child_script(monkeypatch, capsys)
    assert [u["slot"] for u in migration["unassigned_models"]] == ["brain"]

    result = _run_update(monkeypatch, _convergence_as_commit_builds_it(migration), [])

    out = _flat(result.output)
    # (a) the per-slot warning: slot, dropped model, why.
    assert "slot brain: model" in out
    assert FORK_MODEL_ID in out
    assert "build no runner can load" in out
    assert "ggml type 103" in out  # the quant label the real fork detector produced
    assert "was unassigned" in out
    # (b) the remedy that works for an unassigned slot, not the one that cannot.
    assert SLOT_REMEDY in out
    assert "hal0 update --restart-slots" not in out
    # Unassigning is reported, not a blocker: the update still converged.
    assert "profile conversion blocked" not in out
    assert result.exit_code == 0, result.output


def test_exit_2_verdict_excludes_render_error_slots_from_the_restart_list(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fork_only_brain: Path,
    _only_runner_system_is_real: None,
) -> None:
    """(c): after the real conversion, brain's render error is the production wording
    for an emptied binding; `chat` is a plain restartable image-drift slot."""
    migration = _migration_envelope_through_child_script(monkeypatch, capsys)
    render_error = no_model_assigned_message("brain")
    slots = [
        {
            "slot": "brain",
            "render_error": render_error,
            "diffs": [
                {"key": "render_error", "running": None, "rendered": None, "error": render_error},
                *_image_drift(),
            ],
        },
        {"slot": "chat", "diffs": _image_drift()},
    ]

    result = _run_update(monkeypatch, _convergence_as_commit_builds_it(migration), slots)

    out = _flat(result.output)
    assert result.exit_code == 2, result.output
    # The restart list names only the restartable slot.
    assert "restarts are pending: chat still run the old runner image" in out
    assert "restarts are pending: brain" not in out
    # brain gets its own remedy line, and the unassigned warning is still printed.
    assert "slot brain cannot restart until its config is fixed" in out
    assert out.count(SLOT_REMEDY) >= 2  # the warning's fix + the render-error line
    assert "update applied." not in out


def test_without_the_report_the_warning_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: no unassigned entries in the envelope -> no unassigned line (the
    assertions above are not satisfied by unrelated text)."""
    convergence = _convergence_as_commit_builds_it({"from": 1, "to": 1})
    result = _run_update(monkeypatch, convergence, [])
    assert "was unassigned" not in result.output
    assert json.dumps(convergence)  # plain data, serialisable like the real envelope
    assert result.exit_code == 0, result.output
