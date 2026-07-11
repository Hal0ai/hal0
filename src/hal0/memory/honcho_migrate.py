"""Bidirectional Hindsight <-> Honcho memory migration + graph-sync engine.

Two directions, both loopback REST, both idempotent:

* :func:`migrate_hindsight_to_honcho` — one-time backfill. Pages
  ``GET /api/memory/list`` (hal0-api, the Hindsight-backed provider) and
  writes Honcho ``conclusions`` in batches of <=100. Idempotency is
  per-dataset ``migrated_ids`` in :class:`MigrateState`.

* :func:`migrate_honcho_to_hindsight` — the "graph-sync" direction, also
  usable as a recurring job (``hal0 memory sync-graph``). Pages Honcho
  ``conclusions/list`` newest activity and writes ``POST /api/memory/add``
  against hal0-api. Idempotency is a single ``created_at`` watermark; the
  migration-created sessions from the forward direction are skipped so the
  two directions never loop.

Both ends are loopback REST with no required auth (ADR-0012 posture), so
this module talks plain ``httpx.Client`` — no SDK dependency, no auth
plumbing, and trivially fakeable in tests via ``httpx.MockTransport``
threaded through as an injected client.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

DEFAULT_STATE_PATH = Path("/var/lib/hal0/honcho/migrate-state.json")

#: Honcho conclusion `content` is embedding-token-limited (~8192 tokens);
#: this is a conservative char budget, not a tokenizer.
_MAX_CONTENT_CHARS = 60_000

_CONCLUSION_BATCH = 100

#: Session-name prefix stamped on every hindsight->honcho migration session,
#: so the reverse (honcho->hindsight) direction can recognise + skip its own
#: migrated data and never round-trip it back.
_MIGRATION_SESSION_PREFIX = "migration__hindsight__"

ProgressFn = Callable[[str], None]


def _noop_progress(_msg: str) -> None:
    return None


# ── state file ──────────────────────────────────────────────────────────────


class MigrateState:
    """JSON-backed migration bookkeeping, one file for both directions.

    Shape::

        {
          "hindsight_to_honcho": {
            "<dataset>": {"cursor": str|null, "migrated_ids": [...], "count": int}
          },
          "honcho_to_hindsight": {"watermark": str|null, "count": int}
        }

    Loaded eagerly on construction; callers call :meth:`save` explicitly
    after mutating (matches the CLI's "run, then persist" flow — no
    autosave-per-item, so a killed process just re-scans a bit on resume
    rather than corrupting half-written state).
    """

    def __init__(self, path: Path | str = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"hindsight_to_honcho": {}, "honcho_to_hindsight": {"watermark": None, "count": 0}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"hindsight_to_honcho": {}, "honcho_to_hindsight": {"watermark": None, "count": 0}}
        raw.setdefault("hindsight_to_honcho", {})
        raw.setdefault("honcho_to_hindsight", {"watermark": None, "count": 0})
        return raw

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    # -- hindsight -> honcho side -------------------------------------------

    def dataset_state(self, dataset: str) -> dict[str, Any]:
        return self.data["hindsight_to_honcho"].setdefault(
            dataset, {"cursor": None, "migrated_ids": [], "count": 0}
        )

    def migrated_ids(self, dataset: str) -> set[str]:
        return set(self.dataset_state(dataset)["migrated_ids"])

    def mark_migrated(self, dataset: str, ids: Iterable[str]) -> None:
        st = self.dataset_state(dataset)
        seen = set(st["migrated_ids"])
        for i in ids:
            if i not in seen:
                st["migrated_ids"].append(i)
                seen.add(i)
        st["count"] = len(st["migrated_ids"])

    # -- honcho -> hindsight side --------------------------------------------

    def watermark(self) -> str | None:
        return self.data["honcho_to_hindsight"].get("watermark")

    def set_watermark(self, value: str) -> None:
        self.data["honcho_to_hindsight"]["watermark"] = value

    def bump_count(self, n: int) -> None:
        self.data["honcho_to_hindsight"]["count"] = (
            self.data["honcho_to_hindsight"].get("count", 0) + n
        )


# ── honcho REST helpers ──────────────────────────────────────────────────────


def _honcho_client(honcho_base: str, http_client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if http_client is not None:
        return http_client, False
    return httpx.Client(base_url=honcho_base.rstrip("/"), timeout=10.0), True


def _ensure_workspace(client: httpx.Client, workspace: str) -> None:
    client.post("/v3/workspaces", json={"id": workspace}).raise_for_status()


def _ensure_peer(client: httpx.Client, workspace: str, peer: str) -> None:
    client.post(f"/v3/workspaces/{workspace}/peers", json={"id": peer}).raise_for_status()


def _ensure_session(client: httpx.Client, workspace: str, session: str) -> None:
    client.post(f"/v3/workspaces/{workspace}/sessions", json={"id": session}).raise_for_status()


def _create_conclusions(
    client: httpx.Client, workspace: str, conclusions: list[dict[str, Any]]
) -> None:
    for i in range(0, len(conclusions), _CONCLUSION_BATCH):
        batch = conclusions[i : i + _CONCLUSION_BATCH]
        resp = client.post(
            f"/v3/workspaces/{workspace}/conclusions", json={"conclusions": batch}
        )
        resp.raise_for_status()


def _session_name(dataset: str) -> str:
    return _MIGRATION_SESSION_PREFIX + dataset.replace(":", "__")


# ── hal0-api REST helpers ────────────────────────────────────────────────────


def _hal0_client(hal0_base: str, http_client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if http_client is not None:
        return http_client, False
    return httpx.Client(base_url=hal0_base.rstrip("/"), timeout=10.0), True


def _hal0_list_page(
    client: httpx.Client, *, agent_id: str, dataset: str, cursor: str | None, limit: int
) -> dict[str, Any]:
    """One page of ``GET /api/memory/list`` scoped to ``dataset``.

    ``dataset == "private:<agent_id>"`` is reached via ``X-hal0-Private: 1``
    (the route ignores any explicit ``?dataset=`` when private mode is set
    and always resolves to the caller's own bucket — see
    ``hal0.memory.namespace.resolve_write_dataset``). Any other dataset
    (``shared``, ``agents``, ``project:<id>``) is passed explicitly and the
    private header is left off.
    """
    headers = {"X-hal0-Agent": agent_id}
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    if dataset.startswith("private:"):
        headers["X-hal0-Private"] = "1"
    else:
        params["dataset"] = dataset
    resp = client.get("/api/memory/list", headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def _hal0_add(
    client: httpx.Client,
    *,
    agent_id: str,
    text: str,
    tags: list[str],
    metadata: dict[str, Any],
    document_id: str | None,
) -> None:
    body: dict[str, Any] = {"text": text, "tags": tags, "metadata": metadata}
    if document_id is not None:
        body["document_id"] = document_id
    resp = client.post(
        "/api/memory/add",
        headers={"X-hal0-Agent": agent_id, "X-hal0-Private": "1"},
        json=body,
    )
    resp.raise_for_status()


# ── forward: hindsight -> honcho ────────────────────────────────────────────


def migrate_hindsight_to_honcho(
    *,
    hal0_base: str = "http://127.0.0.1:8080",
    honcho_base: str,
    workspace: str,
    user_peer: str,
    agent_id: str,
    datasets: list[str] | None = None,
    dry_run: bool = False,
    resume: bool = False,
    state: MigrateState,
    on_progress: ProgressFn | None = None,
    hal0_http_client: httpx.Client | None = None,
    honcho_http_client: httpx.Client | None = None,
    page_size: int = 200,
) -> dict[str, Any]:
    """Backfill ``agent_id``'s Hindsight data into Honcho conclusions.

    Returns ``{"<dataset>": {"scanned": int, "migrated": int, "skipped": int},
    ..., "total": {"scanned": int, "migrated": int, "skipped": int}}``.

    ``resume=False`` still consults ``state`` for per-item idempotency (an
    id already recorded as migrated is always skipped) — the flag exists so
    the CLI can make "start clean" vs "continue" an explicit, log-worthy
    choice even though the underlying behaviour (id-level dedupe) doesn't
    change. ``dry_run=True`` performs no Honcho writes at all: only the
    hal0-api side is read, for a pure scan/count.
    """
    progress = on_progress or _noop_progress
    ds_list = datasets if datasets is not None else ["shared", f"private:{agent_id}"]

    hal0_client, owns_hal0 = _hal0_client(hal0_base, hal0_http_client)
    honcho_client: httpx.Client | None = None
    owns_honcho = False
    try:
        if not dry_run:
            honcho_client, owns_honcho = _honcho_client(honcho_base, honcho_http_client)
            _ensure_workspace(honcho_client, workspace)
            _ensure_peer(honcho_client, workspace, agent_id)
            _ensure_peer(honcho_client, workspace, user_peer)

        report: dict[str, Any] = {}
        total = {"scanned": 0, "migrated": 0, "skipped": 0}

        for dataset in ds_list:
            progress(f"hindsight->honcho: scanning {dataset}")
            already = state.migrated_ids(dataset)
            scanned = migrated = skipped = 0
            session = _session_name(dataset)
            session_ensured = False
            cursor = state.dataset_state(dataset).get("cursor") if resume else None
            pending: list[dict[str, Any]] = []
            pending_ids: list[str] = []

            while True:
                page = _hal0_list_page(
                    hal0_client,
                    agent_id=agent_id,
                    dataset=dataset,
                    cursor=cursor,
                    limit=page_size,
                )
                items = page.get("items") or []
                if not items:
                    break
                for item in items:
                    scanned += 1
                    item_id = str(item.get("id"))
                    if item_id in already:
                        skipped += 1
                        continue
                    content = (item.get("text") or "")[:_MAX_CONTENT_CHARS]
                    if not content:
                        skipped += 1
                        continue
                    if not dry_run:
                        if not session_ensured:
                            _ensure_session(honcho_client, workspace, session)  # type: ignore[arg-type]
                            session_ensured = True
                        pending.append(
                            {
                                "content": content,
                                "observer_id": agent_id,
                                "observed_id": user_peer,
                                "session_id": session,
                            }
                        )
                        pending_ids.append(item_id)
                        if len(pending) >= _CONCLUSION_BATCH:
                            _create_conclusions(honcho_client, workspace, pending)  # type: ignore[arg-type]
                            state.mark_migrated(dataset, pending_ids)
                            migrated += len(pending_ids)
                            pending, pending_ids = [], []
                    else:
                        migrated += 1
                    already.add(item_id)

                cursor = page.get("next_cursor")
                state.dataset_state(dataset)["cursor"] = cursor
                if not cursor:
                    break

            if pending:
                _create_conclusions(honcho_client, workspace, pending)  # type: ignore[arg-type]
                state.mark_migrated(dataset, pending_ids)
                migrated += len(pending_ids)

            report[dataset] = {"scanned": scanned, "migrated": migrated, "skipped": skipped}
            total["scanned"] += scanned
            total["migrated"] += migrated
            total["skipped"] += skipped
            progress(f"hindsight->honcho: {dataset} scanned={scanned} migrated={migrated}")

        report["total"] = total
        return report
    finally:
        if owns_hal0:
            hal0_client.close()
        if owns_honcho and honcho_client is not None:
            honcho_client.close()


# ── reverse: honcho -> hindsight (a.k.a. graph-sync) ────────────────────────


def _list_conclusions_page(
    client: httpx.Client, workspace: str, *, page: int, size: int
) -> dict[str, Any]:
    resp = client.post(
        f"/v3/workspaces/{workspace}/conclusions/list",
        params={"page": page, "size": size},
        json={"filters": None},
    )
    resp.raise_for_status()
    return resp.json()


def migrate_honcho_to_hindsight(
    *,
    hal0_base: str = "http://127.0.0.1:8080",
    honcho_base: str,
    workspace: str,
    agent_id: str,
    since: str | None = None,
    dry_run: bool = False,
    state: MigrateState,
    on_progress: ProgressFn | None = None,
    hal0_http_client: httpx.Client | None = None,
    honcho_http_client: httpx.Client | None = None,
    page_size: int = 100,
    max_pages: int = 1000,
) -> dict[str, Any]:
    """Sync Honcho conclusions (dialectic/dream output) back into Hindsight.

    Doubles as the recurring "graph-sync" job (``hal0 memory sync-graph``):
    idempotent via a single ``created_at`` watermark in ``state`` (or the
    explicit ``since`` override). Conclusions created by the forward
    migration's own sessions (``migration__hindsight__*``) are always
    skipped so the two directions never loop a fact back and forth.

    Returns ``{"scanned": int, "migrated": int, "skipped": int,
    "watermark": str|None}``.
    """
    progress = on_progress or _noop_progress
    watermark = since if since is not None else state.watermark()

    honcho_client, owns_honcho = _honcho_client(honcho_base, honcho_http_client)
    hal0_client, owns_hal0 = _hal0_client(hal0_base, hal0_http_client)
    try:
        scanned = migrated = skipped = 0
        max_seen = watermark

        for page_num in range(1, max_pages + 1):
            page = _list_conclusions_page(honcho_client, workspace, page=page_num, size=page_size)
            items = page.get("items") or []
            if not items:
                break

            for conclusion in items:
                scanned += 1
                session = conclusion.get("session_id") or ""
                created_at = conclusion.get("created_at")
                if session.startswith(_MIGRATION_SESSION_PREFIX):
                    skipped += 1
                    continue
                if watermark is not None and created_at is not None and created_at <= watermark:
                    skipped += 1
                    continue
                content = conclusion.get("content") or ""
                if not content:
                    skipped += 1
                    continue

                if not dry_run:
                    doc_id = conclusion.get("id")
                    _hal0_add(
                        hal0_client,
                        agent_id=agent_id,
                        text=content,
                        tags=["honcho-sync"],
                        metadata={
                            "honcho_conclusion_id": conclusion.get("id"),
                            "observer": conclusion.get("observer_id"),
                            "observed": conclusion.get("observed_id"),
                            "session": session,
                            "created_at": created_at,
                        },
                        document_id=doc_id if isinstance(doc_id, str) else None,
                    )
                migrated += 1
                if created_at is not None and (max_seen is None or created_at > max_seen):
                    max_seen = created_at

            total_pages = page.get("pages")
            if total_pages is not None and page_num >= total_pages:
                break

        if max_seen is not None and max_seen != watermark:
            state.set_watermark(max_seen)
        state.bump_count(migrated)
        progress(f"honcho->hindsight: scanned={scanned} migrated={migrated} watermark={max_seen}")
        return {"scanned": scanned, "migrated": migrated, "skipped": skipped, "watermark": max_seen}
    finally:
        if owns_honcho:
            honcho_client.close()
        if owns_hal0:
            hal0_client.close()


__all__ = [
    "DEFAULT_STATE_PATH",
    "MigrateState",
    "migrate_hindsight_to_honcho",
    "migrate_honcho_to_hindsight",
]
