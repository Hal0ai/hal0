"""The ``img`` capability picker lists the image catalog, and apply validates against it.

Before this, the picker built image rows from the LLM registry and ran them through the
LLM model-fit check. ``profile_name_for_fit`` answered the GPU chat profile for an image
selection on any GPU device (the ``device in {gpu-rocm, gpu-vulkan}`` branch came before the
image branch), so model-fit rejected every row and the picker was empty on a real ROCm host.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities import catalog
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.capabilities.profile_fit import profile_name_for_fit
from hal0.comfyui import inventory, paths
from hal0.comfyui.catalog import CATALOG, component_path
from hal0.errors import Conflict, NotFound
from tests.capabilities.test_orchestrator_reconciliation import FakeSlotManager

_SDXL_KEY = "conditioner.embedders.1.x"
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


# ── _comfyui_backends: the one lane gate ────────────────────────────────────


def test_comfyui_backends_on_a_rocm_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch)

    assert catalog._comfyui_backends() == ["gpu-vulkan"]


def test_comfyui_backends_empty_on_an_amd_host_without_the_rocm_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, rocm_lane=False)

    assert catalog._comfyui_backends() == []


def test_comfyui_backends_untouched_on_a_non_amd_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, amd=False, rocm_lane=False)

    assert catalog._comfyui_backends() == ["gpu-vulkan"]


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
    """Minimal on-disk state: the seeded ``img`` slot TOML, no capabilities.toml."""
    home = Path(tmp_hal0_home)
    slots_dir = home / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "img.toml").write_text(
        "\n".join(
            [
                'name = "img"',
                'type = "image"',
                "port = 8188",
                'provider = "comfyui"',
                'backend = "vulkan"',
                "",
            ]
        ),
        encoding="utf-8",
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
