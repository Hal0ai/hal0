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

import asyncio
import base64
import contextlib
import json
import re
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from hal0.api import image_cache, live_sockets
from hal0.api.deps import DispatcherDep
from hal0.dispatcher.lane_pin import lane_slot_pin, preferred_slot, set_lane_pin, set_upstream_only
from hal0.dispatcher.stream_usage import ensure_include_usage, strip_usage_only_chunk
from hal0.memory.extraction_env import MEMORY_ALIAS
from hal0.upstreams.filters import apply_filters

if TYPE_CHECKING:
    from hal0.comfyui.resolve import ResolvedImageModel

log = structlog.get_logger("hal0-v1")

# Inference router. Mounted by hal0.api.create_app() on /v1.
# Auth was removed; the server is open on the local network.
router = APIRouter()

# Public probe router — auth-free. Mounted on the same /v1 prefix.
# OpenAI clients (OpenWebUI, third-party SDKs) GET /v1/models before
# they have anywhere to send an Authorization header; gating it would
# break the "drop in an OpenAI-shaped base_url" UX. Publicness is
# declared by NOT attaching an auth dep — not by allowlisting the path
# in a frozenset.
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


def _slot_spec_events(app_state: Any, slot_name: str | None) -> Any:
    """Per-slot spec_events deque of ``(ts, draft_n, accepted_n)`` (or None)."""
    events_store = getattr(app_state, "spec_events", None)
    if events_store is None or not slot_name:
        return None
    return events_store[slot_name]


def _record_spec_timings(events: Any, drafted: Any, accepted: Any) -> None:
    """Append one completion's draft counters; no draft, nothing recorded."""
    if events is None or not isinstance(drafted, int) or drafted <= 0:
        return
    events.append((time.monotonic(), drafted, accepted if isinstance(accepted, int) else 0))


_DRAFT_N_RE = re.compile(rb'"draft_n"\s*:\s*(\d+)')
_DRAFT_ACCEPTED_RE = re.compile(rb'"draft_n_accepted"\s*:\s*(\d+)')


def _update_slot_throughput(app_state: Any, slot_name: str | None, tps: float) -> None:
    """Record a FLM decoding-speed sample (tok/s) for the slot.

    Stored on ``app_state.slot_throughput`` keyed by slot name so
    ``/api/slots/metrics`` surfaces it in the tile throughput gauge.
    """
    store = getattr(app_state, "slot_throughput", None)
    if store is None or not slot_name:
        return
    store[slot_name] = tps


def _update_slot_kv_occupancy(app_state: Any, slot_name: str | None, pct: float) -> None:
    """Record a FLM KV-column occupancy sample (0-100%) for the slot.

    Stored on ``app_state.slot_kv_occupancy`` per slot so the dashboard
    NPU gauge reads the latest column utilisation.
    """
    store = getattr(app_state, "slot_kv_occupancy", None)
    if store is None or not slot_name:
        return
    store[slot_name] = pct


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
    spec_events = _slot_spec_events(app_state, slot_name)
    if events is None and ttft_events is None and spec_events is None:
        return response
    ttft_pending = ttft_events is not None  # one-shot per response

    async def _counting() -> Any:
        nonlocal ttft_pending
        async for chunk in original:
            # The dispatcher's stall-guard terminal frame (#1893) is synthetic
            # — it carries a "delta": but no generated token ever crossed the
            # wire.  It is always yielded as its own chunk, so a per-chunk
            # marker check keeps it out of throughput and (crucially) TTFT:
            # for a silent upstream it would otherwise record a bogus
            # ~idle-timeout-sized TTFT sample.
            if isinstance(chunk, (bytes, bytearray)):
                tokens = 0 if b'"x_hal0_stall"' in chunk else chunk.count(b'"delta":')
            elif isinstance(chunk, str):
                tokens = 0 if '"x_hal0_stall"' in chunk else chunk.count('"delta":')
            else:
                tokens = 0
            if spec_events is not None and isinstance(chunk, (bytes, bytearray)):
                # llama-server puts draft_n / draft_n_accepted in the final
                # chunk's timings, and only when a draft happened.
                m = _DRAFT_N_RE.search(chunk)
                if m is not None:
                    acc = _DRAFT_ACCEPTED_RE.search(chunk)
                    _record_spec_timings(
                        spec_events, int(m.group(1)), int(acc.group(1)) if acc else 0
                    )
            if tokens > 0:
                now = time.monotonic()
                if events is not None:
                    events.append((now, tokens))
                if ttft_pending and ttft_events is not None and dispatch_started is not None:
                    ttft_events.append((now, max(0.0, now - dispatch_started)))
                    ttft_pending = False
                # Keep the last-used stamp moving through the whole stream —
                # only non-streaming responses stamped it before, so a long
                # streamed chat never registered as "active" on the NPU card.
                _stamp_slot_last_used(app_state, slot_name)
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
    # Increment per-slot request counter (shadow slots share with parent)
    req_store = getattr(app_state, "slot_request_count", None)
    if req_store is not None and slot_name:
        req_store[slot_name] = req_store.get(slot_name, 0) + 1
        # Also record last-use timestamp for recency-based animation
        last_used = getattr(app_state, "slot_last_used", None)
        if last_used is not None:
            last_used[slot_name] = time.monotonic()
    try:
        data = json.loads(body_bytes)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    timings = data.get("timings")
    if isinstance(timings, dict):
        _record_spec_timings(
            _slot_spec_events(app_state, slot_name),
            timings.get("draft_n"),
            timings.get("draft_n_accepted"),
        )
    usage = data.get("usage") or {}
    completion = usage.get("completion_tokens") or 0
    if not isinstance(completion, (int, float)) or completion <= 0:
        return
    # Without a real start time, attribute the whole completion to "now"
    # — the rolling window will smear it across the lookback. Better
    # alternatives need start-time tracking through forward().
    events.append((time.monotonic(), int(completion)))

    # FLM (NPU) metrics: extract decoding speed + KV column occupancy from
    # the ``usage`` block so the dashboard can show NPU tok/s and column
    # usage gauge. These fields are FLM-specific; llama.cpp responses
    # don't carry them.
    flm_tps = usage.get("decoding_speed_tps")
    flm_kv = usage.get("kv_token_occupancy_rate_percentage")
    if isinstance(flm_tps, (int, float)):
        _update_slot_throughput(app_state, slot_name, float(flm_tps))
    if isinstance(flm_kv, (int, float)):
        _update_slot_kv_occupancy(app_state, slot_name, float(flm_kv))


def _stamp_slot_last_used(app_state: Any, slot_name: str | None) -> None:
    """Record a monotonic last-use timestamp for *slot_name* (no counter).

    Distinct from :func:`_touch_npu_shadow_count` so callers can mark a
    request as in-flight at dispatch start (the NPU card's per-capability
    activity tint) without double-incrementing the request counter.
    """
    if not slot_name:
        return
    last_used = getattr(app_state, "slot_last_used", None)
    if last_used is not None:
        last_used[slot_name] = time.monotonic()


def _touch_npu_shadow_count(request: Request, slot_name: str) -> None:
    """Increment the per-slot request counter and record a last-use timestamp.

    Called after STT/embed dispatch via the NPU trio router so the
    flm-stt / flm-embed cards show activity even though they don't have
    their own server process.  The timestamp drives which column squares
    get animated (most recent capability wins the glow).
    """
    import time

    store = getattr(request.app.state, "slot_request_count", None)
    last_used = getattr(request.app.state, "slot_last_used", None)
    if store is not None:
        store[slot_name] = store.get(slot_name, 0) + 1
    if last_used is not None:
        last_used[slot_name] = time.monotonic()


_TRUTHY_PARAMS = frozenset({"1", "true", "yes", "on"})

# §21.5: the raw-catalog modality buckets hidden from GET /v1/models by
# default — media-generation + audio models an OpenAI *chat* client never
# wants in its model picker. Text models (chat + embed + rerank) stay
# visible so a client enumerating /v1/models can still discover the
# embeddings/rerank endpoints. Vocabulary is ``hal0.model_meta.classify``'s
# bucket names: ``img`` (image/video-gen), ``tts`` (speech synth), ``stt``
# (ASR/transcription). ``?show_all=true`` returns everything regardless.
_NON_TEXT_MODALITIES = frozenset({"img", "tts", "stt"})


def _parse_bool_param(raw: str | None) -> bool:
    """Parse a query-param string as a bool; missing/unrecognised → False.

    Mirrors the plain ``request.query_params.get(...)`` style already used
    for ``owned_by`` in :func:`list_models` rather than a typed FastAPI
    ``Query(bool)`` — keeps ``show_all`` curl-debuggable with any of
    ``1``/``true``/``yes``/``on`` (case-insensitive), same as the other
    ad-hoc bool toggles across this router.
    """
    return bool(raw) and raw.strip().lower() in _TRUTHY_PARAMS


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


