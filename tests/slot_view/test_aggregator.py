"""Unit tests for hal0.slot_view — SlotViewAggregator + per-concern functions.

Issue #698: each enrichment concern that used to live inline in
``api/routes/slots.py:list_slots()`` gets a direct unit test against
fake stores — no HTTP, no five-piece app-state mock:

  1. slot-state serialization        → serialize_slot
  2. config-field lifting            → config_enrichment
  3. container systemctl/port probe  → container_enrichment
  4. per-slot memory accounting      → SlotViewAggregator.snapshot
  5. metric injection                → SlotViewAggregator.snapshot

The /api/slots wire shape is pinned by the existing route-level suites
(test_slots_routes / test_slots_container_state / test_slots_npu_fields)
which stay UNMODIFIED as the parity oracle.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hal0.slot_view import (
    SlotView,
    SlotViewAggregator,
    config_enrichment,
    container_enrichment,
    loaded_model_names_from_slots,
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
            _slot(backend=None, metadata={"backend": "vulkan", "provider": "llama-server"}),
            model_cache={},
        )
        assert out["backend"] == "vulkan"
        assert out["provider"] == "llama-server"

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


# ── concern 2: config-field lifting (pure TOML lift) ────────────────────────


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


class TestConfigEnrichment:
    def test_every_slot_gets_an_entry(self) -> None:
        # No container skip — config fields are lifted for every slot,
        # profile-backed or not.
        out = config_enrichment(
            [_llm_cfg(), _llm_cfg(name="gpu-chat", profile="vulkan-radv")],
        )
        assert set(out) == {"chat", "gpu-chat"}

    def test_no_runtime_state_keys(self) -> None:
        # config_enrichment is a pure TOML lift: live-state keys
        # (backend_url / actual_backend / backend_mismatch) never appear.
        out = config_enrichment([_llm_cfg(device="gpu-vulkan")])
        e = out["chat"]
        assert "backend_url" not in e
        assert "actual_backend" not in e
        assert "backend_mismatch" not in e

    def test_declared_backend_from_device(self) -> None:
        out = config_enrichment([_llm_cfg(device="gpu-vulkan")])
        assert out["chat"]["declared_backend"] == "vulkan"

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
        out = config_enrichment(configs)
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
        out = config_enrichment(configs)
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
        out = config_enrichment([cfg])
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
        out = config_enrichment([_llm_cfg()])
        e = out["chat"]
        assert e["enable_thinking"] is None
        assert e["n_gpu_layers"] == -1
        assert e["rope_freq_base"] is None
        assert e["idle_timeout_s"] is None
        assert e["workers"] is None
        assert e["llamacpp_args"] is None
        assert "npu" not in e


# ── loaded-model derivation from slot snapshots ──────────────────────────────


class TestLoadedModelNamesFromSlots:
    def test_dispatchable_ready_set_counts(self) -> None:
        slots = [
            _slot(name="a", state=SlotState.READY, model_id="m-ready"),
            _slot(name="b", state=SlotState.SERVING, model_id="m-serving"),
            _slot(name="c", state=SlotState.IDLE, model_id="m-idle"),
            _slot(name="d", state=SlotState.OFFLINE, model_id="m-offline"),
            _slot(name="e", state=SlotState.ERROR, model_id="m-error"),
        ]
        assert loaded_model_names_from_slots(slots) == {"m-ready", "m-serving", "m-idle"}

    def test_junk_entries_skipped(self) -> None:
        slots = [
            _slot(name="a", state=SlotState.READY, model_id=None),
            _slot(name="b", state=SlotState.READY, model_id=""),
            SimpleNamespace(state="ready", model_id=123),
        ]
        assert loaded_model_names_from_slots(slots) == set()

    def test_empty_input(self) -> None:
        assert loaded_model_names_from_slots([]) == set()


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

    async def test_every_slot_is_probed(self) -> None:
        # No container skip: a plain slot (no runtime/profile keys) is
        # probed too — every slot runs as a container now.
        out = await container_enrichment(
            [_llm_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(active=True, healthy=True),
        )
        assert out["chat"]["container_status"] == "running"

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

    async def test_config_enrichment_applied_to_real_slots(self, no_mem: None) -> None:
        agg = _agg(
            FakeSlotManager(
                slots=[_slot()],
                configs=[_llm_cfg()],
            ),
        )
        views = await agg.snapshot()
        payload = views[0].to_dict()
        # Pure config lift lands on the wire payload.
        assert payload["model_default"] == "qwen3-4b"
        assert payload["enabled"] is True
        # And NO legacy live-state key sneaks in.
        assert "backend_url" not in payload

    async def test_snapshot_returns_typed_views(self, no_mem: None) -> None:
        agg = _agg(FakeSlotManager(slots=[_slot()]))
        views = await agg.snapshot()
        assert all(isinstance(v, SlotView) for v in views)
        v = views[0]
        assert v.name == "chat"
        assert v.status == "ready"
        assert v.metrics.toks == 0.0
