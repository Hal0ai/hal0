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

:func:`async_client` also keeps loopback traffic off the proxy
environment (#2585). Slot children, container slots and hal0's own API
all listen on loopback, and httpx's default ``trust_env=True`` hands a
request to ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY`` whenever
``NO_PROXY`` does not name its host, so a prompt bound for
``127.0.0.1`` would cross the proxy in cleartext. A client that may
also reach a remote provider cannot simply turn ``trust_env`` off, so
loopback hosts are mounted on a direct transport instead and every
other host keeps the proxy environment.
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


@cache
def _loopback_mount_patterns() -> tuple[str, ...]:
    """httpx mount patterns for this box's loopback hosts (#2585).

    Built from :data:`hal0.service_identity._LOOPBACK_HOSTS`, the set
    :func:`hal0.service_identity.is_loopback_url` checks, so "loopback"
    means the same thing here as for the per-URL ``trust_env=False``
    clients. A pattern with no port matches every port on that host. hal0
    builds every slot URL on ``127.0.0.1`` (``SlotManager.
    _register_container_upstream`` and the provider health probes), and
    ``[slots].publish_host`` changes only where podman publishes, not the
    URL hal0 dials.
    """
    from hal0.service_identity import _LOOPBACK_HOSTS

    return tuple(
        f"all://[{host}]" if ":" in host else f"all://{host}" for host in sorted(_LOOPBACK_HOSTS)
    )


def _loopback_direct_mounts(kwargs: dict[str, Any]) -> dict[str, httpx.AsyncBaseTransport]:
    """One direct transport, mounted on every loopback pattern (#2585).

    The transport takes the client's own TLS material and ``limits``, so
    the loopback pool is bounded exactly like the client's default pool
    (#415) and every loopback host shares that one pool.
    """
    shared = {k: kwargs[k] for k in ("verify", "cert", "http1", "http2", "limits") if k in kwargs}
    direct = httpx.AsyncHTTPTransport(**shared)
    return dict.fromkeys(_loopback_mount_patterns(), direct)


def async_client(**kwargs: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` with the cached SSL context pre-injected.

    Identical to constructing the client directly, minus the per-call
    CA-bundle load, with one difference: requests to a loopback host go
    direct, never through the proxy environment (#2585). Remote hosts
    still honour ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY`` /
    ``NO_PROXY``. Pass ``trust_env=False`` to ignore the proxy
    environment everywhere.

    Falls back to plain construction when the caller supplies ``verify``
    or ``cert`` explicitly, so a call site that needs bespoke TLS
    material is never quietly overridden. Likewise a caller that passes
    its own ``transport``, ``mounts`` or ``proxy`` owns routing, and no
    loopback mount is added.
    """
    if "verify" not in kwargs and "cert" not in kwargs:
        kwargs["verify"] = shared_ssl_context(bool(kwargs.get("trust_env", True)))
    # Only a real routing option is an override: a forwarded ``proxy=None``
    # still means "use the proxy environment", so it must keep the mount.
    if kwargs.get("trust_env", True) and all(
        kwargs.get(option) is None for option in ("transport", "mounts", "proxy")
    ):
        kwargs["mounts"] = _loopback_direct_mounts(kwargs)
    return httpx.AsyncClient(**kwargs)
