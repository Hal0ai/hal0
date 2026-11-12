"""Client-side helpers for hal0's engine-compatible memory endpoint.

``/api/memory/compat`` (:mod:`hal0.api.routes.memory_compat`, ADR-0058
decision 4b) is where hal0 points a Hindsight coding-agents client: the Pi
driver (``harnesses.pi`` in ``~/.hindsight/coding-agent.json``) and
``hal0 memory agents install`` (``harnesses.<harness>``). Both write the
address from :func:`compat_url` and check a stored key with
:func:`compat_accepts` before reusing it.
"""

from __future__ import annotations

#: The endpoint's prefix on hal0-api.
COMPAT_PATH = "/api/memory/compat"


def compat_url(api_base: str) -> str:
    """The ``apiUrl`` a client uses: hal0-api's origin plus :data:`COMPAT_PATH`."""
    return f"{api_base.rstrip('/')}{COMPAT_PATH}"


def compat_accepts(url: str, token: str, *, timeout: float = 5.0) -> bool | None:
    """Whether the endpoint at ``url`` accepts ``token`` as a memory client.

    ``True`` on a 200 from ``<url>/health`` (the route is BRIDGE class: it
    needs a bridge key with the ``memory`` scope, or the admin credential),
    ``False`` on 401 or 403, ``None`` when that cannot be told (hal0-api or
    the engine down, any other answer). A caller never replaces a key on
    ``None``.
    """
    import httpx

    try:
        resp = httpx.get(
            f"{url}/health", headers={"Authorization": f"Bearer {token}"}, timeout=timeout
        )
    except httpx.HTTPError:
        return None
    if resp.status_code == 200:
        return True
    if resp.status_code in (401, 403):
        return False
    return None
