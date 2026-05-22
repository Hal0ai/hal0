"""HTTP client for the Lemonade Server control plane.

This is the foundational layer for the v0.2 migration (ADR-0006). Every
later PR — Provider, SlotManager rewire, metrics, preload validation —
goes through this wrapper. Inference forward endpoints
(``/v1/chat/completions``, ``/v1/embeddings``, ...) are NOT included
here; hal0's existing dispatcher already speaks the OpenAI-compatible
shape unchanged and proxies them directly. This client owns only the
control-plane surface.

Endpoints covered (per ADR-0006 §3):

  ``GET  /live``         liveness probe; unauthenticated; cheap
  ``GET  /v1/health``    full health + ``loaded[]`` of model_names
  ``GET  /v1/stats``     last-request perf snapshot (metrics shim §9)
  ``POST /v1/load``      load a registered model
  ``POST /v1/unload``    unload by model_name
  ``POST /v1/pull``      download a model from upstream

Bearer auth: hal0 ↔ lemond is loopback-only; the token is hal0's own
``LEMONADE_API_KEY`` (ADR-0006 §Related → ADR-0001), distinct from the
``HAL0_BEARER_TOKEN`` users hit hal0-api with.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from hal0.lemonade.errors import (
    LemonadeHTTPError,
    LemonadeLoadError,
    LemonadeTimeoutError,
    LemonadeUnavailableError,
)

log = logging.getLogger(__name__)

# ADR-0006 §6: lemond binds 127.0.0.1:9100 in the systemd unit.
DEFAULT_BASE_URL = "http://127.0.0.1:9100"

# ADR-0007 §5: hard timeout on /v1/load specifically. Other control
# plane calls get a shorter budget — they're either cheap (/live, /v1/health)
# or background (/v1/pull which returns immediately and streams progress
# elsewhere — handled by a separate progress polling loop).
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_LOAD_TIMEOUT_S = 120.0


class LemonadeClient:
    """Thin async wrapper around lemond's HTTP control plane.

    Design notes:
    - Stateless aside from the shared ``httpx.AsyncClient`` (matching
      the pattern in ``dispatcher/router.py``). Construct one per
      hal0-api process; share across SlotManager + metrics poller +
      preload validator.
    - HTTP errors are re-raised as ``LemonadeError`` subclasses so
      callers never import httpx. Network failures bubble as
      ``LemonadeUnavailableError``; timeouts as ``LemonadeTimeoutError``;
      non-2xx as ``LemonadeHTTPError`` (or ``LemonadeLoadError`` for
      the special /v1/load case).
    - No automatic retries. ADR-0007 §4 forbids retrying /v1/load
      (would guarantee another evict-all); the rest of the API is
      idempotent enough that the caller decides retry policy.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        load_timeout_s: float = DEFAULT_LOAD_TIMEOUT_S,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._load_timeout_s = load_timeout_s
        # Match dispatcher/router.py: lazy-init so unit tests that
        # exercise only one method don't open sockets.
        self._http_client: httpx.AsyncClient | None = http_client
        self._owns_http_client: bool = http_client is None

    # ── lifecycle ──────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_s),
                follow_redirects=False,
            )
        return self._http_client

    async def aclose(self) -> None:
        """Close the underlying httpx client if we own it. Idempotent."""
        if self._http_client is not None and self._owns_http_client:
            await self._http_client.aclose()
            self._http_client = None

    # ── headers ────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    # ── /live ──────────────────────────────────────────────────────

    async def live(self) -> bool:
        """Returns True if ``GET /live`` returned 2xx, False otherwise.

        Used by hal0's healthcheck path — must NOT raise on a down
        daemon. ``/live`` is unauthenticated and zero-work; safe to
        poll on the dashboard's normal cadence.
        """
        try:
            async with self._request("GET", "/live") as resp:
                return 200 <= resp.status_code < 300
        except (LemonadeUnavailableError, LemonadeTimeoutError):
            return False
        except LemonadeHTTPError:
            return False

    # ── /v1/health ─────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """Returns the parsed JSON body of ``GET /v1/health``.

        Expected shape (per Lemonade docs, may evolve): ``{"loaded":
        [{"model_name": "...", "backend_url": "...", ...}], "ready":
        bool, ...}``. Caller treats unknown fields permissively.
        """
        async with self._request("GET", "/v1/health") as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── /v1/stats ──────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Returns the parsed JSON body of ``GET /v1/stats``.

        Lemonade's last-request perf snapshot. ADR-0006 §9 makes this
        the source of truth for hal0's Prometheus shim, replacing the
        v0.1.x ``/metrics`` scrape that doesn't survive the migration.
        """
        async with self._request("GET", "/v1/stats") as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── /v1/load ───────────────────────────────────────────────────

    async def load(
        self,
        model_name: str,
        *,
        recipe: str | None = None,
        ctx_size: int | None = None,
        llamacpp_backend: str | None = None,
        llamacpp_args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Load a model into the Lemonade pool.

        Per ADR-0006 §3 + the v1_load_schema memory, only
        ``model_name`` is required. Optional kwargs map directly to
        Lemonade's documented load fields. The reserved-args list
        (``--reranking``, ``--embedding``, ``--ctx-size``, ``-ngl``,
        etc.) is hardcoded in lemond's router and is NOT extensible —
        any reserved arg passed via ``llamacpp_args`` will trigger a
        4xx. Validate at the caller layer.

        Returns the parsed response body on success. Raises
        ``LemonadeLoadError`` on 4xx/5xx — critically distinct from
        the generic HTTP error class because a /v1/load failure has
        evict-all blast radius (ADR-0007).
        """
        body: dict[str, Any] = {"model_name": model_name}
        if recipe is not None:
            body["recipe"] = recipe
        if ctx_size is not None:
            body["ctx_size"] = ctx_size
        if llamacpp_backend is not None:
            body["llamacpp_backend"] = llamacpp_backend
        if llamacpp_args is not None:
            body["llamacpp_args"] = llamacpp_args
        async with self._request(
            "POST", "/v1/load", json=body, timeout=self._load_timeout_s
        ) as resp:
            if not (200 <= resp.status_code < 300):
                # Specialise the error so SlotManager can distinguish
                # /v1/load failures (evict-all triggered) from other 4xx/5xx.
                raise LemonadeLoadError(
                    resp.status_code,
                    body=_safe_json(resp),
                    msg=f"lemonade /v1/load returned HTTP {resp.status_code} for model_name={model_name!r}",
                )
            return resp.json()

    # ── /v1/unload ─────────────────────────────────────────────────

    async def unload(self, model_name: str) -> dict[str, Any]:
        """Unload a model by name. Idempotent.

        Used by the idle-unload driver (later PR) when a slot's
        last-request age exceeds its configured TTL.
        """
        async with self._request("POST", "/v1/unload", json={"model_name": model_name}) as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── /v1/pull ───────────────────────────────────────────────────

    async def pull(self, model_name: str, *, allow_overwrite: bool = False) -> dict[str, Any]:
        """Request lemond to download ``model_name`` to its model store.

        Returns immediately; pull progress is streamed via lemond's
        ``/logs/stream`` (handled by a separate polling loop, not here).
        """
        body: dict[str, Any] = {"model_name": model_name}
        if allow_overwrite:
            body["allow_overwrite"] = True
        async with self._request("POST", "/v1/pull", json=body) as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── internal: HTTP request envelope ────────────────────────────

    @asynccontextmanager
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> AsyncIterator[httpx.Response]:
        """Single chokepoint for every outbound HTTP call.

        Converts httpx exceptions to our error hierarchy so callers
        never see ``httpx.*`` types. ``timeout`` per-call overrides
        the client default — /v1/load uses the longer load timeout.
        """
        client = self._client()
        kwargs: dict[str, Any] = {"headers": self._headers()}
        if json is not None:
            kwargs["json"] = json
        if timeout is not None:
            kwargs["timeout"] = httpx.Timeout(timeout)
        try:
            resp = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise LemonadeTimeoutError(f"{method} {path} timed out") from exc
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError) as exc:
            raise LemonadeUnavailableError(f"{method} {path}: lemond unreachable ({exc})") from exc
        except httpx.HTTPError as exc:
            # Catch-all for less common httpx errors (NetworkError,
            # CloseError, etc.) — surface as unavailable so callers
            # treat the daemon as offline.
            raise LemonadeUnavailableError(f"{method} {path}: {exc}") from exc
        try:
            yield resp
        finally:
            await resp.aclose()

    def _raise_for_status(self, resp: httpx.Response) -> None:
        """Generic 2xx-or-raise. /v1/load uses its own specialised
        error class (``LemonadeLoadError``) for blast-radius reasons —
        do not route /v1/load through here."""
        if 200 <= resp.status_code < 300:
            return
        raise LemonadeHTTPError(resp.status_code, body=_safe_json(resp))


def _safe_json(resp: httpx.Response) -> object | None:
    """Best-effort JSON parse — never raises. Used inside error paths
    where re-raising a parse failure would mask the original HTTP error.
    """
    try:
        return resp.json()
    except Exception:
        return None
