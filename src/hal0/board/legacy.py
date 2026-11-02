"""One-shot converter: pre-1.8 hal0 board store → Hermes kanban (ADR-0048).

The Operator Board is now a live proxy to the Hermes kanban plugin and hal0's
own SQLite board store is gone. A box upgraded from a release that still held
cards in that store runs this once: every card is pushed into Hermes as a
``triage`` task (keyed by ``idempotency_key`` so a rerun never duplicates; the
dispatcher never claims a triage task, so an import cannot start work), then
moved to its old status.

Outcomes:

* Hermes not installed → every card is exported to a JSON file and the tables
  are dropped.
* Hermes unreachable or without a token (at start or mid-push) → the tables stay
  and the next run retries. The first deferral time is kept in
  ``board_legacy_state``; once an attempt is still unreachable
  ``LEGACY_DEFER_DEADLINE_S`` later, the cards are exported and the tables
  dropped.
* A lasting per-card 4xx → that card goes to the export file (with the upstream
  error) and the rest carry on. A 5xx aborts the run and is retried later.

The tables are dropped only when every card is converted or on disk in an
export file. Exported cards carry ``hermes_id`` when Hermes already holds a
task for them, so a manual re-import can skip those.
"""

# HAL0-SUNSET: v1.9.0  — one-shot converter for pre-1.8 board cards; delete the module and its tests once every supported box has run it.

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from hal0.board import (
    BoardNotInstalled,
    BoardNoToken,
    BoardUnreachable,
    BoardUpstreamError,
)
from hal0.board.client import BoardClient, resolve_board_client
from hal0.config.paths import var_lib
from hal0.db.connection import connect, tx

log = structlog.get_logger(__name__)

LEGACY_RETRY_COOLDOWN_S = 60.0
LEGACY_DEFER_DEADLINE_S = 24 * 60 * 60.0

_DEFERRABLE = (BoardUnreachable, BoardNoToken)

# Children first so foreign keys never block a drop.
_LEGACY_TABLES = (
    "board_legacy_map",
    "board_legacy_state",
    "card_event",
    "card_link",
    "card_comment",
    "card_run",
    "card",
    "board_orchestration",
    "board_profile",
    "board_column",
    "board",
)

_CONTRACT_STATUSES = frozenset(
    {"triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done", "archived"}
)


def _map_status(status: str | None) -> str:
    """Legacy status → a status Hermes accepts via PATCH."""
    if status == "running":
        return "ready"  # Hermes refuses `running`; the dispatcher claims it.
    if status in _CONTRACT_STATUSES:
        return str(status)
    return "triage"


def _drop_tables(conn: sqlite3.Connection) -> None:
    with tx(conn):
        for table in _LEGACY_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")


def _mapped_ids(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return {
            r["card_id"]: r["hermes_id"]
            for r in conn.execute("SELECT card_id, hermes_id FROM board_legacy_map")
        }
    except sqlite3.OperationalError:
        return {}


_EXPORT_PREFIX = "board-legacy-export-"  # scar-ok: one-shot pre-1.8 converter (HAL0-SUNSET v1.9.0)


def _export(
    conn: sqlite3.Connection,
    boards: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    export_dir: Path,
    now: Callable[[], float],
    *,
    reason: str,
) -> Path:
    mapped = _mapped_ids(conn)
    out = [{**c, "hermes_id": mapped[c["id"]]} if c["id"] in mapped else dict(c) for c in cards]
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"{_EXPORT_PREFIX}{int(now())}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"boards": boards, "cards": out}, indent=2, default=str))
    os.replace(tmp, path)
    log.info("board.legacy_exported", path=str(path), cards=len(out), reason=reason)
    return path


def _first_deferred_at(conn: sqlite3.Connection, now: float) -> float:
    """Return the first deferral time, recording ``now`` when there is none yet."""
    conn.execute("CREATE TABLE IF NOT EXISTS board_legacy_state (key TEXT PRIMARY KEY, value TEXT)")
    with tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO board_legacy_state (key, value) VALUES ('first_deferred_at', ?)",
            (str(int(now)),),
        )
    row = conn.execute(
        "SELECT value FROM board_legacy_state WHERE key = 'first_deferred_at'"
    ).fetchone()
    return float(row["value"])


async def _push(
    conn: sqlite3.Connection,
    client: BoardClient,
    boards: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    stamp: str,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Push every card into Hermes; returns ``(converted, skipped, failed)``.

    Rerun safety does not depend on Hermes: Hermes' idempotency lookup ignores
    archived tasks, so each created id is committed to ``board_legacy_map``
    (before the status PATCH). A mapped card whose Hermes task still exists is
    skipped entirely (its status PATCH is not re-sent); a 404 falls through to
    a fresh create. ``idempotency_key`` on creates is a second layer.

    A 4xx on one card's create or PATCH is lasting, so that card is recorded in
    ``failed`` (with the upstream status and message) and the push carries on;
    5xx and transport errors propagate.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS board_legacy_map "
        "(card_id TEXT PRIMARY KEY, hermes_id TEXT NOT NULL)"
    )
    mapped = {
        r["card_id"]: r["hermes_id"]
        for r in conn.execute("SELECT card_id, hermes_id FROM board_legacy_map")
    }
    listing = await client.list_boards()
    have = {b.get("slug") for b in (listing or {}).get("boards", [])}
    by_slug = {b["slug"]: b for b in boards}
    for slug in dict.fromkeys(c["board_slug"] for c in cards):
        if slug in have:
            continue
        prior = by_slug.get(slug, {})
        await client.create_board(
            {
                "slug": slug,
                "name": prior.get("name") or slug,
                "description": prior.get("description") or "",
            }
        )
        have.add(slug)

    converted = skipped = 0
    failed: list[dict[str, Any]] = []
    for card in cards:
        try:
            if await _push_card(conn, client, card, mapped, stamp):
                converted += 1
            else:
                skipped += 1
        except BoardUpstreamError as exc:
            if exc.status >= 500:
                raise
            log.warning("board.legacy_card_failed", card=card["id"], status=exc.status)
            failed.append({**card, "error": {"status": exc.status, "detail": str(exc)}})
    return converted, skipped, failed


