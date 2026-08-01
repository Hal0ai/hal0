"""Unit tests for hal0.upstreams.registry.

Covers:
  - CRUD (add / get / remove / update / list / from_slot / in_priority_order)
  - Auth header dispatch by auth_style
  - TIER1: adaptive cold-boot backoff — step sequence, jitter, total grace cap,
    per-slot override from hardware.json
  - TIER2: negative-tps clamp + counter-reset warning

The async warmup path patches `asyncio.sleep` and `time.monotonic` so the test
suite finishes in milliseconds, not minutes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hal0.upstreams import registry as registry_mod
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


def test_list_and_priority_order() -> None:
    r = UpstreamRegistry()
    r.add(_remote("openai"))
    r.add(_slot("primary"))
    r.add(_remote("anthropic"))
    names = [u.name for u in r.list()]
    assert set(names) == {"openai", "primary", "anthropic"}
    ordered = [u.name for u in r.in_priority_order()]
    # slots before remotes; remotes sorted by name
    assert ordered == ["primary", "anthropic", "openai"]


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


@pytest.mark.asyncio
async def test_warmup_backoff_step_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The warmup loop sleeps for each step (0.5, 1, 2, 5, 10) ±25% in order."""
    r = UpstreamRegistry()
    u = _slot()
    r.add(u)

    # Always-unhealthy probe → warmup will exhaust the grace window.
    async def never_healthy(_: Upstream) -> bool:
        return False

    monkeypatch.setattr(r, "health", never_healthy)

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(registry_mod.asyncio, "sleep", fake_sleep)

    # Use a deterministic monotonic so the deadline truncates last sleeps.
    base = [0.0]

    def fake_monotonic() -> float:
        # Advance virtual clock by whatever we just slept.
        # First call sets the deadline; subsequent calls reflect accumulated sleep.
        if sleeps:
            base[0] = sum(sleeps)
        return base[0]

    monkeypatch.setattr(registry_mod.time, "monotonic", fake_monotonic)

    # Force jitter to 0 for the sequence assertion (we test jitter separately).
    monkeypatch.setattr(registry_mod.random, "uniform", lambda lo, hi: 0.0)

    ok = await r.warmup(u)
    assert ok is False

    # With no jitter, the recorded sleeps must be the exact step sequence
    # repeating the last value (10s) until 180s deadline.
    assert sleeps[:5] == [0.5, 1.0, 2.0, 5.0, 10.0]
    # After step 5 (cumulative 18.5s), the remaining cap-step sleeps are 10s
    # each until the deadline (180s). The final sleep may be truncated.
    rest = sleeps[5:]
    assert all(s <= 10.0 + 1e-9 for s in rest)
    assert pytest.approx(sum(sleeps), abs=1e-6) == 180.0


