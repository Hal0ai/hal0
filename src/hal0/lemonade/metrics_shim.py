"""Lemonade metrics shim — Prometheus-facing aggregator (PR-12, ADR-0008 §3).

v0.1.x's per-slot ``/metrics`` scrape against each llama-server toolbox is
gone (plan §10.1 retired the toolbox path in PR-9). v0.2 routes every
inference through Lemonade, which exposes two perf surfaces:

  * ``GET /v1/stats`` — last-request perf snapshot (TTFT, tok/s,
    prompt + output tokens). Process-wide; NOT keyed by model. hal0 polls
    on a 5s cadence and treats it as "most recent observation".
  * ``GET /v1/health`` — currently loaded model list + per-type budgets
    (``max_models``). Drives the per-model "loaded" gauge so Prometheus
    can compute pool occupancy without parsing logs.

FLM/NPU slots are the second source: FastFlowLM emits its own perf
fields INSIDE ``/v1/chat/completions`` response bodies
(``decoding_speed_tps``, ``prefill_speed_tps``, ``prefill_duration_ttft``,
``kv_token_occupancy_rate_percentage``, ``decoding_duration``). The
shim exposes a public ``record_flm_metrics`` method that the
chat-completion dispatch hook calls when it sniffs those fields. This
is the only source of KV% in v0.2 — Lemonade's bundled llama-server
returns ``null`` for ``n_past`` (plan §12.1), so GPU/llamacpp slots
display ``—`` until a future llama-server bump or local rebuild.

Design contract (mirrors :mod:`hal0.lemonade.idle`):

  * Background task lifecycle: ``start()`` schedules the poll on the
    running loop, ``stop()`` cancels and awaits the task. Idempotent.
  * Resilience: lemond unreachable / 5xx → log + zero metrics for that
    tick; the driver never crashes. FLM-ingest record path is purely
    in-memory and cannot fail.
  * Stateless aside from the latest snapshot. The Prometheus exposition
    surface (:mod:`hal0.api.routes.metrics`) reads ``snapshot()`` and
    formats text without coupling to the shim's internal storage.

Plan refs: §10.1 (new module), §11 PR-12, §12.1 (KV% accepted missing
for GPU slots), ADR-0008 §3. Memory ``hal0_lemonade_flm_npu_install``
documents the FLM-native field names; matched verbatim here so a
Lemonade-Server-side rename surfaces as a one-line patch.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import (
    LemonadeError,
    LemonadeHTTPError,
    LemonadeTimeoutError,
    LemonadeUnavailableError,
)

log = logging.getLogger(__name__)


# Defaults intentionally short — Prometheus default scrape interval is
# 15s, so a 5s poll keeps the shim ahead of the scraper without piling
# up requests on lemond. Plan §11 PR-12 lists 5s explicitly.
DEFAULT_POLL_INTERVAL_S: float = 5.0


# ── snapshot dataclasses ─────────────────────────────────────────────────


@dataclass(frozen=True)
class StatsSnapshot:
    """Last-request perf snapshot from ``GET /v1/stats``.

    Lemonade's ``/v1/stats`` is process-wide and per-inference, NOT keyed
    by model_name (plan §5 api.md research). Treated as a single row of
    gauges labelled by ``source="last_request"``.

    Field names match Lemonade's ``/v1/stats`` response (research-confirmed
    schema): ``time_to_first_token`` (s), ``tokens_per_second`` (float),
    ``input_tokens`` (int), ``output_tokens`` (int), ``prompt_tokens`` (int).
    Any of these may be missing on a fresh process that hasn't served a
    request yet; in that case the corresponding gauge is absent from
    exposition rather than reported as 0 (clearer for dashboards).
    """

    time_to_first_token: float | None = None
    tokens_per_second: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_tokens: int | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> StatsSnapshot:
        """Best-effort parse of ``/v1/stats`` response shape.

        Accepts the v0.2 documented schema (top-level keys) AND falls
        back to ``{"last_request": {...}}`` — the existing client test
        used the nested form, and we want a Lemonade upgrade that
        switches to either shape to keep working. Bad payloads yield
        an empty snapshot (nothing emitted).
        """
        if not isinstance(payload, dict):
            return cls()
        # Prefer the documented top-level keys; if absent, look for a
        # nested ``last_request`` envelope (older Lemonade builds).
        body = payload
        if "time_to_first_token" not in body and isinstance(payload.get("last_request"), dict):
            body = payload["last_request"]
        return cls(
            time_to_first_token=_coerce_float(body.get("time_to_first_token")),
            tokens_per_second=_coerce_float(body.get("tokens_per_second")),
            input_tokens=_coerce_int(body.get("input_tokens")),
            output_tokens=_coerce_int(body.get("output_tokens")),
            prompt_tokens=_coerce_int(body.get("prompt_tokens")),
        )


@dataclass(frozen=True)
class HealthSnapshot:
    """Currently-loaded model list + per-type budgets from ``GET /v1/health``.

    ``loaded_models`` is the set of ``model_name`` strings returned in
    either ``all_models_loaded[]`` (current Lemonade) or ``loaded[]``
    (older builds — same dual-accept logic as ``idle.py``).
    ``max_models`` is the per-type budget dict
    (``{"llm": 1, "embedding": 1, ...}``) used by the dashboard's
    "1/2 LLM slots" rendering.
    """

    loaded_models: tuple[str, ...] = ()
    max_models: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> HealthSnapshot:
        if not isinstance(payload, dict):
            return cls()
        loaded_raw: Any = None
        for key in ("all_models_loaded", "loaded"):
            v = payload.get(key)
            if isinstance(v, list):
                loaded_raw = v
                break
        names: list[str] = []
        if isinstance(loaded_raw, list):
            for entry in loaded_raw:
                if isinstance(entry, dict):
                    name = entry.get("model_name")
                    if isinstance(name, str) and name:
                        names.append(name)
        max_raw = payload.get("max_models")
        budgets: dict[str, int] = {}
        if isinstance(max_raw, dict):
            for k, v in max_raw.items():
                if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool):
                    budgets[k] = int(v)
        return cls(loaded_models=tuple(names), max_models=budgets)


@dataclass(frozen=True)
class FlmMetrics:
    """Native perf fields emitted by FastFlowLM inside
    ``/v1/chat/completions`` response bodies (memory
    ``hal0_lemonade_flm_npu_install``).

    All five fields are optional per call — partial payloads still
    update what's present. ``kv_token_occupancy_rate_percentage`` is
    the ONLY KV% source in v0.2 (plan §12.1 — GPU slots get ``—``).
    """

    decoding_speed_tps: float | None = None
    prefill_speed_tps: float | None = None
    prefill_duration_ttft: float | None = None
    kv_token_occupancy_rate_percentage: float | None = None
    decoding_duration: float | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> FlmMetrics | None:
        """Sniff FLM-native fields from a chat-completion response body.

        Returns ``None`` if no FLM field is present — the call site uses
        that as the recipe discriminator (``payload sans FLM fields ==
        not a FLM response``). This is cleaner than passing a separate
        recipe argument from the dispatcher, which would require the
        hook to resolve recipe per upstream first.
        """
        if not isinstance(payload, dict):
            return None
        present = any(
            k in payload
            for k in (
                "decoding_speed_tps",
                "prefill_speed_tps",
                "prefill_duration_ttft",
                "kv_token_occupancy_rate_percentage",
                "decoding_duration",
            )
        )
        if not present:
            return None
        return cls(
            decoding_speed_tps=_coerce_float(payload.get("decoding_speed_tps")),
            prefill_speed_tps=_coerce_float(payload.get("prefill_speed_tps")),
            prefill_duration_ttft=_coerce_float(payload.get("prefill_duration_ttft")),
            kv_token_occupancy_rate_percentage=_coerce_float(
                payload.get("kv_token_occupancy_rate_percentage")
            ),
            decoding_duration=_coerce_float(payload.get("decoding_duration")),
        )


# ── MetricsShim ──────────────────────────────────────────────────────────


class MetricsShim:
    """Background-polled aggregator for Lemonade metrics + FLM-native
    response-body fields.

    Lifecycle:
        shim = MetricsShim(client)
        await shim.start()
        ...
        await shim.stop()

    The Prometheus exposition surface (:mod:`hal0.api.routes.metrics`)
    reads :meth:`snapshot` and renders text. FLM-native fields arrive
    out-of-band via :meth:`record_flm_metrics` from the v1 chat-completion
    hook.

    Mirrors :class:`hal0.lemonade.idle.IdleDriver` for task lifecycle —
    same start/stop contract, same cancellation discipline, same
    resilience to lemond hiccups.
    """

    def __init__(
        self,
        client: LemonadeClient,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        clock: Any = time.time,
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        self._client = client
        self._poll_interval_s = poll_interval_s
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

        # Snapshots are replaced atomically per tick. Readers (the
        # /metrics route) grab a reference and format synchronously —
        # no locking needed because Python's GIL makes the dict swap
        # atomic and the dataclasses themselves are frozen.
        self._stats: StatsSnapshot = StatsSnapshot()
        self._health: HealthSnapshot = HealthSnapshot()
        # FLM metrics keyed by (slot_name, model_name) so the same
        # model on two slots stays distinct. Tuple key is hashable and
        # serialises cleanly in tests. Bounded by the number of FLM
        # slots; cardinality is small (NPU exclusivity = at most one
        # FLM trio coresident at a time per plan §5).
        self._flm: dict[tuple[str, str], FlmMetrics] = {}
        # Wallclock timestamp of the last successful poll, surfaced as
        # ``hal0_lemonade_metrics_last_scrape_seconds`` so scrapers
        # can alert on stale data.
        self._last_poll_ts: float | None = None

    # ── lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Schedule the poll task. No-op if already running."""
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="lemonade-metrics-shim")
        log.info(
            "lemonade.metrics.started",
            extra={"poll_interval_s": self._poll_interval_s},
        )

    async def stop(self) -> None:
        """Signal the task to exit and await its completion. Idempotent."""
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("lemonade.metrics.stopped")

    # ── poll loop ──────────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover — defensive
                    log.warning(
                        "lemonade.metrics.tick_error",
                        extra={"error": str(exc), "error_type": type(exc).__name__},
                    )
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval_s)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            return

    async def tick(self) -> None:
        """Run one poll cycle: refresh stats + health snapshots.

        Public so tests can drive the loop deterministically. Never
        raises — lemond down / 5xx leaves the prior snapshot in place
        but updates the staleness timestamp (or rather, deliberately
        does NOT update it, so ``last_scrape_seconds`` drifts and
        alerts can fire).
        """
        stats_ok = await self._refresh_stats()
        health_ok = await self._refresh_health()
        if stats_ok or health_ok:
            self._last_poll_ts = float(self._clock())

    async def _refresh_stats(self) -> bool:
        try:
            payload = await self._client.stats()
        except (LemonadeUnavailableError, LemonadeTimeoutError) as exc:
            log.debug(
                "lemonade.metrics.stats_unreachable",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return False
        except LemonadeHTTPError as exc:
            log.warning(
                "lemonade.metrics.stats_http_error",
                extra={"status_code": exc.status_code},
            )
            return False
        except LemonadeError as exc:  # pragma: no cover — defensive
            log.warning("lemonade.metrics.stats_error", extra={"error": str(exc)})
            return False
        self._stats = StatsSnapshot.from_payload(payload)
        return True

    async def _refresh_health(self) -> bool:
        try:
            payload = await self._client.health()
        except (LemonadeUnavailableError, LemonadeTimeoutError) as exc:
            log.debug(
                "lemonade.metrics.health_unreachable",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return False
        except LemonadeHTTPError as exc:
            log.warning(
                "lemonade.metrics.health_http_error",
                extra={"status_code": exc.status_code},
            )
            return False
        except LemonadeError as exc:  # pragma: no cover — defensive
            log.warning("lemonade.metrics.health_error", extra={"error": str(exc)})
            return False
        self._health = HealthSnapshot.from_payload(payload)
        return True

    # ── FLM ingest (called from chat-completion dispatch hook) ─────

    def record_flm_metrics(
        self,
        slot_name: str,
        model_name: str,
        payload: Any,
    ) -> bool:
        """Sniff FLM-native fields from a chat-completion response body.

        Returns True if FLM fields were detected and stored, False
        otherwise. Callers can ignore the return value — the hook is
        wired unconditionally on every chat completion and only does
        work when the payload looks like a FLM response.

        Robustness contract: NEVER raises. Bad slot_name / model_name /
        payload yields a False return; the hook stays a no-op so the
        chat path is unaffected by metric-collection glitches.
        """
        if not isinstance(slot_name, str) or not slot_name:
            return False
        if not isinstance(model_name, str) or not model_name:
            return False
        metrics = FlmMetrics.from_payload(payload)
        if metrics is None:
            return False
        self._flm[(slot_name, model_name)] = metrics
        return True

    # ── snapshot accessor ──────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable snapshot for the Prometheus exposition
        route. Frozen dataclasses are converted to plain dicts so the
        renderer doesn't need to import shim internals.
        """
        return {
            "stats": {
                "time_to_first_token": self._stats.time_to_first_token,
                "tokens_per_second": self._stats.tokens_per_second,
                "input_tokens": self._stats.input_tokens,
                "output_tokens": self._stats.output_tokens,
                "prompt_tokens": self._stats.prompt_tokens,
            },
            "health": {
                "loaded_models": list(self._health.loaded_models),
                "max_models": dict(self._health.max_models),
            },
            "flm": {
                f"{slot}::{model}": {
                    "decoding_speed_tps": m.decoding_speed_tps,
                    "prefill_speed_tps": m.prefill_speed_tps,
                    "prefill_duration_ttft": m.prefill_duration_ttft,
                    "kv_token_occupancy_rate_percentage": m.kv_token_occupancy_rate_percentage,
                    "decoding_duration": m.decoding_duration,
                }
                for (slot, model), m in self._flm.items()
            },
            "last_poll_ts": self._last_poll_ts,
        }


# ── helpers ───────────────────────────────────────────────────────────


def _coerce_float(value: Any) -> float | None:
    """Best-effort numeric coerce. ``None`` / ``bool`` / non-numeric → None.

    bool is an int subclass — exclude explicitly so True/False can't
    masquerade as 1.0/0.0 in a stats payload.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None
