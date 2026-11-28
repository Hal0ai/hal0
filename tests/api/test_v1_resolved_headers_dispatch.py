"""``X-hal0-Resolved-Model`` through the real dispatcher (#4017).

``test_v1_resolved_headers.py`` checks the rule on hand-built routing
decisions. These run a request through the app, so the routing decision is the
one ``Dispatcher`` really builds, and every assertion is exact.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from hal0.upstreams.catalog import UpstreamModel
from hal0.upstreams.registry import Upstream

from .test_v1_audio import _install_mock_transport, _pin_slot_ready

_WAV = {"file": ("clip.wav", b"RIFF" + b"\x00" * 64, "audio/wav")}


def _seed_slot(client: TestClient, name: str, port: int = 8097) -> None:
    client.app.state.upstreams.upsert(
        Upstream(
            name=name,
            kind="slot",
            url=f"http://127.0.0.1:{port}/v1",
            slot_name=name,
            auth_style="none",
        )
    )
    _pin_slot_ready(client, slot_name=name)  # records model_id "test-model"


def _seed_remote(client: TestClient) -> None:
    client.app.state.upstreams.upsert(
        Upstream(
            name="prov",
            kind="remote",
            url="http://prov.invalid/v1",
            auth_style="none",
            curated=True,
            models=(UpstreamModel(id="whisper-x", enabled=True),),
        )
    )


def _answer(status: int = 200, seen: list[bytes] | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req.content)
        body = {"ok": True} if status < 400 else {"error": {"message": "model not found"}}
        return httpx.Response(status, json=body, headers={"X-hal0-Resolved-Model": "from-upstream"})

    return handler


def test_a_request_that_falls_back_to_a_default_slot_names_that_slots_model(
    client: TestClient,
) -> None:
    """No slot is bound to this model; the dispatcher sends it to the anchor
    slot with the caller's name still in the body. The header is the slot's
    record and exactly that."""
    _seed_slot(client, "agent")
    seen: list[bytes] = []
    _install_mock_transport(client, _answer(seen=seen))

    r = client.post(
        "/v1/chat/completions",
        json={"model": "no-such-model-anywhere", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert r.status_code == 200, r.text
    assert b"no-such-model-anywhere" in seen[0]  # the caller's name went on the wire
    assert r.headers["x-hal0-slot"] == "agent"
    assert r.headers["x-hal0-resolved-model"] == "test-model"


def test_multipart_to_a_remote_provider_names_the_id_that_went_on_the_wire(
    client: TestClient,
) -> None:
    """``prov/whisper-x`` resolves to the provider's ``whisper-x``, but a
    multipart upload is forwarded as the caller sent it. The header carries
    what the provider received, and there is no slot."""
    _seed_remote(client)
    seen: list[bytes] = []
    _install_mock_transport(client, _answer(seen=seen))

    r = client.post("/v1/audio/transcriptions", files=_WAV, data={"model": "prov/whisper-x"})

    assert r.status_code == 200, r.text
    assert b"prov/whisper-x" in seen[0]
    assert r.headers["x-hal0-resolved-model"] == "prov/whisper-x"
    assert "x-hal0-slot" not in r.headers


def test_json_to_a_remote_provider_names_the_id_the_dispatcher_sent(client: TestClient) -> None:
    _seed_remote(client)
    seen: list[bytes] = []
    _install_mock_transport(client, _answer(seen=seen))

    r = client.post(
        "/v1/chat/completions",
        json={"model": "prov/whisper-x", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert r.status_code == 200, r.text
    import json

    sent = json.loads(seen[0])["model"]
    assert r.headers["x-hal0-resolved-model"] == sent
    assert "x-hal0-slot" not in r.headers


def test_an_upstream_error_carries_the_slot_and_no_model(client: TestClient) -> None:
    """A mirrored 4xx was not produced by a model hal0 can name."""
    _seed_slot(client, "agent")
    _install_mock_transport(client, _answer(status=404))

    r = client.post(
        "/v1/chat/completions",
        json={"model": "no-such-model-anywhere", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert r.status_code == 404
    assert "x-hal0-resolved-model" not in r.headers
    assert r.headers["x-hal0-slot"] == "agent"


def test_a_remote_error_carries_neither_header(client: TestClient) -> None:
    _seed_remote(client)
    _install_mock_transport(client, _answer(status=404))

    r = client.post("/v1/audio/transcriptions", files=_WAV, data={"model": "prov/whisper-x"})

    assert r.status_code == 404
    assert "x-hal0-resolved-model" not in r.headers
    assert "x-hal0-slot" not in r.headers
