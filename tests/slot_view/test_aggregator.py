"""Unit tests for hal0.slot_view — SlotViewAggregator + per-concern functions.

Issue #698: each enrichment concern that used to live inline in
``api/routes/slots.py:list_slots()`` gets a direct unit test against
fake stores — no HTTP, no five-piece app-state mock:

  1. slot-state serialization        → serialize_slot
  2. lemonade enrichment + drift     → lemonade_enrichment
  3. container systemctl/port probe  → container_enrichment
  4. per-slot memory accounting      → SlotViewAggregator.snapshot
  5. metric injection                → SlotViewAggregator.snapshot

The /api/slots wire shape is pinned by the existing route-level suites
(test_slots_routes / test_slots_lemonade_state / test_slots_container_state
/ test_slots_npu_fields) which stay UNMODIFIED as the parity oracle.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hal0.slot_view import (
    SlotView,
    SlotViewAggregator,
    container_enrichment,
    lemonade_enrichment,
    loaded_model_names,
    serialize_slot,
    synthesize_upstream_entries,
)
from hal0.slots.manager import Slot
from hal0.slots.state import SlotState

# ── fakes ──────────────────────────────────────────────────────────────────


class FakeSlotManager:
    """Just enough surface for the aggregator: list() + iter_configs()."""

    def __init__(
        self,
        slots: list[Slot] | None = None,
        configs: list[dict[str, Any]] | None = None,
    ) -> None:
        self._slots = slots or []
        self._configs = configs or []

    async def list(self) -> list[Slot]:
        return self._slots

    async def iter_configs(self) -> list[dict[str, Any]]:
        return self._configs


class FakeLemonadeShim:
    """health() returns a canned payload — or raises when told to."""

    def __init__(self, health: dict[str, Any] | None = None, raise_exc: bool = False) -> None:
        self._health = health if health is not None else {}
        self._raise = raise_exc

    async def health(self) -> dict[str, Any]:
        if self._raise:
            raise RuntimeError("lemond down")
        return self._health


class FakeContainerProvider:
    """Duck-typed stand-in for ContainerProvider (sync + async mix matches)."""

    def __init__(
        self,
        active: bool = True,
        healthy: bool = True,
        running_image: str | None = None,
        present: bool = True,
        raise_exc: bool = False,
    ) -> None:
        self._active = active
        self._healthy = healthy
        self._running_image = running_image
        self._present = present
        self._raise = raise_exc

    def is_active(self, slot_name: str) -> bool:
        if self._raise:
            raise RuntimeError("podman exploded")
        return self._active

    async def health(self, port: int) -> dict[str, Any]:
        return {"ok": self._healthy}

    def running_image(self, slot_name: str) -> str | None:
        return self._running_image

    def image_present(self, image: str) -> bool:
        return self._present


class FakeUpstreams:
    def __init__(self, entries: list[SimpleNamespace]) -> None:
        self._entries = entries

    def list(self) -> list[SimpleNamespace]:
        return self._entries


def _slot(
    name: str = "chat",
    state: SlotState = SlotState.READY,
    port: int = 8081,
    model_id: str | None = "qwen3-4b",
    backend: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Slot:
    return Slot(
        name,
        state=state,
        port=port,
        model_id=model_id,
        backend=backend,
        metadata=metadata,
    )


def _agg(
    sm: FakeSlotManager,
    *,
    metrics: Any = None,
    shim: Any = None,
    container: Any = None,
    model_cache: dict[str, Any] | None = None,
    upstreams: Any = None,
    last_used: dict[str, str] | None = None,
    pull_jobs: dict[str, Any] | None = None,
    registry: Any = None,
) -> SlotViewAggregator:
    async def _no_metrics() -> dict[str, Any]:
        return {}

    return SlotViewAggregator(
        sm,
        registry=registry,
        metrics=metrics or _no_metrics,
        lemonade_shim=shim or FakeLemonadeShim(),
        container_provider=container or FakeContainerProvider(active=False),
        model_cache=model_cache if model_cache is not None else {},
        upstreams=upstreams or FakeUpstreams([]),
        last_used_model=last_used or {},
        slot_pull_jobs=pull_jobs or {},
    )


@pytest.fixture
def no_mem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the capacity probe so snapshot() never touches systemd."""

    async def fake_build(slots: Any, registry: Any = None, **kw: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr("hal0.slots.capacity.build_per_slot", fake_build)


# ── concern 1: slot-state serialization ────────────────────────────────────


class TestSerializeSlot:
    def test_basic_shape(self) -> None:
        out = serialize_slot(_slot(), model_cache={})
        assert out["name"] == "chat"
        assert out["kind"] == "local"
        assert out["status"] == "ready"
        assert out["state"] == "ready"
        assert out["port"] == 8081
        assert out["model_id"] == "qwen3-4b"
        assert out["models"] == []

    def test_backend_and_provider_lifted_from_metadata(self) -> None:
        out = serialize_slot(
            _slot(backend=None, metadata={"backend": "vulkan", "provider": "lemonade"}),
            model_cache={},
        )
        assert out["backend"] == "vulkan"
        assert out["provider"] == "lemonade"

    def test_explicit_backend_wins_over_metadata(self) -> None:
        out = serialize_slot(
            _slot(backend="rocm", metadata={"backend": "vulkan"}),
            model_cache={},
        )
        assert out["backend"] == "rocm"

    def test_models_orders_active_model_first(self) -> None:
        out = serialize_slot(
            _slot(model_id="b-model"),
            model_cache={"chat": ["a-model", "b-model", "c-model"]},
        )
        assert out["models"] == ["b-model", "a-model", "c-model"]

    def test_no_cache_omits_models_key(self) -> None:
        out = serialize_slot(_slot(), model_cache=None)
        assert "models" not in out


# ── concern 2: lemonade enrichment + drift detection ───────────────────────


def _llm_cfg(name: str = "chat", **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": name,
        "port": 8081,
        "type": "llm",
        "enabled": True,
        "model": {"default": "qwen3-4b"},
    }
    cfg.update(over)
    return cfg


class TestLemonadeEnrichment:
    def test_idle_when_enabled_and_not_loaded(self) -> None:
        out = lemonade_enrichment([_llm_cfg()], {"loaded": []})
        assert out["chat"]["lemonade_state"] == "idle"
        assert "backend_url" not in out["chat"]

    def test_disabled_wins_over_loaded(self) -> None:
        health = {"loaded": [{"model_name": "qwen3-4b"}]}
        out = lemonade_enrichment([_llm_cfg(enabled=False)], health)
        assert out["chat"]["lemonade_state"] == "disabled"

    def test_loaded_lifts_backend_url(self) -> None:
        health = {"loaded": [{"model_name": "qwen3-4b", "backend_url": "http://127.0.0.1:8001"}]}
        out = lemonade_enrichment([_llm_cfg()], health)
        assert out["chat"]["lemonade_state"] == "loaded"
        assert out["chat"]["backend_url"] == "http://127.0.0.1:8001"

    def test_drift_detection_declared_vs_actual(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "hal0.providers.lemonade.resolve_actual_backend",
            lambda entry: "rocm",
        )
        health = {"loaded": [{"model_name": "qwen3-4b", "backend_url": "http://127.0.0.1:8001"}]}
        out = lemonade_enrichment([_llm_cfg(device="gpu-vulkan")], health)
        e = out["chat"]
        assert e["declared_backend"] == "vulkan"
        assert e["actual_backend"] == "rocm"
        assert e["backend_mismatch"] is True

    def test_no_drift_when_backends_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "hal0.providers.lemonade.resolve_actual_backend",
            lambda entry: "vulkan",
        )
        health = {"loaded": [{"model_name": "qwen3-4b", "backend_url": "http://127.0.0.1:8001"}]}
        out = lemonade_enrichment([_llm_cfg(device="gpu-vulkan")], health)
        assert out["chat"]["backend_mismatch"] is False

    def test_actual_backend_omitted_when_unintrospectable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "hal0.providers.lemonade.resolve_actual_backend",
            lambda entry: None,
        )
        health = {"loaded": [{"model_name": "qwen3-4b", "backend_url": "http://127.0.0.1:8001"}]}
        out = lemonade_enrichment([_llm_cfg(device="gpu-vulkan")], health)
        e = out["chat"]
        assert "actual_backend" not in e
        assert "backend_mismatch" not in e
        assert e["declared_backend"] == "vulkan"

    def test_container_slots_are_skipped(self) -> None:
        out = lemonade_enrichment(
            [_llm_cfg(name="gpu-chat", profile="vulkan-radv")],
            {"loaded": []},
        )
        assert "gpu-chat" not in out

    def test_coresident_group_for_npu_trio(self) -> None:
        configs = [
            _llm_cfg(name="agent", device="npu"),
            {
                "name": "stt-npu",
                "device": "npu",
                "type": "transcription",
                "enabled": True,
                "model": {"default": "whisper-v3"},
            },
        ]
        out = lemonade_enrichment(configs, {"loaded": []})
        assert out["agent"]["coresident_group"] == "npu-flm-trio"
        assert out["stt-npu"]["coresident_group"] == "npu-flm-trio"

    def test_no_coresident_group_without_enabled_anchor(self) -> None:
        configs = [
            {
                "name": "stt-npu",
                "device": "npu",
                "type": "transcription",
                "enabled": True,
                "model": {"default": "whisper-v3"},
            },
        ]
        out = lemonade_enrichment(configs, {"loaded": []})
        assert "coresident_group" not in out["stt-npu"]

    def test_config_fields_surfaced(self) -> None:
        cfg = _llm_cfg(
            enable_thinking=True,
            idle_timeout_s=900,
            workers=2,
            server={"extra_args": "--flash-attn on"},
            npu={"asr": True, "embed": False},
        )
        cfg["model"]["n_gpu_layers"] = 24
        cfg["model"]["rope_freq_base"] = 10000.0
        cfg["model"]["labels"] = ["tool-calling"]
        out = lemonade_enrichment([cfg], {"loaded": []})
        e = out["chat"]
        assert e["type"] == "llm"
        assert e["model_default"] == "qwen3-4b"
        assert e["labels"] == ["tool-calling"]
        assert e["enabled"] is True
        assert e["enable_thinking"] is True
        assert e["n_gpu_layers"] == 24
        assert e["rope_freq_base"] == 10000.0
        assert e["idle_timeout_s"] == 900
        assert e["workers"] == 2
        assert e["llamacpp_args"] == "--flash-attn on"
        assert e["npu"] == {"asr": True, "embed": False}

    def test_absent_config_fields_surface_as_defaults(self) -> None:
        out = lemonade_enrichment([_llm_cfg()], {"loaded": []})
        e = out["chat"]
        assert e["enable_thinking"] is None
        assert e["n_gpu_layers"] == -1
        assert e["rope_freq_base"] is None
        assert e["idle_timeout_s"] is None
        assert e["workers"] is None
        assert e["llamacpp_args"] is None
        assert "npu" not in e


