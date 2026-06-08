"""OpenAI-compatible endpoints (mounted under /v1).

All POST endpoints share the same shape: parse the JSON body, hand it to
:meth:`Dispatcher.dispatch` to resolve an :class:`UpstreamCall`, then
:meth:`Dispatcher.forward` it.  Streaming responses (SSE for chat /
completions, binary for ``/audio/speech``) and non-streaming responses
are both handled inside ``forward`` — this module just exposes the
endpoints.

GET ``/v1/models`` aggregates the model ids advertised by every
configured upstream's ``/v1/models``.  Returns the OpenAI shape so
clients (OpenWebUI, the chat UI, third-party SDKs) work unmodified.

POST ``/v1/images/generations`` (Team K) is the odd one out: ComfyUI's
HTTP surface is a graph-submit + history-poll dance, not a direct
OpenAI passthrough. The route resolves the slot via the dispatcher to
discover the port, then drives the ComfyUIProvider directly to translate
the OpenAI body to a workflow, run it, and unwrap the result PNGs back
into the OpenAI response shape. See ``ComfyUIProvider.infer`` for the
upstream protocol.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from hal0.api import image_cache
from hal0.api.deps import DispatcherDep

log = structlog.get_logger("hal0-v1")

# Inference router. Mounted by hal0.api.create_app() on /v1.
# Auth was removed in ADR-0012; the server is open on the local network.
router = APIRouter()

# Public probe router — auth-free. Mounted on the same /v1 prefix.
# OpenAI clients (OpenWebUI, third-party SDKs) GET /v1/models before
# they have anywhere to send an Authorization header; gating it would
# break the "drop in an OpenAI-shaped base_url" UX. Per ADR-0001 Child
# B, publicness is declared by NOT attaching an auth dep — not by
# allowlisting the path in a frozenset.
public_router = APIRouter()


def _slot_events(app_state: Any, slot_name: str | None) -> Any:
    """Return the per-slot tps_events deque for ``slot_name`` (or None).

    ``app_state.tps_events`` is a defaultdict so creating a new deque is
    a side effect of the lookup — keep that out of the hot path when
    slot_name is missing or the deque store itself isn't present.
    """
    events_store = getattr(app_state, "tps_events", None)
    if events_store is None or not slot_name:
        return None
    return events_store[slot_name]


def _slot_ttft_events(app_state: Any, slot_name: str | None) -> Any:
    """Per-slot ttft_events deque (mirrors `_slot_events`)."""
    events_store = getattr(app_state, "ttft_events", None)
    if events_store is None or not slot_name:
        return None
    return events_store[slot_name]


def _instrument_streaming_throughput(
    response: StreamingResponse,
    app_state: Any,
    slot_name: str | None,
    dispatch_started: float | None = None,
) -> StreamingResponse:
    """Wrap a streaming response body iterator with a token counter
    plus a one-shot TTFT recorder.

    Token throughput: appends ``(monotonic, tokens)`` to the per-slot
    deque so /api/slots/metrics can surface a real current-throughput
    number per slot. Token count per chunk is approximated by counting
    ``"delta":`` occurrences in the raw SSE bytes — close enough for a
    throughput indicator and far cheaper than a full SSE parse.

    TTFT: when `dispatch_started` is provided, the first chunk that
    carries at least one ``"delta":`` marker records
    ``(monotonic, monotonic - dispatch_started)`` into the per-slot
    ttft deque. We key off the first content delta (not the first
    arbitrary chunk) because llama-server emits an initial role-only
    chunk before any generated tokens — that role chunk arrives in
    microseconds after prefill and would mask the real prefill cost.
    """
    original = response.body_iterator
    events = _slot_events(app_state, slot_name)
    ttft_events = _slot_ttft_events(app_state, slot_name) if dispatch_started is not None else None
    if events is None and ttft_events is None:
        return response
    ttft_pending = ttft_events is not None  # one-shot per response

    async def _counting() -> Any:
        nonlocal ttft_pending
        async for chunk in original:
            if isinstance(chunk, (bytes, bytearray)):
                tokens = chunk.count(b'"delta":')
            elif isinstance(chunk, str):
                tokens = chunk.count('"delta":')
            else:
                tokens = 0
            if tokens > 0:
                now = time.monotonic()
                if events is not None:
                    events.append((now, tokens))
                if ttft_pending and ttft_events is not None and dispatch_started is not None:
                    ttft_events.append((now, max(0.0, now - dispatch_started)))
                    ttft_pending = False
            yield chunk

    response.body_iterator = _counting()
    return response


def _record_nonstreaming_throughput(
    body_bytes: bytes, app_state: Any, slot_name: str | None
) -> None:
    """Pull ``usage.completion_tokens`` + a recent timestamp out of a JSON
    response body so non-streaming chats also move the per-slot tile."""
    events = _slot_events(app_state, slot_name)
    if events is None or not body_bytes:
        return
    try:
        data = json.loads(body_bytes)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    usage = data.get("usage") or {}
    completion = usage.get("completion_tokens") or 0
    if not isinstance(completion, (int, float)) or completion <= 0:
        return
    # Without a real start time, attribute the whole completion to "now"
    # — the rolling window will smear it across the lookback. Better
    # alternatives need start-time tracking through forward().
    events.append((time.monotonic(), int(completion)))


def _record_flm_native_metrics(
    body_bytes: bytes,
    app_state: Any,
    slot_name: str | None,
    model_name: str | None,
) -> None:
    """Sniff FLM-native perf fields from a chat-completion response body.

    PR-12 (plan §11 + §12.1 + memory ``hal0_lemonade_flm_npu_install``).
    FastFlowLM emits ``decoding_speed_tps`` / ``prefill_speed_tps`` /
    ``prefill_duration_ttft`` / ``kv_token_occupancy_rate_percentage`` /
    ``decoding_duration`` INSIDE the chat-completion response body —
    not via ``/v1/stats``. This hook hands those fields to the
    MetricsShim's in-memory store so the next /api/metrics/prometheus
    scrape includes them.

    The discriminator is presence of any FLM field in the payload
    (handled by ``FlmMetrics.from_payload``). Non-FLM upstreams produce
    no FLM keys → no-op. This keeps the dispatcher path uncoupled from
    recipe routing — we don't need to ask "is this slot llamacpp or
    flm?" before recording; the payload shape answers itself.

    Robust by design: any failure (no shim attached, bad slot/model,
    unparseable JSON) is silently swallowed so a metrics glitch never
    affects the user-visible chat response.
    """
    shim = getattr(app_state, "lemonade_metrics_shim", None)
    if shim is None or not body_bytes or not slot_name or not model_name:
        return
    try:
        data = json.loads(body_bytes)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    try:
        shim.record_flm_metrics(slot_name, model_name, data)
    except Exception:  # pragma: no cover — defensive
        # The shim's record_flm_metrics is documented as non-raising,
        # but we wrap defensively so a future contract slip can't take
        # down the chat path.
        return


async def _read_json_body(request: Request) -> dict[str, Any]:
    """Best-effort JSON parse.  Empty / malformed bodies become ``{}``.

    The dispatcher tolerates empty bodies (path-default model resolution
    kicks in); validation of the parsed shape belongs to the upstream.

    Multipart/form-data requests (audio uploads to /v1/audio/transcriptions
    and friends) are not JSON; we parse just enough to extract the ``model``
    field so the dispatcher can route. The body itself is forwarded raw —
    the upstream FLM server re-reads multipart from the inbound request.
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception:
            return {}
        # Surface the model field so dispatcher.dispatch can route; other
        # fields aren't needed at this layer.
        model = form.get("model")
        return {"model": str(model)} if isinstance(model, str) else {}

    try:
        raw = await request.body()
    except Exception:
        return {}
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _rewrite_chat_slot_alias(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Translate a chat-slot ALIAS in ``body["model"]`` to its model id.

    hermes-role-slots: a request may address a co-resident chat slot by
    its **alias** (slot name: ``chat`` / ``agent`` / ``utility``; legacy
    ``primary`` / ``agent-hermes`` also accepted via back-compat aliases)
    instead of the underlying model id. Lemonade serves chat models by
    name on lemond, so we rewrite the alias to the slot's configured model
    id HERE, at the route layer, before either the dispatcher routes it or
    the lemonade fall-through forwards it. After the rewrite, both
    ``model==alias`` and ``model==model_id`` carry the correct distinct
    model name down the existing path and hit the right co-resident model.

    The rewrite is applied to BOTH:
      * the returned ``body`` dict (handed to ``dispatcher.dispatch``), and
      * the request's cached body bytes (``request._body``) — so the
        ``NoRouteFound`` → ``lemonade_proxy._proxy`` fall-through, which
        re-reads ``request.body()`` verbatim, forwards the rewritten model
        name rather than the bare alias.

    No-op when: the model isn't a known chat-slot alias, equals its own
    model id already, the slot manager is absent, or the config read
    raises (best-effort — never blocks the request).
    """
    raw_model = body.get("model")
    if not isinstance(raw_model, str) or not raw_model:
        return body
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is None:
        return body
    from hal0.api import hal0_chat_slot_alias_map

    try:
        alias_to_model = await hal0_chat_slot_alias_map(slot_manager)
    except Exception:
        return body
    mapped = alias_to_model.get(raw_model)
    if not mapped or mapped == raw_model:
        return body

    new_body = {**body, "model": mapped}
    # Overwrite the cached request body so the lemonade proxy fall-through
    # (which reads request.body()) forwards the rewritten model name. If we
    # can't (unexpected request shape), still return the rewritten dict —
    # the dispatcher path benefits even if the proxy path can't.
    import contextlib

    with contextlib.suppress(Exception):
        request._body = json.dumps(new_body).encode("utf-8")  # type: ignore[attr-defined]
    return new_body


def _normalize_loaded_models(request: Request) -> set[str]:
    """Currently-loaded model ids from the cached health snapshot (NO new lemond poll)."""
    shim = getattr(request.app.state, "lemonade_metrics_shim", None)
    if shim is None:
        return set()
    try:
        return set(shim._health.loaded_models)
    except Exception:  # pragma: no cover — defensive
        return set()


async def _normalize_slot_views(request: Request) -> list:
    """Build SlotView list from slot config (awaits hal0_llm_slot_views, like the
    existing per-request alias-map read)."""
    from hal0.api import hal0_llm_slot_views
    from hal0.normalize.resolver import SlotView

    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return []
    rows = await hal0_llm_slot_views(sm, getattr(request.app.state, "model_registry", None))
    return [
        SlotView(
            name=r["name"],
            role=r.get("role"),
            device=r.get("device", ""),
            model_id=r["model_id"],
            context_length=int(r.get("context_length") or 0),
        )
        for r in rows
    ]


def _is_remote_model(request: Request, model_id: str) -> bool:
    """True if model_id maps to a kind=='remote' upstream (skip thinking injection)."""
    upstreams = getattr(request.app.state, "upstreams", None)
    cache = getattr(request.app.state, "upstream_models", {}) or {}
    if upstreams is None:
        return False
    try:
        for u in upstreams.list():
            if getattr(u, "kind", "") == "remote" and model_id in set(cache.get(u.name, [])):
                return True
    except Exception:  # pragma: no cover — defensive
        return False
    return False


async def _slot_thinking_default(request: Request, model_id: str) -> bool:
    """Per-slot reasoning default: the ``enable_thinking`` flag of the slot whose
    default model is ``model_id``. Falls back to False (global suppression) when
    no slot sets it. Always overridable per request."""
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return False
    try:
        for cfg in await sm.iter_configs():
            if not isinstance(cfg, dict):
                continue
            model = cfg.get("model")
            default_model = model.get("default") if isinstance(model, dict) else None
            if default_model == model_id and cfg.get("enable_thinking") is not None:
                return bool(cfg["enable_thinking"])
    except Exception:
        return False
    return False


async def _normalize_chat_body(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Resolve hal0/* virtual model names + inject thinking policy for lemond-bound calls.

    Rewrites request._body so BOTH the dispatcher path and the NoRouteFound proxy
    fall-through observe the normalized body.
    """
    from hal0.normalize.resolver import LiveSlotResolver
    from hal0.normalize.thinking import apply_thinking_policy

    views = await _normalize_slot_views(request)
    resolver = LiveSlotResolver(
        slot_views_provider=lambda: views,
        loaded_models_provider=lambda: _normalize_loaded_models(request),
    )
    raw_model = body.get("model")
    if isinstance(raw_model, str) and raw_model:
        res = await resolver.resolve(raw_model)
        if res is not None and res.model_id:
            body = {**body, "model": res.model_id}

    model_id = body.get("model")
    if isinstance(model_id, str) and not _is_remote_model(request, model_id):
        default_thinking = await _slot_thinking_default(request, model_id)
        body = apply_thinking_policy(body, default_thinking=default_thinking)

    import contextlib

    with contextlib.suppress(Exception):
        request._body = json.dumps(body).encode("utf-8")  # type: ignore[attr-defined]
    return body


async def _ensure_backend_for_model(request: Request, body: dict[str, Any]) -> None:
    """#430: load a slot-backed model under its DECLARED backend before routing.

    A by-name request reaches lemond by one of several paths depending on
    cache/registry state — the composite ``hal0`` passthrough → lemond
    gateway (PR #424), a real per-slot upstream → ``forward()`` (B1), or the
    no-route → lemonade-proxy catch-all. On every one of them lemond, given a
    model it hasn't loaded, auto-loads it under its GLOBAL ``config.json``
    default backend (``rocm``) — ignoring a slot that declares
    ``device=gpu-vulkan``. B1 only covers the real-slot path, which in the
    current deployment has no registered per-slot upstreams, so it never
    fires.

    Rather than patch each path, we resolve ``model_id`` → owning chat slot
    and drive ``SlotManager.load(slot)`` HERE, before ``dispatcher.dispatch``
    — idempotent, and it routes the device-derived ``llamacpp_backend``
    through ``LemonadeProvider.load``. Whichever path dispatch then takes, the
    model is already loaded under the right backend, so lemond serves the
    existing child instead of auto-loading under its global default. ``load``
    blocks to READY, preserving the existing single-request synchronous-load
    UX (just under the right backend).

    Scope: chat (``type=llm``) slots, matching the alias map and B1's focus;
    a model with no backing chat slot is left to lemond's global default
    (acceptance criterion: unbacked models unaffected). A slot already loaded
    under the wrong backend is NOT corrected mid-request (``load`` is a no-op
    on a ready slot) — that drift is surfaced by status and corrected via the
    manual ``/api/slots/{name}/backend`` control (B3).

    Best-effort: any failure is logged and swallowed so routing still proceeds
    (lemond auto-loads as before) rather than 500ing on this new code path.
    """
    model_id = body.get("model")
    if not isinstance(model_id, str) or not model_id:
        return
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is None:
        return
    from hal0.api import hal0_chat_slot_alias_map

    try:
        alias_to_model = await hal0_chat_slot_alias_map(slot_manager)
    except Exception:
        return
    # Reverse the alias→model_id map: find the chat slot that owns this model.
    slot_name = next((slot for slot, mid in alias_to_model.items() if mid == model_id), None)
    if slot_name is None:
        # No backing chat slot — nothing to honor; lemond's global default applies.
        return
    try:
        await slot_manager.load(slot_name)
    except Exception as exc:
        log.warning(
            "v1.backend_aware_load_failed",
            slot=slot_name,
            model=model_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def _dispatch_and_forward(
    request: Request,
    dispatcher: DispatcherDep,
    body: dict[str, Any] | None = None,
) -> Response:
    if body is None:
        body = await _read_json_body(request)
    # Translate a chat-slot alias (chat/agent/utility; also primary/agent-hermes) → model id
    # before routing so both the dispatcher and the lemonade fall-through
    # see the real model name.
    body = await _rewrite_chat_slot_alias(request, body)
    # #430: backend-aware load BEFORE dispatch, so a slot-backed model is
    # loaded under its declared backend whichever routing path dispatch then
    # takes (composite→gateway, real slot, or proxy fall-through).
    await _ensure_backend_for_model(request, body)
    from hal0.dispatcher.router import NoRouteFound

    try:
        call = await dispatcher.dispatch(request, body=body)
    except NoRouteFound:
        # Lemonade proxy fall-through (#275 bug 5). The dispatcher only
        # knows about models advertised by configured upstreams + the
        # hal0 model registry. Models pulled via Lemonade `/v1/pull`
        # land in lemond's loaded[] but never in hal0's registry, so
        # the dispatcher's `dispatch_and_forward` would 404 on them
        # even though they're perfectly serveable.
        #
        # Delegate to the catch-all `/v1/{path:path}` proxy (PR #248)
        # which forwards verbatim to lemond. This preserves the
        # specialized routes' value (OmniRouter tool-call loop, FLM
        # trio detection, TTFT instrumentation) for the cases they
        # actually handle, while letting bare Lemonade-loaded models
        # round-trip through hal0-api without registry registration.
        from hal0.api.routes.lemonade_proxy import _proxy

        # `request.url.path` is e.g. `/v1/chat/completions`; `_proxy`
        # expects the path AFTER `/v1/` as its second arg (FastAPI's
        # path converter strips it before passing).
        proxy_path = request.url.path.removeprefix("/v1/").lstrip("/")
        # #430 backend-aware load already ran pre-dispatch (see
        # _ensure_backend_for_model above), so the model reaching lemond here
        # is already loaded under its slot's declared backend.
        return await _proxy(request, proxy_path)
    # Remember the most recent model we sent to this upstream so the
    # dashboard's synthetic slot reflects what's actually being used,
    # not the first-non-alias from the catalog.
    last_used = getattr(request.app.state, "last_used_model", None)
    if last_used is not None and call.upstream_name and call.resolved_model:
        last_used[call.upstream_name] = call.resolved_model

    # Capture TTFT against the moment we actually hand off to forward()
    # — anything before this point (auth, body parse, dispatcher
    # routing) is local overhead, not prefill cost.
    dispatch_started = time.monotonic()
    response = await dispatcher.forward(call)
    if isinstance(response, StreamingResponse):
        return _instrument_streaming_throughput(
            response,
            request.app.state,
            call.upstream_name,
            dispatch_started=dispatch_started,
        )
    if isinstance(response, Response) and getattr(response, "body", None):
        _record_nonstreaming_throughput(response.body, request.app.state, call.upstream_name)
        # PR-12: FLM-native metric ingest. The hook is unconditional —
        # ``record_flm_metrics`` only acts when the payload carries FLM
        # fields, so non-FLM upstreams pay only a JSON parse. Streaming
        # FLM responses (where the same fields land in the final SSE
        # chunk) are deferred to a follow-up; plan §11 PR-12 scope is
        # the non-streaming hook + the /v1/stats poll surface.
        _record_flm_native_metrics(
            response.body,
            request.app.state,
            call.upstream_name,
            call.resolved_model,
        )
    return response


@public_router.get("/models")
async def list_models(
    request: Request,
    dispatcher: DispatcherDep,
) -> dict[str, object]:
    """Aggregate ``/v1/models`` across every configured upstream.

    Returns the OpenAI shape: ``{"object": "list", "data": [...]}``.
    Fetches each upstream's catalog on demand (no caching yet — a TTL
    cache lands when the dispatcher gets one).

    Two classes of entries are emitted:

    * **Per-slot alias entries** (``hermes-role-slots``). Every enabled
      chat slot (``type == "llm"``) that is currently loaded in lemond
      surfaces as one model object whose ``id`` is the slot **alias =
      slot name** (``primary``, ``agent-hermes``, ``utility``), carrying a
      human ``name`` (``"<slot> · <model display name>"``) and the slot's
      ``context_length``. Built by :func:`hal0.api.hal0_slot_alias_models`.
      Unloaded / disabled slots are omitted. The alias is stable across
      model swaps so callers can pin a co-resident slot.
    * **Upstream catalog entries** — the raw model ids each
      ``advertise_models`` upstream reports, so non-chat models (embed /
      rerank / image / …) keep their direct-addressing entries. The
      composite ``hal0`` upstream's CHAT model ids are suppressed here so
      they don't duplicate the alias entries above — a chat slot is
      represented exactly once, by its alias.

    PUBLIC — mounted on ``public_router`` so OpenAI SDKs that probe the
    catalog before sending Authorization headers continue to work after
    ADR-0001 Child B.
    """
    from hal0.api import hal0_chat_slot_model_ids, hal0_slot_alias_models

    upstreams = request.app.state.upstreams
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {}) or {}
    seen: set[str] = set()
    data: list[dict[str, Any]] = []
    now = int(time.time())

    slot_manager = getattr(request.app.state, "slot_manager", None)
    model_registry = getattr(request.app.state, "model_registry", None)

    # Per-slot alias entries first so a slot alias (e.g. "chat") wins
    # the id over any same-named raw model id an upstream might advertise.
    if slot_manager is not None and model_registry is not None:
        try:
            alias_entries = await hal0_slot_alias_models(slot_manager, model_registry, now=now)
        except Exception:
            alias_entries = []
        for entry in alias_entries:
            mid = entry.get("id")
            if not isinstance(mid, str) or mid in seen:
                continue
            seen.add(mid)
            data.append(entry)

    # Chat-slot model ids are represented by their aliases above; suppress
    # them from the raw upstream catalog so the composite ``hal0`` upstream
    # doesn't emit duplicate ``id=<model_id>`` rows for the same chat slots.
    chat_model_ids: set[str] = set()
    if slot_manager is not None:
        try:
            chat_model_ids = await hal0_chat_slot_model_ids(slot_manager)
        except Exception:
            chat_model_ids = set()

    for u in upstreams.list():
        if not getattr(u, "advertise_models", True):
            continue
        # The composite ``hal0`` upstream's URL is hal0-api itself —
        # going over HTTP here would re-enter this handler and loop. Its
        # model list lives in ``upstream_models["hal0"]``, refreshed by
        # ``_fetch_hal0_composite_models`` on startup, slot-state events,
        # and the dispatcher passthrough path.
        if u.kind == "slot" and u.slot_name is None and u.name == "hal0":
            advertised = list(model_cache.get("hal0", []))
        else:
            try:
                advertised = await upstreams.fetch_models(u.name)
            except Exception:
                advertised = []
        for mid in advertised:
            if mid in seen:
                continue
            # A chat slot's raw model id is already covered by its alias
            # entry — don't list it twice.
            if mid in chat_model_ids:
                continue
            seen.add(mid)
            data.append(
                {
                    "id": mid,
                    "object": "model",
                    "created": now,
                    "owned_by": u.name,
                }
            )
    # Advertise live-resolve virtual names so Hermes' /model picker discovers them.
    # context_length is mandatory: without it Hermes assumes a 256K window.
    from hal0.normalize.resolver import DEFAULT_CHAINS, LiveSlotResolver

    views = await _normalize_slot_views(request)
    resolver = LiveSlotResolver(
        slot_views_provider=lambda: views,
        loaded_models_provider=lambda: _normalize_loaded_models(request),
    )
    # All 3 canonical names are advertised whenever they resolve. hal0/npu and
    # hal0/utility fall back to the primary when no npu/utility slot is loaded —
    # intentional: the name always routes (see resolve_chain's fallback contract).
    for vname in DEFAULT_CHAINS:  # canonical names only (aliases excluded from the picker)
        if vname in seen:
            continue
        res = await resolver.resolve(vname)
        if res is None or not res.model_id:
            continue
        seen.add(vname)
        device = next((v.device for v in views if v.model_id == res.model_id), "")
        data.append(
            {
                "id": vname,
                "object": "model",
                "created": now,
                "owned_by": "hal0",
                "context_length": res.context_length,
                "_hal0": {
                    "virtual": True,
                    "kind": "live-resolve",
                    "resolves_to": res.model_id,
                    "device": device,
                },
            }
        )

    return {"object": "list", "data": data}


@public_router.get("/models/{model_id:path}")
async def get_model(
    model_id: str,
    request: Request,
    dispatcher: DispatcherDep,
) -> dict[str, object]:
    """Look up a single model by id from the aggregated catalog.

    PUBLIC — the catalog is non-sensitive (just model ids the upstreams
    advertise on their own ``/v1/models``). Pairs with ``list_models``
    so SDKs that resolve a model handle through ``/v1/models/{id}``
    before chatting don't need credentials to do so.
    """
    listing = await list_models(request, dispatcher)
    for entry in listing.get("data", []):  # type: ignore[union-attr]
        if isinstance(entry, dict) and entry.get("id") == model_id:
            return entry
    from hal0.dispatcher.router import NoRouteFound

    raise NoRouteFound(
        f"model {model_id!r} is not advertised by any configured upstream",
        details={"model": model_id},
    )


@router.post("/chat/completions")
async def chat_completions(request: Request, dispatcher: DispatcherDep) -> Response:
    # PR-16: OmniRouter opt-in. When the body carries ``"omni": true``
    # AND we can resolve the request to a known chat slot whose model
    # advertises ``tool-calling``, route through the client-side
    # tool-calling loop instead of doing a direct passthrough. Plan §7
    # + ADR-0008 §8.
    #
    # The opt-in mechanism is a body field (vs query param) because:
    #   1. ``_dispatch_and_forward`` already parses the body, so we
    #      pay no extra read cost.
    #   2. Clients sending the OpenAI-shape body already carry their
    #      knobs in JSON; ``"omni": true`` is the same shape.
    #   3. Stripping the field before forwarding is one line in the
    #      OmniRouter (see ``_strip_omni``).
    #
    # When OmniRouter is unavailable (no slot_manager, no lemonade
    # client, or the request doesn't match a chat slot we own) we
    # fall back to the standard dispatch path.
    body = await _read_json_body(request)
    # Translate a chat-slot alias → model id up front so the OmniRouter
    # caller-slot match (keyed on the model id) and the dispatch path both
    # see the real model name. Also rewrites the cached request body for
    # the lemonade fall-through.
    body = await _rewrite_chat_slot_alias(request, body)
    # SOLE normalization gate (chat only). Resolves hal0/* virtual names +
    # injects the thinking policy, and rewrites request._body. Placed BEFORE
    # the omni branch so the OmniRouter posts the normalized body; the
    # non-omni path then hands the already-normalized body=body into
    # _dispatch_and_forward (which sees request._body too via the proxy
    # fall-through). Deliberately NOT in _dispatch_and_forward — that helper
    # also serves /v1/completions, /v1/embeddings, /v1/rerankings, and the
    # multipart /v1/audio/transcriptions, none of which are chat and where an
    # unconditional request._body=json(body) rewrite would corrupt the
    # multipart upload / inject a meaningless enable_thinking.
    body = await _normalize_chat_body(request, body)
    if body.get("omni") is True:
        looped = await _maybe_run_omni_loop(request, body)
        if looped is not None:
            return looped
    # Strip the knob before forwarding so the upstream never sees it
    # — Lemonade would reject the unknown field on strict-mode
    # backends.
    if "omni" in body:
        body = {k: v for k, v in body.items() if k != "omni"}
        import contextlib

        with contextlib.suppress(Exception):
            request._body = json.dumps(body).encode("utf-8")  # type: ignore[attr-defined]
    return await _dispatch_and_forward(request, dispatcher, body=body)


async def _is_npu_trio_request(
    request: Request,
    body: dict[str, Any],
    *,
    slot_type: str,
) -> bool:
    """Detect whether this request should go through the FLM trio router.

    PR-19 (plan §5.2 + ADR-0009). We route to the trio when:

      1. The request body's ``model`` matches an enabled slot whose
         ``device == "npu"`` AND ``type == slot_type``. We look at both
         ``slot.model.default`` AND ``slot.name`` so callers that pass
         either the model id or the slot name (e.g. dashboard cards
         using ``model="stt-npu"``) hit the same path.
      2. The :class:`FLMTrioRouter` itself is attached on ``app.state``
         (lifespan didn't fail to construct it).

    Returning ``False`` means we fall through to the regular dispatcher
    path — which, in the NPU-not-enabled case, is exactly the right
    fallback: Lemonade routes to a GPU/CPU embed/stt slot if one exists,
    else 404s.

    Note: we deliberately DON'T check whether the FLM chat is currently
    loaded here. That probe lives inside the trio router (it's an
    extra ``/v1/health`` call we don't want to spend on the gating
    check). When the chat isn't loaded the dispatch raises
    :class:`FLMTrioNotAvailable`, which surfaces as a clean 503 with the
    "load an NPU chat slot first" envelope — better UX than silently
    falling through to a 404 from the wrong path.
    """
    if getattr(request.app.state, "flm_trio_router", None) is None:
        return False
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is None:
        return False
    raw_model = body.get("model") if body else None
    if not isinstance(raw_model, str) or not raw_model.strip():
        return False
    requested = raw_model.strip()
    try:
        configs = await slot_manager.iter_configs()
    except Exception:
        return False
    for cfg in configs:
        if cfg.get("type") != slot_type:
            continue
        if cfg.get("device") != "npu":
            continue
        if cfg.get("enabled") is False:
            continue
        # Match by model.default OR by slot name — callers may pass
        # either through depending on UI affordance.
        model_section = cfg.get("model") or {}
        default = ""
        if isinstance(model_section, dict):
            raw_default = model_section.get("default", "")
            if isinstance(raw_default, str):
                default = raw_default.strip()
        slot_name = str(cfg.get("name", "")).strip()
        if requested in (default, slot_name) and (default or slot_name):
            return True
    return False


async def _dispatch_via_flm_trio(
    request: Request,
    *,
    body: dict[str, Any],
    kind: str,
) -> Response | None:
    """Forward an embed/STT request through the FLM trio router.

    Returns a FastAPI :class:`Response` on success / surfaced FLM error,
    or ``None`` when the trio router is not present (caller falls
    through). :class:`FLMTrioNotAvailable` propagates out so the error
    middleware emits the proper 503 envelope; HTTP errors from the FLM
    child are mirrored into the response verbatim.

    Only the embed path is routed through here — STT is multipart and
    handled inside :func:`_forward_multipart` to keep the
    bytes-buffer-then-forward seam in one place.
    """
    if kind != "embed":  # defensive — only one caller today
        return None
    router_obj = getattr(request.app.state, "flm_trio_router", None)
    if router_obj is None:
        return None
    upstream_resp = await router_obj.dispatch_embed_npu(body=body)
    return _wrap_flm_trio_response(upstream_resp)


def _wrap_flm_trio_response(upstream: Any) -> Response:
    """Build a FastAPI :class:`Response` from an httpx response.

    Mirrors what :class:`Dispatcher._forward_direct` does — strips
    hop-by-hop headers, preserves the upstream status code, and copies
    the content-type so OpenAI clients see the same shape they would
    have if Lemonade had handled the call. Streaming responses aren't
    supported (FLM's transcribe + embed endpoints are non-streaming).
    """
    # Drop hop-by-hop / length headers; Starlette recomputes content-length.
    skip = {
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
    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in skip}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


async def _maybe_run_omni_loop(request: Request, body: dict[str, Any]) -> Response | None:
    """Attempt to run the OmniRouter loop for this chat request.

    Returns:
        A FastAPI ``Response`` when the loop ran (success or surfaced
        error), or ``None`` if we can't route through the loop (no
        OmniRouter, unknown caller slot, etc.) — the caller then
        falls back to the standard dispatch path.

    PR-16 scope. Streaming responses are deferred to PR-18; this path
    always returns a non-streaming JSON Response.
    """
    omni = getattr(request.app.state, "omni_router", None)
    if omni is None:
        return None
    # Resolve the caller slot. We look up by the request's ``model``
    # field against configured slots' ``model.default``. The first
    # matching enabled slot wins.
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is None:
        return None
    requested_model = body.get("model")
    if not isinstance(requested_model, str) or not requested_model:
        return None
    configs = await slot_manager.iter_configs()
    caller_slot_name: str | None = None
    for cfg in configs:
        if cfg.get("type") != "llm":
            continue
        if not cfg.get("enabled", True):
            continue
        model_section = cfg.get("model") or {}
        if isinstance(model_section, dict):
            default = model_section.get("default", "")
            if isinstance(default, str) and default == requested_model:
                caller_slot_name = str(cfg.get("name", ""))
                break
    if caller_slot_name is None:
        return None
    result = await omni.run_loop(caller_slot_name=caller_slot_name, body=body)
    return Response(
        content=json.dumps(result).encode("utf-8"),
        status_code=200,
        media_type="application/json",
    )


@router.post("/completions")
async def completions(request: Request, dispatcher: DispatcherDep) -> Response:
    return await _dispatch_and_forward(request, dispatcher)


@router.post("/embeddings")
async def embeddings(request: Request, dispatcher: DispatcherDep) -> Response:
    # PR-19: FLM trio direct-port dispatch for the ``embed-npu`` slot.
    # When a request resolves to an enabled NPU embedding slot AND the
    # FLM chat anchor is loaded, post straight to the FLM child's
    # ``/v1/embeddings`` instead of Lemonade (which doesn't register
    # the embed shadow role — only the chat anchor). Plan §5.2.
    body = await _read_json_body(request)
    if await _is_npu_trio_request(request, body, slot_type="embedding"):
        trio_response = await _dispatch_via_flm_trio(request, body=body, kind="embed")
        if trio_response is not None:
            return trio_response
    return await _dispatch_and_forward(request, dispatcher, body=body)


@router.post("/rerankings")
async def rerankings(request: Request, dispatcher: DispatcherDep) -> Response:
    return await _dispatch_and_forward(request, dispatcher)


@router.post("/audio/transcriptions")
async def audio_transcriptions(request: Request, dispatcher: DispatcherDep) -> Response:
    # Multipart upload — extract the model field to route, then forward the
    # raw multipart bytes unchanged so the upstream's own multipart parser
    # works. JSON re-encoding (the default dispatch path) would corrupt the
    # WAV payload.
    #
    # Per OpenAI's contract the ``model`` form field is required. We surface
    # the missing-model case as 400 (request.missing_model) rather than
    # letting it fall through to the dispatcher's default-model + no-route
    # 404, which obscured the real problem (issue #34).
    #
    # PR-19: FLM trio direct-port dispatch for the ``stt-npu`` slot.
    # When the request targets an enabled NPU transcription slot AND the
    # FLM chat anchor is loaded, post the raw multipart bytes straight
    # to the FLM child's ``/v1/audio/transcriptions``. We do the gating
    # inside ``_forward_multipart`` because (a) we need the model field
    # parsed from the multipart envelope to decide, and (b) the multipart
    # bytes-buffer-then-forward pattern is the same either way — only
    # the destination URL differs.
    response = await _forward_multipart(request, dispatcher, require_model=True)
    return _scrub_audio_decoder_leakage(response)


@router.post("/audio/speech")
async def audio_speech(request: Request, dispatcher: DispatcherDep) -> Response:
    # /v1/audio/speech is the TTS input direction — body is JSON
    # ({"model": "...", "input": "...", "voice": "..."}). Standard path,
    # but the ``model`` field is required by the OpenAI contract; raise
    # 400 explicitly so the caller doesn't see a misleading 404 from the
    # dispatcher's default-model fallback path (issue #34 / harness #18).
    from hal0.errors import BadRequest

    body = await _read_json_body(request)
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise BadRequest(
            "missing required field 'model'",
            details={"field": "model", "path": "/v1/audio/speech"},
            code="request.missing_model",
        )
    return await _dispatch_and_forward(request, dispatcher, body=body)


# ── /v1/images/generations (ComfyUI provider, hal0-managed translation) ────


def _extract_port_from_upstream_url(url: str) -> int | None:
    """Pull the port out of an Upstream.url like ``http://127.0.0.1:8186/v1``.

    Returns None when the URL is malformed or doesn't carry an explicit
    port (remote upstreams could set this to host:443; we only support
    image gen on a local slot, so a missing port is a configuration error
    surfaced upstream as a 502).
    """
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return None
    return parsed.port


@router.post("/images/generations")
async def images_generations(request: Request, dispatcher: DispatcherDep) -> Response:
    """OpenAI-compatible image generation.

    Body shape (subset honoured)::

        {
          "model":           "sdxl-turbo",        # required; curated id
          "prompt":          "a cat in a hat",    # required
          "n":               1,                   # optional, batch_size
          "size":            "1024x1024",         # optional, WxH
          "response_format": "url" | "b64_json"   # optional, default "url"
        }

    Optional ``extra_body`` (hal0 extension): ``seed``, ``steps``,
    ``cfg``, ``negative_prompt``.

    Returns the OpenAI shape::

        {
          "created": 1716000000,
          "data": [
            {"url": "/api/images/cache/<uuid>.png"}     // response_format=url
            // OR
            {"b64_json": "<base64-encoded PNG>"}        // response_format=b64_json
          ],
          "_hal0": {... debug meta from the workflow translator ...}
        }
    """
    from hal0.dispatcher.router import NoRouteFound, UpstreamUnavailable
    from hal0.errors import Hal0Error
    from hal0.providers import get_provider
    from hal0.registry.curated import get_curated

    class _ImagePromptRequired(Hal0Error):
        code = "image.prompt_required"
        status = 422

    class _ImageModelNotCurated(Hal0Error):
        code = "image.model_not_curated"
        status = 404

    body = await _read_json_body(request)
    if not body.get("prompt"):
        raise _ImagePromptRequired("body.prompt is required")

    # 1. Resolve the slot the request should land on. We dispatch to get
    #    the port + slot name; the dispatcher's heuristics already route
    #    /v1/images/* to the `img` slot via the legacy fallback rule.
    call = await dispatcher.dispatch(request, body=body)

    # 2. Find the curated metadata for the requested model id so we know
    #    which workflow template + checkpoint filename to pin into the
    #    workflow.
    requested = (body.get("model") or "").strip() or "sdxl-turbo"
    curated = get_curated(requested)
    if curated is None or curated.capability != "image":
        raise _ImageModelNotCurated(
            f"model {requested!r} is not in the curated image-gen catalogue; "
            "current built-ins: sdxl-turbo, sd-1.5-pruned-emaonly",
            details={"model": requested},
        )

    # 3. Discover the local slot's port from the resolved upstream.
    upstream = request.app.state.upstreams.get(call.upstream_name)
    if upstream is None:
        raise NoRouteFound(
            f"image-gen dispatch landed on upstream {call.upstream_name!r} which is not registered",
            details={"upstream": call.upstream_name},
        )
    port = _extract_port_from_upstream_url(upstream.url)
    if port is None:
        raise UpstreamUnavailable(
            f"image-gen upstream {call.upstream_name!r} has no parseable port "
            f"in url={upstream.url!r}",
            details={"upstream": call.upstream_name, "url": upstream.url},
        )

    # 4. Drive the ComfyUI provider directly. Inject the curated metadata
    #    into the body so the provider's translator knows what to render
    #    without re-looking-up the registry.
    provider = get_provider("comfyui")
    body_with_meta = {
        **body,
        "_hal0_model_class": curated.model_class or "sdxl-turbo",
        "_hal0_ckpt_filename": curated.hf_file,
    }
    result = await provider.infer(port, body_with_meta)

    # Track most-recent-model for dashboard.
    last_used = getattr(request.app.state, "last_used_model", None)
    if last_used is not None and call.upstream_name:
        last_used[call.upstream_name] = requested

    # 5. Emit OpenAI-shaped response.
    response_format = (body.get("response_format") or "url").lower().strip()
    images: list[dict[str, Any]] = []
    for img in result.get("images", []):
        png = img.get("png", b"")
        if not isinstance(png, (bytes, bytearray)) or not png:
            continue
        if response_format == "b64_json":
            images.append({"b64_json": base64.b64encode(bytes(png)).decode("ascii")})
        else:
            stem = image_cache.write_png(bytes(png))
            images.append({"url": f"/api/images/cache/{stem}.png"})

    payload = {
        "created": int(time.time()),
        "data": images,
        "_hal0": {
            "meta": result.get("meta", {}),
            "prompt_id": result.get("prompt_id", ""),
            "upstream": call.upstream_name,
            "model": requested,
        },
    }
    return Response(
        content=json.dumps(payload).encode("utf-8"),
        media_type="application/json",
    )


# Sentinel substrings whose presence in an upstream error body signals
# that the audio decoder (ffmpeg) leaked its argv or CalledProcessError
# repr through. Older / out-of-tree moonshine builds didn't redact this
# before returning a 5xx, so the proxy must scrub defensively. Issue #14
# (tests/harness/FINDINGS.md §14) — the hal0 envelope contract forbids
# echoing subprocess argv or tempfile paths to clients.
_AUDIO_DECODER_LEAK_MARKERS = (b"CalledProcessError", b"ffmpeg", b"FFmpeg", b"FFMPEG")


def _scrub_audio_decoder_leakage(response: Response) -> Response:
    """Replace a leaky upstream STT error with a clean hal0 415 envelope.

    The moonshine container in this repo already converts ffmpeg decode
    failures to a 415 with the ``audio.unsupported_format`` envelope and
    no ``ffmpeg`` substring (see ``packaging/toolbox/moonshine/moonshine_server.py``).
    But the proxy can't assume every reachable upstream is on that build
    — older deployed images, third-party STT containers, or operator-side
    decoders may still surface a ``CalledProcessError`` repr that includes
    the subprocess argv and the user-supplied tempfile path. When we see
    those markers, swap the response for a synthetic 415 carrying the hal0
    envelope shape so callers never see implementation detail.

    Only inspects non-streaming responses with a readable ``body`` attr;
    StreamingResponse passes through untouched (STT responses aren't
    streamed today, but if a future upstream does, the scrub is a no-op
    rather than a body-drain).
    """
    if isinstance(response, StreamingResponse):
        return response
    body = getattr(response, "body", None)
    if not isinstance(body, (bytes, bytearray)) or not body:
        return response
    # The upstream's bad path is by definition a non-2xx; ignore 2xx bodies
    # that happen to mention ffmpeg in a metadata field.
    if 200 <= response.status_code < 300:
        return response
    if not any(marker in body for marker in _AUDIO_DECODER_LEAK_MARKERS):
        return response

    envelope = {
        "error": {
            "code": "audio.unsupported_format",
            "message": "unsupported audio format; expected wav/mp3/flac/ogg/m4a/webm",
            "details": {"upstream_status": response.status_code},
        }
    }
    return Response(
        content=json.dumps(envelope).encode("utf-8"),
        status_code=415,
        media_type="application/json",
    )


_MODEL_FIELD_RE = re.compile(
    rb'Content-Disposition:\s*form-data;\s*name="model"\s*\r\n\r\n([^\r\n]+)',
    re.IGNORECASE,
)


def _extract_multipart_model(raw_body: bytes) -> str:
    """Pull the ``model`` form field out of a multipart body.

    Multipart bodies hold each field as a part with a Content-Disposition
    header naming it; for the ``model`` field the value is a short ASCII
    string immediately following the header's blank line. A regex match
    avoids the full streaming parser starlette ships (which would re-read
    request.stream() — empty after request.body() consumes it).
    """
    m = _MODEL_FIELD_RE.search(raw_body or b"")
    if not m:
        return ""
    try:
        return m.group(1).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


async def _forward_multipart(
    request: Request,
    dispatcher: DispatcherDep,
    *,
    require_model: bool = False,
) -> Response:
    """Route a multipart request without re-serialising its body.

    The dispatcher's normal _remap_model path JSON-encodes the body, which
    corrupts multipart payloads (WAV files etc.). We:

    1. Buffer the raw inbound bytes.
    2. Extract the ``model`` form field with a single regex over the bytes —
       starlette's request.form() reads from request.stream() which is
       empty after request.body() has already consumed the body.
    3. Hand the dispatcher a fake-body dict carrying only ``{"model": ...}``
       so its route resolution still works.
    4. After dispatch picks an upstream, overwrite call.body with the
       original raw bytes + content-type header so httpx forwards verbatim.

    When ``require_model`` is True, missing ``model`` raises a 400 BadRequest
    instead of falling through to the dispatcher's default-model path
    (issue #34 — surfaces a useful error to OpenAI clients that forgot it).
    """
    import httpx

    from hal0.errors import BadRequest

    raw_body = await request.body()
    headers = dict(request.headers)
    content_type = headers.get("content-type") or "multipart/form-data"
    model_value = _extract_multipart_model(raw_body)

    if require_model and not model_value:
        raise BadRequest(
            "missing required field 'model'",
            details={"field": "model", "path": request.url.path},
            code="request.missing_model",
        )

    # PR-19: FLM trio direct-port dispatch for the ``stt-npu`` slot.
    # We do the gating here (rather than in the route handler) because
    # the model field only becomes available after the multipart parse
    # above. When the request targets an enabled NPU transcription slot,
    # forward the raw multipart bytes directly to the FLM child's
    # ``/v1/audio/transcriptions`` — Lemonade has no transcription
    # model registered for the FLM chat anchor, so the standard
    # dispatch path would 404. Plan §5.2.
    if model_value and request.url.path.endswith("/audio/transcriptions"):
        synthetic_body = {"model": model_value}
        if await _is_npu_trio_request(request, synthetic_body, slot_type="transcription"):
            router_obj = getattr(request.app.state, "flm_trio_router", None)
            if router_obj is not None:
                upstream_resp = await router_obj.dispatch_stt_npu(
                    body=raw_body,
                    content_type=content_type,
                )
                return _wrap_flm_trio_response(upstream_resp)

    call = await dispatcher.dispatch(request, body={"model": model_value} if model_value else {})

    last_used = getattr(request.app.state, "last_used_model", None)
    if last_used is not None and call.upstream_name and call.resolved_model:
        last_used[call.upstream_name] = call.resolved_model

    # Replace the dispatcher's JSON-encoded body with the raw multipart bytes.
    call.body = raw_body
    call.headers = {**call.headers, "content-type": content_type}

    # Reuse the dispatcher's existing forward path.
    try:
        return await dispatcher.forward(call)
    except httpx.HTTPError as exc:
        from hal0.dispatcher.router import UpstreamUnavailable

        raise UpstreamUnavailable(
            f"upstream {call.upstream_name!r} multipart forward failed: {exc}",
            details={"upstream": call.upstream_name, "error": str(exc)},
        ) from exc
