"""The knowledge-page refresh policy: ``[memory.refresh]`` onto the engine (ADR-0060).

Hindsight 1.2 refreshes a mental model (a knowledge page is one) on its own
``trigger``: after every consolidation round (``refresh_after_consolidation``),
on a UTC cron (``refresh_cron``; the two are exclusive, and a tick is skipped
when the model is not stale), with a floor between automatic refreshes
(``min_refresh_interval_seconds``, falling back to the bank's
``mental_model_min_refresh_interval_seconds``). Each refresh is a multi-call
reflect run, minutes of the memory model's time; with every page on
``refresh_after_consolidation`` a busy box queued one per page after every
consolidation round, and the refreshes starved retains of the model.

hal0 owns those three timing fields. This module

* maps the config to the timing fields every page gets
  (:func:`desired_trigger`) and to the bank config that goes with them
  (:func:`bank_config_updates`): the floor, the default trigger the engine
  gives a page created without one (``knowledge_page_default_trigger``), and
  ``enable_auto_consolidation``;
* stamps them onto a trigger a writer sends (:func:`enforce_trigger`), which
  compat, the admin passthrough, the provider and the spec apply call;
* reconciles a running engine (:func:`apply_refresh_policy`): reads every
  mental model of each bank, PATCHes only those whose timing fields differ
  (the engine merges a PATCHed trigger over the stored one, so every other
  field, ``tag_groups``, ``budget``, ``mode``, ``fact_types``,
  ``tags_match``, ``exclude_*``, ``full_refresh_cron``, stays as it is), and
  then the bank config. It is idempotent, pages through the list, records an
  error per page, and raises nothing a caller has to catch.

What starts it: hal0-api at boot and on a ``memory.refresh.*`` save
(:mod:`hal0.memory.refresh_runner`), and ``hal0 memory refresh-policy apply``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import structlog

from hal0.config.cron import CronError, previous_fire

log = structlog.get_logger(__name__)

#: The trigger fields the policy owns. A writer's value for any of them is replaced.
TIMING_FIELDS: tuple[str, ...] = (
    "refresh_after_consolidation",
    "refresh_cron",
    "min_refresh_interval_seconds",
)
#: Bank config keys the policy writes.
FLOOR_KEY = "mental_model_min_refresh_interval_seconds"
DEFAULT_TRIGGER_KEY = "knowledge_page_default_trigger"
AUTO_CONSOLIDATION_KEY = "enable_auto_consolidation"

#: The engine's ``limit`` maximum for ``list_mental_models`` is 1000; a page
#: of 100 keeps one response (it carries each model's content) small.
LIST_PAGE = 100
#: Rows read from one bank before giving up on its end.
LIST_CAP = 10000

#: ``call(method, path, params=..., json_body=...)``: one engine request, JSON
#: back, ``httpx.HTTPStatusError`` on a non-2xx answer.
EngineCall = Callable[..., Awaitable[Any]]


def _policy_fields(policy: Any) -> tuple[str, str, int, str]:
    return (
        str(getattr(policy, "pages", "scheduled")),
        str(getattr(policy, "schedule", "0 3 * * *")),
        int(getattr(policy, "min_interval_s", 86400)),
        str(getattr(policy, "consolidation", "continuous")),
    )


#: How long past a scheduled run a stale page may wait before it counts as
#: having missed it: a refresh takes minutes, and the run starts a minute
#: after its tick at the latest.
STALE_MARGIN_S = 3600.0


def stale_grace_s(policy: Any, now: datetime) -> float | None:
    """How long a stale page may go without a refresh before health calls it overdue.

    Under ``pages = "scheduled"`` a page is stale from the moment facts in its
    scope change until the next run, so for most of every day by design. It
    has missed a run only once its last refresh is older than one schedule
    interval (the gap between the last two fires of ``schedule``), or the
    ``min_interval_s`` floor when that is longer, plus :data:`STALE_MARGIN_S`.
    ``None`` under ``after_consolidation`` and ``manual`` (any stale page
    counts) and for a schedule that cannot be read.
    """
    pages, schedule, floor, _ = _policy_fields(policy)
    if pages != "scheduled":
        return None
    try:
        last = previous_fire(schedule, now)
        before = previous_fire(schedule, last - timedelta(minutes=1))
    except CronError:
        return None
    return max((last - before).total_seconds(), float(floor)) + STALE_MARGIN_S


def desired_trigger(policy: Any) -> dict[str, Any]:
    """The timing fields every page gets under ``policy`` (a ``MemoryRefreshConfig``)."""
    pages, schedule, floor, _ = _policy_fields(policy)
    return {
        "refresh_after_consolidation": pages == "after_consolidation",
        "refresh_cron": schedule if pages == "scheduled" else None,
        "min_refresh_interval_seconds": floor,
    }


def _same_timing(current: Mapping[str, Any] | None, wanted: Mapping[str, Any]) -> bool:
    current = current if isinstance(current, Mapping) else {}
    return bool(
        bool(current.get("refresh_after_consolidation")) == wanted["refresh_after_consolidation"]
        and (current.get("refresh_cron") or None) == wanted["refresh_cron"]
        and current.get("min_refresh_interval_seconds") == wanted["min_refresh_interval_seconds"]
    )


def enforce_trigger(
    trigger: Mapping[str, Any] | None, policy: Any
) -> tuple[dict[str, Any], list[str]]:
    """``trigger`` with the policy's timing fields, and the fields it replaced.

    A replaced field is one the writer set to something else, named
    ``trigger.<field>`` (the spelling of compat's ``X-hal0-Ignored-Fields``).
    A field the writer left out is filled in and not reported.
    """
    out = dict(trigger) if isinstance(trigger, Mapping) else {}
    wanted = desired_trigger(policy)
    replaced: list[str] = []
    for key, value in wanted.items():
        if key in out and out[key] != value:
            # ``refresh_cron: ""`` and ``None`` are both "no schedule".
            if key == "refresh_cron" and (out[key] or None) == value:
                pass
            else:
                replaced.append(f"trigger.{key}")
        out[key] = value
    return out, replaced


def bank_config_updates(
    policy: Any, config: Mapping[str, Any] | None, *, owns_auto_off: bool = False
) -> dict[str, Any]:
    """The ``PATCH .../config`` updates that bring one bank to ``policy``. Pure.

    ``config`` is the engine's ``GET .../config`` answer (``config`` is the
    resolved config, ``overrides`` what was set on the bank). Only keys that
    differ are returned, so an empty dict means the bank is already there.

    ``enable_auto_consolidation`` is set to ``false`` for scheduled
    consolidation. For continuous it is cleared (JSON null, the engine's
    "server default" tombstone) only when ``owns_auto_off`` says hal0 set
    that override itself: an override someone else set (an operator turning
    consolidation off to drain a backlog) is left alone and reported
    (:func:`foreign_auto_off`). It is never set ``true``: a bank override of
    ``true`` would beat the engine-wide ``false`` the maintenance isolation
    drop-in sets while a fold-in or embedder switch runs
    (:mod:`hal0.memory.engine_maintenance`).
    """
    answer: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    raw_resolved, raw_overrides = answer.get("config"), answer.get("overrides")
    resolved: Mapping[str, Any] = raw_resolved if isinstance(raw_resolved, Mapping) else {}
    overrides: Mapping[str, Any] = raw_overrides if isinstance(raw_overrides, Mapping) else {}
    wanted = desired_trigger(policy)
    _, _, floor, consolidation = _policy_fields(policy)
    updates: dict[str, Any] = {}
    if resolved.get(FLOOR_KEY) != floor:
        updates[FLOOR_KEY] = floor
    default_trigger = resolved.get(DEFAULT_TRIGGER_KEY)
    if not _same_timing(default_trigger, wanted):
        base = dict(default_trigger) if isinstance(default_trigger, Mapping) else {}
        updates[DEFAULT_TRIGGER_KEY] = {**base, **wanted}
    override = overrides.get(AUTO_CONSOLIDATION_KEY)
    if consolidation == "scheduled":
        if override is not False:
            updates[AUTO_CONSOLIDATION_KEY] = False
    elif override is not None and owns_auto_off:
        updates[AUTO_CONSOLIDATION_KEY] = None
    return updates


def foreign_auto_off(
    bank: str, policy: Any, config: Mapping[str, Any] | None, *, owns_auto_off: bool
) -> str | None:
    """A note when the bank's auto-consolidation is off by an override hal0
    did not set while the policy wants it continuous, else ``None``."""
    _, _, _, consolidation = _policy_fields(policy)
    if consolidation != "continuous" or owns_auto_off:
        return None
    answer: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    raw = answer.get("overrides")
    overrides: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    if overrides.get(AUTO_CONSOLIDATION_KEY) is False:
        return (
            f"auto-consolidation is off on {bank} by an override hal0 did not set; "
            "hal0 leaves it alone"
        )
    return None


# ── which overrides hal0 wrote ─────────────────────────────────────────────


def owned_state_path() -> Path:
    """``/var/lib/hal0/state/memory-refresh-policy.json`` (under ``HAL0_HOME`` in a sandbox):
    the banks whose ``enable_auto_consolidation = false`` hal0 itself set."""
    from hal0.config.paths import var_lib

    return var_lib() / "state" / "memory-refresh-policy.json"


def load_owned_auto_off(path: Path | None = None) -> set[str]:
    try:
        data = json.loads((path or owned_state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    banks = data.get("auto_consolidation_off") if isinstance(data, dict) else None
    return {b for b in banks if isinstance(b, str)} if isinstance(banks, list) else set()


def save_owned_auto_off(banks: set[str], path: Path | None = None) -> None:
    target = path or owned_state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"auto_consolidation_off": sorted(banks)}, sort_keys=True), encoding="utf-8"
        )
        tmp.replace(target)
    except OSError as exc:
        log.warning("hal0.memory.refresh_policy_state_unsaved", error=str(exc))


def trigger_patch(trigger: Mapping[str, Any] | None, policy: Any) -> dict[str, Any] | None:
    """The trigger fields to PATCH onto a model with ``trigger``, or ``None``
    when its timing fields already match the policy. Only the timing fields
    are sent: the engine merges them over the stored trigger."""
    wanted = desired_trigger(policy)
    return None if _same_timing(trigger, wanted) else dict(wanted)


# ── the reconcile ──────────────────────────────────────────────────────────


@dataclass
class BankPolicyReport:
    """What :func:`apply_refresh_policy` did, or would do, to one bank."""

    bank: str
    models_seen: int = 0
    models_patched: list[str] = field(default_factory=list)
    models_unchanged: int = 0
    model_errors: list[dict[str, str]] = field(default_factory=list)
    config_updates: dict[str, Any] = field(default_factory=dict)
    config_error: str | None = None
    #: Set when the bank was not reconciled at all (missing, unreadable).
    skipped: str | None = None
    #: Set when the listing stopped before its end.
    list_error: str | None = None
    #: Things the operator should know that are not errors (a foreign override).
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.model_errors or self.config_error or self.list_error)


@dataclass
class PolicyReport:
    """One reconcile across banks. ``to_dict`` is the CLI's ``--json``."""

    dry_run: bool
    policy: dict[str, Any]
    trigger: dict[str, Any]
    banks: list[BankPolicyReport] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    #: Set when nothing was attempted (writes paused, no engine).
    deferred: str | None = None

    @property
    def ok(self) -> bool:
        return self.deferred is None and all(b.ok for b in self.banks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "policy": self.policy,
            "trigger": self.trigger,
            "deferred": self.deferred,
            "ok": self.ok,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "banks": [{**asdict(b), "ok": b.ok} for b in self.banks],
        }


