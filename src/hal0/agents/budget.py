"""Per-persona spending-cap primitive (OpenRouter Phase 0).

Lands BEFORE the v0.3.x V1 OpenRouter upstream provider + the V2
``hal0-fusion`` MCP server. The DA review of the OpenRouter integration
plan flagged this as the P0 must-fix #3: without a spending-cap
envelope, fusion (4.4x cost vs single-model) combined with a recursing
Hermes loop could drain a $200/credit pool overnight. We need a budget
gate every paid surface can consult BEFORE it makes a call and a
post-response charge recorder so the next gate sees the real number.

Scope decision (PLANNING.md §5 Q2): per-persona only for v0.3. Per-agent
and platform-wide containing scopes are deferred to v0.4 — both are
strict supersets of this primitive and can wrap it without rewriting
the Budget dataclass shape.

Architecture
============

* :class:`Budget` is the configuration dataclass. Every cap is
  ``float | None``; ``None`` means "no cap on this window"; an explicit
  ``0.0`` means "blocked". ``hard_cap=True`` (the default) is "deny
  requests that would overshoot"; ``hard_cap=False`` is "log + allow
  (operator wants visibility, not enforcement)".
* :class:`BudgetLedger` is an append-only JSON-lines log at
  ``/var/lib/hal0/agents/{agent_id}/personas/{persona_id}/spend.jsonl``.
  One row per recorded charge — operator-inspectable with ``tail -f``
  + ``jq``, easy to migrate to SQLite later if we ever need indexed
  queries. No daemons, no cleanup cron, no lock files: append-only is
  the entire mutation model.
* :func:`check_budget` is a PURE function — aggregates the ledger's
  spend over each configured window and compares to caps. The caller
  (V1's OpenRouter provider) supplies the estimated cost; this module
  does not estimate. Decoupling estimation lets every paid surface
  pick its own estimator (token-count x price, or a fixed per-request
  fee, or whatever).
* :func:`record_charge` appends to the ledger with ``fsync`` so a
  crashed process between check + record loses at most one in-flight
  charge.

Eventual consistency
====================

The check-then-record pattern is NOT serialised. Two concurrent calls
from the same persona can both pass :func:`check_budget` (they read
the same ledger state), both make their requests, and both later
:func:`record_charge` for sums that together exceed the cap. This is
acceptable for v0.3: we tolerate periodic over-spend within a single
window in exchange for keeping the primitive lock-free and the ledger
shape trivially auditable. A real lock + a daemon-style enforcer is
v0.4+ work; the JSONL layout migrates cleanly.

Operator inspection
===================

The ledger format is one JSON object per line, sorted oldest-first by
append order. Each row carries::

    {
      "ts": "2026-05-29T00:00:00.000000+00:00",
      "persona_id": "hermes",
      "surface": "openrouter",
      "model": "anthropic/claude-3.7-sonnet",
      "cost_usd": 0.0421,
      "request_id": "req_abc123"
    }

``tail -f`` shows live charges; ``jq -s 'map(.cost_usd) | add'`` totals
the lifetime sum; ``jq -r 'select(.ts | startswith("2026-05-29"))
.cost_usd'`` slices a day. Operators get a debuggable surface without
hal0 needing a query engine.
"""

from __future__ import annotations

import contextlib
import enum
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Canonical on-disk store root for per-persona spend ledgers. Mirrors the
# personas store layout (``/var/lib/hal0/agents/{agent_id}/personas/...``)
# so a single operator backup picks up both knobs and history. Tests
# point at a tmp_path via the ``root`` keyword arg on every helper.
AGENTS_ROOT = Path("/var/lib/hal0/agents")
SPEND_LEDGER_FILENAME = "spend.jsonl"