def _memory_alias_target(request: Request) -> tuple[str, str]:
    """What ``hal0/memory`` resolves to right now (#2919).

    Reads ``[memory.graph]`` off ``app.state.hal0_config`` — the cache every
    ``hal0_config_txn.save`` refreshes — so a PUT on the graph route applies on
    the engine's very next call, no restart. A bare app without the lifespan
    (router-only tests) behaves like the schema default.
    """
    from hal0.config.schema import MemoryGraphConfig
    from hal0.memory.extraction_env import memory_engine_target

    cfg = getattr(request.app.state, "hal0_config", None)
    graph = getattr(getattr(cfg, "memory", None), "graph", None)
    if graph is None:
        graph = MemoryGraphConfig()
    return memory_engine_target(graph)


async def _rewrite_chat_slot_alias(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Translate a chat-slot ALIAS in ``body["model"]`` to its model id.

    hermes-role-slots: a request may address a co-resident llm slot by
    its **alias** (the slot name — ADR-0023 canonical: ``agent`` / ``utility``,
    plus any other enabled llm slot by name) instead of the underlying model
    id. We rewrite the alias to the
    slot's configured model id HERE, at the route layer, before the
    dispatcher routes it. After the rewrite, both ``model==alias`` and
    ``model==model_id`` carry the correct distinct model name down the
    existing path and hit the right co-resident model.

    The rewrite is applied to BOTH:
      * the returned ``body`` dict (handed to ``dispatcher.dispatch``), and
      * the request's cached body bytes (``request._body``) — so any
        downstream consumer that re-reads ``request.body()`` verbatim
        forwards the rewritten model name rather than the bare alias.

    FLM-backed slots get a second translation step: the alias resolves to the
    hal0 ``<tag>-FLM`` catalog id (``gemma4-it-e2b-FLM``), but ``flm serve``
    advertises only the native ``family:size`` tag (``gemma4-it:e2b``). Routing
    keys off the advertised set, so an un-translated ``-FLM`` id — reached via
    the ``npu`` alias OR requested directly by a consumer pinned to the catalog
    id — matches no upstream and 404s with ``dispatch.no_route``. We normalise
    it to the served tag here, mirroring ``SlotManager._resolve_model_info``.

    No-op when: the model isn't a known chat-slot alias and isn't a ``-FLM``
    id, already equals its served name, the slot manager is absent, or the
    config read raises (best-effort — never blocks the request).
    """
    raw_model = body.get("model")
    if not isinstance(raw_model, str) or not raw_model:
        return body

    # Step 1: alias → configured model id (co-resident chat slots). Skipped
    # when the slot manager is absent; a direct model id still flows on.
    target = raw_model
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is not None:
        from hal0.api import hal0_chat_slot_alias_map

        try:
            alias_to_model = await hal0_chat_slot_alias_map(slot_manager)
        except Exception:
            alias_to_model = {}
        target = alias_to_model.get(raw_model) or raw_model

    # Step 2: FLM catalog id → native served tag (gemma4-it-e2b-FLM →
    # gemma4-it:e2b). Independent of the slot manager — a directly-requested
    # -FLM id normalises too. Only fires on the ``-FLM`` suffix, so non-FLM
    # requests never pay the catalog probe.
    if target.endswith("-FLM"):
        try:
            from hal0.providers.flm import flm_id_to_tag_async
        except ImportError:
            flm_id_to_tag_async = None  # type: ignore[assignment]
        if flm_id_to_tag_async is not None:
            # A cold catalog is probed off the event loop (#2334).
            tag = await flm_id_to_tag_async(target)
            if tag:
                target = tag

    if target == raw_model:
        return body

    new_body = {**body, "model": target}
    # Overwrite the cached request body so downstream consumers that
    # re-read request.body() see the rewritten model name. If we can't
    # (unexpected request shape), still return the rewritten dict — the
    # dispatcher path benefits regardless.
    import contextlib

    with contextlib.suppress(Exception):
        request._body = json.dumps(new_body).encode("utf-8")  # type: ignore[attr-defined]
    return new_body


def _normalize_loaded_models(request: Request) -> set[str]:
    """Currently-loaded model ids (cached catalog reads; no live poll).

    Container-backed remotes advertise the models they serve, so their
    cached catalogs are the loaded set (cutover #662).
    """
    import contextlib

    loaded: set[str] = set()
    # Container-backed upstreams advertise their served model only while up, so
    # their cached catalog == "loaded". SlotManager registers local container
    # slots as kind="slot" (registry guards that kind as SlotManager-owned);
    # kind="remote" covers externally-configured container remotes. Filtering
    # on kind=="remote" alone starved the hal0/<slot> alias resolver of every
    # LOCAL slot's model, 404ing all virtual ids on real boxes (O21) — the
    # container-backed marker is ``slot_name``, so key on that.
    upstreams = getattr(request.app.state, "upstreams", None)
    model_cache = getattr(request.app.state, "upstream_models", None)
    if upstreams is not None and model_cache is not None:
        with contextlib.suppress(Exception):
            for up in upstreams.list():
                if getattr(up, "slot_name", None):
                    loaded |= set(model_cache.get(up.name, []))
    return loaded


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
            # Container-backed remotes carry a slot_name — they are LOCAL slots,
            # not genuine external providers, so the thinking policy must apply.
            if getattr(u, "slot_name", None):
                continue
            if getattr(u, "kind", "") != "remote":
                continue
            if model_id in set(cache.get(u.name, [])) or model_id in getattr(u, "owned_models", ()):
                return True
    except Exception:  # pragma: no cover — defensive
        return False
    return False


def _is_pinned_owned(request: Request, model_id: str) -> bool:
    """True if a ``pinned`` upstream lists ``model_id`` in its ``owned_models``.

    Mirrors ``Dispatcher._pinned_owner``: only ``owned_models`` confers
    ownership, never a models cache, so a hal0 peer or cloud remote that
    happens to advertise a slot alias (``agent``) cannot suppress the local
    alias rewrite and backend load.
    """
    upstreams = getattr(request.app.state, "upstreams", None)
    if upstreams is None:
        return False
    try:
        return any(
            getattr(u, "pinned", False) and model_id in getattr(u, "owned_models", ())
            for u in upstreams.list()
        )
    except Exception:  # pragma: no cover — defensive
        return False


async def _slot_thinking_default(
    request: Request, model_id: str, slot_name: str | None = None
) -> bool | None:
    """The reasoning default for a request: the serving slot's effective tune (ADR-0029).

    The slot is the lane the request pinned (``hal0/<slot>``), else the first
    llm slot bound to ``model_id`` (default slot first). Its profile's
    ``enable_thinking``, with the slot's ``[tune]`` override and any
    ``--reasoning`` flag pin applied (:func:`hal0.slots.tune.effective_tune`), decides. ``None`` means the operator expressed no
    preference (unset, no slot, or an unresolvable profile) and the policy's
    own default (off) applies; an explicit ``False`` is the operator's choice
    and is honoured even on grammar requests (#2713). Always overridable per
    request via top-level ``enable_thinking`` / ``chat_template_kwargs``.
    """
    from hal0.errors import Hal0Error
    from hal0.providers.container import resolve_slot_profile
    from hal0.slots.tune import effective_tune

    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return None
    try:
        cfgs = list(await sm.iter_configs())
    except Exception:
        return None
    llm = [c for c in cfgs if isinstance(c, dict) and str(c.get("type") or "llm") == "llm"]
    chosen = None
    if slot_name:
        chosen = next((c for c in llm if c.get("name") == slot_name), None)
    if chosen is None:
        bound = [c for c in llm if str((c.get("model") or {}).get("default") or "") == model_id]
        bound.sort(key=lambda c: c.get("default") is not True)
        chosen = bound[0] if bound else None
    if chosen is None:
        return None
    try:
        profile = resolve_slot_profile(chosen)
    except Hal0Error:
        return None
    # The slot's [tune] may override the profile; a `--reasoning` flag pin wins over both.
    value = effective_tune(chosen, profile).enable_thinking
    return value if isinstance(value, bool) else None


def _model_grammar_collides(request: Request, model_id: str) -> bool:
    """True when ``model_id``'s chat template is known to hard-400 when its
    ``enable_thinking=false`` branch meets a JSON grammar (#2020, #2713).

    The carrier is the registry row's ``chat_template`` fact (ADR-0029: a fact
    about the model file) — the same fact the launch path turns into
    ``--chat-template-file`` (``hal0.providers.container``), so it names the
    template the serving llama-server actually renders. A registry miss, an
    un-wired registry, or ``auto`` (the GGUF-embedded template) is not known to
    collide, and the request gets the normal default suppression.
    """
    from hal0.config.schema import resolve_chat_template
    from hal0.normalize.thinking import template_collides_with_grammar

    registry = getattr(request.app.state, "model_registry", None)
    if registry is None:
        return False
    try:
        entry = registry.get(model_id)
    except Exception:
        return False
    if isinstance(entry, Mapping):
        template = entry.get("chat_template")
    else:
        template = getattr(entry, "chat_template", None)
    if not isinstance(template, str):
        return False
    return template_collides_with_grammar(resolve_chat_template({"chat_template": template}))


async def _normalize_chat_body(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Normalise messages → resolve hal0/* aliases → inject thinking policy.

    Order of operations matters:

      1. **Canonicalise the messages array** — collapse every
         ``role='system'`` entry into one and hoist it to position 0
         (see :func:`hal0.normalize.messages.normalize_system_messages`).
         Doing this BEFORE the alias rewrite keeps every downstream
         consumer — the dispatcher, audit middleware, response
         introspection, the cached ``request._body`` — consistent.
      2. **Rewrite the slot alias** (``hal0/agent`` → ``qwen3-6-…``) so
         registry / dispatch see the real model id.
      3. **Apply the thinking policy** (Qwen3 template kwarg lever;
         see :mod:`hal0.normalize.thinking`).

    ``request._body`` is rewritten at the end so any code re-reading
    ``request.body()`` verbatim (e.g. ``Dispatcher``) observes the
    normalised body too.
    """
    from hal0.normalize.messages import normalize_system_messages
    from hal0.normalize.resolver import LiveSlotResolver
    from hal0.normalize.thinking import apply_thinking_policy

    # Step 1: collapse + hoist system messages to position 0. Done before
    # the alias rewrite so the rewrite sees the same canonical array the
    # dispatcher will forward — avoids a second pass downstream.
    messages = body.get("messages")
    if isinstance(messages, list):
        canonical = normalize_system_messages(messages)
        if canonical is not messages:
            body = {**body, "messages": canonical}

    views = await _normalize_slot_views(request)
    resolver = LiveSlotResolver(
        slot_views_provider=lambda: views,
        loaded_models_provider=lambda: _normalize_loaded_models(request),
    )
    raw_model = body.get("model")
    pinned: str | None = None
    upstream_only = False
    if raw_model == MEMORY_ALIAS:
        # #2919: the memory engine's one fixed id. A slot target continues
        # below exactly as ``hal0/<extraction_slot>`` would; an upstream
        # target becomes that id as-is — no slot lookup, no lane pin, no slot
        # thinking policy (this body goes to a cloud provider) — and the
        # request is marked upstream-only so the dispatcher routes it to its
        # upstream or fails with dispatch.upstream_unavailable, never the
        # agent anchor (not even on a cold or offline catalog).
        kind, target = _memory_alias_target(request)
        if kind == "upstream":
            body = {**body, "model": target}
            set_upstream_only(request, target)
            upstream_only = True
            raw_model = None
        else:
            raw_model = target
    if isinstance(raw_model, str) and raw_model:
        res = await resolver.resolve(raw_model)
        if res is not None and res.model_id:
            body = {**body, "model": res.model_id}
            # #1418: the rewrite collapses `hal0/<slot>` to a bare model id, and
            # a model id says nothing about WHICH slot should serve it — on a box
            # where two slots bind one model (brain + nano both on
            # hal0-brain-sft-fpx8) the lane silently landed on the wrong slot.
            # Carry the matched slot forward as a request-scoped pin so the
            # backend-aware load and the dispatcher's upstream ordering can
            # honour the lane the caller actually asked for. Set whenever the
            # resolver matched a model-bound slot in the chain (`fallback=False`),
            # loaded or not (#2741): for a cold match the pin is what makes
            # `_ensure_backend_for_model` load THAT slot rather than the agent
            # anchor or another slot sharing the model id. On the anchor
            # fallback (no chain entry has a bound model) no pin is set.
            if res.matched_name and not res.fallback:
                set_lane_pin(request, res.matched_name)
                pinned = res.matched_name

    model_id = body.get("model")
    if isinstance(model_id, str) and not upstream_only and not _is_remote_model(request, model_id):
        default_thinking = await _slot_thinking_default(request, model_id, pinned)
        body = apply_thinking_policy(
            body,
            default_thinking=default_thinking,
            grammar_collides=_model_grammar_collides(request, model_id),
        )

    import contextlib

    with contextlib.suppress(Exception):
        request._body = json.dumps(body).encode("utf-8")  # type: ignore[attr-defined]
    return body


async def _ensure_backend_for_model(request: Request, body: dict[str, Any]) -> None:
    """#430: load a slot-backed model under its DECLARED backend before routing.

    We resolve ``model_id`` → owning chat slot and drive
    ``SlotManager.load(slot)`` HERE, before ``dispatcher.dispatch`` —
    idempotent, and the slot's declared device/profile picks the
    backend. Whichever path dispatch then takes, the model is already
    loaded under the right backend. ``load`` blocks to READY,
    preserving the existing single-request synchronous-load UX.

    Scope: chat (``type=llm``) slots, matching the alias map and B1's
    focus; a model with no backing chat slot dispatches as-is
    (acceptance criterion: unbacked models unaffected). A slot already
    loaded under the wrong backend is NOT corrected mid-request
    (``load`` is a no-op on a ready slot) — that drift is surfaced by
    status and corrected via the manual ``/api/slots/{name}/backend``
    control (B3).

    Selection when SEVERAL slots bind one model id (#1418): the reverse lookup
    used to take the FIRST match in declaration order, so a box with two slots
    on one model (``brain`` + ``nano`` both on ``hal0-brain-sft-fpx8``) drove
    the load against whichever was declared first — including an ERROR-parked
    slot whose load could only fail again, while its healthy sibling was
    already serving. Candidates are now ranked by
    :func:`hal0.slots.state.slot_selection_rank` (dispatchable → loading →
    offline → ERROR last) behind the request's lane pin, with declaration order
    as the final tiebreak so the single-slot case is bit-for-bit unchanged.

    Best-effort: any failure is logged and swallowed so routing still
    proceeds rather than 500ing on this code path.
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
    # Reverse the alias→model_id map: every chat slot that owns this model.
    candidates = [slot for slot, mid in alias_to_model.items() if mid == model_id]
    if not candidates:
        # An FLM slot's configured id is hal0's ``-FLM`` catalog id, but the
        # body by now carries the native colon tag (``gemma4-it:e2b``): the
        # resolver's slot views and step 2 of ``_rewrite_chat_slot_alias`` both
        # translate it. Compare through the same translation, so a cold
        # ``hal0/npu`` (or ``hal0/<flm-slot>``) still loads its slot (#2741).
        from hal0.providers.flm import flm_id_to_tag_async

        for slot, mid in alias_to_model.items():
            if mid.endswith("-FLM") and await flm_id_to_tag_async(mid) == model_id:
                candidates.append(slot)
    slot_name = preferred_slot(slot_manager, candidates, lane_slot=lane_slot_pin(request))
    if slot_name is None:
        # No backing chat slot — nothing to honor.
        return
    # D4 (route-level): NEVER lazy-load an LLM back onto the GPU while the
    # arbiter holds exclusive image mode — that fights the arbiter and
    # resurrects the slot it just drained. Raise the structured
    # ``gpu.image_mode`` 503 (Retry-After via the error middleware), the
    # same envelope the dispatch guard emits for raw model ids. NOT
    # swallowed by the best-effort block below — the guard is the outcome.
    arbiter = getattr(slot_manager, "arbiter", None)
    if arbiter is not None:
        # An LLM request in image mode waits for any render to end, then for
        # the reload, instead of a 503; when pinned it returns at once and the
        # guard answers.
        make_room = getattr(arbiter, "make_room_for_llm", None)
        if make_room is not None:
            await make_room(slot_name)
        arbiter.guard_dispatch(slot_name)
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


# DR-1: path → capability-slot pins, mirroring resolve_by_capability rules
# 1-4 in dispatcher/router.py (_EMBED_PATHS / _RERANK_PATHS / _TTS_PATHS /
# _IMAGE_PATHS). Kept in lock-step with those fragments.
_CAPABILITY_PATH_PINS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("/embeddings",), "embed"),
    (("/rerankings", "/rerank"), "rerank"),
    (("/audio/speech",), "tts"),
    (("/images/generations", "/images/edits", "/images/variations"), "img"),
)


def _capability_slot_for_path(path: str) -> str | None:
    """Resolve a path-pinned capability slot name (embed/rerank/tts/img).

    Mirrors the dispatcher's ``resolve_by_capability`` rules 1-4
    (:mod:`hal0.dispatcher.router`): route purely by request path, first
    fragment wins. Returns ``None`` for a non-capability path (e.g. chat).
    """
    for fragments, slot in _CAPABILITY_PATH_PINS:
        if any(frag in path for frag in fragments):
            return slot
    return None


async def _wake_capability_slot(request: Request) -> None:
    """DR-1: wake an idle-EVICTED path-pinned capability slot before dispatch.

    The idle sweeper unloads embed/rerank/tts/img slots to OFFLINE and
    DEREGISTERS their container upstream
    (``SlotManager._deregister_container_upstream``). A follow-up request
    then 404s inside ``resolve_by_capability`` (surfaced as ``NoRouteFound``)
    long before it can reach the dispatcher's container readiness gate — so
    the gate can never reload it. This is the capability-layer twin of the
    chat branch's :func:`_ensure_backend_for_model`: resolve the slot from
    the request PATH (not the model id) and, when it is a known managed slot
    sitting OFFLINE, drive ``SlotManager.load(slot)``, which re-registers the
    upstream (``SlotManager._register_container_upstream``) so the subsequent
    dispatch resolves it instead of 404ing.

    Best-effort, same contract as the chat branch: the arbiter image-mode
    guard fires FIRST (NOT swallowed — it is the outcome), then the load runs
    in a try/except that logs + swallows so routing still proceeds.
    """
    slot_name = _capability_slot_for_path(request.url.path)
    if slot_name is None:
        return
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is None:
        return
    # Only wake a KNOWN managed slot that has a model bound; an unconfigured
    # capability path must dispatch as-is (and 404 as before) rather than trip
    # a spurious load. Critically, a model-less slot must stay dark: the
    # routing layer drops it (``_loaded_slot_from_config``), so waking one here
    # would revive a capability the operator deliberately cleared — and there
    # is nothing to load anyway. Mirror that rule (#1369).
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception:
        return
    from hal0.slots.activation import is_activated

    eligible = {c.get("name") for c in cfgs if is_activated(c)}
    if slot_name not in eligible:
        return
    # Nothing to do unless the slot was actually evicted to OFFLINE — a
    # READY/IDLE/SERVING slot is already routable, and a still-loading slot
    # is handled by the dispatcher's readiness gate.
    from hal0.slots.state import SlotState

    if slot_manager.state(slot_name) is SlotState.ERROR:
        # #2738: a cached ERROR may be a teardown that has since finished
        # (a stop that outlived its budget, persisted across an api
        # restart). ``status()`` runs the convergence that retires it; a real
        # ERROR stays ERROR and is skipped below as before.
        with contextlib.suppress(Exception):
            await slot_manager.status(slot_name)
    if slot_manager.state(slot_name) is not SlotState.OFFLINE:
        return
    # Mirror the chat branch: NEVER lazy-load onto the GPU against the
    # arbiter's exclusive mode. The guard is the outcome — not swallowed.
    arbiter = getattr(slot_manager, "arbiter", None)
    if arbiter is not None:
        # Same LLM-demand step as the chat branch: end an idle image mode first.
        make_room = getattr(arbiter, "make_room_for_llm", None)
        if make_room is not None:
            await make_room(slot_name)
        arbiter.guard_dispatch(slot_name)
    try:
        await slot_manager.load(slot_name)
    except Exception as exc:
        log.warning(
            "v1.capability_wake_failed",
            slot=slot_name,
            path=request.url.path,
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def _request_flm_stream_usage(request: Request, call: Any) -> bool:
    """Ask a streamed FLM chat upstream for its usage chunk (#2994).

    FastFlowLM >= 1.0.10 omits ``usage`` from streams unless
    ``stream_options.include_usage`` is set, which the metrics seam needs for
    exact token counts. Rewrites ``call.body`` and returns True when the extra
    usage-only chunk must be hidden from the client. Scoped to NPU (FLM) slots;
    other upstreams are untouched.
    """
    if not call.streaming or not request.url.path.endswith("/chat/completions"):
        return False
    slot = call.slot_name or call.container_slot_name
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if not slot or slot_manager is None:
        return False
    try:
        cfg = await slot_manager.get_config(slot)
    except Exception:
        return False
    if not isinstance(cfg, dict) or cfg.get("device") != "npu":
        return False
    rewritten = ensure_include_usage(call.body)
    if rewritten is None:
        return False
    call.body, strip = rewritten
    return strip


async def _dispatch_and_forward(
    request: Request,
    dispatcher: DispatcherDep,
    body: dict[str, Any] | None = None,
) -> Response:
    if body is None:
        body = await _read_json_body(request)
    # Translate an llm-slot alias (agent/utility/any enabled llm slot) → model id
    # before routing so the dispatcher sees the real model name.
    # A model owned by a pinned remote upstream is never aliased to, or
    # loaded as, a same-named local slot.
    requested = body.get("model")
    owned_remotely = isinstance(requested, str) and _is_pinned_owned(request, requested.strip())
    if not owned_remotely:
        body = await _rewrite_chat_slot_alias(request, body)
        # #430: backend-aware load BEFORE dispatch, so a slot-backed model is
        # loaded under its declared backend whichever routing path dispatch
        # then takes. A model no upstream serves surfaces as the dispatcher's
        # NoRouteFound envelope (404) — there is no catch-all fall-through.
        await _ensure_backend_for_model(request, body)
    # DR-1: capability twin of the backend-aware load — wake an idle-EVICTED
    # embed/rerank/tts/img slot (resolved from the request PATH) so its
    # upstream is re-registered before dispatch, instead of 404ing.
    await _wake_capability_slot(request)

    # OBS-1 (§13 / S12): the request seam. Observes the SAME call + response
    # objects the tps_events/ttft_events deques below already do — it does
    # not replace them (those keep /api/stats/throughput/history and
    # /api/slots/metrics working exactly as before) — and persists an exact
    # request_metric row asynchronously, off this hot path. `None` when the
    # app was built without the lifespan (bare-router tests) or metrics are
    # disabled; every seam method degrades to a no-op in that case.
    seam = getattr(request.app.state, "metrics_seam", None)
    t_entry = time.monotonic()
    call = None
    try:
        call = await dispatcher.dispatch(request, body=body)
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
        strip_usage = await _request_flm_stream_usage(request, call)
        if call.streaming:
            # The relay keeps the upstream's content type, which need not be
            # text/event-stream; register it for revocation either way (#2816).
            live_sockets.mark_long_lived(request.scope)
        response = await dispatcher.forward(call)
    except Exception as exc:
        if seam is not None:
            seam.record_error(exc, call=call, request=request, t_entry=t_entry)
        raise
    if isinstance(response, StreamingResponse):
        response = _instrument_streaming_throughput(
            response,
            request.app.state,
            call.upstream_name,
            dispatch_started=dispatch_started,
        )
        if seam is not None:
            response = seam.wrap_streaming(
                response,
                call=call,
                request=request,
                t_entry=t_entry,
                dispatch_started=dispatch_started,
            )
        if strip_usage:
            # Outside the seam: the seam has already read the usage chunk.
            response.body_iterator = strip_usage_only_chunk(response.body_iterator)
        return response
    if isinstance(response, Response) and getattr(response, "body", None):
        _record_nonstreaming_throughput(response.body, request.app.state, call.upstream_name)
        if seam is not None:
            seam.record_nonstreaming(
                response.body,
                call=call,
                request=request,
                t_entry=t_entry,
                dispatch_started=dispatch_started,
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

    * **Per-slot alias entries** (``hermes-role-slots``). Every chat slot
      (``type == "llm"``) **with a bound model** surfaces as one model
      object whose ``id`` is the slot **alias = slot name** (``agent``,
      ``utility``), carrying a human ``name``
      (``"<slot> · <model display name>"``) and the slot's
      ``context_length``/``max_context_window``. Built by
      :func:`hal0.api.hal0_slot_alias_models`. **Both warm and cold slots
      are advertised** — dispatch cold-loads on demand when a request
      addresses a cold slot by alias, so restricting discovery to warm
      slots would hide slots the gateway will happily serve. The alias is
      stable across model swaps so callers can pin a co-resident slot.

      (This paragraph previously claimed only slots "currently being
      served" appear and that "unloaded / disabled slots are omitted".
      Neither was true of the builder, and post-#1369/#1408 there is no
      ``enabled`` field at all — model-presence is the activation signal.)
    * **Upstream catalog entries** (incl. the direct-read composite
      catalogue below) — the raw model ids each ``advertise_models``
      upstream reports, so non-chat models (embed / rerank / image / …)
      keep their direct-addressing entries. The composite catalogue's
      CHAT model ids are suppressed here so they don't duplicate the alias
      entries above — a chat slot is represented exactly once, by its
      alias.

    Every entry additionally carries, when the id resolves in the local
    model registry (§21.5, :func:`hal0.api.hal0_apply_registry_detail`):
    ``labels`` (capabilities), ``checkpoint`` (quant label), ``recipe``
    (curated bucket name for a blessed model), and ``downloaded`` (always
    present — ``True`` for a slot alias or a locally-registered catalog
    id, ``False`` for an upstream-only / not-yet-pulled id).

    By default, raw catalog entries whose modality (per
    ``hal0.model_meta.classify``) is a NON-TEXT bucket
    (``_NON_TEXT_MODALITIES`` — image/video-gen, TTS, ASR) are hidden: a
    raw media-gen/audio passthrough id otherwise pollutes a chat client's
    model picker. Text models (chat + embed + rerank) stay visible so a
    client can still discover the embeddings/rerank endpoints. Pass
    ``?show_all=true`` to see everything (slot alias entries are never
    filtered — every enabled llm slot is a chat model by construction).

    PUBLIC — mounted on ``public_router`` so OpenAI SDKs that probe the
    catalog before sending Authorization headers continue to work.
    """
    show_all = _parse_bool_param(request.query_params.get("show_all"))
    data = await _aggregate_models(request, show_all=show_all)
    return {"object": "list", "data": data}


async def _aggregate_models(request: Request, *, show_all: bool) -> list[dict[str, Any]]:
    """Build the ``/v1/models`` ``data`` list (see :func:`list_models`).

    Split out so ``get_model`` can request the UNFILTERED aggregate
    (``show_all=True``) for a by-id lookup — an explicit direct fetch must
    resolve a valid id even when the LIST default would hide its modality
    — while the list route keeps the clean default. The ``owned_by`` /
    ``X-hal0-Model-Filter`` curation applies in both paths.
    """
    from hal0.api import (
        hal0_apply_registry_detail,
        hal0_chat_slot_model_ids,
        hal0_slot_alias_models,
    )
    from hal0.model_meta import classify

    upstreams = request.app.state.upstreams
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {}) or {}
    seen: set[str] = set()
    data: list[dict[str, Any]] = []
    now = int(time.time())

    slot_manager = getattr(request.app.state, "slot_manager", None)
    model_registry = getattr(request.app.state, "model_registry", None)

    # An id a pinned upstream owns dispatches there (Dispatcher._pinned_owner),
    # so its catalog row must come from that upstream too: a slot alias or a
    # composite row with the same id would describe a model the id never runs.
    pinned_owned: set[str] = set()
    try:
        for u in upstreams.list():
            if getattr(u, "pinned", False):
                pinned_owned.update(getattr(u, "owned_models", ()))
    except Exception:
        pinned_owned = set()

    # Per-slot alias entries first so a slot alias (e.g. "chat") wins
    # the id over any same-named raw model id an upstream might advertise.
    if slot_manager is not None and model_registry is not None:
        try:
            alias_entries = await hal0_slot_alias_models(slot_manager, model_registry, now=now)
        except Exception:
            alias_entries = []
        for entry in alias_entries:
            mid = entry.get("id")
            if not isinstance(mid, str) or mid in seen or mid in pinned_owned:
                continue
            seen.add(mid)
            data.append(entry)

    # Chat-slot model ids are represented by their aliases above; suppress
    # them from the raw upstream catalog so the direct-read composite
    # catalogue doesn't emit duplicate ``id=<model_id>`` rows for the same
    # chat slots.
    chat_model_ids: set[str] = set()
    if slot_manager is not None:
        try:
            chat_model_ids = await hal0_chat_slot_model_ids(slot_manager)
        except Exception:
            chat_model_ids = set()

    def _catalog_row(mid: str, owned_by: str) -> dict[str, Any] | None:
        """Build one raw catalog row, or ``None`` if §21.5's default
        modality filter hides it (see ``show_all`` in the docstring above).

        Shared by both raw-catalog sources below (the direct-read
        composite catalogue and the generic upstream loop) so the
        registry lookup + labels/checkpoint/recipe/downloaded fold-in +
        modality classification live in exactly one place.
        """
        registry_entry: Any = None
        if model_registry is not None:
            try:
                if model_registry.has(mid):
                    registry_entry = model_registry.get(mid)
            except Exception:
                registry_entry = None
        if not show_all:
            capabilities = getattr(registry_entry, "capabilities", None)
            if classify(mid, capabilities=capabilities) in _NON_TEXT_MODALITIES:
                return None
        row: dict[str, Any] = {
            "id": mid,
            "object": "model",
            "created": now,
            "owned_by": owned_by,
            "downloaded": registry_entry is not None,
        }
        hal0_apply_registry_detail(row, registry_entry)
        return row

    # Direct read of the composite model catalogue — every ready
    # chat-capable slot's model id, aggregated straight from slot config
    # (see hal0.api._fetch_hal0_composite_models). No pseudo-upstream is
    # registered in the routing table for this, so there's nothing to fetch
    # over HTTP here. Skipped when an operator has defined a real "hal0"
    # upstream in upstreams.toml — that entry is picked up by the loop
    # below like any other remote.
    #
    # #1837: re-read it here rather than trusting ``model_cache["hal0"]``
    # alone. The bucket is maintained by edges (startup, slot-ready,
    # slot mutations); anything that misses an edge — or a mutation that
    # landed while one slot's TOML momentarily wouldn't parse — leaves the
    # bucket wrong with nothing to correct it. The read is served from a
    # 5s TTL cache, so this stays cheap, and it makes the catalogue
    # self-healing instead of event-perfect. A DEGRADED read (enumeration
    # raised, or a slot was skipped) is not authoritative and falls back
    # to the bucket; a complete one refreshes it for the dashboard's
    # synthetic ``hal0`` tile too.
    composite_models = model_cache.get("hal0", [])
    if upstreams.get("hal0") is None and slot_manager is not None:
        from hal0.api import _fetch_hal0_composite_models_detailed

        try:
            fresh, degraded = await _fetch_hal0_composite_models_detailed(slot_manager)
        except Exception:
            fresh, degraded = [], True
        if not degraded:
            composite_models = fresh
            model_cache["hal0"] = list(fresh)
    if upstreams.get("hal0") is None:
        for mid in composite_models:
            if mid in seen or mid in chat_model_ids or mid in pinned_owned:
                continue
            row = _catalog_row(mid, "hal0")
            if row is None:
                continue
            seen.add(mid)
            data.append(row)

    for u in upstreams.list():
        if not getattr(u, "enabled", True):
            continue
        if not getattr(u, "advertise_models", True):
            continue
        try:
            advertised = await upstreams.fetch_models(u.name)
        except Exception:
            advertised = []
        # Per-upstream curation of the discovery catalog only — the dispatch
        # cache keeps the full list, so filtered-out ids stay addressable.
        advertised = apply_filters(advertised, getattr(u, "model_filters", None))
        for mid in advertised:
            if mid in seen:
                continue
            # A chat slot's raw model id is already covered by its alias
            # entry — don't list it twice.
            if mid in chat_model_ids:
                continue
            row = _catalog_row(mid, u.name)
            if row is None:
                continue
            seen.add(mid)
            data.append(row)
    # Canonical virtual names (hal0/agent, hal0/utility, hal0/npu) are
    # no longer advertised here — the per-slot alias entries above already
    # cover every enabled llm slot, and dispatch resolves the canonical
    # names on-demand via the LiveSlotResolver.  Keeping them would
    # double-list the same slot (once as its alias, once as hal0/<alias>).
    #
    # If restoring: iterate DEFAULT_CHAINS and append a live-resolve model
    # object per resolved virtual name.

    # Optional per-consumer owner filter (#1148). A consumer that only wants
    # hal0-local slots — e.g. Hermes' model picker, which floods with ~340
    # openrouter passthroughs otherwise — asks for just the hal0-owned rows
    # via either the ``X-hal0-Model-Filter: hal0`` request header (how Hermes
    # is provisioned) or a ``?owned_by=hal0`` query param (curl-debuggable).
    # This is discovery-surface only; dispatch by explicit id is unaffected,
    # and the default response (no header, no param) is byte-identical to
    # before, so pi and third-party SDKs are untouched.
    owner_filter = request.query_params.get("owned_by") or request.headers.get(
        "x-hal0-model-filter"
    )
    if owner_filter:
        data = [d for d in data if d.get("owned_by") == owner_filter]

    return data


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

    A by-id fetch is an EXPLICIT request, so it bypasses the LIST route's
    default non-text-modality hiding (``_aggregate_models(show_all=True)``)
    — a valid embed/rerank/image/tts/asr id resolves here even though the
    unfiltered catalog list would omit it by default. The ``owned_by`` /
    ``X-hal0-Model-Filter`` curation still applies (a mismatched owner
    scopes the id out, unchanged from before).
    """
    data = await _aggregate_models(request, show_all=True)
    for entry in data:
        if isinstance(entry, dict) and entry.get("id") == model_id:
            return entry

    virtual_entry = await _resolve_virtual_model_entry(request, model_id, data)
    if virtual_entry is not None:
        return virtual_entry

    from hal0.dispatcher.router import NoRouteFound

    raise NoRouteFound(
        f"model {model_id!r} is not advertised by any configured upstream",
        details={"model": model_id},
    )


async def _resolve_virtual_model_entry(
    request: Request, model_id: str, data: list[dict[str, Any]]
) -> dict[str, object] | None:
    """Resolve a hal0 virtual model name (``hal0/agent`` et al.) the same way
    the chat-completions path does (:func:`_normalize_chat_body`), so a
    client that ``GET``s ``/v1/models/{id}`` to validate a virtual name
    before chatting doesn't 404 (#1795 item 4).

    Hermes probes ``model.default`` (``hal0/agent`` under live-resolve) via
    this route on every session start; the aggregated catalog deliberately
    never lists the canonical virtuals (see the comment in
    ``_aggregate_models`` — they'd double-list each slot), so the plain
    id-match loop above always missed and logged a ``dispatch.no_route``
    before the very next chat turn resolved the same name fine. Returns a
    catalog row for the resolved physical model with ``id`` rewritten back
    to the requested virtual name so the caller's own handle round-trips.
    ``None`` when ``model_id`` isn't a virtual name or doesn't resolve to
    any advertised row.
    """
    if not model_id.startswith("hal0/"):
        return None
    requested = model_id
    if model_id == MEMORY_ALIAS:
        # #2919: answer with the engine target's row. An upstream target is a
        # catalog id already — pick its row; a slot target resolves below as
        # ``hal0/<extraction_slot>``. Either way the alias round-trips as the id.
        kind, target = _memory_alias_target(request)
        if kind == "upstream":
            for entry in data:
                if isinstance(entry, dict) and entry.get("id") == target:
                    return {**entry, "id": requested}
            return None
        model_id = target
    from hal0.normalize.resolver import LiveSlotResolver

    views = await _normalize_slot_views(request)
    resolver = LiveSlotResolver(
        slot_views_provider=lambda: views,
        loaded_models_provider=lambda: _normalize_loaded_models(request),
    )
    try:
        res = await resolver.resolve(model_id)
    except Exception:
        return None
    if res is None or not res.model_id:
        return None
    # The aggregated catalog lists a co-resident chat slot under its ALIAS
    # (e.g. "agent"), not its physical model id (e.g. "big") — see
    # ``hal0_slot_alias_models`` / the test_virtual_models module docstring.
    # ``res.matched_name`` is that alias when the resolver matched a
    # model-bound slot in the chain (loaded or not, #2741). On the
    # ``fallback=True`` path (no chain entry has a bound model, so the
    # configured primary was substituted) the resolver deliberately
    # returns ``matched_name=None``, so fall back to scanning the same slot
    # views for whichever slot owns ``res.model_id`` and use ITS alias.
    candidate_ids: list[str] = []
    if res.matched_name:
        candidate_ids.append(res.matched_name)
    candidate_ids.extend(v.name for v in views if v.model_id == res.model_id)
    candidate_ids.append(res.model_id)
    for entry in data:
        if isinstance(entry, dict) and entry.get("id") in candidate_ids:
            return {**entry, "id": requested}
    return None


@router.post("/chat/completions")
async def chat_completions(request: Request, dispatcher: DispatcherDep) -> Response:
    # PR-16: OmniRouter opt-in. When the body carries ``"omni": true``
    # AND we can resolve the request to a known chat slot whose model
    # advertises ``tool-calling``, route through the client-side
    # tool-calling loop instead of doing a direct passthrough.
    #
    # The opt-in mechanism is a body field (vs query param) because:
    #   1. ``_dispatch_and_forward`` already parses the body, so we
    #      pay no extra read cost.
    #   2. Clients sending the OpenAI-shape body already carry their
    #      knobs in JSON; ``"omni": true`` is the same shape.
    #   3. Stripping the field before forwarding is one line in the
    #      OmniRouter (see ``_strip_omni``).
    #
    # When OmniRouter is unavailable (no slot_manager, or the request
    # doesn't match a chat slot we own) we fall back to the standard
    # dispatch path.
    body = await _read_json_body(request)
    # Translate a chat-slot alias → model id up front so the OmniRouter
    # caller-slot match (keyed on the model id) and the dispatch path both
    # see the real model name. Also rewrites the cached request body.
    body = await _rewrite_chat_slot_alias(request, body)
    # SOLE normalization gate (chat only). Resolves hal0/* virtual names +
    # injects the thinking policy, and rewrites request._body. Placed BEFORE
    # the omni branch so the OmniRouter posts the normalized body; the
    # non-omni path then hands the already-normalized body=body into
    # _dispatch_and_forward (which sees request._body too).
    # Deliberately NOT in _dispatch_and_forward — that helper
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
    # — strict-mode backends would reject the unknown field.
    if "omni" in body:
        body = {k: v for k, v in body.items() if k != "omni"}
        import contextlib

        with contextlib.suppress(Exception):
            request._body = json.dumps(body).encode("utf-8")  # type: ignore[attr-defined]
    return await _dispatch_and_forward(request, dispatcher, body=body)


# Pre-canon NPU-trio slot names, kept as routable model aliases after the
# rename to flm-stt/flm-embed so clients and UI dropdowns that still pass the
# old capability id ("embed-npu"/"stt-npu") keep hitting the trio. Keyed by
# the request's slot type.
_NPU_TRIO_LEGACY_ALIAS: dict[str, str] = {
    "embedding": "embed-npu",
    "transcription": "stt-npu",
}


async def _is_npu_trio_request(
    request: Request,
    body: dict[str, Any],
    *,
    slot_type: str,
) -> bool:
    """Detect whether this request should go through the FLM trio router.

    PR-19. We route to the trio when:

      1. The request body's ``model`` matches a slot whose ``device ==
         "npu"`` AND ``type == slot_type``. We look at both
         ``slot.model.default`` AND ``slot.name`` so callers that pass
         either the model id or the slot name (e.g. dashboard cards
         using ``model="flm-stt"``) hit the same path. The pre-canon
         capability aliases ``embed-npu`` / ``stt-npu`` also route here
         (see ``_NPU_TRIO_LEGACY_ALIAS``).
      2. The ANCHOR (the ``device=npu, type=llm`` slot) has a model bound
         and has ``slot_type``'s modality on in its ``[npu]`` table
         (:func:`hal0.slots.activation.npu_modality_active`). #1369: this
         replaces a check on the shadow's own ``enabled`` flag. The shadows
         are display/dispatch records for the anchor's single FLM process
         and always carry a placeholder ``[model].default``, so they cannot
         express "off" — and the anchor's ``[npu]`` table is the stronger
         signal regardless: it IS the ``flm serve --asr/--embed`` flag set,
         so a modality that was never launched can no longer advertise
         itself as routable and 503 on the liveness probe instead.
      3. The :class:`NpuTrioRouter` itself is attached on ``app.state``
         (lifespan didn't fail to construct it).

    Returning ``False`` means we fall through to the regular dispatcher
    path — which, in the NPU-not-enabled case, is exactly the right
    fallback: dispatch routes to a GPU/CPU embed/stt slot if one exists,
    else 404s.

    Note: we deliberately DON'T check whether the FLM chat is currently
    loaded here. That probe lives inside the trio router. When the
    container isn't dispatchable the dispatch raises
    :class:`NpuTrioNotAvailable`, which surfaces as a clean 503 with the
    "load an NPU chat slot first" envelope — better UX than silently
    falling through to a 404 from the wrong path.
    """
    if getattr(request.app.state, "npu_trio_router", None) is None:
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
    from hal0.slots.activation import npu_modality_active

    if not npu_modality_active(configs, slot_type):
        return False
    for cfg in configs:
        if cfg.get("type") != slot_type:
            continue
        if cfg.get("device") != "npu":
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
        # Match the model.default, the (canon) slot name, or the legacy
        # capability alias ("embed-npu"/"stt-npu") for backward compatibility.
        alias = _NPU_TRIO_LEGACY_ALIAS.get(slot_type, "")
        if requested in (default, slot_name, alias) and (default or slot_name):
            return True
    return False


async def _dispatch_via_npu_trio(
    request: Request,
    *,
    body: dict[str, Any],
    kind: str,
) -> Response | None:
    """Forward an embed/STT request through the FLM trio router.

    Returns a FastAPI :class:`Response` on success / surfaced FLM error,
    or ``None`` when the trio router is not present (caller falls
    through). :class:`NpuTrioNotAvailable` propagates out so the error
    middleware emits the proper 503 envelope; HTTP errors from the FLM
    child are mirrored into the response verbatim.

    Only the embed path is routed through here — STT is multipart and
    handled inside :func:`_forward_multipart` to keep the
    bytes-buffer-then-forward seam in one place.
    """
    if kind != "embed":  # defensive — only one caller today
        return None
    router_obj = getattr(request.app.state, "npu_trio_router", None)
    if router_obj is None:
        return None
    # Stamp at dispatch start too, so the capability reads as active while
    # the request is still in flight (the counter increments on completion).
    _stamp_slot_last_used(request.app.state, "flm-embed")
    upstream_resp = await router_obj.dispatch_embed_npu(body=body)
    _touch_npu_shadow_count(request, "flm-embed")
    return _wrap_npu_trio_response(upstream_resp)


def _wrap_npu_trio_response(upstream: Any) -> Response:
    """Build a FastAPI :class:`Response` from an httpx response.

    Mirrors what :class:`Dispatcher._forward_direct` does — strips
    hop-by-hop headers, preserves the upstream status code, and copies
    the content-type so OpenAI clients see the same shape they would
    have on the standard dispatch path. Streaming responses aren't
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
    # Resolve the caller slot. We look up by the request's ``model`` field
    # against configured slots' ``model.default`` (a match implies a bound
    # model, so no separate activation check is needed — #1369). Several
    # slots can bind the same model id (e.g. two LLM slots sharing one
    # checkpoint) — ranked by :func:`hal0.dispatcher.lane_pin.preferred_slot`
    # behind the request's lane pin, same health-ranked selection #1418
    # already gave `_ensure_backend_for_model`'s backend-aware load, rather
    # than declaration order picking an ERROR-parked slot over its healthy
    # sibling (#1469).
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is None:
        return None
    requested_model = body.get("model")
    if not isinstance(requested_model, str) or not requested_model:
        return None
    from hal0.api import _slot_model_id
    from hal0.providers.flm import flm_id_to_tag_async

    configs = await slot_manager.iter_configs()
    candidates: list[str] = []
    for cfg in configs:
        if cfg.get("type") != "llm":
            continue
        default = _slot_model_id(cfg)
        if not default:
            continue
        # An FLM slot's configured id is the ``-FLM`` catalog id, but the body
        # carries the native colon tag by now (alias rewrite + normalize);
        # compare through the same translation the slot views publish (#2779).
        if default == requested_model or (
            default.endswith("-FLM") and await flm_id_to_tag_async(default) == requested_model
        ):
            candidates.append(str(cfg.get("name", "")))
    caller_slot_name = preferred_slot(slot_manager, candidates, lane_slot=lane_slot_pin(request))
    if caller_slot_name is None:
        return None
    result = await omni.run_loop(caller_slot_name=caller_slot_name, body=body)
    # OmniRouter.run_loop's failure contract is a bare {"error": "<string>"}
    # dict (OmniRouter._chat_completion's transport/HTTP/non-JSON branches,
    # and the "loop budget exhausted" fallback) — not an exception. Dumping
    # that straight into a 200 Response used to hand the caller a
    # non-OpenAI body shape on a SUCCESS status code (#1469): no `choices`,
    # no HTTP signal that anything went wrong, silent on any client that
    # checks status before parsing. Surface it through the same structured
    # `{"error": {"code","message","details"}}` envelope every other
    # /api/* failure uses.
    if (
        isinstance(result, dict)
        and "error" in result
        and "choices" not in result
        and isinstance(result.get("error"), str)
    ):
        from hal0.errors import Hal0Error

        class _OmniLoopFailed(Hal0Error):
            code = "omni.loop_failed"
            status = 502

        raise _OmniLoopFailed(
            result["error"],
            details={"caller_slot": caller_slot_name},
        )
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
    # ``/v1/embeddings`` (the dispatcher doesn't register the embed
    # shadow role — only the chat anchor). Plan §5.2.
    body = await _read_json_body(request)
    if await _is_npu_trio_request(request, body, slot_type="embedding"):
        trio_response = await _dispatch_via_npu_trio(request, body=body, kind="embed")
        if trio_response is not None:
            return trio_response
    return await _dispatch_and_forward(request, dispatcher, body=body)


@router.post("/rerankings")
async def rerankings(request: Request, dispatcher: DispatcherDep) -> Response:
    return await _dispatch_and_forward(request, dispatcher)


@router.post("/rerank")
async def rerank(request: Request, dispatcher: DispatcherDep) -> Response:
    """Alias of ``/rerankings`` under the path llama-server (and Jina/Cohere-
    style clients) actually use. The capability path map already resolved
    ``/rerank`` to the rerank slot, but no gateway route existed — clients got
    405 and had to hit the slot port directly (field finding, CT105)."""
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
    # Seed the serving tts slot's persisted request defaults (default_voice /
    # default_speed / default_response_format — Settings → Voice) into the
    # body BEFORE dispatch, mirroring how /v1/images/generations seeds the
    # img slot's [image] defaults. Explicit request params always win.
    _seed_tts_defaults(await _tts_slot_config(request, model), body)
    return await _dispatch_and_forward(request, dispatcher, body=body)


async def _tts_slot_config(request: Request, model: str) -> dict[str, Any]:
    """Config of the tts slot that will serve ``model``, or ``{}``.

    Selection mirrors dispatch closely enough for default-seeding: prefer
    the tts slot bound to the requested model, then the per-type default
    tts slot, then the seeded ``tts`` name, then a sole tts slot.
    """
    manager = getattr(request.app.state, "slot_manager", None)
    if manager is None:
        return {}
    try:
        cfgs = await manager.iter_configs()
    except Exception:  # pragma: no cover — defensive; seeding is optional
        return {}

    def _model_id(cfg: dict[str, Any]) -> str:
        model_section = cfg.get("model") or {}
        mid = (
            cfg.get("model_default")
            or cfg.get("model_id")
            or (model_section.get("default") if isinstance(model_section, dict) else None)
            or ""
        )
        return mid if isinstance(mid, str) else ""

    tts = [c for c in cfgs if (c.get("type") or "").lower() == "tts"]
    return (
        next((c for c in tts if _model_id(c) == model), None)
        or next((c for c in tts if c.get("default")), None)
        or next((c for c in tts if (c.get("name") or "") == "tts"), None)
        or (tts[0] if len(tts) == 1 else None)
        or {}
    )


def _seed_tts_defaults(tts_cfg: dict[str, Any], body: dict[str, Any]) -> None:
    """Fill omitted /v1/audio/speech params from the slot's persisted defaults."""
    voice = tts_cfg.get("default_voice")
    if not body.get("voice") and isinstance(voice, str) and voice:
        body["voice"] = voice
    speed = tts_cfg.get("default_speed")
    if body.get("speed") is None and isinstance(speed, int | float) and not isinstance(speed, bool):
        body["speed"] = float(speed)
    fmt = tts_cfg.get("default_response_format")
    if not body.get("response_format") and isinstance(fmt, str) and fmt:
        body["response_format"] = fmt


# ── /v1/images/generations (ComfyUI provider, hal0-managed translation) ────


async def _img_selection_model(request: Request) -> str | None:
    """The model bound to the img capability, or ``None`` when none is bound and enabled.

    Read from the capability orchestrator's state (``selections.img.img``), the same truth
    the dashboard shows. Only asked for when a request names no model; an orchestrator that
    is missing or cannot answer is "nothing selected", which the caller reports as a 422.
    """
    orchestrator = getattr(request.app.state, "capability_orchestrator", None)
    if orchestrator is None:
        return None
    from hal0.comfyui.autoselect import enabled_img_model

    try:
        return await enabled_img_model(orchestrator)
    except Exception:  # defensive: a probe failure must not turn a missing model into a 500
        return None


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


#: How long :func:`_client_has_gone` waits on the request's next ASGI message.
_DISCONNECT_PROBE_S = 0.05


async def _client_has_gone(request: Request) -> bool:
    """Whether the client has closed the connection, asked by listening for the message.

    ``Request.is_disconnected()`` reads with an already-cancelled scope, which sees a
    disconnect only if the server's ``receive`` answers without waiting, so through the auth
    middleware's stream guard (``live_sockets.guard_http_stream``, used on every box that
    requires auth) it sees one only on the poll after the server's answer (and before #3068
    never did). Waiting a moment on the next message answers on the first call, through that
    wrapper and without it: ``http.disconnect`` means the client is gone, and a timeout means
    it is still there. ``wait_for`` cancels what it started when
    the wait ends, so nothing is left running.
    """
    try:
        message = await asyncio.wait_for(request.receive(), timeout=_DISCONNECT_PROBE_S)
    except TimeoutError:
        return False
    return message.get("type") == "http.disconnect"


async def _refuse_image_that_wont_fit(
    request: Request, resolved: ResolvedImageModel, body: dict[str, Any]
) -> None:
    """Raise 422 ``image.wont_fit`` when the render cannot fit the memory budget.

    Judged at the size the render will use: ``body["size"]`` after the slot and model
    defaults, read through the translator's own parse and clamp. A catalog model is judged
    from its catalog facts, a user checkpoint from its file size. Only a ``wont_fit``
    verdict refuses; ``tight``, ``fits`` and an unknown budget (no GPU pool, unreadable RAM)
    let the render go on, and a failure anywhere in here does too: the preflight is advice
    the render never depends on, so it logs one warning and fails open.

    ``extra_body.hal0_skip_fit_check: true`` skips the check, budget read included.
    """
    extra = body.get("extra_body")
    if isinstance(extra, dict) and extra.get("hal0_skip_fit_check") is True:
        return

    from hal0.api import memory_source
    from hal0.comfyui.catalog import get_model
    from hal0.comfyui.fit import assess, need_for, need_for_checkpoint
    from hal0.comfyui.resolve import ImageWontFit
    from hal0.providers.comfyui_workflows import effective_size

    try:
        size = effective_size(body.get("size"))
        model = get_model(resolved.id)
        if model is not None:
            need = need_for(model, size)
        elif resolved.size_bytes is not None:
            need = need_for_checkpoint(resolved.size_bytes)
        else:
            return
        # The collector has no guard of its own (an empty budget would read as "no GPU").
        fit = assess(need, await memory_source.collect_memory_budget(request.app))
    except Exception:
        log.warning("images.preflight_budget_unavailable", exc_info=True)
        return
    if fit is None or fit.verdict != "wont_fit" or fit.reason is None:
        return
    raise ImageWontFit(
        fit.reason,
        details={
            "need": fit.as_dict()["need"],
            "render_free_gb": fit.render_free_gb,
            "ram_free_gb": fit.ram_free_gb,
            "model": resolved.id,
            "size": size,
        },
    )


@router.post("/images/generations")
async def images_generations(request: Request, dispatcher: DispatcherDep) -> Response:
    """OpenAI-compatible image generation.

    Body shape (subset honoured)::

        {
          "model":           "sdxl-turbo",        # installed catalog id or "ckpt/<path>";
                                                  # empty = the img capability's model
          "prompt":          "a cat in a hat",    # required
          "n":               1,                   # optional, batch_size
          "size":            "1024x1024",         # optional, WxH; default = the img slot's
                                                  # default_size, else the model's native size
          "response_format": "url" | "b64_json"   # optional, default "url"
        }

    Optional ``extra_body`` (hal0 extension): ``seed``, ``steps``,
    ``cfg``, ``negative_prompt``, and ``hal0_skip_fit_check`` (``true`` skips the memory
    preflight, the budget read included).

    Returns the OpenAI shape::

        {
          "created": 1716000000,
          "data": [
            {"url": "/api/images/cache/<uuid>.png"}     // response_format=url
            // OR
            {"b64_json": "<base64-encoded PNG>"}        // response_format=b64_json
          ],
          "_hal0": {"model": ..., "template": ..., "meta": ... translator debug meta ...}
        }

    Errors, in the order they are met:

    * 422 ``image.prompt_required`` for a body without a ``prompt``.
    * 422 ``image.invalid_size`` for a ``size`` that is not a string (``null`` and ``""`` mean
      "no size"); ``error.details.size_type`` names the type, the value is never echoed. A
      string the translator cannot read is not an error: it falls back to its default size.
    * Model resolution (:func:`hal0.comfyui.resolve.resolve_image_model`) raises
      404 ``image.model_unknown``, 409 ``image.model_not_installed``, 422
      ``image.model_not_renderable`` or 422 ``image.model_required``; a models tree the hal0
      user cannot read answers 409 ``comfyui.models_dir_not_writable``.
    * 422 ``image.wont_fit`` when the render cannot fit the memory budget (ADR-0039),
      refused before the arbiter unloads a GPU slot. ``error.details`` carries ``need``,
      ``render_free_gb``, ``ram_free_gb``, ``model`` and ``size``. An unknown budget never
      refuses; ``extra_body.hal0_skip_fit_check: true`` skips the check.
    * 503 ``image.gate_refused`` when ComfyUI's GPU gate refused the prompt (hal0 did not
      hand it the GPU); ``error.message`` is the gate's reason, which is also recorded as
      the gate's latest refusal on ``GET /api/comfyui/status``.

    A client that disconnects (the dashboard's Cancel) while the GPU switches is answered with
    an empty 499 and nothing is submitted to ComfyUI; the arbiter restores the GPU as usual.
    """
    from hal0.api.routes.comfyui import record_gate_refusal
    from hal0.comfyui.install import readable_tree
    from hal0.comfyui.resolve import resolve_image_model
    from hal0.dispatcher.router import NoRouteFound, UpstreamUnavailable
    from hal0.errors import Hal0Error, UnprocessableEntity
    from hal0.providers import get_provider
    from hal0.providers.comfyui import ImageGateRefused

    class _ImagePromptRequired(Hal0Error):
        code = "image.prompt_required"
        status = 422

    body = await _read_json_body(request)
    if not body.get("prompt"):
        raise _ImagePromptRequired("body.prompt is required")
    # `size` is a string or absent (null and "" mean "no size"). Anything else is the
    # client's mistake, caught here before the GPU, the budget or the translator see it.
    # An unreadable string is not: the translator falls back to its default size. The error
    # names the type, never the value: `json.loads` accepts NaN and Infinity, which the
    # error response cannot encode, and the validation handler does not echo payloads either.
    size = body.get("size")
    if size is not None and not isinstance(size, str):
        raise UnprocessableEntity(
            f"size must be a string WIDTHxHEIGHT in pixels, for example 1024x1024; "
            f"got {type(size).__name__}",
            code="image.invalid_size",
            details={"size_type": type(size).__name__},
        )

    # 1. Resolve the model first: a request that cannot render must fail before the GPU
    #    is arbitrated (a cold-start of the img container) on its behalf. The img
    #    selection is only a fallback for a request that names no model.
    raw_model = body.get("model")
    model_arg = None if raw_model is None else str(raw_model)
    selection_model = None if (model_arg or "").strip() else await _img_selection_model(request)
    # Resolution scans the models tree (disk reads), so it runs off the event loop. A tree
    # the hal0 user cannot traverse is the same 409 the models routes answer, not a 500.
    with readable_tree():
        resolved = await asyncio.to_thread(
            resolve_image_model, model_arg, selection_model=selection_model
        )

    # 2. Seed #599 request defaults from the img slot's [image] section
    #    (default_size / default_steps) before anything is judged or dispatched, so the
    #    body the fit check and the dispatcher see already has the resolved defaults.
    #    Size precedence: the request, then the slot's non-empty default_size, then the
    #    model's native size (below, once the slot has had its say). This only reads the
    #    slot configs; the GPU is not touched until the image job below.
    manager = getattr(request.app.state, "slot_manager", None)
    arbiter = manager.arbiter if manager is not None else None
    if manager is not None:
        from hal0.slots.arbiter import gpu_exclusive_group

        cfgs = await manager.iter_configs()
        img_cfg = next((c for c in cfgs if gpu_exclusive_group(c) == "img"), None)
        img_section = (img_cfg or {}).get("image") or {}
        if isinstance(img_section, dict):
            default_size = img_section.get("default_size")
            if not body.get("size") and isinstance(default_size, str) and default_size:
                body["size"] = default_size
            default_steps = img_section.get("default_steps")
            if (
                isinstance(default_steps, int)
                and not isinstance(default_steps, bool)
                and default_steps > 0
            ):
                extra = body.get("extra_body")
                if not isinstance(extra, dict):
                    extra = {}
                    body["extra_body"] = extra
                extra.setdefault("steps", default_steps)
    if not body.get("size"):
        body["size"] = resolved.defaults["size"]

    # 3. Fit preflight (ADR-0039): a render the memory budget cannot hold is refused here,
    #    before the arbiter unloads a single GPU slot to make room for it. Unknown is
    #    never a refusal, and `extra_body.hal0_skip_fit_check: true` opts out.
    await _refuse_image_that_wont_fit(request, resolved, body)

    # 4. GPU arbitration (Phase D, spec §7) — BEFORE dispatch so the img
    #    upstream is registered before resolve_by_capability runs.  When the
    #    img slot is cold (the normal idle state after arbiter restore_llm),
    #    ensure_img() cold-starts the container which calls
    #    _register_container_upstream; without this, dispatch's Rule-4 path
    #    selects slot "img" but no upstream exists yet and raises
    #    LegacyResolutionFailed → 404.  Fix for issue #725.
    #
    # The render is image work from before the GPU switch until provider.infer
    # returns, so the arbiter never restores LLM mode under it; once it is done,
    # an LLM request or the restore loop hands the GPU back.
    async with arbiter.image_job() if arbiter is not None else contextlib.nullcontext():
        if manager is not None:
            from hal0.errors import NotFound

            try:
                await arbiter.ensure_img()
            except NotFound as exc:
                # No img-group slot configured (manually-registered upstream,
                # tests) — nothing to arbitrate; generation proceeds as-is.
                if exc.code != "gpu.img_slot_missing":
                    raise

        # 5. Resolve the slot the request should land on.  ensure_img() above
        #    guarantees the img upstream is registered, so resolve_by_capability
        #    Rule 4 (/images/ path) succeeds even on a cold slot, including after
        #    it crashed or was unloaded while the GPU stayed in image mode (#3048).
        call = await dispatcher.dispatch(request, body=body)

        # 6. Discover the local slot's port from the resolved upstream.
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

        # 7. The last stop before ComfyUI is asked to render. The dashboard's Cancel aborts
        #    this request, and the switch above can take long enough for that to happen
        #    during it: a client that has gone gets nothing rendered. Returning from inside
        #    the `async with` is a normal exit, so image_job() closes and the arbiter's
        #    restore clock starts as for any finished job; the GPU goes back as usual.
        #    499 is nginx's "client closed request"; the access log records it.
        if await _client_has_gone(request):
            log.info("images.client_gone_before_render", model=resolved.id)
            return Response(status_code=499)

        # 8. Drive the ComfyUI provider directly. Inject the resolved template and
        #    checkpoint override so the provider's translator knows what to render
        #    without resolving the model again. Always set from the resolution: a
        #    client's own copies of these keys are overwritten, so a client can
        #    never choose the template or the checkpoint file. `_hal0_model` is the id the
        #    request resolved to (the client's `model` may be blank, an alias or untrimmed);
        #    the provider uses it to name the render in /api/comfyui/status's progress.
        provider = get_provider("comfyui")
        body_with_meta = {
            **body,
            "_hal0_template": resolved.template,
            "_hal0_ckpt_override": resolved.ckpt_override,
            "_hal0_model": resolved.id,
        }
        try:
            result = await provider.infer(port, body_with_meta)
        except ImageGateRefused as exc:
            # ComfyUI's GPU gate refused this render. On a box that requires auth the gate
            # may have no credential to report it with (the report is refused like its
            # other requests), so hal0 records it from the render that ran into it.
            record_gate_refusal(exc.message)
            raise

    # Track most-recent-model for dashboard.
    last_used = getattr(request.app.state, "last_used_model", None)
    if last_used is not None and call.upstream_name:
        last_used[call.upstream_name] = resolved.id

    # 9. Emit OpenAI-shaped response.
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
            "model": resolved.id,
            "template": resolved.template,
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

    The moonshine runner image already converts ffmpeg decode failures to a
    415 with the ``audio.unsupported_format`` envelope and no ``ffmpeg``
    substring (its server lives in the hal0-runner-images repo).
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
    # ``/v1/audio/transcriptions`` — no transcription model is
    # registered for the FLM chat anchor, so the standard dispatch
    # path would 404. Plan §5.2.
    if model_value and request.url.path.endswith("/audio/transcriptions"):
        synthetic_body = {"model": model_value}
        if await _is_npu_trio_request(request, synthetic_body, slot_type="transcription"):
            router_obj = getattr(request.app.state, "npu_trio_router", None)
            if router_obj is not None:
                # Stamp at dispatch start — STT on a cold NPU can run for
                # seconds, and the card should tint while it's in flight.
                _stamp_slot_last_used(request.app.state, "flm-stt")
                upstream_resp = await router_obj.dispatch_stt_npu(
                    body=raw_body,
                    content_type=content_type,
                )
                _touch_npu_shadow_count(request, "flm-stt")
                return _wrap_npu_trio_response(upstream_resp)

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
