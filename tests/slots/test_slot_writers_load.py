"""Every file a hal0 writer produces loads through the strict ``SlotConfig``.

``SlotConfig`` is ``extra="forbid"`` (ADR-0029 §5), so a writer that emits a key
the schema does not declare would write a slot that fails its next typed load.
Each test below drives one writer for real (no mocks of the writer itself),
then loads EVERY file it left in the slots directory through
:func:`hal0.config.loader.load_slot_config`. A writer that grows an undeclared
key fails here, naming the key and the file.

Writers covered: the shipped seeds and ``seed_static_slots``; ``SlotManager.create``
(the HTTP create body, ``add_slot``, the installer scaffold, the NPU backend
route, the stacks create body, the capability orchestrator's ``_ensure_slot_exists``
/ ``_ensure_slot_exists_npu``); ``SlotManager.update_config`` (every field the
dashboard drawer sends); the NPU trio shadow reconciler; the capability
``SlotConfigStore``; the stacks apply projection; ``migrate_slot_id_keying``; and
the profile-centric converter's output.

Listing tests at the bottom prove one slot with a stray key does not break the
enumeration of the others.

Targeted run:
    uv run pytest tests/slots/test_slot_writers_load.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.config import CapabilitySelection
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.config import paths
from hal0.config.loader import ConfigParseError, list_slots, load_slot_config
from hal0.config.schema import HardwareInfo
from hal0.install.static_seeds import STATIC_SEED_SLOTS, seed_static_slots
from hal0.ports.authority import PortAuthority
from hal0.slot_config import SlotConfigStore, SlotSelection, write_slot_toml
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager
from hal0.slots.migrate_id_keying import RecordingSlotArtifactOps, migrate_slot_id_keying

pytestmark = pytest.mark.usefixtures("container_stub")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _slots_dir() -> Path:
    d = paths.slots_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_everything() -> dict[str, Any]:
    """Load every slot file through the strict loader; return ``{stem: SlotConfig}``."""
    stems = list_slots()
    assert stems, "the writer under test left no slot file"
    return {stem: load_slot_config(stem) for stem in stems}


def _raw(stem: str) -> dict[str, Any]:
    return tomllib.loads((_slots_dir() / f"{stem}.toml").read_text(encoding="utf-8"))


# ── seeds ────────────────────────────────────────────────────────────────────


def test_seed_static_slots_output_loads(tmp_hal0_home: str) -> None:
    seeded = seed_static_slots(installer_root=_REPO_ROOT, hw=HardwareInfo())
    assert set(seeded) == set(STATIC_SEED_SLOTS)
    loaded = _load_everything()
    assert set(loaded) == set(STATIC_SEED_SLOTS)
    # The seeds keep what they ship: kind, profile and port survive the load.
    assert loaded["img"].type == "image"
    assert loaded["img"].provider == "comfyui"
    assert loaded["flm"].device == "npu"
    assert loaded["tts"].device == "cpu"


# ── SlotManager.create ───────────────────────────────────────────────────────

#: Every key the dashboard's create dialog / `POST /api/slots` body may carry.
_CREATE_BODY: dict[str, Any] = {
    "name": "custom",
    "type": "llm",
    "model": "qwen3-4b",
    "device": "gpu-vulkan",
    "provider": "llama-server",
    "profile": "chat",
    "binary": "llamacpp",
    "image_pin": "ghcr.io/hal0ai/hal0-runner-llamacpp:test",
    "n_gpu_layers": 20,
    "threads": 4,
    "metrics": True,
    "gpu_index": 0,
    "pinned": False,
    "priority": 40,
    "autoload": False,
    "idle_timeout_s": 120,
    "output_sanity": False,
    "system_prompt": "Be brief.",
    "server": {"env": {"HSA_OVERRIDE_GFX_VERSION": "11.0.0"}},
}


async def test_http_create_body_loads(tmp_hal0_home: str) -> None:
    from hal0.api.routes.slots import _normalize_create_body

    sm = SlotManager()
    body = _normalize_create_body(dict(_CREATE_BODY))
    await sm.create("custom", body)
    cfg = _load_everything()["custom"]
    assert cfg.model.default == "qwen3-4b"
    assert cfg.output_sanity is False
    assert cfg.system_prompt == "Be brief."
    assert cfg.server.env == {"HSA_OVERRIDE_GFX_VERSION": "11.0.0"}


async def test_create_with_tts_voice_defaults_and_tables_loads(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.create(
        "voice",
        {
            "name": "voice",
            "port": 8095,
            "type": "tts",
            "device": "cpu",
            "provider": "kokoro",
            "profile": "kokoro",
            "default_voice": "af_bella",
            "default_speed": 1.1,
            "default_response_format": "mp3",
        },
    )
    await sm.create(
        "pictures",
        {
            "name": "pictures",
            "port": 8188,
            "type": "image",
            "provider": "comfyui",
            "profile": "comfyui",
            "image": {"idle_restore_minutes": 5, "default_size": "512x512", "default_steps": 8},
        },
    )
    loaded = _load_everything()
    assert loaded["voice"].default_voice == "af_bella"
    assert loaded["pictures"].image_gen.default_steps == 8


async def test_add_slot_output_loads(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.add_slot("scribe", type="transcription", model="whisper-base", port=8090)
    cfg = _load_everything()["scribe"]
    assert cfg.type == "transcription"
    assert cfg.default is True  # first of its type becomes the type's default


async def test_installer_scaffold_loads(tmp_hal0_home: str) -> None:
    from hal0.install.orchestrate import _build_slot_cfg

    sm = SlotManager()
    await sm.create(
        "agent",
        _build_slot_cfg(slot="agent", model_id="", device="gpu-rocm", profile="agent", port=8081),
    )
    assert _load_everything()["agent"].profile == "agent"


async def test_npu_backend_route_body_loads(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.create(
        "flm-qwen3",
        {
            "name": "flm-qwen3",
            "port": 8088,
            "device": "npu",
            "type": "llm",
            "runtime": "container",
            "profile": "flm",
            "model": {"default": "qwen3:4b"},
        },
    )
    assert _load_everything()["flm-qwen3"].device == "npu"


async def test_stacks_create_missing_slots_body_loads(tmp_hal0_home: str) -> None:
    from hal0.api.routes.slots import _normalize_create_body

    sm = SlotManager()
    body = {
        "name": "stacked",
        "type": "llm",
        "model": "qwen3-4b",
        "device": "gpu-vulkan",
        "profile": "chat",
    }
    await sm.create("stacked", _normalize_create_body(body))
    assert _load_everything()["stacked"].model.default == "qwen3-4b"


# ── capability orchestrator ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("slot", "device", "provider", "model"),
    [
        ("embed", "gpu-vulkan", "llama-server", "nomic-embed:v1"),
        ("rerank", "gpu-vulkan", "llama-server", "bge-reranker"),
        ("tts", "cpu", "kokoro", "kokoro:82m"),
        ("stt", "cpu", "moonshine", "moonshine:base"),
        ("img", "gpu-rocm", "comfyui", "sdxl"),
    ],
)
async def test_orchestrator_ensure_slot_exists_loads(
    tmp_hal0_home: str, slot: str, device: str, provider: str, model: str
) -> None:
    orch = CapabilityOrchestrator(
        SlotManager(), config_path=Path(tmp_hal0_home) / "etc" / "hal0" / "capabilities.toml"
    )
    await orch._ensure_slot_exists(
        slot,
        CapabilitySelection(device=device, provider=provider, model=model, enabled=True),
    )
    assert _load_everything()[slot].device == device


async def test_orchestrator_ensure_slot_exists_npu_loads(tmp_hal0_home: str) -> None:
    orch = CapabilityOrchestrator(
        SlotManager(), config_path=Path(tmp_hal0_home) / "etc" / "hal0" / "capabilities.toml"
    )
    await orch._ensure_slot_exists_npu(
        "flm-embed",
        "embed",
        CapabilitySelection(device="npu", provider="flm", model="embed-gemma:300m", enabled=True),
    )
    assert _load_everything()["flm-embed"].type == "embedding"


# ── pull-and-activate (installer, brain, agent) ──────────────────────────────


class _Job:
    state = "pending"


@pytest.mark.parametrize("outcome", ["completed", "failed", "raises"])
async def test_pull_and_activate_writes_only_declared_keys(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """The activation stamp used to write ``[meta].pull_failed`` on a failed pull:
    a table nothing read. The in-process write guard now refuses undeclared keys
    (and the stamp is best-effort, so a refusal would have vanished silently), so
    the writer itself must stay inside the schema on every outcome."""
    from hal0.install.orchestrate import PullPlan, run_pull_and_activate
    from hal0.registry import pull as pull_mod

    async def _fake_run_pull(job: Any, **_kw: Any) -> None:
        if outcome == "raises":
            raise RuntimeError("network down")
        job.state = outcome

    monkeypatch.setattr(pull_mod, "run_pull", _fake_run_pull)
    sm = SlotManager()
    await sm.create("chat", {"name": "chat", "port": 8081, "type": "llm", "device": "cpu"})
    plan = PullPlan(model_id="qwen3-4b", job=_Job(), kwargs={}, slot_names=["chat"])
    if outcome == "raises":
        with pytest.raises(RuntimeError, match="network down"):
            await run_pull_and_activate(plan, slot_manager=sm)
    else:
        await run_pull_and_activate(plan, slot_manager=sm)
    cfg = _load_everything()["chat"]
    assert cfg.model.default == ("qwen3-4b" if outcome == "completed" else "")
    assert "meta" not in _raw("chat")


# ── SlotManager.update_config ────────────────────────────────────────────────

#: Every key the slot drawer / `PUT /api/slots/{name}/config` may send.
_UPDATE_BODY: dict[str, Any] = {
    "device": "gpu-vulkan",
    "provider": "llama-server",
    "profile": "chat",
    "binary": "llamacpp",
    "image_pin": "ghcr.io/hal0ai/hal0-runner-llamacpp:test",
    "n_gpu_layers": 0,
    "threads": 8,
    "metrics": False,
    "gpu_index": 1,
    "pinned": True,
    "priority": 10,
    "autoload": True,
    "idle_timeout_s": 0,
    "type": "llm",
    "default": True,
    "output_sanity": False,
    "system_prompt": "Be brief.",
    "model": {"default": "qwen3-4b"},
    "server": {"env": {"A": "b"}},
}


async def test_update_config_with_every_drawer_field_loads(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.create("custom", {"name": "custom", "port": 8091, "device": "cpu", "type": "llm"})
    await sm.update_config("custom", dict(_UPDATE_BODY))
    cfg = _load_everything()["custom"]
    assert cfg.pinned is True
    assert cfg.binary == "llamacpp"
    assert cfg.server.env == {"A": "b"}


async def test_update_config_npu_and_image_tables_load(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.create(
        "flm",
        {
            "name": "flm",
            "port": 8088,
            "device": "npu",
            "provider": "flm",
            "type": "llm",
            "profile": "flm",
            "model": {"default": "qwen3:4b"},
        },
    )
    await sm.update_config("flm", {"npu": {"asr": True, "embed": True, "chat": True}})
    await sm.create(
        "img",
        {"name": "img", "port": 8188, "type": "image", "provider": "comfyui", "profile": "comfyui"},
    )
    await sm.update_config("img", {"image": {"idle_restore_minutes": 3}})
    loaded = _load_everything()
    assert loaded["flm"].npu is not None and loaded["flm"].npu.asr is True
    assert loaded["img"].image_gen.idle_restore_minutes == 3


# ── NPU trio shadows ─────────────────────────────────────────────────────────


async def test_trio_shadows_load(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.create(
        "flm",
        {
            "name": "flm",
            "port": 8088,
            "device": "npu",
            "provider": "flm",
            "type": "llm",
            "runtime": "container",
            "profile": "flm",
            "model": {"default": "qwen3:4b"},
            "npu": {"chat": True, "asr": True, "embed": True},
        },
    )
    await sm.reconcile_npu_trio_slots()  # creates flm-stt / flm-embed
    loaded = _load_everything()
    assert loaded["flm-stt"].served_by == "flm"
    assert loaded["flm-stt"].type == "transcription"
    assert loaded["flm-embed"].served_by == "flm"
    # A drifted shadow is repaired in place and still loads.
    await sm.update_config("flm-stt", {"port": 9999})
    await sm.reconcile_npu_trio_slots()
    assert _load_everything()["flm-stt"].port == 8088


# ── SlotConfigStore (capabilities) and the stacks projection ─────────────────


def test_slot_config_store_reconciled_slot_loads(tmp_hal0_home: str) -> None:
    write_slot_toml(
        _slots_dir() / "tts.toml",
        {
            "name": "tts",
            "port": 8085,
            "type": "tts",
            "device": "cpu",
            "provider": "kokoro",
            "profile": "kokoro",
            "model": {"default": "kokoro:82m"},
        },
    )
    store = SlotConfigStore()
    before = _raw("tts")
    for enabled in (False, True):
        after = store._reconciled_slot(
            before,
            SlotSelection(
                slot="voice",
                child="tts",
                slot_name="tts",
                selection=CapabilitySelection(
                    device="gpu-rocm", provider="qwen3tts", model="qwen3-tts", enabled=enabled
                ),
            ),
        )
        assert after is not None
        write_slot_toml(_slots_dir() / "tts.toml", after)
        _load_everything()


def test_stacks_projection_loads(tmp_hal0_home: str) -> None:
    from hal0.slots.config_write import reconcile_and_guard_slot_config

    write_slot_toml(
        _slots_dir() / "agent.toml",
        {"name": "agent", "port": 8081, "type": "llm", "device": "cpu", "profile": "agent"},
    )
    merged = reconcile_and_guard_slot_config(
        "agent",
        _raw("agent"),
        {
            "model": {"default": "qwen3-4b"},
            "device": "gpu-vulkan",
            "provider": "llama-server",
            "profile": "chat",
        },
        slots_dir=_slots_dir(),
    )
    write_slot_toml(_slots_dir() / "agent.toml", merged)
    assert _load_everything()["agent"].model.default == "qwen3-4b"


# ── id-keying migration ──────────────────────────────────────────────────────


async def test_id_keying_migration_output_loads(tmp_hal0_home: str) -> None:
    db = Path(tmp_hal0_home) / "hal0.db"
    sm = SlotManager(
        identity_store=SlotIdentityStore(db_path=db),
        port_authority=PortAuthority(pool=(8081, 8200), db_path=db),
    )
    await sm.create(
        "img",
        {
            "name": "img",
            "port": 8188,
            "type": "image",
            "provider": "comfyui",
            "profile": "comfyui",
            "image": {"idle_restore_minutes": 9},
        },
    )
    await sm.create(
        "brain",
        {"name": "brain", "port": 8089, "type": "llm", "profile": "brain", "device": "cpu"},
    )
    report = migrate_slot_id_keying(
        identity=sm._identity,
        config_dir=_slots_dir(),
        data_dir=Path(tmp_hal0_home) / "var" / "lib" / "hal0" / "slots",
        ops=RecordingSlotArtifactOps(),
    )
    assert report.migrations
    loaded = _load_everything()
    assert {cfg.name for cfg in loaded.values()} == {"img", "brain"}
    assert all(stem.isdigit() for stem in loaded)
    assert all(cfg.id is not None for cfg in loaded.values())


# ── the profile-centric converter ────────────────────────────────────────────


def test_converter_output_loads(tmp_hal0_home: str) -> None:
    """A pre-ADR-0029 slot, edited by the converter, loads strictly.

    Carries every key the converter strips (including the retired ``lru``,
    ``enabled`` and ``backend``) in both on-disk shapes.
    """
    from hal0.config.migrations import profile_centric as pc
    from hal0.config.migrations.profile_centric_apply import _edit_slot

    flat = {
        "name": "old-flat",
        "port": 8081,
        "type": "llm",
        "backend": "vulkan",
        "enabled": True,
        "lru": True,
        "runtime": "container",
        "parallel": 2,
        "chat_template": "chatml",
        "ctx_size": 8192,
        "mtp": True,
        "enable_thinking": False,
        "vision": False,
        "workers": 2,
        "image": "ghcr.io/foo:v1",
        "model": {
            "default": "m",
            "context_size": 8192,
            "ctx_size": 8192,
            "n_gpu_layers": 4,
            "rope_freq_base": 1.0,
            "extra": {"a": 1},
        },
        "server": {"extra_args": "-fa on", "env": {"A": "b"}},
        "defaults": {"threads": 4},
    }
    nested = {
        "slot": {
            "name": "old-nested",
            "port": 8082,
            "backend": "rocm",
            "enabled": True,
            "lru": False,
            "parallel": 2,
        },
        "model": {"default": "m", "context_size": 4096},
        "server": {"extra_args": "--x"},
    }
    for raw in (flat, nested):
        name = raw.get("name") or raw["slot"]["name"]
        sp = pc.SlotPlan(file=f"{name}.toml", name=name, status="convert", profile_after="chat")
        sp.set_keys["device"] = "gpu-vulkan"
        write_slot_toml(_slots_dir() / f"{name}.toml", _edit_slot(raw, sp))
    loaded = _load_everything()
    assert set(loaded) == {"old-flat", "old-nested"}
    assert loaded["old-flat"].server.env == {"A": "b"}


def test_the_converter_strips_lru(tmp_hal0_home: str) -> None:
    from hal0.config.migrations import profile_centric as pc

    assert "lru" in pc.SLOT_DROP_KEYS


# ── one bad slot does not break the others ───────────────────────────────────


def _two_good_one_bad() -> None:
    d = _slots_dir()
    for name, port in (("alpha", 8081), ("beta", 8082)):
        write_slot_toml(
            d / f"{name}.toml",
            {
                "name": name,
                "port": port,
                "type": "llm",
                "device": "cpu",
                "profile": "chat",
                "model": {"default": "m"},
            },
        )
    write_slot_toml(
        d / "stray.toml",
        {
            "name": "stray",
            "port": 8083,
            "type": "llm",
            "device": "cpu",
            "profile": "agent",
            "model": {"default": "m"},
            "colour": "blue",
        },
    )


def test_the_strict_loader_fails_only_the_bad_slot(tmp_hal0_home: str) -> None:
    _two_good_one_bad()
    assert load_slot_config("alpha").name == "alpha"
    assert load_slot_config("beta").name == "beta"
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("stray")
    assert "'colour'" in str(exc.value)
    assert str(_slots_dir() / "stray.toml") in str(exc.value)


async def test_slot_manager_listing_survives_a_stray_key(tmp_hal0_home: str) -> None:
    _two_good_one_bad()
    sm = SlotManager()
    names = {s.name for s in await sm.list()}
    assert names == {"alpha", "beta", "stray"}
    cfgs, skipped = await sm.iter_configs_detailed()
    assert {c["name"] for c in cfgs} >= {"alpha", "beta"}
    assert "alpha" not in skipped and "beta" not in skipped


def test_profile_in_use_scan_survives_and_still_counts_the_stray_slot(tmp_hal0_home: str) -> None:
    from hal0.profiles import ProfileCatalog

    _two_good_one_bad()
    entries, unreadable = ProfileCatalog()._scan_slots()
    assert dict(entries) == {"alpha": "chat", "beta": "chat", "stray": "agent"}
    assert unreadable == []


def test_runner_image_usage_survives_a_stray_key(tmp_hal0_home: str) -> None:
    from hal0.api.routes.runner_images import _slot_image_usage

    _two_good_one_bad()
    usage = _slot_image_usage()
    assert {"alpha", "beta"} <= set(usage)
    assert "stray" not in usage


def test_referenced_model_scan_keeps_the_good_slots(tmp_hal0_home: str) -> None:
    from hal0.registry.discover import _collect_referenced_model_ids

    _two_good_one_bad()
    ids, complete = _collect_referenced_model_ids()
    assert "m" in ids
    assert complete is False  # the stray slot could not be read: say so, never guess


def test_api_slots_listing_survives_a_stray_key(client: Any) -> None:
    from unittest.mock import AsyncMock, patch

    _two_good_one_bad()
    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ),
        patch("hal0.providers.container.ContainerProvider.image_present", return_value=False),
    ):
        r = client.get("/api/slots")
    assert r.status_code == 200, r.text
    assert {e["name"] for e in r.json()} >= {"alpha", "beta", "stray"}
