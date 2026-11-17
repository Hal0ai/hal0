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
        ["credentials", "set", "openrouter", "--key", "sk-test"],
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
        ["credentials", "set", "corp", "--key", "sk-x", "--env-var", "CORP_KEY"],
    )
    assert result.exit_code == 0, result.output
    assert api["gets"] == []
    _, body = api["posts"][0]
    assert body == {"key": "CORP_KEY", "value": "sk-x"}


# ── `credentials set` (the flat `set-credentials` verb was removed) ──


def test_credentials_set_is_the_canonical_command(api: dict[str, Any]) -> None:
    """`upstream credentials set` is the only spelling."""
    result = runner.invoke(
        upstream_commands.app,
        ["credentials", "set", "openrouter", "--key", "sk-test"],
    )
    assert result.exit_code == 0, result.output
    assert api["gets"] == ["/api/upstreams/openrouter"]
    path, body = api["posts"][0]
    assert path == "/api/providers/openrouter/credentials"
    assert body == {"key": "OPENROUTER_API_KEY", "value": "sk-test"}
    assert "deprecat" not in result.output.lower()


def test_flat_set_credentials_verb_is_gone(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app,
        ["set-credentials", "openrouter", "--key", "sk-test"],
    )
    assert result.exit_code != 0
    assert api["posts"] == []


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


# ── per-model management ──────────────────────────────────────────────────

_LISTING: dict[str, Any] = {
    "name": "openrouter",
    "curated": True,
    "fetched_at": 1700000000.0,
    "stale": False,
    "warning": None,
    "models": [
        {
            "id": "qwen/qwen3-coder",
            "canonical_id": "openrouter/qwen/qwen3-coder",
            "enabled": True,
            "type": "chat",
            "context": 262144,
            "price_in": 0.22,
            "price_out": 0.95,
            "price_unit": "Mtok",
            "discovered": True,
            "recommended": False,
            "in_use_by": [],
        },
        {
            "id": "meta/llama-3.3:free",
            "canonical_id": "openrouter/meta/llama-3.3:free",
            "enabled": False,
            "type": "chat",
            "context": None,
            "price_in": None,
            "price_out": None,
            "price_unit": "Mtok",
            "discovered": False,
            "recommended": False,
            "in_use_by": [],
        },
    ],
}


