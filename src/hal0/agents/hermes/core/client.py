"""Synchronous authenticated transport shared by hal0 Hermes adapters."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .errors import IncompatibleSchema, MissingResource, Unauthorized, Unavailable

_RETRYABLE_STATUS = frozenset({502, 503, 504})
_MAX_ATTEMPTS = 3
_OWNED_HEADERS = frozenset({"authorization", "x-request-id", "idempotency-key"})


class Hal0HermesClient:
    """Small policy-free wrapper around the hal0 HTTP API."""

    def __init__(
        self,
        base_url: str,
        client_key: str | None = None,
        admin_key: str | None = None,
    ) -> None:
        self._client_key = client_key
        self._admin_key = admin_key
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=httpx.Timeout(15.0, connect=2.0),
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> Hal0HermesClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def health(self) -> httpx.Response:
        """Call the open liveness endpoint without a credential."""
        return self._request("GET", "/api/health", key=None)

    def request_read(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Make a read request using only the client credential."""
        return self._request(method, path, key=self._client_key, **kwargs)

    def request_mutation(
        self,
        method: str,
        path: str,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a mutation request using only the admin credential."""
        headers = dict(kwargs.pop("headers", {}))
        _reject_owned_headers(headers)
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return self._request(
            method,
            path,
            key=self._admin_key,
            headers=headers,
            max_attempts=_MAX_ATTEMPTS if idempotency_key is not None else 1,
            validate_headers=False,
            **kwargs,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        key: str | None,
        headers: dict[str, str] | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
        validate_headers: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        if "auth" in kwargs:
            raise ValueError("caller auth override is not allowed")
        path = _relative_target(path)
        request_id = str(uuid4())
        request_headers = dict(headers or {})
        if validate_headers:
            _reject_owned_headers(request_headers)
        request_headers["X-Request-ID"] = request_id
        if key is not None:
            request_headers["Authorization"] = f"Bearer {key}"

        for attempt in range(max_attempts):
            try:
                response = self._client.request(method, path, headers=request_headers, **kwargs)
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt + 1 < max_attempts:
                    continue
                raise Unavailable(request_id=request_id) from None

            if response.status_code in _RETRYABLE_STATUS:
                if attempt + 1 < max_attempts:
                    continue
                raise Unavailable(
                    status_code=response.status_code,
                    error_code=_error_code(response),
                    request_id=request_id,
                )
            return _decode_response(response, request_id)

        raise AssertionError("retry loop exhausted")


def _relative_target(target: str) -> str:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith("//"):
        raise ValueError("hal0 request target must be API-relative")
    return target.lstrip("/")


def _reject_owned_headers(headers: dict[str, str]) -> None:
    if any(str(name).lower() in _OWNED_HEADERS for name in headers):
        raise ValueError("caller cannot override transport-owned headers")


def _decode_response(response: httpx.Response, request_id: str) -> httpx.Response:
    if response.status_code < 400:
        return response

    error_code = _error_code(response)
    details = {
        "status_code": response.status_code,
        "error_code": error_code,
        "request_id": request_id,
    }
    if response.status_code in {401, 403}:
        raise Unauthorized(**details)
    if response.status_code == 404:
        raise MissingResource(**details)
    if response.status_code == 409 or error_code == "schema.incompatible":
        raise IncompatibleSchema(**details)
    raise Unavailable(**details)


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    for field in ("error_code", "code"):
        if isinstance(body.get(field), str):
            return body[field]
    return None
