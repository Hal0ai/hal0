"""MetricsService -- the single object the app lifespan constructs + wires.

One instance per process, stored at ``app.state.metrics_service``, with
``app.state.metrics_seam`` aliased to ``service.seam`` so ``v1.py`` can
grab it without importing this whole module. Owns the writer + T1 seam +
T2 sampler + aggregator + retention background tasks and is the single
place that knows how to start/stop all of them together.

When ``[metrics].enabled = false`` (or ``HAL0_METRICS_ENABLED=0``),
``start()`` never launches any background task and ``seam.enabled`` is
False -- see :mod:`hal0.metrics.config` for the "near-zero when off"
contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from hal0.metrics.aggregator import MetricsAggregator
from hal0.metrics.config import MetricsSettings, load_metrics_settings
from hal0.metrics.retention import MetricsRetention
from hal0.metrics.sampler import SlotSampler
from hal0.metrics.seam import RequestSeam
from hal0.metrics.writer import MetricsWriter

if TYPE_CHECKING:
    from hal0.slots.manager import SlotManager

log = structlog.get_logger("hal0.metrics.service")


class MetricsService:
    def __init__(
        self,
        *,
        slot_manager: SlotManager | None = None,
        settings: MetricsSettings | None = None,
        db_path: Path | str | None = None,
        registry: object | None = None,
    ) -> None:
        self.settings = settings or load_metrics_settings()
        self.writer = MetricsWriter(
            db_path=db_path,
            queue_maxsize=self.settings.queue_maxsize,
            batch_size=self.settings.write_batch_size,
        )
        self.seam = RequestSeam(self.writer, enabled=self.settings.enabled)
        self._sampler: SlotSampler | None = None
        if slot_manager is not None:
            self._sampler = SlotSampler(
                slot_manager=slot_manager,
                writer=self.writer,
                interval_s=self.settings.sample_interval_s,
                registry=registry,
            )
        self._aggregator = MetricsAggregator(
            db_path=db_path, interval_s=self.settings.aggregate_interval_s
        )
        self._retention = MetricsRetention(
            db_path=db_path,
            interval_s=self.settings.retention_interval_s,
            request_days=self.settings.retention_request_days,
            slot_sample_days=self.settings.retention_slot_sample_days,
            rollup_days=self.settings.retention_rollup_days,
        )
        self._started = False

    def start(self) -> None:
        """Apply pending migrations + launch every background task.

        Never raises: an unwritable state root (a read-only test sandbox,
        a permissions problem on ``/var/lib/hal0``) degrades to "metrics
        stayed off" with a warning log, the same "housekeeping must never
        block startup" contract every other lifespan step in
        ``api/__init__.py`` already follows -- metrics observability must
        never be the reason the API itself fails to come up.
        """
        if not self.settings.enabled or self._started:
            return
        try:
            self.writer.start()
            if self._sampler is not None:
                self._sampler.start()
            self._aggregator.start()
            self._retention.start()
        except Exception as exc:
            log.warning(
                "metrics.service_start_failed", error=str(exc), error_type=type(exc).__name__
            )
            self.seam.enabled = False
            return
        self._started = True
        log.info(
            "metrics.service_started",
            sample_interval_s=self.settings.sample_interval_s,
            retention_request_days=self.settings.retention_request_days,
        )

    async def stop(self) -> None:
        if not self._started:
            return
        if self._sampler is not None:
            await self._sampler.stop()
        await self._aggregator.stop()
        await self._retention.stop()
        await self.writer.stop()
        self._started = False
        log.info("metrics.service_stopped")


__all__ = ["MetricsService"]
