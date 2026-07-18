"""Behavioral contract for the shared hal0 Hermes transport."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from hal0.agents.hermes.core import (
    Hal0HermesClient,
    IncompatibleSchema,
    MissingResource,
    Unauthorized,
    Unavailable,
)

_HTTPX_CLIENT = httpx.Client


def _client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> Hal0HermesClient:
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs),
    )
    return Hal0HermesClient(
        "http://hal0.test", client_key="client-secret", admin_key="admin-secret"
    )


def test_health_sends_no_authorization_key(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []
    client = _client(
        monkeypatch,
        lambda request: requests.append(request) or httpx.Response(200, json={"ok": True}),
    )

    response = client.health()

    assert response.json() == {"ok": True}
    assert "Authorization" not in requests[0].headers
    assert requests[0].url.path == "/api/health"


def test_reads_send_only_client_key(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []
    client = _client(
        monkeypatch,
        lambda request: requests.append(request) or httpx.Response(200, json={}),
    )

    client.request_read("GET", "/api/things")

    assert requests[0].headers["Authorization"] == "Bearer client-secret"
    assert "admin-secret" not in str(requests[0].headers)


def test_mutations_send_only_admin_key_and_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    client = _client(
        monkeypatch,
        lambda request: requests.append(request) or httpx.Response(200, json={}),
    )

    client.request_mutation("POST", "/api/things", json={"name": "x"}, idempotency_key="event-1")

    assert requests[0].headers["Authorization"] == "Bearer admin-secret"
    assert requests[0].headers["Idempotency-Key"] == "event-1"
    assert "client-secret" not in str(requests[0].headers)


def test_each_request_has_generated_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    client = _client(
        monkeypatch,
        lambda request: requests.append(request) or httpx.Response(200, json={}),
    )

    client.health()
    client.health()

    ids = [request.headers["X-Request-ID"] for request in requests]
    assert all(ids)
    assert len(set(ids)) == 2


def test_unauthorized_differs_from_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unauthorized = _client(
        monkeypatch,
        lambda request: httpx.Response(401, json={"error": {"code": "auth.invalid"}}),
    )

    with pytest.raises(Unauthorized) as denied:
        unauthorized.request_read("GET", "/api/things")

    assert denied.value.status_code == 401
    assert denied.value.error_code == "auth.invalid"

    def connection_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("client-secret admin-secret unreachable", request=request)

    unavailable = _client(monkeypatch, connection_failure)
    with pytest.raises(Unavailable) as down:
        unavailable.request_read("GET", "/api/things")

    assert down.value.status_code is None


def test_diagnostics_redact_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    def failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("client-secret admin-secret refused", request=request)

    client = _client(monkeypatch, failure)

    with pytest.raises(Unavailable) as caught:
        client.request_read("GET", "/api/things")

    diagnostic = repr(caught.value) + str(caught.value)
    assert "client-secret" not in diagnostic
    assert "admin-secret" not in diagnostic


@pytest.mark.parametrize("status", [502, 503, 504])
def test_retries_transient_statuses_three_times(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    attempts = 0

    def failure(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, json={"error": {"code": "upstream.down"}})

    client = _client(monkeypatch, failure)

    with pytest.raises(Unavailable):
        client.request_read("GET", "/api/things")

    assert attempts == 3


@pytest.mark.parametrize("exception_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_retries_connect_and_timeout_three_times(
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[httpx.RequestError],
) -> None:
    attempts = 0

    def failure(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise exception_type("transient", request=request)

    client = _client(monkeypatch, failure)

    with pytest.raises(Unavailable):
        client.request_read("GET", "/api/things")

    assert attempts == 3


@pytest.mark.parametrize("status", [400, 401, 404, 409, 500, 505])
def test_does_not_retry_other_statuses(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    attempts = 0

    def failure(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, json={"error": {"code": "request.failed"}})

    client = _client(monkeypatch, failure)

    with pytest.raises((IncompatibleSchema, MissingResource, Unauthorized, Unavailable)):
        client.request_read("GET", "/api/things")

    assert attempts == 1


def test_status_and_error_codes_decode_to_typed_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            httpx.Response(404, json={"error": {"code": "resource.missing"}}),
            httpx.Response(409, json={"error": {"code": "schema.incompatible"}}),
        ]
    )
    client = _client(monkeypatch, lambda request: next(responses))

    with pytest.raises(MissingResource) as missing:
        client.request_read("GET", "/api/missing")
    with pytest.raises(IncompatibleSchema) as incompatible:
        client.request_read("GET", "/api/schema")

    assert missing.value.error_code == "resource.missing"
    assert incompatible.value.error_code == "schema.incompatible"
