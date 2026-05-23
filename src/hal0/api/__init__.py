"""FastAPI application factory.

The module-level `app` exists so `uvicorn hal0.api:app` works directly.
For tests and alternate entrypoints, call `create_app()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import structlog
from fastapi import Depends, FastAPI

if TYPE_CHECKING:
    from hal0.lemonade.idle import IdleDriver
    from hal0.lemonade.metrics_shim import MetricsShim

from hal0 import __version__
from hal0.api.auth import first_run as first_run_lock
from hal0.api.auth import rate_limit as auth_rate_limit
from hal0.api.middleware import error_codes, request_id
from hal0.api.middleware.auth import require_token
from hal0.api.routes import (
    agents as agents_routes,
)
from hal0.api.routes import (
    approvals as approvals_routes,
)
from hal0.api.routes import (
    auth as auth_routes,
)
from hal0.api.routes import (
    backends as backends_routes,
)
from hal0.api.routes import (
    bundles as bundles_routes,
)
from hal0.api.routes import (
    capabilities as capabilities_routes,
)
from hal0.api.routes import (
    config as config_routes,
)
from hal0.api.routes import (
    events as events_routes,
)
from hal0.api.routes import (
    hardware,
    health,
    images,
    installer,
    logs,
    models,
    npu,
    providers,
    settings,
    slots,
    updater,
    v1,
)
from hal0.api.routes import (
    lemonade_admin as lemonade_admin_routes,
)
from hal0.api.routes import (
    lemonade_logs as lemonade_logs_routes,
)
from hal0.api.routes import (
    proxmox as proxmox_routes,
)
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.config.loader import ConfigParseError, load_hal0_config, load_upstreams_config
from hal0.dispatcher.router import Dispatcher
from hal0.events import EventBus
from hal0.hardware.probe import HardwareProbe
from hal0.registry.discover import scan_and_register
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager
from hal0.upstreams.registry import Upstream, UpstreamRegistry

log = structlog.get_logger(__name__)


async def _autoregister_slot_upstreams(
    registry: UpstreamRegistry,
    slot_manager: SlotManager,
) -> None:
    """Register an Upstream for every locally-configured slot.

    Without this, a fresh install with only a slot TOML on disk has no
    way to route ``model: "primary"`` to the local llama-server: the
    dispatcher resolves through the upstream registry, and SlotManager
    doesn't auto-mirror its slots there.  This hook closes that gap so
    users only need to write the slot TOML — no separate upstreams.toml
    entry is required for the local-slot case.

    Skips slot names that are already registered (so an explicit
    upstreams.toml entry can override the auto-registered URL, e.g. for
    a reverse-proxy in front of the slot or a different port).
    """
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("slots.autoregister_failed", error=str(exc))
        return
    for cfg in cfgs:
        name = cfg.get("name", "")
        port = cfg.get("port") or cfg.get("slot", {}).get("port")
        if not name or not port:
            continue
        if registry.get(name) is not None:
            log.info("slots.autoregister_skipped", slot=name, reason="already_registered")
            continue
        registry.upsert(
            Upstream(
                name=str(name),
                kind="slot",
                url=f"http://127.0.0.1:{int(port)}/v1",
                slot_name=str(name),
                auth_style="none",
                warmup_strategy="lazy",
                advertise_models=True,
            )
        )
        log.info("slots.autoregistered", slot=name, port=int(port))


# ── FLM multiplex model seeding ────────────────────────────────────────────
# An FLM slot can serve up to three models from one process — the chat tag
# in ``model.default`` plus embed-gemma (when ``defaults.load_embed=true``)
# plus whisper-v3:turbo (when ``defaults.load_asr=true``). Those auxiliary
# models don't show up in FLM's ``/v1/models`` response (it only lists chat
# tags), so the dispatcher's passthrough cache never learns about them and
# routes ``model: "embed-gemma"`` to nowhere. Seed the cache explicitly.
_FLM_EMBED_TAG = "embed-gemma"
_FLM_ASR_TAG = "whisper-v3:turbo"


async def _refresh_model_cache_on_ready(
    event_bus: EventBus,
    upstreams: UpstreamRegistry,
    fetch_and_cache: Callable[[Upstream], Awaitable[list[str]]],
) -> None:
    """Re-fetch ``model_cache[slot]`` whenever a slot transitions to ready.

    The cache backs ``Dispatcher.dispatch`` Step 2 passthrough. When a slot's
    loaded GGUF changes (model swap, restart with a new config), the cache
    must follow — otherwise the dispatcher matches by stale ids and routes
    ``/v1/chat/completions`` to whichever slot last advertised that filename.
    SlotManager already emits ``slot.state`` events; subscribing here keeps
    the cache aligned without coupling the manager to app state.
    """
    async with event_bus.subscribe() as q:
        while True:
            event = await q.get()
            if event.get("type") != "slot.state":
                continue
            data = event.get("data") or {}
            if data.get("to") != "ready":
                continue
            slot_name = data.get("slot")
            if not isinstance(slot_name, str) or not slot_name:
                continue
            upstream = upstreams.get(slot_name)
            if upstream is None:
                continue
            try:
                await fetch_and_cache(upstream)
            except Exception as exc:  # pragma: no cover — defensive
                log.warning(
                    "model_cache.refresh_failed",
                    slot=slot_name,
                    error=str(exc),
                )


async def _seed_multiplex_models(
    registry: UpstreamRegistry,
    slot_manager: SlotManager,
    model_cache: dict[str, list[str]],
) -> None:
    """Add FLM multiplex tags (embed-gemma, whisper-v3:turbo) to the model
    cache for any slot whose config opts into the matching multiplex.

    Idempotent — appends only when missing. Runs after
    ``_autoregister_slot_upstreams`` so every slot already has an upstream
    entry by the time we touch its cache key.
    """
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:
        log.warning("slots.multiplex_seed_failed", error=str(exc))
        return
    for cfg in cfgs:
        name = cfg.get("name", "")
        provider = cfg.get("provider", "")
        if provider != "flm" or not name:
            continue
        if registry.get(name) is None:
            continue
        defaults = cfg.get("defaults") or {}
        bucket = model_cache.setdefault(name, [])
        if defaults.get("load_embed") and _FLM_EMBED_TAG not in bucket:
            bucket.append(_FLM_EMBED_TAG)
            log.info("slots.multiplex_seeded", slot=name, model=_FLM_EMBED_TAG)
        if defaults.get("load_asr") and _FLM_ASR_TAG not in bucket:
            bucket.append(_FLM_ASR_TAG)
            log.info("slots.multiplex_seeded", slot=name, model=_FLM_ASR_TAG)


def _hydrate_upstreams(registry: UpstreamRegistry) -> None:
    """Populate the upstream registry from /etc/hal0/upstreams.toml.

    Missing file is fine — fresh installs have an empty registry until
    the user adds an upstream via the UI or `hal0 upstream add`.  Malformed
    files surface a typed ConfigParseError that propagates to the lifespan;
    we log+continue rather than crashing the API so the UI can still load
    and show the config error to the user.
    """
    try:
        cfg = load_upstreams_config()
    except ConfigParseError as exc:
        log.warning("upstreams.config_parse_failed", error=str(exc))
        return
    for entry in cfg.upstream:
        try:
            registry.upsert(
                Upstream(
                    name=entry.name,
                    kind=entry.kind,
                    url=entry.url,
                    auth_style=entry.auth_style,
                    auth_value_env=entry.auth_value_env,
                    timeout_seconds=entry.timeout_seconds,
                    slot_name=entry.slot_name,
                    warmup_strategy=entry.warmup_strategy,
                    advertise_models=entry.advertise_models,
                )
            )
        except Exception as exc:
            log.warning(
                "upstreams.entry_skipped",
                name=entry.name,
                error=str(exc),
                error_type=type(exc).__name__,
            )


async def _start_lemonade_metrics_shim(app: FastAPI) -> MetricsShim | None:
    """Start the Lemonade metrics shim (PR-12, ADR-0008 §3).

    Polls ``GET /v1/stats`` + ``GET /v1/health`` on a 5s cadence and
    holds the latest snapshot on ``app.state.lemonade_metrics_shim`` so
    the ``GET /api/metrics/prometheus`` route can read it synchronously
    without blocking on a fresh upstream call per scrape.

    Reuses the LemonadeClient attached by the idle driver — sharing the
    client matches the pattern in :mod:`hal0.dispatcher.router` and
    avoids opening a second connection pool against lemond. If the idle
    driver failed to start (no client on app.state), the shim is
    skipped: the shim is purely observability; a busted Lemonade config
    must not block API startup.

    Failures here never block lifespan progression — log + continue, the
    /api/metrics/prometheus endpoint will simply return an empty
    exposition body until the shim attaches successfully on a future
    restart.
    """
    client = getattr(app.state, "lemonade_client", None)
    if client is None:
        log.info("lemonade.metrics.skipped_no_client")
        return None
    try:
        from hal0.lemonade.metrics_shim import MetricsShim

        shim = MetricsShim(client)
        await shim.start()
        app.state.lemonade_metrics_shim = shim
        log.info("lemonade.metrics.shim_attached")
        return shim
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "lemonade.metrics.shim_start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


async def _start_lemonade_idle_driver(app: FastAPI) -> IdleDriver | None:
    """Start the Lemonade idle-unload driver.

    v0.2 (ADR-0008 §1): Lemonade is the sole inference backend; this
    driver always starts. PR-10 removed the prior ``HAL0_BACKEND``
    gate — the v0.1.x toolbox path no longer exists.

    Failures here MUST NOT block API startup — a busted Lemonade
    config shouldn't keep the dashboard from coming up so the user
    can fix it. The driver itself is also resilient to transient
    lemond unavailability (see ``lemonade/idle.py`` docstring).

    See ADR-0007 §Related, ADR-0008 §1.
    """
    import os

    try:
        from hal0.lemonade.client import LemonadeClient
        from hal0.lemonade.idle import IdleDriver

        client = LemonadeClient(
            api_key=os.environ.get("LEMONADE_API_KEY") or None,
        )
        driver = IdleDriver(client)
        await driver.start()
        # Stash on app.state so /api/health surfaces + tests can find it.
        app.state.lemonade_client = client
        app.state.lemonade_idle_driver = driver
        log.info("lemonade.idle.driver_attached")
        return driver
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "lemonade.idle.driver_start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("hal0.api.startup", version=__version__)

    upstreams = UpstreamRegistry()
    _hydrate_upstreams(upstreams)
    model_registry = ModelRegistry()
    hardware_probe = HardwareProbe()

    # Cache the parsed top-level config so request handlers don't repeatedly
    # re-read hal0.toml. The /api/settings PUT path keeps this in sync.
    try:
        hal0_cfg = load_hal0_config()
    except ConfigParseError as exc:
        log.warning("hal0.config.parse_failed", error=str(exc))
        from hal0.config.schema import Hal0Config

        hal0_cfg = Hal0Config()

    # Auto-scan configured model roots so a fresh /mnt/ai-models drop-in
    # shows up in the registry without operator intervention.  Failures
    # here must NOT block startup — the API still has to come up so the
    # user can fix the offending root.
    if hal0_cfg.models.auto_scan_on_start:
        try:
            scan_result = scan_and_register(model_registry, hal0_cfg.models)
            log.info(
                "models.auto_scan_complete",
                added=len(scan_result.get("added", [])),
                skipped=len(scan_result.get("skipped", [])),
                roots=len(scan_result.get("scanned_roots", [])),
            )
        except Exception as exc:
            log.warning("models.auto_scan_failed", error=str(exc))

    # Shared in-process /v1/models cache.  The dispatcher's cold-cache
    # prefetch path needs cached_models() and fetch_models() to share
    # state — without this, prefetch fans out then re-checks the cache
    # and finds it empty, and every request 404s.
    # NOTE: no TTL yet; cache persists for the life of the process.
    # A TTL / invalidation strategy lands when the dispatcher gets its
    # own cache layer.
    model_cache: dict[str, list[str]] = {}

    async def _fetch_and_cache(u: Upstream) -> list[str]:
        models = await upstreams.fetch_models(u.name)
        # Preserve multiplex tags seeded at startup (e.g. embed-gemma /
        # whisper-v3:turbo on FLM slots). Without this, the dispatcher's
        # cold-cache prefetch overwrites the seeded entries and embed /
        # asr routing breaks until process restart.
        existing = model_cache.get(u.name, [])
        merged = list(models)
        for tag in existing:
            if tag not in merged:
                merged.append(tag)
        model_cache[u.name] = merged
        return merged

    # SlotManager owns slot state.  Built before Dispatcher so it can be
    # threaded in and forward() can flip slots into SERVING per request.
    # Construct the event bus first so the SlotManager can side-channel
    # every transition through it — the footer subscribes to /api/events.
    event_bus = EventBus()
    slot_manager = SlotManager(event_bus=event_bus)

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=model_registry,
        cached_models=lambda name: model_cache.get(name, []),
        fetch_models=_fetch_and_cache,
        slot_manager=slot_manager,
    )

    # Idle monitor — demotes READY → IDLE after the configured timeout
    # so the dashboard distinguishes "warm but quiet" from "warm and
    # actively serving" without operator help.  Defaults to 300s; the
    # constructor accepts overrides for tests.
    await slot_manager.start_idle_monitor()

    # Auto-register local slots as upstreams so the dispatcher can route
    # ``model: <slot_name>`` requests without requiring the user to write
    # both a slot TOML AND a matching upstreams.toml entry.  Explicit
    # upstreams.toml entries (hydrated above) win — autoregister skips
    # names that already exist in the registry.
    await _autoregister_slot_upstreams(upstreams, slot_manager)
    await _seed_multiplex_models(upstreams, slot_manager, model_cache)

    from hal0.hardware import HardwareStats

    # First-run OTP lockfile (FINDINGS §28). When no owner password is
    # set yet, mint (or reuse) a one-time token in
    # ``<state>/.first-run.lock`` so a non-loopback caller has to
    # present it before claiming ownership. We only mint if the
    # password store is empty — once a password is set the lockfile
    # has no purpose. Failures here are non-fatal: log + continue,
    # because a missing lockfile collapses the route to "loopback-only"
    # which is still strictly safer than the pre-fix open-LAN window.
    try:
        from hal0.auth.tokens import get_or_create_store

        _bootstrap_store = get_or_create_store(app.state)
        if _bootstrap_store.get_password_hash() is None:
            lock = first_run_lock.mint_lockfile()
            log.info(
                "auth.first_run_lock.ready",
                path=str(lock.path),
            )
        else:
            # Password already set on this install — clean up any stale
            # lockfile left over from the install run so a future
            # rotation doesn't trip over a dangling token.
            first_run_lock.consume_lockfile()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("auth.first_run_lock.mint_failed", error=str(exc))

    app.state.upstreams = upstreams
    app.state.model_registry = model_registry
    app.state.hal0_config = hal0_cfg
    app.state.hardware_probe = hardware_probe
    app.state.hardware_stats = HardwareStats()
    # Model-pull job registry — keyed by model_id, value is the
    # ``PullJob`` dataclass holding live progress + cancel flags. SSE
    # and status routes snapshot ``as_dict()`` rather than hold the
    # dataclass across event-loop ticks.
    app.state.model_pull_jobs = {}
    # Dashboard footer event bus. Constructed above (so SlotManager could
    # be wired with the same instance); published on app.state here so
    # request handlers can reach it via ``request.app.state.events``.
    app.state.events = event_bus
    await event_bus.emit(
        "system.restart",
        "info",
        "system",
        f"hal0 {__version__} starting",
        data={"version": __version__},
    )
    # /api/upstreams hands the dashboard the cached model list so the
    # "models advertised" column reflects live state without an extra
    # round trip per upstream.
    app.state.upstream_models = model_cache
    app.state.dispatcher = dispatcher
    app.state.slot_manager = slot_manager
    app.state.model_cache = model_cache

    # Capability orchestrator — overlay that maps the dashboard's
    # capability-grouped children (embed/voice/img) onto regular slots.
    # The orchestrator is intentionally constructed AFTER the slot
    # manager + registry are ready so initialize_if_missing() can lift
    # current slot config into capabilities.toml on first boot.
    capability_orchestrator = CapabilityOrchestrator(
        slot_manager=slot_manager,
        registry=model_registry,
    )
    try:
        await capability_orchestrator.initialize_if_missing()
    except Exception as exc:
        # Never let an overlay seeding failure block API startup — the
        # dashboard can still hit GET /api/capabilities and see empty
        # selections, which is the correct "blank slate" UX.
        log.warning("capabilities.init_failed", error=str(exc))
    app.state.capability_orchestrator = capability_orchestrator
    # Tracks the most recent model id sent to each upstream so the
    # dashboard's synthetic slot reflects current usage instead of the
    # first-non-alias from the catalog. Populated by v1 routes after
    # dispatch resolves.
    app.state.last_used_model = {}
    # Per-slot rolling window of (monotonic_ts, tokens_in_chunk) tuples
    # measured on the streaming forward path. Keyed by the dispatcher's
    # `call.upstream_name` (a slot name for local slots, an upstream id
    # for remote providers) so /api/slots/metrics can attribute current
    # tok/s to the right SlotCard. A defaultdict so any new slot name
    # picks up its own bounded deque without route-side bookkeeping.
    import collections

    def _new_tps_deque() -> collections.deque[tuple[float, int]]:
        return collections.deque(maxlen=4096)

    app.state.tps_events = collections.defaultdict(_new_tps_deque)

    def _new_ttft_deque() -> collections.deque[tuple[float, float]]:
        return collections.deque(maxlen=128)

    app.state.ttft_events = collections.defaultdict(_new_ttft_deque)

    log.info(
        "hal0.api.upstreams_loaded",
        count=len(upstreams.list()),
        names=[u.name for u in upstreams.list()],
    )

    # Each mounted FastMCP server has a ``StreamableHTTPSessionManager``
    # whose anyio task group must be started inside an async-context
    # before any request can be dispatched. Mounted sub-apps don't get
    # their own lifespans run automatically, so we enter each manager's
    # ``run()`` ctxmgr from the parent lifespan via an AsyncExitStack.
    # Without this every /mcp/* request fails with
    # ``Task group is not initialized``.
    from contextlib import AsyncExitStack

    managers = getattr(app.state, "mcp_session_managers", []) or []

    refresh_task = asyncio.create_task(
        _refresh_model_cache_on_ready(event_bus, upstreams, _fetch_and_cache)
    )

    async def _stop_refresh_task() -> None:
        refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task

    # Lemonade idle-unload driver (ADR-0007 §Related, ADR-0008 §1). v0.2
    # makes Lemonade the sole backend, so this driver always starts —
    # the prior ``HAL0_BACKEND=lemonade`` gate retired in PR-10. Stored
    # on app.state so tests + future shutdown hooks can introspect it.
    lemonade_idle_driver = await _start_lemonade_idle_driver(app)
    # Lemonade metrics shim (PR-12, plan §10.1 + §11). Shares the
    # ``app.state.lemonade_client`` attached by the idle driver so we
    # don't double up on connection pools against lemond. Provides the
    # snapshot the /api/metrics/prometheus route reads.
    lemonade_metrics_shim = await _start_lemonade_metrics_shim(app)

    # OmniRouter (PR-16, plan §7 + ADR-0008 §8). Client-side OpenAI
    # tool-calling loop. Wired here so the /v1/chat/completions route
    # can pick it up via ``request.app.state.omni_router`` when a
    # request body carries ``omni: true``. The router holds a
    # dedicated httpx client so its lifetime is decoupled from the
    # LemonadeClient (which owns its own connection pool for the
    # control plane).
    omni_router_client: httpx.AsyncClient | None = None
    try:
        from hal0.omni_router import OmniRouter

        omni_router_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
            follow_redirects=False,
        )
        lemonade_base_url = os.environ.get("LEMONADE_BASE_URL", "http://127.0.0.1:13305")
        app.state.omni_router = OmniRouter(
            slot_manager=slot_manager,
            http_client=omni_router_client,
            lemonade_base_url=lemonade_base_url,
        )
        log.info("omni_router.attached", base_url=lemonade_base_url)
    except Exception as exc:
        # Never let OmniRouter failure block API startup — the chat
        # route falls back to direct dispatch when ``omni_router`` is
        # absent, which is the same behaviour as the pre-PR-16 baseline.
        log.warning(
            "omni_router.start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        app.state.omni_router = None

    # FLM trio router (PR-19, plan §5 + ADR-0008 §5 + ADR-0009). When an
    # NPU chat slot is loaded with ``flm.args = "--asr 1 --embed 1"``,
    # Lemonade only registers the chat model — STT + embed run on the
    # same FLM child but aren't reachable through Lemonade's dispatcher.
    # This router discovers the FLM child's ``backend_url`` from
    # ``/v1/health`` and posts directly when v1.py's STT/embed routes
    # detect an enabled ``stt-npu`` / ``embed-npu`` slot. Falls back
    # cleanly when the FLM chat isn't loaded (FLMTrioNotAvailable raised
    # at dispatch time so the user sees a clear envelope).
    flm_trio_router = None
    try:
        from hal0.dispatcher.flm_trio import FLMTrioRouter

        lemonade_client_for_trio = getattr(app.state, "lemonade_client", None)
        if lemonade_client_for_trio is not None:
            flm_trio_router = FLMTrioRouter(
                lemonade_client=lemonade_client_for_trio,
            )
            app.state.flm_trio_router = flm_trio_router
            log.info("flm_trio.attached")
        else:
            # No lemonade client → no trio routing. v1.py's gating check
            # treats a missing router as "trio not available" and falls
            # through to the normal Lemonade dispatch path, which 404s
            # for stt-npu/embed-npu requests (since Lemonade has no
            # such models registered) — that 404 is the expected
            # behaviour when NPU isn't wired up.
            app.state.flm_trio_router = None
            log.info("flm_trio.skipped_no_lemonade_client")
    except Exception as exc:
        log.warning(
            "flm_trio.start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        app.state.flm_trio_router = None

    try:
        async with AsyncExitStack() as stack:
            for mgr in managers:
                await stack.enter_async_context(mgr.run())
            stack.push_async_callback(_stop_refresh_task)
            yield
    finally:
        if lemonade_metrics_shim is not None:
            await lemonade_metrics_shim.stop()
        if lemonade_idle_driver is not None:
            await lemonade_idle_driver.stop()
        if omni_router_client is not None:
            with contextlib.suppress(Exception):
                await omni_router_client.aclose()
        await slot_manager.stop_idle_monitor()
        await dispatcher.aclose()
        log.info("hal0.api.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="hal0",
        version=__version__,
        description="Open-source home AI inference platform",
        lifespan=lifespan,
        # OpenAPI docs at /api/docs to keep `/docs` reserved for the UI later
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    request_id.install(app)
    error_codes.install(app)

    # IP-bucket rate limiter for the auth surface (FINDINGS §32).
    # Routes pull this off ``app.state.auth_rate_limiter`` via
    # ``hal0.api.auth.rate_limit.check_rate_limit``. Installing here so
    # the limiter exists before lifespan runs — tests that swap in a
    # custom limiter (e.g. with a stub clock) can do so after the
    # TestClient construction by reassigning ``app.state``.
    auth_rate_limit.install(app)

    # ── Auth wiring ──────────────────────────────────────────────────
    # Per ADR-0001 Child B, the PUBLIC_PATHS frozenset is gone. A route
    # is public iff its router (or route) does NOT declare an auth
    # dependency — there is no allowlist to consult, the FastAPI graph
    # IS the policy.
    #
    # The /api/auth router is mounted bare — /status, /login, /logout,
    # /password (first-run path) are intentionally public; /me declares
    # require_token at the function level; the /tokens subrouter
    # declares require_admin at the subrouter level.
    app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])

    # /v1 is split into a public probe (GET /v1/models + /v1/models/{id})
    # and a writer surface that requires auth. The split lives in v1.py
    # via v1.public_router (probes) + v1.router (inference). OpenAI
    # clients historically GET /v1/models before sending an Authorization
    # header — keeping that probe auth-free preserves SDK compatibility.
    app.include_router(v1.public_router, prefix="/v1", tags=["v1"])
    _v1_auth = [Depends(require_token)]
    app.include_router(v1.router, prefix="/v1", tags=["v1"], dependencies=_v1_auth)

    # Single-purpose protected routers — every endpoint requires a token
    # (or session cookie / forwarded email) when HAL0_AUTH_ENABLED=1.
    _admin_auth = [Depends(require_token)]

    # /api/install drives the first-run wizard. When HAL0_AUTH_ENABLED is
    # unset, the gate is a pure pass-through (require_token short-circuits
    # to an anonymous identity), so the wizard still works on a fresh
    # install with no password set — see FINDINGS §29. Once auth is
    # enabled, every install endpoint requires a valid identity; mutating
    # endpoints additionally declare Depends(require_writer) at the route
    # level (matches the #11 admin-router pattern).
    app.include_router(
        installer.router,
        prefix="/api/install",
        tags=["installer"],
        dependencies=_admin_auth,
    )
    app.include_router(slots.router, prefix="/api/slots", tags=["slots"], dependencies=_admin_auth)
    app.include_router(
        models.router, prefix="/api/models", tags=["models"], dependencies=_admin_auth
    )
    app.include_router(hardware.router, prefix="/api", tags=["hardware"], dependencies=_admin_auth)
    app.include_router(logs.router, prefix="/api/logs", tags=["logs"], dependencies=_admin_auth)
    # PR-11: Lemonade log proxy — surfaces the /logs/stream WS as SSE
    # streams the dashboard consumes for the journal panel (PR-14) and
    # the nuclear-evict toast banner. Same admin auth as the rest of
    # the slot surface.
    app.include_router(
        lemonade_logs_routes.router,
        prefix="/api/lemonade",
        tags=["lemonade", "logs"],
        dependencies=_admin_auth,
    )
    # PR-13: Lemonade admin panel — GET /api/lemonade/config + POST
    # /api/lemonade/config wrap lemond's /internal/config + /internal/set
    # so the Settings → Lemonade admin panel can read + edit runtime
    # config without bypassing hal0's auth. Same admin gate as the log
    # proxy; POST additionally declares require_writer at the route
    # level so cookie sessions ride the CSRF tripwire.
    app.include_router(
        lemonade_admin_routes.router,
        prefix="/api/lemonade",
        tags=["lemonade", "admin"],
        dependencies=_admin_auth,
    )
    app.include_router(
        settings.router,
        prefix="/api/settings",
        tags=["settings"],
        dependencies=_admin_auth,
    )
    # Proxmox integration sub-router (config file at /etc/hal0/proxmox.json).
    # Mounted as a sibling under /api/settings/proxmox so the dashboard's
    # Settings panel can read/write it without touching hal0.toml.
    app.include_router(
        proxmox_routes.router,
        prefix="/api/settings/proxmox",
        tags=["settings", "proxmox"],
        dependencies=_admin_auth,
    )
    app.include_router(
        providers.router, prefix="/api", tags=["providers"], dependencies=_admin_auth
    )
    app.include_router(
        updater.router,
        prefix="/api/updates",
        tags=["updater"],
        dependencies=_admin_auth,
    )

    # Capability slots overlay — operator-facing grouping of embed /
    # voice / img children on top of the SlotManager. Admin-gated like
    # the slots router itself; selections trigger underlying slot
    # lifecycle operations.
    app.include_router(
        capabilities_routes.router,
        prefix="/api/capabilities",
        tags=["capabilities"],
        dependencies=_admin_auth,
    )

    # First-run bundle picker (ADR-0010 / PR-17). Admin-gated for
    # consistency with the rest of the capability surface; the picker
    # writes capabilities.toml entries via the orchestrator and drops
    # a marker file so the dashboard hides the picker on subsequent
    # loads. Plan §8 + §11 PR-17 own the user-facing UX.
    app.include_router(
        bundles_routes.router,
        prefix="/api/bundles",
        tags=["bundles"],
        dependencies=_admin_auth,
    )

    # Backend introspection — live status + currently-loaded children
    # per backend (NPU / GPU-Vulkan / GPU-ROCm / CPU). Read-only and
    # used by the dashboard footer; admin-gated for consistency with
    # the rest of the capability surface.
    app.include_router(
        backends_routes.router,
        prefix="/api/backends",
        tags=["backends"],
        dependencies=_admin_auth,
    )

    # NPU trio swap-status (PR-20). One read-only endpoint that merges
    # the configured NPU LLM slot model with lemond's /v1/health.loaded[]
    # so the dashboard's "Swap incoming" banner has a single source of
    # truth. Admin-gated alongside the rest of the capability surface.
    app.include_router(
        npu.router,
        prefix="/api/npu",
        tags=["npu"],
        dependencies=_admin_auth,
    )

    # Health + config/urls routers carry endpoints that are entirely
    # public (e.g. /api/status, /api/config/urls). Any future protected
    # endpoints added to these routers should declare
    # Depends(require_token) at the function level so the publicness of
    # the rest is declared by absence rather than by allowlist.
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(config_routes.router, prefix="/api/config", tags=["config"])

    # Dashboard footer event surface — read-only, public for the same
    # reason as /api/status: the footer renders during first-run before
    # any credential exists. No mutating endpoints live on this router.
    app.include_router(events_routes.router, prefix="/api/events", tags=["events"])

    # Image cache — generated PNGs from /v1/images/generations.  Admin
    # auth gate: cached PNGs live at predictable /api/images/cache/<uuid>
    # URLs and could leak prompts via filename if exposed publicly.
    app.include_router(
        images.router, prefix="/api/images", tags=["images"], dependencies=_admin_auth
    )

    # Bundled-agent lifecycle (ADR-0004 §2). Install / uninstall / list /
    # status. Single-pick + atomic switch enforced inside AgentManager.
    app.include_router(
        agents_routes.router,
        prefix="/api/agents",
        tags=["agents"],
        dependencies=_admin_auth,
    )

    # Approval inbox (ADR-0004 §5). The dashboard bell, the MCP admin
    # server's gated-tool enqueue, and the ``hal0 agent approvals``
    # CLI all read from the same lifespan-scoped ApprovalQueue. GETs
    # require any token; POST approve/deny require admin (writer)
    # scope — declared inside the route module.
    app.include_router(
        approvals_routes.router,
        prefix="/api/agent/approvals",
        tags=["approvals"],
        dependencies=_admin_auth,
    )

    # ── MCP servers (ADR-0004 §4 + ADR-0005 §2) ─────────────────────
    # Mounted BEFORE _mount_dashboard so the dashboard's SPA fallback
    # doesn't shadow /mcp/* paths. ApprovalQueue + CogneeWrapper are
    # constructed eagerly here (no async setup needed for either) so
    # the mount can wire them in immediately.
    from hal0.mcp import ApprovalQueue

    app.state.approval_queue = ApprovalQueue()

    memory_wrapper = None
    try:
        from hal0.memory import CogneeWrapper

        memory_wrapper = CogneeWrapper()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("hal0.memory.init_failed", error=str(exc))
    app.state.memory_wrapper = memory_wrapper

    # In-process memory dispatcher (Phase 8 closeout, ADR-0004 §7).
    # When Cognee is up, instantiate one MemoryDispatcher and hand it to
    # mount_mcp_servers so the admin MCP server's ``memory_*`` tools hit
    # Cognee directly instead of looping back through HTTP to
    # ``/mcp/memory``. The same client-id + private-mode resolvers the
    # memory MCP uses thread through the dispatcher so audit grounding
    # and namespace promotion stay identical across transports.
    memory_dispatcher = None
    if memory_wrapper is not None:
        try:
            from hal0.api.mcp_mount import client_id_resolver, private_resolver
            from hal0.dispatcher.memory_dispatcher import MemoryDispatcher

            memory_dispatcher = MemoryDispatcher(
                memory_wrapper,
                client_id_resolver=client_id_resolver,
                private_resolver=private_resolver,
            )
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("hal0.memory.dispatcher_init_failed", error=str(exc))
    app.state.memory_dispatcher = memory_dispatcher

    try:
        from hal0.api.mcp_mount import mount_mcp_servers

        mount_mcp_servers(
            app,
            approval_queue=app.state.approval_queue,
            memory_wrapper=memory_wrapper,
            memory_dispatcher=memory_dispatcher,
        )
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("hal0.mcp.mount_failed", error=str(exc))

    _mount_dashboard(app)

    return app


def _mount_dashboard(app: FastAPI) -> None:
    """Serve the built Vue dashboard at ``/`` with SPA fallback.

    Resolution order for ``ui/dist`` (the built Vue bundle):
      1. ``HAL0_UI_DIST`` env override (used by tests + dev installs).
      2. ``/usr/lib/hal0/ui/dist`` (FHS install path per PLAN §2).
      3. ``<repo>/ui/dist`` (editable install — find by walking up from
         this file).

    If none exist (e.g. backend-only smoke tests), skip silently — the
    api still serves ``/api/*`` and ``/v1/*`` as before.

    SPA fallback: any GET that doesn't match a route, doesn't start with
    ``/api`` or ``/v1``, and isn't a static asset returns ``index.html``
    so client-side routing (``/slots``, ``/firstrun`` etc.) survives a
    page reload.
    """
    import os
    from pathlib import Path

    from fastapi.responses import FileResponse, Response
    from fastapi.staticfiles import StaticFiles

    candidates: list[Path] = []
    env_dir = os.environ.get("HAL0_UI_DIST", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path("/usr/lib/hal0/ui/dist"))
    here = Path(__file__).resolve()
    for parent in here.parents:
        repo_dist = parent / "ui" / "dist"
        if repo_dist.exists():
            candidates.append(repo_dist)
            break

    dist = next((p for p in candidates if p.is_dir() and (p / "index.html").is_file()), None)
    if dist is None:
        log.info("dashboard.dist_not_found", searched=[str(c) for c in candidates])
        return

    log.info("dashboard.mounted", dist=str(dist))
    index = dist / "index.html"
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    brand_dir = dist / "brand"
    if brand_dir.is_dir():
        app.mount("/brand", StaticFiles(directory=brand_dir), name="brand")

    @app.get("/favicon.svg", include_in_schema=False)
    async def _favicon() -> Response:
        return FileResponse(dist / "favicon.svg")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str) -> Response:
        # Don't shadow API routes — those return 404 normally if missing.
        if full_path.startswith("api/") or full_path.startswith("v1/"):
            return Response(status_code=404)
        return FileResponse(index)


app = create_app()
