"""Tests for the curated image-gen entries + ComfyUI-subdir pull routing.

Two surfaces under test:

  1. The curated catalogue itself — every image entry validates and
     declares the metadata the workflow translator + pull layer need.
  2. ``hal0.registry.pull.run_pull`` honours ``comfyui_subdir`` so
     SDXL Turbo lands in ``/var/lib/hal0/comfyui/models/checkpoints/``
     instead of the default per-id models tree.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from hal0.config import paths
from hal0.registry.curated import CURATED_MODELS, get_curated
from hal0.registry.pull import (
    _comfyui_models_dir,
    _final_path_for_entry,
    make_job,
    run_pull,
)
from hal0.registry.store import ModelRegistry

# ─── Catalogue contract ───────────────────────────────────────────────────────


def test_curated_catalogue_includes_image_entries() -> None:
    """The named v1 image-gen picks must always be present."""
    ids = {m.id for m in CURATED_MODELS}
    assert {"sdxl-turbo", "sd-1.5-pruned-emaonly", "flux-schnell"}.issubset(ids)


def test_curated_image_entries_have_workflow_metadata() -> None:
    """Every image-gen entry needs model_class + comfyui_subdir + capability."""
    for m in CURATED_MODELS:
        if m.recommended_slot != "img":
            continue
        assert m.capability == "image", f"{m.id}: capability must be 'image'"
        assert m.model_class, f"{m.id}: model_class is required for image entries"
        assert m.comfyui_subdir, f"{m.id}: comfyui_subdir is required (drives pull-path routing)"
        # bundle_only image entries (#500) are Lemonade-stock models loaded
        # via the sd-cpp recipe (GGUF), not pulled into ComfyUI as
        # safetensors — exempt them from the ComfyUI-shipping-format check.
        if m.bundle_only:
            continue
        assert m.hf_file.endswith(".safetensors"), (
            f"{m.id}: image entries currently ship as safetensors"
        )


def test_curated_chat_entries_keep_chat_capability() -> None:
    """The chat picks must still default capability='chat' (no model_class)."""
    for m in CURATED_MODELS:
        if m.recommended_slot != "primary":
            continue
        assert m.capability == "chat"
        assert not m.model_class
        assert not m.comfyui_subdir


def test_get_curated_lookup_for_image_entries() -> None:
    sdxl = get_curated("sdxl-turbo")
    assert sdxl is not None
    assert sdxl.recommended_slot == "img"
    assert sdxl.model_class == "sdxl-turbo"


# ─── pull path routing ───────────────────────────────────────────────────────


def test_final_path_routes_to_comfyui_subdir(tmp_hal0_home: str) -> None:
    """An entry with comfyui_subdir lands under the ComfyUI models tree."""
    p = _final_path_for_entry(
        "sdxl-turbo",
        "sd_xl_turbo_1.0_fp16.safetensors",
        comfyui_subdir="checkpoints",
    )
    expected = (
        Path(tmp_hal0_home)
        / "var-lib"
        / "hal0"
        / "comfyui"
        / "models"
        / "checkpoints"
        / "sd_xl_turbo_1.0_fp16.safetensors"
    )
    assert p == expected


def test_final_path_falls_back_to_default_layout(tmp_hal0_home: str) -> None:
    """Without comfyui_subdir, the legacy /var/lib/hal0/models layout wins."""
    p = _final_path_for_entry("qwen3-4b", "qwen3-4b.gguf", comfyui_subdir=None)
    expected = Path(tmp_hal0_home) / "var-lib" / "hal0" / "models" / "qwen3-4b" / "qwen3-4b.gguf"
    assert p == expected


def test_comfyui_subdir_is_path_safe(tmp_hal0_home: str) -> None:
    """A malicious comfyui_subdir can't escape the comfyui/models tree."""
    p = _comfyui_models_dir("../../etc/passwd")
    # Sanitiser maps anything outside [A-Za-z0-9._-] to '-' and strips
    # leading dashes — result is a single flat name under comfyui/models.
    assert p.name == "etc-passwd"
    # Critically: the path stays directly under <hal0_home>/var-lib/hal0/comfyui/models/
    assert p.parent == paths.var_lib() / "comfyui" / "models"


def test_comfyui_subdir_empty_falls_back_to_checkpoints(tmp_hal0_home: str) -> None:
    """An empty/whitespace subdir lands in checkpoints/ as the safe default."""
    p = _comfyui_models_dir("")
    assert p.name == "checkpoints"


@pytest.mark.asyncio
async def test_run_pull_writes_to_comfyui_subdir(tmp_hal0_home: str) -> None:
    """End-to-end: a pull with comfyui_subdir lands under the right tree."""
    payload = b"FAKE-SAFETENSORS" + b"\x00" * 256
    digest = hashlib.sha256(payload).hexdigest()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={"Content-Length": str(len(payload))},
        )

    job = make_job("sdxl-turbo")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="stabilityai/sdxl-turbo",
            hf_file="sd_xl_turbo_1.0_fp16.safetensors",
            registry=registry,
            client=client,
            comfyui_subdir="checkpoints",
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert job.sha256 == digest
    final = Path(job.path)
    # The pull must land under <hal0_home>/var-lib/hal0/comfyui/models/checkpoints/
    assert "comfyui" in final.parts
    assert "checkpoints" in final.parts
    assert final.name == "sd_xl_turbo_1.0_fp16.safetensors"
    assert final.read_bytes() == payload


@pytest.mark.asyncio
async def test_run_pull_default_subdir_unchanged(tmp_hal0_home: str) -> None:
    """A pull without comfyui_subdir keeps the legacy models/<id>/ layout."""
    payload = b"GGUF" + b"\x00" * 1024

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"Content-Length": str(len(payload))})

    job = make_job("qwen3-4b")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="Qwen/Qwen3-4B-Instruct-GGUF",
            hf_file="qwen3-4b.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "completed"
    final = Path(job.path)
    assert "comfyui" not in final.parts
    assert "models" in final.parts
    assert "qwen3-4b" in final.parts
