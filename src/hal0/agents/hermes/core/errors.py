"""Typed, secret-safe failures from the hal0 Hermes transport."""

from __future__ import annotations


class HermesTransportError(RuntimeError):
    """Base error containing only safe structured diagnostics."""

    default_message = "hal0 request failed"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.status_code = status_code
        self.error_code = error_code
        self.request_id = request_id


class Unauthorized(HermesTransportError):
    """The supplied credential was rejected."""

    default_message = "hal0 authorization failed"


class Unavailable(HermesTransportError):
    """hal0 could not complete the request."""

    default_message = "hal0 is unavailable"


class IncompatibleSchema(HermesTransportError):
    """The response reports an unsupported API schema."""

    default_message = "hal0 API schema is incompatible"


class MissingResource(HermesTransportError):
    """The requested hal0 resource does not exist."""

    default_message = "hal0 resource is missing"