def _bank_path(bank: str, suffix: str = "") -> str:
    return f"/v1/default/banks/{quote(bank, safe='')}{suffix}"


def _detail(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None and response is not None:
        text = ""
        try:
            text = str(response.text)[:200]
        except Exception:  # pragma: no cover - defensive
            text = ""
        return f"HTTP {status}: {text}".strip()
    return f"{type(exc).__name__}: {exc}"


def _status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


async def _bank_exists(call: EngineCall, bank: str) -> bool:
    """Whether the engine lists ``bank``. A ``GET .../config`` of a bank that
    does not exist is not something to probe with: the reconcile must never
    be what creates a bank."""
    page = await call("GET", "/v1/default/banks", params={"q": bank, "limit": 100})
    rows = page.get("banks") if isinstance(page, dict) else None
    return any(isinstance(r, dict) and r.get("bank_id") == bank for r in rows or [])


async def _model_trigger(call: EngineCall, bank: str, model_id: str) -> Any:
    try:
        one = await call("GET", _bank_path(bank, f"/mental-models/{quote(model_id, safe='')}"))
    except Exception:
        return None
    return one.get("trigger") if isinstance(one, dict) else None


async def _reconcile_models(
    call: EngineCall, bank: str, policy: Any, report: BankPolicyReport, *, dry_run: bool
) -> None:
    path = _bank_path(bank, "/mental-models")
    offset = 0
    patched: set[str] = set()
    while offset < LIST_CAP:
        try:
            page = await call(
                "GET", path, params={"detail": "content", "limit": LIST_PAGE, "offset": offset}
            )
        except Exception as exc:
            report.list_error = _detail(exc)
            return
        items = page.get("items") if isinstance(page, dict) else None
        if not isinstance(items, list):
            report.list_error = "the engine answered the mental model list with no items"
            return
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            model_id = item["id"]
            report.models_seen += 1
            trigger = item.get("trigger")
            if not isinstance(trigger, dict):
                # A row listed without its trigger is read on its own before it
                # is judged; otherwise it would be PATCHed on every run.
                trigger = await _model_trigger(call, bank, model_id)
            body = trigger_patch(trigger, policy)
            if body is None or model_id in patched:
                report.models_unchanged += 1
                continue
            if not dry_run:
                try:
                    await call(
                        "PATCH",
                        _bank_path(bank, f"/mental-models/{quote(model_id, safe='')}"),
                        json_body={"trigger": body},
                    )
                except Exception as exc:
                    report.model_errors.append({"id": model_id, "error": _detail(exc)})
                    continue
            patched.add(model_id)
            report.models_patched.append(model_id)
        offset += len(items)
        total = page.get("total")
        if not items or len(items) < LIST_PAGE or (isinstance(total, int) and offset >= total):
            return
    report.list_error = f"stopped after {LIST_CAP} mental models"


async def _reconcile_config(
    call: EngineCall,
    bank: str,
    policy: Any,
    report: BankPolicyReport,
    owned: set[str],
    *,
    dry_run: bool,
) -> None:
    """The bank's config; ``owned`` (the banks whose auto-consolidation
    override hal0 set) is updated in place when the write goes through."""
    try:
        config = await call("GET", _bank_path(bank, "/config"))
    except Exception as exc:
        report.config_error = _detail(exc)
        return
    mine = bank in owned
    updates = bank_config_updates(policy, config, owns_auto_off=mine)
    report.config_updates = updates
    note = foreign_auto_off(bank, policy, config, owns_auto_off=mine)
    if note:
        report.notes.append(note)
    if not updates or dry_run:
        return
    try:
        await call("PATCH", _bank_path(bank, "/config"), json_body={"updates": updates})
    except Exception as exc:
        report.config_error = _detail(exc)
        return
    if AUTO_CONSOLIDATION_KEY in updates:
        if updates[AUTO_CONSOLIDATION_KEY] is False:
            owned.add(bank)
        else:
            owned.discard(bank)


async def apply_refresh_policy(
    call: EngineCall,
    banks: Iterable[str],
    policy: Any,
    *,
    dry_run: bool = False,
    check_write_gate: bool = True,
    owned_path: Path | None = None,
) -> PolicyReport:
    """Bring ``banks`` to ``policy``. Never raises for an engine answer.

    The bank config goes after the models: a page created between the two
    gets the old default trigger and is caught by the next run, while the
    other order could leave every page on the old trigger with a new floor
    if the listing failed. A bank the engine does not list is skipped, never
    created. Nothing is written while ``hal0 memory embedder switch`` (or
    another maintenance run) holds the write gate; the report says so in
    ``deferred``. ``owned_path`` is where hal0 records the banks whose
    auto-consolidation override it set (:func:`owned_state_path` by default).
    """
    pages, schedule, floor, consolidation = _policy_fields(policy)
    report = PolicyReport(
        dry_run=dry_run,
        policy={
            "pages": pages,
            "schedule": schedule,
            "min_interval_s": floor,
            "consolidation": consolidation,
            "consolidation_schedule": str(getattr(policy, "consolidation_schedule", "")),
        },
        trigger=desired_trigger(policy),
    )
    if check_write_gate and not dry_run:
        from hal0.memory.write_gate import MemoryWritesPaused, require_writes_allowed

        try:
            require_writes_allowed()
        except MemoryWritesPaused as exc:
            report.deferred = exc.message
            report.finished_at = time.time()
            return report
    owned = load_owned_auto_off(owned_path)
    owned_before = set(owned)
    for bank in dict.fromkeys(b for b in banks if isinstance(b, str) and b):
        bank_report = BankPolicyReport(bank=bank)
        report.banks.append(bank_report)
        try:
            exists = await _bank_exists(call, bank)
        except Exception as exc:
            bank_report.skipped = f"the bank list could not be read: {_detail(exc)}"
            bank_report.list_error = bank_report.skipped
            continue
        if not exists:
            bank_report.skipped = "the engine has no such bank"
            continue
        await _reconcile_models(call, bank, policy, bank_report, dry_run=dry_run)
        await _reconcile_config(call, bank, policy, bank_report, owned, dry_run=dry_run)
    if owned != owned_before and not dry_run:
        save_owned_auto_off(owned, owned_path)
    report.finished_at = time.time()
    log.info(
        "hal0.memory.refresh_policy_applied",
        dry_run=dry_run,
        pages=pages,
        consolidation=consolidation,
        banks=[
            {
                "bank": b.bank,
                "seen": b.models_seen,
                "patched": len(b.models_patched),
                "errors": len(b.model_errors),
                "config": sorted(b.config_updates),
                "skipped": b.skipped,
                "notes": b.notes,
            }
            for b in report.banks
        ],
    )
    return report


# ── the policy in force, for writers that have no app ──────────────────────

_policy_cache: dict[str, Any] = {"key": None, "policy": None}


def current_policy() -> Any:
    """``[memory.refresh]`` from this box's ``hal0.toml``, cached on its mtime.

    For writers with no ``app.state`` (the provider, the spec apply). A
    ``hal0.toml`` that cannot be read gives the defaults: the policy is what
    keeps page refreshes rare, so an unreadable file must not hand a writer
    back its own trigger.
    """
    from hal0.config import paths
    from hal0.config.schema import MemoryRefreshConfig

    try:
        target = paths.hal0_toml()
        stat = target.stat()
        key: Any = (str(target), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if key is not None and _policy_cache["key"] == key and _policy_cache["policy"] is not None:
        return _policy_cache["policy"]
    try:
        from hal0.config.loader import load_hal0_config

        policy = load_hal0_config().memory.refresh
    except Exception as exc:
        log.warning("hal0.memory.refresh_policy_config_unreadable", error=str(exc))
        policy = MemoryRefreshConfig()
    _policy_cache["key"], _policy_cache["policy"] = key, policy
    return policy


__all__ = [
    "AUTO_CONSOLIDATION_KEY",
    "DEFAULT_TRIGGER_KEY",
    "FLOOR_KEY",
    "STALE_MARGIN_S",
    "TIMING_FIELDS",
    "BankPolicyReport",
    "EngineCall",
    "PolicyReport",
    "apply_refresh_policy",
    "bank_config_updates",
    "current_policy",
    "desired_trigger",
    "enforce_trigger",
    "foreign_auto_off",
    "load_owned_auto_off",
    "owned_state_path",
    "save_owned_auto_off",
    "stale_grace_s",
    "trigger_patch",
]
