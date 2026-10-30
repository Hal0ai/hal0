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

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from hal0 import slot_view as sv_mod
from hal0.config.schema import ProfileConfig
from hal0.profiles import ProfileCatalog
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
        present: bool | None = True,
        raise_exc: bool = False,
    ) -> None:
        self._active = active
        self._healthy = healthy
        self._running_image = running_image
        self._present = present
        self._raise = raise_exc

    def is_active(self, slot: Any) -> bool:
        if self._raise:
            raise RuntimeError("podman exploded")
        return self._active

    async def health(self, port: int) -> dict[str, Any]:
        return {"ok": self._healthy}

    def running_image(self, slot: Any) -> str | None:
        return self._running_image

    def image_present(self, image: str) -> bool | None:
        # Tri-state since #1939: None == "the image store could not be asked".
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

    def test_config_drift_lifted_from_metadata(self) -> None:
        drift = {
            "drifted": True,
            "diffs": [{"key": "--ctx-size", "running": "4096", "rendered": "131072"}],
        }
        out = serialize_slot(_slot(metadata={"config_drift": drift}), model_cache={})
        assert out["config_drift"] == drift

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

    def test_serving_with_empty_models_downgraded_to_idle(self) -> None:
        # #792: a model-requiring slot cannot be "serving" with zero resident
        # models. With an authoritative (provided) model_cache that lists none,
        # the surfaced status must downgrade to idle (the documented "process
        # up but /v1/models empty" state, issue #31). The true FSM ``state``
        # is preserved; only the surfaced ``status`` is corrected.
        out = serialize_slot(
            _slot(state=SlotState.SERVING, model_id="qwen3-4b"),
            model_cache={},
        )
        assert out["status"] == "idle"
        assert out["state"] == "serving"

    def test_serving_with_resident_models_stays_serving(self) -> None:
        # Guard: a real loaded model means "serving" is honest — no downgrade.
        out = serialize_slot(
            _slot(state=SlotState.SERVING, model_id="qwen3-4b"),
            model_cache={"chat": ["qwen3-4b"]},
        )
        assert out["status"] == "serving"

    def test_self_managed_provider_serving_empty_not_downgraded(self) -> None:
        # Guard: kokoro/moonshine/vibevoice serve a baked-in model and report
        # no model_id, so an empty model list is not a lie — leave it alone.
        out = serialize_slot(
            _slot(
                state=SlotState.SERVING,
                model_id=None,
                metadata={"provider": "kokoro"},
            ),
            model_cache={},
        )
        assert out["status"] == "serving"


# ── concern 2: config-field lifting (pure TOML lift) ────────────────────────


