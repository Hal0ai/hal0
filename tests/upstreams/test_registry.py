"""Unit tests for hal0.upstreams.registry.

Covers:
  - CRUD (add / get / remove / update / list / from_slot)
  - Auth header dispatch by auth_style
  - TIER1 constants and the per-slot override loader for hardware.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hal0.upstreams.registry import (
    TIER1_BACKOFF_JITTER_FRAC,
    TIER1_BACKOFF_STEPS,
    TIER1_TOTAL_GRACE_S,
    Upstream,
    UpstreamAlreadyExists,
    UpstreamAuthUnconfigured,
    UpstreamNotFound,
    UpstreamRegistry,
)


def _slot(name: str = "primary", port: int = 8081, **kw: Any) -> Upstream:
    defaults: dict[str, Any] = dict(
        name=name,
        kind="slot",
        url=f"http://127.0.0.1:{port}/v1",
        slot_name=name,
        warmup_strategy="ondemand",
        ttl_warmup_seconds=TIER1_TOTAL_GRACE_S,
    )
    defaults.update(kw)
    return Upstream(**defaults)


def _remote(name: str = "openrouter", **kw: Any) -> Upstream:
    defaults: dict[str, Any] = dict(
        name=name,
        kind="remote",
        url="https://openrouter.ai/api/v1",
        auth_value_env="OPENROUTER_API_KEY",
    )
    defaults.update(kw)
    return Upstream(**defaults)


def test_upstream_dynamic_fields_default() -> None:
    from hal0.config.schema import UpstreamEntry
    from hal0.upstreams.registry import upstream_from_entry

    for u in (
        _remote(),
        upstream_from_entry(UpstreamEntry(name="x", kind="remote", url="http://h/v1")),
    ):
        assert u.managed_by is None
        assert u.pinned is False
        assert u.owned_models == ()
        assert u.stream_idle_timeout_s is None
        assert u.stream_total_timeout_s is None


# ── CRUD ──────────────────────────────────────────────────────────────────────


def test_add_and_get() -> None:
    r = UpstreamRegistry()
    u = _slot()
    r.add(u)
    assert r.get("primary") is u
    assert r.get("missing") is None


def test_add_duplicate_raises() -> None:
    r = UpstreamRegistry()
    r.add(_slot())
    with pytest.raises(UpstreamAlreadyExists):
        r.add(_slot())


def test_upsert_overwrites() -> None:
    r = UpstreamRegistry()
    r.add(_slot())
    r.upsert(_slot(port=8090))
    assert r.get("primary").url.endswith(":8090/v1")


def test_remove() -> None:
    r = UpstreamRegistry()
    r.add(_slot())
    assert r.remove("primary") is True
    assert r.remove("primary") is False
    assert r.get("primary") is None


def test_update_merges_fields() -> None:
    r = UpstreamRegistry()
    r.add(_slot())
    new = r.update("primary", warmup_strategy="always")
    assert new.warmup_strategy == "always"
    assert r.get("primary").warmup_strategy == "always"


def test_update_missing_raises() -> None:
    r = UpstreamRegistry()
    with pytest.raises(UpstreamNotFound):
        r.update("ghost", warmup_strategy="none")


def test_list() -> None:
    r = UpstreamRegistry()
    r.add(_remote("openai"))
    r.add(_slot("primary"))
    r.add(_remote("anthropic"))
    names = [u.name for u in r.list()]
    assert set(names) == {"openai", "primary", "anthropic"}


def test_from_slot() -> None:
    r = UpstreamRegistry()
    r.add(_slot("embed", port=8082))
    r.add(_remote())
    assert r.from_slot("embed").name == "embed"
    assert r.from_slot("nope") is None


# ── Auth headers ──────────────────────────────────────────────────────────────


def test_auth_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "sk-abc")
    r = UpstreamRegistry()
    u = _remote(auth_style="bearer", auth_value_env="MY_KEY")
    headers = r.auth_headers(u)
    assert headers == {"Authorization": "Bearer sk-abc"}


def test_auth_bearer_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credentialed style with an unset env var raises instead of
    dispatching unauthenticated (#1513)."""
    monkeypatch.delenv("MY_KEY", raising=False)
    r = UpstreamRegistry()
    u = _remote(auth_style="bearer", auth_value_env="MY_KEY")
    with pytest.raises(UpstreamAuthUnconfigured):
        r.auth_headers(u)


