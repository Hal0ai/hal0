"""End-to-end routing tests for the NPU trio dispatch (containerized npu slot).

A single ``flm serve`` process inside the containerized ``npu`` slot answers
chat + STT + embed on ONE static port (from the npu slot's TOML). When a
request lands on ``/v1/embeddings`` or ``/v1/audio/transcriptions`` and its
``model`` matches an enabled ``device=npu`` shadow-role slot (``embed-npu``
/ ``stt-npu``), v1.py forwards it straight to that port via
``app.state.npu_trio_router`` (:class:`hal0.dispatcher.npu_trio.NpuTrioRouter`).

These tests drive the live FastAPI app via ``TestClient`` and verify:

  1. Trio-routed embed: request targeting embed-npu + npu container
     dispatchable → POSTs to ``<npu-port>/v1/embeddings``.
  2. Trio-routed STT: multipart forwarded verbatim to
     ``<npu-port>/v1/audio/transcriptions`` (boundary preserved).
  3. Trio-routed request while the npu container is NOT dispatchable →
     503 with the typed ``npu.trio_unavailable`` envelope.
  4. Disabled shadow slot → trio never called; standard dispatch runs.
  5. No NPU slots configured at all → standard dispatch.
  6. The model field drives the trio match (by ``model.default`` AND by
     slot name); a missing/non-matching model skips the trio.

The trio router itself has unit tests in
``tests/dispatcher/test_npu_trio.py`` — the point here is the wiring in
``hal0/api/routes/v1.py``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app

# The npu container's static port, as pinned in the seeded slot TOML.
_NPU_PORT = 14002


# ── Test fixtures ────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def seed_npu_trio(tmp_hal0_home: str) -> None:
    """Lay down the NPU trio slot TOMLs on disk.

    ``npu`` is the containerized anchor (static port, runtime=container);
    ``stt-npu`` / ``embed-npu`` are the shadow-role records whose model
    ids gate the trio dispatch in v1.py.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "npu",
        [
            'name = "npu"',
            f"port = {_NPU_PORT}",
            'device = "npu"',
            'type = "llm"',
            'runtime = "container"',
            "enabled = true",
            "[model]",
            'default = "gemma3:1b"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt-npu",
        [
            'name = "stt-npu"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = true",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "embed-npu",
        [
            'name = "embed-npu"',
            "port = 8085",
            'device = "npu"',
            'type = "embedding"',
            "enabled = true",
            "[model]",
            'default = "embed-gemma"',
        ],
    )


def _make_capture_transport(captures: dict[str, Any]) -> httpx.MockTransport:
    """MockTransport that records every request to the npu container port."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "127.0.0.1" and req.url.port == _NPU_PORT:
            captures["calls"].append(
                {
                    "url": str(req.url),
                    "path": req.url.path,
                    "method": req.method,
                    "content_type": req.headers.get("content-type", ""),
                    "body": req.content,
                }
            )
            if req.url.path == "/v1/embeddings":
                return httpx.Response(
                    200,
                    json={"data": [{"embedding": [0.1, 0.2, 0.3]}], "model": "embed-gemma"},
                )
            if req.url.path == "/v1/audio/transcriptions":
                return httpx.Response(200, json={"text": "hello from FLM"})
            return httpx.Response(404, json={"detail": f"unmocked npu path {req.url.path}"})
        return httpx.Response(404, json={"detail": f"unmocked host {req.url.host}"})

    return httpx.MockTransport(handler)


def _pin_npu_ready(client: TestClient) -> None:
    """Force the npu slot into the dispatchable ready-set (READY).

    ``NpuTrioRouter.resolve_npu_url`` gates on
    ``SlotManager.is_ready_for_dispatch("npu")`` — no container runs under
    test, so the slot would otherwise be OFFLINE and every trio dispatch
    would 503.
    """
    from hal0.slots.state import SlotState, SlotStateRecord

    sm = client.app.state.slot_manager
    sm._states["npu"] = SlotStateRecord(
        name="npu",
        state=SlotState.READY,
        model_id="gemma3:1b",
        port=_NPU_PORT,
        updated_at=time.time(),
        message="pinned by test fixture",
        extra={},
    )


@pytest.fixture
def trio_client(seed_npu_trio: None) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    """TestClient wired so the trio router's POSTs land on a capture stub.

    Returns ``(client, captures)``; ``captures["calls"]`` records every
    request the npu-container stub saw. The npu slot is pinned READY.
    """
    captures: dict[str, Any] = {"calls": []}
    app = create_app()
    with TestClient(app) as client:
        router = client.app.state.npu_trio_router
        assert router is not None, "lifespan must attach the NpuTrioRouter"
        router._http_client = httpx.AsyncClient(transport=_make_capture_transport(captures))
        _pin_npu_ready(client)
        yield client, captures


# ── /v1/embeddings: trio-routed ──────────────────────────────────────────


def test_embed_npu_routes_to_npu_container_when_dispatchable(
    trio_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """Happy path: embed against embed-npu's model goes straight to the
    npu container's static port — not through the dispatcher."""
    client, captures = trio_client

    r = client.post(
        "/v1/embeddings",
        json={"model": "embed-gemma", "input": "hello world"},
    )

    assert r.status_code == 200, r.text
    assert r.json() == {
        "data": [{"embedding": [0.1, 0.2, 0.3]}],
        "model": "embed-gemma",
    }
    embed_calls = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
    assert len(embed_calls) == 1, captures["calls"]
    assert embed_calls[0]["url"] == f"http://127.0.0.1:{_NPU_PORT}/v1/embeddings"


def test_embed_npu_preserves_request_body_verbatim(
    trio_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """Param forwarding (encoding_format, dimensions, …) is verbatim."""
    client, captures = trio_client

    body = {
        "model": "embed-gemma",
        "input": ["one", "two", "three"],
        "encoding_format": "float",
        "dimensions": 768,
    }
    r = client.post("/v1/embeddings", json=body)
    assert r.status_code == 200, r.text

    import json as _json

    embed_calls = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
    assert len(embed_calls) == 1
    decoded = _json.loads(embed_calls[0]["body"])
    assert decoded == body


def test_embed_npu_returns_503_when_npu_not_dispatchable(
    seed_npu_trio: None,
) -> None:
    """npu container OFFLINE → explicit typed envelope.

    The trio's shadow roles have no backend without the npu container in
    the dispatchable ready-set (READY/SERVING/IDLE). Surface a clear
    "load an NPU chat slot first" 503 rather than a mystery 404.
    """
    app = create_app()
    with TestClient(app) as client:
        # No _pin_npu_ready: the npu slot stays OFFLINE.
        r = client.post(
            "/v1/embeddings",
            json={"model": "embed-gemma", "input": "hello world"},
        )

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["code"] == "npu.trio_unavailable"
    assert "load an NPU chat slot first" in body["error"]["message"]


def test_embed_npu_skips_trio_when_slot_disabled(tmp_hal0_home: str) -> None:
    """Disabled embed-npu slot → trio router NOT called; the request flows
    through the normal dispatcher path (here: typed no-route 404)."""
    _seed_slot_toml(
        tmp_hal0_home,
        "embed-npu",
        [
            'name = "embed-npu"',
            "port = 8085",
            'device = "npu"',
            'type = "embedding"',
            "enabled = false",
            "[model]",
            'default = "embed-gemma"',
        ],
    )
    captures: dict[str, Any] = {"calls": []}
    app = create_app()
    with TestClient(app) as client:
        client.app.state.npu_trio_router._http_client = httpx.AsyncClient(
            transport=_make_capture_transport(captures)
        )
        r = client.post(
            "/v1/embeddings",
            json={"model": "embed-gemma", "input": "hello"},
        )
        # Trio never touched; the dispatcher found no upstream and raised
        # its typed NoRouteFound envelope.
        assert captures["calls"] == [], (
            f"trio router was called despite disabled embed-npu slot: {captures['calls']}"
        )
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "dispatch.no_route"


def test_embed_request_for_non_npu_model_skips_trio(
    trio_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """A request whose ``model`` matches neither the embed-npu slot's
    model.default nor its name flows through the normal dispatcher path,
    even with the npu container dispatchable."""
    client, captures = trio_client

    r = client.post(
        "/v1/embeddings",
        json={"model": "nomic-embed-text-v1.5", "input": "hello"},
    )
    assert captures["calls"] == [], f"trio called despite non-NPU model: {captures['calls']}"
    # The dispatcher took it instead; with nothing serving the model the
    # outcome is the typed no-route envelope.
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "dispatch.no_route"


def test_embed_request_matches_trio_by_slot_name(
    trio_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """Callers can pass either the slot's ``model.default`` OR the slot
    name itself (``embed-npu``) as ``model`` — both route through the
    trio. UI surfaces that show "embed-npu" as a dropdown option must
    work without exposing the underlying model id."""
    client, captures = trio_client

    r = client.post(
        "/v1/embeddings",
        json={"model": "embed-npu", "input": "by slot name"},
    )

    assert r.status_code == 200, r.text
    embed_calls = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
    assert len(embed_calls) == 1


# ── /v1/audio/transcriptions: trio-routed ────────────────────────────────


def test_stt_npu_routes_to_npu_container_when_dispatchable(
    trio_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """Happy path: STT against stt-npu's model goes directly to the npu
    container, multipart bytes forwarded verbatim."""
    client, captures = trio_client

    files = {"file": ("clip.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav")}
    data = {"model": "whisper-v3"}
    r = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert r.status_code == 200, r.text
    assert r.json() == {"text": "hello from FLM"}
    stt_calls = [c for c in captures["calls"] if c["path"] == "/v1/audio/transcriptions"]
    assert len(stt_calls) == 1, captures["calls"]
    assert stt_calls[0]["url"] == f"http://127.0.0.1:{_NPU_PORT}/v1/audio/transcriptions"
    # Multipart boundary preserved — the FLM side needs the boundary in
    # the content-type header or its parser fails.
    assert stt_calls[0]["content_type"].startswith("multipart/form-data")
    assert "boundary=" in stt_calls[0]["content_type"]


def test_stt_npu_returns_503_when_npu_not_dispatchable(seed_npu_trio: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        # npu slot stays OFFLINE — not dispatchable.
        files = {"file": ("clip.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav")}
        data = {"model": "whisper-v3"}
        r = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["code"] == "npu.trio_unavailable"
    assert "load an NPU chat slot first" in body["error"]["message"]


def test_stt_npu_skips_trio_when_slot_disabled(tmp_hal0_home: str) -> None:
    """Disabled stt-npu → trio NOT called; standard dispatch runs."""
    _seed_slot_toml(
        tmp_hal0_home,
        "stt-npu",
        [
            'name = "stt-npu"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = false",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    captures: dict[str, Any] = {"calls": []}
    app = create_app()
    with TestClient(app) as client:
        client.app.state.npu_trio_router._http_client = httpx.AsyncClient(
            transport=_make_capture_transport(captures)
        )
        files = {"file": ("clip.wav", b"RIFF\x00\x00", "audio/wav")}
        data = {"model": "whisper-v3"}
        client.post("/v1/audio/transcriptions", files=files, data=data)
        assert captures["calls"] == [], (
            f"trio router was called despite disabled stt-npu slot: {captures['calls']}"
        )


def test_stt_request_matches_trio_by_slot_name(
    trio_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """Passing ``model="stt-npu"`` (slot name) also routes through the trio."""
    client, captures = trio_client

    files = {"file": ("clip.wav", b"RIFF\x00\x00", "audio/wav")}
    data = {"model": "stt-npu"}
    r = client.post("/v1/audio/transcriptions", files=files, data=data)
    assert r.status_code == 200, r.text
    stt_calls = [c for c in captures["calls"] if c["path"] == "/v1/audio/transcriptions"]
    assert len(stt_calls) == 1


# ── No NPU trio configured at all ────────────────────────────────────────


def test_embed_with_no_npu_slots_configured_skips_trio(tmp_hal0_home: str) -> None:
    """A host without ANY NPU slots configured → trio gating never fires,
    the request goes through standard dispatch."""
    captures: dict[str, Any] = {"calls": []}
    app = create_app()
    with TestClient(app) as client:
        client.app.state.npu_trio_router._http_client = httpx.AsyncClient(
            transport=_make_capture_transport(captures)
        )
        client.post(
            "/v1/embeddings",
            json={"model": "embed-gemma", "input": "hello"},
        )
        assert captures["calls"] == [], captures["calls"]


# ── Edge: empty model field ──────────────────────────────────────────────


def test_embed_with_missing_model_skips_trio(
    trio_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """Embed request without a ``model`` field → trio match impossible →
    standard dispatcher path runs (path-default model resolution). The
    trio router must not be called."""
    client, captures = trio_client

    client.post("/v1/embeddings", json={"input": "no model field"})

    assert captures["calls"] == [], captures["calls"]
