"""hal0-peer classification + hal0-internal URL joining (issue #1425).

``/api/stats/hardware`` and ``/api/slots/metrics`` are **hal0's own** API
paths. The stats/metrics aggregators fan them out across upstreams so a
remote hal0 box ("haloai-style fanout") can contribute its slot metrics to
this dashboard. That fan-out was gated on ``kind != "slot"``, which is not
the same question: every third-party OpenAI-compatible provider is also
``kind == "remote"``, so openrouter.ai and api.minimax.io were being asked
for hal0-internal paths ~20 times a minute — guaranteed 404s that leak
hal0's internal path structure to third parties and burn provider rate
limit.

This module owns the two things that fix were missing:

  * :func:`is_hal0_peer` — is this upstream *another hal0*, as opposed to a
    third-party provider that merely speaks the same chat protocol?
  * :func:`peer_api_url` — join a peer's base URL with a hal0-internal path
    **without** producing the doubled ``/api/api/...`` seen in the issue.

Both are pure functions so the outbound-URL contract can be asserted
directly in tests rather than inferred from a log line.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "hal0_peer_upstreams",
    "is_hal0_peer",
    "is_private_host",
    "peer_api_url",
]


# Single-label hostnames and these suffixes never resolve on the public
# internet — they are LAN / split-horizon names, i.e. a plausible home for a
# peer hal0. Matching is lexical on purpose: no DNS lookup happens on the
# dashboard's hot path, and a resolver answer is not a security boundary.
_PRIVATE_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".lan",
    ".internal",
    ".intranet",
    ".home.arpa",
)


def is_private_host(host: str) -> bool:
    """True when ``host`` cannot address the public internet.

    Literal addresses are classified with :mod:`ipaddress` (loopback,
    RFC1918, link-local, CGNAT-free unique-local v6, …). Names are matched
    lexically against :data:`_PRIVATE_HOST_SUFFIXES` plus the bare
    single-label case. Empty / unparseable input is treated as **public**
    — the conservative answer, since the whole point is to not egress.
    """
    host = (host or "").strip().lower()
    if not host:
        return False
    # Strip an IPv6 literal's brackets before parsing.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return bool(
            addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_unspecified
        )
    if host == "localhost":
        return True
    if host.endswith(_PRIVATE_HOST_SUFFIXES):
        return True
    # Bare single-label name ("hal0", "halo") — only resolvable through a
    # local search domain.
    return "." not in host


def is_hal0_peer(upstream: Any) -> bool:
    """True when ``upstream`` is another hal0 whose internal API we may poll.

    Decision order:

      1. ``kind == "slot"`` → **False**. Slot upstreams point back at *this*
         hal0-api host:port; proxying a hal0-internal path to them is a
         self-call that deadlocks the single-worker async server (this is
         the pre-existing carve-out documented on
         ``_proxy_upstream_endpoint``, kept intact).
      2. ``enabled is False`` → **False**. A kill-switched upstream must not
         be touched by any poller.
      3. An explicit ``hal0_peer`` flag in ``upstreams.toml`` wins outright,
         in both directions.
      4. Otherwise auto-derive: a peer hal0 lives on the operator's own
         network, so only a private/loopback host qualifies. Every
         third-party provider (openrouter.ai, api.minimax.io, api.openai.com
         …) is a public host and is therefore never probed.

    Rule 4 is what makes the default *safe*: no configuration change can
    accidentally re-enable egress of hal0-internal paths to a public API,
    while the haloai-style LAN fanout the feature exists for keeps working
    with no operator action.
    """
    if getattr(upstream, "kind", "remote") == "slot":
        return False
    if getattr(upstream, "enabled", True) is False:
        return False
    explicit = getattr(upstream, "hal0_peer", None)
    if explicit is not None:
        return bool(explicit)
    host = urlsplit(str(getattr(upstream, "url", "") or "")).hostname or ""
    return is_private_host(host)


def hal0_peer_upstreams(upstreams: Any) -> list[Any]:
    """Filter a registry's ``list()`` down to the hal0 peers."""
    try:
        entries = list(upstreams.list())
    except Exception:
        return []
    return [u for u in entries if is_hal0_peer(u)]


def peer_api_url(base_url: str, suffix: str) -> str:
    """Join a peer's OpenAI-compat base URL with a hal0-internal API path.

    Upstream base URLs end in ``/v1`` by convention; hal0's dashboard API is
    a *sibling* of ``/v1`` on the same host:port, so the ``/v1`` segment is
    dropped before ``suffix`` is appended.

    The doubled-path bug in #1427 (``https://openrouter.ai/api/v1`` +
    ``/api/slots/metrics`` → ``https://openrouter.ai/api/api/slots/metrics``)
    came from doing that concatenation blind: stripping ``/v1`` off
    ``/api/v1`` leaves ``/api``, and ``suffix`` starts with ``/api/`` too.
    This helper collapses that overlap, so no caller can construct an
    ``/api/api/`` path regardless of how the base URL was written.

    Query strings and fragments on the base URL are dropped — they have no
    meaning for a dashboard GET and would otherwise be silently smuggled
    onto an unrelated path.
    """
    suffix = "/" + suffix.lstrip("/")
    parts = urlsplit(base_url.strip())
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    # Collapse a repeated leading segment: ".../api" + "/api/slots/metrics".
    head = "/" + suffix.lstrip("/").split("/", 1)[0]
    if head != "/" and path.endswith(head):
        path = path[: -len(head)]
    return urlunsplit((parts.scheme, parts.netloc, path + suffix, "", ""))
