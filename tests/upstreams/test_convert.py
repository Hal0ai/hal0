"""Tests for hal0.upstreams.convert: legacy rows become curated rows."""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.config import paths
from hal0.config.loader import load_upstreams_config
from hal0.upstreams.convert import ConvertResult, convert_legacy_upstreams
from hal0.upstreams.discovery import DiscoveryCache
from hal0.upstreams.registry import Upstream, UpstreamRegistry, upstream_from_entry


def _row(name: str, extra: str = "") -> str:
    return (
        "[[upstream]]\n"
        f'name = "{name}"\n'
        'kind = "remote"\n'
        'url = "https://api.example.invalid/v1"\n'
        'auth_value_env = "EXAMPLE_KEY"\n'
        f"{extra}\n"
    )


def _boot(toml_body: str) -> tuple[UpstreamRegistry, Path]:
    etc = paths.etc()
    etc.mkdir(parents=True, exist_ok=True)
    path = etc / "upstreams.toml"
    path.write_text(toml_body)
    registry = UpstreamRegistry()
    for entry in load_upstreams_config().upstream:
        registry.upsert(upstream_from_entry(entry))
    return registry, path


def _serve(
    monkeypatch: pytest.MonkeyPatch, registry: UpstreamRegistry, payloads: dict[str, Any]
) -> list[str]:
    """Make ``fetch_model_payload`` answer from ``payloads``; return the call log."""
    calls: list[str] = []

    async def fake(name: str) -> Any:
        calls.append(name)
        return payloads.get(name)

    monkeypatch.setattr(registry, "fetch_model_payload", fake)
    return calls


def _ids(*ids: str) -> dict[str, Any]:
    return {"data": [{"id": i} for i in ids]}


def _entry(name: str) -> dict[str, Any]:
    entry = next(e for e in load_upstreams_config().upstream if e.name == name)
    return entry.model_dump()


def _disk(path: Path, name: str) -> dict[str, Any]:
    rows = tomllib.loads(path.read_text())["upstream"]
    return next(r for r in rows if r["name"] == name)


async def test_no_filters_enables_everything(tmp_hal0_home: str, monkeypatch) -> None:
    registry, path = _boot(_row("mm"))
    _serve(monkeypatch, registry, {"mm": _ids("a", "b")})

    result = await convert_legacy_upstreams(registry, providers_toml=None)

    assert result == ConvertResult(converted=("mm",), deferred=(), folded_provider_rows=0)
    disk = _disk(path, "mm")
    assert disk["curated"] is True
    assert [(m["id"], m["enabled"]) for m in disk["models"]] == [("a", True), ("b", True)]
    live = registry.get("mm")
    assert live is not None and live.curated and not live.model_filters
    assert [m.id for m in live.models] == ["a", "b"]


async def test_filters_decide_enabled(tmp_hal0_home: str, monkeypatch) -> None:
    extra = '[upstream.model_filters]\nmodels = ["a"]\nexclude = ["*-preview"]\n'
    registry, path = _boot(_row("mm", extra))
    _serve(monkeypatch, registry, {"mm": _ids("a", "b", "a-preview")})

    result = await convert_legacy_upstreams(registry, providers_toml=None)

    assert result.converted == ("mm",)
    disk = _disk(path, "mm")
    assert disk["curated"] is True
    assert "model_filters" not in disk
    assert {m["id"]: m["enabled"] for m in disk["models"]} == {
        "a": True,
        "b": False,
        "a-preview": False,
    }
    live = registry.get("mm")
    assert live is not None and live.model_filters is None and live.curated


async def test_model_fields_copied_from_discovery_and_preset(
    tmp_hal0_home: str, monkeypatch
) -> None:
    registry, path = _boot(_row("or", 'catalog_id = "openrouter"'))
    body = {
        "data": [
            {
                "id": "qwen/qwen3-coder",
                "context_length": 262144,
                "pricing": {"prompt": "0.00000022", "completion": "0.00000095"},
            },
            {"id": "text-embedding-3-small"},
        ]
    }
    _serve(monkeypatch, registry, {"or": body})

    await convert_legacy_upstreams(registry, providers_toml=None)

    rows = {m["id"]: m for m in _disk(path, "or")["models"]}
    coder = rows["qwen/qwen3-coder"]
    assert coder["context"] == 262144
    assert coder["price_in"] == pytest.approx(0.22)
    assert coder["price_out"] == pytest.approx(0.95)
    assert coder["price_unit"] == "Mtok"
    assert coder["type"] == "chat"
    assert rows["text-embedding-3-small"]["type"] == "embed"
    assert "context" not in rows["text-embedding-3-small"]


