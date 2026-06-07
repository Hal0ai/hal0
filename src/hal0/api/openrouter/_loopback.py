"""Loopback-only guard helpers for the OpenRouter OAuth callback.

Quoting ADR-0020:

> The OAuth PKCE callback URL is constrained to
> ``http://127.0.0.1:<port>/api/openrouter/auth/callback``.
>
> hal0-api keeps binding ``0.0.0.0:8080`` for the existing dashboard +
> tool surfaces. The callback route ``/api/openrouter/auth/callback``
> is registered, but a per-route guard rejects every request whose
> ``request.client.host`` is not loopback.

This module owns the host-classification primitive (``is_loopback_host``)
plus a FastAPI-friendly helper (``require_loopback``) that raises a
typed HTTPException on a non-loopback client. Both are deliberately
small, dependency-free, and unit-tested in isolation so V1 (the actual
PKCE exchange flow) inherits a well-behaved foundation.

The loopback guard is implemented per-route, not as a global
middleware, because every other hal0-api surface intentionally accepts
LAN traffic. A global middleware would force allowlisting the rest of
the API, inverting the decision recorded in ADR-0012.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

# IPv4 + IPv6 loopback literals, plus the textual ``localhost`` form
# that some browsers / OS resolvers still hand to ASGI servers when the
# DNS entry resolves to 127.0.0.1. Anything else is treated as
# untrusted — including private RFC1918 LAN ranges such as 10.0.0.0/8.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback_host(host: str | None) -> bool:
    """Return ``True`` only for loopback client hosts.

    Accepts the IPv4 loopback (``127.0.0.1``), the IPv6 loopback
    (``::1``), and the literal hostname ``localhost``. Everything else
    — including private LAN ranges, public IPs, empty strings, and
    ``None`` — returns ``False``.

    The check is intentionally a strict allowlist rather than a CIDR
    test against ``127.0.0.0/8``: hal0-api is bound to a single
    interface, and the only loopback address ASGI servers hand to
    request scopes in practice is ``127.0.0.1`` (or ``::1`` for an
    IPv6 listener). A broader range would silently accept spoofed
    headers in adversarial deployments.
    """
    if not host:
        return False
    return host in _LOOPBACK_HOSTS


def require_loopback(request: Request) -> None:
    """Raise ``HTTPException(403)`` for non-loopback callers.

    Designed to be called at the top of a route handler — see
    ``hal0.api.openrouter.auth.callback``. Returns ``None`` on success
    so the route body proceeds normally; raises on failure so FastAPI
    serialises the typed error envelope.

    The 403 (not 404) is deliberate: leaking the existence of the
    callback URL is harmless (the URL is in the public ADR), and 403
    makes the "you must complete this flow over localhost / an SSH
    tunnel" story explicit to operators who hit the endpoint from
    their laptop.
    """
    client = request.client
    host = client.host if client is not None else None
    if is_loopback_host(host):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "loopback_required",
            "message": (
                "OpenRouter OAuth callback is constrained to loopback per "
                "ADR-0020. Complete the flow from the hal0 host or SSH-tunnel "
                "127.0.0.1:8080 to your local machine."
            ),
            "adr": "ADR-0020",
            "client_host": host,
        },
    )