@pytest.mark.asyncio
async def test_warmup_backoff_jitter_within_25_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each sleep stays within ±25% of its nominal step (TIER1 jitter band)."""
    r = UpstreamRegistry()
    u = _slot()
    r.add(u)

    monkeypatch.setattr(r, "health", lambda _: _async_false())

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(registry_mod.asyncio, "sleep", fake_sleep)

    base = [0.0]

    def fake_monotonic() -> float:
        if sleeps:
            base[0] = sum(sleeps)
        return base[0]

    monkeypatch.setattr(registry_mod.time, "monotonic", fake_monotonic)

    await r.warmup(u)

    # First 5 sleeps map to the 5 nominal steps; assert each within band.
    for sleep, nominal in zip(sleeps[:5], TIER1_BACKOFF_STEPS, strict=False):
        assert sleep >= nominal * (1 - TIER1_BACKOFF_JITTER_FRAC) - 1e-9
        assert sleep <= nominal * (1 + TIER1_BACKOFF_JITTER_FRAC) + 1e-9


async def _async_false() -> bool:
    return False


async def _async_true() -> bool:
    return True


@pytest.mark.asyncio
async def test_warmup_total_grace_caps_at_180s(monkeypatch: pytest.MonkeyPatch) -> None:
    """No matter how many attempts, total cumulative sleep <= 180s."""
    r = UpstreamRegistry()
    u = _slot()
    r.add(u)

    monkeypatch.setattr(r, "health", lambda _: _async_false())

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(registry_mod.asyncio, "sleep", fake_sleep)

    base = [0.0]

    def fake_monotonic() -> float:
        if sleeps:
            base[0] = sum(sleeps)
        return base[0]

    monkeypatch.setattr(registry_mod.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(registry_mod.random, "uniform", lambda lo, hi: 0.0)

    ok = await r.warmup(u)
    assert ok is False
    assert sum(sleeps) <= TIER1_TOTAL_GRACE_S + 1e-9


@pytest.mark.asyncio
async def test_warmup_returns_true_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warmup returns True as soon as health() succeeds (no needless sleeps after)."""
    r = UpstreamRegistry()
    u = _slot()
    r.add(u)

    healthy_after: dict[str, int] = {"count": 0}

    async def health(_: Upstream) -> bool:
        healthy_after["count"] += 1
        return healthy_after["count"] >= 3  # healthy on the 3rd probe

    monkeypatch.setattr(r, "health", health)

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(registry_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(registry_mod.random, "uniform", lambda lo, hi: 0.0)

    ok = await r.warmup(u)
    assert ok is True
    # First probe is inside the lock (no sleep), then 2 backoff sleeps before
    # the third health probe returns True.
    assert len(sleeps) == 2
    assert sleeps == [0.5, 1.0]


@pytest.mark.asyncio
async def test_warmup_strategy_none_just_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """warmup_strategy == 'none' delegates to a single health probe."""
    r = UpstreamRegistry()
    u = _slot(warmup_strategy="none")
    r.add(u)
    monkeypatch.setattr(r, "health", lambda _: _async_true())
    ok = await r.warmup(u)
    assert ok is True


@pytest.mark.asyncio
async def test_warmup_non_slot_returns_false() -> None:
    r = UpstreamRegistry()
    u = _remote()
    r.add(u)
    assert await r.warmup(u) is False


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


@pytest.mark.asyncio
async def test_warmup_uses_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Per-slot override actually drives the sleep sequence."""
    hw = tmp_path / "hardware.json"
    hw.write_text(
        json.dumps(
            {
                "slots": {
                    "primary": {
                        "backoff_steps": [0.1, 0.1],
                        "warmup_grace_s": 0.5,
                    }
                }
            }
        )
    )
    r = UpstreamRegistry()
    r.load_slot_overrides(hw)
    u = _slot()
    r.add(u)

    monkeypatch.setattr(r, "health", lambda _: _async_false())

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(registry_mod.asyncio, "sleep", fake_sleep)

    base = [0.0]

    def fake_monotonic() -> float:
        if sleeps:
            base[0] = sum(sleeps)
        return base[0]

    monkeypatch.setattr(registry_mod.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(registry_mod.random, "uniform", lambda lo, hi: 0.0)

    ok = await r.warmup(u)
    assert ok is False
    assert sum(sleeps) <= 0.5 + 1e-9
    # Each sleep must be one of the override step values (after jitter=0).
    for s in sleeps:
        assert s <= 0.1 + 1e-9


# ── TIER2: negative tps clamp ─────────────────────────────────────────────────


def test_tps_first_sample_returns_zero() -> None:
    r = UpstreamRegistry()
    tps = r.record_tokens("primary", token_counter=100, now=1.0)
    assert tps == 0.0


def test_tps_normal_progression() -> None:
    r = UpstreamRegistry()
    r.record_tokens("primary", token_counter=0, now=0.0)
    tps = r.record_tokens("primary", token_counter=50, now=5.0)
    assert tps == pytest.approx(10.0)
    assert r.get_tps("primary") == pytest.approx(10.0)


def test_tps_clamps_negative_to_zero(caplog: pytest.LogCaptureFixture) -> None:
    """TIER2: counter reset (process restart) must not produce negative tps."""
    r = UpstreamRegistry()
    r.record_tokens("primary", token_counter=10_000, now=10.0)
    tps = r.record_tokens("primary", token_counter=42, now=15.0)
    assert tps == 0.0
    assert r.get_tps("primary") == 0.0


def test_tps_logs_warning_on_counter_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """TIER2: a warning is emitted when the counter goes backwards."""
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeLog:
        def info(self, *a: Any, **kw: Any) -> None: ...
        def debug(self, *a: Any, **kw: Any) -> None: ...
        def warning(self, event: str, **kw: Any) -> None:
            calls.append((event, kw))

    monkeypatch.setattr(registry_mod, "log", FakeLog())
    r = UpstreamRegistry()
    r.record_tokens("primary", token_counter=10_000, now=10.0)
    r.record_tokens("primary", token_counter=42, now=15.0)
    assert any(evt == "upstream.tps_counter_reset" for evt, _ in calls)


def test_tps_zero_delta_t_keeps_value_non_negative() -> None:
    r = UpstreamRegistry()
    r.record_tokens("primary", token_counter=100, now=1.0)
    tps = r.record_tokens("primary", token_counter=200, now=1.0)
    # Same instant — tps holds at the previous (clamped) value, which was 0.
    assert tps >= 0.0


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
