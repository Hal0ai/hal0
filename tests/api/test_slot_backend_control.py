"""Tests for the runtime-backend control endpoint (ADR-0022 B3).

POST /api/slots/{name}/backend switches a slot's runtime backend by
writing the slot's ``device`` field to TOML (rocm→gpu-rocm,
vulkan→gpu-vulkan, cpu→cpu, auto→clear) and restarting the slot when it
is currently loaded so the model reloads under the new backend.

Validation:
  - rocm/vulkan → 409 ``backend.build_missing`` when the build dir's
    ``llama-server`` binary is absent.
  - cpu / auto → always valid.
  - flm/npu → 400 ``backend.not_selectable``.

Idempotent: same backend already declared (and, when loaded, already the
actual backend) → no-op, ``reloaded: false``.

Response 200 carries the standard ``_slot_to_dict`` payload PLUS
``requested_backend`` / ``declared_backend`` / ``actual_backend`` /
``reloaded``.

Mocks Lemonade's HTTP surface (same pattern as test_slots_routes.py) and
monkeypatches the build-presence check + resolve_actual_backend so the
tests don't depend on the host's installed llama.cpp builds.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.api.routes.slots as slots_mod
import hal0.providers as providers_mod
import hal0.providers.lemonade as lemonade_mod
from hal0.api import create_app
from hal0.lemonade.client import LemonadeClient
from hal0.providers.lemonade import LemonadeProvider


@pytest.fixture
def lemonade_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Install a Lemonade stub provider; return its mutable state dict."""
    state: dict[str, Any] = {
        "loaded": [{"model_name": "qwen3-4b", "backend_url": "http://127.0.0.1:14002/v1"}],
        "load_calls": [],
        "unload_calls": [],
    }

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            body = json.loads(req.content.decode() or "{}")
            state["load_calls"].append(body)
            state["loaded"] = [
                {
                    "model_name": body.get("model_name", ""),
                    "backend_url": "http://127.0.0.1:14002/v1",
                }
            ]
            return httpx.Response(200, json={"status": "loaded"})
        if req.url.path == "/v1/unload":
            body = json.loads(req.content.decode() or "{}")
            state["unload_calls"].append(body)
            state["loaded"] = []
            return httpx.Response(200, json={"status": "unloaded"})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": state["loaded"]})
        return httpx.Response(404, json={"detail": f"unmocked {req.url.path}"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(h), base_url="http://test")
    provider = LemonadeProvider(client=LemonadeClient(http_client=transport))
    original = providers_mod._PROVIDERS["lemonade"]
    providers_mod._PROVIDERS["lemonade"] = provider
    try:
        yield state
    finally:
        providers_mod._PROVIDERS["lemonade"] = original


@pytest.fixture
def slot_toml(tmp_hal0_home: str) -> Path:
    """Write a primary.toml declaring device=gpu-vulkan."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "primary.toml"
    path.write_text(
        "\n".join(
            [
                'name = "primary"',
                "port = 8081",
                'device = "gpu-vulkan"',
                'provider = "lemonade"',
                "enabled = true",
                "[model]",
                'default = "qwen3-4b"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def all_builds_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend both rocm + vulkan llama-server binaries are installed."""
    monkeypatch.setattr(slots_mod, "_backend_build_present", lambda _b: True)


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def _read_device(slot_toml: Path) -> str | None:
    with slot_toml.open("rb") as fh:
        return tomllib.load(fh).get("device")


# ── happy path: switch vulkan → rocm (loaded → restart) ─────────────────────


def test_switch_to_rocm_writes_device_and_restarts_when_loaded(
    slot_toml: Path,
    lemonade_stub: dict[str, Any],
    all_builds_present: None,
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # After the restart the child runs rocm — make resolve_actual_backend say so.
    monkeypatch.setattr(lemonade_mod, "resolve_actual_backend", lambda _e: "rocm")
    r = isolated_client.post("/api/slots/primary/backend", json={"backend": "rocm"})
    assert r.status_code == 200, r.text
    body = r.json()
    # device persisted to TOML.
    assert _read_device(slot_toml) == "gpu-rocm"
    # response contract.
    assert body["requested_backend"] == "rocm"
    assert body["declared_backend"] == "rocm"
    assert body["reloaded"] is True
    # standard slot payload still present.
    assert body["name"] == "primary"
    # a restart cycle issued an unload + load.
    assert lemonade_stub["unload_calls"], "restart should issue an unload"
    assert lemonade_stub["load_calls"], "restart should issue a load"


# ── device alias + gpu- normalization ───────────────────────────────────────


def test_accepts_device_alias_and_normalizes_gpu_form(
    slot_toml: Path,
    lemonade_stub: dict[str, Any],
    all_builds_present: None,
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lemonade_mod, "resolve_actual_backend", lambda _e: "rocm")
    r = isolated_client.post("/api/slots/primary/backend", json={"device": "gpu-rocm"})
    assert r.status_code == 200, r.text
    assert r.json()["requested_backend"] == "rocm"
    assert _read_device(slot_toml) == "gpu-rocm"


# ── 409 build_missing ────────────────────────────────────────────────────────


def test_rocm_build_missing_returns_409(
    slot_toml: Path,
    lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # rocm build absent, vulkan present.
    monkeypatch.setattr(slots_mod, "_backend_build_present", lambda b: b != "rocm")
    r = isolated_client.post("/api/slots/primary/backend", json={"backend": "rocm"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "backend.build_missing"
    # device must NOT have changed.
    assert _read_device(slot_toml) == "gpu-vulkan"


# ── 400 not_selectable for flm/npu ───────────────────────────────────────────


@pytest.mark.parametrize("bad", ["flm", "npu"])
def test_flm_npu_rejected_400(
    bad: str,
    slot_toml: Path,
    lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    r = isolated_client.post("/api/slots/primary/backend", json={"backend": bad})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "backend.not_selectable"


# ── auto clears device ───────────────────────────────────────────────────────


def test_auto_clears_device(
    slot_toml: Path,
    lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lemonade_mod, "resolve_actual_backend", lambda _e: None)
    r = isolated_client.post("/api/slots/primary/backend", json={"backend": "auto"})
    assert r.status_code == 200, r.text
    # device cleared (empty string written).
    dev = _read_device(slot_toml)
    assert not dev, f"expected device cleared, got {dev!r}"
    assert r.json()["requested_backend"] == "auto"


# ── idempotent no-op ─────────────────────────────────────────────────────────


def test_idempotent_same_backend_no_reload(
    slot_toml: Path,
    lemonade_stub: dict[str, Any],
    all_builds_present: None,
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requesting the already-declared backend (and matching actual) is a no-op."""
    # Declared is vulkan; the live child is also vulkan → fully idempotent.
    monkeypatch.setattr(lemonade_mod, "resolve_actual_backend", lambda _e: "vulkan")
    r = isolated_client.post("/api/slots/primary/backend", json={"backend": "vulkan"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reloaded"] is False
    assert body["declared_backend"] == "vulkan"
    # No restart cycle.
    assert not lemonade_stub["unload_calls"], "idempotent no-op must not restart"
    # device unchanged.
    assert _read_device(slot_toml) == "gpu-vulkan"


# ── missing body ─────────────────────────────────────────────────────────────


def test_missing_backend_field_400(
    slot_toml: Path,
    lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    r = isolated_client.post("/api/slots/primary/backend", json={})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "backend.missing"