def test_models_prints_table(api: dict[str, Any]) -> None:
    api["get_response"] = _LISTING
    result = runner.invoke(upstream_commands.app, ["models", "openrouter"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert api["gets"] == ["/api/upstreams/openrouter/models"]
    assert "qwen/qwen3-coder" in result.output
    assert "meta/llama-3.3:free" in result.output
    assert "262144" in result.output
    assert "0.22" in result.output


def test_models_json_is_raw_listing(api: dict[str, Any]) -> None:
    import json

    api["get_response"] = _LISTING
    result = runner.invoke(upstream_commands.app, ["models", "openrouter", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _LISTING


def test_models_uncurated_row_says_so(api: dict[str, Any]) -> None:
    api["get_response"] = {
        **_LISTING,
        "curated": False,
        "stale": True,
        "warning": "the provider returned no models; check the url and key, then refresh again",
    }
    result = runner.invoke(upstream_commands.app, ["models", "openrouter"])
    assert result.exit_code == 0, result.output
    assert "not converted" in result.output
    assert "check the url and key" in result.output


def test_models_protected_upstream_is_not_called_unconverted(api: dict[str, Any]) -> None:
    api["get_response"] = {**_LISTING, "name": "hermes", "curated": False, "protected": True}
    result = runner.invoke(upstream_commands.app, ["models", "hermes"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "not converted" not in result.output
    assert "read-only" in result.output
    assert "hal0 upstream refresh" not in result.output


def test_enable_patches_the_row_with_only_enabled(api: dict[str, Any]) -> None:
    result = runner.invoke(upstream_commands.app, ["enable", "openrouter", "qwen/qwen3-coder"])
    assert result.exit_code == 0, result.output
    # Slashes stay path separators for the server's ``:path`` converter.
    assert api["patches"] == [
        ("/api/upstreams/openrouter/models/qwen/qwen3-coder", {"enabled": True})
    ]


def test_disable_encodes_odd_characters_but_keeps_slashes(api: dict[str, Any]) -> None:
    result = runner.invoke(
        upstream_commands.app, ["disable", "openrouter", "meta/llama-3.3:free?x#y"]
    )
    assert result.exit_code == 0, result.output
    path, body = api["patches"][0]
    assert path == "/api/upstreams/openrouter/models/meta/llama-3.3:free%3Fx%23y"
    assert body == {"enabled": False}


def test_enable_surfaces_the_server_error(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.cli._shared import CliApiError

    def boom(path: str, **_kw: Any) -> Any:
        raise CliApiError("upstream openrouter is not converted yet", status=409)

    monkeypatch.setattr(upstream_commands, "api_patch", boom)
    result = runner.invoke(upstream_commands.app, ["enable", "openrouter", "m"])
    assert result.exit_code == 1
    assert "not converted yet" in result.output


def test_refresh_posts_and_shows_warning(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    api["refresh_response"] = {
        **_LISTING,
        "warning": "the provider returned no models; no key is configured",
        "stale": True,
    }

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> Any:
        api["posts"].append((path, json))
        return api["refresh_response"]

    monkeypatch.setattr(upstream_commands, "api_post", fake_post)
    result = runner.invoke(upstream_commands.app, ["refresh", "openrouter"])
    assert result.exit_code == 0, result.output
    assert api["posts"] == [("/api/upstreams/openrouter/refresh", None)]
    assert "no key is configured" in result.output
    assert "qwen/qwen3-coder" in result.output


def test_probe_reads_key_only_from_named_env_var(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROBE_TEST_KEY", "sk-secret-value")
    seen: list[tuple[str, Any]] = []

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> Any:
        seen.append((path, json))
        return {
            "ok": True,
            "status": 200,
            "latency_ms": 41.0,
            "models": [
                {
                    "id": "m1",
                    "canonical_id": None,
                    "type": "chat",
                    "type_source": "provider",
                    "context": 8192,
                    "price_in": None,
                    "price_out": None,
                    "price_unit": "Mtok",
                    "recommended": False,
                }
            ],
            "rate_limit": {
                "limit_requests": None,
                "remaining_requests": None,
                "limit_tokens": None,
                "remaining_tokens": None,
            },
            "error": None,
        }

    monkeypatch.setattr(upstream_commands, "api_post", fake_post)
    result = runner.invoke(
        upstream_commands.app,
        ["probe", "--catalog-id", "openrouter", "--key-env", "PROBE_TEST_KEY"],
    )
    assert result.exit_code == 0, result.output
    assert seen == [
        ("/api/providers/probe", {"catalog_id": "openrouter", "key": "sk-secret-value"})
    ]
    assert "sk-secret-value" not in result.output
    assert "m1" in result.output


def test_probe_fails_clearly_when_the_env_var_is_unset(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PROBE_TEST_KEY", raising=False)
    result = runner.invoke(
        upstream_commands.app,
        ["probe", "--url", "https://api.example.com/v1", "--key-env", "PROBE_TEST_KEY"],
    )
    assert result.exit_code == 1
    assert "PROBE_TEST_KEY" in result.output
    assert api["posts"] == []


def test_probe_has_no_option_that_takes_a_key_value() -> None:
    result = runner.invoke(
        upstream_commands.app, ["probe", "--url", "https://api.example.com/v1", "--key", "x"]
    )
    assert result.exit_code != 0


def test_probe_needs_a_target(api: dict[str, Any]) -> None:
    result = runner.invoke(upstream_commands.app, ["probe"])
    assert result.exit_code == 1
    assert api["posts"] == []


def test_probe_reports_failure_with_exit_1(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        upstream_commands,
        "api_post",
        lambda path, **_kw: {
            "ok": False,
            "status": 401,
            "latency_ms": 12.0,
            "models": [],
            "rate_limit": {},
            "error": "provider rejected the key (401)",
        },
    )
    result = runner.invoke(upstream_commands.app, ["probe", "--url", "https://api.example.com/v1"])
    assert result.exit_code == 1
    assert "rejected the key" in result.output
