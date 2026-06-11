"""FLM trio direct-port dispatch — PR-19.

When a Lemonade NPU chat slot is loaded with ``flm.args = "--asr 1 --embed 1"``
(plan §5.1), a single ``flm serve`` child process answers three OpenAI-shaped
endpoints simultaneously: ``/v1/chat/completions``, ``/v1/audio/transcriptions``,
and ``/v1/embeddings``. Lemonade only registers the **chat** model; it does
not know the ASR or embed roles exist. The dashboard surfaces three slots
anyway (``agent``, ``stt-npu``, ``embed-npu``) because the operator manages
them as a unit (ADR-0008 §5, ADR-0009).

That asymmetry means hal0 must bypass Lemonade's dispatcher for the two
"shadow" roles:

  - **Chat → Lemonade.** ``/v1/chat/completions`` keeps routing through
    Lemonade's ``/v1/chat/completions``; Lemonade's router proxies to the
    FLM child internally, so the existing dispatcher path works unchanged.
  - **STT → FLM child directly.** ``/v1/audio/transcriptions`` for the
    ``stt-npu`` slot must POST straight to ``{backend_url}/v1/audio/transcriptions``
    where ``backend_url`` is whatever port lemond assigned to the FLM
    child. Lemonade would 404 these because it has no transcription
    model registered.
  - **Embed → FLM child directly.** Same story for ``/v1/embeddings``
    against the ``embed-npu`` slot.

Discovery: ``GET /v1/health`` returns a ``loaded[]`` list of entries each
carrying ``model_name``, ``backend_url``, ``recipe``, ``type``, … We pick
the entry whose ``recipe == "flm"`` AND ``type == "llm"`` — that's the
chat anchor; its ``backend_url`` is the FLM child process we route to.

This module is pure dispatch — slot lifecycle / capabilities orchestration
lives in :mod:`hal0.capabilities.orchestrator` and :mod:`hal0.slots.manager`.
We assume the operator has already enabled the trio via capabilities.toml;
our job is to honour that selection at request time.

Edge cases (plan §5.3, ADR-0009):

  1. **FLM chat not loaded.** Trio dispatch raises :class:`FLMTrioNotAvailable`
     so the caller can surface a clear "load an NPU chat slot first"
     message instead of mysteriously 404ing.
  2. **``/v1/health`` returns no FLM entry.** Same as (1) — there's no
     backend_url to POST to.
  3. **Trio model swap mid-request.** The request already in flight uses
     the stale backend_url it discovered; subsequent requests re-discover
     the new url on each call. This is the looser-coupling tradeoff the
     plan accepts in §5.3 — we don't cache backend_url across requests.
  4. **NPU slot disabled.** Trio router is NEVER called; v1.py's gating
     check (active stt-npu/embed-npu + device=npu + enabled=true) keeps
     normal Lemonade routing in charge. The dispatcher then 404s if no
     fallback slot is configured — expected fallthrough behaviour.

Per the locked plan: no NPU exclusivity validation here (PR-20 owns); no
caching of backend_url (the swap-mid-request semantics require fresh
lookup); no LemonadeClient surface changes (we use the existing
``.health()`` method only).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from hal0.dispatcher._npu_common import is_container_npu_cfg
from hal0.errors import Hal0Error
from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import LemonadeError

log = logging.getLogger(__name__)

# Per-request timeout for the trio's HTTP calls. STT can be slow on cold
# NPU (whisper warm-up); give it a generous read budget. Embed is fast
# but shares the same budget for simplicity — the request type already
# bounds the worst case (embed of a single string completes in <1s on
# Strix Halo per spike #2 in memory ``hal0_lemonade_flm_npu_install``).
_DEFAULT_TIMEOUT_S = 120.0


class FLMTrioNotAvailable(Hal0Error):
    """The FLM chat anchor isn't loaded, so the trio's shadow roles
    (``stt-npu`` / ``embed-npu``) have no backend to forward to.

    Surfaced when:

      - ``/v1/health`` doesn't list a ``recipe == "flm"`` AND ``type == "llm"``
        entry, OR
      - that entry lacks a non-empty ``backend_url``, OR
      - lemond itself is unreachable (the health probe raises).

    Plan §5.3 mandates the surface message: "load an NPU chat slot first".
    The 503 status fits the "service component temporarily unavailable"
    semantics — operator action required.
    """

    code = "npu.trio_unavailable"
    status = 503


class FLMTrioRouter:
    """Discovers the FLM child process and dispatches STT/embed to it.

    Held as a singleton on ``app.state.flm_trio_router`` (constructed in
    :func:`hal0.api.create_app`'s lifespan), one per process. The held
    :class:`LemonadeClient` is the same instance the rest of hal0 uses,
    so :meth:`find_flm_chat_backend_url` shares the httpx connection pool
    with every other ``/v1/health`` reader.

    The router is **stateless** apart from the injected client + http
    client — no caching of backend_url across calls, because plan §5.3
    explicitly accepts that a trio model swap mid-request leaves the
    in-flight request bound to the stale URL while the next one
    re-discovers fresh. Caching would invert that semantics and force
    cache invalidation hooks in every load/swap path.

    Args:
        lemonade_client: Used for ``/v1/health`` discovery. Required.
        http_client: Optional shared httpx client for the FLM POSTs.
            When ``None``, a per-call client is created (zero shared
            state — fine for tests; in production wire one in via the
            lifespan so connections pool).
        timeout_s: Per-call timeout for the FLM POST. Default 120s —
            long enough for cold-NPU STT warm-up.
    """

    def __init__(
        self,
        lemonade_client: LemonadeClient,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        slot_manager: Any | None = None,
    ) -> None:
        self._lemonade = lemonade_client
        self._http_client = http_client
        self._timeout_s = timeout_s
        self._slot_manager = slot_manager

    # ── discovery ──────────────────────────────────────────────────

    async def _container_npu_url(self) -> str | None:
        """Return static-port URL for a containerized npu slot, or None.

        Returns non-None only when ALL hold:
          - slot_manager is wired
          - "npu" slot config is a containerized NPU slot
            (see :func:`hal0.dispatcher._npu_common.is_container_npu_cfg`)
          - slot state is "ready" or "serving" (SERVING is READY with an
            inference in flight — the container still answers concurrent
            STT/embed requests)

        Wraps all accessor calls in try/except so a missing config,
        SlotConfigError, or any accessor bug falls through to the
        lemond walk without crashing dispatch.
        """
        if self._slot_manager is None:
            return None
        try:
            cfg = await self._slot_manager.get_config("npu")
        except Exception as exc:
            log.debug(
                "flm_trio.container_resolve_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None
        if not is_container_npu_cfg(cfg):
            return None
        port = cfg.get("port")
        if not port:
            return None
        try:
            slot = await self._slot_manager.status("npu")
            # SlotState is a StrEnum — .value is the wire string.
            state_val = slot.state.value
        except Exception as exc:
            log.debug(
                "flm_trio.container_resolve_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None
        if state_val not in {"ready", "serving"}:
            return None
        return f"http://127.0.0.1:{int(port)}"

    async def find_flm_chat_backend_url(self) -> str | None:
        """Return the FLM child's ``backend_url`` if one is loaded.

        Walks ``/v1/health.loaded[]`` (also ``all_models_loaded[]`` for
        forward-compat with the alternate key Lemonade emits — see
        ``hal0/lemonade/metrics_shim.py`` comments) and picks the first
        entry whose:

          - ``recipe == "flm"`` (rules out llama.cpp slots that happen to
            be loaded on the iGPU)
          - ``type == "llm"``    (rules out embed / ASR shadow entries
            should Lemonade ever advertise them; today it does not)
          - ``backend_url`` is a non-empty string

        Returns ``None`` when no such entry exists, when lemond raises,
        or when the JSON body is missing the expected list shape. Never
        raises — caller treats ``None`` as "trio not available".

        The "never raises" contract is deliberate: every consumer wants
        the same fallback behaviour (raise :class:`FLMTrioNotAvailable`
        at the dispatch site), and bubbling the LemonadeError up here
        would force every call site to repeat the same wrapping.
        """
        # Phase A: container-first resolution. When the npu slot is a ready
        # container slot its port is static in slot config — skip the lemond
        # health walk entirely. Legacy lemond walk stays as fallback (removed
        # in Phase E).
        container_url = await self._container_npu_url()
        if container_url is not None:
            return container_url

        try:
            health = await self._lemonade.health()
        except LemonadeError as exc:
            log.debug(
                "flm_trio.health_unavailable",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "flm_trio.health_unexpected_error",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None

        if not isinstance(health, dict):
            return None

        # Lemonade emits the loaded list under either key depending on
        # version — accept both. ``all_models_loaded`` is the newer name;
        # ``loaded`` is what the deep-dive doc and the client docstring
        # currently expect. Walking both keys keeps us pinned-version-
        # agnostic.
        for key in ("loaded", "all_models_loaded"):
            entries = health.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("recipe") != "flm":
                    continue
                if entry.get("type") != "llm":
                    continue
                backend_url = entry.get("backend_url")
                if isinstance(backend_url, str) and backend_url.strip():
                    # Normalise the discovered base so the
                    # ``{backend_url}/v1/...`` joins below always produce
                    # exactly one ``/v1``. lemond versions disagree on the
                    # shape: some emit a bare ``host:port``, but 10.6.0
                    # includes the OpenAI ``/v1`` suffix
                    # (``http://127.0.0.1:8001/v1``). Without stripping it the
                    # dispatch URL became ``/v1/v1/embeddings`` → 404 (the
                    # NPU embed/STT slots silently failed on the public API
                    # path). Strip a trailing ``/v1`` and any trailing slash.
                    url = backend_url.strip().rstrip("/")
                    if url.endswith("/v1"):
                        url = url[: -len("/v1")]
                    return url

        return None

    # ── dispatch — STT ─────────────────────────────────────────────

    async def dispatch_stt_npu(
        self,
        *,
        body: bytes,
        content_type: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Forward an STT request to the FLM child's ``/v1/audio/transcriptions``.

        ``body`` is the raw multipart bytes from the inbound request —
        forwarded verbatim so the FLM-side multipart parser sees the
        same wire format the client sent. ``content_type`` MUST carry
        the multipart boundary; recompute it from the inbound headers
        before passing it in (the v1.py route already does this via
        ``request.headers["content-type"]``).

        Returns the raw :class:`httpx.Response`; the caller decides how
        to wrap it for FastAPI (the v1.py audio route builds a
        :class:`starlette.responses.Response` from the bytes + status,
        same as the dispatcher's ``_forward_direct``).

        Raises:
            FLMTrioNotAvailable: No FLM chat loaded. Message is the
                exact "load an NPU chat slot first" string from
                plan §5.3 so the dashboard can pattern-match.
        """
        backend_url = await self.find_flm_chat_backend_url()
        if backend_url is None:
            raise FLMTrioNotAvailable(
                "FLM trio not available — load an NPU chat slot first.",
                details={"endpoint": "/v1/audio/transcriptions"},
            )
        return await self._post(
            f"{backend_url}/v1/audio/transcriptions",
            content=body,
            headers=self._merge_headers(extra_headers, content_type=content_type),
        )

    # ── dispatch — embed ───────────────────────────────────────────

    async def dispatch_embed_npu(
        self,
        *,
        body: dict[str, Any],
        extra_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Forward an embeddings request to the FLM child's ``/v1/embeddings``.

        ``body`` is the parsed OpenAI-shape body (``{"model": ..., "input": ...}``);
        we re-serialise it as JSON and POST it to the FLM child. Param
        forwarding is verbatim — anything the caller passes through
        (e.g., ``encoding_format``, ``dimensions``) lands on the FLM
        process unchanged.

        Returns the raw :class:`httpx.Response`; the v1.py route wraps
        it into a FastAPI :class:`Response`.

        Raises:
            FLMTrioNotAvailable: No FLM chat loaded.
        """
        backend_url = await self.find_flm_chat_backend_url()
        if backend_url is None:
            raise FLMTrioNotAvailable(
                "FLM trio not available — load an NPU chat slot first.",
                details={"endpoint": "/v1/embeddings"},
            )
        return await self._post(
            f"{backend_url}/v1/embeddings",
            json=body,
            headers=self._merge_headers(extra_headers, content_type="application/json"),
        )

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _merge_headers(
        extra: Mapping[str, str] | None,
        *,
        content_type: str,
    ) -> dict[str, str]:
        """Build the outbound header dict.

        We always set ``content-type`` (callers pass it in so the
        multipart boundary survives for STT); ``extra`` lets the caller
        forward anything else relevant (today: nothing — the dispatcher
        normally forwards request headers, but the trio path skips that
        because hop-by-hop / auth headers from the inbound request
        shouldn't leak to the FLM child).
        """
        out: dict[str, str] = {"content-type": content_type}
        if extra:
            for k, v in extra.items():
                # Don't let extra clobber our content-type — the
                # multipart boundary is computed on the inbound side and
                # the caller passes that exact string in via the
                # ``content_type`` arg.
                if k.lower() == "content-type":
                    continue
                out[k] = v
        return out

    async def _post(
        self,
        url: str,
        *,
        content: bytes | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Single chokepoint for the FLM POST. Uses the shared client when
        provided, else opens a per-call one.
        """
        if self._http_client is not None:
            return await self._http_client.post(
                url,
                content=content,
                json=json,
                headers=headers,
                timeout=self._timeout_s,
            )
        # Per-call client — slightly more expensive but keeps the router
        # usable without a wired-in pool (tests, ad-hoc scripts).
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            return await client.post(url, content=content, json=json, headers=headers)


__all__ = [
    "FLMTrioNotAvailable",
    "FLMTrioRouter",
]
