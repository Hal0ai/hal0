"""POST /api/slots persists the capability profile the slot type implies (#1830).

End-to-end cover for the dashboard "New slot" modal's exact body shape
(``ui/src/dash/slots/CreateSlotModal.jsx`` sends
``{name,type,runtime,model,device,autoload,priority}`` — no ``profile``). The
inference itself lives at the ``SlotManager.create`` chokepoint; this pins the
route → TOML result, which is what the rc.5 validation run actually read off
the box (``/etc/hal0/slots/<name>.toml`` with no ``profile`` key → slot loads
``ready`` and 501s ``/v1/rerank``).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


@pytest.fixture
def isolated_app(tmp_hal0_home: str) -> FastAPI:
    # Instantiate after tmp_hal0_home is in place (same rationale as
    # test_slots_routes.isolated_app).
    return create_app()


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(isolated_app) as c:
        yield c


def _slot_toml(home: str, name: str) -> dict:
    return tomllib.loads(
        (Path(home) / "etc" / "hal0" / "slots" / f"{name}.toml").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("slot_type", "device", "expected"),
    [
        ("reranking", "gpu-rocm", "reranking"),
        ("reranking", "cpu", "reranking"),
        ("embedding", "gpu-rocm", "embedding"),
        ("embedding", "cpu", "embedding"),
    ],
)
def test_post_slots_persists_the_inferred_profile(
    tmp_hal0_home: str,
    isolated_client: TestClient,
    slot_type: str,
    device: str,
    expected: str,
) -> None:
    r = isolated_client.post(
        "/api/slots",
        json={
            "name": "zzskui",
            "type": slot_type,
            "runtime": "container",
            "model": "rcmodel",
            "device": device,
            "autoload": False,
            "priority": 50,
        },
    )
    assert r.status_code == 201, r.text
    assert _slot_toml(tmp_hal0_home, "zzskui").get("profile") == expected


def test_post_slots_leaves_an_llm_slot_profileless(
    tmp_hal0_home: str,
    isolated_client: TestClient,
) -> None:
    r = isolated_client.post(
        "/api/slots",
        json={
            "name": "zzskllm",
            "type": "llm",
            "runtime": "container",
            "model": "rcmodel",
            "device": "gpu-rocm",
            "autoload": False,
        },
    )
    assert r.status_code == 201, r.text
    assert not _slot_toml(tmp_hal0_home, "zzskllm").get("profile")