class BudgetWindow(enum.StrEnum):
    """Aggregation windows the budget primitive understands.

    * ``daily`` — sums charges since 00:00 UTC of the current day.
    * ``monthly`` — sums charges since 00:00 UTC of the 1st of the
      current calendar month.
    * ``lifetime`` — sums every recorded charge.

    The enum exists so the API + UI have a stable string contract for
    the window selector; the dataclass itself stores caps per window as
    separate fields so the common "set them all at once" case stays
    ergonomic.
    """

    DAILY = "daily"
    MONTHLY = "monthly"
    LIFETIME = "lifetime"


@dataclass
class Budget:
    """Configuration block — one persona's spending caps.

    Every cap is ``float | None``; ``None`` means "no cap on this
    window". An explicit ``0.0`` means "block every paid request" (the
    operator deliberately fenced this persona off). ``hard_cap`` is the
    enforcement toggle: ``True`` (default) denies requests that would
    overshoot; ``False`` allows them through but :func:`check_budget`
    still reports the breach so the caller can log a warning.

    The dataclass round-trips through TOML — :func:`parse_budget` reads
    the ``[persona.budget]`` sub-table and :meth:`to_dict` writes it
    back. Empty / unset budget renders as an empty table so seed
    personas can ship an opt-in stub without setting any actual caps.
    """

    daily_usd: float | None = None
    monthly_usd: float | None = None
    lifetime_usd: float | None = None
    per_call_max_usd: float | None = None
    hard_cap: bool = True

    def is_empty(self) -> bool:
        """``True`` when no caps are configured (the seed-stub shape)."""
        return (
            self.daily_usd is None
            and self.monthly_usd is None
            and self.lifetime_usd is None
            and self.per_call_max_usd is None
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the TOML sub-table shape :func:`parse_budget` accepts.

        Unset caps (``None``) are omitted from the output so the
        rendered table stays small and the operator can tell at a
        glance which knobs are active. ``hard_cap`` is always written —
        its default (``True``) is the safer pick so explicit-False
        configurations are visible in the file.
        """
        out: dict[str, Any] = {}
        if self.daily_usd is not None:
            out["daily_usd"] = float(self.daily_usd)
        if self.monthly_usd is not None:
            out["monthly_usd"] = float(self.monthly_usd)
        if self.lifetime_usd is not None:
            out["lifetime_usd"] = float(self.lifetime_usd)
        if self.per_call_max_usd is not None:
            out["per_call_max_usd"] = float(self.per_call_max_usd)
        out["hard_cap"] = bool(self.hard_cap)
        return out


@dataclass
class BudgetCheck:
    """Outcome of :func:`check_budget`.

    * ``allowed`` — whether the caller should proceed. For
      ``hard_cap=False`` budgets, ``allowed`` is ``True`` even when a
      cap would have been breached; ``reason`` carries the would-be
      block message so the caller can log it.
    * ``reason`` — human-readable explanation, or ``None`` when the
      request is squarely within every configured cap. Set when a cap
      blocks (or would have blocked) the call.
    * ``remaining_usd`` — per-window remaining headroom. Keys are the
      window names from :class:`BudgetWindow`; values are
      ``cap - spent`` (clamped at ``0.0``). Windows with no cap are
      omitted from the dict — callers can iterate without checking for
      ``None``.
    """

    allowed: bool
    reason: str | None
    remaining_usd: dict[str, float] = field(default_factory=dict)


# ── parse helpers ──────────────────────────────────────────────────────────


def _parse_optional_float(value: Any, field_name: str) -> float | None:
    """Coerce a TOML scalar into ``float | None``.

    TOML emits ints + floats as distinct types; both are valid budget
    amounts. ``None`` (missing key) stays ``None``. Anything else is a
    :class:`ValueError` so the API + CLI can surface a structured 400
    instead of letting a bad TOML crash the agent loop later.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool is a subclass of int in Python; reject explicitly so an
        # operator who writes ``daily_usd = true`` gets a clear error.
        raise ValueError(f"[persona.budget].{field_name} must be a number, got bool")
    if isinstance(value, int | float):
        coerced = float(value)
        if coerced < 0:
            raise ValueError(f"[persona.budget].{field_name} must be >= 0")
        return coerced
    raise ValueError(f"[persona.budget].{field_name} must be a number, got {type(value).__name__}")


def parse_budget(toml_section: dict[str, Any] | None) -> Budget:
    """Build a :class:`Budget` from the parsed ``[persona.budget]`` table.

    Accepts ``None`` / missing section as "no budget configured" — the
    returned :class:`Budget` is empty (every cap ``None``,
    ``hard_cap=True``). Unknown extra keys are silently ignored so a
    future v0.4 superset (per-agent caps) can land additional knobs
    without breaking v0.3 personas.

    Raises :class:`ValueError` (caller wraps in ``PersonaError``) when
    a known field is the wrong type or negative.
    """
    if toml_section is None:
        return Budget()
    if not isinstance(toml_section, dict):
        raise ValueError("[persona.budget] must be a table")

    daily = _parse_optional_float(toml_section.get("daily_usd"), "daily_usd")
    monthly = _parse_optional_float(toml_section.get("monthly_usd"), "monthly_usd")
    lifetime = _parse_optional_float(toml_section.get("lifetime_usd"), "lifetime_usd")
    per_call = _parse_optional_float(toml_section.get("per_call_max_usd"), "per_call_max_usd")

    hard_cap_raw = toml_section.get("hard_cap", True)
    if not isinstance(hard_cap_raw, bool):
        raise ValueError("[persona.budget].hard_cap must be a bool")

    return Budget(
        daily_usd=daily,
        monthly_usd=monthly,
        lifetime_usd=lifetime,
        per_call_max_usd=per_call,
        hard_cap=hard_cap_raw,
    )


# ── ledger ─────────────────────────────────────────────────────────────────


def _ledger_path(agent_id: str, persona_id: str, *, root: Path | None = None) -> Path:
    """Resolve the spend ledger path for one (agent, persona)."""
    base = root if root is not None else AGENTS_ROOT
    return base / agent_id / "personas" / persona_id / SPEND_LEDGER_FILENAME


@dataclass(frozen=True)
class SpendRow:
    """One charge entry — what gets serialised to one JSONL line."""

    ts: datetime
    persona_id: str
    surface: str
    model: str
    cost_usd: float
    request_id: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts.astimezone(UTC).isoformat(),
                "persona_id": self.persona_id,
                "surface": self.surface,
                "model": self.model,
                "cost_usd": float(self.cost_usd),
                "request_id": self.request_id,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, line: str) -> SpendRow:
        body = json.loads(line)
        ts_raw = body["ts"]
        # ``fromisoformat`` accepts the offset suffix in 3.11+; tolerate
        # the ``Z`` shorthand a future writer might emit.
        if ts_raw.endswith("Z"):
            ts_raw = ts_raw[:-1] + "+00:00"
        return cls(
            ts=datetime.fromisoformat(ts_raw),
            persona_id=str(body["persona_id"]),
            surface=str(body["surface"]),
            model=str(body["model"]),
            cost_usd=float(body["cost_usd"]),
            request_id=str(body["request_id"]),
        )


