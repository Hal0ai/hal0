"""``hal0 comfyui models {list,install,remove}`` and ``hal0 comfyui files``.

Thin clients over ``/api/comfyui/*`` (ADR-0036): the tests replace the three ``api_*``
helpers with an in-memory API and check what the commands send, print and exit with.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import comfyui_commands
from hal0.cli._shared import CliApiError

runner = CliRunner()


def _job(model_id: str, state: str, *, done: float = 0.0, total: float = 8.0, **extra: Any):
    return {
        "id": f"job-{model_id}",
        "model_id": model_id,
        "state": state,
        "bytes_downloaded": round(done * 1e9),
        "bytes_total": round(total * 1e9),
        "error": extra.pop("error", None),
        "error_code": extra.pop("error_code", None),
        **extra,
    }


def _row(model_id: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "id": model_id,
        "label": model_id,
        "task": "txt2img",
        "state": "none",
        "approx_gb": 7.6,
        "missing_gb": 7.6,
        "recommended": False,
        "job": None,
    }
    return {**row, **overrides}


class FakeApi:
    """The slice of hal0-api that the comfyui commands talk to."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.models: list[dict[str, Any]] = []
        self.files: dict[str, Any] = {"root": "/models", "files": []}
        # POST /models/{id}/install: a response, or an exception to raise
        self.installs: dict[str, Any] = {}
        # one jobs listing per GET /jobs; the last one repeats
        self.job_polls: list[list[dict[str, Any]]] = []
        self.removed: dict[str, Any] = {}

    def get(self, path: str, **_kw: Any) -> Any:
        self.calls.append(("GET", path))
        if path == "/api/comfyui/models":
            return {"models": self.models}
        if path == "/api/comfyui/files":
            return self.files
        if path == "/api/comfyui/jobs":
            jobs = self.job_polls.pop(0) if len(self.job_polls) > 1 else self.job_polls[0]
            return {"jobs": jobs}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str, **_kw: Any) -> Any:
        self.calls.append(("POST", path))
        model_id = path.removeprefix("/api/comfyui/models/").removesuffix("/install")
        outcome = self.installs.get(model_id, {"job": _job(model_id, "queued")})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def delete(self, path: str, **_kw: Any) -> Any:
        self.calls.append(("DELETE", path))
        outcome = self.removed.get(path.removeprefix("/api/comfyui/models/"), {"removed": []})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def polls(self) -> int:
        return sum(1 for call in self.calls if call == ("GET", "/api/comfyui/jobs"))


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(comfyui_commands, "api_get", fake.get)
    monkeypatch.setattr(comfyui_commands, "api_post", fake.post)
    monkeypatch.setattr(comfyui_commands, "api_delete", fake.delete)
    monkeypatch.setattr(comfyui_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(comfyui_commands, "_POLL_INTERVAL_S", 0.0)  # no real waiting
    return fake


def _run(*args: str):
    return runner.invoke(comfyui_commands.app, list(args))


def _api_error(status: int, code: str, message: str = "refused") -> CliApiError:
    return CliApiError(f"POST /x → HTTP {status}: {message}", code=code, status=status)


# ── models list ─────────────────────────────────────────────────────────────


def test_cli_models_list_renders_table(api: FakeApi) -> None:
    api.models = [
        _row("sdxl-lightning", recommended=True),
        _row("sdxl-turbo", state="complete", missing_gb=0.0),
        _row("qwen-image", task="txt2img", state="partial", missing_gb=12.5),
        _row("esrgan-4x", task="upscale", missing_gb=0.1),
    ]

    result = _run("models", "list")

    assert result.exit_code == 0, result.output
    lines = {
        m: next(x for x in result.output.splitlines() if m in x)
        for m in ("sdxl-lightning", "sdxl-turbo", "qwen-image", "esrgan-4x")
    }
    assert "*" in lines["sdxl-lightning"] and "7.6" in lines["sdxl-lightning"]
    assert "none" in lines["sdxl-lightning"] and "txt2img" in lines["sdxl-lightning"]
    assert "*" not in lines["sdxl-turbo"]  # only the recommended pick is marked
    assert "complete" in lines["sdxl-turbo"]
    assert "partial" in lines["qwen-image"] and "12.5" in lines["qwen-image"]
    assert "upscale" in lines["esrgan-4x"]
    assert api.calls == [("GET", "/api/comfyui/models")]


def test_cli_models_list_shows_an_active_install(api: FakeApi) -> None:
    api.models = [_row("sdxl-turbo", job=_job("sdxl-turbo", "running", done=3.0, total=6.0))]

    result = _run("models", "list")

    assert "installing" in result.output


def test_cli_models_list_json_is_the_raw_response(api: FakeApi) -> None:
    api.models = [_row("sdxl-lightning", recommended=True)]

    result = _run("models", "list", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"models": api.models}


def test_cli_models_list_exits_1_when_the_api_errors(api: FakeApi, monkeypatch) -> None:
    def boom(_path: str, **_kw: Any) -> Any:
        raise CliApiError("GET /x failed: ConnectError")

    monkeypatch.setattr(comfyui_commands, "api_get", boom)

    result = _run("models", "list")

    assert result.exit_code == 1
    assert "ConnectError" in result.output


def test_cli_commands_stop_early_when_the_api_is_down(api: FakeApi, monkeypatch) -> None:
    monkeypatch.setattr(comfyui_commands, "_api_unreachable", lambda _url: True)

    for args in (["models", "list"], ["models", "install", "sdxl-turbo"], ["files"]):
        assert _run(*args).exit_code == 1
    assert api.calls == []


# ── models install ──────────────────────────────────────────────────────────


def test_cli_install_polls_until_completed(api: FakeApi) -> None:
    api.job_polls = [
        [_job("sdxl-lightning", "running", done=1.9, total=7.6)],
        [_job("sdxl-lightning", "running", done=5.7, total=7.6)],
        [_job("sdxl-lightning", "completed", done=7.6, total=7.6)],
    ]

    result = _run("models", "install", "sdxl-lightning")

    assert result.exit_code == 0, result.output
    assert api.calls[0] == ("POST", "/api/comfyui/models/sdxl-lightning/install")
    assert api.polls() == 3  # it kept polling until the job was terminal
    assert "sdxl-lightning: 25% (1.9/7.6 GB)" in result.output
    assert "sdxl-lightning: 75% (5.7/7.6 GB)" in result.output
    assert "installed" in result.output


def test_cli_install_exit1_on_failed(api: FakeApi) -> None:
    api.job_polls = [
        [_job("sdxl-lightning", "running", done=1.0)],
        [_job("sdxl-lightning", "failed", error="connection reset", error_code="x")],
    ]

    result = _run("models", "install", "sdxl-lightning")

    assert result.exit_code == 1
    assert "connection reset" in result.output


def test_cli_install_exit1_on_cancelled(api: FakeApi) -> None:
    api.job_polls = [[_job("sdxl-lightning", "cancelled")]]

    result = _run("models", "install", "sdxl-lightning")

    assert result.exit_code == 1
    assert "cancelled" in result.output


def test_cli_install_already_installed_is_success(api: FakeApi) -> None:
    api.installs["sdxl-turbo"] = _api_error(409, "comfyui.model_installed", "already installed")

    result = _run("models", "install", "sdxl-turbo")

    assert result.exit_code == 0, result.output
    assert "already installed" in result.output
    assert api.polls() == 0  # nothing started, nothing to follow


def test_cli_install_follows_an_install_that_is_already_running(api: FakeApi) -> None:
    api.installs["sdxl-turbo"] = _api_error(409, "comfyui.install_running", "already running")
    api.job_polls = [[_job("sdxl-turbo", "running", done=2.0)], [_job("sdxl-turbo", "completed")]]

    result = _run("models", "install", "sdxl-turbo")

    assert result.exit_code == 0, result.output
    assert api.polls() == 2


def test_cli_install_post_error_exits_1_but_others_still_finish(api: FakeApi) -> None:
    api.installs["qwen-image"] = _api_error(507, "comfyui.insufficient_disk", "need 51 GB")
    api.job_polls = [[_job("sdxl-turbo", "running", done=1.0)], [_job("sdxl-turbo", "completed")]]

    result = _run("models", "install", "qwen-image", "sdxl-turbo")

    assert result.exit_code == 1
    assert "need 51 GB" in result.output
    assert ("POST", "/api/comfyui/models/sdxl-turbo/install") in api.calls
    assert api.polls() == 2
    assert "sdxl-turbo" in result.output and "installed" in result.output


def test_cli_install_unknown_model_exits_1(api: FakeApi) -> None:
    api.installs["nope"] = _api_error(404, "comfyui.model_unknown", "no image model 'nope'")

    result = _run("models", "install", "nope")

    assert result.exit_code == 1
    assert "no image model 'nope'" in result.output


def test_cli_install_waits_for_every_model_and_ignores_other_jobs(api: FakeApi) -> None:
    stale = _job("qwen-image", "failed", error="from an earlier run")
    api.job_polls = [
        [
            _job("sdxl-turbo", "completed"),
            _job("esrgan-4x", "running", done=0.01, total=0.1),
            stale,
        ],
        [_job("sdxl-turbo", "completed"), _job("esrgan-4x", "completed"), stale],
    ]

    result = _run("models", "install", "sdxl-turbo", "esrgan-4x")

    assert result.exit_code == 0, result.output  # the other model's failed job is not ours
    assert api.polls() == 2
    assert "from an earlier run" not in result.output


def test_cli_install_reports_one_failure_among_several(api: FakeApi) -> None:
    api.job_polls = [
        [_job("sdxl-turbo", "completed"), _job("esrgan-4x", "failed", error="HTTP 404")]
    ]

    result = _run("models", "install", "sdxl-turbo", "esrgan-4x")

    assert result.exit_code == 1
    assert "HTTP 404" in result.output


def test_cli_install_a_vanished_job_is_a_failure(api: FakeApi) -> None:
    api.job_polls = [[]]  # hal0-api restarted: its job table is empty

    result = _run("models", "install", "sdxl-turbo")

    assert result.exit_code == 1
    assert "sdxl-turbo" in result.output


def test_cli_install_progress_is_throttled_to_whole_steps(api: FakeApi) -> None:
    polls = [[_job("sdxl-turbo", "running", done=1.0 + i * 0.01, total=100.0)] for i in range(30)]
    api.job_polls = [*polls, [_job("sdxl-turbo", "completed")]]

    result = _run("models", "install", "sdxl-turbo")

    assert result.exit_code == 0, result.output
    assert result.output.count("sdxl-turbo:") <= 3  # 30 polls within one step print once


def test_cli_install_ctrl_c_leaves_the_install_running(
    api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(_path: str, **_kw: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(comfyui_commands, "api_get", interrupted)

    result = _run("models", "install", "sdxl-turbo")

    assert result.exit_code == 130
    assert "continue on the daemon" in result.output
    assert ("DELETE", "/api/comfyui/models/sdxl-turbo") not in api.calls


def test_cli_install_ctrl_c_while_starting_leaves_started_installs_running(
    api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def second_post_interrupted(path: str, **kw: Any) -> Any:
        if "qwen-image" in path:
            raise KeyboardInterrupt
        return api.post(path, **kw)

    monkeypatch.setattr(comfyui_commands, "api_post", second_post_interrupted)

    result = _run("models", "install", "sdxl-turbo", "qwen-image")

    assert result.exit_code == 130
    assert "Traceback" not in result.output
    assert "already started continue on the daemon" in result.output
    assert api.polls() == 0  # it never reached the follow loop


# ── models remove ───────────────────────────────────────────────────────────


def test_cli_remove_prints_what_was_removed(api: FakeApi) -> None:
    api.removed["sdxl-turbo"] = {
        "removed": ["checkpoints/sd_xl_turbo_1.0_fp16.safetensors", "workflows/sdxl-turbo.json"]
    }

    result = _run("models", "remove", "sdxl-turbo")

    assert result.exit_code == 0, result.output
    assert api.calls == [("DELETE", "/api/comfyui/models/sdxl-turbo")]
    assert "checkpoints/sd_xl_turbo_1.0_fp16.safetensors" in result.output
    assert "workflows/sdxl-turbo.json" in result.output


def test_cli_remove_says_when_nothing_was_there(api: FakeApi) -> None:
    result = _run("models", "remove", "sdxl-turbo")

    assert result.exit_code == 0, result.output
    assert "nothing" in result.output.lower()


def test_cli_remove_exits_1_on_a_running_install(api: FakeApi) -> None:
    api.removed["sdxl-turbo"] = _api_error(409, "comfyui.install_running", "install is running")

    result = _run("models", "remove", "sdxl-turbo")

    assert result.exit_code == 1
    assert "install is running" in result.output


# ── files ───────────────────────────────────────────────────────────────────


def test_cli_files_groups_by_kind(api: FakeApi) -> None:
    api.files = {
        "root": "/srv/comfyui/models",
        "files": [
            {
                "kind": "checkpoint",
                "subdir": "checkpoints",
                "relpath": "sd_xl_turbo_1.0_fp16.safetensors",
                "size_bytes": int(6.9e9),
                "models": ["sdxl-turbo"],
            },
            {
                "kind": "checkpoint",
                "subdir": "checkpoints",
                "relpath": "mine.safetensors",
                "size_bytes": int(2e9),
                "models": [],
            },
            {
                "kind": "vae",
                "subdir": "vae",
                "relpath": "sdxl_vae.safetensors",
                "size_bytes": int(0.3e9),
                "models": ["sdxl-lightning", "sdxl-turbo"],
            },
        ],
    }

    result = _run("files")

    assert result.exit_code == 0, result.output
    out = result.output
    assert "/srv/comfyui/models" in out
    assert out.index("checkpoint") < out.index("sd_xl_turbo_1.0_fp16.safetensors")
    assert out.index("sd_xl_turbo_1.0_fp16.safetensors") < out.index("mine.safetensors")
    assert out.index("mine.safetensors") < out.index("vae") < out.index("sdxl_vae.safetensors")
    owned = next(line for line in out.splitlines() if "sd_xl_turbo" in line)
    assert "sdxl-turbo" in owned and "6.9" in owned
    shared = next(line for line in out.splitlines() if "sdxl_vae" in line)
    assert "sdxl-lightning" in shared and "sdxl-turbo" in shared
    assert api.calls == [("GET", "/api/comfyui/files")]


def test_cli_files_says_when_the_tree_is_empty(api: FakeApi) -> None:
    result = _run("files")

    assert result.exit_code == 0, result.output
    assert "no model files" in result.output.lower()


# ── the command surface ─────────────────────────────────────────────────────


def test_comfyui_app_exposes_only_models_and_files() -> None:
    """The script-driven bulk fetch command is gone; this is everything `hal0 comfyui` has."""
    commands = {cmd.name for cmd in comfyui_commands.app.registered_commands}
    groups = {group.name for group in comfyui_commands.app.registered_groups}
    model_commands = {cmd.name for cmd in comfyui_commands.models_app.registered_commands}

    assert commands == {"files"}
    assert groups == {"models"}
    assert model_commands == {"list", "install", "remove"}
