"""End-to-end routing tests for the FLM trio dispatch (PR-19, plan §5).

When a request lands on ``/v1/embeddings`` or ``/v1/audio/transcriptions``
and the active slot is an enabled NPU shadow role (``embed-npu`` /
``stt-npu``), the request must bypass Lemonade's dispatcher and forward
directly to the FLM child process port discovered from ``/v1/health``.

These tests drive the live FastAPI app via ``TestClient`` and verify:

  1. Trio-routed embed: request targeting embed-npu + FLM chat loaded
     → POSTs to ``<flm-backend>/v1/embeddings``, NOT to Lemonade's
     ``/v1/embeddings``.
  2. Trio-routed STT: request targeting stt-npu + FLM chat loaded
     → POSTs multipart to ``<flm-backend>/v1/audio/transcriptions``.
  3. Trio-routed embed without FLM chat → 503 with the "load an NPU
     chat slot first" envelope.
  4. Disabled NPU slot → request flows through the normal dispatcher
     path (existing fallback). The trio router is never called.
  5. No NPU slot configured at all → standard dispatcher path.
  6. The model field correctly drives the trio match (matching by
     ``model.default`` AND by slot name).

The test surface stays small — the trio router itself has thorough unit
tests in ``tests/dispatcher/test_flm_trio.py``. The point here is to
prove the wiring inside ``hal0/api/routes/v1.py`` makes the gating call
and reaches the right destination.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.providers as providers_mod
from hal0.api import create_app
from hal0.lemonade.client import LemonadeClient
from hal0.providers.lemonade import LemonadeProvider
from hal0.upstreams.registry import Upstream

# ── Test fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def lemonade_state() -> dict[str, Any]:
    """Mutable handle for the lemond stub's /v1/health response.

    Tests override ``state["loaded"]`` to switch between "FLM chat
    loaded" and "FLM chat absent" scenarios.
    """
    return {"loaded": []}


@pytest.fixture
def trio_test_app(
    lemonade_state: dict[str, Any], tmp_hal0_home: str
) -> Iterator[tuple[FastAPI, dict[str, Any]]]:
    """Build a hal0 app whose Lemonade stub serves ``lemonade_state`` for
    /v1/health, AND whose FLM child stub records every inbound request.

    Returns ``(app, flm_captures)``:

      - ``app``: the FastAPI app, ready for :class:`TestClient`.
      - ``flm_captures``: a dict that tests inspect after a request to
        verify which URL/path/body the FLM child saw. Cleared between
        scenarios by re-running the fixture.

    The FLM child stub listens on ``http://127.0.0.1:14002`` (mirrors
    what real lemond reports in ``/v1/health.loaded[].backend_url``).
    """
    flm_captures: dict[str, Any] = {"calls": []}

    def transport_handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        path = req.url.path
        # Lemonade control-plane stub.
        if host == "test" or path == "/v1/health":
            if path == "/v1/health":
                return httpx.Response(200, json={"loaded": lemonade_state["loaded"]})
            if path in ("/v1/load", "/v1/unload"):
                return httpx.Response(200, json={"status": "ok"})
            if path == "/v1/embeddings":
                # If THIS path is hit on Lemonade we want to fail the
                # test loudly — trio routing should bypass lemond.
                flm_captures.setdefault("lemonade_embed_hits", []).append(
                    {"url": str(req.url), "body": req.content}
                )
                return httpx.Response(200, json={"data": [{"embedding": [0.0]}]})
            return httpx.Response(404, json={"detail": f"unmocked lemonade path {path}"})
        # FLM child stub — note the host:port matches what /v1/health
        # advertises as backend_url.
        if host == "127.0.0.1" and req.url.port == 14002:
            flm_captures["calls"].append(
                {
                    "url": str(req.url),
                    "path": path,
                    "method": req.method,
                    "content_type": req.headers.get("content-type", ""),
                    "body": req.content,
                }
            )
            if path == "/v1/embeddings":
                return httpx.Response(
                    200,
                    json={"data": [{"embedding": [0.1, 0.2, 0.3]}], "model": "embed-gemma"},
                )
            if path == "/v1/audio/transcriptions":
                return httpx.Response(200, json={"text": "hello from FLM"})
            return httpx.Response(404, json={"detail": f"unmocked FLM path {path}"})
        return httpx.Response(404, json={"detail": f"unmocked host {host} path {path}"})

    # Two clients sharing the same MockTransport instance — one is
    # bound to the Lemonade base URL (so /v1/health works as a
    # relative path), the other has no base URL so the trio router's
    # absolute ``http://127.0.0.1:14002/...`` URLs route through the
    # same transport. Both delegate to ``transport_handler`` which
    # discriminates on host/port.
    mock = httpx.MockTransport(transport_handler)
    lemonade_transport = httpx.AsyncClient(transport=mock, base_url="http://test")
    trio_transport = httpx.AsyncClient(transport=mock)
    # Drive the LemonadeProvider singleton at the same stub.
    provider = LemonadeProvider(client=LemonadeClient(http_client=lemonade_transport))
    original_provider = providers_mod._PROVIDERS["lemonade"]
    providers_mod._PROVIDERS["lemonade"] = provider
    # Stash the trio transport in the captures dict so the trio_client
    # fixture can wire it into the router (it has to wait for the
    # lifespan to construct the router first).
    flm_captures["_trio_transport"] = trio_transport

    app = create_app()
    try:
        yield app, flm_captures
    finally:
        providers_mod._PROVIDERS["lemonade"] = original_provider


def _seed_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def seed_npu_trio(tmp_hal0_home: str) -> None:
    """Lay down the FLM trio slot TOMLs on disk."""
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
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


@pytest.fixture
def trio_client(
    trio_test_app: tuple[FastAPI, dict[str, Any]],
    seed_npu_trio: None,
) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    """TestClient + the FLM capture dict in one tuple."""
    app, captures = trio_test_app
    with TestClient(app) as c:
        # The trio router needs the lemonade_client wired in; lifespan
        # constructs one via the idle driver but uses os.environ for the
        # api key — for tests we want it to reuse the stub provider's
        # client so /v1/health goes through the MockTransport. And we
        # also need the trio router's outbound httpx client to share
        # the mock transport so the absolute ``127.0.0.1:14002`` POSTs
        # land on the FLM stub instead of attempting real DNS.
        c.app.state.flm_trio_router._lemonade = providers_mod._PROVIDERS[  # type: ignore[attr-defined]
            "lemonade"
        ].client()
        c.app.state.flm_trio_router._http_client = captures["_trio_transport"]  # type: ignore[attr-defined]
        # Seed a fallback upstream so the dispatcher has somewhere to
        # land non-trio requests. The trio-routed ones should never
        # reach it.
        c.app.state.upstreams.upsert(
            Upstream(
                name="primary",
                kind="slot",
                url="http://127.0.0.1:8081/v1",
                slot_name="primary",
                auth_style="none",
            )
        )
        yield c, captures


def _flm_loaded_chat() -> list[dict[str, Any]]:
    """The /v1/health.loaded[] entry shape that signals "FLM chat live"."""
    return [
        {
            "model_name": "gemma3:1b",
            "recipe": "flm",
            "type": "llm",
            "backend_url": "http://127.0.0.1:14002",
        }
    ]


# ── /v1/embeddings: trio-routed ──────────────────────────────────────────


def test_embed_npu_routes_to_flm_child_when_chat_loaded(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """Happy path: embed against embed-npu's model goes directly to the
    FLM child, not to Lemonade."""
    client, captures = trio_client
    lemonade_state["loaded"] = _flm_loaded_chat()

    r = client.post(
        "/v1/embeddings",
        json={"model": "embed-gemma", "input": "hello world"},
    )

    assert r.status_code == 200, r.text
    assert r.json() == {
        "data": [{"embedding": [0.1, 0.2, 0.3]}],
        "model": "embed-gemma",
    }
    # The FLM child saw the request — at the expected path.
    embed_calls = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
    assert len(embed_calls) == 1, captures["calls"]
    assert embed_calls[0]["url"] == "http://127.0.0.1:14002/v1/embeddings"
    # Lemonade's /v1/embeddings was NOT called — that's the whole point.
    assert "lemonade_embed_hits" not in captures


def test_embed_npu_preserves_request_body_verbatim(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """Param forwarding (encoding_format, dimensions, …) is verbatim."""
    client, captures = trio_client
    lemonade_state["loaded"] = _flm_loaded_chat()

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


def test_embed_npu_returns_503_when_flm_chat_not_loaded(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """No FLM chat in loaded[] → explicit error envelope.

    Plan §5.3: the trio's shadow roles require the chat anchor to be
    loaded. Without it, surface a clear "load an NPU chat slot first"
    message rather than mysteriously 404-ing.
    """
    client, _ = trio_client
    lemonade_state["loaded"] = []  # nothing loaded

    r = client.post(
        "/v1/embeddings",
        json={"model": "embed-gemma", "input": "hello world"},
    )

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["code"] == "npu.trio_unavailable"
    assert "load an NPU chat slot first" in body["error"]["message"]


def test_embed_npu_skips_trio_when_slot_disabled(
    trio_test_app: tuple[FastAPI, dict[str, Any]],
    lemonade_state: dict[str, Any],
    tmp_hal0_home: str,
) -> None:
    """Disabled embed-npu slot → trio router NOT called, request flows
    through the normal dispatcher path."""
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
    # No NPU chat anchor seeded either.
    lemonade_state["loaded"] = _flm_loaded_chat()  # even with FLM loaded
    app, captures = trio_test_app
    with TestClient(app) as client:
        # Seed a fallback upstream so the dispatcher resolves somewhere.
        client.app.state.upstreams.upsert(
            Upstream(
                name="primary",
                kind="slot",
                url="http://127.0.0.1:9100/v1",
                slot_name="primary",
                auth_style="none",
            )
        )
        # Dispatcher will try to forward to ``http://127.0.0.1:9100/v1/embeddings``;
        # the MockTransport handler 404s unmocked hosts, which the test
        # treats as "dispatcher path attempted" — proving trio was skipped.
        r = client.post(
            "/v1/embeddings",
            json={"model": "embed-gemma", "input": "hello"},
        )
        # The dispatcher's forward path returned 404 (mock for the
        # primary upstream wasn't wired) — the exact status doesn't
        # matter; what matters is that NO call landed on the FLM child.
        flm_embed_hits = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
        assert flm_embed_hits == [], (
            f"trio router was called despite disabled embed-npu slot: {flm_embed_hits}"
        )
        # And request never succeeded — fallback dispatcher tried to
        # reach the (unmocked) primary upstream.
        assert r.status_code != 200, r.text


def test_embed_request_for_non_npu_model_skips_trio(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """A request whose ``model`` doesn't match the embed-npu slot's
    model.default flows through the normal dispatcher path, even with
    FLM chat loaded."""
    client, captures = trio_client
    lemonade_state["loaded"] = _flm_loaded_chat()

    # ``nomic-embed-text-v1.5`` is NOT the embed-npu default (which is
    # ``embed-gemma``). Trio gating should skip.
    r = client.post(
        "/v1/embeddings",
        json={"model": "nomic-embed-text-v1.5", "input": "hello"},
    )
    # The trio router must NOT have been called.
    flm_embed_hits = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
    assert flm_embed_hits == [], f"trio router was called despite non-NPU model: {flm_embed_hits}"
    # The dispatcher took it instead; outcome depends on registry binding,
    # but the FLM child was not touched.
    _ = r  # status not asserted; the gating decision is the assertion


def test_embed_request_matches_trio_by_slot_name(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """Callers can pass either the slot's ``model.default`` OR the slot
    name itself (``embed-npu``) as ``model`` — both route through the
    trio. UI surfaces that show "embed-npu" as a dropdown option must
    work without exposing the underlying model id."""
    client, captures = trio_client
    lemonade_state["loaded"] = _flm_loaded_chat()

    r = client.post(
        "/v1/embeddings",
        json={"model": "embed-npu", "input": "by slot name"},
    )

    assert r.status_code == 200, r.text
    flm_embed_hits = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
    assert len(flm_embed_hits) == 1


# ── /v1/audio/transcriptions: trio-routed ────────────────────────────────


def test_stt_npu_routes_to_flm_child_when_chat_loaded(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """Happy path: STT against stt-npu's model goes directly to FLM."""
    client, captures = trio_client
    lemonade_state["loaded"] = _flm_loaded_chat()

    files = {"file": ("clip.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav")}
    data = {"model": "whisper-v3"}
    r = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert r.status_code == 200, r.text
    assert r.json() == {"text": "hello from FLM"}
    stt_calls = [c for c in captures["calls"] if c["path"] == "/v1/audio/transcriptions"]
    assert len(stt_calls) == 1, captures["calls"]
    assert stt_calls[0]["url"] == "http://127.0.0.1:14002/v1/audio/transcriptions"
    # Multipart boundary preserved — the FLM side needs the boundary in
    # the content-type header or its parser fails.
    assert stt_calls[0]["content_type"].startswith("multipart/form-data")
    assert "boundary=" in stt_calls[0]["content_type"]