async def test_unreachable_provider_is_deferred_and_untouched(
    tmp_hal0_home: str, monkeypatch
) -> None:
    extra = '[upstream.model_filters]\ninclude = ["a*"]\n'
    registry, path = _boot(_row("mm", extra) + "\n" + _row("keep"))
    before = path.read_bytes()
    live_before = registry.get("mm")
    _serve(monkeypatch, registry, {})  # every fetch answers None

    result = await convert_legacy_upstreams(registry, providers_toml=None)

    assert result.converted == ()
    assert result.deferred == ("mm", "keep")
    assert path.read_bytes() == before
    assert registry.get("mm") == live_before


async def test_empty_model_list_is_deferred(tmp_hal0_home: str, monkeypatch) -> None:
    registry, path = _boot(_row("mm"))
    before = path.read_bytes()
    _serve(monkeypatch, registry, {"mm": {"data": []}})

    result = await convert_legacy_upstreams(registry, providers_toml=None)

    assert (result.converted, result.deferred) == ((), ("mm",))
    assert path.read_bytes() == before


async def test_agent_managed_and_slot_rows_skipped(tmp_hal0_home: str, monkeypatch) -> None:
    registry, _ = _boot("")
    registry.add(
        Upstream(name="hermes.x", kind="remote", url="http://127.0.0.1:9/v1", managed_by="agent:x")
    )
    registry.add(
        Upstream(name="primary", kind="slot", url="http://127.0.0.1:8081/v1", slot_name="primary")
    )
    calls = _serve(monkeypatch, registry, {"hermes.x": _ids("a"), "primary": _ids("a")})

    result = await convert_legacy_upstreams(registry, providers_toml=None)

    assert result == ConvertResult((), (), 0)
    assert calls == []
    assert not registry.get("hermes.x").curated  # type: ignore[union-attr]
    assert not registry.get("primary").curated  # type: ignore[union-attr]


async def test_already_curated_row_skipped(tmp_hal0_home: str, monkeypatch) -> None:
    body = _row("mm", 'curated = true\n[[upstream.models]]\nid = "only"\nenabled = false\n')
    registry, path = _boot(body)
    before = path.read_bytes()
    calls = _serve(monkeypatch, registry, {"mm": _ids("only", "other")})

    result = await convert_legacy_upstreams(registry, providers_toml=None)

    assert result == ConvertResult((), (), 0)
    assert calls == []
    assert path.read_bytes() == before


def _providers_toml(body: str) -> Path:
    p = paths.etc() / "providers.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


async def test_providers_toml_selection_wins_and_file_retired(
    tmp_hal0_home: str, monkeypatch
) -> None:
    extra = '[upstream.model_filters]\nexclude = ["b"]\n'  # filters would enable a and c
    registry, path = _boot(_row("mm", extra) + "\n" + _row("other"))
    pt = _providers_toml(
        '[[provider]]\ncatalog_id = "openrouter"\nname = "mm"\nmodels = ["b", "ghost"]\n'
    )
    _serve(monkeypatch, registry, {"mm": _ids("a", "b", "c"), "other": _ids("x")})

    result = await convert_legacy_upstreams(registry, providers_toml=pt)

    assert result.converted == ("mm", "other")
    assert result.folded_provider_rows == 1
    assert {m["id"]: m["enabled"] for m in _disk(path, "mm")["models"]} == {
        "a": False,
        "b": True,
        "c": False,
    }
    assert [m["enabled"] for m in _disk(path, "other")["models"]] == [True]
    assert not pt.exists()
    assert pt.with_name("providers.toml.retired").exists()


async def test_providers_toml_matches_catalog_id_when_name_empty(
    tmp_hal0_home: str, monkeypatch
) -> None:
    registry, path = _boot(_row("openrouter"))
    pt = _providers_toml('[[provider]]\ncatalog_id = "openrouter"\nmodels = ["a"]\n')
    _serve(monkeypatch, registry, {"openrouter": _ids("a", "b")})

    result = await convert_legacy_upstreams(registry, providers_toml=pt)

    assert result.folded_provider_rows == 1
    assert {m["id"]: m["enabled"] for m in _disk(path, "openrouter")["models"]} == {
        "a": True,
        "b": False,
    }


