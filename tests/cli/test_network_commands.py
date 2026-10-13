"""Tests for ``hal0 network {public-url,origins}`` (#2382).

The verbs edit hal0.toml ``[network]`` through the API (the locked config
transaction), never api.env: ``public-url`` through ``PUT /api/settings``,
``origins add|remove`` through ``POST /api/settings/network``, which edits
the list on the server. Pinned against a stubbed API surface, the same style
as ``test_auth_commands.py``; the server side is covered in
``tests/api/test_allowed_origins_network.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import network_commands
from hal0.cli.main import app as root_app

runner = CliRunner()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "network": {"public_url": None, "extra_origins": ["http://box.lan:8080"]},
        "puts": [],
        "posts": [],
    }

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        assert path == "/api/settings"
        return {"security": {}, "network": dict(state["network"])}

    def fake_put(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        assert path == "/api/settings"
        state["puts"].append(json)
        state["network"].update((json or {}).get("network", {}))
        return {"network": dict(state["network"])}

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        # Mirrors the server: removals and additions applied to the current list.
        assert path == "/api/settings/network"
        body = json or {}
        state["posts"].append(body)
        before = list(state["network"]["extra_origins"])
        kept = [o for o in before if o not in body.get("remove_origins", [])]
        after = [*kept, *(o for o in body.get("add_origins", []) if o not in kept)]
        state["network"]["extra_origins"] = after
        changes = [] if after == before else [{"path": "network.extra_origins"}]
        return {"network": dict(state["network"]), "_hal0": {"changeset": {"changes": changes}}}

    monkeypatch.setattr(network_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(network_commands, "api_get", fake_get)
    monkeypatch.setattr(network_commands, "api_put", fake_put)
    monkeypatch.setattr(network_commands, "api_post", fake_post)
    return state


def test_registered_on_root_app() -> None:
    result = runner.invoke(root_app, ["network", "--help"])
    assert result.exit_code == 0, result.output
    assert "public-url" in result.output
    assert "origins" in result.output


def test_public_url_set_normalises_and_puts(api: dict[str, Any]) -> None:
    result = runner.invoke(network_commands.app, ["public-url", "set", "https://Hal0.Example.com/"])
    assert result.exit_code == 0, result.output
    assert api["puts"] == [{"network": {"public_url": "https://hal0.example.com"}}]
    assert "no restart" in result.output


def test_public_url_set_rejects_invalid_without_calling_api(api: dict[str, Any]) -> None:
    result = runner.invoke(network_commands.app, ["public-url", "set", "hal0.example.com"])
    assert result.exit_code == 1
    assert "http:// or https://" in result.output
    assert api["puts"] == []


def test_public_url_clear_sends_null(api: dict[str, Any]) -> None:
    result = runner.invoke(network_commands.app, ["public-url", "clear"])
    assert result.exit_code == 0, result.output
    assert api["puts"] == [{"network": {"public_url": None}}]


def test_origins_add_sends_one_normalised_entry(api: dict[str, Any]) -> None:
    """The CLI never round-trips the whole list: one entry, applied server-side."""
    result = runner.invoke(
        network_commands.app, ["origins", "add", "HTTPS://Proxy.Example.com:443/"]
    )
    assert result.exit_code == 0, result.output
    assert api["posts"] == [{"add_origins": ["https://proxy.example.com"]}]
    assert api["puts"] == []
    assert api["network"]["extra_origins"] == ["http://box.lan:8080", "https://proxy.example.com"]
    assert "Added" in result.output


def test_origins_add_existing_reports_noop(api: dict[str, Any]) -> None:
    result = runner.invoke(network_commands.app, ["origins", "add", "http://box.lan:8080/"])
    assert result.exit_code == 0, result.output
    assert "already" in result.output
    assert api["network"]["extra_origins"] == ["http://box.lan:8080"]


@pytest.mark.parametrize("bad", ["box.lan:8080", "https://x.example.com/path", "ftp://x"])
def test_origins_add_rejects_malformed(api: dict[str, Any], bad: str) -> None:
    result = runner.invoke(network_commands.app, ["origins", "add", bad])
    assert result.exit_code == 1
    assert api["posts"] == []
    assert api["puts"] == []


def test_origins_remove(api: dict[str, Any]) -> None:
    result = runner.invoke(network_commands.app, ["origins", "remove", "http://BOX.lan:8080"])
    assert result.exit_code == 0, result.output
    assert api["posts"] == [{"remove_origins": ["http://box.lan:8080"]}]
    assert api["network"]["extra_origins"] == []

    missing = runner.invoke(network_commands.app, ["origins", "remove", "http://other:1"])
    assert missing.exit_code == 0
    assert "nothing to remove" in missing.output


def test_origins_list_includes_public_url_origin(api: dict[str, Any]) -> None:
    api["network"]["public_url"] = "https://hal0.example.com:8443/app"
    result = runner.invoke(network_commands.app, ["origins", "list"])
    assert result.exit_code == 0, result.output
    lines = [line.strip() for line in result.output.splitlines()]
    assert lines == ["https://hal0.example.com:8443  (from public_url)", "http://box.lan:8080"]


def test_origins_list_on_box_without_network_section(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(network_commands, "api_get", lambda _p, **_k: {"security": {}})
    result = runner.invoke(network_commands.app, ["origins", "list"])
    assert result.exit_code == 0, result.output
    assert "No [network] origins" in result.output
