"""Facts for ``GET /api/memory/health``, gathered at a bounded cost (ADR-0058).

:func:`collect_cached` reads everything :mod:`hal0.memory.health_path` decides
from, plus the figures the Memory area shows beside the steps:

* the engine's reachability and version (the ``/api/memory/engine`` rule);
* the extraction target: slot, mode, whether it resolves, its concurrency,
  and whether the engine's LLM endpoint accepts a connection;
* the embedder record against the config;
* per bank: stats, the waiting and the failed operations, and the mental
  models and knowledge pages.

The cost is bounded four ways. At most :data:`MAX_CONCURRENT_REQUESTS` engine
requests are in flight. An operations list is paged only as far as its figure
needs (:func:`read_operations`), never past :data:`OPERATIONS_CAP_PER_BANK`
rows, and says so (``truncated``) when it needed more. The whole collection
stops at :data:`TOTAL_BUDGET_S`: whatever did not finish is reported with
what is known and its section is named in ``partial``. And the result is kept
for :data:`CACHE_TTL_S`.

Nothing here raises to the route. A read that fails, times out or returns a
shape this module does not recognise leaves its section partial.

``GET /api/memory/lanes`` takes its per-bank queue rows from
:func:`bank_queue_row`, so its counts are the engine's totals and its oldest
age comes from the end of the list, not from the first page.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import Counter
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import Request

from hal0.api.routes import memory as _memory
from hal0.api.routes import memory_admin as _admin
from hal0.config.loader import load_hal0_config
from hal0.memory import extraction_env as _ee
from hal0.memory.bank_layout import HEALTH_LEGACY_UNIFIED
from hal0.memory.embedder_verdict import embedder_verdict
from hal0.memory.health_path import EXTRACTION_FAILURE_WINDOW_S, HealthFacts
from hal0.redaction import redact_log_line

log = logging.getLogger(__name__)

#: Engine requests in flight at once.
MAX_CONCURRENT_REQUESTS = 8
#: Wall-clock budget for one collection.
TOTAL_BUDGET_S = 8.0
#: Budget for deciding whether the engine answers at all. Shorter than the
#: total so a hung engine reads as unreachable, not as a slow page.
ENGINE_PROBE_TIMEOUT_S = 3.0
#: Read budget for one engine request (the client default is 120 s).
REQUEST_TIMEOUT_S = 5.0
#: Budget for the TCP connect to the engine's LLM endpoint.
LLM_PROBE_TIMEOUT_S = 1.5
#: How long a collection is reused. The dashboard polls.
CACHE_TTL_S = 10.0

#: ``limit`` maximum of the engine's ``list_operations`` (OpenAPI, 1.2.0).
OPERATIONS_PAGE = 100
#: Rows read from one bank's operations list before giving up on its end.
OPERATIONS_CAP_PER_BANK = 2000
#: ``limit`` maximum of the engine's ``list_mental_models``.
MENTAL_MODELS_PAGE = 1000
MENTAL_MODELS_CAP_PER_BANK = 5000

#: The engine's ``OperationResponse.status`` vocabulary this module reads.
STATUS_WAITING = "pending"
STATUS_PROCESSING = "processing"
STATUS_FAILED = "failed"

#: ``task_type`` values whose failure is a failed extraction. ``batch_retain``
#: is the parent of ``retain`` and is listed for a batch that failed before it
#: was split; split parents are left out of every read (``exclude_parents``).
EXTRACTION_TASK_TYPES = frozenset({"retain", "batch_retain", "file_convert_retain"})

TOP_ERRORS_MAX = 5
ERROR_MESSAGE_MAX_CHARS = 160
#: Raw error text looked at before normalising. Bounds the regex work on a
#: traceback-sized message.
_ERROR_SCAN_CHARS = 2000

#: ``banks.layout`` when the provider does not say (a provider that is not
#: Hindsight): the layout every store had before the two banks.
BANK_LAYOUT_DEFAULT = HEALTH_LEGACY_UNIFIED

SECTION_EXTRACTION = "extraction"
SECTION_EMBEDDER = "embedder"
SECTION_QUEUE = "queue"
SECTION_KNOWLEDGE = "knowledge"
SECTION_BANKS = "banks"
_SECTION_ORDER = (
    SECTION_EXTRACTION,
    SECTION_EMBEDDER,
    SECTION_QUEUE,
    SECTION_KNOWLEDGE,
    SECTION_BANKS,
)


# ── error messages ─────────────────────────────────────────────────────────

_PAYLOAD_RE = re.compile(
    r"\{|\[\s*[{\"']|\b(?:request body|body|payload|prompt|messages|content|input)\s*[:=]",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
#: Words after which a provider's message tends to echo the credential it
#: refused ("Incorrect API key provided: …"). The line ends at the word.
_SECRET_WORD_RE = re.compile(
    r"\b(?:api[ _-]?key|secret|password|passwd|bearer|authorization|credentials?"
    r"|(?:access|auth|refresh)[ _-]?token)\b",
    re.IGNORECASE,
)
_HEX_ID_RE = re.compile(r"\b(?:[A-Za-z]{1,8}-)?[0-9a-fA-F]{12,}\b")
_OPAQUE_RE = re.compile(r"[A-Za-z0-9_\-+=]{20,}")
_HOST_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_NUMBER_RE = re.compile(r"(?<![\w.\-])\d+(?:\.\d+)?")
_STATUS_CONTEXT_RE = re.compile(r"(?:status|code|http|error)[\s:=]*$", re.IGNORECASE)


def _opaque(match: re.Match[str]) -> str:
    run = match.group(0)
    mixed = any(c.isdigit() for c in run) and any(c.isalpha() for c in run)
    return "<id>" if mixed else run


def _number(match: re.Match[str]) -> str:
    # An HTTP status is a category, not a variable: 402 and 500 stay apart.
    digits = match.group(0)
    if len(digits) == 3 and _STATUS_CONTEXT_RE.search(match.string[: match.start()]):
        return digits
    return "<n>"


def normalise_error(message: Any) -> str:
    """One engine ``error_message`` as a group key that is safe to show.

    The first line only. Secret shapes are masked by the shared redactor
    first. Anything from the start of a structured payload on is dropped, so
    an echoed request or response body never reaches the page, and so is
    anything after a word that names a credential. Ids, hosts and numbers
    become placeholders, so messages that differ only in those group
    together. The result is capped at :data:`ERROR_MESSAGE_MAX_CHARS`.
    """
    if not isinstance(message, str):
        return ""
    line = next((ln for ln in message[:_ERROR_SCAN_CHARS].splitlines() if ln.strip()), "")
    line = redact_log_line(line)
    payload = _PAYLOAD_RE.search(line)
    if payload:
        line = line[: payload.start()]
    secret_word = _SECRET_WORD_RE.search(line)
    if secret_word:
        line = line[: secret_word.end()]
    line = _UUID_RE.sub("<id>", line)
    line = _HEX_ID_RE.sub("<id>", line)
    line = _OPAQUE_RE.sub(_opaque, line)
    line = _HOST_RE.sub("<host>", line)
    line = _NUMBER_RE.sub(_number, line)
    line = " ".join(line.split()).rstrip(" -:,;(")
    if len(line) > ERROR_MESSAGE_MAX_CHARS:
        line = line[: ERROR_MESSAGE_MAX_CHARS - 1].rstrip() + "…"
    return line


# ── engine reads ───────────────────────────────────────────────────────────


class _Malformed(Exception):
    """The engine answered 2xx with a shape this module does not recognise."""


class EngineReader:
    """Bounded GETs: a concurrency cap, a per-request timeout, an optional deadline."""

    def __init__(
        self,
        client: Any,
        *,
        deadline: float | None = None,
        max_concurrent: int = MAX_CONCURRENT_REQUESTS,
    ) -> None:
        self._client = client
        self._deadline = deadline
        self._slots = asyncio.Semaphore(max_concurrent)
        self.requests = 0

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with self._slots:
            timeout_s = REQUEST_TIMEOUT_S
            if self._deadline is not None:
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("memory health budget spent")
                timeout_s = min(timeout_s, remaining)
            self.requests += 1
            return await self._client.request_json("GET", path, params=params, timeout_s=timeout_s)


def _bank_path(bank_id: str, tail: str) -> str:
    return f"/v1/default/banks/{quote(bank_id, safe=':@')}/{tail}"


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.timestamp()


def _count(value: Any) -> int | None:
    """A non-negative integer from an engine payload, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


