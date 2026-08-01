"""Shared httpx client construction — one cached SSL context per process (#1507).

``httpx`` builds a fresh :class:`ssl.SSLContext` on **every**
``AsyncClient`` construction, and building one loads the system CA
bundle off disk. Measured on a v1.0 box (httpx 0.28.1, Python 3.12):

    httpx.AsyncClient(timeout=1.0)                 89.2  ms
    httpx.AsyncClient(timeout=1.0, verify=ctx)      0.121 ms   <- 737x cheaper

That is 89 ms of *event-loop CPU*, not I/O — it cannot be hidden behind
an ``await``. hal0 constructs a throwaway client per probe, so the
metrics sampler, the slot fail-watcher and the ``/api/slots`` container
probe each paid it every tick, all on the same event loop. Profiling
``GET /api/slots`` on a 16-slot box (py-spy, 22 s window) attributed
**8.45 s** to ``ssl.create_default_context`` alone — the single largest
consumer of loop time, and the reason a ~7 s request took 12-15 s.

Every one of those URLs is ``http://127.0.0.1:<port>/...``, which never
negotiates TLS at all.

Caching keeps TLS behaviour byte-identical: the context still comes from
``httpx.create_ssl_context()``, it is just built once per process
instead of once per request. An ``ssl.SSLContext`` is explicitly
designed to be shared across many connections and threads.

Use :func:`async_client` as a drop-in for ``httpx.AsyncClient(...)``.
Callers that pass their own ``verify`` or a client ``cert`` keep full
control — the shared context is only injected when neither is given.
"""

from __future__ import annotations

import ssl
from functools import cache
from typing import Any

import httpx

__all__ = ["async_client", "shared_ssl_context"]


@cache
def shared_ssl_context(trust_env: bool = True) -> ssl.SSLContext:
    """The process-wide SSL context, built once per ``trust_env`` value.

    Keyed on ``trust_env`` because that flag changes which CA material
    httpx loads (``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` honoured or not);
    sharing one context across both settings would silently ignore the
    caller's choice.
    """
    return httpx.create_ssl_context(trust_env=trust_env)


def async_client(**kwargs: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` with the cached SSL context pre-injected.

    Behaviourally identical to constructing the client directly, minus
    the per-call CA-bundle load. Falls back to plain construction when
    the caller supplies ``verify`` or ``cert`` explicitly, so a call site
    that needs bespoke TLS material is never quietly overridden.
    """
    if "verify" not in kwargs and "cert" not in kwargs:
        kwargs["verify"] = shared_ssl_context(bool(kwargs.get("trust_env", True)))
    return httpx.AsyncClient(**kwargs)
