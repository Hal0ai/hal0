"""A new install's first engine start on the configured embedder (#3518).

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_first_start.py -q
"""

from __future__ import annotations

import json
import shutil
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
MODEL_KEY = "Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL="
PROVIDER_LINE = "Environment=HINDSIGHT_API_EMBEDDINGS_PROVIDER=local"


class Box:
    """One box under ``HAL0_HOME`` (conftest points it at ``tmp_path``), with a fake engine.

    The engine "runs" between a ``systemctl restart`` and a ``systemctl stop``
    and is on whatever embedder the drop-in named when it was started. It
    answers ``/health`` only on a model in ``loads``; on any other model its
    process has exited and systemd shows the restart, unless the model is in
    ``slow`` (first process still running, never healthy). ``reachable`` is
    whether the model host answers.
    """

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

        self.loads: set[str] = {HS, BGE}
        self.slow: set[str] = set()
        self.stops = True
        self.reachable = True
        self.pid = 100
        self.running_on: str | None = None
        self.started_on: list[str | None] = []
        self.calls: list[list[str]] = []
        self.seam = SystemCtlSeam(run=self._run, is_hal0_user=lambda: False)
        self.slept: list[float] = []
        self._clock = 0.0
        # The render follows a running engine: let the record module see ours.
        monkeypatch.setattr(er, "engine_evidence", lambda **_k: self.evidence())

    # ── the fake engine ──────────────────────────────────────────────────────

    def dropin_model(self) -> str | None:
        if not self.dropin.exists():
            return None
        for line in self.dropin.read_text().splitlines():
            if line.startswith(MODEL_KEY):
                return line[len(MODEL_KEY) :]
        return None

    def _run(self, argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if argv[:2] == ["systemctl", "restart"]:
            self.pid += 1
            self.running_on = self.dropin_model() or BGE
            self.started_on.append(self.running_on)
        elif argv[:2] == ["systemctl", "stop"] and self.stops:
            self.running_on = None
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    def healthy(self) -> bool:
        return self.running_on in self.loads and self.running_on not in self.slow

    def unit_state(self) -> dict[str, Any]:
        if self.running_on is None:
            return {"active": "inactive", "restarts": 0, "main_pid": 0}
        if self.running_on in self.loads or self.running_on in self.slow:
            return {"active": "active", "restarts": 0, "main_pid": self.pid}
        return {"active": "activating", "restarts": 1, "main_pid": 0}

    def evidence(self) -> dict[str, Any]:
        live = {"provider": "local", "model": self.running_on} if self.running_on else None
        on_disk = None
        if self.dropin_model():
            on_disk = {"provider": "local", "model": self.dropin_model()}
        return {"live": live, "on_disk": on_disk, "on_disk_source": "drop-in" if on_disk else None}

    # ── the box ──────────────────────────────────────────────────────────────

    def unit_state_after_failed_start(self) -> dict[str, Any]:
        was, self.running_on = self.running_on, "no-such/model"
        try:
            return self.unit_state()
        finally:
            self.running_on = was

    def with_store(self) -> Box:
        (self.hs_dir / ".pg0" / "instances" / "hindsight" / "data").mkdir(parents=True)
        return self

    def with_record(self, model: str, source: str) -> Box:
        er.save_embedder_record(er.EmbedderRecord("local", model, source))
        return self

    def saved(self) -> dict[str, Any] | None:
        if not self.record.exists():
            return None
        try:
            return json.loads(self.record.read_text())
        except ValueError:
            return None

    def systemctl(self) -> list[str]:
        return [" ".join(c[1:]) for c in self.calls if c and c[0] == "systemctl"]

    def run(self, **kwargs: Any) -> dict[str, Any]:
        def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            self._clock += seconds

        kwargs.setdefault("healthy", self.healthy)
        kwargs.setdefault("unit_state", self.unit_state)
        kwargs.setdefault("host_reachable", lambda: self.reachable)
        result = fs.run_first_start(
            seam=self.seam, sleep=sleep, monotonic=lambda: self._clock, **kwargs
        )
        self.assert_record_matches_dropin()
        return result

    def assert_record_matches_dropin(self) -> None:
        """The invariant: where both exist, they name the same embedder."""
        saved, model = self.saved(), self.dropin_model()
        if saved is not None and model is not None:
            assert saved["model"] == model, f"record {saved['model']} but drop-in {model}"
        if self.running_on is not None and model is not None:
            assert self.running_on == model, f"engine on {self.running_on}, drop-in {model}"


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Box:
    return Box(monkeypatch, tmp_path)


# ── a box with no store ───────────────────────────────────────────────────────


def test_no_store_writes_the_dropin_then_the_record_then_starts(box: Box) -> None:
    result = box.run()

    assert result["status"] == fs.STARTED
    assert (result["provider"], result["model"]) == ("local", HS)
    assert PROVIDER_LINE in box.dropin.read_text() and box.dropin_model() == HS
    saved = box.saved()
    assert saved is not None
    assert (saved["model"], saved["source"], saved["mixed"]) == (HS, er.SOURCE_CONFIGURED, False)
    # daemon-reload before the start: a drop-in is read only at unit load.
    assert box.systemctl() == [
        "reset-failed hindsight-api.service",
        "daemon-reload",
        "restart hindsight-api.service",
    ]
    assert box.started_on == [HS]


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
    assert box.dropin_model() == BGE and box.saved()["model"] == BGE
    assert box.systemctl() == []


def test_rerun_before_the_store_exists_is_idempotent(box: Box) -> None:
    first = box.run()
    body, saved = box.dropin.read_text(), box.saved()
    box.running_on = None  # the engine went away without ever creating a store

    second = box.run()

    assert first["status"] == second["status"] == fs.STARTED
    assert box.dropin.read_text() == body
    assert box.saved() == saved  # same record, same recorded_at: never rewritten


def test_rerun_renders_an_earlier_fallback_record_not_the_config(box: Box) -> None:
    box.with_record(BGE, er.SOURCE_FIRST_START_FALLBACK)

    result = box.run()

    assert result["status"] == fs.PREPARED
    assert box.dropin_model() == BGE
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
    box.dropin.write_text(f"[Service]\n{MODEL_KEY}{BGE}\n")
    before = (box.dropin.read_text(), box.saved())

    assert box.run()["status"] == fs.SKIPPED
    assert (box.dropin.read_text(), box.saved()) == before
    assert box.calls == []


def test_an_unavailable_store_is_left_alone(box: Box, tmp_path: Path) -> None:
    """The engine home is a link to a disk that is not there."""
    shutil.rmtree(box.hs_dir)
    box.hs_dir.symlink_to(tmp_path / "not-mounted")
    assert er.store_state() == er.STORE_UNAVAILABLE

    result = box.run()

    assert result["status"] == fs.SKIPPED
    assert not box.dropin.exists() and box.saved() is None
    assert box.calls == []


def test_a_running_engine_is_left_alone(box: Box) -> None:
    box.running_on = BGE

    assert box.run()["status"] == fs.SKIPPED
    assert not box.dropin.exists() and box.saved() is None
    assert box.calls == []


def test_an_unreadable_record_is_left_alone(box: Box) -> None:
    box.record.write_text("{not json")

    assert box.run()["status"] == fs.SKIPPED
    assert box.record.read_text() == "{not json"
    assert not box.dropin.exists()


# ── the first start does not come up ──────────────────────────────────────────


def test_embedder_is_the_cause_falls_back_and_only_then_records_the_default(box: Box) -> None:
    """The configured model cannot be loaded; the engine default can."""
    box.loads = {BGE}
    seen: list[tuple[str | None, str | None]] = []
    real_healthy = box.healthy

    def healthy() -> bool:
        saved = box.saved()
        seen.append((box.running_on, saved["model"] if saved else None))
        return real_healthy()

    result = box.run(healthy=healthy)

    assert result["status"] == fs.FELL_BACK
    assert result["configured"] == {"provider": "local", "model": HS}
    assert box.dropin_model() == BGE and HS not in box.dropin.read_text()
    saved = box.saved()
    assert (saved["provider"], saved["model"]) == ("local", BGE)
    assert saved["source"] == er.SOURCE_FIRST_START_FALLBACK and saved["mixed"] is False
    assert box.started_on == [HS, BGE] and box.running_on == BGE
    # While the default was only on trial the record still said the configured pair.
    assert (BGE, HS) in seen and (BGE, BGE) not in seen
    assert box.systemctl() == [
        "reset-failed hindsight-api.service",
        "daemon-reload",
        "restart hindsight-api.service",
        "stop hindsight-api.service",
        "reset-failed hindsight-api.service",
        "daemon-reload",
        "restart hindsight-api.service",
    ]


def test_trial_changes_only_the_embedder_lines(box: Box) -> None:
    box.loads = {BGE}
    box.run(wait_s=0.0, healthy=lambda: True)
    configured_body = box.dropin.read_text()
    box.running_on = None

    box.run()

    assert box.dropin_model() == BGE
    assert box.dropin.read_text() == configured_body.replace(HS, BGE)


def test_an_unreachable_model_host_skips_the_trial(box: Box) -> None:
    """Nothing can be downloaded, the engine default included: nothing is tried."""
    box.loads, box.reachable = set(), False

    result = box.run()

    assert result["status"] == fs.MODELS_UNREACHABLE
    assert (result["model"], result["source"]) == (HS, er.SOURCE_CONFIGURED)
    assert box.dropin_model() == HS
    saved = box.saved()
    assert (saved["model"], saved["source"]) == (HS, er.SOURCE_CONFIGURED)
    # Started once and left retrying: no stop, no trial on the default.
    assert box.started_on == [HS] and box.running_on == HS
    assert "stop hindsight-api.service" not in box.systemctl()
    assert "could not download its models" in fs.describe(result)


def test_reachable_host_and_both_embedders_fail_restores_the_configured_one(box: Box) -> None:
    """The host answers but the engine starts on neither: the embedder is not the cause."""
    box.loads = set()

    result = box.run()

    assert result["status"] == fs.NOT_STARTED
    assert (result["model"], result["source"]) == (HS, er.SOURCE_CONFIGURED)
    assert box.dropin_model() == HS
    saved = box.saved()
    assert (saved["model"], saved["source"]) == (HS, er.SOURCE_CONFIGURED)
    # Tried on both, then left retrying on the configured one.
    assert box.started_on == [HS, BGE, HS] and box.running_on == HS
    assert box.systemctl()[-3:] == [
        "reset-failed hindsight-api.service",
        "daemon-reload",
        "restart hindsight-api.service",
    ]


def test_a_cause_other_than_the_embedder_keeps_the_configured_embedder(box: Box) -> None:
    """A failed unit on both attempts (a taken port, a database that will not start)."""
    result = box.run(healthy=lambda: False, unit_state=lambda: {"active": "failed", "restarts": 0})

    assert result["status"] == fs.NOT_STARTED
    assert box.dropin_model() == HS and box.saved()["model"] == HS


def test_a_trial_that_is_only_slow_proves_nothing(box: Box) -> None:
    box.loads, box.slow = set(), {BGE}

    result = box.run(wait_s=6.0)

    assert result["status"] == fs.NOT_STARTED
    assert box.dropin_model() == HS and box.saved()["model"] == HS
    assert box.started_on == [HS, BGE, HS]


def test_the_first_exit_is_a_failing_start(box: Box) -> None:
    """One restart is enough: a healthy first start never has one."""
    box.loads = {BGE}
    assert box.unit_state_after_failed_start()["restarts"] == 1

    result = box.run()

    assert result["status"] == fs.FELL_BACK
    assert box.slept == []  # seen on the first probe, not after a second restart


def test_another_main_pid_is_a_failing_start(box: Box) -> None:
    """The process was replaced between two probes and the counters say nothing."""
    box.loads = {BGE}
    pids = iter([101, 101, 102])

    def unit_state() -> dict[str, Any]:
        if box.running_on == HS:
            return {"active": "active", "restarts": 0, "main_pid": next(pids, 102)}
        return box.unit_state()

    result = box.run(unit_state=unit_state)

    assert result["status"] == fs.FELL_BACK
    assert box.started_on == [HS, BGE]


def test_an_unreadable_unit_is_not_a_failing_start(box: Box) -> None:
    box.loads, box.slow = set(), {HS}

    result = box.run(
        wait_s=6.0, unit_state=lambda: {"active": None, "restarts": 0, "main_pid": None}
    )

    assert result["status"] == fs.STARTING and box.started_on == [HS]


def test_a_slow_first_attempt_is_left_on_the_configured_embedder(box: Box) -> None:
    """Still downloading when the wait runs out: no trial, nothing rewritten."""
    box.loads, box.slow = set(), {HS}

    result = box.run(wait_s=9.0)

    assert result["status"] == fs.STARTING
    assert box.slept == [3.0, 3.0, 3.0]
    assert box.dropin_model() == HS and box.saved()["model"] == HS
    assert "stop hindsight-api.service" not in box.systemctl()


def test_an_engine_that_comes_up_at_the_last_moment_is_not_put_on_trial(box: Box) -> None:
    polls = iter([False, True])

    result = box.run(
        healthy=lambda: next(polls), unit_state=lambda: {"active": "active", "restarts": 2}
    )

    assert result["status"] == fs.STARTED
    assert box.saved()["model"] == HS and box.started_on == [HS]


def test_no_trial_while_the_engine_will_not_stop(box: Box) -> None:
    """The render follows a running engine, so the default cannot be put on trial."""
    box.loads, box.stops = set(), False

    result = box.run()

    assert result["status"] == fs.NOT_STARTED
    assert box.dropin_model() == HS and box.saved()["model"] == HS
    assert box.started_on == [HS]


def test_a_trial_dropin_that_cannot_be_written_leaves_the_configured_one(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    box.loads = {BGE}
    real = box.seam.write_hindsight_showcase_dropin
    writes: list[int] = []

    def second_write_fails(*args: Any, **kwargs: Any) -> None:
        writes.append(1)
        if len(writes) == 2:
            raise OSError("no space left on device")
        real(*args, **kwargs)

    monkeypatch.setattr(box.seam, "write_hindsight_showcase_dropin", second_write_fails)

    result = box.run()

    assert result["status"] == fs.NOT_STARTED
    assert box.dropin_model() == HS
    assert (box.saved()["model"], box.saved()["source"]) == (HS, er.SOURCE_CONFIGURED)
    assert box.started_on == [HS, HS]


def test_a_dropin_that_cannot_be_put_back_takes_the_record_with_it(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record follows the drop-in on disk, never the other way round."""
    box.loads = set()
    real = box.seam.write_hindsight_showcase_dropin
    writes: list[int] = []

    def third_write_fails(*args: Any, **kwargs: Any) -> None:
        writes.append(1)
        if len(writes) == 3:
            raise OSError("no space left on device")
        real(*args, **kwargs)

    monkeypatch.setattr(box.seam, "write_hindsight_showcase_dropin", third_write_fails)

    result = box.run()

    assert result["status"] == fs.FELL_BACK and result["reason"]
    assert box.dropin_model() == BGE
    assert (box.saved()["model"], box.saved()["source"]) == (
        BGE,
        er.SOURCE_FIRST_START_FALLBACK,
    )
    assert "could not be put back" in fs.describe(result)


def test_a_proved_fallback_whose_record_cannot_be_written_goes_back(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    box.loads = {BGE}

    def refuse(*_a: Any, **_k: Any) -> None:
        raise OSError("read-only file system")

    box.run(wait_s=0.0, healthy=lambda: False, unit_state=lambda: {"active": "active"})
    box.running_on = None
    monkeypatch.setattr(er, "save_embedder_record", refuse)

    result = box.run()

    assert result["status"] == fs.NOT_STARTED
    assert box.dropin_model() == HS and box.saved()["model"] == HS


def test_a_start_command_that_fails_is_left_to_the_installer(box: Box) -> None:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        box.calls.append(list(argv))
        if "restart" in argv and kwargs.get("check"):
            raise subprocess.CalledProcessError(1, argv, "", "unit not found")
        return subprocess.CompletedProcess(argv, 0, "", "")

    box.seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    result = box.run()

    assert result["status"] == fs.STARTING
    assert box.saved()["model"] == HS  # the drop-in on disk carries it


# ── the unit-state reader, the log line and the process exit code ─────────────


@pytest.mark.parametrize(
    ("stdout", "rc", "expected"),
    [
        (
            "ActiveState=active\nNRestarts=0\nMainPID=4242\n",
            0,
            {"active": "active", "restarts": 0, "main_pid": 4242},
        ),
        (
            "NRestarts=3\nMainPID=0\nActiveState=activating\n",
            0,
            {"active": "activating", "restarts": 3, "main_pid": 0},
        ),
        (
            "ActiveState=failed\nNRestarts=junk\nMainPID=junk\n",
            0,
            {"active": "failed", "restarts": 0, "main_pid": None},
        ),
        ("", 1, {"active": None, "restarts": 0, "main_pid": None}),
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

    assert fs.engine_unit_state() == {"active": None, "restarts": 0, "main_pid": None}


# ── is the model host there at all ────────────────────────────────────────────


class _Answer:
    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def _host(monkeypatch: pytest.MonkeyPatch, outcome: Any, unit_env: dict[str, str]) -> list[Any]:
    asked: list[Any] = []

    def urlopen(request: Any, timeout: float) -> _Answer:
        asked.append((request.full_url, request.get_method(), timeout))
        if isinstance(outcome, BaseException):
            raise outcome
        return _Answer()

    monkeypatch.setattr(fs.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ee, "hindsight_unit_env", lambda: unit_env)
    return asked


def test_model_host_reachable_asks_the_hub_once_with_a_short_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _host(monkeypatch, None, {})

    assert fs.model_host_reachable() is True
    assert asked == [("https://huggingface.co", "HEAD", 5.0)]


def test_model_host_reachable_honours_the_units_hf_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _host(monkeypatch, None, {"HF_ENDPOINT": "https://mirror.example/"})

    assert fs.model_host_reachable() is True
    assert asked[0][0] == "https://mirror.example"


def test_an_error_status_still_means_the_host_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    _host(monkeypatch, urllib.error.HTTPError("https://huggingface.co", 503, "busy", {}, None), {})

    assert fs.model_host_reachable() is True


@pytest.mark.parametrize(
    "error",
    [
        __import__("urllib.error").error.URLError("name or service not known"),
        TimeoutError("timed out"),
        ConnectionRefusedError("refused"),
    ],
)
def test_model_host_unreachable(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    _host(monkeypatch, error, {})

    assert fs.model_host_reachable() is False


def test_a_bad_hf_endpoint_falls_back_to_the_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _host(monkeypatch, None, {"HF_ENDPOINT": "file:///etc/passwd"})

    assert fs.model_host_reachable() is True
    assert asked[0][0] == "https://huggingface.co"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (fs.SKIPPED, fs.EXIT_OK),
        (fs.PREPARED, fs.EXIT_OK),
        (fs.STARTED, fs.EXIT_OK),
        (fs.STARTING, fs.EXIT_OK),
        (fs.FELL_BACK, fs.EXIT_FELL_BACK),
        (fs.NOT_STARTED, fs.EXIT_NOT_STARTED),
        (fs.MODELS_UNREACHABLE, fs.EXIT_MODELS_UNREACHABLE),
        (fs.FAILED, fs.EXIT_FAILED),
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
        "reason": None if status == fs.FELL_BACK else "why",
    }
    monkeypatch.setattr(fs, "run_first_start", lambda: result)

    assert fs.main() == code
    line = capsys.readouterr().out.strip()
    assert line.startswith("memory e") and "\n" not in line


def test_the_not_started_line_does_not_blame_the_embedder() -> None:
    line = fs.describe(
        {
            "status": fs.NOT_STARTED,
            "provider": "local",
            "model": HS,
            "reason": "it does not start on the engine default either",
        }
    )

    assert line.startswith("memory engine: it did not start")
    assert f"stay on {HS}" in line


def test_the_fell_back_line_names_the_configured_model() -> None:
    line = fs.describe(
        {
            "status": fs.FELL_BACK,
            "provider": "local",
            "model": BGE,
            "configured": {"provider": "local", "model": HS},
        }
    )

    assert HS in line and BGE in line


def test_main_never_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(fs, "run_first_start", boom)

    assert fs.main() == fs.EXIT_FAILED
    assert "first-start step failed" in capsys.readouterr().out