#: What a read of one operations list is for. It decides how far to page.
WANT_COUNT = "count"  # how many: the engine's total, one request
WANT_OLDEST = "oldest"  # how many, and the age of the oldest that is due
WANT_ROWS = "rows"  # every row, up to the cap


@dataclass
class OperationsRead:
    """One bank's operations in one status. Filled as pages arrive, so a read
    that is cut short keeps what it had; ``complete`` says it finished."""

    count: int | None = None
    #: Age of the oldest row that is due. A waiting row whose ``next_retry_at``
    #: is still ahead was put off on purpose and is not counted as stuck.
    oldest_s: float | None = None
    #: Rows the read needed were left unread at the cap.
    truncated: bool = False
    complete: bool = False
    rows: list[dict[str, Any]] = field(default_factory=list)
    _seen: int = 0

    def absorb(self, rows: list[Any], status: str, now: float, *, keep: bool) -> int:
        """Take one page in; returns how many of its rows are due."""
        due = 0
        for row in rows:
            if not isinstance(row, dict) or str(row.get("status", "")).lower() != status:
                continue
            self._seen += 1
            if keep:
                self.rows.append(row)
            created = _epoch(row.get("created_at"))
            retry_at = _epoch(row.get("next_retry_at"))
            if created is None or (retry_at is not None and retry_at > now):
                continue
            due += 1
            age = round(max(0.0, now - created), 1)
            if self.oldest_s is None or age > self.oldest_s:
                self.oldest_s = age
        return due


