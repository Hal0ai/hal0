"""Which client request headers hal0 may copy onto a request it forwards (#2538).

hal0 proxies client requests to upstreams it does not control: llama-server and
other slot children on loopback, container slots, the Hermes dashboard, and
remote cloud providers. A client request to hal0 carries hal0's own
credentials and identity (the ``hal0_session`` cookie, which
``resolve_principal`` treats as an admin login; ``Authorization``;
``X-API-Key``; the ``X-hal0-*`` agent/private-mode headers) and whatever the
browser or SDK adds. None of that belongs to the upstream.

So forwarding is an allowlist, not a denylist: a header reaches an upstream
only when it is named here. Upstream credentials are never copied from the
client; each forwarder adds its own (``UpstreamRegistry.auth_headers`` for the
dispatcher, ``X-hal0-Agent`` for the plugin manifest proxy).

What is on the list, and why:

``content-type``
    The body's media type. The dispatcher re-serialises JSON bodies, and the
    multipart audio path replaces the value with the original boundary-bearing
    one, but slots and providers still read it.
``accept``
    Lets an upstream pick between JSON and ``text/event-stream``.
``user-agent``
    Harmless and occasionally used by providers for client attribution.
``anthropic-version``, ``anthropic-beta``
    Anthropic protocol selectors. ``auth_headers`` overrides
    ``anthropic-version`` for ``auth_style = "anthropic"`` upstreams.
``openai-organization``, ``openai-project``, ``openai-beta``
    OpenAI protocol / billing selectors an SDK caller set on purpose.

Deliberately left off: ``cookie``, ``authorization``, ``x-api-key``,
``proxy-*``, ``x-hal0-*``, ``forwarded`` / ``x-forwarded-*`` / ``x-real-ip``
(credentials, hal0 identity, client network topology), the hop-by-hop set,
``host`` and ``content-length`` (httpx computes both), ``accept-encoding``
(the dispatcher relays streams with ``aiter_raw`` and strips the response's
``content-encoding``, so it must choose the encoding itself), and the
``x-stainless-*`` SDK telemetry headers (client OS, arch and runtime; no
upstream needs them).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

__all__ = ["FORWARDABLE_REQUEST_HEADERS", "forwardable_request_headers"]

FORWARDABLE_REQUEST_HEADERS: frozenset[str] = frozenset(
    {
        "content-type",
        "accept",
        "user-agent",
        "anthropic-version",
        "anthropic-beta",
        "openai-organization",
        "openai-project",
        "openai-beta",
    }
)


def forwardable_request_headers(
    headers: Mapping[str, str],
    *,
    extra: Iterable[str] = (),
) -> dict[str, str]:
    """Return the subset of ``headers`` an upstream may receive.

    Keys come back lower-cased so a forwarder that then sets its own header
    (``X-hal0-Agent``, ``Authorization``) replaces rather than duplicates a
    differently-cased client copy. ``extra`` adds call-site specific names
    (for example conditional-GET headers for a static-asset proxy); it can
    widen the list but the result is still an allowlist.
    """
    allowed = FORWARDABLE_REQUEST_HEADERS | {h.lower() for h in extra}
    return {k.lower(): v for k, v in headers.items() if k.lower() in allowed}