# ── concern 3: container probe ──────────────────────────────────────────────


def _container_cfg(name: str = "gpu-chat", **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": name,
        "port": 8088,
        "type": "llm",
        "runtime": "container",
        "model": {"default": "llama-3b"},
    }
    cfg.update(over)
    return cfg


class TestContainerEnrichment:
    async def test_running_and_healthy(self) -> None:
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(active=True, healthy=True),
        )
        e = out["gpu-chat"]
        assert e["container_status"] == "running"
        assert e["container_health"] is True
        assert e["runtime"] == "container"
        assert e["profile"] == ""
        assert e["image"] is None
        assert e["resolved_command"] is None

    async def test_active_but_unhealthy_is_starting(self) -> None:
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(active=True, healthy=False),
        )
        assert out["gpu-chat"]["container_status"] == "starting"

    async def test_active_without_port_is_running_unhealthy(self) -> None:
        cfg = _container_cfg()
        cfg["port"] = 0
        out = await container_enrichment(
            [cfg],
            pull_jobs={},
            provider=FakeContainerProvider(active=True, healthy=True),
        )
        e = out["gpu-chat"]
        assert e["container_status"] == "running"
        assert e["container_health"] is False

    async def test_inactive_is_stopped(self) -> None:
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(active=False),
        )
        e = out["gpu-chat"]
        assert e["container_status"] == "stopped"
        assert e["container_health"] is False

    async def test_provider_failure_degrades_to_stopped(self) -> None:
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(raise_exc=True),
        )
        e = out["gpu-chat"]
        assert e["container_status"] == "stopped"
        assert e["container_health"] is False

    async def test_lemonade_slots_are_skipped(self) -> None:
        out = await container_enrichment(
            [_llm_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(),
        )
        assert out == {}

    async def test_actual_image_surfaced(self) -> None:
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(
                active=True, healthy=True, running_image="ghcr.io/x/y:1"
            ),
        )
        e = out["gpu-chat"]
        assert e["actual_image"] == "ghcr.io/x/y:1"
        # No declared image (no profile) → no mismatch verdict.
        assert "image_mismatch" not in e

    async def test_inflight_pull_job_wins_image_status(self) -> None:
        job = SimpleNamespace(state="pulling")
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={"gpu-chat": job},
            provider=FakeContainerProvider(active=False),
        )
        assert out["gpu-chat"]["image_status"] == "pulling"

    async def test_no_image_means_missing_status(self) -> None:
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(active=False),
        )
        assert out["gpu-chat"]["image_status"] == "missing"

    async def test_npu_table_surfaced(self) -> None:
        out = await container_enrichment(
            [_container_cfg(npu={"asr": True, "embed": True})],
            pull_jobs={},
            provider=FakeContainerProvider(active=False),
        )
        assert out["gpu-chat"]["npu"] == {"asr": True, "embed": True}