def _operations_page(page: Any) -> tuple[list[Any], int | None]:
    if not isinstance(page, dict) or not isinstance(page.get("operations"), list):
        raise _Malformed("operations list")
    return page["operations"], _count(page.get("total"))


async def read_operations(
    reader: EngineReader,
    bank_id: str,
    status: str,
    out: OperationsRead,
    *,
    now: float,
    want: str,
    cap: int = OPERATIONS_CAP_PER_BANK,
) -> None:
    """Read ``bank_id``'s operations in ``status`` into ``out``, as far as ``want`` needs.

    The engine returns the filtered ``total`` and lists newest first
    (``ORDER BY created_at DESC``). So the count never needs a second page,
    and the oldest row is on the last one: :data:`WANT_OLDEST` reads the first
    page for the total and then walks back from the end until a page holds a
    row that is due. :data:`WANT_ROWS` reads forward to the end, because a
    failure's time is its last update, which the order says nothing about.
    Every read stops at ``cap`` rows and sets ``truncated`` if it needed more.

    A response without a ``total`` is read forward to the end, whatever is
    wanted. Split parent batches are left out; their children stand for them.
    """
    path = _bank_path(bank_id, "operations")
    base = {"status": status, "limit": OPERATIONS_PAGE, "exclude_parents": "true"}
    keep = want == WANT_ROWS

    rows, total = _operations_page(await reader.get(path, {**base, "offset": 0}))
    out.absorb(rows, status, now, keep=keep)
    read = len(rows)
    out.count = total if total is not None else out._seen
    more = len(rows) >= OPERATIONS_PAGE and (total is None or total > read)

    if more and total is not None and want == WANT_OLDEST:
        start, found = total, False
        while start > len(rows) and read < cap and not found:
            start = max(len(rows), start - OPERATIONS_PAGE)
            page, _ = _operations_page(await reader.get(path, {**base, "offset": start}))
            read += len(page)
            found = out.absorb(page, status, now, keep=False) > 0
        out.truncated = not found and start > len(rows)
    elif more and not (total is not None and want == WANT_COUNT):
        offset = read
        while more and offset < cap:
            page, total = _operations_page(await reader.get(path, {**base, "offset": offset}))
            out.absorb(page, status, now, keep=keep)
            offset += len(page)
            out.count = total if total is not None else out._seen
            more = len(page) >= OPERATIONS_PAGE and (total is None or total > offset)
        out.truncated = more
    out.complete = True


