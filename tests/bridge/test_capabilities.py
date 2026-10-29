"""The sanitised capabilities view behind ``agent_capabilities`` (ADR-0043 decision 10).

``capabilities_view`` reads a Hermes profile's ``config.yaml`` and reports what
the agent can do right now: toolsets, MCP server *names*, skills directories,
enabled platforms, model aliases and the approvals mode. It is read-only and is
the one place a remote machine learns about the box's agent configuration, so
the tests pin what it must never carry: the fixture config holds a bearer header
under an MCP server, a URL with userinfo and an API key, and none of them may
appear in the output, in whatever form.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from hal0.bridge.capabilities import CAPABILITY_KEYS, capabilities_view

SECRET = "nlQ_0123456789abcdefSECRET"

#: The profile API key hal0 holds; it authenticates the version probe and must never be echoed.
API_KEY = "hermes-profile-key-0123456789abcdef"

DEFAULT_CONFIG: dict[str, Any] = {
    "toolsets": ["hermes-cli", "memory"],
    "agent": {"disabled_toolsets": ["a2a", "terminal"], "max_turns": 90},
    "model": {"default": "qwen-27b", "provider": "custom", "api_key": SECRET},
    "model_aliases": {
        "agent": {
            "model": "Qwen3.8-27B",
            "provider": "custom",
            "base_url": f"http://user:{SECRET}@192.0.2.10:8080/v1",
            "api_key": SECRET,
        },
        "fast": {"model": "Qwen3.5-4B", "provider": "custom"},
    },
    "mcp_servers": {
        "hal0-memory": {
            "url": "http://127.0.0.1:8080/mcp/memory/mcp",
            "headers": {"Authorization": f"Bearer {SECRET}"},
        },
        "hal0-admin": {
            "url": "http://127.0.0.1:8080/mcp/admin/mcp",
            "headers": {"Authorization": f"Bearer {SECRET}"},
            "env": {"HAL0_ADMIN_KEY": SECRET},
        },
    },
    "skills": {"external_dirs": ["/etc/hal0/agent-skills", "/var/lib/hal0/skills"]},
    "gateway": {
        "platforms": {
            "api_server": {"enabled": True, "extra": {"key": SECRET}},
            "telegram": {"enabled": False, "token": SECRET},
        }
    },
    "approvals": {"mode": "smart"},
}


def _write(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")


@pytest.fixture
def tmp_hermes_home(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    _write(home / "config.yaml", DEFAULT_CONFIG)
    return home


def _no_gateway(_url: str, _headers: Callable[[], Mapping[str, str]]) -> Any:
    return None


def test_view_has_exactly_the_documented_keys(tmp_hermes_home: Path) -> None:
    view = capabilities_view(tmp_hermes_home, "default", fetch_json=_no_gateway)
    assert set(view) == set(CAPABILITY_KEYS)
    assert list(CAPABILITY_KEYS) == [
        "agent",
        "profile",
        "api_server_version",
        "model_aliases",
        "toolsets_enabled",
        "toolsets_disabled",
        "mcp_servers",
        "skills_dirs",
        "platforms_enabled",
        "approvals_mode",
    ]


def test_capabilities_view_has_no_secret_shaped_values(tmp_hermes_home: Path) -> None:
    view = capabilities_view(tmp_hermes_home, "default", fetch_json=_no_gateway)
    flat = json.dumps(view)
    assert "Bearer" not in flat and "nlQ_" not in flat and SECRET not in flat
    assert "192.0.2.10" not in flat and "127.0.0.1" not in flat  # no urls at all
    assert "Authorization" not in flat and "api_key" not in flat and "headers" not in flat
    assert view["mcp_servers"] == ["hal0-admin", "hal0-memory"]
    assert view["toolsets_disabled"] and "a2a" in view["toolsets_disabled"]


def test_view_reports_the_default_profile(tmp_hermes_home: Path) -> None:
    view = capabilities_view(tmp_hermes_home, "default", fetch_json=_no_gateway)
    assert view["agent"] == "hermes" and view["profile"] == "default"
    assert view["toolsets_enabled"] == ["hermes-cli", "memory"]
    assert view["toolsets_disabled"] == ["a2a", "terminal"]
    assert view["skills_dirs"] == ["/etc/hal0/agent-skills", "/var/lib/hal0/skills"]
    assert view["platforms_enabled"] == ["api_server"]
    assert view["approvals_mode"] == "smart"
    assert view["model_aliases"] == {
        "agent": {"model": "Qwen3.8-27B", "provider": "custom"},
        "fast": {"model": "Qwen3.5-4B", "provider": "custom"},
    }


def test_a_named_profile_reports_only_its_own_file(tmp_hermes_home: Path) -> None:
    """Hermes merges a profile's config.yaml over its built-in defaults only, never over the
    default profile's file, so a field the profile does not set is null ("Hermes default")."""
    _write(
        tmp_hermes_home / "profiles" / "coder" / "config.yaml",
        {
            "toolsets": ["hermes-cli"],
            "mcp_servers": {"hal0-memory": {"url": "http://x", "headers": {"A": SECRET}}},
            "approvals": {"mode": "manual"},
        },
    )
    view = capabilities_view(tmp_hermes_home, "coder", fetch_json=_no_gateway)
    assert view["agent"] == "hermes/coder" and view["profile"] == "coder"
    # set by the profile's own file
    assert view["toolsets_enabled"] == ["hermes-cli"]
    assert view["mcp_servers"] == ["hal0-memory"]
    assert view["approvals_mode"] == "manual"
    # not set by it: null, never the default profile's value
    assert view["toolsets_disabled"] is None
    assert view["skills_dirs"] is None
    assert view["model_aliases"] is None
    assert view["platforms_enabled"] is None
    assert SECRET not in json.dumps(view)