# ── concern 4: per-slot memory accounting ───────────────────────────────────


class TestMemoryAttribution:
    async def test_mem_mb_stamped_from_capacity_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_build(slots: Any, registry: Any = None, **kw: Any) -> dict[str, Any]:
            return {"chat": {"mem_mb": 1234.56}}

        monkeypatch.setattr("hal0.slots.capacity.build_per_slot", fake_build)
        agg = _agg(FakeSlotManager(slots=[_slot()]))
        views = await agg.snapshot()
        assert views[0].mem_mb == 1234.6
        assert views[0].to_dict()["mem_mb"] == 1234.6

    async def test_mem_mb_zero_when_no_row(self, no_mem: None) -> None:
        agg = _agg(FakeSlotManager(slots=[_slot()]))
        views = await agg.snapshot()
        assert views[0].mem_mb == 0
        assert views[0].to_dict()["mem_mb"] == 0

    async def test_capacity_probe_failure_never_breaks_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(slots: Any, registry: Any = None, **kw: Any) -> dict[str, Any]:
            raise RuntimeError("cgroup probe exploded")

        monkeypatch.setattr("hal0.slots.capacity.build_per_slot", boom)
        agg = _agg(FakeSlotManager(slots=[_slot()]))
        views = await agg.snapshot()
        assert views[0].mem_mb == 0

    async def test_registry_passed_to_capacity_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        async def fake_build(slots: Any, registry: Any = None, **kw: Any) -> dict[str, Any]:
            seen["registry"] = registry
            return {}

        monkeypatch.setattr("hal0.slots.capacity.build_per_slot", fake_build)
        marker = object()
        agg = _agg(FakeSlotManager(slots=[_slot()]), registry=marker)
        await agg.snapshot()
        assert seen["registry"] is marker


