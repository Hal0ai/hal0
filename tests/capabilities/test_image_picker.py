"""The ``img`` capability picker lists the image catalog, and apply validates against it.

Before this, the picker built image rows from the LLM registry and ran them through the
LLM model-fit check. ``profile_name_for_fit`` answered the GPU chat profile for an image
selection on any GPU device (the ``device in {gpu-rocm, gpu-vulkan}`` branch came before the
image branch), so model-fit rejected every row and the picker was empty on a real ROCm host.
"""

from __future__ import annotations

import json
import os
import struct
import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities import catalog
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.capabilities.profile_fit import profile_name_for_fit
from hal0.comfyui import inventory, paths
from hal0.comfyui.catalog import CATALOG, component_path
from hal0.errors import BadRequest, Conflict, NotFound
from hal0.providers._gpu import (
    GpuPreflightError,
    require_kfd_for_gpu_slot,
    runtime_lane_for_provider,
)
from hal0.providers.comfyui import ComfyUIProvider
from tests.capabilities.test_orchestrator_reconciliation import FakeSlotManager

_SDXL_KEY = "conditioner.embedders.1.x"
# The img slot as the installer seeds it: provider comfyui, device gpu-rocm, profile comfyui.
_SHIPPED_IMG_SEED = (
    Path(__file__).resolve().parents[2] / "installer" / "etc-hal0" / "slots" / "img.toml"
)
_ALL_BACKENDS = [{"id": "gpu-vulkan"}, {"id": "gpu-rocm"}, {"id": "cpu"}]


@pytest.fixture(autouse=True)
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "comfyui-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    assert paths.models_root().is_relative_to(tmp_path)
    return root


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


def _host(monkeypatch: pytest.MonkeyPatch, *, amd: bool = True, rocm_lane: bool = True) -> None:
    """A host that advertises every GPU lane; ``rocm_lane`` is whether /dev/kfd and a render node exist."""
    monkeypatch.setattr(catalog, "available_backends", lambda: list(_ALL_BACKENDS))
    monkeypatch.setattr(catalog, "host_is_amd_gpu", lambda: amd)
    monkeypatch.setattr(catalog, "rocm_lane_present", lambda: rocm_lane)


def _install(model_id: str) -> None:
    root = paths.models_root()
    for component in CATALOG[model_id].components:
        path = component_path(component, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def _checkpoint(relpath: str) -> None:
    path = paths.models_root() / "checkpoints" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({_SDXL_KEY: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}})
    body = raw.encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(body)) + body + b"\x00\x00")


# ── profile_fit: the image branch comes before the generic GPU branch ───────


@pytest.mark.parametrize("device", ["gpu-vulkan", "gpu-rocm", "cpu", ""])
def test_profile_fit_image_before_gpu(device: str) -> None:
    assert profile_name_for_fit("image", device, "comfyui") == "comfyui"


def test_profile_fit_chat_on_gpu_is_unchanged() -> None:
    """Only the image selection moved ahead of the GPU branch; chat still gets its GPU profile."""
    assert profile_name_for_fit("chat", "gpu-vulkan") not in (None, "comfyui")


# ── comfyui_backends: the one lane gate ────────────────────────────────────


def test_comfyui_backends_on_a_rocm_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch)

    assert catalog.comfyui_backends() == ["gpu-vulkan"]


def test_comfyui_backends_empty_on_an_amd_host_without_the_rocm_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, rocm_lane=False)

    assert catalog.comfyui_backends() == []


def test_comfyui_backends_untouched_on_a_non_amd_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, amd=False, rocm_lane=False)

    assert catalog.comfyui_backends() == ["gpu-vulkan"]


# ── the picker ──────────────────────────────────────────────────────────────


def test_image_picker_rows_on_rocm_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe that found the bug: a ROCm host's image picker was empty."""
    _host(monkeypatch)
    _checkpoint("sub/mine.safetensors")

    rows = catalog.models_for_capability("image", registry=None)

    assert rows
    renderable = {m.id for m in CATALOG.values() if m.api_renderable}
    user = {f["api_id"] for f in inventory.list_files() if "api_id" in f}
    assert user == {"ckpt/sub/mine"}
    assert {r["id"] for r in rows} == renderable | user
    for row in rows:
        assert [b["provider"] for b in row["backends"]] == ["comfyui"]
        assert [b["id"] for b in row["backends"]] == ["gpu-vulkan"]
        assert row["capabilities"] == ["image"]


