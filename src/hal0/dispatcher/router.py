"""Dispatcher — registry-aware request router.

The :class:`Dispatcher` reads the model registry and upstream list to
decide where to forward each OpenAI-compatible request.  It does not
start or stop slots; if a slot is offline, it returns a structured
dispatch error and leaves slot management to the caller.

Resolution order (PLAN.md §3, ported from haloai ``lib/dispatcher.py``):

  1. **registry** — exact :class:`ModelRegistry` binding for the requested
     model id (or path-default for ``/embeddings``, ``/rerank``, and ``/rerankings``).  If
     the bound upstream is online, forward there.
  2. **passthrough** — pick any upstream whose cached ``/v1/models``
     already advertises the requested model id.
  3. **cold-cache prefetch** — fan out ``/v1/models`` against remote
     upstreams whose caches are empty (with a configurable timeout —
     Tier 2), then re-check passthrough.  The prefetch fanout is wrapped
     in :class:`SingleFlightGroup` (Tier 3) so 100 concurrent identical
     prefetches share a single upstream call.
  4. **legacy fallback** — :func:`hal0.dispatcher.proxy.resolve_slot`
     path-and-name heuristics from haloai ``lib/proxy.py``.  Kept until
     v0.2.

Decision logging: every routing decision emits one structured log line
to journald with ``SYSLOG_IDENTIFIER=hal0-dispatch`` (PLAN.md §5 Tier 2),
carrying ``{request_id, model, resolution_path, upstream, cache_state,
latency_ms}``.

Port targets: haloai ``lib/dispatcher.py`` (617 lines), ``lib/proxy.py``.
See PLAN.md §3 and §5 Tier 1+2+3.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi.responses import Response, StreamingResponse

from hal0.dispatcher.proxy import LegacyResolutionFailed, resolve_slot
from hal0.dispatcher.single_flight import SingleFlightGroup
from hal0.errors import Hal0Error
from hal0.upstreams.registry import Upstream, UpstreamRegistry

if TYPE_CHECKING:
    from fastapi import Request

    from hal0.registry.store import ModelRegistry
    from hal0.slots.manager import SlotManager

# Hop-by-hop response headers that must not be forwarded to the client.
# httpx already decompresses content; content-length must be recomputed; and
# Transfer-Encoding/Connection are conn-scoped per RFC 7230.
_HOP_BY_HOP_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "content-encoding",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# NOTE: structlog binds via contextvars in the request_id middleware, so
# this logger automatically carries {request_id} on every emitted line.
log = structlog.get_logger("hal0-dispatch")

# ── Composite ``hal0`` upstream ───────────────────────────────────────────────
#
# ``_autoregister_slot_upstreams`` (hal0.api.__init__) registers ONE synthetic
# upstream named ``hal0`` that aggregates every chat-capable slot's model id
# under a single ``/v1/models`` listing (issue #422 / R4 H2). It is special in
# two ways that the dispatch path must respect:
#
#   1. It is NOT a real slot. ``SlotManager`` has no ``hal0`` entry, so the
#      readiness gate (``_check_slot_ready_for_dispatch``) and the SERVING wrap
#      must be skipped for it — otherwise a populated model cache would route a
#      chat request to the composite and immediately 503 with
#      ``slot 'hal0' is offline`` (the gate calls ``_current_state('hal0')``
#      which finds no slot and returns OFFLINE).
#   2. Its registered ``url`` is hal0-api's OWN ``/v1`` surface
#      (``127.0.0.1:8080/v1``). That value is deliberate so the ``/v1/models``
#      aggregator can short-circuit it instead of recursing over HTTP. But it
#      is the WRONG target to *forward* a chat request to — forwarding to
#      ``:8080`` would re-enter ``/v1/chat/completions`` and loop forever. The
#      real inference backend is the Lemonade gateway, so composite forwards
#      are redirected there.
_HAL0_COMPOSITE_NAME = "hal0"

# Lemonade's OpenAI-compatible gateway (ADR-0008 §1: lemond binds
# 127.0.0.1:13305). Overridable via ``LEMONADE_BASE_URL`` to match
# ``hal0.api.routes.lemonade_proxy._lemonade_base_url``.
_LEMONADE_DEFAULT_BASE_URL = "http://127.0.0.1:13305"


def _is_hal0_composite(upstream: Upstream) -> bool:
    """True for the synthetic composite ``hal0`` upstream.

    The composite is the single ``kind="slot"`` entry with no backing
    ``slot_name`` whose name is ``hal0`` (see
    ``hal0.api._autoregister_slot_upstreams``). Real per-slot upstreams
    always carry a ``slot_name``; remote providers are ``kind="remote"``.
    """
    return (
        upstream.kind == "slot"
        and upstream.slot_name is None
        and upstream.name == _HAL0_COMPOSITE_NAME
    )


def _lemonade_gateway_base() -> str:
    """Return the Lemonade OpenAI-compat gateway base URL (no trailing slash)."""
    import os

    return os.environ.get("LEMONADE_BASE_URL", _LEMONADE_DEFAULT_BASE_URL).rstrip("/")


def _resolve_target_url(upstream: Upstream, request_path: str) -> str:
    """Build the forward URL for ``upstream`` given the incoming request path.

    For the composite ``hal0`` upstream the forward must NOT go to the
    registered ``:8080`` URL (that re-enters hal0-api and loops); it is
    redirected to the Lemonade gateway. Every other upstream forwards to
    its own ``url`` via :func:`_join_url`.
    """
    if _is_hal0_composite(upstream):
        return _join_url(_lemonade_gateway_base() + "/v1", request_path)
    return _join_url(upstream.url, request_path)


# Transport-layer errors that indicate the upstream's child process is gone
# rather than a request-level failure.  These are the recoverable triggers
# for ``_recover_evicted_slot``:
#   - ConnectError: TCP connect refused (port closed before the request).
#   - RemoteProtocolError: peer dropped the connection mid-request (the
#     classic "lemonade evicted while we were dialing" race).
#
# Read/Write timeouts are intentionally excluded — those usually mean the
# child is alive but overloaded, and re-spawning would lose user work.
_RECOVERABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)

# Path defaults used only for routing — never written back into the body.
# Mirrors haloai lib/dispatcher.py:_DEFAULT_MODEL etc.
_DEFAULT_MODEL = "chat"
_EMBED_DEFAULT = "embed"
# Phase C: /rerank paths now route to a dedicated rerank slot (vulkan llama-
# server with --reranking, port 8083). Previously this pointed at "embed".
_RERANK_DEFAULT = "rerank"
_TTS_DEFAULT = "tts"
# Phase D: model-less /v1/images/* requests default to the img slot (ComfyUI).
_IMAGE_DEFAULT = "img"

# Outgoing upstream path rewrites applied before forwarding.
# Key: incoming /v1/* path (as-is from the client).
# Value: replacement path to use in the upstream URL.
#
# /v1/rerankings → /v1/rerank
#   hal0's public reranking route is /v1/rerankings (OpenAI-compat shape).
#   llama-server's native reranking endpoint is POST /rerank (not /rerankings).
#   The embed slot never served /v1/rerankings — this rewrite only fires for
#   the dispatcher-selected rerank upstream; the lemonade fall-through
#   (/api/routes/lemonade_proxy) is NOT touched by this table.
_UPSTREAM_PATH_REWRITES: dict[str, str] = {
    "/v1/rerankings": "/v1/rerank",
}


# ── Typed errors ──────────────────────────────────────────────────────────────


class DispatchError(Hal0Error):
    """Base for any failure the dispatcher itself raises.

    All subclasses use the ``dispatch.*`` namespace so the structured
    error envelope identifies them as routing failures rather than
    upstream errors.
    """

    code = "dispatch.error"
    status = 500


class NoRouteFound(DispatchError):
    """No upstream could be found for the requested model id."""

    code = "dispatch.no_route"
    status = 404


class UnknownUpstream(DispatchError):
    """Registry binds the model to an upstream that is not registered."""

    code = "dispatch.unknown_upstream"
    status = 400


class RegistryLoadFailed(DispatchError):  # TIER1
    """Reading the registry raised — surface it instead of silently swallowing.

    Replaces the haloai ``lib/dispatcher.py:115-120`` ``except Exception``
    that returned an empty dict on failure (silently degrading every
    subsequent dispatch to passthrough-only).  Now: log WARN, raise typed.
    """

    code = "dispatch.registry_unavailable"
    status = 503


class UpstreamUnavailable(DispatchError):
    """The chosen upstream couldn't be reached (timeout, connection refused, etc.).

    Distinct from a 5xx response body — those are passed through verbatim
    so the client sees the real upstream error envelope.  This error means
    we never got an HTTP response at all.
    """

    code = "dispatch.upstream_unavailable"
    status = 502


class SlotLoading(DispatchError):
    """The target slot is mid-swap — model is starting/loading/unloading.

    Raised by ``Dispatcher.forward`` before attempting the HTTP forward
    when the slot is in any non-ready state.  Without this gate, requests
    in the swap window either ConnectError (port not bound) → 502, or the
    inner llama-server returns its own raw 503 ("still loading the
    model"), leaving clients with a 5xx and no Retry-After hint.

    The envelope carries a ``progress`` block under ``details`` so the
    dashboard can render a "model loading…" chip instead of a generic
    error, and ``retry_after_s`` so OpenAI-compatible SDKs back off
    correctly (the error middleware promotes it to a real HTTP header).
    """

    code = "slot.loading"
    status = 503


# ── UpstreamCall ──────────────────────────────────────────────────────────────


@dataclass
class UpstreamCall:
    """A fully-resolved routing decision ready to be forwarded.

    Mirrors the shape in haloai ``lib/dispatcher.py``, adapted for hal0's
    typed :class:`Upstream` model.
    """

    upstream_name: str
    """Name of the selected upstream (slot name or remote provider id)."""

    target_url: str
    """Fully-qualified URL to forward the request to, including path."""

    headers: dict[str, str] = field(default_factory=dict)
    """Auth and content headers to inject before forwarding."""

    body: bytes = b""
    """Re-encoded request body (may differ from original if model was remapped)."""

    streaming: bool = False
    """Whether the upstream response should be streamed."""

    method: str = "POST"
    """HTTP method to use when forwarding."""

    resolved_model: str = ""
    """Model identifier as the upstream expects it (may differ from requested_model)."""

    requested_model: str = ""
    """Original model field from the client request body."""

    resolution_path: str = ""
    """Debug breadcrumb describing how this routing decision was made.

    Examples: ``registry``, ``passthrough:openrouter``,
    ``passthrough-prefetched:anthropic``, ``legacy_slot:primary``.
    """

    slot_name: str = ""
    """Local slot name when the upstream is ``kind=slot``, empty otherwise.

    ``Dispatcher.forward`` wraps the HTTP round-trip in
    :meth:`SlotManager.serving` for the duration of the request when this
    is non-empty, so the slot moves READY/IDLE → SERVING → READY around
    each /v1 call.  Remote upstreams (OpenAI, OpenRouter, …) leave this
    empty — they have no local slot to mark.
    """

    container_slot_name: str = ""
    """Slot name when the upstream is a container-backed ``kind=remote`` entry.

    Set by ``_container_slot_name_of`` when the remote upstream carries a
    ``slot_name`` marker (written by ``SlotManager._register_container_upstream``).
    Non-empty triggers a live readiness preflight in ``Dispatcher.forward()``
    (systemctl is-active + /health probe) *before* forwarding so that a
    down or still-starting container returns a structured ``slot.loading``
    503 instead of a raw 502 ConnectError.

    Deliberately separate from ``slot_name`` — container remotes must NOT
    enter the ``_ensure_slot_loaded_backend_aware`` / ``_forward_with_serving``
    path (no auto-load, no SERVING state wrap).
    """

    latency_ms: float = 0.0
    """Time spent in routing logic (not including the upstream round-trip)."""


# ── Hook protocols (cross-subtree stubs) ──────────────────────────────────────
#
# `cached_models`, `is_online`, and `fetch_models` are owned by Agent J's
# upstreams subtree.  Until that lands, the Dispatcher accepts them as
# injectable callables and falls back to safe defaults (empty cache,
# best-effort fetch via UpstreamRegistry.fetch_models()).  This keeps the
# Dispatcher testable in isolation while preserving the haloai algorithm.

CachedModelsFn = Callable[[str], list[str]]
"""Return the cached /v1/models list for an upstream by name (may be empty)."""

IsOnlineFn = Callable[[Upstream], Awaitable[bool]]
"""Async predicate: is this upstream reachable right now?"""

FetchModelsFn = Callable[[Upstream], Awaitable[list[str]]]
"""Async fetcher: pull /v1/models from the upstream, updating its cache."""


def _default_cached_models(_name: str) -> list[str]:
    # NOTE: until Agent J wires the cache, every cache lookup is empty so
    # passthrough never short-circuits.  Cold-cache prefetch still triggers.
    return []


async def _default_is_online(_upstream: Upstream) -> bool:
    # NOTE: pessimistic default — Agent J's health probe will replace this.
    return False


async def _default_fetch_models(_upstream: Upstream) -> list[str]:
    return []


# ── Dispatcher ────────────────────────────────────────────────────────────────


class Dispatcher:
    """Routes incoming OpenAI-compatible requests to an upstream or slot.

    Instantiated once in the API lifespan and injected via FastAPI
    ``Depends()``.  Thread/async-safe: all state is either immutable or
    confined to per-call locals / asyncio primitives.

    Dependencies are constructor-injected so unit tests can mock them
    cleanly:

    Args:
        upstream_registry:  Registry of routing targets.
        model_registry:     Source of truth for model→upstream bindings.
        prefetch_timeout_s: Cold-cache prefetch fanout timeout
                            (PLAN.md §5 Tier 2 — was hardcoded 4s, now 8s).
        cached_models:      Returns the cached /v1/models for an upstream.
        is_online:          Health probe predicate.
        fetch_models:       Async /v1/models fetcher.
        single_flight:      Coalescing group for prefetch fanouts (Tier 3).
    """

    def __init__(
        self,
        upstream_registry: UpstreamRegistry | None = None,
        model_registry: ModelRegistry | None = None,
        *,
        prefetch_timeout_s: float = 8.0,  # TIER2 — configurable (was hardcoded 4s)
        cached_models: CachedModelsFn | None = None,
        is_online: IsOnlineFn | None = None,
        fetch_models: FetchModelsFn | None = None,
        single_flight: SingleFlightGroup | None = None,
        http_client: httpx.AsyncClient | None = None,
        slot_manager: SlotManager | None = None,
    ) -> None:
        self._upstreams = upstream_registry or UpstreamRegistry()
        self._models = model_registry
        # TIER2 — prefetch timeout is configurable.  Default 8s (haloai had 4s).
        self.prefetch_timeout_s: float = prefetch_timeout_s
        self._cached_models: CachedModelsFn = cached_models or _default_cached_models
        self._is_online: IsOnlineFn = is_online or _default_is_online
        self._fetch_models: FetchModelsFn = fetch_models or _default_fetch_models
        # TIER3 — every cold-cache prefetch goes through this group.
        self._single_flight: SingleFlightGroup = single_flight or SingleFlightGroup()
        # Shared HTTP client for forward().  Lazy-init so unit tests that
        # only exercise dispatch() don't open sockets.
        self._http_client: httpx.AsyncClient | None = http_client
        self._owns_http_client: bool = http_client is None
        # SlotManager — when supplied, forward() wraps slot-kind calls in
        # SlotManager.serving() so the slot transitions READY/IDLE →
        # SERVING → READY around each /v1 request (task #10 SERVING).
        # Optional so unit tests that only exercise dispatch() can omit it.
        self._slot_manager: SlotManager | None = slot_manager

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            # Long read timeout for slow generation; short connect/write/pool.
            # Streaming responses ignore the read timeout once the stream starts.
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
                follow_redirects=False,
            )
        return self._http_client

    async def aclose(self) -> None:
        """Close the shared httpx client.  Safe to call multiple times."""
        if self._http_client is not None and self._owns_http_client:
            await self._http_client.aclose()
            self._http_client = None

    # ── public API ────────────────────────────────────────────────────────

    async def dispatch(
        self,
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> UpstreamCall:
        """Resolve a request to an :class:`UpstreamCall`.

        Args:
            request: The incoming FastAPI Request object.
            body:    Parsed JSON body dict.  If None, the body is read
                     from ``request.body()`` internally.

        Returns:
            A populated :class:`UpstreamCall` ready for forwarding.

        Raises:
            NoRouteFound: No upstream could serve the request.
            UnknownUpstream: Registry pointed at a non-existent upstream.
            RegistryLoadFailed: Reading the registry raised.
        """
        t0 = time.monotonic()
        if body is None:
            try:
                body = await request.json()
            except Exception:
                body = {}
        body = body or {}
        path = request.url.path
        method = request.method

        original_model = ""
        raw_model = body.get("model")
        if isinstance(raw_model, str):
            original_model = raw_model.strip()
        model_id = original_model or self._default_for_path(path)
        streaming = bool(body.get("stream"))

        # ── Step 0: container-slot preemption ────────────────────────────
        # A loaded container slot (kind="remote" + slot_name) is the
        # authoritative server for the models it advertises. The model
        # registry binds every registered id — including container-served
        # models — to the synthetic composite ``hal0`` upstream, which would
        # forward to lemonade. So a container slot MUST win over that binding,
        # else hal0/* requests for a container-backed model route to lemonade
        # and 404 (cutover #662). Only fires on a warm cache hit for a
        # container remote; lemonade-only models are untouched.
        for upstream in self._upstreams_in_priority_order():
            if _container_slot_name_of(upstream) and model_id in self._cached_models(upstream.name):
                call = UpstreamCall(
                    upstream_name=upstream.name,
                    target_url=_resolve_target_url(upstream, path),
                    headers=self._build_headers(request, upstream),
                    body=_remap_model(body, model_id),
                    streaming=streaming,
                    method=method,
                    resolved_model=model_id,
                    requested_model=original_model,
                    resolution_path=f"container:{upstream.name}",
                    slot_name=_slot_name_of(upstream),
                    container_slot_name=_container_slot_name_of(upstream),
                )
                self._log_decision(call, t0, cache_state="warm")
                return call

        # ── Step 1: registry lookup ──────────────────────────────────────
        registry_entry = self._registry_route_for(model_id)
        if registry_entry is not None:
            upstream_name, upstream_model = registry_entry
            upstream = self._upstreams.get(upstream_name)
            if upstream is None:
                # Explicit binding to a missing upstream — config error, not a
                # silent fallthrough.  Mirrors haloai's UnknownUpstream raise.
                raise UnknownUpstream(
                    f"model {model_id!r} maps to upstream {upstream_name!r} "
                    f"which is not registered in upstreams.toml",
                    details={"model": model_id, "upstream": upstream_name},
                )

            advertised = self._cached_models(upstream.name)
            online = bool(advertised) or await self._is_online(upstream)
            if not online and upstream.kind == "slot":
                # Slot caches can be cold even when healthy.  Try one fresh
                # fetch before declaring it offline.
                try:
                    await self._fetch_models(upstream)
                    advertised = self._cached_models(upstream.name)
                    online = bool(advertised)
                except Exception as exc:  # TIER1 — log, don't swallow silently
                    log.warning(
                        "registry-bound slot fetch failed",
                        upstream=upstream.name,
                        model=model_id,
                        error=str(exc),
                    )

            if online:
                # Slot-as-truth body remap: if the requested model id isn't
                # in the slot's advertised set, rewrite to what the slot
                # actually has loaded (required for strict backends like vLLM).
                effective_body = body
                resolved = upstream_model
                if advertised and model_id not in advertised:
                    actual = next(iter(advertised))
                    effective_body = {**body, "model": actual}
                    resolved = actual
                call = UpstreamCall(
                    upstream_name=upstream.name,
                    target_url=_resolve_target_url(upstream, path),
                    headers=self._build_headers(request, upstream),
                    body=json.dumps(effective_body).encode("utf-8"),
                    streaming=streaming,
                    method=method,
                    resolved_model=resolved,
                    requested_model=original_model,
                    resolution_path="registry",
                    slot_name=_slot_name_of(upstream),
                    container_slot_name=_container_slot_name_of(upstream),
                )
                self._log_decision(call, t0, cache_state="warm" if advertised else "probed")
                return call
            # Registry-bound slot offline → fall through.
            log.info(
                "registry binding offline; falling through",
                upstream=upstream.name,
                model=model_id,
            )

        # ── Step 2: passthrough on warm caches ───────────────────────────
        # The composite ``hal0`` upstream participates here (PR #424): a
        # cache hit yields a call whose ``_slot_name_of`` is "" (no readiness
        # gate / SERVING wrap) and whose ``_resolve_target_url`` redirects to
        # the lemond gateway instead of hal0-api's own :8080 (avoids the
        # self-recursion loop). Backend-aware loading for slot-backed models
        # is handled at the route layer before dispatch (#430), independent
        # of which upstream wins here.
        for upstream in self._upstreams_in_priority_order():
            if model_id in self._cached_models(upstream.name):
                call = UpstreamCall(
                    upstream_name=upstream.name,
                    target_url=_resolve_target_url(upstream, path),
                    headers=self._build_headers(request, upstream),
                    body=_remap_model(body, model_id),
                    streaming=streaming,
                    method=method,
                    resolved_model=model_id,
                    requested_model=original_model,
                    resolution_path=f"passthrough:{upstream.name}",
                    slot_name=_slot_name_of(upstream),
                    container_slot_name=_container_slot_name_of(upstream),
                )
                self._log_decision(call, t0, cache_state="warm")
                return call

        # ── Step 3: cold-cache prefetch ──────────────────────────────────
        cold_remotes = [
            u
            for u in self._upstreams_in_priority_order()
            if u.kind == "remote" and not self._cached_models(u.name)
        ]
        if cold_remotes:
            await self._cold_prefetch(cold_remotes)  # TIER2 + TIER3
            for upstream in self._upstreams_in_priority_order():
                if model_id in self._cached_models(upstream.name):
                    call = UpstreamCall(
                        upstream_name=upstream.name,
                        target_url=_resolve_target_url(upstream, path),
                        headers=self._build_headers(request, upstream),
                        body=_remap_model(body, model_id),
                        streaming=streaming,
                        method=method,
                        resolved_model=model_id,
                        requested_model=original_model,
                        resolution_path=f"passthrough-prefetched:{upstream.name}",
                        slot_name=_slot_name_of(upstream),
                        container_slot_name=_container_slot_name_of(upstream),
                    )
                    self._log_decision(call, t0, cache_state="prefetched")
                    return call

        # ── Step 4: legacy heuristics ────────────────────────────────────
        try:  # TIER1 — narrow exception handling; log + re-raise typed errors
            slot_upstream = resolve_slot(path, body, self._upstreams)
        except LegacyResolutionFailed as exc:
            # Bubble the typed error up after logging the decision point.
            log.warning(
                "legacy fallback exhausted",
                model=model_id,
                path=path,
                error=exc.message,
            )
            raise NoRouteFound(
                f"model {model_id!r} not found in registry, no upstream advertised it, "
                f"and legacy slot resolution failed",
                details={"model": model_id, "path": path, "legacy_error": exc.message},
            ) from exc
        except Hal0Error:
            # Typed errors are caller-meaningful: re-raise unchanged.
            raise
        except Exception as exc:  # TIER1 — was: silent swallow at haloai dispatcher.py:291
            log.warning(
                "legacy fallback raised unexpectedly",
                model=model_id,
                path=path,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise NoRouteFound(
                f"model {model_id!r}: legacy slot resolution raised {type(exc).__name__}",
                details={"model": model_id, "path": path, "error": str(exc)},
            ) from exc

        call = UpstreamCall(
            upstream_name=slot_upstream.name,
            target_url=_join_url(slot_upstream.url, path),
            headers=self._build_headers(request, slot_upstream),
            body=_remap_model(body, original_model),
            streaming=streaming,
            method=method,
            resolved_model=original_model or model_id,
            requested_model=original_model,
            resolution_path=f"legacy_slot:{slot_upstream.name}",
            slot_name=_slot_name_of(slot_upstream),
            container_slot_name=_container_slot_name_of(slot_upstream),
        )
        self._log_decision(call, t0, cache_state="legacy")
        return call

    async def forward(self, call: UpstreamCall) -> Response:
        """Execute the HTTP forward and return a FastAPI Response.

        Two paths:

        - **Streaming** (``call.streaming``): returns a
          :class:`StreamingResponse` that pipes the upstream's chunks
          straight to the client.  Suitable for SSE
          (``text/event-stream``) and raw binary (e.g. Kokoro's
          ``audio/wav``).
        - **Non-streaming**: reads the full upstream body and returns a
          :class:`Response` with the same status code and content.

        Upstream non-2xx responses are passed through verbatim so the
        client sees the upstream's error envelope (matters for
        OpenAI-compat error shapes).  Network-level failures (timeout,
        connection refused, DNS) raise :class:`UpstreamUnavailable` so
        the error middleware emits a structured ``dispatch.upstream_unavailable``
        envelope.

        Slot upstreams (``call.slot_name`` set) are wrapped in
        :meth:`SlotManager.serving` so the slot moves READY/IDLE →
        SERVING → READY around the request.  For streaming responses the
        context is held open until the iterator drains, so SERVING is
        only released after the stream closes (task #10 SERVING).

        Mirrors haloai ``lib/dispatcher.py``'s ``_forward_direct`` and
        ``_forward_streaming`` (PLAN.md §3).
        """
        if self._slot_manager is not None and (call.slot_name or call.container_slot_name):
            # Phase D (spec §7): exclusive-GPU image-mode guard. Fires
            # BEFORE the lazy-load + readiness gates so an llm-group slot
            # refused during image mode surfaces the gpu.image_mode 503
            # envelope (with its own Retry-After) instead of slot.loading —
            # and so the backend-aware lazy-load below can never pull an
            # LLM model back onto the GPU while the arbiter holds it for
            # the img slot.
            self._guard_gpu_image_mode(call)
        if call.slot_name and self._slot_manager is not None:
            # B1 (ADR-0022): backend-aware lazy-load. lemond auto-loads a
            # model the dispatcher forwards by name using its GLOBAL
            # config.json default backend — ignoring the slot's declared
            # device. On a cold miss we kick SlotManager.load(slot_name)
            # FIRST, which routes through LemonadeProvider.load(cfg) and
            # sends the device-derived llamacpp_backend, so the per-model
            # backend sticks. We do NOT inject llamacpp_backend into the
            # chat-completions body — lemond's chat endpoint ignores it.
            await self._ensure_slot_loaded_backend_aware(call)
            # Swap-window gate: refuse to forward if the slot is loading.
            # Without this, requests hit a dead port (502) or a still-
            # loading llama-server (raw 503 with no Retry-After).
            self._check_slot_ready_for_dispatch(call)
            return await self._forward_with_serving(call)
        if call.container_slot_name and self._slot_manager is not None:
            # Container-slot readiness gate (#656): container slots register
            # as kind="remote" upstreams so slot_name is empty above and the
            # Lemonade gate never fires.  We probe the container directly
            # (systemctl is-active + /health) before forwarding so the client
            # gets a structured slot.loading 503 with Retry-After instead of
            # a raw 502 ConnectError when the container is down or starting.
            await self._check_container_slot_ready(call)
        return await self._forward_plain(call)

    def _guard_gpu_image_mode(self, call: UpstreamCall) -> None:
        """Refuse llm-group dispatch while the GPU is in exclusive image mode.

        Delegates to :meth:`GpuArbiter.guard_llm_dispatch`, which raises the
        typed ``GpuImageMode`` (503, code ``gpu.image_mode``, details carry
        ``retry_after_s``) for llm-group slots and no-ops for everything
        else (img slot itself, NPU/CPU/lemonade slots, remote upstreams).
        The error middleware promotes ``retry_after_s`` to a ``Retry-After``
        header exactly like it does for ``SlotLoading`` — no parallel
        plumbing.

        Test stand-ins without an ``arbiter`` attribute opt out via the
        ``getattr`` (the real SlotManager always exposes the lazy property).
        """
        arbiter = getattr(self._slot_manager, "arbiter", None)
        if arbiter is None:
            return
        arbiter.guard_llm_dispatch(call.slot_name or call.container_slot_name)

    async def _ensure_slot_loaded_backend_aware(self, call: UpstreamCall) -> None:
        """Kick a backend-aware load on a cold miss before forwarding.

        B1 (ADR-0022) — the name-based lazy-load gap. When a request
        resolves to a slot whose model is NOT currently in lemond's
        ``/v1/health.loaded[]``, lemond would auto-load it on the first
        forward using its global default backend (rocm) regardless of the
        slot's declared ``device``. To make the per-model backend stick we
        instead drive ``SlotManager.load(slot_name)`` here, which sends the
        device-derived ``llamacpp_backend`` through ``LemonadeProvider.load``.

        Behaviour:
          - Slot already READY/SERVING/IDLE → no-op (model is loaded, fast
            path; no load call).
          - Otherwise → kick ``SlotManager.load(slot_name)`` (awaited so the
            load actually starts and the slot transitions out of OFFLINE).
            Control then returns to ``forward()`` whose
            ``_check_slot_ready_for_dispatch`` raises the typed
            ``SlotLoading`` 503 (with Retry-After) the client retries into —
            by which point the model is loading under the correct backend.

        Never injects into the request body. Load errors are logged and
        swallowed so the subsequent ready-check, not this helper, decides
        the client-facing outcome.
        """
        assert self._slot_manager is not None  # narrowed by forward()
        slot_name = call.slot_name
        # Ready-set: READY | SERVING | IDLE — single source per #696.
        if self._slot_manager.is_ready_for_dispatch(slot_name):
            # Model is already loaded under whatever backend it loaded with;
            # nothing to do. (A declared≠actual drift is surfaced by the
            # status enrichment, not corrected mid-request.)
            return
        # Cold miss — drive the backend-aware load. SlotManager.load is
        # idempotent (it returns early when already loaded) and routes the
        # device→llamacpp_backend through LemonadeProvider.load(cfg), so the
        # per-model backend sticks instead of falling back to lemond's
        # global default.
        try:
            await self._slot_manager.load(slot_name)
        except Exception as exc:
            log.warning(
                "dispatch.backend_aware_load_failed",
                slot=slot_name,
                upstream=call.upstream_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _check_container_slot_ready(self, call: UpstreamCall) -> None:
        """Raise :class:`SlotLoading` if a container-backed slot isn't ready.

        Delegates to :meth:`SlotManager.container_readiness_check` which
        runs two live probes:

          1. ``systemctl is-active`` — unit must be running.
          2. GET /health on the slot port — inference server must be up.

        Raises :class:`SlotLoading` (503 + Retry-After) when either probe
        fails, giving clients a retryable structured error instead of a
        raw 502 ConnectError.

        No-op when the slot is confirmed ready; forward proceeds normally.
        """
        assert self._slot_manager is not None  # narrowed by forward()
        slot_name = call.container_slot_name
        ready, reason = await self._slot_manager.container_readiness_check(slot_name)
        if not ready:
            raise SlotLoading(
                f"container slot {slot_name!r} is not ready ({reason})",
                details={
                    "slot": slot_name,
                    "state": reason,
                    "retry_after_s": 15,
                    "progress": {
                        "phase": reason,
                        "requested_model": call.requested_model,
                        "upstream": call.upstream_name,
                    },
                },
            )

    def _check_slot_ready_for_dispatch(self, call: UpstreamCall) -> None:
        """Raise :class:`SlotLoading` if the target slot isn't ready to serve.

        Ready set: ``READY``, ``SERVING``, ``IDLE`` (per #696 — single source
        in :meth:`SlotManager.is_ready_for_dispatch`).  Any other state means
        the slot is mid-lifecycle (``OFFLINE``, ``PULLING``, ``STARTING``,
        ``WARMING``, ``UNLOADING``, ``ERROR``) and forwarding would either
        ConnectError or get a raw 503 from llama-server's "still loading"
        gate.  We raise a typed error here so the middleware can emit a
        structured envelope plus a ``Retry-After`` header.
        """
        assert self._slot_manager is not None  # narrowed by caller
        if self._slot_manager.is_ready_for_dispatch(call.slot_name):
            return

        current = self._slot_manager.state(call.slot_name)
        raise SlotLoading(
            f"slot {call.slot_name!r} is {current.value} — not ready to serve",
            details=self._build_loading_response(call, current),
        )

    async def _recover_evicted_slot(self, call: UpstreamCall) -> bool:
        """Attempt to resync a slot whose upstream port went dead unexpectedly.

        Called from ``_forward_direct`` / ``_forward_streaming`` when an
        :class:`httpx.ConnectError` lands on a slot upstream.  hal0's
        in-memory state said the slot was READY/SERVING/IDLE (the gate
        let us through), but the upstream port was dead — the usual
        cause is a Lemonade idle/OOM eviction that hal0 didn't observe.

        Delegates to ``SlotManager.recover_evicted_slot`` which forces
        the slot OFFLINE and re-runs the normal load lifecycle (per-slot
        lock serializes concurrent requests).

        Returns:
          ``True``  — slot recovered, caller should retry the forward once.
          ``False`` — recovery is not applicable (remote upstream, no slot
            manager wired) or the recover call itself raised.  Caller
            should surface the original :class:`UpstreamUnavailable`.

        Raises:
          GpuImageMode: NON-NEGOTIABLE (D4 review) — when the GpuArbiter
            is in exclusive image mode and this is an llm-group slot, the
            dead port is (or may as well be) the ARBITER's doing: it
            unloaded the slot to hand the GPU to the img slot.  Recovery
            here would reload an LLM model INTO image mode and fight the
            arbiter for the GPU, so we suppress it and surface the same
            structured ``gpu.image_mode`` 503 envelope the dispatch guard
            emits (Retry-After included via the error middleware).
        """
        if not (call.slot_name and self._slot_manager is not None):
            return False
        arbiter = getattr(self._slot_manager, "arbiter", None)
        if arbiter is not None:
            # Raises GpuImageMode for llm-group slots while mode == img;
            # no-op otherwise. Covers the race where the arbiter flipped
            # to img (and force-killed the container) mid-request.
            arbiter.guard_llm_dispatch(call.slot_name)
        log.warning(
            "dispatch.upstream_dead_attempting_recover",
            upstream=call.upstream_name,
            slot=call.slot_name,
            target=call.target_url,
        )
        try:
            await self._slot_manager.recover_evicted_slot(call.slot_name)
        except Exception as exc:
            log.warning(
                "dispatch.recover_evicted_slot_failed",
                slot=call.slot_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False
        log.info(
            "dispatch.upstream_recovered",
            upstream=call.upstream_name,
            slot=call.slot_name,
        )
        return True

    def _build_loading_response(
        self,
        call: UpstreamCall,
        state: Any,  # SlotState; typed loosely to avoid an import cycle
    ) -> dict[str, Any]:
        """Shape the ``details`` block on the :class:`SlotLoading` envelope.

        Option B (UX-aware): the response includes a ``progress`` block so
        the dashboard renders a "model loading…" chip with the slot + the
        model the user asked for, while still being a clean 503 that
        OpenAI-style SDKs treat as retryable via ``Retry-After``.

        Retry cadence: Strix Halo iGPU loads typically take 15-60s
        depending on model size.  We hint 15s — enough to avoid hammering
        the API, short enough that an active user doesn't give up.
        """
        return {
            "slot": call.slot_name,
            "state": state.value,
            "retry_after_s": 15,
            "progress": {
                "phase": state.value,
                "requested_model": call.requested_model,
                "upstream": call.upstream_name,
            },
        }

    async def _forward_plain(self, call: UpstreamCall) -> Response:
        client = self._get_http_client()
        if call.streaming:
            return await self._forward_streaming(client, call)
        return await self._forward_direct(client, call)

    async def _forward_with_serving(self, call: UpstreamCall) -> Response:
        """Wrap _forward_plain in SlotManager.serving for the slot's lifetime.

        Non-streaming responses release the context before returning.
        Streaming responses keep the context alive by wrapping the
        upstream iterator so the slot stays SERVING until the client
        finishes consuming the stream (or the response is GC'd).
        """
        assert self._slot_manager is not None  # narrowed by forward()
        ctx = self._slot_manager.serving(call.slot_name)
        await ctx.__aenter__()
        released = False

        async def _release() -> None:
            nonlocal released
            if released:
                return
            released = True
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as exc:  # never bury the request's outcome
                log.warning(
                    "dispatch.serving_release_failed",
                    slot=call.slot_name,
                    error=str(exc),
                )

        try:
            client = self._get_http_client()
            if call.streaming:
                resp = await self._forward_streaming(client, call)
                inner = resp.body_iterator

                async def _drained() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in inner:
                            yield chunk if isinstance(chunk, bytes) else chunk.encode()
                    finally:
                        await _release()

                resp.body_iterator = _drained()
                return resp
            try:
                return await self._forward_direct(client, call)
            finally:
                await _release()
        except BaseException:
            await _release()
            raise

    async def _forward_direct(
        self,
        client: httpx.AsyncClient,
        call: UpstreamCall,
    ) -> Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await client.request(
                    call.method,
                    call.target_url,
                    content=call.body or None,
                    headers=call.headers,
                )
                break
            except _RECOVERABLE_TRANSPORT_ERRORS as exc:
                if attempt == 1 and await self._recover_evicted_slot(call):
                    continue
                log.warning(
                    "dispatch.forward_failed",
                    upstream=call.upstream_name,
                    method=call.method,
                    target=call.target_url,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise UpstreamUnavailable(
                    f"upstream {call.upstream_name!r} unreachable: {type(exc).__name__}",
                    details={
                        "upstream": call.upstream_name,
                        "target": call.target_url,
                        "error": str(exc),
                    },
                ) from exc
            except (httpx.HTTPError, OSError) as exc:
                log.warning(
                    "dispatch.forward_failed",
                    upstream=call.upstream_name,
                    method=call.method,
                    target=call.target_url,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise UpstreamUnavailable(
                    f"upstream {call.upstream_name!r} unreachable: {type(exc).__name__}",
                    details={
                        "upstream": call.upstream_name,
                        "target": call.target_url,
                        "error": str(exc),
                    },
                ) from exc

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=_filter_response_headers(resp.headers),
            media_type=resp.headers.get("content-type"),
        )

    async def _forward_streaming(
        self,
        client: httpx.AsyncClient,
        call: UpstreamCall,
    ) -> StreamingResponse:
        # We open the stream eagerly so connect errors surface as
        # UpstreamUnavailable instead of leaking through StreamingResponse
        # as a generator-time exception that confuses the error middleware.
        attempt = 0
        while True:
            attempt += 1
            try:
                req = client.build_request(
                    call.method,
                    call.target_url,
                    content=call.body or None,
                    headers=call.headers,
                )
                resp = await client.send(req, stream=True)
                break
            except _RECOVERABLE_TRANSPORT_ERRORS as exc:
                if attempt == 1 and await self._recover_evicted_slot(call):
                    continue
                log.warning(
                    "dispatch.forward_stream_open_failed",
                    upstream=call.upstream_name,
                    method=call.method,
                    target=call.target_url,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise UpstreamUnavailable(
                    f"upstream {call.upstream_name!r} unreachable: {type(exc).__name__}",
                    details={
                        "upstream": call.upstream_name,
                        "target": call.target_url,
                        "error": str(exc),
                    },
                ) from exc
            except (httpx.HTTPError, OSError) as exc:
                log.warning(
                    "dispatch.forward_stream_open_failed",
                    upstream=call.upstream_name,
                    method=call.method,
                    target=call.target_url,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise UpstreamUnavailable(
                    f"upstream {call.upstream_name!r} unreachable: {type(exc).__name__}",
                    details={
                        "upstream": call.upstream_name,
                        "target": call.target_url,
                        "error": str(exc),
                    },
                ) from exc

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()

        return StreamingResponse(
            _iter(),
            status_code=resp.status_code,
            headers=_filter_response_headers(resp.headers),
            media_type=resp.headers.get("content-type"),
        )

    # ── internals ────────────────────────────────────────────────────────

    def _default_for_path(self, path: str) -> str:
        if "/embeddings" in path:
            return _EMBED_DEFAULT
        if "/rerank" in path:
            return _RERANK_DEFAULT
        if "/audio/speech" in path:
            return _TTS_DEFAULT
        if "/images/" in path:
            return _IMAGE_DEFAULT
        return _DEFAULT_MODEL

    def _registry_route_for(self, model_id: str) -> tuple[str, str] | None:
        """Look up a registry binding for a model id.

        Returns (upstream_name, upstream_model) or None if the model isn't
        in the registry or the registry isn't available.  A registry
        load failure raises :class:`RegistryLoadFailed` — never silently
        returns None (PLAN.md §5 Tier 1 against haloai dispatcher.py:115-120).
        """
        if self._models is None:
            return None
        try:  # TIER1 — was: bare except returning {} at haloai dispatcher.py:115-120
            route = self._models.route_for(model_id)
        except NotImplementedError:
            # Cross-subtree stub: Agent I/B hasn't shipped the registry yet.
            # NOTE: treated as "no binding" rather than fatal so the dispatcher
            # remains useful in unit tests against partial stubs.
            return None
        except Hal0Error:
            raise
        except Exception as exc:  # narrow, typed re-raise
            log.warning(
                "registry load failed",
                model=model_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise RegistryLoadFailed(
                "model registry unavailable",
                details={"error": str(exc)},
            ) from exc

        if route is None:
            return None
        # route_for returns a URL by interface; for now treat the URL as the
        # upstream identifier and rely on the model registry's richer API once
        # Agent I finalises it.  In tests we mock _registry_route_for directly.
        # NOTE: until ModelRegistry exposes (upstream_name, upstream_model)
        # tuple natively, this method may be monkey-patched in tests.
        return (route, model_id)

    def _upstreams_in_priority_order(self) -> list[Upstream]:
        try:
            return list(self._upstreams.list())
        except NotImplementedError:
            # Cross-subtree stub fallback.
            return []

    def _build_headers(self, request: Request, upstream: Upstream) -> dict[str, str]:
        """Forward client headers minus hop-by-hop, plus upstream auth slot.

        # NOTE: auth header materialisation depends on Agent J's
        # upstreams.auth_headers() helper which isn't shipped yet.  For
        # now we forward client headers verbatim minus hop-by-hop.
        """
        out: dict[str, str] = {}
        for k, v in request.headers.items():
            lk = k.lower()
            if lk in _HOP_BY_HOP or lk == "authorization":
                continue
            out[k] = v
        # Future hook: out.update(auth_headers(upstream)) when Agent J ships it.
        _ = upstream  # silence unused
        return out

    async def _cold_prefetch(self, cold_remotes: list[Upstream]) -> None:  # TIER2 + TIER3
        """Fan out /v1/models against cold remote upstreams, single-flighted.

        All concurrent prefetches for the same upstream share one HTTP call
        (Tier 3).  The total fanout is bounded by ``prefetch_timeout_s``
        (Tier 2, configurable; was hardcoded 4s in haloai).
        """

        async def _one(u: Upstream) -> None:
            key = f"prefetch:{u.name}"
            try:
                await self._single_flight.do(key, self._fetch_models, u)
            except Exception as exc:  # TIER1 — log, don't crash the fanout
                # Errors in cold prefetch are *expected* (remotes go offline).
                # We swallow at this granularity ONLY because the call sites
                # (passthrough recheck below) treat missing cache as no-route.
                log.debug(
                    "cold prefetch leg failed",
                    upstream=u.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        try:
            await asyncio.wait_for(
                asyncio.gather(*(_one(u) for u in cold_remotes), return_exceptions=True),
                timeout=self.prefetch_timeout_s,
            )
        except TimeoutError:
            log.info(
                "cold-cache prefetch timed out",
                remotes=len(cold_remotes),
                timeout_s=self.prefetch_timeout_s,
            )

    def _log_decision(self, call: UpstreamCall, t0: float, *, cache_state: str) -> None:
        """Emit one structured log line per dispatch decision (Tier 2)."""
        latency_ms = (time.monotonic() - t0) * 1000.0
        call.latency_ms = latency_ms
        # TIER2 — structlog with hal0-dispatch identifier (set by app logging config).
        log.info(
            "dispatch.decision",
            model=call.resolved_model or call.requested_model,
            resolution_path=call.resolution_path,
            upstream=call.upstream_name,
            cache_state=cache_state,
            latency_ms=round(latency_ms, 3),
        )


# ── helpers ───────────────────────────────────────────────────────────────────

# Hop-by-hop headers never forwarded (RFC 7230 §6.1).
_HOP_BY_HOP = frozenset(
    {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "upgrade",
        "content-encoding",
        "content-length",
        "host",
    }
)


def _remap_model(body: dict[str, Any], new_model: str) -> bytes:
    """Set ``body["model"] = new_model`` and re-serialise to bytes."""
    if new_model:
        body = {**body, "model": new_model}
    return json.dumps(body).encode("utf-8")


def _slot_name_of(upstream: Upstream) -> str:
    """Return the local slot name for ``upstream`` (empty for remotes).

    ``UpstreamCall.slot_name`` carries this through to ``forward()``,
    which uses it to gate the SERVING transition.  Falls back to
    ``upstream.name`` when ``slot_name`` is unset — autoregistered slots
    use the same value for both, but explicit upstreams.toml entries can
    override.

    The composite ``hal0`` upstream is exempt: it has no backing slot, so
    returning ``"hal0"`` here would make ``forward()`` run the readiness
    gate against a non-existent slot (always OFFLINE → spurious 503) and
    wrap the call in a ``SlotManager.serving("hal0")`` context that can
    never settle. Returning ``""`` routes it through ``_forward_plain``,
    which is correct because the actual slot lifecycle is enforced on the
    Lemonade gateway hop the composite forwards to.
    """
    if upstream.kind != "slot":
        return ""
    if _is_hal0_composite(upstream):
        return ""
    return upstream.slot_name or upstream.name


def _container_slot_name_of(upstream: Upstream) -> str:
    """Return the container slot name for a ``kind=remote`` container-backed upstream.

    Container slots register via ``SlotManager._register_container_upstream``
    as ``kind="remote"`` entries, and since #656 they carry ``slot_name`` set
    to the slot's name.  This distinguishes them from genuine external remotes
    (OpenAI, OpenRouter…) that have ``slot_name=None``.

    The returned name is stored in ``UpstreamCall.container_slot_name`` and
    triggers ``Dispatcher._check_container_slot_ready()`` before forwarding,
    returning a structured ``slot.loading`` 503 instead of a raw 502
    ConnectError when the container is down or still starting.
    """
    if upstream.kind != "remote":
        return ""
    # slot_name is set only for container-backed remotes (see _register_container_upstream)
    return upstream.slot_name or ""


def _join_url(upstream_url: str, request_path: str) -> str:
    """Map ``/v1/<path>`` onto ``<upstream_url>/<path>``.

    Upstream URLs end in ``/v1`` by convention (per upstreams.toml).  The
    incoming request path is ``/v1/chat/completions`` etc., so we strip
    the leading ``/v1`` before joining to avoid ``/v1/v1/...``.

    Before joining, applies ``_UPSTREAM_PATH_REWRITES`` so that e.g.
    ``/v1/rerankings`` is rewritten to ``/v1/rerank`` for llama-server
    backends (which serve POST ``/rerank`` natively, not ``/rerankings``).
    """
    # Apply path rewrites before stripping /v1 — rewrites operate on the full
    # incoming path so both the key and value already carry the /v1 prefix.
    effective_path = _UPSTREAM_PATH_REWRITES.get(request_path, request_path)
    base = upstream_url.rstrip("/")
    suffix = effective_path[3:] if effective_path.startswith("/v1") else effective_path
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return base + suffix


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    """Drop hop-by-hop and length headers so Starlette can recompute them."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS}


__all__ = [
    "DispatchError",
    "Dispatcher",
    "NoRouteFound",
    "RegistryLoadFailed",
    "UnknownUpstream",
    "UpstreamCall",
    "UpstreamUnavailable",
]
