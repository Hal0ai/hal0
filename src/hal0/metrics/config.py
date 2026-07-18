"""Metrics configuration -- a standalone reader, not part of ``Hal0Config``.

``config/schema.py`` is owned by a concurrent lane (P2-device) for the
duration of this build, so :class:`MetricsSettings` is intentionally its
own small dataclass rather than a new section grafted onto
:class:`hal0.config.schema.Hal0Config`. It reads an optional ``[metrics]``
table straight out of ``hal0.toml`` via ``tomllib`` (best-effort, never
raises) with environment-variable overrides on top -- the same override
precedence shape the rest of hal0 uses for operator knobs. A follow-up
lane can fold this into the schema once P2-device lands; the field names
here already match what that migration would need.

Master on/off + "near-zero when off" (plan §13.5): when ``enabled`` is
False every background task (T1 seam, T2 sampler, aggregator, retention)
stays unconstructed -- there is no polling loop to disable, just an
absent one.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

from hal0.config import paths


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class MetricsSettings:
    """Operator-tunable knobs for the OBS-1 metrics core.

    Every field has a shipped-box-safe default; nothing here requires an
    operator to touch ``hal0.toml`` at all.
    """

    enabled: bool = True
    sample_interval_s: float = 5.0
    aggregate_interval_s: float = 3600.0
    retention_interval_s: float = 6 * 3600.0
    retention_request_days: int = 7
    retention_slot_sample_days: int = 3
    retention_rollup_days: int = 90
    #: Bounded queue depth for the async writer (plan §13.5 R1).
    queue_maxsize: int = 1024
    #: Rows drained per BEGIN IMMEDIATE transaction.
    write_batch_size: int = 64


def _read_toml_metrics_table() -> dict:
    """Best-effort read of ``[metrics]`` from hal0.toml. Never raises."""
    try:
        cfg_path = paths.etc() / "hal0.toml"
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    table = data.get("metrics")
    return table if isinstance(table, dict) else {}


def load_metrics_settings() -> MetricsSettings:
    """Resolve :class:`MetricsSettings` from TOML (best-effort) + env overrides.

    Precedence: environment variable > ``[metrics]`` TOML table > default.
    A malformed/missing TOML file degrades to defaults silently -- metrics
    configuration must never block API startup.
    """
    table = _read_toml_metrics_table()

    def pick_bool(key: str, env: str, default: bool) -> bool:
        base = bool(table.get(key, default)) if key in table else default
        return _env_bool(env, base)

    def pick_float(key: str, env: str, default: float) -> float:
        base = float(table[key]) if key in table else default
        return _env_float(env, base)

    def pick_int(key: str, env: str, default: int) -> int:
        base = int(table[key]) if key in table else default
        return _env_int(env, base)

    return MetricsSettings(
        enabled=pick_bool("enabled", "HAL0_METRICS_ENABLED", True),
        sample_interval_s=pick_float("sample_interval_s", "HAL0_METRICS_SAMPLE_INTERVAL_S", 5.0),
        aggregate_interval_s=pick_float(
            "aggregate_interval_s", "HAL0_METRICS_AGGREGATE_INTERVAL_S", 3600.0
        ),
        retention_interval_s=pick_float(
            "retention_interval_s", "HAL0_METRICS_RETENTION_INTERVAL_S", 6 * 3600.0
        ),
        retention_request_days=pick_int(
            "retention_request_days", "HAL0_METRICS_RETENTION_REQUEST_DAYS", 7
        ),
        retention_slot_sample_days=pick_int(
            "retention_slot_sample_days", "HAL0_METRICS_RETENTION_SLOT_SAMPLE_DAYS", 3
        ),
        retention_rollup_days=pick_int(
            "retention_rollup_days", "HAL0_METRICS_RETENTION_ROLLUP_DAYS", 90
        ),
        queue_maxsize=pick_int("queue_maxsize", "HAL0_METRICS_QUEUE_MAXSIZE", 1024),
        write_batch_size=pick_int("write_batch_size", "HAL0_METRICS_WRITE_BATCH_SIZE", 64),
    )


__all__ = ["MetricsSettings", "load_metrics_settings"]
