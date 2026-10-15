"""``hal0 profile`` — thin CLI over /api/profiles (#1796).

There was no CLI surface for profiles at all before this file; ``list`` /
``show`` cover the read half of ``/api/profiles`` every slot TOML
references.
"""

from __future__ import annotations

import json
import re

import pytest
from rich.console import Console
from typer.testing import CliRunner

from hal0.cli import profile_commands

runner = CliRunner()


def _stub_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_commands, "_api_unreachable", lambda _url: False)


def test_list_json_emits_raw_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"name": "agent", "ctx_size": 65536, "vision": "auto", "mtp": "auto"}]
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(profile_commands.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_list_renders_table_with_names(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "name": "agent",
            "ctx_size": 65536,
            "enable_thinking": True,
            "vision": "auto",
            "mtp": "off",
            "runner": None,
            "intent": "Long-context agent",
            "tps": 52.8,
            "used_by": ["primary"],
        },
        {
            "name": "chat",
            "ctx_size": None,
            "enable_thinking": None,
            "vision": "off",
            "mtp": "auto",
            "runner": "vulkan",
            "intent": None,
            "tps": None,
            "used_by": [],
        },
    ]
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)
    # The module console sizes itself once at import; CI has no terminal.
    monkeypatch.setattr(profile_commands, "console", Console(width=200))

    result = runner.invoke(profile_commands.app, ["list"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "agent" in result.output
    assert "chat" in result.output
    assert "65,536" in result.output
    assert "native" in result.output  # ctx_size unset
    assert "primary" in result.output
    assert "device" not in result.output.lower()


def test_list_renders_a_quarantined_row_with_its_error(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"name": "agent", "ctx_size": 65536, "vision": "auto", "mtp": "auto", "used_by": []},
        {
            "name": "broken",
            "quarantined": True,
            "error": "not_a_field: Extra inputs are not permitted",
            "seed": False,
            "used_by": ["primary"],
        },
    ]
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)
    monkeypatch.setattr(profile_commands, "console", Console(width=200))

    result = runner.invoke(profile_commands.app, ["list"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "broken" in result.output
    assert "quarantined" in result.output
    assert "Extra inputs are not permitted" in result.output


def test_list_empty_shows_dim_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: [])

    result = runner.invoke(profile_commands.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "No profiles available" in result.output


def test_show_json_emits_raw_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"name": "rocm", "ctx_size": 8192, "used_by": ["primary"]}
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(profile_commands.app, ["show", "rocm", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_show_renders_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "name": "agent",
        "seed": True,
        "ctx_size": 65536,
        "enable_thinking": None,
        "vision": "auto",
        "mtp": "off",
        "runner": None,
        "intent": "Long-context agent",
        "tps": 52.8,
        "rtf": None,
        "used_by": ["primary"],
        "resolved_flags": "-fa on",
    }
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(profile_commands.app, ["show", "agent"])
    assert result.exit_code == 0, result.output
    assert "agent" in result.output
    assert "ctx_size" in result.output and "65536" in result.output
    assert "primary" in result.output
    assert "-fa on" in result.output


# ── create / edit: the profile owns the context window (ADR-0029) ────────────


class _Api:
    """Records the writes and serves a canned profile catalog."""

    def __init__(self, profiles: dict[str, dict]) -> None:
        self.profiles = profiles
        self.posts: list[tuple[str, dict]] = []
        self.puts: list[tuple[str, dict]] = []

    def get(self, path: str, **_k):
        name = path.rsplit("/", 1)[-1]
        if name not in self.profiles:
            raise profile_commands.CliApiError(f"profile {name!r} not found")
        return self.profiles[name]

    def post(self, path: str, *, json=None, **_k):
        self.posts.append((path, json))
        return {"name": json["name"], **json}

    def put(self, path: str, *, json=None, **_k):
        self.puts.append((path, json))
        return {"name": path.rsplit("/", 1)[-1], **json}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> _Api:
    fake = _Api(
        {
            "agent": {
                "name": "agent",
                "seed": True,
                "flags": "-fa on -ctk q8_0",
                "ctx_size": 65536,
                "enable_thinking": True,
                "vision": "auto",
                "mtp": "auto",
                "runner": None,
                "intent": "Long-context agent",
            },
            "mine": {"name": "mine", "seed": False, "flags": "", "ctx_size": 4096},
        }
    )
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", fake.get)
    monkeypatch.setattr(profile_commands, "api_post", fake.post)
    monkeypatch.setattr(profile_commands, "api_put", fake.put)
    return fake


def test_create_sets_ctx_size(api: _Api) -> None:
    result = runner.invoke(
        profile_commands.app,
        [
            "create",
            "tiny",
            "--ctx-size",
            "8192",
            "--flags",
            "-b 512",
            "--no-thinking",
            "--mtp",
            "off",
        ],
    )
    assert result.exit_code == 0, result.output
    assert api.posts == [
        (
            "/api/profiles",
            {
                "name": "tiny",
                "flags": "-b 512",
                "ctx_size": 8192,
                "enable_thinking": False,
                "mtp": "off",
            },
        )
    ]


@pytest.mark.parametrize("bad", ["127", "0", "-5", str(2**24 + 1), "abc"])
def test_create_rejects_ctx_size_out_of_range(api: _Api, bad: str) -> None:
    result = runner.invoke(profile_commands.app, ["create", "tiny", "--ctx-size", bad])
    assert result.exit_code == 2, result.output
    assert api.posts == []


@pytest.mark.parametrize("ok", ["128", str(2**24)])
def test_create_accepts_the_range_edges(api: _Api, ok: str) -> None:
    result = runner.invoke(profile_commands.app, ["create", "edge", "--ctx-size", ok])
    assert result.exit_code == 0, result.output
    assert api.posts[0][1]["ctx_size"] == int(ok)


def test_create_from_a_seed_duplicates_it_and_records_provenance(api: _Api) -> None:
    result = runner.invoke(
        profile_commands.app, ["create", "agent-small", "--from", "agent", "--ctx-size", "16384"]
    )
    assert result.exit_code == 0, result.output
    body = api.posts[0][1]
    assert body["name"] == "agent-small"
    assert body["cloned_from"] == "agent"
    assert body["flags"] == "-fa on -ctk q8_0"
    assert body["enable_thinking"] is True
    assert body["ctx_size"] == 16384  # the option wins over the copied value


def test_create_from_a_missing_profile_fails_without_posting(api: _Api) -> None:
    result = runner.invoke(profile_commands.app, ["create", "x", "--from", "nope"])
    assert result.exit_code == 1
    assert "not found" in result.output
    assert api.posts == []


def test_create_rejects_a_bad_vision_value(api: _Api) -> None:
    result = runner.invoke(profile_commands.app, ["create", "x", "--vision", "maybe"])
    assert result.exit_code == 2, result.output
    assert api.posts == []


def test_edit_a_custom_profile_sends_a_sparse_body(api: _Api) -> None:
    result = runner.invoke(profile_commands.app, ["edit", "mine", "--ctx-size", "32768"])
    assert result.exit_code == 0, result.output
    assert api.puts == [("/api/profiles/mine", {"ctx_size": 32768})]
    assert "next restart" in result.output


def test_edit_clear_ctx_size_sends_an_explicit_null(api: _Api) -> None:
    result = runner.invoke(profile_commands.app, ["edit", "mine", "--clear-ctx-size"])
    assert result.exit_code == 0, result.output
    assert api.puts == [("/api/profiles/mine", {"ctx_size": None})]


def test_edit_a_seed_is_refused_with_the_duplicate_hint(api: _Api) -> None:
    result = runner.invoke(profile_commands.app, ["edit", "agent", "--ctx-size", "32768"])
    assert result.exit_code == 1
    assert "read-only" in result.output
    plain = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.output).split())
    assert "create <new-name> --from agent" in plain
    assert api.puts == []


def test_edit_with_nothing_to_change_is_a_usage_error(api: _Api) -> None:
    result = runner.invoke(profile_commands.app, ["edit", "mine"])
    assert result.exit_code == 2
    assert "No fields provided" in result.output
    assert api.puts == []


def test_edit_ctx_size_and_clear_are_mutually_exclusive(api: _Api) -> None:
    result = runner.invoke(
        profile_commands.app, ["edit", "mine", "--ctx-size", "8192", "--clear-ctx-size"]
    )
    assert result.exit_code == 2, result.output
    assert api.puts == []


def test_edit_surfaces_an_api_refusal(api: _Api, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(path: str, *, json=None, **_k):
        raise profile_commands.CliApiError("slot.hardware_flag_denied: -ngl belongs on the slot")

    monkeypatch.setattr(profile_commands, "api_put", refuse)
    result = runner.invoke(profile_commands.app, ["edit", "mine", "--flags", "-ngl 99"])
    assert result.exit_code == 1
    assert "slot.hardware_flag_denied" in result.output
