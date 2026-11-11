"""A new install's first engine start on the configured embedder (#3518).

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_first_start.py -q
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.memory import embedder_record as er
from hal0.memory import extraction_env as ee
from hal0.memory import first_start as fs
from hal0.system.seam import SystemCtlSeam

BGE = "BAAI/bge-small-en-v1.5"
HS = "hindsight-ai/hs-embed-1"
PROVIDER_LINE = "Environment=HINDSIGHT_API_EMBEDDINGS_PROVIDER=local"


def _model_line(model: str) -> str:
    return f"Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL={model}"


class Box:
    """One box under ``HAL0_HOME`` (conftest points it at ``tmp_path``)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        dropins = tmp_path / "hindsight-api.service.d"
        self.dropin = dropins / "showcase-lanes.conf"
        monkeypatch.setattr(ee, "SHOWCASE_DROP_IN_PATH", self.dropin)
        monkeypatch.setattr(
            ee, "LEGACY_LLM_CAP_DROP_IN_PATH", dropins / "zz-h0-98-llm-global-cap.conf"
        )
        self.record = er.embedder_record_path()
        self.record.parent.mkdir(parents=True, exist_ok=True)
        from hal0.memory.engine_upgrade import hindsight_dir

        # What install.sh leaves before the engine's first start.
        self.hs_dir = hindsight_dir()
        (self.hs_dir / "hf-cache").mkdir(parents=True)
        (self.hs_dir / ".venv").mkdir()
        self.calls: list[list[str]] = []
        self.fail_writes = False
        self.seam = SystemCtlSeam(run=self._run, is_hal0_user=lambda: False)
        self.slept: list[float] = []
        self._clock = 0.0

    def _run(self, argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    def with_store(self) -> Box:
        (self.hs_dir / ".pg0" / "instances" / "hindsight" / "data").mkdir(parents=True)
        return self

    def with_record(self, model: str, source: str) -> Box:
        er.save_embedder_record(er.EmbedderRecord("local", model, source))
        return self

    def saved(self) -> dict[str, Any] | None:
        return json.loads(self.record.read_text()) if self.record.exists() else None

    def systemctl(self) -> list[str]:
        return [" ".join(c[1:]) for c in self.calls if c and c[0] == "systemctl"]

    def run(self, **kwargs: Any) -> dict[str, Any]:
        def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            self._clock += seconds

        kwargs.setdefault("healthy", lambda: True)
        kwargs.setdefault("unit_state", lambda: {"active": "active", "restarts": 0})
        return fs.run_first_start(
            seam=self.seam, sleep=sleep, monotonic=lambda: self._clock, **kwargs
        )


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Box:
    return Box(monkeypatch, tmp_path)


def _sequence(*values: Any):
    """A callable that returns each value in turn, then the last one forever."""
    left = list(values)

    def _next() -> Any:
        return left.pop(0) if len(left) > 1 else left[0]

    return _next


# ── a box with no store ───────────────────────────────────────────────────────


def test_no_store_writes_the_dropin_then_the_record_then_starts(box: Box) -> None:
    result = box.run()

    assert result["status"] == fs.STARTED
    assert (result["provider"], result["model"]) == ("local", HS)
    body = box.dropin.read_text()
    assert PROVIDER_LINE in body and _model_line(HS) in body
    saved = box.saved()
    assert saved is not None
    assert (saved["model"], saved["source"], saved["mixed"]) == (HS, er.SOURCE_CONFIGURED, False)
    # daemon-reload first: a drop-in is read only at unit load.
    assert box.systemctl() == ["daemon-reload", "restart hindsight-api.service"]


def test_the_record_is_not_written_when_the_dropin_cannot_be(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a: Any, **_k: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(box.seam, "write_hindsight_showcase_dropin", refuse)

    result = box.run()

    assert result["status"] == fs.FAILED
    assert box.saved() is None and not box.dropin.exists()
    assert box.systemctl() == []  # the installer starts the engine on the base unit


def test_a_configured_engine_default_needs_no_watched_start(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.config import loader
    from hal0.config.schema import Hal0Config

    cfg = Hal0Config()
    cfg.memory.models.embeddings_model = BGE
    monkeypatch.setattr(loader, "load_hal0_config", lambda: cfg)

    result = box.run()

    assert result["status"] == fs.PREPARED
    assert _model_line(BGE) in box.dropin.read_text()
    assert box.saved()["model"] == BGE
    assert box.systemctl() == []


def test_rerun_before_the_store_exists_is_idempotent(box: Box) -> None:
    first = box.run()
    body, saved = box.dropin.read_text(), box.saved()

    second = box.run()

    assert first["status"] == second["status"] == fs.STARTED
    assert box.dropin.read_text() == body
    assert box.saved() == saved  # same record, same recorded_at: never rewritten


def test_rerun_renders_an_earlier_fallback_record_not_the_config(box: Box) -> None:
    box.with_record(BGE, er.SOURCE_FIRST_START_FALLBACK)

    result = box.run()

    assert result["status"] == fs.PREPARED
    assert _model_line(BGE) in box.dropin.read_text()
    assert box.saved()["source"] == er.SOURCE_FIRST_START_FALLBACK
    assert box.systemctl() == []


# ── a box this step must not touch ────────────────────────────────────────────


def test_an_existing_store_is_left_alone(box: Box) -> None:
    box.with_store()

    result = box.run()

    assert result["status"] == fs.SKIPPED
    assert not box.dropin.exists() and box.saved() is None
    assert box.calls == []


def test_an_existing_store_keeps_its_dropin_and_record(box: Box) -> None:
    box.with_store().with_record(BGE, er.SOURCE_ENGINE_DEFAULT)
    box.dropin.parent.mkdir(parents=True)
    box.dropin.write_text("[Service]\n" + _model_line(BGE) + "\n")
    before = (box.dropin.read_text(), box.saved())

    assert box.run()["status"] == fs.SKIPPED
    assert (box.dropin.read_text(), box.saved()) == before
    assert box.calls == []


def test_an_unavailable_store_is_left_alone(box: Box, tmp_path: Path) -> None:
    """The engine home is a link to a disk that is not there."""
    import shutil

    shutil.rmtree(box.hs_dir)
    box.hs_dir.symlink_to(tmp_path / "not-mounted")
    assert er.store_state() == er.STORE_UNAVAILABLE

    result = box.run()

    assert result["status"] == fs.SKIPPED
    assert not box.dropin.exists() and box.saved() is None
    assert box.calls == []


def test_a_running_engine_is_left_alone(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        er,
        "engine_evidence",
        lambda **_k: {
            "live": {"provider": None, "model": None},
            "on_disk": None,
            "on_disk_source": None,
        },
    )

    assert box.run()["status"] == fs.SKIPPED
    assert not box.dropin.exists() and box.saved() is None
    assert box.calls == []


def test_an_unreadable_record_is_left_alone(box: Box) -> None:
    box.record.write_text("{not json")

    assert box.run()["status"] == fs.SKIPPED
    assert box.record.read_text() == "{not json"
    assert not box.dropin.exists()


# ── the first start does not come up ──────────────────────────────────────────


def test_a_failing_start_falls_back_to_the_engine_default(box: Box) -> None:
    """The model cannot be loaded: the engine exits and systemd restarts it."""
    result = box.run(
        healthy=lambda: False,
        unit_state=_sequence(
            {"active": "active", "restarts": 0},
            {"active": "activating", "restarts": 1},
            {"active": "activating", "restarts": 2},
        ),
    )

    assert result["status"] == fs.FELL_BACK
    assert result["configured"] == {"provider": "local", "model": HS}
    body = box.dropin.read_text()
    assert _model_line(BGE) in body and HS not in body
    saved = box.saved()
    assert (saved["provider"], saved["model"]) == ("local", BGE)
    assert saved["source"] == er.SOURCE_FIRST_START_FALLBACK and saved["mixed"] is False
    assert box.systemctl() == [
        "daemon-reload",
        "restart hindsight-api.service",
        "stop hindsight-api.service",
        "daemon-reload",
        "restart hindsight-api.service",
    ]


def test_a_failed_unit_falls_back(box: Box) -> None:
    result = box.run(healthy=lambda: False, unit_state=lambda: {"active": "failed", "restarts": 0})

    assert result["status"] == fs.FELL_BACK
    assert box.saved()["model"] == BGE


def test_one_restart_is_not_a_failing_start(box: Box) -> None:
    result = box.run(
        healthy=_sequence(False, False, True),
        unit_state=lambda: {"active": "active", "restarts": 1},
    )

    assert result["status"] == fs.STARTED
    assert box.saved()["model"] == HS


def test_a_slow_first_attempt_is_left_on_the_configured_embedder(box: Box) -> None:
    """Still downloading when the wait runs out: no fallback, nothing rewritten."""
    result = box.run(healthy=lambda: False, wait_s=9.0)

    assert result["status"] == fs.STARTING
    assert box.slept == [3.0, 3.0, 3.0]
    assert _model_line(HS) in box.dropin.read_text()
    assert box.saved()["model"] == HS
    assert "stop hindsight-api.service" not in box.systemctl()


def test_an_engine_that_comes_up_at_the_last_moment_is_not_fallen_back(box: Box) -> None:
    result = box.run(
        healthy=_sequence(False, True),
        unit_state=lambda: {"active": "active", "restarts": 2},
    )

    assert result["status"] == fs.STARTED
    assert box.saved()["model"] == HS


def test_no_fallback_while_the_engine_will_not_stop(box: Box) -> None:
    """The render follows a running engine, so the default cannot be rendered."""
    result = box.run(
        healthy=lambda: False,
        unit_state=lambda: {"active": "failed", "restarts": 0},
        evidence=lambda: {"live": {"provider": "local", "model": HS}},
    )

    assert result["status"] == fs.FALLBACK_FAILED
    assert _model_line(HS) in box.dropin.read_text()
    assert box.saved()["model"] == HS


def test_a_fallback_that_cannot_rewrite_the_dropin_restores_the_record(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record and unit files must still name the same embedder afterwards."""
    real = box.seam.write_hindsight_showcase_dropin
    writes: list[int] = []

    def second_write_fails(*args: Any, **kwargs: Any) -> None:
        writes.append(1)
        if len(writes) > 1:
            raise OSError("no space left on device")
        real(*args, **kwargs)

    monkeypatch.setattr(box.seam, "write_hindsight_showcase_dropin", second_write_fails)

    result = box.run(healthy=lambda: False, unit_state=lambda: {"active": "failed", "restarts": 0})

    assert result["status"] == fs.FALLBACK_FAILED
    assert _model_line(HS) in box.dropin.read_text()
    saved = box.saved()
    assert (saved["model"], saved["source"]) == (HS, er.SOURCE_CONFIGURED)
    assert box.systemctl()[-1] == "restart hindsight-api.service"


def test_a_start_command_that_fails_is_left_to_the_installer(box: Box) -> None:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        box.calls.append(list(argv))
        if "restart" in argv and kwargs.get("check"):
            raise subprocess.CalledProcessError(1, argv, "", "unit not found")
        return subprocess.CompletedProcess(argv, 0, "", "")

    box.seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    result = box.run(healthy=lambda: False)

    assert result["status"] == fs.STARTING
    assert box.saved()["model"] == HS  # the drop-in on disk carries it


# ── the unit-state reader and the process exit code ───────────────────────────


@pytest.mark.parametrize(
    ("stdout", "rc", "expected"),
    [
        ("ActiveState=active\nNRestarts=0\n", 0, {"active": "active", "restarts": 0}),
        ("NRestarts=3\nActiveState=activating\n", 0, {"active": "activating", "restarts": 3}),
        ("ActiveState=failed\nNRestarts=junk\n", 0, {"active": "failed", "restarts": 0}),
        ("", 1, {"active": None, "restarts": 0}),
    ],
)
def test_engine_unit_state_parses_systemctl_show(
    monkeypatch: pytest.MonkeyPatch, stdout: str, rc: int, expected: dict[str, Any]
) -> None:
    def run(argv: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        assert argv[:3] == ["systemctl", "show", "hindsight-api.service"]
        return subprocess.CompletedProcess(argv, rc, stdout, "")

    monkeypatch.setattr(fs.subprocess, "run", run)

    assert fs.engine_unit_state() == expected


def test_engine_unit_state_without_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_a: Any, **_k: Any) -> None:
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(fs.subprocess, "run", run)

    assert fs.engine_unit_state() == {"active": None, "restarts": 0}


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (fs.SKIPPED, fs.EXIT_OK),
        (fs.PREPARED, fs.EXIT_OK),
        (fs.STARTED, fs.EXIT_OK),
        (fs.STARTING, fs.EXIT_OK),
        (fs.FELL_BACK, fs.EXIT_FELL_BACK),
        (fs.FAILED, fs.EXIT_FAILED),
        (fs.FALLBACK_FAILED, fs.EXIT_FAILED),
    ],
)
def test_main_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: str, code: int
) -> None:
    result = {
        "status": status,
        "provider": "local",
        "model": BGE if status == fs.FELL_BACK else HS,
        "source": "configured",
        "configured": {"provider": "local", "model": HS},
        "reason": "why",
    }
    monkeypatch.setattr(fs, "run_first_start", lambda: result)

    assert fs.main() == code
    line = capsys.readouterr().out.strip()
    assert line.startswith("memory embedder: ") and "\n" not in line


def test_main_never_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(fs, "run_first_start", boom)

    assert fs.main() == fs.EXIT_FAILED
    assert "first-start step failed" in capsys.readouterr().out
