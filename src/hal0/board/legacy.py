"""One-shot converter: legacy hal0 board store → Hermes kanban (ADR-0046).

The Operator Board is now a live proxy to the Hermes kanban plugin and hal0's
own SQLite board store is gone. A box upgraded from a release that still held
cards in that store runs this once: every card is pushed into Hermes (keyed by
``idempotency_key`` so a rerun never duplicates), or — when Hermes is not
reachable — exported to a JSON file so nothing is lost. The legacy tables are
dropped only after every card is converted or the export file is on disk.
"""

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
from hal0.db.connection import connect, tx

log = structlog.get_logger(__name__)

_UNREACHABLE = (BoardUnreachable, BoardNotInstalled, BoardNoToken)

# Children first so foreign keys never block a drop.
_LEGACY_TABLES = (
    "board_legacy_map",
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


def _export(
    boards: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    export_dir: Path,
    now: Callable[[], float],
) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"board-legacy-export-{int(now())}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"boards": boards, "cards": cards}, indent=2, default=str))
    os.replace(tmp, path)
    log.info("board.legacy_exported", path=str(path), cards=len(cards))
    return path


async def _push(
    conn: sqlite3.Connection,
    client: BoardClient,
    boards: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    stamp: str,
) -> tuple[int, int]:
    """Push every card into Hermes; returns ``(converted, skipped)``.

    Rerun safety does not depend on Hermes: Hermes' idempotency lookup ignores
    archived tasks, so each created id is committed to ``board_legacy_map``
    (before the status PATCH). A mapped card whose Hermes task still exists is
    skipped entirely (its status PATCH is not re-sent); a 404 falls through to
    a fresh create. ``idempotency_key`` on creates is a second layer.
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
        legacy = by_slug.get(slug, {})
        await client.create_board(
            {
                "slug": slug,
                "name": legacy.get("name") or slug,
                "description": legacy.get("description") or "",
            }
        )
        have.add(slug)

    converted = skipped = 0
    for card in cards:
        slug = card["board_slug"]
        known = mapped.get(card["id"])
        try:
            await client.get_task(known or card["id"], board=slug)
            skipped += 1
            continue
        except BoardUpstreamError as exc:
            if exc.status != 404:
                raise
        created = await client.create_task(
            {
                "title": card["title"],
                "body": f"{card['body'] or ''}\n\nimported from hal0 board {stamp}",
                "assignee": card["assignee"],
                "priority": card["priority"],
                "tenant": card["tenant"],
                "idempotency_key": card["id"],
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
        try:
            await client.update_task(new_id, {"status": status}, board=slug)
        except BoardUpstreamError as exc:
            # The plugin may refuse to set `archived` over PATCH; the card still
            # exists in Hermes, so note it and carry on.
            if status != "archived" or exc.status >= 500:
                raise
            log.warning("board.legacy_archive_rejected", card=card["id"], status=exc.status)
        converted += 1
    return converted, skipped


async def convert_legacy_board(
    client: BoardClient | None,
    *,
    db_path: Path | None = None,
    export_dir: Path = Path("/var/lib/hal0/state"),
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Move legacy cards into Hermes (or a JSON export), then drop the tables."""
    result: dict[str, Any] = {"converted": 0, "skipped": 0, "exported": None, "dropped": False}
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
        export = client is None
        if client is not None:
            try:
                result["converted"], result["skipped"] = await _push(
                    conn, client, boards, cards, stamp
                )
            except _UNREACHABLE:
                export = True
            except BoardUpstreamError as exc:
                log.warning(
                    "board.legacy_aborted", error=str(exc), code=exc.code, details=exc.details
                )
                result["aborted"] = exc.code
                return result
        if export:
            result["exported"] = str(_export(boards, cards, export_dir, now))
        _drop_tables(conn)
        result["dropped"] = True
    return result


async def run_once(app: Any) -> None:
    """Run the converter at most once per process (safe under concurrent callers)."""
    state = app.state
    if getattr(state, "board_legacy_done", False):
        return
    lock = getattr(state, "board_legacy_lock", None)
    if lock is None:
        lock = state.board_legacy_lock = asyncio.Lock()
    async with lock:
        if getattr(state, "board_legacy_done", False):
            return
        try:
            client: BoardClient | None = resolve_board_client(app)
        except (BoardNotInstalled, BoardNoToken):
            client = None
        result = await convert_legacy_board(client)
        if result.get("aborted"):
            # Leave the flag unset so the next request retries.
            return
        state.board_legacy_done = True
        log.info("board.legacy_converted", **result)


__all__ = ["convert_legacy_board", "run_once"]
