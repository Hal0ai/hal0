"""Leaf-module home for the structured-error base class.

Lives here (not in ``hal0.api.middleware.error_codes``) so non-api modules
— probes, loaders, providers — can raise typed errors without triggering
the api import chain. The middleware re-exports ``Hal0Error`` for the
import path everything else has always used.

Typed 4xx subclasses
====================

For the common HTTP client-error statuses, this module also provides
ready-to-raise subclasses that set ``status`` correctly. Each one carries
a sensible default ``code`` in the appropriate namespace, but callers can
override the code per-raise to keep code strings stable and descriptive.

Example::

    from hal0.errors import BadRequest, NotFound

    if not body.get("name"):
        raise BadRequest("'name' is required", code="slot.name_missing")

    slot = registry.get(name)
    if slot is None:
        raise NotFound(f"slot {name!r} not found", code="slot.not_found")

The api error envelope middleware (:mod:`hal0.api.middleware.error_codes`)
catches these uniformly with the rest of the ``Hal0Error`` family and
renders them as the canonical envelope ``{"error": {"code", "message",
"details"}}`` with the right HTTP status.
"""

from __future__ import annotations

from typing import Any


class Hal0Error(Exception):
    """Base class for typed errors with stable codes.

    Subclasses set ``code`` (dotted namespace, e.g. ``slot.not_ready``) and
    optionally ``status`` (HTTP status code surfaced by the api middleware).
    """

    code: str = "system.internal"
    status: int = 500

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        # Per-instance code override — lets callers raise the generic 4xx
        # subclasses (BadRequest, NotFound, ...) without sub-subclassing
        # for every distinct error site. Defaults to the class attribute
        # so existing subclass-driven sites keep working unchanged.
        if code is not None:
            self.code = code


# ── Typed 4xx subclasses ─────────────────────────────────────────────────────
#
# These cover the everyday client-error statuses. They're deliberately thin —
# the api middleware does the envelope rendering. Each subclass sets the
# correct ``status`` plus a default ``code`` in a sensible namespace.
#
# Per the hal0 error envelope contract (see docs/error-envelope.md / #39),
# every non-2xx ``/api/*`` response carries ``{"error": {"code", "message",
# "details"}}``. These subclasses are the ergonomic on-ramp for routes that
# want the right status + envelope shape without inventing a new exception
# class per call site.


class BadRequest(Hal0Error):
    """400 — the request was syntactically or semantically invalid.

    Use this for client-side validation failures the route itself catches
    (missing fields, malformed JSON, unparseable values) before any business
    logic runs.

    Example::

        raise BadRequest("'name' must be a non-empty string")
        raise BadRequest("unknown backend", code="slot.unknown_backend",
                         details={"backend": value})
    """

    code = "validation.invalid"
    status = 400


class Unauthorized(Hal0Error):
    """401 — no valid credentials presented.

    Use when the caller needs to authenticate but didn't, or presented
    credentials that didn't validate. For authenticated-but-insufficient,
    use :class:`Forbidden` instead.

    Example::

        raise Unauthorized("missing bearer token", code="auth.required")
    """

    code = "auth.required"
    status = 401


class Forbidden(Hal0Error):
    """403 — authenticated but the credentials lack the required scope.

    Example::

        raise Forbidden("admin scope required", code="auth.forbidden")
    """

    code = "auth.forbidden"
    status = 403


class NotFound(Hal0Error):
    """404 — the named resource does not exist.

    Example::

        raise NotFound(f"slot {name!r} not found", code="slot.not_found")
    """

    code = "resource.not_found"
    status = 404


class Conflict(Hal0Error):
    """409 — the request conflicts with current resource state.

    Use for duplicate-create attempts, edit-vs-edit races, or operations
    that aren't valid for the resource's current lifecycle state.

    Example::

        raise Conflict("slot is already running", code="slot.already_running")
    """

    code = "resource.conflict"
    status = 409


class UnprocessableEntity(Hal0Error):
    """422 — the request was well-formed but failed business-rule validation.

    Distinct from :class:`BadRequest` (400) which signals structural or
    syntactic failures. Use 422 when the shape is correct but the values
    are semantically incompatible (e.g. cross-field constraints).

    Note: FastAPI's automatic pydantic validation also produces 422s — those
    are caught by ``hal0.api.middleware.error_codes`` and reshaped into the
    envelope automatically. Raise this class explicitly for hand-written
    validation in route bodies.

    Example::

        raise UnprocessableEntity(
            "start_at must be earlier than end_at",
            code="schedule.invalid_window",
            details={"start_at": start, "end_at": end},
        )
    """

    code = "validation.unprocessable"
    status = 422


__all__ = [
    "BadRequest",
    "Conflict",
    "Forbidden",
    "Hal0Error",
    "NotFound",
    "Unauthorized",
    "UnprocessableEntity",
]