def test_image_picker_rows_carry_the_install_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch)
    _install("sdxl-lightning")
    _checkpoint("mine.safetensors")

    rows = {r["id"]: r for r in catalog.models_for_capability("image", registry=None)}

    assert rows["sdxl-lightning"]["backends"][0]["downloaded"] is True
    assert rows["sdxl-turbo"]["backends"][0]["downloaded"] is False
    assert rows["sdxl-turbo"]["backends"][0]["pullable"] is True
    assert rows["ckpt/mine"]["backends"][0]["downloaded"] is True
    assert rows["ckpt/mine"]["backends"][0]["pullable"] is False


def test_image_picker_rows_skip_llm_model_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("an image row must not run the LLM model-fit check")

    monkeypatch.setattr("hal0.capabilities.catalog.evaluate_model_fit", _boom)

    backend = catalog.models_for_capability("image", registry=None)[0]["backends"][0]

    # "allowed" is the FitStatus an LLM row that clears model-fit carries, and the default
    # a row without a verdict gets, so the picker reads one vocabulary across capabilities.
    assert backend["fit_status"] == "allowed"
    assert backend["fit_reasons"] == []
    assert backend["profile"] == "comfyui"


def test_image_picker_rows_pass_the_display_fields_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch)

    rows = {r["id"]: r for r in catalog.models_for_capability("image", registry=None)}

    lightning = rows["sdxl-lightning"]
    assert lightning["display_name"] == CATALOG["sdxl-lightning"].label
    assert lightning["license"] == CATALOG["sdxl-lightning"].license
    assert lightning["size_gb"] == CATALOG["sdxl-lightning"].approx_gb
    assert sum(1 for r in rows.values() if r["recommended"]) == 1


def test_other_capabilities_keep_their_row_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The display fields appear only on rows that carry them (image)."""
    _host(monkeypatch)

    for row in catalog.models_for_capability("embed", registry=None):
        assert not {"display_name", "license", "recommended"} & set(row)


def test_image_picker_empty_without_rocm_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, rocm_lane=False)
    _checkpoint("mine.safetensors")

    assert catalog.models_for_capability("image", registry=None) == []


# ── apply: an image selection is validated against the image catalog ────────


@pytest.fixture
def home(tmp_hal0_home: str) -> Path:
    """On-disk state of a fresh install: the shipped ``img`` slot TOML, no capabilities.toml."""
    home = Path(tmp_hal0_home)
    slots_dir = home / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "img.toml").write_text(
        _SHIPPED_IMG_SEED.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return home


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "enabled": True,
        "model": "sdxl-lightning",
        "device": "gpu-vulkan",
        "provider": "comfyui",
    }
    body.update(overrides)
    return body


async def test_apply_img_not_downloaded_names_install_route(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host(monkeypatch)
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)  # type: ignore[arg-type]

    with pytest.raises(Conflict) as exc:
        await orch.apply("img", "img", _body())

    assert exc.value.status == 409
    assert exc.value.code == "capability.model_not_downloaded"
    details = exc.value.details
    assert details["model"] == "sdxl-lightning"
    assert details["downloaded"] is False
    # An image model is installed from the catalog, never pulled from the registry.
    assert details["pullable"] is False
    assert details["install"] == "POST /api/comfyui/models/sdxl-lightning/install"
    assert "POST /api/comfyui/models/sdxl-lightning/install" in str(exc.value)
    # The LLM pull route is not the remedy for an image model.
    assert "pull_endpoint" not in details
    assert "/api/models/sdxl-lightning/pull" not in str(exc.value)
    assert [c for c in fake.calls if c[0] in {"load", "swap", "create"}] == []
    assert not (home / "etc" / "hal0" / "capabilities.toml").exists()


async def test_apply_img_installed_ok(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch)
    _install("sdxl-lightning")
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())  # type: ignore[arg-type]

    result = await orch.apply("img", "img", _body())

    assert result["enabled"] is True
    assert result["model"] == "sdxl-lightning"


async def test_apply_img_user_checkpoint_ok(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch)
    _checkpoint("sub/mine.safetensors")
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())  # type: ignore[arg-type]

    result = await orch.apply("img", "img", _body(model="ckpt/sub/mine"))

    assert result["model"] == "ckpt/sub/mine"


async def test_apply_img_staging_an_uninstalled_model_while_disabled_is_allowed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host(monkeypatch)
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())  # type: ignore[arg-type]

    result = await orch.apply("img", "img", _body(enabled=False))

    assert result["enabled"] is False
    assert result["model"] == "sdxl-lightning"


async def test_apply_img_unknown_model_is_404(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch)
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())  # type: ignore[arg-type]

    with pytest.raises(NotFound) as exc:
        await orch.apply("img", "img", _body(model="no-such-model"))

    assert exc.value.code == "capability.unknown_model"


async def test_apply_img_never_consults_the_llm_registry_or_model_fit(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registry entry that shares the id must not turn the image gate into an LLM weights check."""
    _host(monkeypatch)

    class _Registry:
        def has(self, model_id: str) -> bool:
            raise AssertionError("the image branch must not look the model up in the registry")

        def list(self) -> list[Any]:
            return []

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the image branch must not run the LLM model-fit check")

    monkeypatch.setattr("hal0.capabilities.orchestrator.evaluate_model_fit", _boom)
    _install("sdxl-lightning")
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager(), registry=_Registry())  # type: ignore[arg-type]

    result = await orch.apply("img", "img", _body())

    assert result["model"] == "sdxl-lightning"