def test_stt_npu_returns_503_when_flm_chat_not_loaded(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    client, _ = trio_client
    lemonade_state["loaded"] = []  # no FLM chat

    files = {"file": ("clip.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav")}
    data = {"model": "whisper-v3"}
    r = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["code"] == "npu.trio_unavailable"
    assert "load an NPU chat slot first" in body["error"]["message"]


def test_stt_npu_skips_trio_when_slot_disabled(
    trio_test_app: tuple[FastAPI, dict[str, Any]],
    lemonade_state: dict[str, Any],
    tmp_hal0_home: str,
) -> None:
    """Disabled stt-npu → trio NOT called even with FLM chat loaded."""
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
    lemonade_state["loaded"] = _flm_loaded_chat()
    app, captures = trio_test_app
    with TestClient(app) as client:
        client.app.state.upstreams.upsert(
            Upstream(
                name="primary",
                kind="slot",
                url="http://127.0.0.1:9100/v1",
                slot_name="primary",
                auth_style="none",
            )
        )
        files = {"file": ("clip.wav", b"RIFF\x00\x00", "audio/wav")}
        data = {"model": "whisper-v3"}
        client.post("/v1/audio/transcriptions", files=files, data=data)
        flm_stt_hits = [c for c in captures["calls"] if c["path"] == "/v1/audio/transcriptions"]
        assert flm_stt_hits == [], (
            f"trio router was called despite disabled stt-npu slot: {flm_stt_hits}"
        )


def test_stt_request_matches_trio_by_slot_name(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """Passing ``model="stt-npu"`` (slot name) also routes through the trio."""
    client, captures = trio_client
    lemonade_state["loaded"] = _flm_loaded_chat()

    files = {"file": ("clip.wav", b"RIFF\x00\x00", "audio/wav")}
    data = {"model": "stt-npu"}
    r = client.post("/v1/audio/transcriptions", files=files, data=data)
    assert r.status_code == 200, r.text
    stt_calls = [c for c in captures["calls"] if c["path"] == "/v1/audio/transcriptions"]
    assert len(stt_calls) == 1


# ── No NPU trio configured at all ────────────────────────────────────────


def test_embed_with_no_npu_slots_configured_skips_trio(
    trio_test_app: tuple[FastAPI, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """A host without ANY NPU slots configured → trio router never gates,
    request goes through standard dispatch."""
    # Note: trio_test_app + tmp_hal0_home are wired but NO slot TOMLs
    # are seeded here.
    lemonade_state["loaded"] = []  # even if FLM is loaded; matters less
    app, captures = trio_test_app
    with TestClient(app) as client:
        client.app.state.upstreams.upsert(
            Upstream(
                name="primary",
                kind="slot",
                url="http://127.0.0.1:9100/v1",
                slot_name="primary",
                auth_style="none",
            )
        )
        client.post(
            "/v1/embeddings",
            json={"model": "embed-gemma", "input": "hello"},
        )
        flm_embed_hits = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
        assert flm_embed_hits == [], flm_embed_hits


# ── Edge: empty model field ──────────────────────────────────────────────


def test_embed_with_missing_model_skips_trio(
    trio_client: tuple[TestClient, dict[str, Any]],
    lemonade_state: dict[str, Any],
) -> None:
    """Embed request without a ``model`` field → trio match impossible
    → standard dispatcher path runs (which falls back on the default-
    embed model id). Trio router must not be called."""
    client, captures = trio_client
    lemonade_state["loaded"] = _flm_loaded_chat()

    client.post("/v1/embeddings", json={"input": "no model field"})

    flm_embed_hits = [c for c in captures["calls"] if c["path"] == "/v1/embeddings"]
    assert flm_embed_hits == [], flm_embed_hits