class BudgetLedger:
    """Append-only JSON-lines spend log for one (agent, persona).

    Construct with the resolved path (call sites typically use
    :func:`ledger_for` to derive it). :meth:`append` writes one row +
    ``fsync``; :meth:`iter_rows` streams everything back. The ledger
    deliberately has NO compaction / rotation in v0.3 — the row size
    is on the order of 200 bytes and a heavy-use persona doing 1000
    paid calls a day still only writes ~70 MB/yr. Rotation lands when
    we ship per-agent scope in v0.4.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_parent(self) -> None:
        """Create the agent/persona dir tree if missing.

        Called from :meth:`append` before the first write so a fresh
        install (no operator-set budget yet) creates the directory
        lazily. Separate method so tests can pre-create the dir to
        inspect mode bits without triggering an actual append.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, row: SpendRow) -> None:
        """Append + fsync one row.

        The fsync is what guarantees the row survives a crash between
        the OpenRouter response landing and the next budget check; the
        cost (~5ms per call) is acceptable since paid calls already
        take 100s of milliseconds upstream. If fsync becomes a hot
        path later, batching is a v0.4 problem.
        """
        self.ensure_parent()
        line = row.to_json() + "\n"
        # Open + write + fsync + close. We deliberately open and close
        # per-row instead of keeping a long-lived fd so an operator
        # tail or rotate doesn't keep us on a deleted inode.
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            with contextlib.suppress(OSError):  # pragma: no cover — non-POSIX fs in tests
                os.fsync(fh.fileno())

    def iter_rows(self) -> list[SpendRow]:
        """Read every recorded row, oldest first.

        Returns an empty list when the ledger doesn't exist yet — that's
        the "no charges yet" state, not an error. Skips malformed lines
        with a structured log line so one bad write (a half-flushed row
        from a crashed process) doesn't blind every subsequent check.
        """
        if not self.path.exists():
            return []
        out: list[SpendRow] = []
        with open(self.path, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    out.append(SpendRow.from_json(line))
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    log.warning(
                        "budget.ledger.skip_malformed",
                        path=str(self.path),
                        lineno=lineno,
                        error=str(exc),
                    )
        return out


def ledger_for(
    agent_id: str,
    persona_id: str,
    *,
    root: Path | None = None,
) -> BudgetLedger:
    """Resolve the ledger for one (agent, persona). Convenience wrapper."""
    return BudgetLedger(_ledger_path(agent_id, persona_id, root=root))


# ── window aggregation ─────────────────────────────────────────────────────


def _day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class SpendStats:
    """Aggregated spend across the canonical windows.

    Returned by :func:`spend_stats` so API responses (and the UI editor)
    can render today / mtd / lifetime totals from one read pass over
    the ledger.
    """

    today_usd: float
    mtd_usd: float
    lifetime_usd: float


def spend_stats(ledger: BudgetLedger, now: datetime | None = None) -> SpendStats:
    """Aggregate the ledger over (today, month-to-date, lifetime).

    One pass over :meth:`BudgetLedger.iter_rows`. Cheap enough at v0.3
    volumes (kbs of rows) that we don't bother caching; if a heavy
    persona starts to thrash this we'll add a per-day cumulative
    sidecar in v0.4.
    """
    moment = now if now is not None else datetime.now(UTC)
    today_floor = _day_start(moment)
    month_floor = _month_start(moment)
    today = mtd = lifetime = 0.0
    for row in ledger.iter_rows():
        cost = float(row.cost_usd)
        lifetime += cost
        if row.ts >= today_floor:
            today += cost
        if row.ts >= month_floor:
            mtd += cost
    return SpendStats(today_usd=today, mtd_usd=mtd, lifetime_usd=lifetime)


# ── check + record ─────────────────────────────────────────────────────────


def check_budget(
    budget: Budget,
    ledger: BudgetLedger,
    estimated_cost_usd: float,
    *,
    now: datetime | None = None,
) -> BudgetCheck:
    """Pure check — does ``estimated_cost_usd`` fit inside the budget?

    Pre-call gate the V1 OpenRouter provider calls before issuing the
    upstream request. Returns :class:`BudgetCheck` with ``allowed``
    + a structured ``reason`` when a cap blocks the call. The
    ``remaining_usd`` map carries each configured window's headroom
    AFTER subtracting the estimated cost — callers can surface "$X.YZ
    left today" to the operator without re-aggregating themselves.

    Most-restrictive-wins ordering: per-call cap → daily → monthly →
    lifetime. The first breached cap wins the reason string; we don't
    enumerate every breach since the operator only sees one toast.

    ``hard_cap=False`` still computes the reason but keeps ``allowed``
    ``True`` — the caller is expected to log + proceed.
    """
    moment = now if now is not None else datetime.now(UTC)
    estimated = float(estimated_cost_usd)
    if estimated < 0:
        raise ValueError("estimated_cost_usd must be >= 0")

    stats = spend_stats(ledger, now=moment)
    remaining: dict[str, float] = {}
    reason: str | None = None

    # Per-call doesn't get a remaining entry — the cap is the call,
    # not a window.
    if budget.per_call_max_usd is not None and estimated > budget.per_call_max_usd:
        reason = (
            f"per-call cap ${budget.per_call_max_usd:.4f} exceeded by estimate ${estimated:.4f}"
        )

    if budget.daily_usd is not None:
        headroom = max(0.0, budget.daily_usd - stats.today_usd)
        # Remaining reflects PRE-call headroom — the operator UI surfaces
        # "you have $X.YZ left today", not "you'd have left if this call
        # went through". The check's allowed bool is the gate; remaining
        # is purely informational.
        remaining[BudgetWindow.DAILY.value] = headroom
        if reason is None and estimated > headroom:
            reason = (
                f"daily cap ${budget.daily_usd:.4f} would be exceeded — "
                f"spent ${stats.today_usd:.4f}, estimate ${estimated:.4f}"
            )

    if budget.monthly_usd is not None:
        headroom = max(0.0, budget.monthly_usd - stats.mtd_usd)
        remaining[BudgetWindow.MONTHLY.value] = headroom
        if reason is None and estimated > headroom:
            reason = (
                f"monthly cap ${budget.monthly_usd:.4f} would be exceeded — "
                f"spent ${stats.mtd_usd:.4f}, estimate ${estimated:.4f}"
            )

    if budget.lifetime_usd is not None:
        headroom = max(0.0, budget.lifetime_usd - stats.lifetime_usd)
        remaining[BudgetWindow.LIFETIME.value] = headroom
        if reason is None and estimated > headroom:
            reason = (
                f"lifetime cap ${budget.lifetime_usd:.4f} would be exceeded — "
                f"spent ${stats.lifetime_usd:.4f}, estimate ${estimated:.4f}"
            )

    if reason is None:
        return BudgetCheck(allowed=True, reason=None, remaining_usd=remaining)
    # Reason set: hard_cap decides whether we actually block.
    return BudgetCheck(
        allowed=not budget.hard_cap,
        reason=reason,
        remaining_usd=remaining,
    )


def record_charge(
    ledger: BudgetLedger,
    *,
    persona_id: str,
    surface: str,
    model: str,
    cost_usd: float,
    request_id: str,
    now: datetime | None = None,
) -> SpendRow:
    """Append a charge to the ledger; return the row that was written.

    Caller (the OpenRouter provider in V1) computes the real cost from
    the upstream's ``usage`` block + the model's posted price, then
    calls this once per response. The recorded row is the source of
    truth for the next :func:`check_budget` call.
    """
    moment = now if now is not None else datetime.now(UTC)
    row = SpendRow(
        ts=moment,
        persona_id=persona_id,
        surface=surface,
        model=model,
        cost_usd=float(cost_usd),
        request_id=request_id,
    )
    ledger.append(row)
    return row


__all__ = [
    "AGENTS_ROOT",
    "Budget",
    "BudgetCheck",
    "BudgetLedger",
    "BudgetWindow",
    "SpendRow",
    "SpendStats",
    "check_budget",
    "ledger_for",
    "parse_budget",
    "record_charge",
    "spend_stats",
]

# Anti-circular-import hint: also re-exposed as ``ledger_path`` for
# routes that need to surface the on-disk path without instantiating a
# ledger (CHANGELOG / debug response).
ledger_path = _ledger_path
