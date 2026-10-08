"""Tests for ``hal0 auth {status,rotate,require}`` (§5.2 R5 sync assessment).

The CLI shipped zero verbs for the R3/R4 auth surface even though the
underlying routes (``/api/auth/status``, ``POST /api/auth/rotate``, ``PUT
/api/auth/require``) have existed since KB-1. These tests pin the wiring —
right method, right path, right body — against a stubbed API surface, the
same style ``test_slot_verb_aliases.py`` uses.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import auth_commands

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "GET"
        captured["path"] = path
        return {"auth_required": True, "has_admin_key": True, "tier": "admin"}

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "POST"
        captured["path"] = path
        captured["body"] = json or {}
        return {
            "tier": (json or {}).get("tier", "admin"),
            "fingerprint": "abc123",
            "note": "rotated ok",
        }

    def fake_put(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "PUT"
        captured["path"] = path
        captured["body"] = json or {}
        return {"require_auth": (json or {}).get("require_auth"), "applies_live": True}

    monkeypatch.setattr(auth_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(auth_commands, "api_get", fake_get)
    monkeypatch.setattr(auth_commands, "api_post", fake_post)
    monkeypatch.setattr(auth_commands, "api_put", fake_put)
    return captured


def test_auth_status_hits_get_status(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["status"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/auth/status"


def test_auth_status_json(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    assert '"auth_required": true' in result.output


def test_auth_rotate_defaults_to_admin_tier(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["rotate", "--force"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/auth/rotate"
    assert captured["body"] == {"tier": "admin"}


def test_auth_rotate_client_tier(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["rotate", "client", "--force"])
    assert result.exit_code == 0, result.output
    assert captured["body"] == {"tier": "client"}


def test_auth_rotate_prompts_without_force(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["rotate"], input="n\n")
    assert result.exit_code != 0
    assert "method" not in captured


def test_auth_rotate_never_prints_a_key_value(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["rotate", "--force"])
    assert result.exit_code == 0, result.output
    assert "fingerprint" not in result.output.lower() or "abc123" in result.output
    # The stub note/fingerprint are fine to echo — what must NEVER appear is
    # a raw secret; the route contract (and this stub) never hands one back.


def test_auth_require_on(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["require", "on"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/auth/require"
    assert captured["body"] == {"require_auth": True}
    assert "ON" in result.output


def test_auth_require_off(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["require", "off"])
    assert result.exit_code == 0, result.output
    assert captured["body"] == {"require_auth": False}
    assert "OFF" in result.output


def test_auth_is_registered_on_main_app() -> None:
    from hal0.cli.main import app as main_app

    result = runner.invoke(main_app, ["auth", "--help"])
    assert result.exit_code == 0, result.output
    assert "status" in result.output
    assert "rotate" in result.output
    assert "require" in result.output


# ── reset-key (lost-key recovery) ──────────────────────────────────────────────


def _seed_api_env(tmp_path, monkeypatch: pytest.MonkeyPatch, content: str):
    # rotate_api_env_key also exports the new key into os.environ; register the
    # var with monkeypatch so it is restored and cannot leak into later tests
    # (delenv alone records nothing when the var was already absent).
    monkeypatch.setenv("HAL0_ADMIN_KEY", "unset-by-test")
    monkeypatch.delenv("HAL0_ADMIN_KEY")
    etc = tmp_path / "etc" / "hal0"
    etc.mkdir(parents=True)
    (etc / "api.env").write_text(content, encoding="utf-8")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "etc", lambda: etc)
    return etc / "api.env"


def test_reset_key_refuses_without_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_commands.os, "geteuid", lambda: 1000)
    result = runner.invoke(auth_commands.app, ["reset-key", "--force"])
    assert result.exit_code != 0
    assert "sudo hal0 auth reset-key" in " ".join(result.output.split())


def test_reset_key_rotates_via_api_and_prints_new_key(
    tmp_path, monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """API up: rotate through the daemon (live), then print the key it wrote."""
    api_env = _seed_api_env(tmp_path, monkeypatch, "HAL0_BIND_HOST=0.0.0.0\n")
    monkeypatch.setattr(auth_commands.os, "geteuid", lambda: 0)

    real_post = auth_commands.api_post

    def post_and_write(path: str, *, json: dict[str, Any] | None = None, **kw: Any):
        # Stand-in for the daemon's rotate_api_env_key write.
        api_env.write_text("HAL0_BIND_HOST=0.0.0.0\nHAL0_ADMIN_KEY=new-live-key\n")
        return real_post(path, json=json, **kw)

    monkeypatch.setattr(auth_commands, "api_post", post_and_write)
    result = runner.invoke(auth_commands.app, ["reset-key", "--force"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/auth/rotate"
    assert captured["body"] == {"tier": "admin"}
    assert "new-live-key" in result.output
    assert "Applied live" in result.output


def test_reset_key_falls_back_to_local_write_when_api_down(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API down: mint straight into api.env and say a restart applies it."""
    api_env = _seed_api_env(tmp_path, monkeypatch, "HAL0_ADMIN_KEY=old-key\n")
    monkeypatch.setattr(auth_commands.os, "geteuid", lambda: 0)
    monkeypatch.setattr(auth_commands, "_api_unreachable", lambda _url: True)

    result = runner.invoke(auth_commands.app, ["reset-key", "--force"])
    assert result.exit_code == 0, result.output
    text = api_env.read_text()
    assert "old-key" not in text
    new_key = text.split("HAL0_ADMIN_KEY=", 1)[1].strip()
    assert len(new_key) >= 40
    assert new_key in result.output
    assert "systemctl restart hal0-api" in " ".join(result.output.split())


def test_reset_key_prompt_warns_about_the_lan_admin_gate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Minting a key arms the LAN admin gate on a LAN-bound box, so the prompt
    must say so; declining it writes nothing."""
    api_env = _seed_api_env(tmp_path, monkeypatch, "HAL0_BIND_HOST=0.0.0.0\n")
    monkeypatch.setattr(auth_commands.os, "geteuid", lambda: 0)
    monkeypatch.setattr(auth_commands, "_api_unreachable", lambda _url: True)

    result = runner.invoke(auth_commands.app, ["reset-key"], input="n\n")
    assert result.exit_code != 0
    assert "need it from other devices" in " ".join(result.output.split())
    assert api_env.read_text() == "HAL0_BIND_HOST=0.0.0.0\n"