async def bank_queue_row(reader: EngineReader, bank_id: str, *, now: float) -> dict[str, Any]:
    """The ``/api/memory/lanes`` row for one bank.

    ``{"bank_id", "error"}`` when the engine cannot be read for this bank.
    """
    waiting, failed, processing = OperationsRead(), OperationsRead(), OperationsRead()
    results = await asyncio.gather(
        read_operations(reader, bank_id, STATUS_WAITING, waiting, now=now, want=WANT_OLDEST),
        read_operations(reader, bank_id, STATUS_FAILED, failed, now=now, want=WANT_COUNT),
        read_operations(reader, bank_id, STATUS_PROCESSING, processing, now=now, want=WANT_COUNT),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            return {"bank_id": bank_id, "error": str(result)}
    return {
        "bank_id": bank_id,
        "pending": waiting.count or 0,
        "processing": processing.count or 0,
        "failed": failed.count or 0,
        "oldest_pending_age_s": waiting.oldest_s,
    }


@dataclass
class KnowledgeRead:
    """One bank's mental models. A knowledge page is a mental model the engine
    marks ``is_knowledge_page``; the one list carries both."""

    pages: int = 0
    pages_stale: int = 0
    models: int = 0
    models_stale: int = 0
    refresh_failed: int = 0
    last_success: float | None = None
    last_success_at: str | None = None
    complete: bool = False

    def absorb(self, item: dict[str, Any]) -> None:
        stale = item.get("is_stale") is True
        if item.get("is_knowledge_page") is True:
            self.pages += 1
            self.pages_stale += stale
        else:
            self.models += 1
            self.models_stale += stale
        if item.get("last_refresh_failed_at"):
            self.refresh_failed += 1
        # The engine sets last_refreshed_at to created_at when a model is
        # made, so only a later value is a refresh that finished.
        refreshed_at = item.get("last_refreshed_at")
        refreshed = _epoch(refreshed_at)
        if refreshed is None or refreshed_at == item.get("created_at"):
            return
        if self.last_success is None or refreshed > self.last_success:
            self.last_success, self.last_success_at = refreshed, refreshed_at


async def read_knowledge(reader: EngineReader, bank_id: str, out: KnowledgeRead) -> None:
    path = _bank_path(bank_id, "mental-models")
    offset = 0
    while offset < MENTAL_MODELS_CAP_PER_BANK:
        page = await reader.get(
            path, {"detail": "metadata", "limit": MENTAL_MODELS_PAGE, "offset": offset}
        )
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise _Malformed("mental models list")
        items = page["items"]
        for item in items:
            if isinstance(item, dict):
                out.absorb(item)
        offset += len(items)
        total = _count(page.get("total"))
        if len(items) < MENTAL_MODELS_PAGE or (total is not None and offset >= total):
            out.complete = True
            return
    # Past the cap the counts are a floor; ``complete`` stays false.


@dataclass
class _Bank:
    bank_id: str
    facts: int | None
    documents: int | None = None
    consolidation_pending: int | None = None
    consolidation_failed: int | None = None
    stats_complete: bool = False
    waiting: OperationsRead = field(default_factory=OperationsRead)
    failed: OperationsRead = field(default_factory=OperationsRead)
    knowledge: KnowledgeRead = field(default_factory=KnowledgeRead)


async def _read_stats(reader: EngineReader, bank: _Bank) -> None:
    stats = await reader.get(_bank_path(bank.bank_id, "stats"))
    if not isinstance(stats, dict):
        raise _Malformed("bank stats")
    bank.documents = _count(stats.get("total_documents"))
    bank.consolidation_pending = _count(stats.get("pending_consolidation"))
    bank.consolidation_failed = _count(stats.get("failed_consolidation"))
    if bank.facts is None:
        bank.facts = _count(stats.get("total_nodes"))
    bank.stats_complete = True


# ── local facts (seams for tests; the blocking ones run off the loop) ──────


def _engine_llm() -> dict[str, Any]:
    return _ee.effective_engine_llm()


def _embedder_status(models_cfg: Any) -> dict[str, Any]:
    from hal0.memory.embedder_record import configured_embedder, embedder_status

    return embedder_status(configured_embedder(models_cfg))


async def _llm_reachable(base_url: Any) -> bool | None:
    """Does the engine's LLM endpoint accept a TCP connection? ``None`` = no URL to try.

    A connect and nothing more: no request, no credential, no provider call.
    The engine's own probe (``POST …/health/llm``) makes a real completion
    per call and is documented as not for polling.
    """
    if not isinstance(base_url, str) or not base_url:
        return None
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    if not host:
        return None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), LLM_PROBE_TIMEOUT_S
        )
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def _read_extraction(request: Request, graph_cfg: Any, out: dict[str, Any]) -> None:
    """The extraction subset of ``GET /api/memory/graph/status``, from the same
    functions, without that handler's catalog warm-up, per-bank stats walk,
    chunk audit or unit-env comparison."""
    provider = request.app.state.memory_provider
    slot = None
    with contextlib.suppress(Exception):
        slot = provider.graph_status().get("extraction_slot")
    slot = slot or graph_cfg.extraction_slot
    available = await _memory._enabled_llm_slots(request)
    out["slot"] = slot
    out["mode"] = _memory._extraction_mode(graph_cfg, available)
    limits = await asyncio.to_thread(_ee.effective_limits, graph_cfg)
    out["concurrency"] = limits.max_concurrent
    llm = await asyncio.to_thread(_engine_llm)
    # The slot is in play only when the engine calls hal0/memory and that
    # resolves to a slot: not for an upstream model, and not when the engine's
    # env file points its LLM somewhere else.
    kind, _target = _ee.memory_engine_target(graph_cfg)
    slot_in_play = kind == "slot" and llm.get("overridden") is not True
    out["slot_resolves"] = (
        _memory._extraction_slot_resolves(slot, available) if slot_in_play else None
    )
    out["llm_reachable"] = await _llm_reachable(llm.get("base_url"))
    out["complete"] = True


