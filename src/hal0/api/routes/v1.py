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


def _instrument_streaming_throughput(
    response: StreamingResponse, app_state: Any
) -> StreamingResponse:
    """Wrap a streaming response body iterator with a token counter.

    Increments ``app_state.tps_events`` with one (monotonic, tokens)
    entry per chunk. Token count per chunk is approximated by counting
    ``"delta":`` occurrences in the raw SSE bytes — close enough for a
    throughput indicator and far cheaper than a full SSE parse.
    """
    original = response.body_iterator
    events = getattr(app_state, "tps_events", None)
    if events is None:
        return response

    async def _counting() -> Any:
        async for chunk in original:
            if isinstance(chunk, (bytes, bytearray)):
                tokens = chunk.count(b'"delta":')
                if tokens > 0:
                    events.append((time.monotonic(), tokens))
            elif isinstance(chunk, str):
                tokens = chunk.count('"delta":')
                if tokens > 0:
                    events.append((time.monotonic(), tokens))
            yield chunk

    response.body_iterator = _counting()
    return response


def _record_nonstreaming_throughput(body_bytes: bytes, app_state: Any) -> None:
    """Pull ``usage.completion_tokens`` + a recent timestamp out of a JSON
    response body so non-streaming chats also move the throughput tile."""
    events = getattr(app_state, "tps_events", None)
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
    call = await dispatcher.dispatch(request, body=body)
    # Remember the most recent model we sent to this upstream so the
    # dashboard's synthetic slot reflects what's actually being used,
    # not the first-non-alias from the catalog.
    last_used = getattr(request.app.state, "last_used_model", None)
    if last_used is not None and call.upstream_name and call.resolved_model:
        last_used[call.upstream_name] = call.resolved_model

    response = await dispatcher.forward(call)
    if isinstance(response, StreamingResponse):
        return _instrument_streaming_throughput(response, request.app.state)
    if isinstance(response, Response) and getattr(response, "body", None):
        _record_nonstreaming_throughput(response.body, request.app.state)
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
    seen: set[str] = set()
    data: list[dict[str, Any]] = []
    now = int(time.time())
    for u in upstreams.list():
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
    return await _dispatch_and_forward(request, dispatcher)


@router.post("/completions")
async def completions(request: Request, dispatcher: DispatcherDep) -> Response:
    return await _dispatch_and_forward(request, dispatcher)


@router.post("/embeddings")
async def embeddings(request: Request, dispatcher: DispatcherDep) -> Response:
    return await _dispatch_and_forward(request, dispatcher)


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
    # the missing-model case as 400 (validation.invalid) rather than letting
    # it fall through to the dispatcher's default-model + no-route 404,
    # which obscured the real problem (issue #34).
    return await _forward_multipart(request, dispatcher, require_model=True)


@router.post("/audio/speech")
async def audio_speech(request: Request, dispatcher: DispatcherDep) -> Response:
    # /v1/audio/speech is the TTS input direction — body is JSON
    # ({"model": "...", "input": "...", "voice": "..."}). Standard path,
    # but the ``model`` field is required by the OpenAI contract; raise
    # 400 explicitly so the caller doesn't see a misleading 404 from the
    # dispatcher's default-model fallback path (issue #34).
    from hal0.errors import BadRequest

    body = await _read_json_body(request)
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise BadRequest(
            "Request body field 'model' is required",
            details={"field": "model", "path": "/v1/audio/speech"},
            code="validation.invalid",
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
            "Request body field 'model' is required",
            details={"field": "model", "path": request.url.path},
            code="validation.invalid",
        )

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