def _llm_cfg(name: str = "chat", **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": name,
        "port": 8081,
        "type": "llm",
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

    def test_lifts_autoload_and_priority(self) -> None:
        """Effective autoload (shim: bound model + no key → true) and priority
        are lifted so the drawer renders without a per-slot /config fetch."""
        out = config_enrichment(
            [
                # cfg A: bound model, no autoload/priority keys → shim defaults.
                _llm_cfg(),
                # cfg B: explicit keys win.
                _llm_cfg(name="b", autoload=False, priority=10),
            ],
        )
        assert out["chat"]["autoload"] is True
        assert out["chat"]["priority"] == 50
        assert out["b"]["autoload"] is False
        assert out["b"]["priority"] == 10

    def test_coresident_group_for_npu_trio(self) -> None:
        configs = [
            _llm_cfg(name="agent", device="npu"),
            {
                "name": "stt-npu",
                "device": "npu",
                "type": "transcription",
                "model": {"default": "whisper-v3"},
            },
        ]
        out = config_enrichment(configs)
        assert out["agent"]["coresident_group"] == "npu-flm-trio"
        assert out["stt-npu"]["coresident_group"] == "npu-flm-trio"

    def test_no_coresident_group_without_a_live_anchor(self) -> None:
        """No NPU LLM anchor with a model bound → no trio (#1369)."""
        configs = [
            {
                "name": "stt-npu",
                "device": "npu",
                "type": "transcription",
                "model": {"default": "whisper-v3"},
            },
        ]
        out = config_enrichment(configs)
        assert "coresident_group" not in out["stt-npu"]

    def test_no_coresident_group_when_the_anchor_has_no_model(self) -> None:
        """The seeded model-less ``flm`` anchor doesn't make a trio real."""
        configs = [
            _llm_cfg(name="flm", device="npu", model={"default": ""}),
            {
                "name": "flm-stt",
                "device": "npu",
                "type": "transcription",
                "model": {"default": "whisper-v3"},
            },
        ]
        out = config_enrichment(configs)
        assert "coresident_group" not in out["flm-stt"]
        assert "coresident_group" not in out["flm"]

    def test_shadow_modality_lifted_from_the_anchor(self) -> None:
        """#1369: the shadow's ON/OFF pill reads the ANCHOR's [npu] table.

        The shadow's own config can't answer — it carries a placeholder model
        and (pre-#1369) a mirrored flag that drifted from FLM's launch flags.
        """
        configs = [
            _llm_cfg(name="flm", device="npu", npu={"asr": True, "embed": False}),
            {
                "name": "flm-stt",
                "device": "npu",
                "type": "transcription",
                "model": {"default": "whisper-v3"},
            },
            {
                "name": "flm-embed",
                "device": "npu",
                "type": "embedding",
                "model": {"default": "embed-gemma"},
            },
        ]
        out = config_enrichment(configs)
        assert out["flm-stt"]["npu_modality_active"] is True
        assert out["flm-embed"]["npu_modality_active"] is False
        # The anchor itself is not a shadow — no lifted modality key.
        assert "npu_modality_active" not in out["flm"]

    def test_shadow_modality_falls_back_to_legacy_load_asr_load_embed(self) -> None:
        """#1670: a pre-1.0 anchor with no [npu] table at all still honors
        providers/flm.py's legacy [defaults].load_asr/load_embed keys at
        launch — the lifted pill must agree, not render every NPU pill off."""
        configs = [
            _llm_cfg(
                name="flm",
                device="npu",
                defaults={"load_asr": True, "load_embed": False},
            ),
            {
                "name": "flm-stt",
                "device": "npu",
                "type": "transcription",
                "model": {"default": "whisper-v3"},
            },
            {
                "name": "flm-embed",
                "device": "npu",
                "type": "embedding",
                "model": {"default": "embed-gemma"},
            },
        ]
        out = config_enrichment(configs)
        assert out["flm-stt"]["npu_modality_active"] is True
        assert out["flm-embed"]["npu_modality_active"] is False

    def test_explicit_npu_table_outranks_stale_legacy_defaults(self) -> None:
        """[npu] is PRIMARY whenever it exists at all — an explicit
        asr=false must win even if legacy defaults.load_asr is still True,
        same precedence providers/flm.py itself uses."""
        configs = [
            _llm_cfg(
                name="flm",
                device="npu",
                npu={"asr": False},
                defaults={"load_asr": True},
            ),
            {
                "name": "flm-stt",
                "device": "npu",
                "type": "transcription",
                "model": {"default": "whisper-v3"},
            },
        ]
        out = config_enrichment(configs)
        assert out["flm-stt"]["npu_modality_active"] is False

    def test_anchor_npu_entry_folds_in_legacy_defaults(self) -> None:
        """The anchor's OWN lifted `entry["npu"]` (drawer/card seed) must
        also see the legacy fallback, not just the shadow's derived
        npu_modality_active."""
        configs = [
            _llm_cfg(name="flm", device="npu", defaults={"load_asr": True, "load_embed": True}),
        ]
        out = config_enrichment(configs)
        assert out["flm"]["npu"] == {"asr": True, "embed": True, "chat": True}

    def test_legacy_defaults_ignored_on_non_npu_slots(self) -> None:
        """A chat slot's unrelated [defaults] table (chat_template, etc.)
        must never be misread as NPU modality flags — the fallback is
        scoped to device=="npu" slots only."""
        configs = [
            _llm_cfg(name="chat", defaults={"load_asr": True, "load_embed": True}),
        ]
        out = config_enrichment(configs)
        assert "npu" not in out["chat"]

    def test_config_fields_surfaced(self) -> None:
        # ADR-0029: the slot payload carries placement and lifecycle; every
        # tune lives on the slot's profile. Stray tuning keys in a raw dict
        # (an unconverted file) are never lifted onto the payload.
        cfg = _llm_cfg(
            idle_timeout_s=900,
            n_gpu_layers=24,
            npu={"asr": True, "embed": False},
        )
        cfg["enable_thinking"] = True
        cfg["mtp"] = True
        cfg["vision"] = False
        cfg["chat_template"] = "chatml"
        cfg["workers"] = 2
        cfg["server"] = {"extra_args": "--flash-attn on"}
        cfg["model"]["rope_freq_base"] = 10000.0
        cfg["model"]["context_size"] = 32768
        e = config_enrichment([cfg])["chat"]
        assert e["type"] == "llm"
        assert e["model_default"] == "qwen3-4b"
        # #1369: no ``enabled`` key is lifted — ``model_default`` IS the
        # activation signal the dashboard reads.
        assert "enabled" not in e
        assert e["n_gpu_layers"] == 24
        assert e["idle_timeout_s"] == 900
        assert e["npu"] == {"asr": True, "embed": False, "chat": True}
        for removed in (
            "enable_thinking",
            "mtp",
            "vision",
            "chat_template",
            "workers",
            "llamacpp_args",
            "rope_freq_base",
            "ctx_max",
        ):
            assert removed not in e, removed

    def test_hw_grid_fields_surfaced_top_level(self) -> None:
        # spec-hw-slot-ownership §2/§3: NGL / THREADS / BINARY / image_pin are
        # top-level slot-owned hardware fields lifted for the edit-drawer grid.
        cfg = _llm_cfg(
            n_gpu_layers=99,
            threads=8,
            binary="rocmfpx",
            image_pin="ghcr.io/owner/repo:dbg",
        )
        e = config_enrichment([cfg])["chat"]
        assert e["n_gpu_layers"] == 99
        assert e["threads"] == 8
        assert e["binary"] == "rocmfpx"
        assert e["image_pin"] == "ghcr.io/owner/repo:dbg"

    def test_hw_grid_fields_absent_surface_as_null(self) -> None:
        # Absent HW fields surface as null (threads/binary/image_pin) so the
        # drawer dirty-tracks real on-disk state; NGL keeps its -1 sentinel.
        e = config_enrichment([_llm_cfg()])["chat"]
        assert e["n_gpu_layers"] == -1
        assert e["threads"] is None
        assert e["binary"] is None
        assert e["image_pin"] is None

    def test_binary_retired_alias_spelling_surfaces_verbatim(self) -> None:
        # The runner registry has no aliases any more (ADR-0028): a leftover
        # ``binary = "vulkanfpx"`` is just an unknown key, shown as stored for
        # the drawer's out-of-vocab option. ``hal0 runner-images migrate`` rewrites
        # it to ``standard`` on disk; the view never guesses.
        cfg = _llm_cfg(binary="vulkanfpx")
        e = config_enrichment([cfg])["chat"]
        assert e["binary"] == "vulkanfpx"

    def test_binary_current_key_surfaces_as_stored(self) -> None:
        cfg = _llm_cfg(binary="standard")
        e = config_enrichment([cfg])["chat"]
        assert e["binary"] == "standard"

    def test_binary_unknown_key_still_surfaces_verbatim(self) -> None:
        # Genuinely unknown keys are NOT rewritten — the drawer's
        # out-of-vocab option owns that surface (collapse spec §2).
        cfg = _llm_cfg(binary="ghost")
        e = config_enrichment([cfg])["chat"]
        assert e["binary"] == "ghost"

    def test_ngl_is_read_only_from_the_top_level(self) -> None:
        # NGL is slot placement (a top-level field). A nested
        # [model].n_gpu_layers is an unconverted tuning key: never read.
        cfg = _llm_cfg(n_gpu_layers=40)
        cfg["model"]["n_gpu_layers"] = 12
        assert config_enrichment([cfg])["chat"]["n_gpu_layers"] == 40
        nested_only = _llm_cfg()
        nested_only["model"]["n_gpu_layers"] = 12
        assert config_enrichment([nested_only])["chat"]["n_gpu_layers"] == -1

    def test_absent_config_fields_surface_as_defaults(self) -> None:
        e = config_enrichment([_llm_cfg()])["chat"]
        assert e["n_gpu_layers"] == -1
        assert e["idle_timeout_s"] is None
        assert e["metrics"] is True
        assert "npu" not in e
        # The window is not a config-field lift: ``ctx_effective`` /
        # ``ctx_source`` come from the profile resolver at snapshot time.
        assert "ctx_max" not in e
        assert "ctx_effective" not in e


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


#: A slot-owned pinned image ref (spec-hw-slot-ownership §3) — the shortest
#: way to give a test slot a declared ``image`` so the image_status branch is
#: reachable at all.
_PINNED_IMAGE = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-server"


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
        # #2750: a profile-less llm slot launches a resolved runner image, so
        # the view reports the same ref the launch resolves.
        from hal0.providers.container import _resolve_image_ref

        assert e["image"] == _resolve_image_ref(_container_cfg(), None)
        assert e["image"]
        assert e["resolved_command"]

    async def test_a_profile_contributes_no_hardware_class(self, tmp_hal0_home: str) -> None:
        # ADR-0029: a profile selects no hardware (``device_class`` /
        # ``backend`` are gone from it), so the container enrichment lifts
        # neither from the slot's profile; the slot's device is the hardware.
        ProfileCatalog().create("explicit", ProfileConfig(flags="-fa auto", ctx_size=8192))
        out = await container_enrichment(
            [_container_cfg(profile="explicit")],
            pull_jobs={},
            provider=FakeContainerProvider(active=True, healthy=True),
        )
        e = out["gpu-chat"]
        assert e["profile"] == "explicit"
        assert "device_class" not in e
        assert "backend" not in e

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
        # #2750: the profile-less slot resolves a launch image, so the
        # running ref is compared against it and a verdict is reported.
        assert e["image"]
        assert e["image_mismatch"] is True

    async def test_inflight_pull_job_wins_image_status(self) -> None:
        job = SimpleNamespace(state="pulling")
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={"gpu-chat": job},
            provider=FakeContainerProvider(active=False),
        )
        assert out["gpu-chat"]["image_status"] == "pulling"

    async def test_bare_llm_slot_reports_resolved_image_status(self) -> None:
        # #2750: a bare llm slot (no profile, no pin) launches the runner
        # default, so the view reports it and probes it like any other slot.
        sv_mod._image_present_cache.clear()
        out = await container_enrichment(
            [_container_cfg()],
            pull_jobs={},
            provider=FakeContainerProvider(active=False, present=True),
        )
        assert out["gpu-chat"]["image"]
        assert out["gpu-chat"]["image_status"] == "present"

    async def test_image_resolve_error_means_unknown_status_and_logs(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # #2800: a raising image resolution is a fault (every container slot
        # resolves an image since #2750), so it reports unknown plus a log
        # line, not the quiet not-configured.
        import hal0.providers.container as container_mod

        def _boom(cfg: Any, prof: Any) -> str:
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(container_mod, "_resolve_image_ref", _boom)
        with caplog.at_level("WARNING"):
            out = await container_enrichment(
                [_container_cfg()],
                pull_jobs={},
                provider=FakeContainerProvider(active=False),
            )
        assert out["gpu-chat"]["image"] is None
        assert out["gpu-chat"]["image_status"] == "unknown"
        assert "reason=resolve-error" in caplog.text

    async def test_argv_preview_error_does_not_contradict_image_status(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # #2800 review: the argv preview raising is not an image-resolve
        # failure. The image resolved, so the status is the probed one and no
        # resolve-error line is logged; only resolved_command goes None.
        import hal0.providers.container as container_mod

        def _boom(cfg: Any) -> list[str]:
            raise RuntimeError("argv unbuildable")

        monkeypatch.setattr(container_mod, "resolved_command_for_slot", _boom)
        sv_mod._image_present_cache.clear()
        with caplog.at_level("WARNING"):
            out = await container_enrichment(
                [_container_cfg()],
                pull_jobs={},
                provider=FakeContainerProvider(active=False),
            )
        assert out["gpu-chat"]["image"]
        assert out["gpu-chat"]["resolved_command"] is None
        assert out["gpu-chat"]["image_status"] == "present"
        assert "reason=resolve-error" not in caplog.text

    async def test_pin_only_slot_reports_pin_and_probed_status(self) -> None:
        # A profileless slot whose image_pin declares the image used to answer
        # image=None / image_status="not-configured" while its unit launched
        # the pin (observed live: a pinned slot rendering "IMAGE STATUS —").
        # The view now resolves the same chain the launch does.
        sv_mod._image_present_cache.clear()
        out = await container_enrichment(
            [_container_cfg(image_pin=_PINNED_IMAGE)],
            pull_jobs={},
            provider=FakeContainerProvider(active=False, present=True),
        )
        e = out["gpu-chat"]
        assert e["image"] == _PINNED_IMAGE
        assert e["image_status"] == "present"

    async def test_unresolvable_profile_still_resolves_launch_image(
        self, tmp_hal0_home: str
    ) -> None:
        # A profile id the catalogue can't resolve (custom/stale) doesn't stop
        # the launch — _resolve_image_ref falls through to the pin/default —
        # so it must not stop the view either.
        sv_mod._image_present_cache.clear()
        out = await container_enrichment(
            [_container_cfg(profile="ghost-profile", image_pin=_PINNED_IMAGE)],
            pull_jobs={},
            provider=FakeContainerProvider(active=False, present=True),
        )
        e = out["gpu-chat"]
        assert e["image"] == _PINNED_IMAGE
        assert e["image_status"] == "present"

    # ── #1939: the four-state block is a five-state block ──────────────────
    #
    # `missing` is reserved for "podman was asked and said no". Every way of
    # NOT getting an answer — the seam is unusable, the provider raised, the
    # whole probe timed out — is `unknown`. Each of these three assertions
    # pins one site that used to answer with a confident lie.

    async def test_unanswerable_image_probe_reports_unknown_not_missing(
        self, tmp_hal0_home: str
    ) -> None:
        ProfileCatalog().create("imgprof", ProfileConfig(flags=""))
        out = await container_enrichment(
            [_container_cfg(profile="imgprof", image_pin=_PINNED_IMAGE)],
            pull_jobs={},
            provider=FakeContainerProvider(active=False, present=None),
        )
        e = out["gpu-chat"]
        assert e["image"] == _PINNED_IMAGE
        assert e["image_status"] == "unknown"

    async def test_declared_image_still_reports_present_and_missing(
        self, tmp_hal0_home: str
    ) -> None:
        """The tri-state must not swallow the two definitive answers — in
        particular ``missing`` has to stay reachable, or the #1937 regression
        check (``image_status: missing`` on a running slot is a defect) would
        be pinning a state nothing can ever emit."""
        ProfileCatalog().create("imgprof", ProfileConfig(flags=""))
        cfg = _container_cfg(profile="imgprof", image_pin=_PINNED_IMAGE)

        present = await container_enrichment(
            [cfg], pull_jobs={}, provider=FakeContainerProvider(active=False, present=True)
        )
        # The TTL cache is keyed by image ref and lives for 15 s, so the second
        # probe would otherwise be served the first one's answer.
        sv_mod._image_present_cache.clear()
        missing = await container_enrichment(
            [cfg], pull_jobs={}, provider=FakeContainerProvider(active=False, present=False)
        )

        assert present["gpu-chat"]["image_status"] == "present"
        assert missing["gpu-chat"]["image_status"] == "missing"

    async def test_probe_exception_reports_unknown_not_missing(
        self, tmp_hal0_home: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The except-branch around the image probe defaulted to ``missing``,
        so a provider that blew up looked exactly like an absent image.

        It also has to LOG. The payload carries no reason field by design, so
        an ``unknown`` with nothing behind it in the journal would be
        undiagnosable — which is the failure family this issue is about. The
        seam's own unknowns are logged by ``image_present``; this branch
        covers every unknown that never reached it.
        """
        ProfileCatalog().create("imgprof", ProfileConfig(flags=""))

        class _Exploding(FakeContainerProvider):
            def image_present(self, image: str) -> bool:
                raise RuntimeError("podman store is on fire")

        with caplog.at_level("WARNING"):
            out = await container_enrichment(
                [_container_cfg(profile="imgprof", image_pin=_PINNED_IMAGE)],
                pull_jobs={},
                provider=_Exploding(active=False),
            )
        assert out["gpu-chat"]["image_status"] == "unknown"
        assert "slot_view.image_probe_failed" in caplog.text
        assert "gpu-chat" in caplog.text
        assert "podman store is on fire" in caplog.text

    async def test_npu_table_surfaced(self) -> None:
        out = await container_enrichment(
            [_container_cfg(npu={"asr": True, "embed": True})],
            pull_jobs={},
            provider=FakeContainerProvider(active=False),
        )
        assert out["gpu-chat"]["npu"] == {"asr": True, "embed": True, "chat": True}

    async def test_npu_table_falls_back_to_legacy_defaults(self) -> None:
        """#1670: same legacy-defaults fallback as config_enrichment, for
        the /status aggregator's lifted entry."""
        out = await container_enrichment(
            [_container_cfg(device="npu", defaults={"load_asr": True, "load_embed": False})],
            pull_jobs={},
            provider=FakeContainerProvider(active=False),
        )
        assert out["gpu-chat"]["npu"] == {"asr": True, "embed": False, "chat": True}


class MapContainerProvider(FakeContainerProvider):
    """Per-slot ``is_active`` — the trio anchor runs, shadows have no unit."""

    def __init__(self, active_map: dict[str, bool], **kw: Any) -> None:
        super().__init__(**kw)
        self._active_map = active_map

    def is_active(self, slot: Any) -> bool:
        # Production passes the slot CONFIG (#1417 — the provider derives the
        # id-keyed instance token from it); index the per-slot map by name.
        name = str(slot.get("name") or "") if isinstance(slot, dict) else str(slot)
        return self._active_map.get(name, False)


def _npu_anchor_cfg(**over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": "npu",
        "port": 8088,
        "type": "llm",
        "device": "npu",
        "model": {"default": "gemma3:4b"},
    }
    cfg.update(over)
    return cfg


def _shadow_cfg(name: str = "embed", slot_type: str = "embedding", **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": name,
        "port": 8082,
        "type": slot_type,
        "device": "npu",
        "model": {"default": "embed-gemma:300m"},
    }
    cfg.update(over)
    return cfg


class TestShadowSlotStatusInheritance:
    """#733: embed/stt shadow slots have no unit/container of their own —
    the npu anchor's FLM process serves them coresident, so their live
    container status must be the anchor's, not their (always-absent) own."""

    @pytest.fixture(autouse=True)
    def no_real_systemctl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """container_enrichment's stopped-vs-crashed escalation shells out
        to a real ``systemctl is-active hal0-slot@{name}.service`` whenever
        the (fake, injected) provider reports a slot inactive — that raw
        subprocess call is not mediated by FakeContainerProvider /
        MapContainerProvider at all. On a live host with real hal0 slot
        units installed (e.g. ``hal0-slot@embed.service`` /
        ``hal0-slot@npu.service`` in a ``failed`` state), this leaks real
        host state into the test and flips the expected ``stopped`` into
        ``crashed``. Force the escalation probe to see a bare "inactive"
        unit so status is derived purely from the injected provider."""
        import subprocess

        class _FakeCompletedProcess:
            stdout = b"inactive\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeCompletedProcess())

    async def test_shadow_inherits_running_anchor(self) -> None:
        out = await container_enrichment(
            [
                _npu_anchor_cfg(),
                _shadow_cfg("embed", "embedding"),
                _shadow_cfg("stt", "transcription"),
            ],
            pull_jobs={},
            provider=MapContainerProvider({"npu": True}, healthy=True),
        )
        for shadow in ("embed", "stt"):
            assert out[shadow]["container_status"] == "running"
            assert out[shadow]["container_health"] is True
            assert out[shadow]["served_by"] == "npu"
        # anchor itself is untouched by the inheritance pass
        assert out["npu"]["container_status"] == "running"
        assert "served_by" not in out["npu"]

    async def test_stopped_anchor_propagates(self) -> None:
        out = await container_enrichment(
            [_npu_anchor_cfg(), _shadow_cfg()],
            pull_jobs={},
            provider=MapContainerProvider({"npu": False}),
        )
        assert out["embed"]["container_status"] == "stopped"
        assert out["embed"]["served_by"] == "npu"

    async def test_every_npu_shadow_inherits_from_a_live_anchor(self) -> None:
        """#1369: shadows carry placeholder models, so there is no per-shadow
        opt-out any more — a live anchor serves all of them coresident. (The
        old test pinned ``enabled=False`` suppressing inheritance; the modality
        gate now lives on the anchor's ``[npu]`` table instead.)"""
        out = await container_enrichment(
            [_npu_anchor_cfg(), _shadow_cfg()],
            pull_jobs={},
            provider=MapContainerProvider({"npu": True}, healthy=True),
        )
        assert out["embed"]["container_status"] == "running"
        assert out["embed"]["served_by"] == "npu"

    async def test_shadow_without_anchor_stays_stopped(self) -> None:
        out = await container_enrichment(
            [_shadow_cfg()],
            pull_jobs={},
            provider=MapContainerProvider({}),
        )
        assert out["embed"]["container_status"] == "stopped"
        assert "served_by" not in out["embed"]

    async def test_model_less_anchor_does_not_serve(self) -> None:
        """A model-less anchor isn't running FLM, so it can't serve shadows."""
        out = await container_enrichment(
            [_npu_anchor_cfg(model={"default": ""}), _shadow_cfg()],
            pull_jobs={},
            provider=MapContainerProvider({"npu": True}, healthy=True),
        )
        assert out["embed"]["container_status"] == "stopped"
        assert "served_by" not in out["embed"]

    async def test_non_npu_slots_unaffected(self) -> None:
        out = await container_enrichment(
            [_npu_anchor_cfg(), _container_cfg()],
            pull_jobs={},
            provider=MapContainerProvider({"npu": True}, healthy=True),
        )
        assert out["gpu-chat"]["container_status"] == "stopped"
        assert "served_by" not in out["gpu-chat"]


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


# ── concern 4.5: ctx_max EFFECTIVE-window re-resolution (#1788) ─────────────


class _FakeCtxModel:
    """A registry row whose file declares ``native`` tokens of context."""

    def __init__(self, native: int | None) -> None:
        self._native = native

    def model_dump(self) -> dict[str, Any]:
        return {"metadata": {"context_length": self._native} if self._native else {}}


class _FakeCtxRegistry:
    def __init__(self, models: dict[str, Any]) -> None:
        self._models = models

    def get(self, model_id: str) -> Any:
        if model_id not in self._models:
            raise KeyError(model_id)
        return self._models[model_id]


#: Not in the curated catalogue, so an unknown native window stays unknown.
_CTX_MODEL = "uncatalogued-ctx-test-model"


async def _ctx_view(cfg: dict[str, Any], native: int | None) -> dict[str, Any]:
    model_id = cfg["model"]["default"]
    agg = _agg(
        FakeSlotManager(slots=[_slot(name=cfg["name"], model_id=model_id)], configs=[cfg]),
        registry=_FakeCtxRegistry({model_id: _FakeCtxModel(native)}),
    )
    views = await agg.snapshot()
    return views[0].to_dict()


class TestCtxEffectiveResolution:
    """ADR-0029: ``ctx_effective`` / ``ctx_source`` come from the one resolver
    the launch path uses (the slot profile's ``ctx_size``, else the model's
    native window capped at 32768, else 8192). There is no slot ceiling."""

    @pytest.mark.parametrize(
        ("profile", "native", "expected"),
        [
            ("agent", 8000, (65536, "profile")),
            ("chat", 8000, (8000, "native")),
            ("chat", 262144, (32768, "native")),
            ("chat", None, (8192, "fallback")),
        ],
    )
    async def test_window_and_source(
        self,
        no_mem: None,
        tmp_hal0_home: str,
        profile: str,
        native: int | None,
        expected: tuple[int, str],
    ) -> None:
        cfg = _llm_cfg(profile=profile, model={"default": _CTX_MODEL})
        d = await _ctx_view(cfg, native)
        assert (d["ctx_effective"], d["ctx_source"]) == expected
        assert "ctx_max" not in d

    async def test_flm_slot_reports_its_profile_window(
        self, no_mem: None, tmp_hal0_home: str
    ) -> None:
        cfg = _llm_cfg(device="npu", profile="flm", model={"default": "gemma3:1b"})
        d = await _ctx_view(cfg, None)
        assert (d["ctx_effective"], d["ctx_source"]) == (16384, "profile")

    async def test_resolution_failure_degrades_to_none(
        self, no_mem: None, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A broken resolver must never take down the whole slots list.
        def boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("resolver exploded")

        monkeypatch.setattr("hal0.providers.container.slot_effective_context", boom)
        d = await _ctx_view(_llm_cfg(profile="chat", model={"default": _CTX_MODEL}), 8000)
        assert d["ctx_effective"] is None
        assert d["ctx_source"] is None

    async def test_missing_profile_has_no_window(self, no_mem: None, tmp_hal0_home: str) -> None:
        d = await _ctx_view(_llm_cfg(profile="no-such", model={"default": _CTX_MODEL}), 8000)
        assert (d["ctx_effective"], d["ctx_source"]) == (None, None)

    async def test_non_llm_slot_kind_has_no_window(self, no_mem: None, tmp_hal0_home: str) -> None:
        """#1859: a tts slot has no context-window concept — no ctx fields."""
        cfg = _llm_cfg(name="voice", type="tts", profile="kokoro", model={"default": "kokoro-v1"})
        d = await _ctx_view(cfg, 8000)
        assert "ctx_effective" not in d
        assert "ctx_source" not in d


def _write_slot_toml(home: str, name: str, lines: list[str]) -> None:
    slots = Path(home) / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    (slots / f"{name}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_profiles(home: str, body: str) -> None:
    etc = Path(home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "profiles.toml").write_text(body, encoding="utf-8")


def _npu_anchor(home: str, name: str, profile: str) -> None:
    _write_slot_toml(
        home,
        name,
        [
            f'name = "{name}"',
            "port = 8088",
            'device = "npu"',
            'type = "llm"',
            f'profile = "{profile}"',
        ],
    )


def _shadow(name: str, slot_type: str, served_by: str | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": name,
        "port": 0,
        "type": slot_type,
        "device": "npu",
        "model": {"default": ""},
    }
    if served_by is not None:
        cfg["served_by"] = served_by
    return cfg


class TestFlmTrioShadowWindow:
    """An FLM trio shadow (an NPU embedding / transcription slot) runs no
    process of its own: it reports its anchor's window — the slot named by
    ``served_by``, else the ``flm`` anchor (``container._served_by_anchor_cfg``)."""

    async def test_shadow_with_served_by_reports_its_anchors_window(
        self, no_mem: None, tmp_hal0_home: str
    ) -> None:
        # Two NPU anchors with different windows, so the assertion proves the
        # shadow followed ``served_by`` rather than the ``flm`` fallback.
        _write_profiles(tmp_hal0_home, '[profile.npu-32k]\nctx_size = 32768\nrunner = "flm"\n')
        _npu_anchor(tmp_hal0_home, "npu-main", "npu-32k")
        _npu_anchor(tmp_hal0_home, "flm", "flm")
        from hal0.providers.container import slot_effective_context

        embed = _shadow("embed-npu", "embedding", served_by="npu-main")
        d = await _ctx_view(embed, None)
        assert (d["ctx_effective"], d["ctx_source"]) == (32768, "profile")
        stt = _shadow("stt-npu", "transcription", served_by="npu-main")
        assert slot_effective_context(stt) == (32768, "profile")

    async def test_shadow_without_served_by_falls_back_to_the_flm_anchor(
        self, no_mem: None, tmp_hal0_home: str
    ) -> None:
        _npu_anchor(tmp_hal0_home, "flm", "flm")  # the flm seed: 16384
        from hal0.providers.container import slot_effective_context

        d = await _ctx_view(_shadow("embed-npu", "embedding"), None)
        assert (d["ctx_effective"], d["ctx_source"]) == (16384, "profile")
        assert slot_effective_context(_shadow("stt-npu", "transcription")) == (16384, "profile")

    async def test_an_8192_anchor_gives_both_shadows_8192(
        self, no_mem: None, tmp_hal0_home: str
    ) -> None:
        """The CT105 shape: the FLM anchor runs an 8192 window (a converted
        profile), and its embedding and transcription shadows report 8192,
        not the ``flm`` seed's 16384."""
        _write_profiles(tmp_hal0_home, '[profile.flm-ctx8192]\nctx_size = 8192\nrunner = "flm"\n')
        _npu_anchor(tmp_hal0_home, "flm", "flm-ctx8192")
        from hal0.providers.container import slot_effective_context

        d = await _ctx_view(_shadow("embed-npu", "embedding"), None)
        assert (d["ctx_effective"], d["ctx_source"]) == (8192, "profile")
        assert slot_effective_context(_shadow("stt-npu", "transcription")) == (8192, "profile")
        served = _shadow("embed-npu", "embedding", served_by="flm")
        assert slot_effective_context(served) == (8192, "profile")


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


class TestSpecMetrics:
    """metrics.spec: counters from the scrape + the plan fields (ADR-0031 §6)."""

    _PLAN: ClassVar[dict[str, Any]] = {
        "speculation": {
            "mode": "draft",
            "kind": "dflash",
            "variant": "q8",
            "unavailable_reason": None,
        }
    }

    def _cfg(self, model: str | None) -> dict[str, Any]:
        return {"name": "chat", "model": {"default": model} if model else {}}

    async def test_spec_carries_plan_and_counters(
        self, monkeypatch: pytest.MonkeyPatch, no_mem: None
    ) -> None:
        monkeypatch.setattr(
            "hal0.providers.container.resolved_argv_detail_for_slot", lambda cfg: self._PLAN
        )

        async def fake_metrics() -> dict[str, Any]:
            return {
                "chat": {
                    "spec": {
                        "active": True,
                        "draft_tokens": 300,
                        "accepted_tokens": 210,
                        "drafts": 80,
                        "acceptance": 0.7,
                        "mean_accepted_len": 3.6,
                        "live_acceptance": 0.5,
                    }
                }
            }

        agg = _agg(
            FakeSlotManager(slots=[_slot()], configs=[self._cfg("qwen3-4b")]),
            metrics=fake_metrics,
        )
        spec = (await agg.snapshot())[0].to_dict()["metrics"]["spec"]
        assert spec == {
            "active": True,
            "draft_tokens": 300,
            "accepted_tokens": 210,
            "drafts": 80,
            "acceptance": 0.7,
            "mean_accepted_len": 3.6,
            "live_acceptance": 0.5,
            "mode": "draft",
            "kind": "dflash",
            "variant": "q8",
            "unavailable_reason": None,
        }

    async def test_loaded_but_not_drafting_is_inactive(
        self, monkeypatch: pytest.MonkeyPatch, no_mem: None
    ) -> None:
        monkeypatch.setattr(
            "hal0.providers.container.resolved_argv_detail_for_slot", lambda cfg: self._PLAN
        )
        agg = _agg(FakeSlotManager(slots=[_slot()], configs=[self._cfg("qwen3-4b")]))
        spec = (await agg.snapshot())[0].to_dict()["metrics"]["spec"]
        assert spec["active"] is False
        assert spec["draft_tokens"] == 0
        assert spec["acceptance"] is None
        assert spec["live_acceptance"] is None
        assert spec["mode"] == "draft"

    async def test_spec_absent_when_slot_has_no_model(
        self, monkeypatch: pytest.MonkeyPatch, no_mem: None
    ) -> None:
        monkeypatch.setattr(
            "hal0.providers.container.resolved_argv_detail_for_slot", lambda cfg: self._PLAN
        )
        agg = _agg(FakeSlotManager(slots=[_slot(model_id=None)], configs=[self._cfg(None)]))
        assert "spec" not in (await agg.snapshot())[0].to_dict()["metrics"]

    async def test_plan_failure_never_breaks_snapshot(
        self, monkeypatch: pytest.MonkeyPatch, no_mem: None
    ) -> None:
        def boom(cfg: Any) -> Any:
            raise RuntimeError("plan exploded")

        monkeypatch.setattr("hal0.providers.container.resolved_argv_detail_for_slot", boom)
        agg = _agg(FakeSlotManager(slots=[_slot()], configs=[self._cfg("qwen3-4b")]))
        assert "spec" not in (await agg.snapshot())[0].to_dict()["metrics"]


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

    def test_synthetic_reason_differs_by_kind(self) -> None:
        """#1905: the local ``hal0`` composite isn't "backed by a remote
        upstream" — that reason string was copy-pasted onto every synthetic
        entry regardless of kind, so the hal0 tile lied about why it's
        synthetic. Only genuinely remote-backed entries get the "install a
        local slot" reason; the local composite gets its own copy."""
        upstreams = FakeUpstreams(
            [
                SimpleNamespace(name="hal0", kind="slot", url="http://l"),
                SimpleNamespace(name="haloai", kind="remote", url="http://r"),
                # Every loaded container slot registers a same-name
                # kind="slot" upstream — it must NOT claim to be the
                # composite (review nit: the composite string belongs to
                # the reserved name, not the kind).
                SimpleNamespace(name="chat", kind="slot", url="http://c"),
            ]
        )
        entries = synthesize_upstream_entries(
            upstreams,
            model_cache={},
            last_used_model={},
            loaded_models=set(),
        )
        by_name = {e["name"]: e for e in entries}
        assert "remote upstream" not in by_name["hal0"]["_synthetic_reason"]
        assert "composite" in by_name["hal0"]["_synthetic_reason"].lower()
        assert "remote upstream" in by_name["haloai"]["_synthetic_reason"]
        assert "composite" not in by_name["chat"]["_synthetic_reason"].lower()
        assert "remote upstream" not in by_name["chat"]["_synthetic_reason"]

    def test_synthetic_reason_explicit_remote_hal0_classified_by_kind(self) -> None:
        """An operator may define an explicit ``hal0`` upstream with
        ``kind="remote"`` in upstreams.toml — that entry is genuinely
        remote-backed, so the reserved name alone must not earn it the
        local-composite explanation."""
        from hal0.slot_view import _synthetic_reason

        reason = _synthetic_reason(SimpleNamespace(name="hal0", kind="remote"))
        assert "remote upstream" in reason
        assert "composite" not in reason.lower()


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
        assert "enabled" not in payload
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


class TestTuneInSnapshot:
    """ADR-0045: the snapshot lifts the raw ``[tune]`` and the merged ``tune_effective``."""

    async def test_snapshot_lifts_tune_and_effective(
        self, no_mem: None, tmp_hal0_home: str
    ) -> None:
        cfg = _llm_cfg(
            profile="chat",
            model={"default": _CTX_MODEL},
            tune={"vision": "off", "reasoning_effort": "high"},
        )
        d = await _ctx_view(cfg, 8000)
        assert d["tune"] == {"vision": "off", "reasoning_effort": "high"}
        te = d["tune_effective"]
        assert te["vision"] == "off" and te["sources"]["vision"] == "slot"
        assert te["sources"]["speculation"] == "profile"
        assert set(te) == {
            "ctx_size",
            "enable_thinking",
            "reasoning_effort",
            "reasoning_budget",
            "vision",
            "speculation",
            "sources",
            "reasoning_effort_unavailable",
        }

    async def test_snapshot_without_tune_is_empty_and_profile_sourced(
        self, no_mem: None, tmp_hal0_home: str
    ) -> None:
        d = await _ctx_view(_llm_cfg(profile="chat", model={"default": _CTX_MODEL}), 8000)
        assert d["tune"] == {}
        assert set(d["tune_effective"]["sources"].values()) <= {"profile", "profile-flags"}

    async def test_snapshot_ctx_source_slot(self, no_mem: None, tmp_hal0_home: str) -> None:
        cfg = _llm_cfg(profile="chat", model={"default": _CTX_MODEL}, tune={"ctx_size": 4096})
        d = await _ctx_view(cfg, 8000)
        assert (d["ctx_effective"], d["ctx_source"]) == (4096, "slot")
        assert d["tune_effective"]["ctx_size"] == 4096

    async def test_tune_effective_none_when_profile_missing_or_not_llm(
        self, no_mem: None, tmp_hal0_home: str
    ) -> None:
        d = await _ctx_view(_llm_cfg(profile="no-such", model={"default": _CTX_MODEL}), 8000)
        assert d["tune_effective"] is None
        d = await _ctx_view(_llm_cfg(device="npu", profile="flm", model={"default": "g:1b"}), None)
        assert d["tune_effective"] is None
