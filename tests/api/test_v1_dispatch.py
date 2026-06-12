"""Wiring tests for the /v1 router after the forward() landing.

These tests exercise the full FastAPI stack (lifespan, dispatcher,
upstream registry) with an empty upstream catalog — enough to verify
the wire-up without needing a live model.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_v1_models_returns_empty_list_with_no_upstreams(client: TestClient) -> None:
    """GET /v1/models returns the OpenAI shape with an empty data array."""
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"] == []


def test_v1_chat_completions_no_route_returns_typed_404(client: TestClient) -> None:
    """POST /v1/chat/completions with no upstreams → 404 dispatch.no_route.

    The catch-all proxy fall-through is gone (epic #687): NoRouteFound
    from the dispatcher surfaces directly as its typed error envelope.
    """
    r = client.post(
        "/v1/chat/completions",
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "dispatch.no_route"


def test_v1_completions_no_route_returns_typed_404(client: TestClient) -> None:
    r = client.post("/v1/completions", json={"model": "primary", "prompt": "hi"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "dispatch.no_route"


def test_v1_embeddings_no_route_returns_typed_404(client: TestClient) -> None:
    r = client.post("/v1/embeddings", json={"model": "embed", "input": "test"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "dispatch.no_route"


def test_v1_models_specific_404_envelope(client: TestClient) -> None:
    r = client.get("/v1/models/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "dispatch.no_route"


def test_v1_routes_are_no_longer_501_stubs(client: TestClient) -> None:
    """Regression: ensure /v1/* routes don't return system.not_implemented."""
    for method, path, body in [
        ("GET", "/v1/models", None),
        ("POST", "/v1/chat/completions", {"model": "primary", "messages": []}),
        ("POST", "/v1/completions", {"model": "primary", "prompt": ""}),
        ("POST", "/v1/embeddings", {"model": "embed", "input": ""}),
    ]:
        r = client.request(method, path, json=body)
        assert r.status_code != 501, f"{method} {path}: still a stub"
        if r.status_code >= 400:
            assert r.json()["error"]["code"] != "system.not_implemented"


# ── issue #34 / harness #18: missing-model on /v1/audio/* → 400, not 404 ───


def test_v1_audio_speech_missing_model_returns_400(client: TestClient) -> None:
    """POST /v1/audio/speech without 'model' → 400 request.missing_model.

    Pre-issue-#34 the dispatcher's default-model + no-route fallback
    surfaced a confusing 404 ('dispatch.no_route'). The route now raises
    BadRequest up front so OpenAI clients see a useful error message
    naming the missing field. Code lives in the ``request.*`` namespace
    per harness finding #18.
    """
    r = client.post("/v1/audio/speech", json={"input": "hello", "voice": "alloy"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "request.missing_model"
    assert "model" in body["error"]["message"].lower()


def test_v1_audio_speech_empty_model_returns_400(client: TestClient) -> None:
    """An empty / whitespace-only model field is treated as missing."""
    r = client.post("/v1/audio/speech", json={"model": "   ", "input": "hi"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "request.missing_model"


def test_v1_audio_transcriptions_missing_model_returns_400(client: TestClient) -> None:
    """POST /v1/audio/transcriptions without a model form field → 400.

    Multipart variant of the same contract — the regex-extracted model is
    empty so the route raises BadRequest before dispatching to a default
    that wouldn't route anyway.
    """
    # Minimal multipart body with a fake audio file but NO model field.
    files = {"file": ("clip.wav", b"RIFFfake", "audio/wav")}
    r = client.post("/v1/audio/transcriptions", files=files)
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "request.missing_model"
    assert "model" in body["error"]["message"].lower()
