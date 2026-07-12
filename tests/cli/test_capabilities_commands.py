"""Tests for ``hal0 capabilities`` — list/set (live API) + migrate (footgun fix).

``migrate`` used to default to a live write of ``/etc/hal0/capabilities.toml``
(``--dry-run`` was opt-in). That inverted the safety contract every sibling
repair command uses (``hal0 migrate model-layout`` is dry-run by default,
``--apply`` opts into the write). These tests pin the new default: no flags =
preview only, ``--apply`` = write.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.capabilities.config import CapabilityConfig, CapabilitySelection
from hal0.cli import capabilities_commands as cc

runner = CliRunner()


@pytest.fixture
def stub_config(monkeypatch: pytest.MonkeyPatch):
    """Stub load/save so migrate never touches a real capabilities.toml."""
    saved: dict[str, Any] = {"cfg": None}

    def _install(selections: dict[str, dict[str, CapabilitySelection]]) -> CapabilityConfig:
        cfg = CapabilityConfig(selections=selections)
        monkeypatch.setattr(cc, "load_capabilities_config", lambda: cfg)

        def _save(c: CapabilityConfig) -> None:
            saved["cfg"] = c

        monkeypatch.setattr(cc, "save_capabilities_config", _save)
        monkeypatch.setattr(cc, "file_lock", lambda *_a, **_k: _NullLock())
        return cfg

    _install.saved = saved  # type: ignore[attr-defined]
    return _install


class _NullLock:
    def __enter__(self) -> _NullLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _illegal_selection() -> dict[str, dict[str, CapabilitySelection]]:
    # A model no longer in the catalog — models_for_capability() stubbed to
    # return nothing, so this always classifies as "unknown_model". "embed" is
    # a legal child of the "embed" slot (see _CHILD_TO_CAPABILITY).
    return {"embed": {"embed": CapabilitySelection(backend="cpu", provider="", model="ghost")}}


def test_migrate_default_is_dry_run_and_does_not_write(
    stub_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_config(_illegal_selection())
    monkeypatch.setattr(cc, "models_for_capability", lambda *_a, **_k: [])

    result = runner.invoke(cc.app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert "--dry-run" in result.output
    assert stub_config.saved["cfg"] is None  # never written


def test_migrate_apply_writes(stub_config, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_config(_illegal_selection())
    monkeypatch.setattr(cc, "models_for_capability", lambda *_a, **_k: [])

    result = runner.invoke(cc.app, ["migrate", "--apply"])

    assert result.exit_code == 0, result.output
    assert "migrated" in result.output
    assert stub_config.saved["cfg"] is not None


def test_migrate_deprecated_dry_run_flag_is_still_a_noop_preview(
    stub_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old ``--dry-run`` flag is hidden but harmless — still previews."""
    stub_config(_illegal_selection())
    monkeypatch.setattr(cc, "models_for_capability", lambda *_a, **_k: [])

    result = runner.invoke(cc.app, ["migrate", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert stub_config.saved["cfg"] is None


def test_migrate_nothing_to_do(stub_config, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_config({})
    result = runner.invoke(cc.app, ["migrate"])
    assert result.exit_code == 0, result.output
    assert "nothing to migrate" in result.output


# ── list / set — thin clients over the live API ─────────────────────────────


def test_list_renders_selections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_api_unreachable", lambda _u: False)
    monkeypatch.setattr(
        cc,
        "api_get",
        lambda path, **_k: {
            "selections": {
                "embed": {
                    "default": {
                        "backend": "cpu",
                        "provider": "",
                        "model": "bge-small",
                        "enabled": True,
                    }
                }
            }
        },
    )
    result = runner.invoke(cc.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "bge-small" in result.output


def test_list_empty_selections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_api_unreachable", lambda _u: False)
    monkeypatch.setattr(cc, "api_get", lambda path, **_k: {"selections": {}})
    result = runner.invoke(cc.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "no capability selections" in result.output


def test_set_unknown_slot_dies() -> None:
    result = runner.invoke(cc.app, ["set", "not-a-slot", "default", "--model", "x"])
    assert result.exit_code != 0
    assert "unknown capability slot" in result.output


def test_set_requires_at_least_one_field(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(cc.app, ["set", "embed", "embed"])
    assert result.exit_code != 0
    assert "nothing to set" in result.output


def test_set_posts_only_provided_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_api_unreachable", lambda _u: False)
    captured: dict[str, Any] = {}

    def fake_post(path: str, *, json: Any = None, **_k: Any) -> dict:
        captured["path"] = path
        captured["json"] = json
        return {"ok": True, "selection": json}

    monkeypatch.setattr(cc, "api_post", fake_post)
    result = runner.invoke(cc.app, ["set", "embed", "embed", "--model", "bge-small"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/capabilities/embed/embed"
    assert captured["json"] == {"model": "bge-small"}