async def _read_embedder(models_cfg: Any, out: dict[str, Any]) -> None:
    out["status"] = await asyncio.to_thread(_embedder_status, models_cfg)


async def _soft(what: str, work: Coroutine[Any, Any, None]) -> None:
    """Run one read; a failure leaves its accumulator incomplete and is not raised."""
    try:
        await work
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("memory.health.read_failed what=%s error=%s", what, type(exc).__name__)


# ── the collection ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bank_entries(banks: list[Any]) -> list[_Bank]:
    out: list[_Bank] = []
    for entry in banks:
        bank_id = entry.get("bank_id") if isinstance(entry, dict) else entry
        if not isinstance(bank_id, str) or not bank_id:
            continue
        facts = _count(entry.get("fact_count")) if isinstance(entry, dict) else None
        out.append(_Bank(bank_id=bank_id, facts=facts))
    return out


def _extraction_failures(banks: list[_Bank], now: float) -> tuple[int, list[dict[str, Any]]]:
    """Failed retains inside the window, and their messages grouped."""
    failures = 0
    messages: Counter[str] = Counter()
    for bank in banks:
        for row in bank.failed.rows:
            task_type = row.get("task_type") or row.get("operation_type")
            if task_type not in EXTRACTION_TASK_TYPES:
                continue
            failed_at = _epoch(row.get("updated_at")) or _epoch(row.get("created_at"))
            if failed_at is None or now - failed_at > EXTRACTION_FAILURE_WINDOW_S:
                continue
            failures += 1
            message = normalise_error(row.get("error_message"))
            if message:
                messages[message] += 1
    ranked = sorted(messages.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_ERRORS_MAX]
    return failures, [{"message": message, "count": count} for message, count in ranked]


def _known_sum(values: list[int | None]) -> int:
    return sum(v for v in values if v is not None)


def _embedder_section(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "recorded": {"provider": status.get("recorded_provider"), "model": status.get("recorded")},
        "configured": {
            "provider": status.get("configured_provider"),
            "model": status.get("configured"),
        },
        "mixed": bool(status.get("mixed")),
        "needs_switch": bool(status.get("needs_switch")),
        "conflict": status.get("conflict"),
        "informational": embedder_verdict(status).informational,
    }


