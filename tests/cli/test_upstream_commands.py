"""Tests for ``hal0 upstream`` command request shapes.

The API's create/update bodies are ``extra="forbid"`` — a CLI flag that
maps to a wrong field name 422s against the live server. These tests pin
every body the CLI sends to the exact field names the routes accept
(regression for the first cut, which sent ``openai_base_url``/``kind``/
``allow``/``deny``/``api_key`` and failed on every write).
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import upstream_commands

runner = CliRunner()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the API client surface and capture calls."""
    calls: dict[str, Any] = {"posts": [], "patches": [], "deletes": [], "gets": []}

    monkeypatch.setattr(upstream_commands, "_api_unreachable", lambda _url: False)

    def fake_get(path: str, **_kw: Any) -> Any:
        calls["gets"].append(path)
        if path == "/api/upstreams":
            return calls.get("list_response", [])
        return calls.get(
            "get_response",
            {"name": path.rsplit("/", 1)[-1], "auth_value_env": "OPENROUTER_API_KEY"},
        )

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        calls["posts"].append((path, json))
        if path == "/api/upstreams":
            return {**(json or {}), "auth_value_env": (json or {}).get("auth_value_env", "")}
        return {"ok": True}

    def fake_patch(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        calls["patches"].append((path, json))
        return json or {}

    def fake_delete(path: str, **_kw: Any) -> dict[str, Any]:
        calls["deletes"].append(path)
        return {"ok": True, "removed_from_toml": True}

    monkeypatch.setattr(upstream_commands, "api_get", fake_get)
    monkeypatch.setattr(upstream_commands, "api_post", fake_post)
    monkeypatch.setattr(upstream_commands, "api_patch", fake_patch)
    monkeypatch.setattr(upstream_commands, "api_delete", fake_delete)
    return calls


def test_create_sends_url_not_openai_base_url(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app,
        ["create", "corp", "--url", "https://llm.corp/v1/"],
    )
    assert result.exit_code == 0, result.output
    path, body = api["posts"][0]
    assert path == "/api/upstreams"
    assert body["url"] == "https://llm.corp/v1"  # trailing slash stripped
    assert "openai_base_url" not in body
    assert "kind" not in body  # server forces remote; body forbids the field


def test_create_with_catalog_and_flags(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app,
        ["create", "orx", "--catalog", "openrouter", "--disabled", "--hide-models"],
    )
    assert result.exit_code == 0, result.output
    _, body = api["posts"][0]
    assert body == {
        "name": "orx",
        "advertise_models": False,
        "enabled": False,
        "catalog_id": "openrouter",
    }


def test_create_with_api_key_chains_credentials(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app,
        [
            "create",
            "orx",
            "--url",
            "https://openrouter.ai/api/v1",
            "--auth-env",
            "OPENROUTER_API_KEY",
            "--api-key",
            "sk-test",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(api["posts"]) == 2
    cred_path, cred_body = api["posts"][1]
    assert cred_path == "/api/providers/orx/credentials"
    # The credentials route contract: {key: ENV_NAME, value: secret}.
    assert cred_body == {"key": "OPENROUTER_API_KEY", "value": "sk-test"}


def test_update_sends_spec_filter_fields(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app,
        [
            "update",
            "orx",
            "--include",
            "anthropic/*",
            "--include",
            "google/*",
            "--exclude",
            "*:free",
            "--model",
            "deepseek/deepseek-r1",
        ],
    )
    assert result.exit_code == 0, result.output
    _, body = api["patches"][0]
    assert body["model_filters"] == {
        "models": ["deepseek/deepseek-r1"],
        "include": ["anthropic/*", "google/*"],
        "exclude": ["*:free"],
    }
    assert "allow" not in str(body)
    assert "deny" not in str(body)


def test_update_clear_filters_sends_all_empty(api: dict[str, Any]) -> None:
    result = runner.invoke(upstream_commands.app, ["update", "orx", "--clear-filters"])
    assert result.exit_code == 0, result.output
    _, body = api["patches"][0]
    assert body["model_filters"] == {"models": [], "include": [], "exclude": []}


def test_update_toggles_and_structural(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app,
        ["update", "orx", "--disabled", "--url", "http://proxy/v1", "--timeout", "60"],
    )
    assert result.exit_code == 0, result.output
    _, body = api["patches"][0]
    assert body == {"url": "http://proxy/v1", "timeout_seconds": 60.0, "enabled": False}


def test_update_nothing_is_noop(api: dict[str, Any]) -> None:
    result = runner.invoke(upstream_commands.app, ["update", "orx"])
    assert result.exit_code == 0
    assert "Nothing to update" in result.output
    assert api["patches"] == []


def test_advertise_on_patches_true_and_echoes_state(api: dict[str, Any]) -> None:
    result = runner.invoke(upstream_commands.app, ["advertise", "orx", "on"])
    assert result.exit_code == 0, result.output
    path, body = api["patches"][0]
    assert path == "/api/upstreams/orx"
    assert body == {"advertise_models": True}
    assert "advertised" in result.output


def test_advertise_off_patches_false_and_echoes_state(api: dict[str, Any]) -> None:
    result = runner.invoke(upstream_commands.app, ["advertise", "orx", "off"])
    assert result.exit_code == 0, result.output
    _, body = api["patches"][0]
    assert body == {"advertise_models": False}
    assert "hidden" in result.output


def test_advertise_rejects_bad_state(api: dict[str, Any]) -> None:
    result = runner.invoke(upstream_commands.app, ["advertise", "orx", "maybe"])
    assert result.exit_code != 0
    assert api["patches"] == []


def test_advertise_unknown_upstream_errors_clearly(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def raising_patch(path: str, **_kw: Any) -> dict[str, Any]:
        raise upstream_commands.CliApiError("404 upstream 'nope' not found")

    monkeypatch.setattr(upstream_commands, "api_patch", raising_patch)
    result = runner.invoke(upstream_commands.app, ["advertise", "nope", "off"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_delete_requires_confirm_and_force_skips(api: dict[str, Any]) -> None:
    result = runner.invoke(upstream_commands.app, ["delete", "orx"], input="n\n")
    assert result.exit_code == 0
    assert api["deletes"] == []
    result = runner.invoke(upstream_commands.app, ["delete", "orx", "--force"])
    assert result.exit_code == 0, result.output
    assert api["deletes"] == ["/api/upstreams/orx"]


def test_set_credentials_resolves_env_var_and_sends_key_value(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app,
        ["set-credentials", "openrouter", "--key", "sk-test"],
    )
    assert result.exit_code == 0, result.output
    # Resolved the env-var name from GET /api/upstreams/{name}.
    assert api["gets"] == ["/api/upstreams/openrouter"]
    path, body = api["posts"][0]
    assert path == "/api/providers/openrouter/credentials"
    assert body == {"key": "OPENROUTER_API_KEY", "value": "sk-test"}


def test_set_credentials_explicit_env_var_skips_lookup(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app,
        ["set-credentials", "corp", "--key", "sk-x", "--env-var", "CORP_KEY"],
    )
    assert result.exit_code == 0, result.output
    assert api["gets"] == []
    _, body = api["posts"][0]
    assert body == {"key": "CORP_KEY", "value": "sk-x"}


def test_list_renders_url_and_filter_summary(api: dict[str, Any]) -> None:
    # Short values throughout — Rich truncates wide cells at the 80-col
    # test terminal, so realistic-length URLs can't be asserted verbatim.
    api["list_response"] = [
        {
            "name": "orx",
            "kind": "remote",
            "url": "http://gw/v1",
            "enabled": True,
            "advertise_models": True,
            "auth_value_env": "ORX_KEY",
            "auth_key_present": True,
            "model_filters": {"models": [], "include": ["anthropic/*"], "exclude": ["*:free"]},
        }
    ]
    result = runner.invoke(upstream_commands.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "orx" in result.output
    assert "include:1" in result.output
    assert "exclude:1" in result.output


def test_test_command_reports_latency_and_fails_on_unreachable(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def ok_post(path: str, **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "latency_ms": 123.4, "models_count": 42}

    monkeypatch.setattr(upstream_commands, "api_post", ok_post)
    result = runner.invoke(upstream_commands.app, ["test", "orx"])
    assert result.exit_code == 0
    assert "123 ms" in result.output and "42 models" in result.output

    def bad_post(path: str, **_kw: Any) -> dict[str, Any]:
        return {"ok": False, "error": "401 unauthorized"}

    monkeypatch.setattr(upstream_commands, "api_post", bad_post)
    result = runner.invoke(upstream_commands.app, ["test", "orx"])
    assert result.exit_code == 1
    assert "401" in result.output
