"""Tests for [npu] asr/embed toggle fields on /api/slots (A8).

Verifies:
  - ``npu`` dict (with ``asr`` / ``embed`` bools) appears on container
    slots that carry a ``[npu]`` TOML section. The toggles are lifted by
    the slot_view enrichment (``config_enrichment`` /
    ``container_enrichment``) from the slot TOML's ``[npu]`` table.
  - Slots without a ``[npu]`` table do NOT have a ``npu`` key in the
    response (absent preferred over null for clean JSON contracts).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

# ── helpers ────────────────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app_with_npu_slots(tmp_hal0_home: str) -> FastAPI:
    """App with one NPU container slot (has [npu] table) and one plain chat slot."""
    # NPU container slot with [npu] toggles
    _seed_slot_toml(
        tmp_hal0_home,
        "npu",
        [
            'name = "npu"',
            "port = 8090",
            'type = "llm"',
            'device = "npu"',
            'profile = "flm"',
            "[model]",
            'default = "Llama-3.2-3B-Instruct"',
            "[npu]",
            "asr = true",
            "embed = false",
        ],
    )
    # Plain chat slot — no [npu] section
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'type = "llm"',
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    return create_app()


@pytest.fixture
def client_with_npu_slots(
    app_with_npu_slots: FastAPI,
) -> Iterator[TestClient]:
    with TestClient(app_with_npu_slots) as c:
        yield c


# ── npu toggle tests ────────────────────────────────────────────────────────────


def test_slot_list_includes_npu_toggles(
    client_with_npu_slots: TestClient,
) -> None:
    """Container NPU slot with [npu] table exposes npu={asr, embed} on /api/slots."""

    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "subprocess.run",
            return_value=MagicMock(stdout=b"inactive", returncode=3),
        ),
    ):
        r = client_with_npu_slots.get("/api/slots")

    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert "npu" in by_name, "npu slot must appear in list"
    slot = by_name["npu"]
    assert "npu" in slot, f"npu key must be present; got keys: {list(slot.keys())}"
    assert slot["npu"] == {"asr": True, "embed": False}


def test_slot_without_npu_table_omits_field(
    client_with_npu_slots: TestClient,
) -> None:
    """Slot without a [npu] section must NOT have a 'npu' key in the response."""

    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "subprocess.run",
            return_value=MagicMock(stdout=b"inactive", returncode=3),
        ),
    ):
        r = client_with_npu_slots.get("/api/slots")

    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert "chat" in by_name
    assert "npu" not in by_name["chat"], (
        "chat slot has no [npu] table; 'npu' key must be absent from response"
    )


def test_put_config_npu_roundtrip(
    tmp_hal0_home: str,
) -> None:
    """PUT /api/slots/npu/config {npu: {asr: true}} -> GET shows asr=true."""

    # Seed a slot with asr=false initially
    _seed_slot_toml(
        tmp_hal0_home,
        "npu",
        [
            'name = "npu"',
            "port = 8090",
            'type = "llm"',
            'device = "npu"',
            'profile = "flm"',
            "[model]",
            'default = "Llama-3.2-3B-Instruct"',
            "[npu]",
            "asr = false",
            "embed = false",
        ],
    )
    app = create_app()
    with (
        TestClient(app) as client,
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "subprocess.run",
            return_value=MagicMock(stdout=b"inactive", returncode=3),
        ),
    ):
        # Confirm initial state
        r = client.get("/api/slots")
        assert r.status_code == 200
        npu_slot = next(s for s in r.json() if s["name"] == "npu")
        assert npu_slot["npu"]["asr"] is False

        # Update asr → true
        r2 = client.put("/api/slots/npu/config", json={"npu": {"asr": True}})
        assert r2.status_code == 200, r2.text

        # Re-GET and verify asr is now true
        r3 = client.get("/api/slots")
        assert r3.status_code == 200
        npu_slot2 = next(s for s in r3.json() if s["name"] == "npu")
        assert "npu" in npu_slot2, "npu key must survive config update"
        assert npu_slot2["npu"]["asr"] is True