def test_a_profile_without_a_config_file_reports_nothing_of_the_default(
    tmp_hermes_home: Path,
) -> None:
    (tmp_hermes_home / "profiles" / "bare").mkdir(parents=True)
    view = capabilities_view(tmp_hermes_home, "bare", fetch_json=_no_gateway)
    assert all(
        view[key] is None
        for key in CAPABILITY_KEYS
        if key not in ("agent", "profile", "api_server_version")
    )


def test_the_default_profile_reports_its_own_unset_fields_as_null(tmp_path: Path) -> None:
    home = tmp_path / "hermes"
    _write(home / "config.yaml", {"toolsets": ["hermes-cli"]})
    view = capabilities_view(home, "default", fetch_json=_no_gateway)
    assert view["toolsets_enabled"] == ["hermes-cli"]
    assert view["toolsets_disabled"] is None and view["mcp_servers"] is None
    assert view["approvals_mode"] is None and view["platforms_enabled"] is None


def test_a_set_but_empty_field_is_an_empty_list_not_null(tmp_path: Path) -> None:
    home = tmp_path / "hermes"
    _write(
        home / "config.yaml",
        {
            "toolsets": [],
            "mcp_servers": {},
            "gateway": {"platforms": {"telegram": {"enabled": False}}},
        },
    )
    view = capabilities_view(home, "default", fetch_json=_no_gateway)
    assert view["toolsets_enabled"] == [] and view["mcp_servers"] == []
    assert view["platforms_enabled"] == []


def test_unreadable_or_missing_config_gives_an_empty_view(tmp_path: Path) -> None:
    view = capabilities_view(tmp_path / "nowhere", "default", fetch_json=_no_gateway)
    assert view["toolsets_enabled"] is None and view["toolsets_disabled"] is None
    assert view["mcp_servers"] is None and view["model_aliases"] is None
    assert view["platforms_enabled"] is None and view["skills_dirs"] is None
    assert view["approvals_mode"] is None and view["api_server_version"] is None


