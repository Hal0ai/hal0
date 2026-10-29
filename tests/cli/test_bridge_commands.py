"""Tests for ``hal0 bridge {add,list,revoke,rotate,scopes}`` (ADR-0043).

The CLI is a thin client over ``/api/bridge/clients``: these tests stub the
``api_*`` helpers in :mod:`hal0.cli.bridge_commands` with fakes that return the
REST response shapes the routes document, and drive the top-level ``hal0``
app so the ``bridge`` registration in :mod:`hal0.cli.main` is covered too.
"""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from hal0.cli import bridge_commands
from hal0.cli._shared import CliApiError
from hal0.cli.main import app

runner = CliRunner()

_ADMIN_NOTE = "operator credential"
_SERVERS = {"agents": "hal0-agents", "memory": "hal0-memory", "admin": "hal0-admin"}
_CODES = {
    "bridge.client_exists": 409,
    "bridge.client_not_found": 404,
    "bridge.client_revoked": 409,
    "bridge.invalid_name": 422,
    "bridge.invalid_scope": 422,
    "bridge.disabled": 404,
}


def _error(code: str, message: str) -> CliApiError:
    """The exception ``_api_request`` raises for the ``{"error": {...}}`` envelope."""
    status = _CODES[code]
    return CliApiError(f"HTTP {status}: {message}", code=code, status=status)


def _client(name: str, scopes: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "note": "",
        "scopes": scopes,
        "created_at": 1_792_000_000.0,
        "created_by": "admin",
        "revoked_at": None,
        "last_seen_at": None,
        **extra,
    }


def _minted(name: str, scopes: list[str], note: str = "") -> dict[str, Any]:
    key = f"hal0bk_{name}.tok3n"
    ordered = [s for s in ("agents", "memory", "admin") if s in scopes]
    lines = [
        {
            "server": _SERVERS[s],
            "scope": s,
            "mcp_url": f"http://box:8080/mcp/{s}",
            "claude_mcp_add": (
                f"claude mcp add --transport http {_SERVERS[s]} http://box:8080/mcp/{s} "
                f'--header "Authorization: Bearer {key}"'
            ),
        }
        for s in ordered
    ]
    return {
        "client": _client(name, ordered, note=note),
        "key": key,
        "onboarding": {"lines": lines},
    }