# ── concern 5: metric injection ─────────────────────────────────────────────


class TestMetricInjection:
    async def test_metrics_remapped_to_card_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_build(slots: Any, registry: Any = None, **kw: Any) -> dict[str, Any]:
            return {"chat": {"mem_mb": 2048.0}}

        monkeypatch.setattr("hal0.slots.capacity.build_per_slot", fake_build)

        async def fake_metrics() -> dict[str, Any]:
            return {
                "chat": {
                    "tokens_per_sec": 22.84,
                    "ttft_seconds": 0.1234,
                    "ctx": 32768,
                    "kv_cache_usage": 0.4567,
                }
            }

        agg = _agg(FakeSlotManager(slots=[_slot()]), metrics=fake_metrics)
        views = await agg.snapshot()
        m = views[0].to_dict()["metrics"]
        assert m == {
            "toks": 22.8,
            "ttft": 123,
            "ctx": 32768,
            "kv": 45.7,
            "mem": 2.0,
        }

    async def test_absent_metrics_row_yields_defaults(self, no_mem: None) -> None:
        agg = _agg(FakeSlotManager(slots=[_slot()]))
        views = await agg.snapshot()
        assert views[0].to_dict()["metrics"] == {
            "toks": 0.0,
            "ttft": None,
            "ctx": 0,
            "kv": None,
            "mem": 0.0,
        }

    async def test_metrics_source_failure_never_breaks_snapshot(self, no_mem: None) -> None:
        async def boom() -> dict[str, Any]:
            raise RuntimeError("metrics path exploded")

        agg = _agg(FakeSlotManager(slots=[_slot()]), metrics=boom)
        views = await agg.snapshot()
        assert views[0].to_dict()["metrics"]["toks"] == 0.0