def test_approvals_mode_off_is_reported_as_off(tmp_hermes_home: Path) -> None:
    # YAML 1.1 reads a bare ``off`` as False; Hermes normalises it back to "off".
    cfg = dict(DEFAULT_CONFIG)
    cfg["approvals"] = {"mode": False}
    _write(tmp_hermes_home / "config.yaml", cfg)
    view = capabilities_view(tmp_hermes_home, "default", fetch_json=_no_gateway)
    assert view["approvals_mode"] == "off"


def test_secret_shaped_names_and_values_are_dropped(tmp_hermes_home: Path) -> None:
    cfg = dict(DEFAULT_CONFIG)
    cfg["mcp_servers"] = {
        "fine": {"url": "http://x"},
        "HAL0_ADMIN_KEY": {"url": "http://x"},  # a sensitive-keyed name
        f"Bearer {SECRET}": {"url": "http://x"},  # a secret-shaped name
    }
    cfg["model_aliases"] = {
        "ok": {"model": "m", "provider": "custom"},
        "api_key": {"model": "m", "provider": "custom"},
        "leaky": {"model": f"Bearer {SECRET}", "provider": "custom"},
    }
    cfg["skills"] = {"external_dirs": ["/var/lib/hal0/skills", f"/x/Bearer {SECRET}"]}
    _write(tmp_hermes_home / "config.yaml", cfg)
    view = capabilities_view(tmp_hermes_home, "default", fetch_json=_no_gateway)
    assert view["mcp_servers"] == ["fine"]
    assert list(view["model_aliases"]) == ["ok"]
    assert view["skills_dirs"] == ["/var/lib/hal0/skills"]
    assert SECRET not in json.dumps(view)


def test_version_comes_from_health_detailed(tmp_hermes_home: Path) -> None:
    seen: list[str] = []

    def fetch(url: str, _headers: Callable[[], Mapping[str, str]]) -> Any:
        seen.append(url)
        return {"status": "ok", "version": "0.22.4", "token": SECRET}

    view = capabilities_view(
        tmp_hermes_home, "default", fetch_json=fetch, read_api_key=lambda: API_KEY
    )
    assert view["api_server_version"] == "0.22.4"
    assert seen == ["http://127.0.0.1:8642/health/detailed"]
    assert SECRET not in json.dumps(view)

    seen.clear()
    capabilities_view(tmp_hermes_home, "coder", fetch_json=fetch, read_api_key=lambda: API_KEY)
    assert seen == ["http://127.0.0.1:8642/p/coder/health/detailed"]


def test_version_probe_sends_the_profile_key_and_never_echoes_it(tmp_hermes_home: Path) -> None:
    """The route answers 401 without the key; the key is only ever a request header."""
    sent: list[Mapping[str, str]] = []

    def fetch(_url: str, headers: Callable[[], Mapping[str, str]]) -> Any:
        sent.append(dict(headers()))
        return {"version": "0.22.4"}

    view = capabilities_view(
        tmp_hermes_home, "coder", fetch_json=fetch, read_api_key=lambda: API_KEY
    )
    assert sent == [{"Authorization": f"Bearer {API_KEY}"}]
    assert API_KEY not in json.dumps(view)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_no_key_means_no_request_and_no_version(tmp_hermes_home: Path, key: str | None) -> None:
    calls: list[str] = []

    def fetch(url: str, _headers: Callable[[], Mapping[str, str]]) -> Any:
        calls.append(url)
        return {"version": "0.22.4"}

    view = capabilities_view(tmp_hermes_home, "default", fetch_json=fetch, read_api_key=lambda: key)
    assert view["api_server_version"] is None
    assert calls == []  # without the key the route would only answer 401
    view = capabilities_view(tmp_hermes_home, "default", fetch_json=fetch, read_api_key=None)
    assert view["api_server_version"] is None and calls == []


