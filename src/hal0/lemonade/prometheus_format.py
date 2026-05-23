"""Prometheus text-exposition renderer for :mod:`hal0.lemonade.metrics_shim`.

Kept separate from the shim itself so:
  * MetricsShim stays focused on aggregation + lifecycle and is easy
    to unit-test without coupling to text-format quirks.
  * Adding new gauge families is a one-line append here, not a churn
    through the snapshot-storage layer.
  * Future swap to ``prometheus_client`` (currently NOT a hal0
    dependency — see plan §10.1 minimalism) only touches this module.

Format reference: Prometheus exposition format 0.0.4
(https://prometheus.io/docs/instrumenting/exposition_formats/). We emit
only ``# HELP`` / ``# TYPE`` headers and ``gauge`` samples — counters
are deferred to a later PR (architect.md research note proposed
``lemonade.load.attempts_total`` + ``lemonade.evict.nuclear_total``,
not in PR-12 scope).

Metric naming follows the ``hal0_lemonade_<noun>_<unit>`` convention so
all PR-12 series share a single prefix. Labels use the standard
``key="value"`` quoting; values are escaped per Prometheus spec
(backslash, double-quote, newline).
"""

from __future__ import annotations

from typing import Any

# Order matters for human-readable output: HELP/TYPE precede their
# samples, and we group related families together (stats then health
# then FLM). The Prometheus parser doesn't care about order, but
# diffing two scrapes visually is easier when families don't interleave.


def render_prometheus_exposition(snapshot: dict[str, Any] | None) -> str:
    """Render a :meth:`MetricsShim.snapshot` dict as Prometheus text format.

    ``None`` snapshot (shim not yet attached) → empty body. Empty body is
    a valid Prometheus exposition; scrapers treat it as "no series",
    which is the correct "no data yet" state on a fresh process before
    the first poll tick.

    The shim's snapshot may contain ``None`` field values when Lemonade
    hasn't served a request yet (``/v1/stats`` reports zeros / nulls).
    We skip ``None`` rows entirely rather than emit ``NaN`` so the
    dashboard's "—" placeholder is unambiguous (absence ≠ explicit NaN).
    """
    if snapshot is None:
        return ""

    lines: list[str] = []
    _emit_stats(lines, snapshot.get("stats") or {})
    _emit_health(lines, snapshot.get("health") or {})
    _emit_flm(lines, snapshot.get("flm") or {})
    _emit_last_poll(lines, snapshot.get("last_poll_ts"))

    # Prometheus convention: trailing newline so concatenating
    # exposition bodies (federation) doesn't accidentally fuse rows.
    if lines and not lines[-1].endswith("\n"):
        lines.append("")
    return "\n".join(lines)


# ── /v1/stats family ─────────────────────────────────────────────────


def _emit_stats(lines: list[str], stats: dict[str, Any]) -> None:
    """Emit ``hal0_lemonade_*`` gauges sourced from ``GET /v1/stats``.

    ``/v1/stats`` is process-wide and reports last-request perf; we tag
    every sample with ``source="last_request"`` so future per-model
    aggregations (when Lemonade adds per-model stats) can sit alongside
    without colliding on the unlabelled series.
    """
    ttft = stats.get("time_to_first_token")
    if ttft is not None:
        _emit_gauge(
            lines,
            name="hal0_lemonade_ttft_seconds",
            help_text="Time to first token from /v1/stats (last request, process-wide).",
            samples=[({"source": "last_request"}, float(ttft))],
        )

    tps = stats.get("tokens_per_second")
    if tps is not None:
        _emit_gauge(
            lines,
            name="hal0_lemonade_decode_tokens_per_second",
            help_text="Decode tokens-per-second from /v1/stats (last request, process-wide).",
            samples=[({"source": "last_request"}, float(tps))],
        )

    prompt_tokens = stats.get("prompt_tokens")
    if prompt_tokens is not None:
        _emit_gauge(
            lines,
            name="hal0_lemonade_prompt_tokens",
            help_text="Prompt-token count from /v1/stats (last request, process-wide).",
            samples=[({"source": "last_request"}, float(prompt_tokens))],
        )

    output_tokens = stats.get("output_tokens")
    if output_tokens is not None:
        # Plan §10.1 calls this ``hal0_lemonade_decode_tokens``; Lemonade
        # exposes the same number under ``output_tokens`` (research §5).
        # We follow the plan naming to match the rest of the prefix.
        _emit_gauge(
            lines,
            name="hal0_lemonade_decode_tokens",
            help_text="Decoded-token count from /v1/stats (last request, process-wide).",
            samples=[({"source": "last_request"}, float(output_tokens))],
        )

    input_tokens = stats.get("input_tokens")
    if input_tokens is not None:
        _emit_gauge(
            lines,
            name="hal0_lemonade_input_tokens",
            help_text="Input-token count from /v1/stats (last request, process-wide).",
            samples=[({"source": "last_request"}, float(input_tokens))],
        )


# ── /v1/health family ────────────────────────────────────────────────