# ── synthetic upstream entries + composition ───────────────────────────────


class TestSyntheticEntries:
    def test_local_composite_serving_only_when_loaded(self) -> None:
        upstreams = FakeUpstreams([SimpleNamespace(name="hal0", kind="slot", url="http://l")])
        entries = synthesize_upstream_entries(
            upstreams,
            model_cache={"hal0": ["qwen3-4b"]},
            last_used_model={},
            loaded_models={"qwen3-4b"},
        )
        assert entries[0]["status"] == "serving"
        entries = synthesize_upstream_entries(
            upstreams,
            model_cache={"hal0": ["qwen3-4b"]},
            last_used_model={},
            loaded_models=set(),
        )
        assert entries[0]["status"] == "offline"

    def test_remote_serving_when_cache_populated(self) -> None:
        upstreams = FakeUpstreams([SimpleNamespace(name="haloai", kind="remote", url="http://r")])
        entries = synthesize_upstream_entries(
            upstreams,
            model_cache={"haloai": ["m-1"]},
            last_used_model={"haloai": "m-1"},
            loaded_models=set(),
        )
        e = entries[0]
        assert e["status"] == "serving"
        assert e["kind"] == "remote"
        assert e["model"] == "m-1"
        assert e["_synthetic"] is True


class TestSnapshotComposition:
    async def test_real_slots_win_over_synthetic_on_name_collision(self, no_mem: None) -> None:
        upstreams = FakeUpstreams(
            [
                SimpleNamespace(name="chat", kind="slot", url="http://l"),
                SimpleNamespace(name="haloai", kind="remote", url="http://r"),
            ]
        )
        agg = _agg(
            FakeSlotManager(slots=[_slot(name="chat")]),
            upstreams=upstreams,
            model_cache={"haloai": ["m-1"]},
        )
        views = await agg.snapshot()
        names = [v.name for v in views]
        assert names == ["chat", "haloai"]
        assert views[0].synthetic is False
        assert views[0].kind == "local"
        assert views[1].synthetic is True

    async def test_synthetic_view_omits_mem_mb(self, no_mem: None) -> None:
        upstreams = FakeUpstreams([SimpleNamespace(name="haloai", kind="remote", url="http://r")])
        agg = _agg(FakeSlotManager(), upstreams=upstreams, model_cache={"haloai": ["m-1"]})
        views = await agg.snapshot()
        payload = views[0].to_dict()
        assert "mem_mb" not in payload
        assert payload["metrics"]["mem"] == 0.0

    async def test_real_view_key_order_ends_with_mem_then_metrics(self, no_mem: None) -> None:
        agg = _agg(FakeSlotManager(slots=[_slot()]))
        views = await agg.snapshot()
        keys = list(views[0].to_dict().keys())
        assert keys[-2:] == ["mem_mb", "metrics"]

    async def test_lemonade_shim_failure_degrades_to_no_enrichment(self, no_mem: None) -> None:
        agg = _agg(
            FakeSlotManager(
                slots=[_slot()],
                configs=[_llm_cfg()],
            ),
            shim=FakeLemonadeShim(raise_exc=True),
        )
        views = await agg.snapshot()
        # Health unreadable → slot still listed, enrichment degrades to idle.
        assert views[0].to_dict()["lemonade_state"] == "idle"

    async def test_snapshot_returns_typed_views(self, no_mem: None) -> None:
        agg = _agg(FakeSlotManager(slots=[_slot()]))
        views = await agg.snapshot()
        assert all(isinstance(v, SlotView) for v in views)
        v = views[0]
        assert v.name == "chat"
        assert v.status == "ready"
        assert v.metrics.toks == 0.0

    async def test_loaded_model_names_parses_both_health_keys(self) -> None:
        health = {
            "loaded": [{"model_name": "a"}],
            "all_models_loaded": [{"model_name": "b"}, "junk", {"model_name": ""}],
        }
        assert loaded_model_names(health) == {"a", "b"}
        assert loaded_model_names({}) == set()
        assert loaded_model_names(None) == set()
