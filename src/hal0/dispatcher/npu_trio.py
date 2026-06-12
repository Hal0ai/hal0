"""NPU trio direct-port dispatch.

A single ``flm serve`` process inside the containerized ``npu`` slot
(``hal0-slot@npu``) answers three OpenAI-shaped endpoints simultaneously:
``/v1/chat/completions``, ``/v1/audio/transcriptions``, and
``/v1/embeddings``. Chat routes through the slot's registered upstream like
any other slot; the two "shadow" roles (STT + embed) are forwarded straight
to the container's static port by this router when v1.py's gating check
(:func:`hal0.api.routes.v1._is_npu_trio_request`) detects an enabled
``device=npu`` transcription/embedding slot record.

The container's port is static in slot config — no discovery step. The
router is stateless apart from the injected slot manager + optional shared
http client.

Edge cases:

  1. **NPU container not dispatchable** (OFFLINE/ERROR/transitional) —
     dispatch raises :class:`NpuTrioNotAvailable` so the caller surfaces a
     clear "load an NPU chat slot first" envelope instead of a mystery 404.
  2. **Model swap mid-request.** A swap = container restart; the in-flight
     request fails on the dead port and the next one resolves fresh state.
  3. **NPU slot disabled.** The router is never called — v1.py's gating
     check keeps normal dispatch in charge.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from hal0.dispatcher._npu_common import is_container_npu_cfg
from hal0.errors import Hal0Error

log = logging.getLogger(__name__)

# Per-request timeout for the trio's HTTP calls. STT can be slow on cold
# NPU (whisper warm-up); give it a generous read budget. Embed is fast
# but shares the same budget for simplicity.
_DEFAULT_TIMEOUT_S = 120.0


class NpuTrioNotAvailable(Hal0Error):
    """The NPU container isn't dispatchable, so the trio's shadow roles
    (``stt-npu`` / ``embed-npu``) have no backend to forward to.

    Surfaced when the ``npu`` slot config is missing/not a container NPU
    slot, has no port, or isn't in the dispatchable ready-set (#696:
    READY, SERVING, or IDLE).
    """

    code = "npu.trio_unavailable"
    status = 503


class NpuTrioRouter:
    """Resolves the npu container's static port and dispatches STT/embed to it.

    Held as a singleton on ``app.state.npu_trio_router`` (constructed in
    :func:`hal0.api.create_app`'s lifespan), one per process.

    Args:
        slot_manager: Used to read the ``npu`` slot config + ready state.
        http_client: Optional shared httpx client for the FLM POSTs.
            When ``None``, a per-call client is created (zero shared
            state — fine for tests; in production wire one in via the
            lifespan so connections pool).
        timeout_s: Per-call timeout for the FLM POST. Default 120s —
            long enough for cold-NPU STT warm-up.
    """

    def __init__(
        self,
        slot_manager: Any,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._slot_manager = slot_manager
        self._http_client = http_client
        self._timeout_s = timeout_s

    # ── discovery ──────────────────────────────────────────────────

    async def resolve_npu_url(self) -> str | None:
        """Return the static-port URL for the containerized npu slot, or None.

        Returns non-None only when ALL hold:
          - the "npu" slot config is a containerized NPU slot
            (see :func:`hal0.dispatcher._npu_common.is_container_npu_cfg`)
          - the slot is in the dispatchable ready-set per #696: READY,
            SERVING, or IDLE (SERVING = READY with an inference in flight;
            IDLE = warm but quiet — both are dispatchable targets)

        Wraps all accessor calls in try/except so a missing config,
        SlotConfigError, or any accessor bug degrades to None ("trio not
        available") without crashing dispatch. Never raises.
        """
        if self._slot_manager is None:
            return None
        try:
            cfg = await self._slot_manager.get_config("npu")
        except Exception as exc:
            log.debug(
                "npu_trio.resolve_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None
        if not is_container_npu_cfg(cfg):
            return None
        port = cfg.get("port")
        if not port:
            return None
        try:
            if not self._slot_manager.is_ready_for_dispatch("npu"):
                return None
        except Exception as exc:
            log.debug(
                "npu_trio.resolve_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None
        return f"http://127.0.0.1:{int(port)}"

    # ── dispatch — STT ─────────────────────────────────────────────

    async def dispatch_stt_npu(
        self,
        *,
        body: bytes,
        content_type: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Forward an STT request to the npu container's ``/v1/audio/transcriptions``.

        ``body`` is the raw multipart bytes from the inbound request —
        forwarded verbatim so the FLM-side multipart parser sees the
        same wire format the client sent. ``content_type`` MUST carry
        the multipart boundary; recompute it from the inbound headers
        before passing it in (the v1.py route already does this via
        ``request.headers["content-type"]``).

        Returns the raw :class:`httpx.Response`; the caller decides how
        to wrap it for FastAPI.

        Raises:
            NpuTrioNotAvailable: NPU container not dispatchable. Message
                is the exact "load an NPU chat slot first" string so the
                dashboard can pattern-match.
        """
        backend_url = await self.resolve_npu_url()
        if backend_url is None:
            raise NpuTrioNotAvailable(
                "NPU trio not available — load an NPU chat slot first.",
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
        """Forward an embeddings request to the npu container's ``/v1/embeddings``.

        ``body`` is the parsed OpenAI-shape body (``{"model": ..., "input": ...}``);
        we re-serialise it as JSON and POST it to the container. Param
        forwarding is verbatim — anything the caller passes through
        (e.g., ``encoding_format``, ``dimensions``) lands unchanged.

        Returns the raw :class:`httpx.Response`; the v1.py route wraps
        it into a FastAPI :class:`Response`.

        Raises:
            NpuTrioNotAvailable: NPU container not dispatchable.
        """
        backend_url = await self.resolve_npu_url()
        if backend_url is None:
            raise NpuTrioNotAvailable(
                "NPU trio not available — load an NPU chat slot first.",
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
        forward anything else relevant. ``extra`` never clobbers the
        content-type — the multipart boundary is computed on the inbound
        side and passed via the ``content_type`` arg.
        """
        out: dict[str, str] = {"content-type": content_type}
        if extra:
            for k, v in extra.items():
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
    "NpuTrioNotAvailable",
    "NpuTrioRouter",
]