# ── apply on the slot as the installer ships it ─────────────────────────────
#
# The seeded img slot says ``device = "gpu-rocm"`` (ComfyUI's runner is ROCm-only, #2067) and
# first boot lifts that into the capability selection. The picker's one ComfyUI row is
# labelled ``gpu-vulkan``, the generic GPU row (#1941). Both name the same lane, so neither
# spelling may make `apply` refuse an installed model.


def _slot(home: Path) -> dict[str, Any]:
    with open(home / "etc" / "hal0" / "slots" / "img.toml", "rb") as f:
        return tomllib.load(f)


def _selection(home: Path) -> dict[str, Any]:
    with open(home / "etc" / "hal0" / "capabilities.toml", "rb") as f:
        return tomllib.load(f)["selections"]["img"]["img"]


async def _first_boot(home: Path) -> CapabilityOrchestrator:
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())  # type: ignore[arg-type]
    await orch.initialize_if_missing()
    return orch


async def test_first_boot_lifts_the_seeds_rocm_device_into_the_img_selection(home: Path) -> None:
    """The premise of the cases below: a real box's persisted img device is gpu-rocm."""
    await _first_boot(home)

    assert _selection(home)["device"] == "gpu-rocm"
    assert _selection(home)["provider"] == "comfyui"


@pytest.mark.parametrize(
    "partial",
    [
        pytest.param({"enabled": True, "model": "sdxl-lightning"}, id="no-device-in-the-request"),
        pytest.param(
            {"enabled": True, "model": "sdxl-lightning", "device": "gpu-rocm"}, id="gpu-rocm"
        ),
    ],
)
async def test_apply_img_on_the_seeded_rocm_device_ok(
    home: Path, monkeypatch: pytest.MonkeyPatch, partial: dict[str, Any]
) -> None:
    _host(monkeypatch)
    _install("sdxl-lightning")
    orch = await _first_boot(home)

    result = await orch.apply("img", "img", partial)

    assert result["enabled"] is True
    assert result["model"] == "sdxl-lightning"
    # The seed's contract (#2067) is untouched: still a ROCm comfyui slot with its profile.
    slot = _slot(home)
    assert (slot["device"], slot["provider"], slot["profile"]) == ("gpu-rocm", "comfyui", "comfyui")
    assert slot["model"]["default"] == "sdxl-lightning"