def test_auth_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
    r = UpstreamRegistry()
    u = _remote(
        name="anthropic",
        url="https://api.anthropic.com/v1",
        auth_style="anthropic",
        auth_value_env="ANTHROPIC_API_KEY",
    )
    headers = r.auth_headers(u)
    assert headers["x-api-key"] == "sk-ant-xyz"
    assert headers["anthropic-version"] == "2023-06-01"


def test_auth_none() -> None:
    r = UpstreamRegistry()
    u = _remote(auth_style="none")
    assert r.auth_headers(u) == {}


def test_auth_unknown_style_emits_no_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "google_query" was retired in #1513 (it emitted no header, so calls
    dispatched unauthenticated); an unknown style with a key present still
    falls through to no headers rather than raising here — the schema
    validator is the gate for unknown styles."""
    monkeypatch.setenv("G_KEY", "foo")
    r = UpstreamRegistry()
    u = _remote(auth_style="mystery", auth_value_env="G_KEY")
    assert r.auth_headers(u) == {}


# ── TIER1: adaptive backoff ───────────────────────────────────────────────────


def test_tier1_constants_match_spec() -> None:
    """TIER1: probe intervals (0.5, 1, 2, 5, 10), grace 180s, jitter ±25%."""
    assert TIER1_BACKOFF_STEPS == (0.5, 1.0, 2.0, 5.0, 10.0)
    assert TIER1_BACKOFF_JITTER_FRAC == 0.25
    assert TIER1_TOTAL_GRACE_S == 180.0


def test_load_slot_overrides_from_hardware_json(tmp_path: Path) -> None:
    """TIER1 per-slot override: backoff_steps + warmup_grace_s from hardware.json."""
    hw = tmp_path / "hardware.json"
    hw.write_text(
        json.dumps(
            {
                "slots": {
                    "primary": {
                        "backoff_steps": [0.1, 0.2, 0.4],
                        "warmup_grace_s": 30,
                    }
                }
            }
        )
    )
    r = UpstreamRegistry()
    r.load_slot_overrides(hw)
    u = _slot()
    assert r._effective_backoff_steps(u) == (0.1, 0.2, 0.4)
    assert r._effective_total_grace_s(u) == 30.0


def test_load_slot_overrides_missing_file(tmp_path: Path) -> None:
    r = UpstreamRegistry()
    r.load_slot_overrides(tmp_path / "nope.json")
    u = _slot()
    assert r._effective_backoff_steps(u) == TIER1_BACKOFF_STEPS
    assert r._effective_total_grace_s(u) == TIER1_TOTAL_GRACE_S


def test_load_slot_overrides_malformed(tmp_path: Path) -> None:
    hw = tmp_path / "hardware.json"
    hw.write_text("not json at all {{{")
    r = UpstreamRegistry()
    r.load_slot_overrides(hw)
    u = _slot()
    # Falls back to the defaults baked into the Upstream.
    assert r._effective_backoff_steps(u) == TIER1_BACKOFF_STEPS


# ── Persistent mutations (upstreams.toml round-trip) ─────────────────────────


def _write_upstreams_toml(body: str) -> Path:
    from hal0.config import paths

    etc = paths.etc()
    etc.mkdir(parents=True, exist_ok=True)
    p = etc / "upstreams.toml"
    p.write_text(body)
    return p


_OPENROUTER_TOML = """
[[upstream]]
name = "openrouter"
kind = "remote"
url = "https://openrouter.ai/api/v1"
auth_value_env = "OPENROUTER_API_KEY"
"""


def _registry_with_openrouter() -> UpstreamRegistry:
    r = UpstreamRegistry()
    r.add(_remote("openrouter"))
    return r


class TestApplyPersistentPatch:
    def test_persists_fields_to_toml(self, tmp_hal0_home: str) -> None:
        import tomllib

        path = _write_upstreams_toml(_OPENROUTER_TOML)
        r = _registry_with_openrouter()

        merged = r.apply_persistent_patch(
            "openrouter",
            {
                "enabled": False,
                "model_filters": {"include": ["anthropic/*"], "exclude": ["*:free"]},
            },
        )
        assert merged.enabled is False
        assert merged.model_filters is not None
        assert merged.model_filters.include == ("anthropic/*",)

        on_disk = tomllib.loads(path.read_text())["upstream"][0]
        assert on_disk["enabled"] is False
        assert on_disk["model_filters"]["include"] == ["anthropic/*"]
        assert on_disk["model_filters"]["exclude"] == ["*:free"]

    def test_empty_filters_clear_to_none(self, tmp_hal0_home: str) -> None:
        import tomllib

        path = _write_upstreams_toml(_OPENROUTER_TOML)
        r = _registry_with_openrouter()
        r.apply_persistent_patch("openrouter", {"model_filters": {"include": ["a/*"]}})
        merged = r.apply_persistent_patch(
            "openrouter", {"model_filters": {"models": [], "include": [], "exclude": []}}
        )
        assert merged.model_filters is None
        on_disk = tomllib.loads(path.read_text())["upstream"][0]
        assert "model_filters" not in on_disk  # exclude_none drops the cleared table

    def test_auto_registered_upstream_is_memory_only(self, tmp_hal0_home: str) -> None:
        from hal0.config import paths

        r = _registry_with_openrouter()  # no TOML row at all
        merged = r.apply_persistent_patch("openrouter", {"enabled": False})
        assert merged.enabled is False
        assert not (paths.etc() / "upstreams.toml").exists()

    def test_unknown_upstream_raises(self, tmp_hal0_home: str) -> None:
        r = UpstreamRegistry()
        with pytest.raises(UpstreamNotFound):
            r.apply_persistent_patch("ghost", {"enabled": False})

    def test_invalid_patch_rejected_and_memory_untouched(self, tmp_hal0_home: str) -> None:
        from pydantic import ValidationError

        _write_upstreams_toml(_OPENROUTER_TOML)
        r = _registry_with_openrouter()
        with pytest.raises(ValidationError):
            r.apply_persistent_patch("openrouter", {"auth_style": "basic"})
        assert r.get("openrouter").auth_style == "bearer"  # type: ignore[union-attr]

    def test_failed_save_leaves_memory_unchanged(
        self, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_upstreams_toml(_OPENROUTER_TOML)
        r = _registry_with_openrouter()

        import hal0.config.loader as loader

        def boom(*a: Any, **kw: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(loader, "save_upstreams_config", boom)
        with pytest.raises(OSError):
            r.apply_persistent_patch("openrouter", {"enabled": False})
        assert r.get("openrouter").enabled is True  # type: ignore[union-attr]

    def test_set_advertise_still_works_as_wrapper(self, tmp_hal0_home: str) -> None:
        import tomllib

        path = _write_upstreams_toml(_OPENROUTER_TOML)
        r = _registry_with_openrouter()
        merged = r.set_advertise("openrouter", False)
        assert merged.advertise_models is False
        assert tomllib.loads(path.read_text())["upstream"][0]["advertise_models"] is False


class TestCreateRemovePersistent:
    def _entry(self, name: str = "minimax", **kw: Any) -> Any:
        from hal0.config.schema import UpstreamEntry

        defaults: dict[str, Any] = dict(
            name=name,
            kind="remote",
            url="https://api.minimax.io/v1",
            auth_value_env="MINIMAX_API_KEY",
        )
        defaults.update(kw)
        return UpstreamEntry(**defaults)

    def test_create_appends_row_and_registers(self, tmp_hal0_home: str) -> None:
        import tomllib

        path = _write_upstreams_toml(_OPENROUTER_TOML)
        r = _registry_with_openrouter()
        upstream = r.create_persistent(self._entry())
        assert upstream.kind == "remote"
        assert r.get("minimax") is not None
        rows = tomllib.loads(path.read_text())["upstream"]
        assert [row["name"] for row in rows] == ["openrouter", "minimax"]

    def test_create_works_without_existing_toml(self, tmp_hal0_home: str) -> None:
        import tomllib

        from hal0.config import paths

        r = UpstreamRegistry()
        r.create_persistent(self._entry())
        rows = tomllib.loads((paths.etc() / "upstreams.toml").read_text())["upstream"]
        assert rows[0]["name"] == "minimax"

    def test_create_duplicate_in_registry_raises(self, tmp_hal0_home: str) -> None:
        r = _registry_with_openrouter()
        with pytest.raises(UpstreamAlreadyExists):
            r.create_persistent(self._entry(name="openrouter"))

    def test_create_duplicate_in_toml_only_raises(self, tmp_hal0_home: str) -> None:
        _write_upstreams_toml(_OPENROUTER_TOML)
        r = UpstreamRegistry()  # registry empty, TOML row present
        with pytest.raises(UpstreamAlreadyExists):
            r.create_persistent(self._entry(name="openrouter"))

    def test_create_rejects_reserved_and_slot_kind(self, tmp_hal0_home: str) -> None:
        from hal0.upstreams.registry import UpstreamProtected

        r = UpstreamRegistry()
        with pytest.raises(UpstreamProtected):
            r.create_persistent(self._entry(name="hal0"))
        with pytest.raises(UpstreamProtected):
            r.create_persistent(self._entry(kind="slot", slot_name="primary"))

    def test_remove_deletes_row_and_registry_entry(self, tmp_hal0_home: str) -> None:
        import tomllib

        path = _write_upstreams_toml(_OPENROUTER_TOML)
        r = _registry_with_openrouter()
        assert r.remove_persistent("openrouter") is True
        assert r.get("openrouter") is None
        assert tomllib.loads(path.read_text()).get("upstream", []) == []

    def test_remove_auto_registered_returns_false(self, tmp_hal0_home: str) -> None:
        r = _registry_with_openrouter()  # in-memory only
        assert r.remove_persistent("openrouter") is False
        assert r.get("openrouter") is None

    def test_remove_protects_composite_and_slots(self, tmp_hal0_home: str) -> None:
        from hal0.upstreams.registry import UpstreamProtected

        r = UpstreamRegistry()
        r.add(Upstream(name="hal0", kind="slot", url="http://127.0.0.1:8080/v1"))
        r.add(_slot("primary"))
        with pytest.raises(UpstreamProtected):
            r.remove_persistent("hal0")
        with pytest.raises(UpstreamProtected):
            r.remove_persistent("primary")

    def test_remove_unknown_raises(self, tmp_hal0_home: str) -> None:
        with pytest.raises(UpstreamNotFound):
            UpstreamRegistry().remove_persistent("ghost")


def test_upstream_from_entry_maps_all_fields(tmp_hal0_home: str) -> None:
    from hal0.config.schema import UpstreamEntry, UpstreamModelFilters
    from hal0.upstreams.registry import upstream_from_entry

    entry = UpstreamEntry(
        name="corp",
        kind="remote",
        url="https://llm.corp.internal/v1",
        auth_style="header",
        auth_header="X-Api-Key",
        auth_value_env="CORP_KEY",
        timeout_seconds=42.0,
        warmup_strategy="lazy",  # alias — normalizes to ondemand
        enabled=False,
        model_filters=UpstreamModelFilters(exclude=["*-draft"]),
    )
    u = upstream_from_entry(entry)
    assert u.auth_header == "X-Api-Key"
    assert u.enabled is False
    assert u.warmup_strategy == "ondemand"
    assert u.model_filters is not None and u.model_filters.exclude == ("*-draft",)
