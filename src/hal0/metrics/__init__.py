"""hal0.metrics -- the OBS-1 observability core (plan §13).

One measurement seam (T1, ``seam.RequestSeam``) + one background sampler
(T2, ``sampler.SlotSampler``) write into the same SQLite substrate
``hal0.db`` already provides (ML-1), through a single bounded async
writer (``writer.MetricsWriter``) so metrics writes never contend with
each other or block a request. ``service.MetricsService`` is the one
object the app lifespan constructs; ``read`` backs the §21.3 read API
(``GET /api/stats`` / ``/api/system-stats`` / ``/api/models/health``).

Zero-dep, zero-config core (plan §13.1): everything here is stdlib +
``hal0.db`` (stdlib ``sqlite3``). Prometheus/Grafana/Langfuse companion
exports are out of scope for this module.
"""

from __future__ import annotations

from hal0.metrics.config import MetricsSettings, load_metrics_settings
from hal0.metrics.seam import RequestSeam
from hal0.metrics.service import MetricsService
from hal0.metrics.writer import MetricsWriter

__all__ = [
    "MetricsService",
    "MetricsSettings",
    "MetricsWriter",
    "RequestSeam",
    "load_metrics_settings",
]