async def test_providers_toml_empty_selection_does_not_disable_everything(
    tmp_hal0_home: str, monkeypatch
) -> None:
    registry, path = _boot(_row("mm"))
    pt = _providers_toml('[[provider]]\ncatalog_id = "x"\nname = "mm"\nmodels = []\n')
    _serve(monkeypatch, registry, {"mm": _ids("a", "b")})

    await convert_legacy_upstreams(registry, providers_toml=pt)

    assert [m["enabled"] for m in _disk(path, "mm")["models"]] == [True, True]


async def test_providers_toml_kept_when_its_row_deferred(tmp_hal0_home: str, monkeypatch) -> None:
    registry, path = _boot(_row("mm") + "\n" + _row("other"))
    body = (
        '[[provider]]\ncatalog_id = "x"\nname = "mm"\nmodels = ["a"]\n'
        '[[provider]]\ncatalog_id = "y"\nname = "other"\nmodels = ["x"]\n'
    )
    pt = _providers_toml(body)
    _serve(monkeypatch, registry, {"mm": _ids("a", "b")})  # "other" is unreachable

    result = await convert_legacy_upstreams(registry, providers_toml=pt)

    assert result.converted == ("mm",)
    assert result.deferred == ("other",)
    assert pt.read_text() == body
    assert not pt.with_name("providers.toml.retired").exists()
    # The reachable row still took its selection.
    assert {m["id"]: m["enabled"] for m in _disk(path, "mm")["models"]} == {"a": True, "b": False}


async def test_providers_toml_malformed_is_skipped_not_fatal(
    tmp_hal0_home: str, monkeypatch
) -> None:
    registry, _ = _boot(_row("mm"))
    pt = _providers_toml("[[provider\nthis is not toml")
    _serve(monkeypatch, registry, {"mm": _ids("a")})

    result = await convert_legacy_upstreams(registry, providers_toml=pt)

    assert result.converted == ("mm",)
    assert result.folded_provider_rows == 0
    assert pt.read_text() == "[[provider\nthis is not toml"
    assert not pt.with_name("providers.toml.retired").exists()


async def test_existing_retired_file_is_never_overwritten(tmp_hal0_home: str, monkeypatch) -> None:
    registry, _ = _boot(_row("mm"))
    pt = _providers_toml('[[provider]]\ncatalog_id = "x"\nname = "mm"\nmodels = ["a"]\n')
    retired = pt.with_name("providers.toml.retired")
    retired.write_text("older archive")
    _serve(monkeypatch, registry, {"mm": _ids("a")})

    await convert_legacy_upstreams(registry, providers_toml=pt)

    assert pt.exists()
    assert retired.read_text() == "older archive"


async def test_one_failing_row_does_not_stop_the_others(tmp_hal0_home: str, monkeypatch) -> None:
    registry, path = _boot(_row("bad") + "\n" + _row("good"))
    before_bad = _entry("bad")
    # An id the schema rejects (blank) makes only that row's write fail.
    _serve(
        monkeypatch, registry, {"bad": {"data": [{"id": "ok"}, {"id": "  "}]}, "good": _ids("a")}
    )

    result = await convert_legacy_upstreams(registry, providers_toml=None)

    assert result.converted == ("good",)
    assert result.deferred == ("bad",)
    assert _entry("bad") == before_bad  # the file is rewritten whole, but the row is the same
    assert not registry.get("bad").curated  # type: ignore[union-attr]
    assert _disk(path, "good")["curated"] is True


