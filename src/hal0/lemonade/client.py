"""HTTP client for the Lemonade Server control plane.

This is the foundational layer for the v0.2 migration (ADR-0008). Every
later PR — Provider, SlotManager rewire, metrics — goes through this
wrapper. Inference forward endpoints (``/v1/chat/completions``,
``/v1/embeddings``, ...) are NOT included here; hal0's existing
dispatcher already speaks the OpenAI-compatible shape unchanged and
proxies them directly. This client owns only the control-plane surface.

Endpoints covered (per ADR-0008 §1 + plan §2.2):

  ``GET  /live``                  liveness probe; unauthenticated; cheap
  ``GET  /v1/health``             full health + ``loaded[]`` of model_names
  ``GET  /v1/stats``              last-request perf snapshot (metrics shim)
  ``POST /v1/load``               load a registered model
  ``POST /v1/unload``             unload by model_name
  ``POST /v1/pull``               download a model from upstream
  ``POST /internal/shutdown``     clean unload + exit (systemd ExecStop)
  ``GET  /internal/config``       full runtime config snapshot
  ``POST /internal/set``          atomic config setter
  ``POST /internal/cleanup-cache``  HF cache hygiene (weekly cron)
  ``WS   /logs/stream``           server log entries (logs.subscribe / .entry)

Bearer auth: hal0 ↔ lemond is loopback-only; the token is hal0's own
``LEMONADE_API_KEY`` (ADR-0008 §1 + ADR-0001), distinct from the
``HAL0_BEARER_TOKEN`` users hit hal0-api with. The four ``/internal/*``
endpoints are loopback-only at the lemond layer (403 from non-localhost)
in addition to the Bearer check.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx

from hal0.lemonade.errors import (
    LemonadeError,
    LemonadeHTTPError,
    LemonadeLoadError,
    LemonadeTimeoutError,
    LemonadeUnavailableError,
)

log = logging.getLogger(__name__)

# ADR-0008 §1: lemond binds 127.0.0.1:13305 (Lemonade default) loopback-
# only, supervised by the hal0-lemonade.service systemd unit.
DEFAULT_BASE_URL = "http://127.0.0.1:13305"

# ADR-0008 §3 + §4: hard timeout on /v1/load specifically. /v1/load has
# evict-all blast radius and is never retried by this client (the caller
# decides recovery). Other control-plane calls get a shorter budget —
# they're either cheap (/live, /v1/health) or background (/v1/pull which
# returns immediately and streams progress elsewhere — handled by a
# separate progress polling loop).
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_LOAD_TIMEOUT_S = 120.0

# PERF: /v1/health is hit once per configured slot during a single
# /api/slots refresh (SlotManager.list() fans out _is_active over every
# slot, each calling health()), plus one more from the route's enrichment
# pass — ~8 identical round-trips per request on a 7-slot box. The body is
# a global pool snapshot, so a short coalescing cache collapses the burst to
# one upstream call without changing observed behaviour.
#
# #474: raised 0.5s -> 2.0s to match the dashboard's fastest poll cadence
# (useLemonadeHealth at 2s). lemond's control plane (cpp-httplib, 8 threads,
# accept backlog 5) wedges under poll pressure when a big model is loaded and
# inference serialises its threads; a 2s window keeps the multiple
# health-bearing routes (/api/slots, /api/status) to one upstream poll per
# window. Staleness ceiling stays at 2s, acceptable for a status snapshot.
_HEALTH_CACHE_TTL_S = 2.0


class LemonadeClient:
    """Thin async wrapper around lemond's HTTP control plane.

    Design notes:
    - Stateless aside from the shared ``httpx.AsyncClient`` (matching
      the pattern in ``dispatcher/router.py``). Construct one per
      hal0-api process; share across SlotManager + metrics poller.
    - HTTP errors are re-raised as ``LemonadeError`` subclasses so
      callers never import httpx. Network failures bubble as
      ``LemonadeUnavailableError``; timeouts as ``LemonadeTimeoutError``;
      non-2xx as ``LemonadeHTTPError`` (or ``LemonadeLoadError`` for
      the special /v1/load case).
    - No automatic retries. ADR-0008 §3 (nuclear-evict's "not found"
      exemption) forbids retrying /v1/load — a non-not-found failure
      already triggered evict-all; another attempt would just repeat
      the blast radius. The rest of the API is idempotent enough that
      the caller decides retry policy.
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
        # PERF: short-TTL coalescing cache for /v1/health (see
        # _HEALTH_CACHE_TTL_S). _health_lock serialises the concurrent
        # burst from SlotManager.list()'s asyncio.gather so exactly one
        # upstream request fills the cache for the whole batch.
        self._health_cache: dict[str, Any] | None = None
        self._health_cache_at: float = 0.0
        self._health_lock: asyncio.Lock = asyncio.Lock()

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

        PERF: results are cached for ``_HEALTH_CACHE_TTL_S`` and the
        concurrent burst (e.g. SlotManager.list()'s per-slot probes) is
        coalesced under ``_health_lock`` so a single /api/slots refresh
        makes one upstream call instead of one per slot. Errors are NOT
        cached — a failed probe falls straight through to the caller's
        existing degrade-to-empty handling and the next call retries.

        Only callers that derive slot/loaded state go through this cache.
        Background consumers that just need a field from health (the
        log-stream ws-port resolver) use :meth:`_health_uncached` so they
        never pre-populate this cache with a snapshot the foreground
        introspection would then read stale within the TTL window.
        """
        now = time.monotonic()
        if self._health_cache is not None and (now - self._health_cache_at) < _HEALTH_CACHE_TTL_S:
            return self._health_cache
        async with self._health_lock:
            now = time.monotonic()
            if (
                self._health_cache is not None
                and (now - self._health_cache_at) < _HEALTH_CACHE_TTL_S
            ):
                return self._health_cache
            body = await self._health_uncached()
            self._health_cache = body
            self._health_cache_at = time.monotonic()
            return body

    async def _health_uncached(self) -> dict[str, Any]:
        """Raw ``GET /v1/health`` with no caching. Raises the usual
        ``LemonadeError`` subclasses on transport/HTTP failure."""
        async with self._request("GET", "/v1/health") as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── /v1/stats ──────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Returns the parsed JSON body of ``GET /v1/stats``.

        Lemonade's last-request perf snapshot. Per ADR-0008 + plan §2.2
        + §12.1 this is the source of truth for hal0's Prometheus shim
        (TTFT, tok/s, prompt_tokens), replacing the v0.1.x ``/metrics``
        scrape that doesn't survive the migration. KV% for GPU slots is
        NOT in /v1/stats — accepted gap, see plan §12.1.
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
        llamacpp_args: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Load a model into the Lemonade pool.

        Per ADR-0008 §3 + the ``hal0_lemonade_v1_load_schema`` memory,
        only ``model_name`` is required. Optional kwargs map directly to
        Lemonade's documented load fields. The reserved-args list
        (``--reranking``, ``--embedding``, ``--ctx-size``, ``-ngl``,
        etc.) is hardcoded in lemond's router and is NOT extensible —
        any reserved arg passed via ``llamacpp_args`` will trigger a
        4xx. Validate at the caller layer.

        ``llamacpp_args`` wire format is a SINGLE SPACE-SEPARATED
        STRING (Lemonade's C++ JSON parser, nlohmann::json, throws
        "type must be string, but is array" on a list — confirmed in
        spike #2 and the api.md research note). For caller ergonomics
        this method accepts either:

        - ``None`` — key omitted from the body entirely. NEVER send
          JSON ``null``; ``request_json["llamacpp_args"]`` is an
          unconditional accessor and nlohmann raises on null.
        - ``str`` — forwarded verbatim (e.g. ``"--threads 8"``).
        - ``list[str]`` — joined with single spaces; ``[]`` becomes
          the empty string ``""``, which Lemonade treats as
          "use default" via the ``is_empty_option`` sentinel.

        Returns the parsed response body on success. Raises
        ``LemonadeLoadError`` on 4xx/5xx — critically distinct from
        the generic HTTP error class because a /v1/load failure has
        evict-all blast radius (ADR-0008 §3).
        """
        body: dict[str, Any] = {"model_name": model_name}
        if recipe is not None:
            body["recipe"] = recipe
        if ctx_size is not None:
            body["ctx_size"] = ctx_size
        if llamacpp_backend is not None:
            body["llamacpp_backend"] = llamacpp_backend
        if llamacpp_args is not None:
            body["llamacpp_args"] = (
                llamacpp_args if isinstance(llamacpp_args, str) else " ".join(llamacpp_args)
            )
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

    # ── /internal/shutdown ─────────────────────────────────────────

    async def shutdown(self) -> dict[str, Any]:
        """Request a clean unload + process exit from lemond.

        Wired to the systemd unit's ``ExecStop`` (plan §3). Loopback-
        only at the lemond layer (lemond returns 403 from a non-
        localhost caller) on top of the same Bearer auth as ``/v1/*``.
        Returns the parsed JSON body on success; callers typically
        ignore it and watch the unit's state.
        """
        async with self._request("POST", "/internal/shutdown") as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── /internal/config ───────────────────────────────────────────

    async def internal_config(self) -> dict[str, Any]:
        """Return the full runtime config snapshot from lemond.

        Source of truth for the Settings → Lemonade admin panel
        (plan §11 PR-13). Per plan §2.2 this surface is loopback-only
        at the lemond layer; non-localhost callers get 403.
        """
        async with self._request("GET", "/internal/config") as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── /internal/set ──────────────────────────────────────────────

    async def internal_set(self, values: dict[str, Any]) -> dict[str, Any]:
        """Atomically set one or more runtime-config keys on lemond.

        Body shape: ``{key: value, ...}``. Keys split into "immediate
        effect" (``port``, ``host``, ``log_level``, ``global_timeout``,
        ``no_broadcast``, ``extra_models_dir``) and "deferred until
        next load" (``max_loaded_models``, ``ctx_size``,
        ``llamacpp_backend``, ``llamacpp_args``, ``sdcpp_backend``,
        ``whispercpp_backend``, ``steps``, ``cfg_scale``, ``width``,
        ``height``, ``flm_args``) — see plan §2.2.

        ADR-0008 §7: hal0 does NOT use the ``extra.*`` namespace,
        so callers should not flip extra-models-dir auto-discovery on.
        """
        async with self._request("POST", "/internal/set", json=values) as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── /internal/cleanup-cache ────────────────────────────────────

    async def internal_cleanup_cache(self) -> dict[str, Any]:
        """Trigger HuggingFace cache hygiene on lemond.

        Wired to a weekly cron in plan §2.2. Returns the parsed JSON
        body — typically a small report dict; callers log it.
        """
        async with self._request("POST", "/internal/cleanup-cache") as resp:
            self._raise_for_status(resp)
            return resp.json()

    # ── /logs/stream (WebSocket) ───────────────────────────────────

    async def stream_logs(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator yielding parsed JSON log entries from ``/logs/stream``.

        Lemonade exposes server logs as a WebSocket frame stream (per the
        ``hal0_lemonade_ws_protocol`` reference): the client subscribes
        once with ``{"type": "logs.subscribe", "after_seq": null}``, then
        receives ``logs.snapshot`` (the in-memory ring buffer) followed by
        ``logs.entry`` frames for each new line. This method yields the
        parsed JSON message dicts as they arrive; the caller decides
        how to filter / fan out.

        The WebSocket server does NOT live on the OpenAI gateway port
        (``self._base_url``, 13305 on hal0). lemond binds it on a
        separate, OS-assigned port reported by ``GET /v1/health`` as
        ``websocket_port``. Connecting to the gateway port returns 404
        for every upgrade attempt — which, paired with the journal
        bridge's reconnect loop, produced the ~1 Hz ``Error 404: GET
        /logs/stream`` storm (issue #421). We resolve the real WS port
        from ``/v1/health`` before connecting; if the field is absent
        (no WS server running) we yield nothing rather than hammering a
        dead path.

        Used by ``/api/lemonade/logs/stream`` (PR-11) which fans the
        stream out to the dashboard and looks for the nuclear-evict
        trigger line ("Load failed with non-file-not-found error,
        evicting all models and retrying...") to emit a structured
        ``nuclear_evict`` event. Per ADR-0008 §3 this is hal0's only
        observability hook for the evict-all blast radius.

        On any failure (lemond down, websockets lib missing, connection
        drop) the iterator simply stops — callers treat that as "no
        events for now" and reconnect on their own cadence.
        """
        # Lazy import: ``websockets`` ships transitively via
        # ``uvicorn[standard]`` but isn't a hal0 direct dep, so keep
        # the import scoped to this method.
        try:
            import websockets  # type: ignore[import-untyped]
            from websockets.exceptions import (  # type: ignore[import-untyped]
                ConnectionClosed,
                InvalidHandshake,
            )
        except ImportError:
            return

        import json as _json

        ws_url = await self._resolve_logs_ws_url()
        if ws_url is None:
            # No websocket_port advertised (WS server not running, or
            # lemond unreachable) — don't connect; a failed handshake
            # would otherwise spam lemond's log with 404s at the
            # caller's reconnect cadence (issue #421).
            return
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            # ``additional_headers`` was renamed from ``extra_headers`` in
            # websockets 13.x; try the new name first, fall back for
            # older lib versions so this works across the supported range.
            try:
                ws = await websockets.connect(  # type: ignore[attr-defined]
                    ws_url, additional_headers=headers, open_timeout=self._timeout_s
                )
            except TypeError:
                ws = await websockets.connect(  # type: ignore[attr-defined]
                    ws_url, extra_headers=headers, open_timeout=self._timeout_s
                )
        except (OSError, InvalidHandshake, TimeoutError):
            return

        try:
            await ws.send(_json.dumps({"type": "logs.subscribe", "after_seq": None}))
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    msg = _json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    yield msg
        except (ConnectionClosed, OSError):
            return
        finally:
            with contextlib.suppress(OSError, ConnectionClosed):
                await ws.close()

    async def _resolve_logs_ws_url(self) -> str | None:
        """Build the ``ws://…/logs/stream`` URL using lemond's real WS port.

        lemond serves the log-stream WebSocket on a port distinct from the
        OpenAI gateway port (``self._base_url``). It advertises that port
        via ``GET /v1/health`` → ``websocket_port`` (only present when the
        WS server is running). We reuse the gateway host but swap in the
        advertised port. Returns ``None`` when health is unreachable or
        ``websocket_port`` is missing/invalid, signalling the caller to
        skip the connection entirely (see issue #421).
        """
        try:
            # Uncached: the journal bridge calls this on every (re)connect;
            # routing it through the cached health() would poison the
            # slot-introspection cache with a stale loaded[] snapshot.
            health = await self._health_uncached()
        except LemonadeError:
            return None
        ws_port = health.get("websocket_port") if isinstance(health, dict) else None
        if not isinstance(ws_port, int) or ws_port <= 0:
            return None

        base = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        parsed = urlsplit(base)
        host = parsed.hostname or "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{parsed.scheme}://{host}:{ws_port}/logs/stream"

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