class FakeApi:
    """Records each call and answers in the documented REST shapes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.existing = {
            "claude-code": _client(
                "claude-code",
                ["agents", "memory"],
                note="devops",
                last_seen_at=1_792_003_600.0,
            ),
            "old-bot": _client("old-bot", ["agents"], revoked_at=1_792_100_000.0),
        }

    def post(self, path: str, *, json: Any = None, **_kw: Any) -> Any:
        self.calls.append(("POST", path, json))
        if path == "/api/bridge/clients":
            # Like the store: no ``scopes`` in the body means the default set.
            asked = json.get("scopes", ["agents", "memory"])
            scopes = ["agents", *[s for s in asked if s != "agents"]]
            return _minted(json["name"], scopes, json["note"])
        name = path.removeprefix("/api/bridge/clients/").removesuffix("/rotate")
        if name not in self.existing:
            raise _error("bridge.client_not_found", f"no bridge client named {name!r}")
        return _minted(name, self.existing[name]["scopes"])

    def get(self, path: str, **_kw: Any) -> Any:
        self.calls.append(("GET", path, None))
        return {"items": list(self.existing.values())}

    def delete(self, path: str, **_kw: Any) -> Any:
        self.calls.append(("DELETE", path, None))
        name = path.removeprefix("/api/bridge/clients/")
        if name not in self.existing:
            raise _error("bridge.client_not_found", f"no bridge client named {name!r}")
        return {"client": {**self.existing[name], "revoked_at": 1_792_200_000.0}}

    def patch(self, path: str, *, json: Any = None, **_kw: Any) -> Any:
        self.calls.append(("PATCH", path, json))
        name = path.removeprefix("/api/bridge/clients/")
        if any(s not in _SERVERS for s in json["scopes"]):
            raise _error("bridge.invalid_scope", "unknown bridge scope 'root'")
        return {"client": _client(name, json["scopes"])}


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(bridge_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(bridge_commands, "api_post", fake.post)
    monkeypatch.setattr(bridge_commands, "api_get", fake.get)
    monkeypatch.setattr(bridge_commands, "api_delete", fake.delete)
    monkeypatch.setattr(bridge_commands, "api_patch", fake.patch)
    return fake


@pytest.fixture
def fake_api_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def disabled(*_a: Any, **_kw: Any) -> Any:
        raise _error("bridge.disabled", "the agent bridge is disabled")

    monkeypatch.setattr(bridge_commands, "_api_unreachable", lambda _url: False)
    for verb in ("api_post", "api_get", "api_delete", "api_patch"):
        monkeypatch.setattr(bridge_commands, verb, disabled)


def _flat(text: str) -> str:
    """Collapse the line wrapping rich applies to stderr messages."""
    return " ".join(text.split())


def test_add_prints_key_once_and_onboarding(fake_api: FakeApi) -> None:
    res = runner.invoke(app, ["bridge", "add", "claude-code", "--note", "devops"])
    assert res.exit_code == 0, res.output
    assert "hal0bk_claude-code." in res.stdout
    assert "Shown once — hal0 stores only a hash." in res.stdout
    assert "claude mcp add --transport http hal0-agents" in res.stdout
    assert "claude mcp add --transport http hal0-memory" in res.stdout
    assert "hal0-admin" not in res.stdout
    assert _ADMIN_NOTE not in res.stdout
    assert fake_api.calls == [
        (
            "POST",
            "/api/bridge/clients",
            # No --scope: the body leaves scopes out and the server's default applies.
            {"name": "claude-code", "note": "devops"},
        )
    ]


def test_add_prints_each_line_verbatim_unwrapped(fake_api: FakeApi) -> None:
    res = runner.invoke(app, ["bridge", "add", "claude-code"])
    assert res.exit_code == 0, res.output
    for line in _minted("claude-code", ["agents", "memory"])["onboarding"]["lines"]:
        # One physical line each: wrapping would break the pasted command.
        assert line["claude_mcp_add"] in res.stdout.splitlines()


def test_add_prints_an_ipv6_url_intact(fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    """rich would read ":100:" in "[2001:db8::100:5]" as the 💯 emoji code."""
    url = "http://[2001:db8::100:5]:8080/mcp/agents/mcp"
    minted = _minted("cc", ["agents"])
    line = minted["onboarding"]["lines"][0]
    line["mcp_url"] = url
    line["claude_mcp_add"] = (
        f'claude mcp add --transport http hal0-agents {url} --header "Authorization: Bearer k"'
    )
    monkeypatch.setattr(bridge_commands, "api_post", lambda *_a, **_kw: minted)
    res = runner.invoke(app, ["bridge", "add", "cc"])
    assert res.exit_code == 0, res.output
    assert line["claude_mcp_add"] in res.stdout.splitlines()
    assert "\N{HUNDRED POINTS SYMBOL}" not in res.stdout


def test_add_admin_scope_prints_warning_and_line(fake_api: FakeApi) -> None:
    res = runner.invoke(app, ["bridge", "add", "ops", "--scope", "admin"])
    assert res.exit_code == 0, res.output
    assert "claude mcp add --transport http hal0-admin" in res.stdout
    assert _ADMIN_NOTE in res.stdout
    assert fake_api.calls[0][2]["scopes"] == ["admin"]


def test_add_scope_is_repeatable(fake_api: FakeApi) -> None:
    res = runner.invoke(
        app, ["bridge", "add", "ops", "--scope", "memory", "--scope", "admin", "--note", "n"]
    )
    assert res.exit_code == 0, res.output
    assert fake_api.calls[0][2] == {"name": "ops", "note": "n", "scopes": ["memory", "admin"]}


def test_add_duplicate_name_exits_one(fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    def exists(*_a: Any, **_kw: Any) -> Any:
        raise _error("bridge.client_exists", "bridge client 'claude-code' already exists")

    monkeypatch.setattr(bridge_commands, "api_post", exists)
    res = runner.invoke(app, ["bridge", "add", "claude-code"])
    assert res.exit_code == 1
    assert "already exists" in _flat(res.output)


def test_scopes_command(fake_api: FakeApi) -> None:
    ok = runner.invoke(app, ["bridge", "scopes", "claude-code", "memory", "admin"])
    assert ok.exit_code == 0, ok.output
    assert fake_api.calls == [
        ("PATCH", "/api/bridge/clients/claude-code", {"scopes": ["memory", "admin"]})
    ]
    bad = runner.invoke(app, ["bridge", "scopes", "claude-code", "root"])
    assert bad.exit_code == 1
    assert "unknown bridge scope" in _flat(bad.output)


def test_scopes_admin_prints_warning(fake_api: FakeApi) -> None:
    res = runner.invoke(app, ["bridge", "scopes", "claude-code", "admin"])
    assert res.exit_code == 0, res.output
    assert _ADMIN_NOTE in res.stdout
    plain = runner.invoke(app, ["bridge", "scopes", "claude-code", "memory"])
    assert _ADMIN_NOTE not in plain.stdout


def test_scopes_requires_a_scope(fake_api: FakeApi) -> None:
    assert runner.invoke(app, ["bridge", "scopes", "claude-code"]).exit_code == 2


def _console_width(monkeypatch: pytest.MonkeyPatch, width: int) -> None:
    """Pin the width of the console the commands print to.

    ``bridge_commands.console`` is built at import time, so a ``COLUMNS`` passed
    to ``runner.invoke`` never reaches it: the width is the terminal's, or 80 off
    a tty (CI). Tests that depend on a width set it here.
    """
    monkeypatch.setattr(bridge_commands, "console", Console(width=width, force_terminal=False))


def test_list_table(fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    _console_width(monkeypatch, 200)
    res = runner.invoke(app, ["bridge", "list"])
    assert res.exit_code == 0, res.output
    assert fake_api.calls == [("GET", "/api/bridge/clients", None)]
    for text in ("claude-code", "agents, memory", "devops", "old-bot", "2026-10-"):
        assert text in res.stdout
    for column in ("name", "scopes", "note", "created", "last seen", "revoked"):
        assert column in res.stdout.lower()


def test_list_keeps_names_and_scopes_whole_at_80_columns(
    fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At 80 columns the note column wraps; a name and the scopes stay on one line."""
    _console_width(monkeypatch, 80)
    res = runner.invoke(app, ["bridge", "list"])
    assert res.exit_code == 0, res.output
    assert "claude-code" in res.stdout
    assert "old-bot" in res.stdout
    assert "agents, memory" in res.stdout


