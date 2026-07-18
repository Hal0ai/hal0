"""hal0-owned Operator Board repository — the store behind ``/api/board/*``.

hal0 is authoritative for the board (rework R4 §Agents-and-brain). This module
is the single seam between the FROZEN FE↔BE wire contract (``ui/CONTRACTS.md``
"Operator Board", SPEC §4) and local SQLite (``db/migrations/005_board.sql``).
It replaces the Hermes-kanban proxy forward: the route layer
(:mod:`hal0.api.routes.board`) calls these methods instead of
``HermesKanbanClient.request_json`` and gets back the same shapes the proxy
used to relay verbatim.

Design (matches :class:`hal0.ports.authority.PortAuthority` /
:class:`hal0.registry.sqlite_store.SqliteModelRegistry`):

* stdlib ``sqlite3`` via :func:`hal0.db.connection.connect` — one fresh,
  PRAGMA-configured connection per call (WAL, ``foreign_keys=ON``,
  ``busy_timeout=5000``); writes go through ``BEGIN IMMEDIATE``
  (:func:`hal0.db.connection.tx`). No cached connection, no module singleton.
* :func:`hal0.db.migrate.migrate` runs on construction — idempotent, so every
  process boots the schema forward for free.
* Every mutation bumps a revision token (per-card for KB-6 ETag concurrency)
  and appends a ``card_event`` row so the ``/events`` WS reflects the write.

Concurrency / ETag (KB-6): :meth:`update_task` / :meth:`delete_task` accept an
``if_revision``; a mismatch raises :class:`hal0.errors.Conflict` (409). The
route maps that to the same JSON-error envelope every other write uses.

First-boot import (KB-4): :meth:`ensure_initialized` runs once per process. If
the store is empty and a reachable Hermes kanban client is supplied, it imports
the live board; if Hermes is absent/unreachable it seeds a clean empty default
board. Idempotent — guarded by "no board rows exist", so it never re-imports
once any board exists.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar

from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate
from hal0.errors import Conflict, NotFound

#: Frozen visible-lane order + labels (mirrors ui/CONTRACTS.md and the seed in
#: 005_board.sql). Kept here too so an empty board still answers /board with all
#: lanes present even before any card is written.
VISIBLE_STATUSES: tuple[str, ...] = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "running",
    "blocked",
    "review",
    "done",
)
ARCHIVED_STATUS = "archived"
VALID_STATUSES: frozenset[str] = frozenset((*VISIBLE_STATUSES, ARCHIVED_STATUS))

#: Slug of the default board seeded when Hermes is absent / the import is empty.
DEFAULT_BOARD_SLUG = "default"
DEFAULT_BOARD_NAME = "operator board"

#: Cap on how many of a card's most-recent events ride inline in its envelope.
_CARD_EVENTS_LIMIT = 50


def _now() -> float:
    return time.time()


def _new_card_id() -> str:
    """A prototype-shaped card id: ``t_`` + 8 hex chars."""
    return f"t_{secrets.token_hex(4)}"


class BoardStore:
    """SQLite repository for the Operator Board.

    The documented interface is exactly the proxy surface the route layer
    needs — reads (``get_board``/``get_task``/``list_boards``/…), audited
    mutations (``create_task``/``update_task``/…), and the ``/events`` cursor
    feed (``events_since``). Executor-flavoured calls (``dispatch_nudge``,
    ``specify``, ``decompose``) return contract-shaped acknowledgements and are
    where the KB-5 dispatch seam attaches; with no executor wired they are
    honest no-ops.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._init_lock = asyncio.Lock()
        self._initialized = False
        with connect(self._db_path) as conn:
            migrate(conn)

    # ── connection plumbing ──────────────────────────────────────────────

    @contextmanager
    def _read(self, conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with connect(self._db_path) as c:
                yield c

    @contextmanager
    def _write(self, conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with connect(self._db_path) as c, tx(c):
                yield c

    # ── first-boot initialisation ────────────────────────────────────────

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def ensure_initialized(self, client: Any | None = None) -> None:
        """Run the one-time first-boot import/seed (idempotent, once per process).

        ``client`` is the optional :class:`hal0.board.HermesKanbanClient`. When
        the store is empty and the client is reachable, the live Hermes board is
        imported; otherwise a clean empty default board is seeded. Safe to call
        on every request — the in-process flag short-circuits after the first,
        and the write itself re-checks emptiness under the lock, so a partial or
        failed import never leaves the board unusable.
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            if not self._is_empty():
                self._initialized = True
                return
            imported = None
            if client is not None:
                imported = await self._fetch_hermes_snapshot(client)
            self._seed(imported)
            self._initialized = True

    def _is_empty(self) -> bool:
        with self._read() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM board").fetchone()
        return int(row["n"]) == 0

    async def _fetch_hermes_snapshot(self, client: Any) -> dict[str, Any] | None:
        """Best-effort pull of the live Hermes board for the first-boot import.

        Returns ``{"boards": [...], "cards": {slug: [card,...]}}`` on success, or
        ``None`` when Hermes is absent/unreachable/returns nothing usable — the
        caller then seeds an empty board. Every upstream error is swallowed: a
        broken import must degrade to "clean empty board", never crash boot.
        """
        try:
            raw_boards = await client.request_json("GET", "/boards")
        except Exception:
            return None
        boards = _coerce_board_list(raw_boards)
        if not boards:
            return None
        cards: dict[str, list[dict[str, Any]]] = {}
        for board in boards:
            slug = board.get("slug")
            if not slug:
                continue
            try:
                raw = await client.request_json("GET", "/board", params={"board": slug})
            except Exception:
                cards[slug] = []
                continue
            cards[slug] = _coerce_card_list(raw)
        return {"boards": boards, "cards": cards}

    def _seed(self, imported: dict[str, Any] | None) -> None:
        """Seed the store from an import snapshot, or seed an empty default board."""
        with self._write() as conn:
            # Re-check under the write lock — a concurrent request may have seeded
            # while we were fetching from Hermes.
            if int(conn.execute("SELECT COUNT(*) AS n FROM board").fetchone()["n"]) > 0:
                return
            if not imported or not imported.get("boards"):
                self._insert_board_row(
                    conn,
                    slug=DEFAULT_BOARD_SLUG,
                    name=DEFAULT_BOARD_NAME,
                    icon="",
                    description="",
                    is_current=True,
                )
                return
            boards = imported["boards"]
            cards = imported.get("cards") or {}
            for idx, board in enumerate(boards):
                slug = board.get("slug")
                if not slug:
                    continue
                self._insert_board_row(
                    conn,
                    slug=slug,
                    name=str(board.get("name") or slug),
                    icon=str(board.get("icon") or ""),
                    description=str(board.get("desc") or board.get("description") or ""),
                    is_current=(idx == 0),
                )
                for card in cards.get(slug, []):
                    self._import_card(conn, slug, card)

    def _import_card(self, conn: sqlite3.Connection, slug: str, card: dict[str, Any]) -> None:
        status = card.get("status")
        if status not in VALID_STATUSES:
            status = "triage"
        cid = str(card.get("id") or _new_card_id())
        conn.execute(
            "INSERT OR IGNORE INTO card "
            "(id, board_slug, title, status, assignee, tenant, priority, workspace, "
            "created_by, body, block_reason, schedule, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                slug,
                str(card.get("title") or ""),
                status,
                card.get("assignee") or card.get("profile"),
                card.get("tenant"),
                int(card.get("priority") or 0),
                card.get("workspace"),
                card.get("created_by") or card.get("createdBy"),
                card.get("body") or card.get("desc"),
                card.get("block_reason") or card.get("blockReason"),
                card.get("schedule"),
                card.get("summary"),
            ),
        )

    # ── board reads ──────────────────────────────────────────────────────

    def get_board(
        self, *, board: str | None = None, include_archived: bool = False
    ) -> dict[str, Any]:
        """Return ``{"lanes": {status: [card,...]}}`` for the resolved board.

        Lanes are keyed by status in the frozen visible order; ``archived`` is
        appended only when ``include_archived``. An empty board still returns all
        lanes (empty lists) so the FE renders "— no tasks —" per lane.
        """
        with self._read() as conn:
            slug = self._resolve_board(conn, board)
            statuses = list(VISIBLE_STATUSES)
            if include_archived:
                statuses.append(ARCHIVED_STATUS)
            lanes: dict[str, list[dict[str, Any]]] = {s: [] for s in statuses}
            if slug is None:
                return {"lanes": lanes}
            rows = conn.execute(
                "SELECT * FROM card WHERE board_slug = ? ORDER BY priority DESC, created_at ASC",
                (slug,),
            ).fetchall()
            for row in rows:
                if row["status"] not in lanes:
                    continue
                lanes[row["status"]].append(self._serialize_card(conn, row))
        return {"lanes": lanes}

    def get_task(self, task_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        """Return the full canonical card envelope, or raise 404."""
        with self._read(conn) as c:
            row = c.execute("SELECT * FROM card WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise NotFound(f"board task {task_id!r} not found", code="board.task_not_found")
            return self._serialize_card(c, row)

    def get_task_log(self, task_id: str, *, tail: int | None = None) -> list[dict[str, Any]]:
        """Pull-only worker log — the card's run rows, most recent last."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM card_run WHERE card_id = ? ORDER BY at ASC, id ASC",
                (task_id,),
            ).fetchall()
        runs = [_run_row(r) for r in rows]
        if tail is not None and tail > 0:
            runs = runs[-tail:]
        return runs

    def list_boards(self) -> list[dict[str, Any]]:
        """The board switcher list: ``{slug, name, icon, desc, count, current}``."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT b.*, "
                "(SELECT COUNT(*) FROM card c WHERE c.board_slug = b.slug "
                " AND c.status != 'archived') AS count "
                "FROM board b ORDER BY b.created_at ASC"
            ).fetchall()
        return [
            {
                "slug": r["slug"],
                "name": r["name"],
                "icon": r["icon"],
                "desc": r["description"],
                "count": int(r["count"]),
                "current": bool(r["is_current"]),
            }
            for r in rows
        ]

    def list_profiles(self) -> list[dict[str, Any]]:
        """Profiles ``{id, label, count}`` — registry rows unioned with any
        profile referenced by a live card, count derived from cards."""
        with self._read() as conn:
            registry = {r["name"]: r["label"] for r in conn.execute("SELECT * FROM board_profile")}
            counts = {
                r["assignee"]: int(r["n"])
                for r in conn.execute(
                    "SELECT assignee, COUNT(*) AS n FROM card "
                    "WHERE assignee IS NOT NULL AND status != 'archived' GROUP BY assignee"
                )
            }
        names = sorted(set(registry) | set(counts))
        return [{"id": n, "label": registry.get(n) or n, "count": counts.get(n, 0)} for n in names]

    def list_assignees(self, *, board: str | None = None) -> list[dict[str, Any]]:
        """Distinct assignees ``{id, label}`` across the board (or all boards)."""
        with self._read() as conn:
            slug = self._resolve_board(conn, board) if board else None
            if slug is not None:
                rows = conn.execute(
                    "SELECT DISTINCT assignee FROM card "
                    "WHERE assignee IS NOT NULL AND board_slug = ? ORDER BY assignee",
                    (slug,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT DISTINCT assignee FROM card "
                    "WHERE assignee IS NOT NULL ORDER BY assignee"
                ).fetchall()
        return [{"id": r["assignee"], "label": r["assignee"]} for r in rows]

    def stats(self, *, board: str | None = None) -> dict[str, Any]:
        """``{total, by_status}`` — total excludes archived (frozen shape)."""
        with self._read() as conn:
            slug = self._resolve_board(conn, board) if board else None
            if slug is not None:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM card WHERE board_slug = ? GROUP BY status",
                    (slug,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM card GROUP BY status"
                ).fetchall()
        by_status = {s: 0 for s in (*VISIBLE_STATUSES, ARCHIVED_STATUS)}
        for r in rows:
            by_status[r["status"]] = int(r["n"])
        total = sum(v for s, v in by_status.items() if s != ARCHIVED_STATUS)
        return {"total": total, "by_status": by_status}

    def diagnostics(self) -> dict[str, Any]:
        """A real (non-stub) health signal derived from live counts."""
        with self._read() as conn:
            boards = int(conn.execute("SELECT COUNT(*) AS n FROM board").fetchone()["n"])
            cards = int(conn.execute("SELECT COUNT(*) AS n FROM card").fetchone()["n"])
            blocked = int(
                conn.execute("SELECT COUNT(*) AS n FROM card WHERE status = 'blocked'").fetchone()[
                    "n"
                ]
            )
        return {
            "ok": True,
            "store": "hal0-sqlite",
            "boards": boards,
            "cards": cards,
            "blocked": blocked,
        }

    def workers_active(self) -> list[dict[str, Any]]:
        """Active workers derived from cards in the ``running`` lane, by assignee."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT assignee, COUNT(*) AS n FROM card "
                "WHERE status = 'running' AND assignee IS NOT NULL GROUP BY assignee"
            ).fetchall()
        return [{"id": r["assignee"], "status": "active", "claimed": int(r["n"])} for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM card_run WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise NotFound(f"run {run_id!r} not found", code="board.run_not_found")
            return _run_row(row)

    def get_config(self) -> dict[str, Any]:
        """The 4 read-only orchestration config knobs (GET /config)."""
        with self._read() as conn:
            row = conn.execute("SELECT * FROM board_orchestration WHERE id = 1").fetchone()
        return {
            "tick_interval": row["tick_interval"],
            "failure_limit": row["failure_limit"],
            "claim_ttl": row["claim_ttl"],
            "max_in_flight": row["max_in_flight"],
        }

    def get_orchestration(self) -> dict[str, Any]:
        """The 4 editable knobs + the 4 read-only config knobs (GET /orchestration)."""
        with self._read() as conn:
            row = conn.execute("SELECT * FROM board_orchestration WHERE id = 1").fetchone()
        return {
            "orchestrator_profile": row["orchestrator_profile"],
            "default_assignee": row["default_assignee"],
            "auto_decompose": bool(row["auto_decompose"]),
            "auto_promote_children": bool(row["auto_promote_children"]),
            "tick_interval": row["tick_interval"],
            "failure_limit": row["failure_limit"],
            "claim_ttl": row["claim_ttl"],
            "max_in_flight": row["max_in_flight"],
        }

    # ── card mutations ───────────────────────────────────────────────────

    def create_task(self, body: dict[str, Any], *, board: str | None = None) -> dict[str, Any]:
        """Create a card; returns ``{"task": <card>}`` (frozen create shape)."""
        body = body or {}
        status = body.get("status") or "triage"
        if status not in VALID_STATUSES:
            status = "triage"
        cid = str(body.get("id") or _new_card_id())
        with self._write() as conn:
            slug = self._resolve_board(conn, board or body.get("board"), create_default=True)
            conn.execute(
                "INSERT INTO card "
                "(id, board_slug, title, status, assignee, tenant, priority, workspace, "
                "created_by, body, block_reason, schedule, summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    slug,
                    str(body.get("title") or ""),
                    status,
                    body.get("assignee") or body.get("profile"),
                    body.get("tenant"),
                    int(body.get("priority") or 0),
                    body.get("workspace"),
                    body.get("created_by"),
                    body.get("body") or body.get("desc"),
                    body.get("block_reason"),
                    body.get("schedule"),
                    body.get("summary"),
                ),
            )
            self._append_event(conn, slug, cid, "created", {"status": status})
            card = self.get_task(cid, conn=conn)
        return {"task": card}

    #: Card columns a PATCH body may set directly (camelCase aliases accepted).
    _PATCHABLE: ClassVar[dict[str, str]] = {
        "title": "title",
        "status": "status",
        "assignee": "assignee",
        "profile": "assignee",
        "tenant": "tenant",
        "priority": "priority",
        "workspace": "workspace",
        "body": "body",
        "desc": "body",
        "block_reason": "block_reason",
        "blockReason": "block_reason",
        "schedule": "schedule",
        "summary": "summary",
    }

    def update_task(
        self,
        task_id: str,
        patch: dict[str, Any],
        *,
        if_revision: int | None = None,
    ) -> dict[str, Any]:
        """Apply a partial update, bump the card revision, append an event.

        KB-6: when ``if_revision`` is given and does not match the card's
        current revision, raise :class:`hal0.errors.Conflict` (409) — an
        edit-vs-edit race the caller must re-read and retry. Returns the updated
        card envelope (its new ``revision`` is the fresh ETag).
        """
        patch = patch or {}
        with self._write() as conn:
            row = conn.execute(
                "SELECT board_slug, status, revision FROM card WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"board task {task_id!r} not found", code="board.task_not_found")
            self._check_revision(row["revision"], if_revision)
            assignments, params, applied = self._build_patch(patch)
            if assignments:
                conn.execute(
                    f"UPDATE card SET {', '.join(assignments)}, "
                    "updated_at = ?, revision = revision + 1 WHERE id = ?",
                    (*params, _now(), task_id),
                )
            else:
                conn.execute(
                    "UPDATE card SET updated_at = ?, revision = revision + 1 WHERE id = ?",
                    (_now(), task_id),
                )
            self._append_event(conn, row["board_slug"], task_id, "updated", applied)
            return self.get_task(task_id, conn=conn)

    def delete_task(self, task_id: str, *, if_revision: int | None = None) -> dict[str, Any]:
        """Delete a card (comments/links/runs/events cascade). 404 if absent."""
        with self._write() as conn:
            row = conn.execute(
                "SELECT board_slug, revision FROM card WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"board task {task_id!r} not found", code="board.task_not_found")
            self._check_revision(row["revision"], if_revision)
            conn.execute("DELETE FROM card WHERE id = ?", (task_id,))
            self._append_event(conn, row["board_slug"], task_id, "deleted", {})
        return {"ok": True, "id": task_id}

    def comment_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Append a comment; returns the updated card envelope."""
        body = body or {}
        with self._write() as conn:
            row = conn.execute("SELECT board_slug FROM card WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise NotFound(f"board task {task_id!r} not found", code="board.task_not_found")
            conn.execute(
                "INSERT INTO card_comment (card_id, author, body, at) VALUES (?, ?, ?, ?)",
                (task_id, body.get("author"), str(body.get("body") or ""), _now()),
            )
            conn.execute(
                "UPDATE card SET updated_at = ?, revision = revision + 1 WHERE id = ?",
                (_now(), task_id),
            )
            self._append_event(conn, row["board_slug"], task_id, "commented", {})
            return self.get_task(task_id, conn=conn)

    def bulk_update(self, body: dict[str, Any]) -> dict[str, Any]:
        """Apply the same patch to many cards; returns ``{"updated": n}``."""
        body = body or {}
        ids = body.get("ids") or []
        patch = {k: v for k, v in body.items() if k != "ids"}
        updated = 0
        with self._write() as conn:
            for task_id in ids:
                row = conn.execute(
                    "SELECT board_slug FROM card WHERE id = ?", (str(task_id),)
                ).fetchone()
                if row is None:
                    continue
                assignments, params, applied = self._build_patch(patch)
                if assignments:
                    conn.execute(
                        f"UPDATE card SET {', '.join(assignments)}, "
                        "updated_at = ?, revision = revision + 1 WHERE id = ?",
                        (*params, _now(), str(task_id)),
                    )
                    self._append_event(conn, row["board_slug"], str(task_id), "updated", applied)
                    updated += 1
        return {"updated": updated}

    def reassign(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Reassign a card's owner (board state, not an executor call)."""
        body = body or {}
        assignee = body.get("profile") or body.get("assignee")
        return self.update_task(task_id, {"assignee": assignee})

    def reclaim(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Reclaim a stuck card back to ``ready`` and clear its block reason."""
        return self.update_task(task_id, {"status": "ready", "block_reason": None})

    def add_link(self, body: dict[str, Any]) -> dict[str, Any]:
        """Add a parent->child dependency edge."""
        body = body or {}
        parent_id = body.get("parent_id")
        child_id = body.get("child_id")
        if not parent_id or not child_id:
            raise Conflict("link requires parent_id and child_id", code="board.link_invalid")
        with self._write() as conn:
            for cid in (parent_id, child_id):
                if conn.execute("SELECT 1 FROM card WHERE id = ?", (cid,)).fetchone() is None:
                    raise NotFound(f"board task {cid!r} not found", code="board.task_not_found")
            conn.execute(
                "INSERT OR IGNORE INTO card_link (parent_id, child_id) VALUES (?, ?)",
                (parent_id, child_id),
            )
            slug_row = conn.execute(
                "SELECT board_slug FROM card WHERE id = ?", (child_id,)
            ).fetchone()
            self._append_event(
                conn, slug_row["board_slug"], child_id, "link_added", {"parent": parent_id}
            )
        return {"ok": True, "parent_id": parent_id, "child_id": child_id}

    def remove_link(self, parent_id: str, child_id: str) -> dict[str, Any]:
        """Remove a parent->child dependency edge (ids ride as query params)."""
        with self._write() as conn:
            conn.execute(
                "DELETE FROM card_link WHERE parent_id = ? AND child_id = ?",
                (parent_id, child_id),
            )
            slug_row = conn.execute(
                "SELECT board_slug FROM card WHERE id = ?", (child_id,)
            ).fetchone()
            if slug_row is not None:
                self._append_event(
                    conn, slug_row["board_slug"], child_id, "link_removed", {"parent": parent_id}
                )
        return {"ok": True, "parent_id": parent_id, "child_id": child_id}

    # ── board / profile / orchestration mutations ────────────────────────

    def create_board(self, body: dict[str, Any]) -> dict[str, Any]:
        body = body or {}
        slug = body.get("slug")
        if not slug:
            raise Conflict("board requires a slug", code="board.slug_required")
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM board WHERE slug = ?", (slug,)).fetchone() is not None:
                raise Conflict(f"board {slug!r} already exists", code="board.exists")
            is_first = int(conn.execute("SELECT COUNT(*) AS n FROM board").fetchone()["n"]) == 0
            self._insert_board_row(
                conn,
                slug=slug,
                name=str(body.get("name") or slug),
                icon=str(body.get("icon") or ""),
                description=str(body.get("desc") or body.get("description") or ""),
                is_current=is_first,
            )
        return {"slug": slug, "name": body.get("name") or slug}

    def update_board(self, slug: str, body: dict[str, Any]) -> dict[str, Any]:
        body = body or {}
        fields = {
            "name": "name",
            "icon": "icon",
            "desc": "description",
            "description": "description",
        }
        assignments, params = [], []
        for key, col in fields.items():
            if key in body:
                assignments.append(f"{col} = ?")
                params.append(body[key])
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM board WHERE slug = ?", (slug,)).fetchone() is None:
                raise NotFound(f"board {slug!r} not found", code="board.not_found")
            if assignments:
                conn.execute(
                    f"UPDATE board SET {', '.join(assignments)}, "
                    "updated_at = ?, revision = revision + 1 WHERE slug = ?",
                    (*params, _now(), slug),
                )
        return {"ok": True, "slug": slug}

    def delete_board(self, slug: str) -> dict[str, Any]:
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM board WHERE slug = ?", (slug,)).fetchone() is None:
                raise NotFound(f"board {slug!r} not found", code="board.not_found")
            was_current = bool(
                conn.execute("SELECT is_current FROM board WHERE slug = ?", (slug,)).fetchone()[0]
            )
            conn.execute("DELETE FROM board WHERE slug = ?", (slug,))
            if was_current:
                nxt = conn.execute(
                    "SELECT slug FROM board ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if nxt is not None:
                    conn.execute("UPDATE board SET is_current = 1 WHERE slug = ?", (nxt["slug"],))
        return {"ok": True, "slug": slug}

    def switch_board(self, slug: str) -> dict[str, Any]:
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM board WHERE slug = ?", (slug,)).fetchone() is None:
                raise NotFound(f"board {slug!r} not found", code="board.not_found")
            conn.execute("UPDATE board SET is_current = 0")
            conn.execute("UPDATE board SET is_current = 1 WHERE slug = ?", (slug,))
        return {"ok": True, "slug": slug, "current": slug}

    def update_profile(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        body = body or {}
        with self._write() as conn:
            conn.execute(
                "INSERT INTO board_profile (name, label, description) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "label = COALESCE(excluded.label, board_profile.label), "
                "description = COALESCE(excluded.description, board_profile.description)",
                (name, body.get("label") or name, body.get("description")),
            )
        return {"ok": True, "name": name}

    def update_orchestration(self, body: dict[str, Any]) -> dict[str, Any]:
        """PUT the 4 editable knobs (config knobs stay read-only)."""
        body = body or {}
        fields = {
            "orchestrator_profile": "orchestrator_profile",
            "default_assignee": "default_assignee",
            "auto_decompose": "auto_decompose",
            "auto_promote_children": "auto_promote_children",
        }
        assignments, params = [], []
        for key, col in fields.items():
            if key in body:
                assignments.append(f"{col} = ?")
                val = body[key]
                params.append(int(val) if isinstance(val, bool) else val)
        with self._write() as conn:
            if assignments:
                conn.execute(
                    f"UPDATE board_orchestration SET {', '.join(assignments)} WHERE id = 1",
                    tuple(params),
                )
        return self.get_orchestration()

    # ── executor seam (KB-5) — contract-shaped no-ops without an executor ─

    def dispatch_nudge(self, *, max_dispatch: int | None = None) -> dict[str, Any]:
        """One-shot dispatcher nudge. Routes through the KB-5 dispatch seam;
        with no executor wired, honestly reports zero dispatched."""
        return {"dispatched": 0}

    def specify(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Refine a card into a spec — an executor/agent action (KB-5 seam)."""
        self._touch(task_id, "specify")
        return {"ok": True, "id": task_id}

    def decompose(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Decompose a card into children — an executor/agent action (KB-5 seam)."""
        self._touch(task_id, "decompose")
        return {"ok": True, "id": task_id}

    # ── events feed (WS) ─────────────────────────────────────────────────

    def events_since(
        self, cursor: int, *, board: str | None = None, limit: int = 200
    ) -> tuple[list[dict[str, Any]], int]:
        """Return ``(events, new_cursor)`` for the ``/events`` WS.

        ``events`` are ``card_event`` rows with ``cursor`` > the given value,
        oldest first, optionally board-filtered. ``new_cursor`` is the max
        cursor returned (or the input cursor when nothing is new), which the WS
        echoes in the frame and the browser passes back as ``?since=``.
        """
        with self._read() as conn:
            if board:
                rows = conn.execute(
                    "SELECT * FROM card_event WHERE cursor > ? AND board_slug = ? "
                    "ORDER BY cursor ASC LIMIT ?",
                    (cursor, board, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM card_event WHERE cursor > ? ORDER BY cursor ASC LIMIT ?",
                    (cursor, limit),
                ).fetchall()
        events = [
            {
                "id": int(r["cursor"]),
                "kind": r["kind"],
                "task_id": r["card_id"],
                "board": r["board_slug"],
                "at": r["at"],
                "json": r["json"],
            }
            for r in rows
        ]
        new_cursor = events[-1]["id"] if events else cursor
        return events, new_cursor

    def latest_cursor(self) -> int:
        with self._read() as conn:
            row = conn.execute("SELECT MAX(cursor) AS c FROM card_event").fetchone()
        return int(row["c"]) if row["c"] is not None else 0

    # ── internals ────────────────────────────────────────────────────────

    def _touch(self, task_id: str, kind: str) -> None:
        with self._write() as conn:
            row = conn.execute("SELECT board_slug FROM card WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise NotFound(f"board task {task_id!r} not found", code="board.task_not_found")
            conn.execute(
                "UPDATE card SET updated_at = ?, revision = revision + 1 WHERE id = ?",
                (_now(), task_id),
            )
            self._append_event(conn, row["board_slug"], task_id, kind, {})

    def _build_patch(self, patch: dict[str, Any]) -> tuple[list[str], list[Any], dict[str, Any]]:
        """Map a PATCH body onto ``(assignments, params, applied)``.

        ``applied`` is the column->value dict that actually landed, used as the
        event payload. An unknown/invalid status is rejected.
        """
        assignments: list[str] = []
        params: list[Any] = []
        applied: dict[str, Any] = {}
        for key, col in self._PATCHABLE.items():
            if key not in patch or col in applied:
                continue
            val = patch[key]
            if col == "status" and val not in VALID_STATUSES:
                raise Conflict(f"invalid status {val!r}", code="board.invalid_status")
            assignments.append(f"{col} = ?")
            params.append(val)
            applied[col] = val
        return assignments, params, applied

    @staticmethod
    def _check_revision(current: int, if_revision: int | None) -> None:
        if if_revision is not None and int(current) != int(if_revision):
            raise Conflict(
                "stale board write — card was modified since it was read",
                code="board.stale_write",
                details={"expected_revision": if_revision, "current_revision": int(current)},
            )

    def _insert_board_row(
        self,
        conn: sqlite3.Connection,
        *,
        slug: str,
        name: str,
        icon: str,
        description: str,
        is_current: bool,
    ) -> None:
        if is_current:
            conn.execute("UPDATE board SET is_current = 0")
        conn.execute(
            "INSERT INTO board (slug, name, icon, description, is_current) VALUES (?, ?, ?, ?, ?)",
            (slug, name, icon, description, 1 if is_current else 0),
        )

    def _resolve_board(
        self, conn: sqlite3.Connection, board: str | None, *, create_default: bool = False
    ) -> str | None:
        """Resolve the target board slug: explicit ``board`` wins, else the
        current board. ``create_default`` seeds the default board on demand for a
        write into an otherwise-empty store."""
        if board:
            return board
        row = conn.execute("SELECT slug FROM board WHERE is_current = 1 LIMIT 1").fetchone()
        if row is not None:
            return row["slug"]
        any_row = conn.execute("SELECT slug FROM board ORDER BY created_at ASC LIMIT 1").fetchone()
        if any_row is not None:
            return any_row["slug"]
        if create_default:
            self._insert_board_row(
                conn,
                slug=DEFAULT_BOARD_SLUG,
                name=DEFAULT_BOARD_NAME,
                icon="",
                description="",
                is_current=True,
            )
            return DEFAULT_BOARD_SLUG
        return None

    def _append_event(
        self,
        conn: sqlite3.Connection,
        board_slug: str | None,
        card_id: str | None,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            "INSERT INTO card_event (board_slug, card_id, kind, json, at) VALUES (?, ?, ?, ?, ?)",
            (board_slug, card_id, kind, json.dumps(payload, separators=(",", ":")), _now()),
        )

    def _serialize_card(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        cid = row["id"]
        parents = [
            r["parent_id"]
            for r in conn.execute(
                "SELECT parent_id FROM card_link WHERE child_id = ? ORDER BY parent_id", (cid,)
            )
        ]
        children = [
            r["child_id"]
            for r in conn.execute(
                "SELECT child_id FROM card_link WHERE parent_id = ? ORDER BY child_id", (cid,)
            )
        ]
        comments = [
            {"author": r["author"], "at": r["at"], "body": r["body"]}
            for r in conn.execute(
                "SELECT * FROM card_comment WHERE card_id = ? ORDER BY at ASC, id ASC", (cid,)
            )
        ]
        events = [
            {"kind": r["kind"], "at": r["at"], "json": r["json"]}
            for r in conn.execute(
                "SELECT * FROM card_event WHERE card_id = ? ORDER BY cursor DESC LIMIT ?",
                (cid, _CARD_EVENTS_LIMIT),
            )
        ]
        events.reverse()
        runs = [
            _run_row(r)
            for r in conn.execute(
                "SELECT * FROM card_run WHERE card_id = ? ORDER BY at ASC, id ASC", (cid,)
            )
        ]
        done_children = 0
        if children:
            placeholders = ",".join("?" for _ in children)
            done_children = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM card WHERE id IN ({placeholders}) AND status = 'done'",
                    tuple(children),
                ).fetchone()["n"]
            )
        dep_count = f"{done_children}/{len(children)}" if children else None
        return {
            "id": cid,
            "title": row["title"],
            "status": row["status"],
            "assignee": row["assignee"],
            "profile": row["assignee"],
            "tenant": row["tenant"],
            "priority": row["priority"],
            "workspace": row["workspace"],
            "created_by": row["created_by"],
            "created": row["created_at"],
            "updated_at": row["updated_at"],
            "body": row["body"],
            "block_reason": row["block_reason"],
            "schedule": row["schedule"],
            "summary": row["summary"],
            "revision": int(row["revision"]),
            "deps": {"parents": parents, "children": children},
            "comments": comments,
            "events": events,
            "runs": runs,
            "comment_count": len(comments),
            "dep_count": dep_count,
        }


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "state": row["state"],
        "profile": row["profile"],
        "dur": row["dur"],
        "at": row["at"],
        "msg": row["msg"],
    }


def _coerce_board_list(raw: Any) -> list[dict[str, Any]]:
    """Normalise the Hermes ``GET /boards`` response into a list of dicts."""
    if isinstance(raw, list):
        return [b for b in raw if isinstance(b, dict)]
    if isinstance(raw, dict):
        for key in ("boards", "items", "results"):
            if isinstance(raw.get(key), list):
                return [b for b in raw[key] if isinstance(b, dict)]
    return []


def _coerce_card_list(raw: Any) -> list[dict[str, Any]]:
    """Normalise the Hermes ``GET /board`` response (any of its shapes) into a
    flat list of card dicts — mirrors useBoard.ts's four-shape tolerance."""
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        if isinstance(raw.get("lanes"), dict):
            out: list[dict[str, Any]] = []
            for cards in raw["lanes"].values():
                if isinstance(cards, list):
                    out.extend(c for c in cards if isinstance(c, dict))
            return out
        if isinstance(raw.get("columns"), list):
            out = []
            for col in raw["columns"]:
                if isinstance(col, dict) and isinstance(col.get("tasks"), list):
                    out.extend(c for c in col["tasks"] if isinstance(c, dict))
            return out
        for key in ("tasks", "cards"):
            if isinstance(raw.get(key), list):
                return [c for c in raw[key] if isinstance(c, dict)]
    return []


__all__ = [
    "ARCHIVED_STATUS",
    "DEFAULT_BOARD_SLUG",
    "VALID_STATUSES",
    "VISIBLE_STATUSES",
    "BoardStore",
]
