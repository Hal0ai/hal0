"""Pure-Python unit tests for :mod:`hal0.agents.budget`.

Pins the dataclass + ledger + check semantics every paid surface (V1
OpenRouter, V2 fusion MCP) is going to lean on. Test ordering matches
the module's section layout: dataclass round-trip, parse helper,
ledger I/O, check matrix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hal0.agents.budget import (
    Budget,
    BudgetCheck,
    BudgetLedger,
    BudgetWindow,
    SpendRow,
    SpendStats,
    check_budget,
    ledger_for,
    parse_budget,
    record_charge,
    spend_stats,
)

# ── Budget dataclass ────────────────────────────────────────────────────────


def test_budget_default_is_empty() -> None:
    """A fresh Budget has every cap None and hard_cap defaulting to True."""
    b = Budget()
    assert b.daily_usd is None
    assert b.monthly_usd is None
    assert b.lifetime_usd is None
    assert b.per_call_max_usd is None
    assert b.hard_cap is True
    assert b.is_empty() is True


def test_budget_to_dict_omits_unset_caps() -> None:
    """Unset caps are skipped — the rendered TOML stays small + readable."""
    b = Budget(daily_usd=5.0, hard_cap=False)
    out = b.to_dict()
    assert out == {"daily_usd": 5.0, "hard_cap": False}
    assert "monthly_usd" not in out
    assert "lifetime_usd" not in out


def test_budget_to_dict_preserves_explicit_zero() -> None:
    """Explicit 0.0 ("block everything") survives round-trip."""
    b = Budget(daily_usd=0.0)
    out = b.to_dict()
    assert out["daily_usd"] == 0.0


# ── parse_budget ────────────────────────────────────────────────────────────


def test_parse_budget_none_returns_empty_budget() -> None:
    """Missing [persona.budget] sub-table is a no-op, not an error."""
    b = parse_budget(None)
    assert b == Budget()


def test_parse_budget_full_table() -> None:
    raw = {
        "daily_usd": 1.0,
        "monthly_usd": 10.0,
        "lifetime_usd": 100.0,
        "per_call_max_usd": 0.5,
        "hard_cap": False,
    }
    b = parse_budget(raw)
    assert b == Budget(
        daily_usd=1.0,
        monthly_usd=10.0,
        lifetime_usd=100.0,
        per_call_max_usd=0.5,
        hard_cap=False,
    )


def test_parse_budget_rejects_negative() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        parse_budget({"daily_usd": -1.0})


def test_parse_budget_rejects_non_number() -> None:
    with pytest.raises(ValueError, match="must be a number"):
        parse_budget({"daily_usd": "abc"})


def test_parse_budget_rejects_bool_for_number() -> None:
    """``daily_usd = true`` is a TOML mistake; reject it explicitly."""
    with pytest.raises(ValueError, match="bool"):
        parse_budget({"daily_usd": True})


def test_parse_budget_rejects_non_bool_hard_cap() -> None:
    with pytest.raises(ValueError, match="hard_cap"):
        parse_budget({"hard_cap": "yes"})


def test_parse_budget_ignores_unknown_keys() -> None:
    """Forward-compat: a v0.4 superset's extra knobs don't trip v0.3."""
    b = parse_budget({"daily_usd": 1.0, "weekly_usd": 9.99, "future_field": "x"})
    assert b == Budget(daily_usd=1.0)


def test_parse_budget_accepts_int_as_float() -> None:
    """TOML ints are valid amounts (``daily_usd = 5`` not just ``5.0``)."""
    b = parse_budget({"daily_usd": 5})
    assert b.daily_usd == 5.0


# ── SpendRow JSON round-trip ────────────────────────────────────────────────


def test_spend_row_round_trip() -> None:
    ts = datetime(2026, 5, 29, 12, 34, 56, tzinfo=UTC)
    row = SpendRow(
        ts=ts,
        persona_id="hermes",
        surface="openrouter",
        model="claude-3.7",
        cost_usd=0.0421,
        request_id="req-1",
    )
    decoded = SpendRow.from_json(row.to_json())
    assert decoded == row


def test_spend_row_tolerates_z_suffix() -> None:
    """``Z`` shorthand round-trips even if a future writer emits it."""
    line = '{"ts":"2026-05-29T12:00:00Z","persona_id":"h","surface":"or","model":"x","cost_usd":0.01,"request_id":"r1"}'
    row = SpendRow.from_json(line)
    assert row.ts.tzinfo is not None
    assert row.persona_id == "h"


# ── BudgetLedger round-trip ─────────────────────────────────────────────────


def test_ledger_round_trip_appends_and_reads(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    assert ledger.iter_rows() == []
    row = SpendRow(
        ts=datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
        persona_id="hermes",
        surface="openrouter",
        model="m1",
        cost_usd=0.1,
        request_id="r-1",
    )
    ledger.append(row)
    rows = ledger.iter_rows()
    assert len(rows) == 1
    assert rows[0] == row


def test_ledger_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "spend.jsonl"
    path.write_text(
        "not json\n"
        '{"ts":"2026-05-29T00:00:00+00:00","persona_id":"h","surface":"or","model":"m","cost_usd":0.5,"request_id":"r1"}\n'
        "\n"
        "{}\n",
        encoding="utf-8",
    )
    ledger = BudgetLedger(path)
    rows = ledger.iter_rows()
    assert len(rows) == 1
    assert rows[0].cost_usd == 0.5


def test_ledger_creates_parent_dirs_on_append(tmp_path: Path) -> None:
    """Fresh install: nobody has written the ledger dir yet — append seeds it."""
    deep = tmp_path / "a" / "b" / "c" / "spend.jsonl"
    ledger = BudgetLedger(deep)
    ledger.append(
        SpendRow(
            ts=datetime.now(UTC),
            persona_id="h",
            surface="or",
            model="m",
            cost_usd=0.01,
            request_id="r1",
        )
    )
    assert deep.exists()
    assert deep.parent.is_dir()


def test_ledger_for_resolves_canonical_layout(tmp_path: Path) -> None:
    ledger = ledger_for("hermes-agent", "hermes", root=tmp_path)
    assert ledger.path == tmp_path / "hermes-agent" / "personas" / "hermes" / "spend.jsonl"


# ── spend_stats ─────────────────────────────────────────────────────────────


def _row(
    *,
    ts: datetime,
    cost: float = 0.1,
    request_id: str = "r",
) -> SpendRow:
    return SpendRow(
        ts=ts,
        persona_id="hermes",
        surface="openrouter",
        model="m",
        cost_usd=cost,
        request_id=request_id,
    )


def test_spend_stats_aggregates_windows(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    # Today: 0.30
    ledger.append(_row(ts=now.replace(hour=1), cost=0.1, request_id="t1"))
    ledger.append(_row(ts=now.replace(hour=2), cost=0.2, request_id="t2"))
    # Yesterday but same month: 0.50
    ledger.append(_row(ts=now - timedelta(days=1), cost=0.5, request_id="y1"))
    # Previous month: 1.00
    ledger.append(_row(ts=now - timedelta(days=40), cost=1.0, request_id="m1"))
    stats = spend_stats(ledger, now=now)
    assert stats.today_usd == pytest.approx(0.3)
    assert stats.mtd_usd == pytest.approx(0.8)
    assert stats.lifetime_usd == pytest.approx(1.8)


def test_spend_stats_empty_ledger_zeroes(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    stats = spend_stats(ledger)
    assert stats == SpendStats(0.0, 0.0, 0.0)


# ── check_budget edge cases ─────────────────────────────────────────────────


def test_check_no_cap_allows_any_estimate(tmp_path: Path) -> None:
    """Empty Budget → unconditional allow + empty remaining."""
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    result = check_budget(Budget(), ledger, 5.0)
    assert result.allowed is True
    assert result.reason is None
    assert result.remaining_usd == {}


def test_check_daily_cap_blocks(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    budget = Budget(daily_usd=1.0)
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    # Already spent 0.7 today
    ledger.append(_row(ts=now.replace(hour=1), cost=0.7, request_id="t1"))
    # Estimate 0.5 would push us to 1.2 — blocked.
    result = check_budget(budget, ledger, 0.5, now=now)
    assert result.allowed is False
    assert result.reason is not None
    assert "daily cap" in result.reason
    # Remaining headroom snapshot still reflects the pre-call total.
    assert result.remaining_usd[BudgetWindow.DAILY.value] == pytest.approx(0.3)


def test_check_per_call_blocks_independent_of_window(tmp_path: Path) -> None:
    """A single oversized call breaches per_call_max even with empty ledger."""
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    budget = Budget(per_call_max_usd=0.05, daily_usd=100.0)
    result = check_budget(budget, ledger, 0.10)
    assert result.allowed is False
    assert "per-call cap" in (result.reason or "")


def test_check_hard_cap_false_warns_but_allows(tmp_path: Path) -> None:
    """``hard_cap=False`` → ``allowed`` stays True even when a cap would block."""
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    budget = Budget(daily_usd=1.0, hard_cap=False)
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    ledger.append(_row(ts=now.replace(hour=1), cost=0.9, request_id="t1"))
    result = check_budget(budget, ledger, 0.5, now=now)
    assert result.allowed is True
    # …but the reason is populated so the caller can log a warning.
    assert result.reason is not None
    assert "daily cap" in result.reason


def test_check_most_restrictive_wins(tmp_path: Path) -> None:
    """When daily + lifetime are both set, the FIRST breached wins the reason."""
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    budget = Budget(daily_usd=1.0, lifetime_usd=2.0)
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    ledger.append(_row(ts=now.replace(hour=1), cost=0.9, request_id="t1"))
    result = check_budget(budget, ledger, 0.5, now=now)
    # Daily breach fires before lifetime is even checked.
    assert result.allowed is False
    assert "daily cap" in (result.reason or "")


def test_check_day_boundary_aggregation(tmp_path: Path) -> None:
    """A charge from yesterday doesn't count against today's window."""
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    budget = Budget(daily_usd=1.0)
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    # 23 hours ago is still "today" UTC (now=noon, charge at 13:00 prev-day)
    # — actually 23 hours before noon is 13:00 yesterday, which is BEFORE
    # today's midnight floor (00:00 today). So this should NOT count.
    ledger.append(_row(ts=now - timedelta(hours=23), cost=10.0, request_id="y"))
    result = check_budget(budget, ledger, 0.5, now=now)
    assert result.allowed is True
    # Today's spent total is 0 (yesterday's 10.0 doesn't roll over);
    # remaining is the full daily cap.
    assert result.remaining_usd[BudgetWindow.DAILY.value] == pytest.approx(1.0)


def test_check_lifetime_breach(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    budget = Budget(lifetime_usd=10.0)
    now = datetime(2026, 5, 29, tzinfo=UTC)
    # Two months back — falls outside daily + monthly but counts lifetime.
    ledger.append(_row(ts=now - timedelta(days=70), cost=9.5, request_id="old"))
    result = check_budget(budget, ledger, 1.0, now=now)
    assert result.allowed is False
    assert "lifetime cap" in (result.reason or "")


def test_check_rejects_negative_estimate(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    with pytest.raises(ValueError):
        check_budget(Budget(), ledger, -0.01)


def test_check_returns_remaining_pre_call_headroom(
    tmp_path: Path,
) -> None:
    """remaining_usd is the operator-facing pre-call headroom per window."""
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    budget = Budget(daily_usd=10.0, monthly_usd=100.0)
    now = datetime(2026, 5, 29, tzinfo=UTC)
    ledger.append(_row(ts=now.replace(hour=1), cost=1.0, request_id="t1"))
    result = check_budget(budget, ledger, 2.0, now=now)
    assert result.allowed is True
    # Pre-call headroom: daily 10 - 1 spent = 9; monthly 100 - 1 = 99.
    assert result.remaining_usd[BudgetWindow.DAILY.value] == pytest.approx(9.0)
    assert result.remaining_usd[BudgetWindow.MONTHLY.value] == pytest.approx(99.0)


# ── record_charge ──────────────────────────────────────────────────────────


def test_record_charge_appends_with_resolved_timestamp(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    fixed = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    row = record_charge(
        ledger,
        persona_id="hermes",
        surface="openrouter",
        model="claude-3.7",
        cost_usd=0.05,
        request_id="r1",
        now=fixed,
    )
    assert row.ts == fixed
    saved = ledger.iter_rows()
    assert saved == [row]


def test_record_charge_round_trip_preserves_metadata(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "spend.jsonl")
    record_charge(
        ledger,
        persona_id="hermes",
        surface="fusion",
        model="meta/llama-3.1-405b",
        cost_usd=0.42,
        request_id="req-meaning-of-life",
    )
    rows = ledger.iter_rows()
    assert len(rows) == 1
    assert rows[0].surface == "fusion"
    assert rows[0].model == "meta/llama-3.1-405b"
    assert rows[0].cost_usd == pytest.approx(0.42)


# ── BudgetCheck shape ──────────────────────────────────────────────────────


def test_budget_check_dataclass_defaults() -> None:
    """remaining_usd defaults to empty dict — callers always get a mapping."""
    bc = BudgetCheck(allowed=True, reason=None)
    assert bc.remaining_usd == {}