def test_list_empty(fake_api: FakeApi) -> None:
    fake_api.existing.clear()
    res = runner.invoke(app, ["bridge", "list"])
    assert res.exit_code == 0, res.output
    assert "No bridge clients" in res.stdout


def test_list_accepts_iso_timestamps(fake_api: FakeApi) -> None:
    fake_api.existing = {
        "iso": _client("iso", ["agents"], created_at="2026-10-28T12:30:00+00:00"),
    }
    res = runner.invoke(app, ["bridge", "list"])
    assert res.exit_code == 0, res.output
    assert "2026-10-28" in res.stdout


def test_revoke_success_output(fake_api: FakeApi) -> None:
    res = runner.invoke(app, ["bridge", "revoke", "claude-code"])
    assert res.exit_code == 0, res.output
    assert "Revoked bridge client claude-code" in res.stdout
    assert fake_api.calls == [("DELETE", "/api/bridge/clients/claude-code", None)]


def test_revoke_and_rotate_exit_codes(fake_api: FakeApi) -> None:
    missing = runner.invoke(app, ["bridge", "revoke", "nope"])
    assert missing.exit_code == 1
    assert "no bridge client named" in _flat(missing.output)
    assert runner.invoke(app, ["bridge", "rotate", "claude-code"]).exit_code == 0
    assert runner.invoke(app, ["bridge", "rotate", "nope"]).exit_code == 1


def test_rotate_prints_new_key_and_lines(fake_api: FakeApi) -> None:
    res = runner.invoke(app, ["bridge", "rotate", "claude-code"])
    assert res.exit_code == 0, res.output
    assert "hal0bk_claude-code." in res.stdout
    assert "Shown once — hal0 stores only a hash." in res.stdout
    assert "claude mcp add --transport http hal0-memory" in res.stdout
    assert fake_api.calls == [("POST", "/api/bridge/clients/claude-code/rotate", None)]


@pytest.mark.parametrize(
    "argv",
    [
        ["add", "claude-code"],
        ["list"],
        ["revoke", "claude-code"],
        ["rotate", "claude-code"],
        ["scopes", "claude-code", "memory"],
    ],
)
def test_disabled_message(fake_api_disabled: None, argv: list[str]) -> None:
    res = runner.invoke(app, ["bridge", *argv])
    assert res.exit_code == 1
    assert _flat(res.output) == (
        "Error: The agent bridge is disabled: set [bridge] enabled = true "
        "in /etc/hal0/hal0.toml and restart hal0-api."
    )


def test_unreachable_api_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(bridge_commands, "_api_unreachable", lambda _url: True)
    monkeypatch.setattr(bridge_commands, "api_get", lambda *a, **k: called.append("get"))
    res = runner.invoke(app, ["bridge", "list"])
    assert res.exit_code == 1
    assert called == []
