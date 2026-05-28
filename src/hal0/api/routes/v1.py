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

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from hal0.api import image_cache
from hal0.api.deps import DispatcherDep

# Inference router — auth-required. Mounted by hal0.api.create_app() with
# Depends(require_token) so /v1/chat/completions, /v1/embeddings, the
# audio + image endpoints, etc. all gate on a valid credential.
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


async def _dispatch_and_forward(
    request: Request,
    dispatcher: DispatcherDep,
    body: dict[str, Any] | None = None,
) -> Response:
    if body is None:
        body = await _read_json_body(request)
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

    PUBLIC — mounted on ``public_router`` so OpenAI SDKs that probe the
    catalog before sending Authorization headers continue to work after
    ADR-0001 Child B.
    """
    upstreams = request.app.state.upstreams
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {}) or {}
    seen: set[str] = set()
    data: list[dict[str, Any]] = []
    now = int(time.time())
    for u in upstreams.list():
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
            seen.add(mid)
            data.append(
                {
                    "id": mid,
                    "object": "model",
                    "created": now,
                    "owned_by": u.name,
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
    if body.get("omni") is True:
        looped = await _maybe_run_omni_loop(request, body)
        if looped is not None:
            return looped
    # Strip the knob before forwarding so the upstream never sees it
    # — Lemonade would reject the unknown field on strict-mode
    # backends.
    if "omni" in body:
        body = {k: v for k, v in body.items() if k != "omni"}
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
            "current built-ins: sdxl-turbo, sd-1.5-pruned-emaonly, flux-schnell",
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
