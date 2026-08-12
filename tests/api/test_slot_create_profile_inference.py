"""Slot creation infers the capability profile (#1830).

Every explicit slot-creation path (``hal0 slot create``, the dashboard
"New slot" modal, a raw ``POST /api/slots``) used to persist a slot TOML with
NO ``profile`` key. Bound to an unstamped model (auto-scan / ``model add`` /
pull / curated all register with ``defaults: null``), the #1787 profile-template
gate in ``providers/container.py`` never fires, so an ``embedding`` /
``reranking`` slot launched with no ``--embedding`` / ``--reranking`` flag: it
reached ``state=ready`` and 501'd the one endpoint it exists to serve.

The fix infers the capability profile at the ONE creation chokepoint
(``_normalize_create_body``, shared by the slots route and stack apply), gated
on ``profile_fits_slot`` so an incoherent pick is dropped rather than written.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes.slots import _normalize_create_body
from hal0.slots.profile_adopt import infer_slot_profile, profile_for_slot_type


@pytest.fixture
def isolated_app(tmp_hal0_home: str) -> FastAPI:
    return create_app()


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(isolated_app) as c:
        yield c


# ── the (type, device) → profile rule ────────────────────────────────────────


@pytest.mark.parametrize(
    ("slot_type", "device", "expected"),
    [
        # The two capability types the 501 was reported for — the seed
        # embedding/reranking profiles are device-agnostic llama-server
        # profiles, so CPU boxes get them too (the rc.5 repro was CPU-only).
        ("embedding", "cpu", "embedding"),
        ("embedding", "gpu-rocm", "embedding"),
        ("embedding", "gpu-vulkan", "embedding"),
        ("reranking", "cpu", "reranking"),
        ("reranking", "gpu-rocm", "reranking"),
        # NPU embeddings run on the FLM runtime, not llama-server.
        ("embedding", "npu", "flm"),
        # Engine-switched capability types.
        ("tts", "cpu", "kokoro"),
        ("tts", "gpu-rocm", "qwen3-tts"),
        ("transcription", "cpu", "moonshine"),
        ("transcription", "npu", "flm"),
        ("image", "img", "comfyui"),
        # llm is deliberately left alone: no mode flag is at stake, so the
        # base tune stays the operator's choice.
        ("llm", "cpu", None),
        ("llm", "gpu-rocm", None),
    ],
)
def test_profile_for_slot_type(slot_type: str, device: str, expected: str | None) -> None:
    assert profile_for_slot_type(slot_type, device) == expected


def test_infer_slot_profile_drops_an_unfitting_pick(tmp_hal0_home: str) -> None:
    """A profile that doesn't fit the slot is never written."""
    # comfyui only supports the ``image`` slot type — an image slot mistakenly
    # created on a GPU device must not adopt it silently as a mode-changing tune.
    assert infer_slot_profile({"type": "image", "device": "img"}) == "comfyui"
    assert infer_slot_profile({"type": "llm", "device": "cpu"}) is None


# ── the create chokepoint ────────────────────────────────────────────────────


def test_normalize_create_body_infers_reranking_profile(tmp_hal0_home: str) -> None:
    out = _normalize_create_body(
        {"name": "zzsk", "type": "reranking", "device": "cpu", "model": "rcrerank"},
        port_start=8190,
        port_end=8195,
    )
    assert out["profile"] == "reranking"


def test_normalize_create_body_infers_embedding_profile(tmp_hal0_home: str) -> None:
    out = _normalize_create_body(
        {"name": "zzske", "type": "embedding", "device": "gpu-rocm", "model": "rcembed"},
        port_start=8190,
        port_end=8195,
    )
    assert out["profile"] == "embedding"


def test_normalize_create_body_keeps_an_explicit_profile(tmp_hal0_home: str) -> None:
    out = _normalize_create_body(
        {"name": "zzsk", "type": "embedding", "device": "cpu", "profile": "cpu-chat"},
        port_start=8190,
        port_end=8195,
    )
    assert out["profile"] == "cpu-chat"


def test_normalize_create_body_leaves_llm_slots_profileless(tmp_hal0_home: str) -> None:
    out = _normalize_create_body(
        {"name": "zzsk", "type": "llm", "device": "cpu", "model": "m"},
        port_start=8190,
        port_end=8195,
    )
    assert "profile" not in out


# ── end to end: the TOML the UI modal / CLI create actually gets ─────────────


def _read_slot_toml(home: str, name: str) -> dict:
    return tomllib.loads(
        (Path(home) / "etc" / "hal0" / "slots" / f"{name}.toml").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("slot_type", "device", "expected"),
    [("reranking", "cpu", "reranking"), ("embedding", "gpu-rocm", "embedding")],
)
def test_post_slots_persists_the_inferred_profile(
    tmp_hal0_home: str,
    isolated_client: TestClient,
    slot_type: str,
    device: str,
    expected: str,
) -> None:
    """The CreateSlotModal body shape (no ``profile``) still lands with one."""
    r = isolated_client.post(
        "/api/slots",
        json={
            "name": "zzskui",
            "type": slot_type,
            "runtime": "container",
            "model": "rcrerank",
            "device": device,
            "autoload": False,
            "priority": 50,
        },
    )
    assert r.status_code == 201, r.text
    assert _read_slot_toml(tmp_hal0_home, "zzskui").get("profile") == expected