def test_the_real_fetch_sends_the_bearer_header(
    tmp_hermes_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    seen: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        seen["url"], seen["headers"] = url, kwargs.get("headers")
        seen["trust_env"] = kwargs.get("trust_env")
        return httpx.Response(200, json={"version": "0.22.4"})

    monkeypatch.setattr(httpx, "get", fake_get)
    view = capabilities_view(tmp_hermes_home, "default", read_api_key=lambda: API_KEY)
    assert view["api_server_version"] == "0.22.4"
    assert seen["url"] == "http://127.0.0.1:8642/health/detailed"
    assert seen["headers"] == {"Authorization": f"Bearer {API_KEY}"}
    assert seen["trust_env"] is False  # the profile key never goes through a proxy


def test_the_key_never_goes_through_a_proxy_for_a_hermes_beyond_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Hermes listening on all interfaces is not loopback; HTTP_PROXY must still be ignored."""
    import httpx

    home = tmp_path / "hermes"
    cfg = dict(DEFAULT_CONFIG)
    cfg["gateway"] = {
        "platforms": {"api_server": {"enabled": True, "host": "192.0.2.9", "port": 8642}}
    }
    _write(home / "config.yaml", cfg)
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.200:3128")
    seen: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        seen["url"], seen["trust_env"] = url, kwargs.get("trust_env")
        return httpx.Response(200, json={"version": "0.22.4"})

    monkeypatch.setattr(httpx, "get", fake_get)
    capabilities_view(home, "default", read_api_key=lambda: API_KEY)
    assert seen["url"].startswith("http://192.0.2.9:8642/")
    assert seen["trust_env"] is False


@pytest.mark.parametrize(
    "answer",
    [
        None,
        {},
        {"version": 7},
        {"version": ""},
        ["x"],
        "up",
        {"version": SECRET},  # not version-shaped: never echoed, even unrecognised as a secret
        {"version": "0.22.4 and more words"},
        # a 40-hex secret fullmatched the old suffix rule; a version needs a dotted core
        {"version": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"},
        {"version": "1"},
        {"version": "0.22.4-" + "a" * 25},  # and is short
    ],
)
def test_unreadable_version_is_none(tmp_hermes_home: Path, answer: Any) -> None:
    view = capabilities_view(
        tmp_hermes_home, "default", fetch_json=lambda _u, _h: answer, read_api_key=lambda: API_KEY
    )
    assert view["api_server_version"] is None


@pytest.mark.parametrize(
    "version",
    ["0.22.4", "v0.22.4", "0.22.4-rc1", "0.22.4+local.3", "0.22.4+g4d04303", "1.2", "1.2.3.4"],
)
def test_a_version_shaped_answer_is_reported(tmp_hermes_home: Path, version: str) -> None:
    view = capabilities_view(
        tmp_hermes_home,
        "default",
        fetch_json=lambda _u, _h: {"version": version},
        read_api_key=lambda: API_KEY,
    )
    assert view["api_server_version"] == version


def test_unreachable_gateway_gives_none_not_an_error(tmp_hermes_home: Path) -> None:
    def boom(_url: str, _headers: Callable[[], Mapping[str, str]]) -> Any:
        raise OSError("connection refused to http://127.0.0.1:8642")

    view = capabilities_view(
        tmp_hermes_home, "default", fetch_json=boom, read_api_key=lambda: API_KEY
    )
    assert view["api_server_version"] is None
    assert "8642" not in json.dumps(view)


def test_default_fetch_against_a_closed_port_is_none(tmp_path: Path) -> None:
    """With no injected fetcher the real probe runs; a closed port is ``None``, within 3 s."""
    home = tmp_path / "hermes"
    cfg = dict(DEFAULT_CONFIG)
    cfg["gateway"] = {
        "platforms": {"api_server": {"enabled": True, "host": "127.0.0.1", "port": 1}}
    }
    _write(home / "config.yaml", cfg)
    assert (
        capabilities_view(home, "default", read_api_key=lambda: API_KEY)["api_server_version"]
        is None
    )


@pytest.mark.parametrize("profile", ["../etc", "a/b", "", "x" * 80, "with space"])
def test_a_profile_name_that_is_not_a_url_segment_is_refused(
    tmp_hermes_home: Path, profile: str
) -> None:
    with pytest.raises(ValueError):
        capabilities_view(tmp_hermes_home, profile, fetch_json=_no_gateway)