async def test_failed_write_leaves_row_as_it_was(tmp_hal0_home: str, monkeypatch) -> None:
    registry, path = _boot(_row("mm"))
    before = path.read_bytes()
    _serve(monkeypatch, registry, {"mm": _ids("a")})

    import hal0.config.loader as loader

    def boom(*a: Any, **kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(loader, "save_upstreams_config", boom)

    result = await convert_legacy_upstreams(registry, providers_toml=None)

    assert result.deferred == ("mm",)
    assert path.read_bytes() == before
    assert not registry.get("mm").curated  # type: ignore[union-attr]


async def test_second_run_is_a_no_op(tmp_hal0_home: str, monkeypatch) -> None:
    registry, path = _boot(_row("mm"))
    pt = _providers_toml('[[provider]]\ncatalog_id = "x"\nname = "mm"\nmodels = ["a"]\n')
    calls = _serve(monkeypatch, registry, {"mm": _ids("a", "b")})
    await convert_legacy_upstreams(registry, providers_toml=pt)
    after_first = path.read_bytes()
    calls.clear()

    second = await convert_legacy_upstreams(registry, providers_toml=pt)

    assert second == ConvertResult((), (), 0)
    assert calls == []
    assert path.read_bytes() == after_first


async def test_conversion_reseeds_dispatch_cache_and_stores_discovery(
    tmp_hal0_home: str, monkeypatch
) -> None:
    extra = '[upstream.model_filters]\nexclude = ["b"]\n'
    registry, _ = _boot(_row("mm", extra))
    # Passthrough before: the network's list, filters notwithstanding.
    model_cache = {"mm": ["a", "b", "c"]}
    discovery = DiscoveryCache()
    _serve(monkeypatch, registry, {"mm": _ids("a", "b", "c")})

    await convert_legacy_upstreams(
        registry, providers_toml=None, model_cache=model_cache, discovery=discovery
    )

    assert model_cache["mm"] == ["a", "c"]
    entry = discovery.get("mm")
    assert entry is not None and [m.id for m in entry[0]] == ["a", "b", "c"]


async def test_deferred_row_keeps_its_cache_entries(tmp_hal0_home: str, monkeypatch) -> None:
    registry, _ = _boot(_row("mm"))
    model_cache = {"mm": ["a", "b"]}
    discovery = DiscoveryCache()
    _serve(monkeypatch, registry, {})

    await convert_legacy_upstreams(
        registry, providers_toml=None, model_cache=model_cache, discovery=discovery
    )

    assert model_cache == {"mm": ["a", "b"]}
    assert discovery.get("mm") is None


# ── boot phase ────────────────────────────────────────────────────────────────


def _boot_ctx(registry: UpstreamRegistry, model_cache: dict[str, list[str]]):
    from hal0.api import BootState

    ctx = BootState()
    ctx.upstreams = registry
    ctx.model_cache = model_cache
    app = SimpleNamespace(state=SimpleNamespace(upstream_discovery=DiscoveryCache()))
    return app, ctx


async def test_boot_phase_converts_in_background_and_switches_routing(
    tmp_hal0_home: str, monkeypatch
) -> None:
    from hal0.api import _boot_upstream_conversion

    registry, path = _boot(_row("mm", '[upstream.model_filters]\nexclude = ["b"]\n'))
    model_cache: dict[str, list[str]] = {"mm": ["a", "b"]}
    app, ctx = _boot_ctx(registry, model_cache)
    gate = asyncio.Event()

    async def slow(name: str) -> Any:
        await gate.wait()
        return _ids("a", "b")

    monkeypatch.setattr(registry, "fetch_model_payload", slow)

    await _boot_upstream_conversion(app, ctx)  # returns without waiting on the provider
    assert ctx.upstream_conversion_task is not None
    assert not ctx.upstream_conversion_task.done()
    assert model_cache["mm"] == ["a", "b"]

    gate.set()
    await ctx.upstream_conversion_task

    assert model_cache["mm"] == ["a"]  # curated: the enabled list, no restart needed
    assert _disk(path, "mm")["curated"] is True
    assert app.state.upstream_discovery.get("mm") is not None
    await ctx.stop_upstream_conversion_task()


async def test_boot_phase_cancels_cleanly_on_shutdown(tmp_hal0_home: str, monkeypatch) -> None:
    from hal0.api import _boot_upstream_conversion

    registry, path = _boot(_row("mm"))
    before = path.read_bytes()
    app, ctx = _boot_ctx(registry, {})

    async def hang(name: str) -> Any:
        await asyncio.sleep(3600)

    monkeypatch.setattr(registry, "fetch_model_payload", hang)
    await _boot_upstream_conversion(app, ctx)
    await asyncio.sleep(0)

    await asyncio.wait_for(ctx.stop_upstream_conversion_task(), timeout=2)

    assert ctx.upstream_conversion_task.done()
    assert path.read_bytes() == before


async def test_boot_phase_never_raises(tmp_hal0_home: str, monkeypatch) -> None:
    from hal0.api import _boot_upstream_conversion

    registry, _ = _boot(_row("mm"))
    app, ctx = _boot_ctx(registry, {})

    async def explode(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr("hal0.upstreams.convert.convert_legacy_upstreams", explode)

    await _boot_upstream_conversion(app, ctx)
    await ctx.upstream_conversion_task  # the task swallows and logs; it does not raise
    await ctx.stop_upstream_conversion_task()