async def test_auto_select_keeps_the_seeded_slots_rocm_device(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-select applies ``gpu-rocm``, not the lane gate's ``gpu-vulkan``: the slot is not rewritten."""
    from hal0.comfyui.autoselect import auto_select_first_image_model

    _host(monkeypatch)
    assert catalog.comfyui_backends() == ["gpu-vulkan"]  # the value auto-select must not pass on
    _install("sdxl-lightning")
    orch = await _first_boot(home)

    await auto_select_first_image_model(CATALOG["sdxl-lightning"], orchestrator=orch)

    slot = _slot(home)
    assert (slot["device"], slot["provider"], slot["profile"]) == ("gpu-rocm", "comfyui", "comfyui")
    assert slot["model"]["default"] == "sdxl-lightning"
    assert _selection(home)["enabled"] is True
    assert _selection(home)["model"] == "sdxl-lightning"
    assert _selection(home)["device"] == "gpu-rocm"


async def test_apply_img_on_the_seeded_rocm_device_still_gates_on_the_install(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gpu-rocm spelling reads the same row, including its ``downloaded: false``."""
    _host(monkeypatch)
    orch = await _first_boot(home)

    with pytest.raises(Conflict) as exc:
        await orch.apply("img", "img", {"enabled": True, "model": "sdxl-lightning"})

    assert exc.value.code == "capability.model_not_downloaded"
    assert exc.value.details["install"] == "POST /api/comfyui/models/sdxl-lightning/install"
    assert exc.value.details["backend"] == "gpu-rocm"


@pytest.mark.parametrize("device", ["cpu", "gpu-cuda"])
async def test_apply_img_refuses_a_device_that_is_not_the_gpu_lane(
    home: Path, monkeypatch: pytest.MonkeyPatch, device: str
) -> None:
    """Only the GPU lane's two spellings are the picker's row; anything else is still illegal."""
    _host(monkeypatch)
    _install("sdxl-lightning")
    orch = await _first_boot(home)

    with pytest.raises(BadRequest) as exc:
        await orch.apply(
            "img", "img", {"enabled": True, "model": "sdxl-lightning", "device": device}
        )

    assert exc.value.code == "capability.illegal_backend_model_pair"
    assert exc.value.details["legal_backends"] == ["gpu-vulkan"]
    assert exc.value.details["backend"] == device


# ── the slot an apply writes is one the comfyui provider launches ───────────


def _kfd_and_render_node(tmp_path: Path) -> dict[str, Any]:
    """Faked device nodes the kfd preflight accepts, for the current uid."""
    kfd = tmp_path / "kfd"
    kfd.write_text("")
    dri = tmp_path / "dri"
    dri.mkdir()
    (dri / "renderD128").symlink_to("/dev/null")
    return {
        "kfd_path": str(kfd),
        "dri_dir": str(dri),
        "env": {},
        "amd_host": True,
        "runner_uid": os.getuid(),
    }


@pytest.mark.parametrize("device", ["gpu-rocm", "gpu-vulkan"])
async def test_the_slot_an_img_apply_writes_passes_the_providers_launch_gates(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, device: str
) -> None:
    """Task 5 auto-selects with ``device = comfyui_backends()[0]`` (``gpu-vulkan``); an operator
    on the seeded box sends ``gpu-rocm``. Whichever is written, the provider builds its env and
    the ROCm-lane kfd preflight treats it as the same ROCm slot."""
    _host(monkeypatch)
    _install("sdxl-lightning")
    orch = await _first_boot(home)

    await orch.apply("img", "img", {"enabled": True, "model": "sdxl-lightning", "device": device})

    slot = _slot(home)
    assert slot["device"] == device
    assert slot["provider"] == "comfyui"
    assert slot["profile"] == "comfyui"
    provider = ComfyUIProvider()
    env = provider.build_env(slot, {"path": ""})
    assert env["HAL0_BACKEND"] in {"rocm", "vulkan"}
    lane = runtime_lane_for_provider(provider)
    assert lane == "rocm"
    nodes = _kfd_and_render_node(tmp_path)
    require_kfd_for_gpu_slot("img", device=slot["device"], runtime_lane=lane, **nodes)  # no raise

    # Without the compute node the gate refuses both spellings alike.
    missing = {**nodes, "kfd_path": str(tmp_path / "no-kfd")}
    with pytest.raises(GpuPreflightError):
        require_kfd_for_gpu_slot("img", device=slot["device"], runtime_lane=lane, **missing)