def _emit_health(lines: list[str], health: dict[str, Any]) -> None:
    """Emit ``hal0_lemonade_models_loaded`` + ``hal0_lemonade_max_models``."""
    loaded = health.get("loaded_models") or []
    if isinstance(loaded, list) and loaded:
        samples: list[tuple[dict[str, str], float]] = []
        for name in loaded:
            if isinstance(name, str) and name:
                samples.append(({"model_name": name}, 1.0))
        if samples:
            _emit_gauge(
                lines,
                name="hal0_lemonade_models_loaded",
                help_text="Set membership: 1 if the model is currently loaded in Lemonade's pool.",
                samples=samples,
            )

    max_models = health.get("max_models") or {}
    if isinstance(max_models, dict) and max_models:
        samples = []
        for type_name, budget in sorted(max_models.items()):
            if not isinstance(type_name, str) or not type_name:
                continue
            if isinstance(budget, bool) or not isinstance(budget, (int, float)):
                continue
            samples.append(({"type": type_name}, float(budget)))
        if samples:
            _emit_gauge(
                lines,
                name="hal0_lemonade_max_models",
                help_text="Per-type concurrent-load budget from /v1/health.max_models.",
                samples=samples,
            )


# ── FLM native family ────────────────────────────────────────────────


def _emit_flm(lines: list[str], flm: dict[str, Any]) -> None:
    """Emit per-(slot, model) FLM-native gauges.

    Field set matches memory ``hal0_lemonade_flm_npu_install``:
    ``decoding_speed_tps``, ``prefill_speed_tps``,
    ``prefill_duration_ttft``, ``kv_token_occupancy_rate_percentage``,
    ``decoding_duration``.
    """
    if not isinstance(flm, dict) or not flm:
        return

    # Build per-metric sample lists so each metric family emits its
    # HELP/TYPE header once and groups all label combinations together.
    by_metric: dict[str, list[tuple[dict[str, str], float]]] = {
        "decoding_speed_tps": [],
        "prefill_speed_tps": [],
        "prefill_duration_ttft": [],
        "kv_token_occupancy_rate_percentage": [],
        "decoding_duration": [],
    }

    for key, payload in flm.items():
        if not isinstance(key, str) or "::" not in key:
            continue
        slot_name, model_name = key.split("::", 1)
        if not slot_name or not model_name or not isinstance(payload, dict):
            continue
        labels = {"slot_name": slot_name, "model_name": model_name}
        for metric_name, samples in by_metric.items():
            value = payload.get(metric_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                samples.append((labels, float(value)))

    # Metric naming mirrors the /v1/stats family where possible so a
    # dashboard query for "TTFT" can union both series.
    families: list[tuple[str, str, str]] = [
        (
            "decoding_speed_tps",
            "hal0_lemonade_flm_decode_tokens_per_second",
            "FLM native decode tok/s from chat-completion response body.",
        ),
        (
            "prefill_speed_tps",
            "hal0_lemonade_flm_prefill_tokens_per_second",
            "FLM native prefill tok/s from chat-completion response body.",
        ),
        (
            "prefill_duration_ttft",
            "hal0_lemonade_flm_ttft_seconds",
            "FLM native time to first token from chat-completion response body.",
        ),
        (
            "kv_token_occupancy_rate_percentage",
            "hal0_lemonade_kv_occupancy_ratio",
            (
                "FLM native KV cache occupancy ratio (0-100). "
                "GPU/llamacpp slots have no KV% in v0.2 (plan §12.1)."
            ),
        ),
        (
            "decoding_duration",
            "hal0_lemonade_flm_decode_duration_seconds",
            "FLM native total decode duration from chat-completion response body.",
        ),
    ]
    for field_key, metric_name, help_text in families:
        samples = by_metric[field_key]
        if samples:
            _emit_gauge(lines, name=metric_name, help_text=help_text, samples=samples)


# ── meta family ──────────────────────────────────────────────────────


def _emit_last_poll(lines: list[str], last_poll_ts: float | None) -> None:
    """Emit ``hal0_lemonade_metrics_last_scrape_seconds`` for staleness alerts."""
    if last_poll_ts is None:
        return
    _emit_gauge(
        lines,
        name="hal0_lemonade_metrics_last_scrape_seconds",
        help_text="Wallclock timestamp of the last successful MetricsShim poll.",
        samples=[({}, float(last_poll_ts))],
    )


# ── low-level text formatting ────────────────────────────────────────


def _emit_gauge(
    lines: list[str],
    *,
    name: str,
    help_text: str,
    samples: list[tuple[dict[str, str], float]],
) -> None:
    """Emit one gauge family — HELP, TYPE, then each sample line.

    Per spec: ``# HELP`` value spans to end-of-line; backslash and
    newline must be escaped. ``# TYPE`` is one of counter/gauge/etc.
    Sample format: ``<name>{<label>="<value>",...} <number>``.
    """
    lines.append(f"# HELP {name} {_escape_help(help_text)}")
    lines.append(f"# TYPE {name} gauge")
    for labels, value in samples:
        lines.append(f"{name}{_format_labels(labels)} {_format_value(value)}")


def _format_labels(labels: dict[str, str]) -> str:
    """Render a label dict as ``{k="v",...}``. Empty dict → empty string."""
    if not labels:
        return ""
    parts = [f'{k}="{_escape_label_value(v)}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _escape_help(text: str) -> str:
    """Escape ``\\`` and newline for HELP lines per the exposition spec."""
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _escape_label_value(value: str) -> str:
    """Escape ``\\``, ``"``, and newline for label values per spec."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_value(value: float) -> str:
    """Format a numeric sample. Integers stay integer-formatted to match
    Prometheus convention; floats use repr to preserve precision.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if value.is_integer():
        # Match the integer-output convention for whole-number gauges
        # (token counts, max_models) — easier to eyeball in raw scrapes.
        return str(int(value))
    return repr(value)