async def _push_card(
    conn: sqlite3.Connection,
    client: BoardClient,
    card: dict[str, Any],
    mapped: dict[str, str],
    stamp: str,
) -> bool:
    """Push one card; ``False`` when Hermes already holds it."""
    slug = card["board_slug"]
    known = mapped.get(card["id"])
    try:
        await client.get_task(known or card["id"], board=slug)
        return False
    except BoardUpstreamError as exc:
        if exc.status != 404:
            raise
    created = await client.create_task(
        {
            "title": (card["title"] or "").strip() or f"hal0 card {card['id']}",
            "body": f"{card['body'] or ''}\n\nimported from hal0 board {stamp}",
            "assignee": card["assignee"],
            "priority": card["priority"],
            "tenant": card["tenant"],
            "idempotency_key": card["id"],
            # Triage never reaches the dispatcher; the PATCH below sets the status.
            "triage": True,
        },
        board=slug,
    )
    new_id = created["task"]["id"]
    with tx(conn):
        conn.execute(
            "INSERT OR REPLACE INTO board_legacy_map (card_id, hermes_id) VALUES (?, ?)",
            (card["id"], new_id),
        )
    status = _map_status(card["status"])
    if status == "triage":
        return True
    try:
        await client.update_task(new_id, {"status": status}, board=slug)
    except BoardUpstreamError as exc:
        # The plugin may refuse to set `archived` over PATCH; the card still
        # exists in Hermes, so note it and carry on.
        if status != "archived" or exc.status >= 500:
            raise
        log.warning("board.legacy_archive_rejected", card=card["id"], status=exc.status)
    return True


async def convert_legacy_board(
    client: BoardClient | None,
    *,
    db_path: Path | None = None,
    export_dir: Path | None = None,
    now: Callable[[], float] = time.time,
    defer: str | None = None,
) -> dict[str, Any]:
    """Move pre-1.8 cards into Hermes (or a JSON export), then drop the tables.

    ``client is None`` means Hermes is not installed (export now). ``defer``
    carries the code of a transient resolve failure (unreachable / no token).
    """
    result: dict[str, Any] = {"converted": 0, "skipped": 0, "exported": None, "dropped": False}
    out_dir = export_dir if export_dir is not None else var_lib() / "state"
    with connect(db_path) as conn:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='board'"
        ).fetchone()
        if present is None:
            return result
        has_card = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='card'"
        ).fetchone()
        cards = (
            [dict(r) for r in conn.execute("SELECT * FROM card ORDER BY created_at, id")]
            if has_card
            else []
        )
        boards = [dict(r) for r in conn.execute("SELECT * FROM board ORDER BY created_at, slug")]
        if not cards:
            _drop_tables(conn)
            result["dropped"] = True
            return result

        stamp = datetime.fromtimestamp(now(), UTC).strftime("%Y-%m-%d")
        export_cards = cards
        reason = "not_installed"
        deferred = defer
        if client is not None:
            try:
                converted, skipped, failed = await _push(conn, client, boards, cards, stamp)
            except _DEFERRABLE as exc:
                deferred = exc.code
            except BoardNotInstalled:
                pass  # export everything, as when there is no client
            except BoardUpstreamError as exc:
                log.warning(
                    "board.legacy_aborted", error=str(exc), code=exc.code, details=exc.details
                )
                result["aborted"] = exc.code
                return result
            else:
                result["converted"], result["skipped"] = converted, skipped
                if not failed:
                    _drop_tables(conn)
                    result["dropped"] = True
                    return result
                result["failed"] = len(failed)
                export_cards, reason = failed, "card_errors"
        if deferred is not None:
            first = _first_deferred_at(conn, now())
            if now() - first < LEGACY_DEFER_DEADLINE_S:
                log.warning("board.legacy_deferred", code=deferred)
                result["deferred"] = deferred
                return result
            reason = "deadline"
        result["exported"] = str(_export(conn, boards, export_cards, out_dir, now, reason=reason))
        _drop_tables(conn)
        result["dropped"] = True
    return result


async def run_once(app: Any) -> None:
    """Run the converter at most once per process (safe under concurrent callers).

    After an abort or deferral the next attempt waits ``LEGACY_RETRY_COOLDOWN_S``
    (``app.state.board_legacy_next_try``) so a busy API does not re-run the push
    on every request.
    """
    state = app.state
    if getattr(state, "board_legacy_done", False):
        return
    lock = getattr(state, "board_legacy_lock", None)
    if lock is None:
        lock = state.board_legacy_lock = asyncio.Lock()
    async with lock:
        if getattr(state, "board_legacy_done", False):
            return
        if time.time() < getattr(state, "board_legacy_next_try", 0.0):
            return
        client: BoardClient | None = None
        defer: str | None = None
        try:
            client = resolve_board_client(app)
        except BoardNotInstalled:
            pass
        except BoardNoToken as exc:
            defer = exc.code
        result = await convert_legacy_board(client, defer=defer)
        if result.get("aborted") or result.get("deferred"):
            # Leave the flag unset so a later request retries, after the cooldown.
            state.board_legacy_next_try = time.time() + LEGACY_RETRY_COOLDOWN_S
            return
        state.board_legacy_done = True
        log.info("board.legacy_converted", **result)


__all__ = ["convert_legacy_board", "run_once"]