def _unreachable() -> tuple[dict[str, Any], HealthFacts]:
    sections: dict[str, Any] = {
        "checked_at": _now_iso(),
        "partial": [],
        "engine": {"reachable": False, "version": None},
        SECTION_EXTRACTION: None,
        SECTION_EMBEDDER: None,
        SECTION_QUEUE: None,
        SECTION_KNOWLEDGE: None,
        SECTION_BANKS: None,
        "writes": None,
    }
    return sections, HealthFacts(engine_reachable=False)


async def collect(request: Request) -> tuple[dict[str, Any], HealthFacts]:
    """Gather the response sections and the facts the path is decided from.

    Requires ``app.state.memory_provider`` (the route answers for a box with
    memory off). A provider without a Hindsight client is the boot fallback:
    the engine did not answer, which is what this reports.
    """
    started = time.monotonic()
    deadline = started + TOTAL_BUDGET_S
    provider = request.app.state.memory_provider
    client = getattr(provider, "hindsight_client", None)
    if client is None:
        return _unreachable()

    try:
        engine = await asyncio.wait_for(_admin.engine_status(request), ENGINE_PROBE_TIMEOUT_S)
    except Exception:
        engine = {"reachable": False}
    if not engine.get("reachable"):
        return _unreachable()

    try:
        bank_list = await asyncio.wait_for(
            _memory._all_banks(client), max(0.1, deadline - time.monotonic())
        )
    except Exception:
        bank_list = None
    banks = _bank_entries(bank_list) if isinstance(bank_list, list) else []

    cfg = None
    with contextlib.suppress(Exception):
        cfg = load_hal0_config()

    now = time.time()
    reader = EngineReader(client, deadline=deadline)
    extraction: dict[str, Any] = {}
    embedder: dict[str, Any] = {}
    work: list[tuple[str, Coroutine[Any, Any, None]]] = []
    if cfg is not None:
        work.append(("extraction", _read_extraction(request, cfg.memory.graph, extraction)))
        work.append(("embedder", _read_embedder(cfg.memory.models, embedder)))
    for bank in banks:
        work.append(("stats", _read_stats(reader, bank)))
        work.append(
            (
                "waiting",
                read_operations(
                    reader, bank.bank_id, STATUS_WAITING, bank.waiting, now=now, want=WANT_OLDEST
                ),
            )
        )
        work.append(
            (
                "failed",
                read_operations(
                    reader, bank.bank_id, STATUS_FAILED, bank.failed, now=now, want=WANT_ROWS
                ),
            )
        )
        work.append(("knowledge", read_knowledge(reader, bank.bank_id, bank.knowledge)))

    tasks = [asyncio.ensure_future(_soft(what, coro)) for what, coro in work]
    if tasks:
        _done, late = await asyncio.wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
        for task in late:
            task.cancel()
        if late:
            await asyncio.gather(*late, return_exceptions=True)

    failures_6h, top_errors = _extraction_failures(banks, now)
    oldest = [b.waiting.oldest_s for b in banks if b.waiting.oldest_s is not None]
    waiting_total = _known_sum([b.waiting.count for b in banks])
    failed_total = _known_sum([b.failed.count for b in banks])
    status = embedder.get("status")
    listed = isinstance(bank_list, list)

    partial: set[str] = set()
    if not extraction.get("complete") or not all(b.failed.complete for b in banks) or not listed:
        partial.add(SECTION_EXTRACTION)
    if status is None:
        partial.add(SECTION_EMBEDDER)
    if not listed or not all(
        b.waiting.complete and b.failed.complete and b.stats_complete for b in banks
    ):
        partial.add(SECTION_QUEUE)
    if not listed or not all(b.knowledge.complete for b in banks):
        partial.add(SECTION_KNOWLEDGE)
    if not listed or not all(b.stats_complete for b in banks):
        partial.add(SECTION_BANKS)

    successes = [b.knowledge for b in banks if b.knowledge.last_success is not None]
    latest = max(successes, key=lambda k: k.last_success or 0.0, default=None)
    pages_stale = sum(b.knowledge.pages_stale for b in banks)
    models_stale = sum(b.knowledge.models_stale for b in banks)
    refresh_failed = sum(b.knowledge.refresh_failed for b in banks)
    knowledge: dict[str, Any] = {
        "pages": sum(b.knowledge.pages for b in banks),
        "pages_stale": pages_stale,
        "models": sum(b.knowledge.models for b in banks),
        "models_stale": models_stale,
        "refresh_failed": refresh_failed,
        "last_success_at": latest.last_success_at if latest is not None else None,
    }
    sections: dict[str, Any] = {
        "checked_at": _now_iso(),
        "partial": [name for name in _SECTION_ORDER if name in partial],
        "engine": {"reachable": True, "version": engine.get("version")},
        SECTION_EXTRACTION: {
            "slot": extraction.get("slot"),
            "mode": extraction.get("mode"),
            "slot_resolves": extraction.get("slot_resolves"),
            "llm_reachable": extraction.get("llm_reachable"),
            "concurrency": extraction.get("concurrency"),
            "failures_6h": failures_6h,
            "top_errors": top_errors,
        },
        SECTION_EMBEDDER: _embedder_section(status),
        SECTION_QUEUE: {
            "waiting": waiting_total,
            "failed": failed_total,
            "oldest_waiting_s": max(oldest) if oldest else None,
            "consolidation_pending": _known_sum([b.consolidation_pending for b in banks]),
            "consolidation_failed": _known_sum([b.consolidation_failed for b in banks]),
            "by_bank": [
                {
                    "bank_id": b.bank_id,
                    "waiting": b.waiting.count,
                    "failed": b.failed.count,
                    "oldest_waiting_s": b.waiting.oldest_s,
                }
                for b in banks
            ],
            "truncated": any(b.waiting.truncated or b.failed.truncated for b in banks),
        },
        SECTION_KNOWLEDGE: knowledge,
        SECTION_BANKS: {
            # ADR-0058 decision 7: from the layout record, via the provider.
            "layout": str(getattr(provider, "layout", None) or BANK_LAYOUT_DEFAULT),
            "total": len(banks) if listed else None,
            "facts": _known_sum([b.facts for b in banks]) if listed else None,
            "list": [
                {"bank_id": b.bank_id, "facts": b.facts, "documents": b.documents} for b in banks
            ],
        },
        # Filled from the write gate by a later change (ADR-0058, decision 4).
        "writes": None,
    }
    facts = HealthFacts(
        engine_reachable=True,
        slot_resolves=extraction.get("slot_resolves"),
        llm_reachable=extraction.get("llm_reachable"),
        extraction_failures=failures_6h,
        extraction_top_errors=tuple(top_errors),
        operations_failed=failed_total,
        operations_waiting=waiting_total,
        oldest_waiting_s=max(oldest) if oldest else None,
        embedder=status,
        pages_stale=pages_stale,
        models_stale=models_stale,
        refresh_failed=refresh_failed,
    )
    log.debug(
        "memory.health.collected banks=%d requests=%d elapsed_s=%.2f partial=%s",
        len(banks),
        reader.requests,
        time.monotonic() - started,
        sections["partial"],
    )
    return sections, facts


async def collect_cached(request: Request) -> tuple[dict[str, Any], HealthFacts]:
    """:func:`collect`, reused for :data:`CACHE_TTL_S`.

    The same ``(monotonic time, result)`` slot on ``app.state`` the graph
    status chunk check uses. One collection runs at a time: a second caller
    waits for it and takes its result.
    """
    state = request.app.state
    lock = getattr(state, "memory_health_lock", None)
    if lock is None:
        lock = state.memory_health_lock = asyncio.Lock()
    async with lock:
        cached = getattr(state, "memory_health_cache", None)
        if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_S:
            kept: tuple[dict[str, Any], HealthFacts] = cached[1]
            return kept
        result = await collect(request)
        state.memory_health_cache = (time.monotonic(), result)
        return result


__all__ = [
    "CACHE_TTL_S",
    "MAX_CONCURRENT_REQUESTS",
    "OPERATIONS_CAP_PER_BANK",
    "TOTAL_BUDGET_S",
    "EngineReader",
    "bank_queue_row",
    "collect",
    "collect_cached",
    "normalise_error",
]
