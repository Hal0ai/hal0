"""Unreadable paths must stay local to a doctor row (#2620)."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer
from rich.console import Console

from hal0.cli import doctor_all as da
from hal0.cli.doctor_verify import Check


def _denied(*args: Any, **kwargs: Any) -> Any:
    raise PermissionError("permission denied")


@pytest.mark.parametrize(
    ("check", "kwargs", "key"),
    [
        (da.check_hardware_freshness, {}, "hardware_freshness"),
        (da.check_model_store, {"models": [{"path": "model.gguf"}]}, "models"),
        (da.check_hal0_target, {}, "hal0_target"),
        (da.check_hermes_mcp_config_auth, {"auth": None}, "hermes_mcp_auth"),
        (da.check_hermes_command_approvals, {}, "hermes_command_approvals"),
        (da.check_hermes_anchor_window, {}, "hermes_anchor_window"),
        (da.check_openwebui_mcp, {}, "openwebui_mcp"),
        (da.check_hindsight_llm_auth, {"auth": None}, "hindsight_llm_auth"),
        (
            da.check_hal0_linger,
            {"probe": lambda: SimpleNamespace(enabled=True)},
            "hal0_linger",
        ),
    ],
)
@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_existence_errors_warn(
    monkeypatch: pytest.MonkeyPatch,
    check: Callable[..., Check],
    kwargs: dict[str, Any],
    key: str,
    error: type[OSError],
) -> None:
    def denied(path: Path) -> bool:
        raise error("cannot traverse parent")

    monkeypatch.setattr(Path, "exists", denied)
    row = check(**kwargs)
    assert row.key == key
    assert row.status == "warn"
    assert "as this user" in row.detail
    assert "sudo hal0 doctor all" in row.detail


@pytest.mark.parametrize(
    ("check", "kwargs"),
    [
        (da.check_hermes_mcp_config_auth, {"auth": {"auth_required": True}}),
        (da.check_hermes_anchor_window, {}),
        (da.check_hermes_default_memory, {}),
        (da.check_hermes_brain_memory, {}),
        (da.check_ui_dist, {}),
        (da.check_api_env_entries, {}),
        (da.check_hindsight_llm_auth, {"auth": {"auth_required": True}}),
    ],
)
def test_read_errors_warn(
    monkeypatch: pytest.MonkeyPatch, check: Callable[..., Check], kwargs: dict[str, Any]
) -> None:
    monkeypatch.setattr(Path, "exists", lambda path: True)
    monkeypatch.setattr(Path, "read_text", _denied)
    row = check(**kwargs)
    assert row.status == "warn"
    assert "sudo hal0 doctor all" in row.detail


def test_secret_stat_error_is_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "stat", _denied)
    row = da.check_secret_file_modes()
    assert row.status == "warn"
    assert "sudo hal0 doctor all" in row.detail


def test_openwebui_client_existence_error_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = tmp_path / "unit.service"
    unit.touch()
    original = Path.exists

    def exists(path: Path) -> bool:
        return original(path) if path == unit else _denied()

    monkeypatch.setattr(Path, "exists", exists)
    row = da.check_openwebui_mcp(unit_path=unit, enabled=True, db_path=tmp_path / "bridge.db")
    assert row.status == "warn"
    assert "sudo hal0 doctor all" in row.detail


def test_anchor_helper_error_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from hal0.agents import anchor_window

    config = tmp_path / "config.yaml"
    config.write_text("model:\n  default: agent\n")
    monkeypatch.setattr(anchor_window, "resolve_anchor_window", _denied)
    row = da.check_hermes_anchor_window(
        config_path=config, fetch=lambda *args: (None, []), venv_python=tmp_path / "python"
    )
    assert row.status == "warn"
    assert "sudo hal0 doctor all" in row.detail


def test_approvals_helper_error_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda path: True)
    row = da.check_hermes_command_approvals(status_fn=_denied)
    assert row.status == "warn"
    assert "sudo hal0 doctor all" in row.detail


def test_anchor_interpreter_existence_error_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  default: agent\n")
    original = Path.exists

    def exists(path: Path) -> bool:
        return original(path) if path == config else _denied()

    monkeypatch.setattr(Path, "exists", exists)
    row = da.check_hermes_anchor_window(config_path=config, venv_python=tmp_path / "python")
    assert row.status == "warn"
    assert "sudo hal0 doctor all" in row.detail


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    ("status", "critical", "code"), [("pass", False, 0), ("fail", False, 1), ("fail", True, 2)]
)
def test_all_finishes_and_preserves_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    json_output: bool,
    status: str,
    critical: bool,
    code: int,
) -> None:
    from hal0.agents import hermes_provision as hp

    real_checks = {
        "check_hermes_mcp_config_auth",
        "check_hermes_command_approvals",
        "check_hermes_anchor_window",
    }
    for name in vars(da):
        if name.startswith("check_") and name not in real_checks:
            monkeypatch.setattr(
                da, name, lambda *args, **kwargs: Check("other", "Other row", "pass", "checked")
            )
    monkeypatch.setattr(hp, "HERMES_HOME_DEFAULT", tmp_path)
    monkeypatch.setattr(Path, "exists", _denied)
    monkeypatch.setattr(
        da,
        "gather_payloads",
        lambda base: dict.fromkeys(
            ("health", "urls", "system", "capabilities", "memory", "services")
        ),
    )
    monkeypatch.setattr(
        da,
        "build_checks",
        lambda **kwargs: [Check("api", "API", status, "existing result", critical=critical)],
    )
    monkeypatch.setattr(da, "_get_any", lambda *args, **kwargs: None)
    monkeypatch.setattr(da, "console", Console(width=240))
    with pytest.raises(typer.Exit) as exc:
        da.doctor_all_cmd(json_output=json_output)
    assert exc.value.exit_code == code
    output = capsys.readouterr().out
    assert "sudo hal0 doctor all" in output
    assert "Other row" in output
    assert "existing result" in output
    assert "Traceback" not in output
