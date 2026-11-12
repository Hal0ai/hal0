"""Move a memory store to another embedder (#3518, ADR-0058 decision 2).

``hal0 memory embedder switch <model>`` is the one way a store's embedder
changes. hindsight-api's embedder is instance-wide and a stored vector does
not say which model made it, so the only honest way to change the model is to
re-embed everything. The engine does that itself on a bank restore: an export
archive never carries embeddings, and ``transfer/import`` in ``restore`` mode
writes the whole bank into a new id, embedding every fact with the engine's
current model and making no LLM call.

Isolation
---------

hal0's write gate (:mod:`hal0.memory.write_gate`) stops every write that goes
through hal0. It cannot stop a client that talks to the engine's own port,
and nearly every way this command could lose or mislabel data needs exactly
such a client. So for the whole of a run the engine is taken away from them:
it is restarted listening only on a private loopback port this run picked,
and is returned to its normal address at the end.

The mechanism is one drop-in, :data:`ISOLATION_DROP_IN_NAME`, in the engine
unit's ``.d`` directory. It clears ``ExecStart`` and sets it again to the
command the unit runs now (read with ``systemctl cat``, so an operator's own
``ExecStart`` drop-in, a LAN bind for instance, is what gets rewritten) with
``--host 127.0.0.1 --port <private>``. It sorts after every other drop-in, so
it wins; it also turns the engine's own auto-consolidation off, so the engine
changes nothing by itself while it is private. The preflight reads the unit
(:func:`~hal0.memory.engine_maintenance.unit_refusals`) and refuses, before anything changes, an
``ExecStart`` it cannot rewrite word for word (a shell wrapper, an unkept
prefix, an expanded host or port), a later-sorting drop-in that would win
over this one, and an ``ExecStartPre``/``Post``/``Condition``/``Reload`` that
names the normal port. After each restart onto the private port the run
confirms that a process of the engine's unit owns the listening socket
(:func:`~hal0.memory.engine_maintenance.loopback_listener`), that ``/version`` reports the engine the
preflight saw, and that nothing answers at the normal address: only a refused
connection counts as nothing. Any of these failing stops the run before it
has exported anything.

Consequences, all intended: recall is unavailable for the whole run, not only
during restarts; hal0-api sees the engine as down; ``--apply`` and
``--resume`` need root, because no wrapper verb lets the service account
replace a unit's ``ExecStart``. When a run stops in a state that needs
``--resume``, the engine *stays* private with the gate set, and ``--status``
and ``hal0 doctor`` say so.

The run
-------

In order (:meth:`EmbedderSwitch.run`):

1. **Preflight** (:meth:`EmbedderSwitch.preflight`; the whole of a dry run).
   Refuses unless everything it needs holds. It writes nothing, anywhere.
2. **Pause** writes through hal0 and let the engine's queues drain.
3. **Isolate** the engine (restart, still on its original embedder), let the
   queues drain again, and list the banks. That list is the set this run
   rebuilds.
4. **Export** every bank to ``<state>/backups/memory-embedder-switch-<run>/``.
   The bank is read before the export is submitted and again once the archive
   is on disk; only when both reads and the archive agree is that the
   baseline.
5. **Flip**: record the target, re-render the managed drop-in from it,
   restart the engine (still private), and confirm from the engine's own
   environment that it runs the target.
6. **Restore** every archive into ``<bank>.switch-<run>`` and, once the engine
   is quiet, compare with the archive. Every bank is restored and compared
   before any original is touched.
7. **Swap**, bank by bank: check the original once more, delete it, restore
   its archive again, this time into the bank's own id, check the result
   against the original, and only then delete the rebuilt copy.
8. **Finish**: record the embedder without ``mixed``, return the engine to
   its normal address, retire the journal, then lift the gate.

Anything that fails before step 7 is undone (:meth:`EmbedderSwitch._rollback`):
temporary banks deleted while the engine is still private, the engine
returned to its normal address on the original embedder, and only once it is
confirmed running that embedder is the original record written back. The
originals were never touched.

Step 7 cannot be undone, so it is resumable instead. The journal
(:func:`journal_path`, mirrored into the archive directory on every save) is
written and synced *before* each delete and each restore into a bank's own
id, naming the step about to happen. ``--resume`` (:meth:`EmbedderSwitch.resume`) never trusts a
journal step by itself: it looks at which of the two ids the engine has now
(:func:`swap_action`). Whatever it can repair from the original or from the
archive it repairs, so that the same resume does not fail twice for a reason
the switch could have fixed; for the rest ``--status`` says exactly what the
operator does. A journal of another version, or with a phase or step this
code does not know, is refused, never guessed at (:data:`JOURNAL_VERSION`).

A restore is never cancelled. Hindsight 1.2.0 marks a cancelled operation
``cancelled`` at once, but a bank restore does not stop for it and the
status it writes when it does end is suppressed, so a cancel would make a
restore that is still writing look finished. A restore a resume finds
running is waited for (:meth:`EmbedderSwitch._await_restore`).

What is still checked
---------------------

With the engine isolated, the comparisons are assertions: they should never
fire, and if one does the run stops resumably. Counts compared strictly are
facts, mental models, directives and documents (:data:`~hal0.memory.engine_maintenance.HARD_COUNTS`).
Observations are not: the engine's consolidation moves them by itself and an
export leaves out an observation whose source facts are gone, so a rebuilt
bank may hold more observations than its archive, never fewer. The
fingerprint (:data:`~hal0.memory.engine_maintenance.FINGERPRINT_SIGNALS`) is kept to what cannot move by
itself: digests of the directives, of the config overrides and of the mental
models' definitions, and the time a document was last ingested.

Keeping ids
-----------

A bank keeps its ids across the switch: its knowledge-base folders and
pages (``kf-``/``kp-``), its directives, its mental models and its
documents. Agents and the Hindsight coding-agents plugin hold page ids
(``relates_to_page_id``, the ``[[page:kp-...]]`` initiative markers they
retain), and the dashboard links to a page by its id, so a switch that
changed them would break all of those without a word (#3597).

hindsight-api 1.2.0 keeps them only on a restore into the bank's own id
once that bank is gone (``engine/transfer/importer.py``): knowledge-node ids
are unique across the whole schema, so ``_restore_knowledge_pages`` gives a
node a fresh UUID when its id is still held by another bank, and
``_restore_bank`` drops every directive id (``_drop_ids``) when the target
id is not the archive's own. Neither id can be set on create through the
HTTP API (``create_knowledge_page`` and ``create_directive`` mint their
own). The restore into ``<bank>.switch-<run>`` is therefore the check, made
before any original is touched, and the bank that stays is a second restore
of the same archive into the bank's own id, made after the original is
deleted and compared with the original as it was exported: its counts,
its fingerprint (directive and knowledge-node ids included), and the content
checks of :data:`CONTENT_SIGNALS`, which a restore that came back lossy with
the right counts fails: every document id with its content hash, every
fact's text and tags, and each mental model's generated content.
Mental-model ids are bank-scoped and survive either restore.

Fact ids are the one thing no restore keeps: the engine replays every fact
and gives it a new id (it remaps the mental models' evidence itself). The
dry run says so.

The price is a second embedding pass per bank. The rebuilt copy stays until
the bank under its own id matches, so at no point is a bank held only by
its archive.

Test hook
---------

``hook(point)`` is called at every step boundary (:data:`HOOK_POINTS`); tests
raise from it to stop a run exactly there.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

from hal0.memory import embedder_record, write_gate
from hal0.memory import engine_maintenance as em
from hal0.memory.embedder_record import (
    SOURCE_CONFIGURED,
    SOURCE_SWITCH,
    SOURCE_UNAVAILABLE,
    Embedder,
    EmbedderRecord,
    describe_embedder,
)

log = structlog.get_logger(__name__)

#: Version 2 is the delete-and-restore swap that keeps ids (#3597). A
#: version 1 journal was written by the rename-based swap before it, whose
#: steps (``renaming``, ``renamed``) mean nothing to this one: it is refused,
#: never guessed at.
JOURNAL_VERSION = 2

#: What a restored bank is checked by besides its counts and
#: :data:`~hal0.memory.engine_maintenance.FINGERPRINT_SIGNALS`, against its original once it was exported
#: (#3597): the rebuilt copy as soon as its restore ends and again before the
#: original is deleted, and the bank restored into its own id. Each is exact:
#: a lossy restore with the right counts fails it, and a failure names the
#: documents and mental models that differ.
#:
#: The engine's own ``content_hash`` is not compared: a restore recomputes it
#: from the text it stores (``importer.py``), so a bank with
#: ``store_document_text`` off, whose rows keep the hash of a text they do
#: not hold, would never match. The document listing carries no text, only
#: its length (``LENGTH(original_text)``, 0 when none is stored), and that is
#: what is compared.
CONTENT_SIGNALS: dict[str, str] = {
    "documents": "every document id with the length of its stored text (0 when the bank "
    "does not store document text); the text itself is not in the engine's listing",
    "facts": "per document, a digest of every world and experience fact's text, tags and "
    "type (observations are left out: the engine consolidates them by itself)",
    "mental-model content": "each mental model's generated content, while its "
    "last_refreshed_at is the original's",
}

#: What a run costs besides embedding, measured on the same engine on a test
#: container, 2026-11-11: an engine restart took 15 to 33 s, and each export
#: and each restore has a floor of about a second however small the bank.
RESTART_S = 25.0
OPERATION_FLOOR_S = 1.0
#: Each bank is restored twice: into its rebuilt copy, the check made before
#: any original is touched, and then into its own id once the original is
#: deleted, the only restore that keeps its ids (see "Keeping ids" above).
RESTORES_PER_BANK = 2

#: What the switch keeps and what it cannot, stated in every plan (#3597).
IDS_ACROSS_SWITCH: dict[str, Any] = {
    "kept": [
        "knowledge-base folder and page ids (kf-..., kp-...)",
        "directive ids",
        "mental-model ids",
        "document ids",
    ],
    "changed": ["fact ids: the engine gives every restored fact a new id"],
    "verified": [
        "facts, mental models, directives and documents by count, observations at least "
        "as many as archived",
        "knowledge-base folder and page ids, directive ids and text, mental-model ids and "
        "definitions, and the bank's config, by digest",
        "document ids with the length of their stored text, and every world and "
        "experience fact's text and tags, by digest per document",
        "each mental model's generated content, unless the engine refreshed it after the restore",
    ],
    "note": "each bank's rebuilt copy is compared with the original as it was exported, "
    "content included, before the original is deleted; the bank is then restored into its "
    "own id and compared again before its rebuilt copy is deleted, so "
    "links and agents that hold a page or directive id keep working. A fact id written down "
    "before the switch (a dashboard fact link, an id kept for memory_delete) no longer "
    "resolves after it. With --allow-pending the document, fact and mental-model content "
    "checks are skipped: the bank was still being written when it was exported",
}

#: A successful run restarts the engine three times: onto its private port,
#: onto the target embedder, and back to its normal address.
RESTARTS_PER_RUN = 3

#: The drop-in that isolates the engine. It sorts after every hal0 drop-in and
#: after any name an operator is likely to choose, and it replaces
#: ``ExecStart``, so it wins over an operator's own ``ExecStart`` drop-in.
ISOLATION_DROP_IN_NAME = "zzzz-hal0-embedder-switch.conf"

#: Everything this command is called, on disk and in what it says
#: (:class:`~hal0.memory.engine_maintenance.MaintenanceNames`). Fixed: a run
#: interrupted on a box before an upgrade is found again by these names.
NAMES = em.MaintenanceNames(
    command="hal0 memory embedder switch",
    actor="the switch",
    journal="memory-embedder-switch.json",
    lock="memory-embedder-switch.lock",
    archive_prefix="memory-embedder-switch-",
    drop_in=ISOLATION_DROP_IN_NAME,
    mirror="journal.json",
    gate_reason=write_gate.REASON_EMBEDDER_SWITCH,
    log_event="hal0.memory.embedder_switch",
)

#: Free space wanted on the engine's filesystem, as a multiple of its data
#: directory: the rebuilt banks sit beside the originals until the swap.
STORE_FREE_FACTOR = 1.0
#: And for the archives, which carry no vectors and no indexes.
ARCHIVE_FREE_FACTOR = 0.5

#: hal0-api's bank-id grammar (``memory_admin._BANK_RE``) allows 128 chars; a
#: temporary id that would not fit is shortened, never truncated mid-name.
_BANK_ID_MAX = 128

#: Every id this command ever creates: ``<bank>.switch-<run>``, the digest
#: form of it, and the scratch bank a restore into a bank's own id is
#: recorded against.
_TEMP_RE = re.compile(
    r"(?:\.switch-[0-9a-f]{8}$)|(?:^switch-[0-9a-f]{8}-(?:scratch|[0-9a-f]{16})$)"
)

# Journal phases, in the order a run passes through them.
PHASE_PAUSED = "paused"
PHASE_EXPORTING = "exporting"
PHASE_FLIPPING = "flipping"
PHASE_RESTORING = "restoring"
PHASE_SWAPPING = "swapping"
PHASE_ROLLING_BACK = "rolling_back"
PHASE_ROLLBACK_FAILED = "rollback_failed"
PHASE_ROLLED_BACK = "rolled_back"
PHASE_COMPLETE = "complete"
_FINISHED_PHASES = (PHASE_ROLLED_BACK, PHASE_COMPLETE)

# Per-bank steps. ``deleting`` and ``placing`` are saved before the call
# they name; ``deleted`` after it answered, and ``placed`` once the bank
# under its own id was found to match.
STEP_PLANNED = "planned"
STEP_EXPORTED = "exported"
STEP_RESTORED = "restored"
STEP_DELETING = "deleting"
STEP_DELETED = "deleted"
STEP_PLACING = "placing"
STEP_PLACED = "placed"
_BEFORE_DELETE = (STEP_PLANNED, STEP_EXPORTED, STEP_RESTORED)
_STEPS = (*_BEFORE_DELETE, STEP_DELETING, STEP_DELETED, STEP_PLACING, STEP_PLACED)

# What :func:`swap_action` can answer.
ACTION_SWAP = "delete the original, then restore its archive into its own id"
ACTION_PLACE = "restore the bank from its archive into its own id, keeping its ids"
ACTION_CHECK_PLACED = (
    "check the bank restored into its own id against its archive, restoring it again if it "
    "does not match"
)
ACTION_DROP_COPY = "delete the rebuilt copy: the bank under its own id matched"
ACTION_VERIFY = "check the bank under its final id against its archive"
ACTION_FINAL = "nothing: the bank is under its final id"
ACTION_REBUILD = "export and rebuild the bank again, then swap it"
ACTION_RECREATED = "stop: the original id exists again beside the rebuilt bank"
ACTION_DELETE_SENT = "stop: the original may be partly deleted"

#: Where ``hook`` is called. ``<bank>`` is the bank's own id.
HOOK_POINTS: tuple[str, ...] = (
    "paused",
    "isolated",
    "exported:<bank>",
    "before_flip",
    "flipped",
    "restored:<bank>",
    "before_swap",
    "before_delete:<bank>",
    "deleted:<bank>",
    "before_place:<bank>",
    "placed:<bank>",
    "before_finish",
)

#: Page size for the content listings read only for a bank restored into its
#: own id: documents and facts can run to thousands, and the engine's list
#: routes take any limit.
_CONTENT_PAGE = 500


class SwitchError(em.MaintenanceError):
    """A step of the switch failed. The message is written for the operator.

    ``resumable`` is true when the run stopped inside the swap, or a rollback
    could not finish: writes stay paused and ``--resume`` carries on.
    """


class _EngineNotAsked(Exception):
    """``status`` does not query an engine that is stopped or that another process answers for."""


class SwitchRefused(SwitchError):
    """Refused before anything was changed. ``plan`` is the preflight report, when there is one."""

    def __init__(self, message: str, plan: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.plan = plan or {}


@dataclass
class BankEntry:
    """One bank's line in the journal.

    ``counts`` and ``fingerprint`` are the bank as its archive was taken;
    ``restored_*`` the rebuilt bank as its restore finished.
    """

    bank: str
    temp: str
    counts: dict[str, int] = field(default_factory=dict)
    links: int = 0
    fingerprint: dict[str, Any] = field(default_factory=dict)
    archive: str | None = None
    step: str = STEP_PLANNED
    restored_counts: dict[str, int] | None = None
    restored_fingerprint: dict[str, Any] | None = None
    #: The original's :meth:`EmbedderSwitch.content_state` once it was
    #: exported; ``None`` for a run with ``--allow-pending``.
    contents: dict[str, Any] | None = None
    #: ``{"bank", "id", "target", "engine_pid", "engine_invocation"}`` of a
    #: restore that may still be running: the bank it is recorded against,
    #: its operation id, the bank it writes, and the engine process and unit
    #: invocation (systemd's ``InvocationID``) it was sent to.
    restore_op: dict[str, Any] | None = None
    note: str | None = None
    #: How the last restore into the bank's own id failed its check, and how
    #: many restores in a row failed it the same way.
    place_wrong: str | None = None
    place_repeats: int = 0
    #: The engine's own embedding work (:data:`~hal0.memory.engine_maintenance.ENGINE_EMBEDDING_TYPES`)
    #: seen live on the original after the engine was first restarted on the
    #: target, as ``{"bank", "type", "id", "status"}``. It may have run on
    #: the target embedder, so a rollback records the store as mixed for this
    #: bank.
    #: Absent from a journal written before #3604 and then empty.
    engine_work: list[dict[str, Any]] = field(default_factory=list)
    #: A rollback's verdict on the original, saved as soon as it is reached
    #: (:meth:`EmbedderSwitch._rollback`): ``{"mixed": bool, "reason": str}``,
    #: where ``mixed`` means it may hold vectors of the target model. ``None``
    #: until its check has run; a later look can only turn ``mixed`` on.
    #: Absent from a journal written before it existed and then ``None``.
    taint: dict[str, Any] | None = None


@dataclass
class Journal:
    """The state of one run, on disk from the pause to the finish.

    ``flip_started`` goes true, and is saved, before the record or the
    drop-in is touched: a rollback puts both back only when it is set.
    """

    run_id: str
    started_at: str
    pid: int
    phase: str
    target: dict[str, str]
    previous: dict[str, Any]
    archive_dir: str
    allow_pending: bool = False
    #: The loopback port the engine listens on while this run has it, and
    #: whether the run has started to move it there (saved before it does).
    private_port: int | None = None
    isolated: bool = False
    flip_started: bool = False
    #: True while the switch itself holds the engine's unit stopped (it found
    #: a foreign process on the private port and would otherwise have left
    #: the unit crash-looping into it); cleared by the next successful start.
    engine_stopped: bool = False
    banks: list[BankEntry] = field(default_factory=list)
    stray_banks: list[str] = field(default_factory=list)
    error: str | None = None
    updated_at: str | None = None
    #: When the engine was first restarted on the target embedder (UTC, ISO),
    #: saved before that restart: an engine operation on an original whose
    #: row moved since then ran on the target. A journal written before #3604
    #: has none; a rollback then counts from ``started_at``.
    flipped_at: str | None = None
    version: int = JOURNAL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Journal:
        known = set(BankEntry.__dataclass_fields__)
        banks = [
            BankEntry(**{k: v for k, v in b.items() if k in known}) for b in data.get("banks") or []
        ]
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "banks"}
        return cls(banks=banks, **fields)

    @property
    def scratch(self) -> str:
        return scratch_bank_id(self.run_id)

    def own_ids(self) -> set[str]:
        """Every id this run may have created."""
        return {e.temp for e in self.banks} | {self.scratch}


def journal_path() -> Path:
    """``/var/lib/hal0/state/memory-embedder-switch.json`` (sandboxed under ``HAL0_HOME``)."""
    return em.journal_path(NAMES)


def lock_path() -> Path:
    return em.lock_path(NAMES)


def isolation_drop_in_path() -> Path:
    return em.isolation_drop_in_path(NAMES)


def isolated_port(path: Path | None = None) -> int | None:
    """The private port the switch's isolation drop-in pins the engine to (:func:`em.isolated_port`)."""
    return em.isolated_port(NAMES, path)


def temp_bank_id(bank: str, run_id: str) -> str:
    """``<bank>.switch-<run>``, or a short id built from a digest when that would not fit."""
    candidate = f"{bank}.switch-{run_id}"
    if len(candidate.encode("utf-8")) <= _BANK_ID_MAX:
        return candidate
    digest = hashlib.sha256(bank.encode("utf-8")).hexdigest()[:16]
    return f"switch-{run_id}-{digest}"


def scratch_bank_id(run_id: str) -> str:
    """An empty bank a restore into a bank's own id is recorded against (see ``_place``)."""
    return f"switch-{run_id}-scratch"


def is_switch_temp(bank: str) -> bool:
    """True for any id shaped like one this command creates."""
    return bool(_TEMP_RE.search(bank))


_PHASES = (
    PHASE_PAUSED,
    PHASE_EXPORTING,
    PHASE_FLIPPING,
    PHASE_RESTORING,
    PHASE_SWAPPING,
    PHASE_ROLLING_BACK,
    PHASE_ROLLBACK_FAILED,
    *_FINISHED_PHASES,
)


def _parse_journal(raw: str) -> Journal:
    """A journal this switch knows how to carry on. Raises :class:`ValueError` otherwise.

    Anything it was not written to understand, an older or newer version, a
    phase or a bank step it does not know, is refused rather than guessed at:
    a guess in the swap can delete a bank.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("not a switch journal")
    where = data.get("archive_dir")
    archives = f"; its archives are in {where}" if isinstance(where, str) and where else ""
    version = data.get("version")
    if version != JOURNAL_VERSION:
        raise ValueError(
            f"journal version {version!r}, written by a different hal0; this one carries on "
            f"only version {JOURNAL_VERSION} and will not guess at another{archives}. Check "
            "every bank against its archive by hand before lifting the pause"
        )
    if data.get("phase") not in _PHASES:
        raise ValueError(f"unknown phase {data.get('phase')!r}{archives}")
    for bank in data.get("banks") or []:
        step = bank.get("step", STEP_PLANNED) if isinstance(bank, dict) else None
        if step not in _STEPS:
            name = bank.get("bank") if isinstance(bank, dict) else bank
            raise ValueError(f"bank {name!r} is at unknown step {step!r}{archives}")
    return Journal.from_dict(data)


def _parse_time(value: str | None) -> datetime | None:
    """A journal time as an aware datetime; ``None`` when it is missing or unreadable."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def _mark(entry: BankEntry, reason: str, *, mixed: bool = True) -> None:
    """Record a rollback's verdict on ``entry``. A verdict already ``mixed`` stays so."""
    before = entry.taint or {}
    if before.get("mixed") and not mixed:
        return
    if before.get("mixed") and before.get("reason"):
        if not reason or reason in before["reason"]:
            return
        reason = f"{before['reason']}; {reason}"
    entry.taint = {"mixed": bool(before.get("mixed")) or mixed, "reason": reason}


def _finished(journal: Journal) -> bool:
    return journal.phase in _FINISHED_PHASES


def locate_journal() -> dict[str, Any]:
    """Find the journal of an unfinished run wherever a copy survives (:func:`em.locate_journal`)."""
    return em.locate_journal(NAMES, _parse_journal, _finished)


def load_journal() -> Journal | None:
    """The journal of an unfinished run, or ``None``. Raises :class:`SwitchError` if unusable."""
    found = locate_journal()
    if found["journal"] is None and found["error"]:
        raise SwitchError(f"the switch journal is not usable: {found['error']}")
    return found["journal"]


def interrupted_switch() -> dict[str, Any] | None:
    """A short description of an unfinished switch for status surfaces, else ``None``.

    Never raises: ``hal0 doctor``, the updater and the engine upgrade call it.
    """
    found = locate_journal()
    journal = found["journal"]
    if journal is None:
        if found["error"]:
            return {"phase": "unreadable", "error": found["error"]}
        return None
    return {
        "phase": journal.phase,
        "run_id": journal.run_id,
        "started_at": journal.started_at,
        "target": journal.target,
        "error": journal.error,
        "archive_dir": journal.archive_dir,
        "source": found["source"],
        "isolated": journal.isolated,
    }


def lock_held() -> bool:
    """True when a process holds the switch lock. Creates nothing."""
    return em.lock_held(NAMES)


def blocking_reason() -> str | None:
    """Why nothing else may restart or rebuild the memory engine now, or ``None``.

    For ``hal0 update`` and the engine upgrade: an upgrade snapshots the
    engine's data directory and may roll back to it, which in the middle of a
    switch would bring back originals the switch had already deleted.
    """
    if lock_held():
        return "`hal0 memory embedder switch` is running on this box"
    left = interrupted_switch()
    if left is not None:
        return (
            f"an embedder switch (run {left.get('run_id')}, phase {left.get('phase')}) did not "
            "finish; run `hal0 memory embedder switch --resume` first"
        )
    gate = write_gate.read_gate()
    if gate is not None and gate.get("reason") == write_gate.REASON_EMBEDDER_SWITCH:
        return (
            f"memory writes are paused by an embedder switch (run {gate.get('run_id')}) that "
            "left no journal; see `hal0 memory embedder switch --status`"
        )
    if isolated_port() is not None:
        return (
            f"the memory engine is isolated by an embedder switch ({isolation_drop_in_path()} "
            "is present); see `hal0 memory embedder switch --status`"
        )
    return None


def swap_action(step: str, has_original: bool, has_rebuilt: bool) -> str:
    """What the swap does for one bank, from its journal step and the ids the engine has now.

    ``has_original`` is whether the bank's own id exists, ``has_rebuilt``
    whether its rebuilt copy (``<bank>.switch-<run>``) does. The ids decide;
    the step only tells two otherwise identical pictures apart.
    """
    if step == STEP_PLACED:
        if has_original:
            # Verified when it was placed. It is not verified again on every
            # resume: the bank is live once the run ends, and a check that
            # failed here would fail the same way for ever.
            return ACTION_DROP_COPY if has_rebuilt else ACTION_FINAL
        return ACTION_PLACE
    if step == STEP_PLACING:
        # The journal said ``placing`` before the restore into the own id was
        # sent, so a bank under that id is this run's, whole or not.
        return ACTION_CHECK_PLACED if has_original else ACTION_PLACE
    if step == STEP_DELETING and has_original:
        # The delete may have been sent and may have taken part of the bank:
        # it is neither deleted again nor exported again without a person.
        return ACTION_DELETE_SENT
    if not has_original:
        # Deleted (or lost) with or without its rebuilt copy: the archive
        # puts it back under its own id either way.
        return ACTION_PLACE
    if not has_rebuilt:
        # Before the delete: the rebuilt copy was lost while the original
        # stood. After it: something put a bank under the original id.
        return ACTION_REBUILD if step in _BEFORE_DELETE else ACTION_VERIFY
    # Both ids. After a delete that answered, the original id can only be
    # back because something recreated it.
    return ACTION_RECREATED if step == STEP_DELETED else ACTION_SWAP


#: How many ids a content difference names per kind before it says "and N more".
_SHOWN_IDS = 3


def _ids_text(ids: list[str]) -> str:
    shown = ", ".join(ids[:_SHOWN_IDS])
    return shown + (f" and {len(ids) - _SHOWN_IDS} more" if len(ids) > _SHOWN_IDS else "")


def _keyed_moved(name: str, before: Any, now: Any, *, changed: str) -> str | None:
    """How two ``{id: value}`` maps differ, naming a few ids of each kind; ``None`` when equal.

    A side that is not a map was recorded in a form this build does not
    read, and is reported as a difference, never assumed equal.
    """
    if not isinstance(before, dict) or not isinstance(now, dict):
        return None if before == now else f"{name} (recorded in a form this build cannot compare)"
    missing = sorted(str(k) for k in before.keys() - now.keys())
    extra = sorted(str(k) for k in now.keys() - before.keys())
    differ = sorted(str(k) for k in before.keys() & now.keys() if before[k] != now[k])
    parts = [
        f"{label} {_ids_text(ids)}"
        for label, ids in (("missing", missing), ("not in the original", extra), (changed, differ))
        if ids
    ]
    return f"{name} ({'; '.join(parts)})" if parts else None


def _contents_moved(before: Mapping[str, Any], now: Mapping[str, Any]) -> list[str]:
    """The :data:`CONTENT_SIGNALS` that differ, each naming a few of the ids that do.

    Documents are named by id. Facts get a new id on every restore, so they
    are named by the document they belong to (``(no document)`` for those
    that belong to none). A mental model's content is compared only while
    its ``last_refreshed_at`` is the original's: the engine regenerates
    content by itself, and a model it refreshed after the restore was not
    lost by it.
    """
    moved = [
        text
        for text in (
            _keyed_moved(
                "documents",
                before.get("documents"),
                now.get("documents"),
                changed="text length differs for",
            ),
            _keyed_moved(
                "facts",
                before.get("facts"),
                now.get("facts"),
                changed="differ for document",
            ),
        )
        if text
    ]
    then, current = before.get("model_content") or {}, now.get("model_content") or {}
    lost = sorted(
        str(mid)
        for mid, old in then.items()
        if mid in current
        and current[mid].get("refreshed") == old.get("refreshed")
        and current[mid].get("content") != old.get("content")
    )
    if lost:
        moved.append(f"mental-model content (differs for {_ids_text(lost)})")
    return moved


class EmbedderSwitch(em.EngineMaintenance):
    """One run of the switch against one engine.

    The engine session, its isolation and identity checks, the queue waits,
    the archives and the journal on disk are
    :class:`~hal0.memory.engine_maintenance.EngineMaintenance`'s, run under
    :data:`NAMES`; this class is the switch's plan and sequence.

    ``client`` is a :class:`hal0.memory.hindsight_client.HindsightRestClient`
    pointed at the engine itself, not at hal0-api: the switch's own imports
    and deletes must not meet the pause it sets.

    Everything that touches the host is injectable, and defaults to the real
    thing: ``apply_dropin(embedder)`` renders the managed drop-in for that
    pair and restarts the engine (returning the ``apply_showcase_dropin``
    dict), ``evidence()`` is :func:`embedder_record.engine_evidence`,
    ``isolate(port, run_id)`` and ``release()`` write and remove the isolation drop-in
    (:func:`em.write_isolation_dropin`, :func:`em.remove_isolation_dropin`; the
    restart that follows either is ``apply_dropin``'s), ``isolated_port()``
    reads it back, ``client_for(base_url)`` builds the client for the private
    port, ``listener(port)`` says which process listens on a loopback port
    (:func:`em.loopback_listener`), ``unit_text()`` is ``systemctl cat`` of the
    engine's unit (:func:`em.read_unit_text`), ``unit_invocation()`` is its
    systemd ``InvocationID`` (:func:`em.engine_invocation_id`), ``privileged()`` says whether
    this process may do all of that, and ``hook(point)`` is the test hook
    described in the module docstring.
    """

    _error_type = SwitchError

    def __init__(
        self,
        client: Any,
        *,
        target: Embedder | None = None,
        allow_pending: bool = False,
        configured: Embedder | None = None,
        apply_dropin: Callable[[Embedder], dict[str, Any]] | None = None,
        evidence: Callable[[], Mapping[str, Any]] | None = None,
        privileged: Callable[[], bool] | None = None,
        isolate: Callable[[int, str], dict[str, Any]] | None = None,
        release: Callable[[], dict[str, Any]] | None = None,
        stop_unit: Callable[[], dict[str, Any]] | None = None,
        isolated_port: Callable[[], int | None] | None = None,
        client_for: Callable[[str], Any] | None = None,
        listener: Callable[[int], Mapping[str, Any]] | None = None,
        unit_text: Callable[[], str | None] | None = None,
        unit_invocation: Callable[[], str | None] | None = None,
        pick_port: Callable[[], int] = em.free_loopback_port,
        hs_dir: Path | None = None,
        timeouts: em.Timeouts | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        progress: Callable[[str], None] | None = None,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(
            client,
            names=NAMES,
            apply_dropin=apply_dropin or self._default_apply_dropin,
            evidence=evidence,
            isolate=isolate,
            release=release,
            stop_unit=stop_unit,
            isolated_port=isolated_port,
            client_for=client_for,
            listener=listener,
            unit_text=unit_text,
            unit_invocation=unit_invocation,
            timeouts=timeouts,
            sleep=sleep,
            clock=clock,
            now=now,
            progress=progress,
            hook=hook,
        )
        self._pick_port = pick_port
        self._target = target
        self._allow_pending = allow_pending
        self._configured = configured
        self._privileged = privileged or self._default_privileged
        self._hs_dir = hs_dir
        #: The run this session carries (set by :meth:`run` and :meth:`resume`,
        #: never by :meth:`status`), which the engine's own work seen on an
        #: original after the flip is recorded in.
        self._run_journal: Journal | None = None

    def _refusal(self, message: str) -> SwitchError:
        return SwitchRefused(message)

    # ── host seams (defaults) ───────────────────────────────────────────

    def _engine_home(self) -> Path:
        if self._hs_dir is None:
            from hal0.memory.engine_upgrade import hindsight_dir

            self._hs_dir = hindsight_dir()
        return self._hs_dir

    @staticmethod
    def _default_privileged() -> bool:
        # Root only: isolating the engine writes a drop-in that replaces the
        # unit's ExecStart, and no wrapper verb hands that to the service
        # account (a root-side verb that accepts a command line is the kind
        # of thing #1740 removed).
        return os.geteuid() == 0

    @staticmethod
    def _default_apply_dropin(embedder: Embedder) -> dict[str, Any]:
        from hal0.config.loader import load_hal0_config
        from hal0.memory import extraction_env

        mem = load_hal0_config().memory
        return extraction_env.apply_showcase_dropin(
            models=mem.models,
            decision=mem.decision,
            lanes=mem.lanes,
            graph=mem.graph,
            restart=True,
            embedder=embedder,
        )

    def _configured_embedder(self) -> Embedder:
        if self._configured is None:
            from hal0.config.loader import load_hal0_config

            self._configured = embedder_record.configured_embedder(load_hal0_config().memory.models)
        return self._configured

    # ── engine calls ────────────────────────────────────────────────────

    async def content_state(self, bank: str) -> dict[str, Any]:
        """What ``bank`` holds, by id, as :data:`CONTENT_SIGNALS` describes.

        ``documents`` maps each document id to its stored-text length,
        ``facts`` each document id to a digest of its facts, and
        ``model_content`` each mental-model id to its refresh time and a
        digest of its content. Read for a bank's original once it is
        exported and for each bank restored from it: unlike :meth:`bank_state` it lists every
        document and fact, which a bank of a thousand documents answers in a
        few dozen requests.
        """
        base = em.bank_path(bank)
        documents = await self._list_all(
            f"{base}/documents",
            key="items",
            what="listing documents",
            bank=bank,
            page_size=_CONTENT_PAGE,
        )
        facts: list[Any] = []
        for kind in ("world", "experience"):
            facts += await self._list_all(
                f"{base}/memories/list",
                {"type": kind},
                key="items",
                what=f"listing {kind} facts",
                bank=bank,
                page_size=_CONTENT_PAGE,
            )
        models = await self._list_all(
            f"{base}/mental-models",
            {"detail": "content"},
            key="items",
            what="reading mental models",
            bank=bank,
        )
        by_document: dict[str, list[dict[str, Any]]] = {}
        for f in facts:
            if isinstance(f, dict):
                by_document.setdefault(str(f.get("document_id") or "(no document)"), []).append(
                    {
                        "type": f.get("fact_type"),
                        "text": f.get("text"),
                        "tags": sorted(str(t) for t in (f.get("tags") or [])),
                    }
                )
        return {
            # Not the engine's content_hash: a restore recomputes it (see
            # CONTENT_SIGNALS). The listing's text_length is LENGTH(original_text).
            "documents": {
                str(d.get("id")): d.get("text_length") for d in documents if isinstance(d, dict)
            },
            "facts": {doc: em.digest(rows) for doc, rows in by_document.items()},
            "model_content": {
                str(m.get("id")): {
                    "refreshed": m.get("last_refreshed_at"),
                    "content": em.digest(m.get("content")),
                }
                for m in models
                if isinstance(m, dict)
            },
        }

    # ── the engine: where it listens and what it runs ───────────────────

    # ── archives ────────────────────────────────────────────────────────

    async def _export_verified(self, journal: Journal, entry: BankEntry, index: int) -> None:
        """Export ``entry`` until the bank stood still for it and the archive agrees.

        The bank is read before the export is submitted and again once the
        archive is on disk. Only when the two reads agree, and the archive
        agrees with them, do the archive's counts and that fingerprint become
        the baseline every later comparison uses. An archive may hold fewer
        observations than the bank: the engine's export leaves out an
        observation whose source facts are gone, and that is noted, not
        refused.
        """
        last = ""
        for _attempt in range(3):
            before = await self._quiet_state(entry.bank)
            archived = await self._export_bank(journal, entry, index)
            after = await self.bank_state(entry.bank)
            moved = em.diff(before["counts"], after["counts"]) or em.signals_moved(
                before["fingerprint"], after["fingerprint"]
            )
            differs = em.diff(after["counts"], archived)
            extra = archived["observations"] - after["counts"]["observations"]
            if journal.allow_pending:
                # Operations may still be writing, so the archive, not the
                # moving bank, is what the rebuilt bank must match.
                moved, differs, extra = {}, {}, 0
            if moved:
                last = "it changed while it was being exported"
                continue
            if differs or extra > 0:
                shown = em.diff_text(differs) or f"observations {archived['observations']}"
                last = (
                    f"its archive does not match it: {shown} (the archive's count, then the "
                    "bank's). The engine's export leaves out a fact that belongs to no document"
                )
                continue
            entry.counts, entry.links = archived, after["links"]
            entry.fingerprint = after["fingerprint"]
            # With operations still writing, the bank as it is now is not what
            # the archive holds, so there is nothing exact to compare with.
            entry.contents = None if journal.allow_pending else await self.content_state(entry.bank)
            entry.note = (
                f"{-extra} observation(s) whose source facts are gone are not in the archive"
                if extra < 0
                else None
            )
            entry.step = STEP_EXPORTED
            self._save(journal)
            return
        raise SwitchError(
            f"bank {entry.bank} could not be exported: {last}. Nothing was changed",
            bank=entry.bank,
        )

    async def _restore_checked(
        self, journal: Journal, entry: BankEntry, *, via: str | None = None
    ) -> None:
        """Restore into the temporary id and require the result to match the archive."""
        state = await self._restore(journal, entry, entry.temp, embedder=self._target, via=via)
        entry.restored_counts = state["counts"]
        entry.restored_fingerprint = state["fingerprint"]
        differs = em.diff(entry.counts, state["counts"], observation_floor=True)
        if differs:
            self._save(journal)
            raise SwitchError(
                f"bank {entry.bank} did not come back whole: {em.diff_text(differs)}",
                bank=entry.bank,
                details={"differs": differs},
            )
        # The copy keeps the original's document and mental-model ids, so its
        # content is compared now, long before the original is deleted.
        lost = (
            _contents_moved(entry.contents, await self.content_state(entry.temp))
            if entry.contents
            else []
        )
        if lost:
            self._save(journal)
            raise SwitchError(
                f"bank {entry.bank} did not come back whole: its rebuilt copy {entry.temp} "
                f"differs from the original as it was archived: {'; '.join(lost)}. "
                f"{entry.bank} was not touched",
                bank=entry.bank,
                details={"contents": lost},
            )

    # ── journal, lock, record ───────────────────────────────────────────

    @staticmethod
    def _record_from(previous: Mapping[str, Any], *, mixed: bool | None = None) -> EmbedderRecord:
        return EmbedderRecord(
            provider=str(previous["provider"]),
            model=str(previous["model"]),
            source=str(previous.get("source") or SOURCE_SWITCH),
            mixed=bool(previous.get("mixed")) if mixed is None else mixed,
            conflict=previous.get("conflict")
            if isinstance(previous.get("conflict"), dict)
            else None,
        )

    # ── preflight ───────────────────────────────────────────────────────

    async def preflight(self, *, apply: bool = False) -> dict[str, Any]:
        """Everything a run needs to be true, checked without changing anything.

        Returns the plan: the embedders, the engine, every bank with its
        counts and queue depth, disk space, the estimated restore time, and
        ``refusals``, a list of ``{"code", "message"}``. A run goes ahead only
        when that list is empty. Nothing is written here, with or without
        ``apply``: not the embedder record, not a lock, not a directory.
        """
        target = self._target
        refusals: list[dict[str, str]] = []
        warnings: list[str] = []

        def refuse(code: str, message: str) -> None:
            refusals.append({"code": code, "message": message})

        plan: dict[str, Any] = {
            "target": target._asdict() if target is not None else None,
            "recorded": None,
            "mixed": False,
            "engine": {"reachable": False, "version": None},
            "banks": [],
            "totals": dict.fromkeys(em.COUNT_KEYS, 0),
            "disk": None,
            "estimate_s": None,
            "estimate": None,
            "ids": IDS_ACROSS_SWITCH,
            "allow_pending": self._allow_pending,
            "refusals": refusals,
            "warnings": warnings,
        }
        if target is None:
            refuse("no_target", "name the embedding model to switch to")
            return plan
        if not embedder_record._valid_pair(target.provider, target.model):
            refuse(
                "invalid_target",
                f"{target.provider!r} / {target.model!r} is not a provider and model "
                "[memory.models] would accept",
            )
            return plan

        left = interrupted_switch()
        if left is not None:
            refuse(
                "interrupted_switch",
                f"an earlier switch (run {left.get('run_id')}, phase {left.get('phase')}) did "
                "not finish; see `hal0 memory embedder switch --status`, then run it with "
                "--resume",
            )
        gate = write_gate.read_gate()
        if gate is not None:
            refuse(
                "gate_set",
                f"memory writes are already paused (run {gate.get('run_id')}, archives in "
                f"{gate.get('archive_dir')}); see `hal0 memory embedder switch --status`",
            )
        if not apply and lock_held():
            refuse("locked", "another `hal0 memory embedder switch` is running on this box")
        hs_dir = self._engine_home()
        if (hs_dir / ".venv.new").exists():
            refuse(
                "engine_upgrade",
                f"a memory-engine upgrade is in progress or was left unfinished ({hs_dir}/.venv.new "
                "exists); let `hal0 update` finish first",
            )
        if (hs_dir / ".pg0.restore-failed").exists():
            refuse(
                "engine_data_untrusted",
                f"{hs_dir}/.pg0.restore-failed marks the engine's data directory as untrusted "
                "after a failed upgrade rollback; run `hal0 update` to restore it first",
            )
        job = em.update_in_progress()
        if job is not None:
            refuse(
                "update_in_progress",
                f"`hal0 update` job {job} is queued or running; let it finish (it may upgrade "
                "and restart the memory engine)",
            )
        if apply and not self._privileged():
            refuse(
                "not_privileged",
                "run this as root (sudo): it moves the memory engine to a private port for "
                "the duration, which means writing a drop-in for the engine's unit, restarting "
                "the engine and replacing banks",
            )
        private = self._isolated_port()
        if private is not None:
            refuse(
                "engine_private",
                f"the memory engine is already isolated on a private port ({isolation_drop_in_path()} "
                "exists): an earlier switch left it there. See `hal0 memory embedder switch "
                "--status`",
            )
            # Look at the engine where it is, so that what it holds is in the report.
            self._use_port(private or None)
        # The unit: what isolating the engine would rewrite, and what would fight it.
        refusals.extend(em.unit_refusals(NAMES, self._unit_text(), self._normal_port()))

        # The record and the engine's environment. Read only: a run writes the
        # record down after every refusal has been evaluated.
        configured = self._configured_embedder()
        view = embedder_record.resolve_embedder(
            configured, hs_dir=hs_dir, evidence=self._evidence()
        )
        record = view.record
        plan["recorded"] = {**record.embedder._asdict(), "source": record.source}
        plan["mixed"] = record.mixed
        plan["configured"] = configured._asdict()
        if view.record_error:
            refuse(
                "record_unreadable",
                f"the embedder record at {view.record_error} cannot be read; repair or remove "
                "it first (hal0 never overwrites a record it cannot read)",
            )
        elif record.source == SOURCE_UNAVAILABLE:
            refuse(
                "store_unavailable",
                "the memory store's location is not available (a link that does not resolve, "
                "or a disk that is not mounted)",
            )
        elif record.source == SOURCE_CONFIGURED and not record.persisted:
            refuse(
                "no_store",
                "this box has no memory store yet, so there is nothing to re-embed: set "
                "[memory.models] and the engine will start on that embedder",
            )
        elif not record.persisted:
            warnings.append(
                f"the embedder record is not written down yet; a run records "
                f"{describe_embedder(*record.embedder)} first"
            )
        conflict = view.conflict
        if conflict and not view.record_error:
            said = "; ".join(
                f"{layer.replace('_', ' ')}: {describe_embedder(v['provider'], v['model'])}"
                for layer, v in conflict.items()
            )
            refuse(
                "record_conflict",
                f"the embedder record says {describe_embedder(*record.embedder)} but the engine "
                f"disagrees ({said}). Remove the override or correct the record, then run "
                "this again (see the memory_embedder row of `hal0 doctor all`)",
            )
        if view.live is None:
            refuse(
                "engine_environment",
                "the running engine's environment cannot be read (the engine is not running, "
                "or this account may not read it; use sudo). The switch confirms from it "
                "that the engine really moved",
            )
        if record.embedder == target and not record.mixed:
            refuse(
                "same_embedder",
                f"the store is already on {describe_embedder(*target)} and is not mixed; "
                "nothing to do",
            )

        # The engine.
        try:
            version = await self._client.request_json("GET", "/version", timeout_s=10.0)
        except httpx.HTTPError as exc:
            refuse(
                "engine_unreachable",
                f"the memory engine does not answer at {self._client.base_url} "
                f"({type(exc).__name__}); start it first",
            )
            return plan
        version = version if isinstance(version, dict) else {}
        features = version.get("features") if isinstance(version.get("features"), dict) else {}
        if isinstance(version.get("api_version"), str) and version["api_version"]:
            self._api_version = version["api_version"]
        plan["engine"] = {
            "reachable": True,
            "version": version.get("api_version"),
            "export_api": bool(features.get("document_export_api")),
            "import_api": bool(features.get("document_import_api")),
            "live": view.live._asdict() if view.live is not None else None,
        }
        if not features.get("document_export_api"):
            refuse(
                "export_disabled",
                "the engine's export API is off (features.document_export_api on /version); "
                "remove HINDSIGHT_API_ENABLE_DOCUMENT_EXPORT_API=false from its environment "
                "and restart it",
            )
        if not features.get("document_import_api"):
            refuse(
                "import_disabled",
                "the engine's import API is off (features.document_import_api on /version); "
                "remove HINDSIGHT_API_ENABLE_DOCUMENT_IMPORT_API=false from its environment "
                "and restart it",
            )

        # The banks. The engine's own refreshes and consolidations are not a
        # reason to refuse: a run waits for them (#3604), engine_ops_s for all
        # the banks together, not for each. A dry run names them. Work the
        # engine has parked until after that budget is refused at once, and a
        # dry run refuses it the same way, since --apply would.
        engine_work: dict[str, list[dict[str, Any]]] = {}
        parked_banks: set[str] = set()
        waited_from = self._clock()
        deadline = waited_from + self.t.engine_ops_s
        try:
            for bank in await self._bank_ids():
                state = await self.bank_state(bank)
                if not self._allow_pending and (state["pending"] or state["processing"]):
                    if apply:
                        state, own = await self._await_engine_work(bank, state, deadline=deadline)
                    else:
                        own = await self._engine_work(bank, state)
                    if own:
                        engine_work[bank] = own
                        parked = self._parked_past(own, deadline)
                        if parked and not apply:
                            error = self._parked_error(bank, parked, deadline)
                            parked_banks.add(bank)
                            refuse(
                                "engine_operation_parked",
                                "--apply would refuse: " + error.message,
                            )
                            plan.setdefault("engine_operations", []).extend(
                                error.details["parked_operations"]
                            )
                plan["banks"].append(
                    {k: state[k] for k in ("counts", "links", "pending", "processing")}
                    | {"bank": bank}
                )
                for key in em.COUNT_KEYS:
                    plan["totals"][key] += state["counts"][key]
                if (
                    isinstance(state["listed_facts"], int)
                    and state["listed_facts"] != state["nodes_total"]
                ):
                    warnings.append(
                        f"bank {bank}: the bank list says {state['listed_facts']} memories, its "
                        f"stats say {state['nodes_total']}; the switch goes by the stats"
                    )
        except SwitchError as exc:
            parked = "parked_operations" in exc.details
            refuse("engine_operation_parked" if parked else "engine_error", exc.message)
            if parked:
                plan["engine_operations"] = exc.details["parked_operations"]
            return plan
        leftovers = [b for b in plan["banks"] if is_switch_temp(b["bank"])]
        if leftovers:
            listing = "; ".join(
                f"{b['bank']} ({b['counts']['facts']} facts, {b['counts']['documents']} documents)"
                for b in leftovers
            )
            where = (gate or {}).get("archive_dir") or (left or {}).get("archive_dir")
            refuse(
                "leftover_banks",
                f"banks left by an earlier switch are still on the engine: {listing}"
                + (f"; its archives are in {where}" if where else "")
                + ". They may hold the only copy of a bank. See `hal0 memory embedder switch "
                "--status`; delete them only once every bank exists under its own id",
            )
        if engine_work:
            listing = "; ".join(em.operations_text(b, ops) for b, ops in engine_work.items())
            if apply:
                waited = self._clock() - waited_from
                refuse(
                    "engine_operations",
                    f"the engine's own work did not finish: {listing}. The switch waited "
                    f"{waited:.0f}s for it, its budget being {self.t.engine_ops_s:g}s for all "
                    "banks together, before anything is paused. The engine queues this by "
                    "itself (a mental-model refresh after a restore or a restart, consolidation "
                    "of new facts). Let it finish (`hal0 memory ops list` shows it), then run "
                    "the switch again",
                )
            elif listing := "; ".join(
                em.operations_text(b, ops)
                for b, ops in engine_work.items()
                if b not in parked_banks
            ):
                warnings.append(
                    f"the engine is running its own work: {listing}. The switch waits up to "
                    f"{self.t.engine_ops_s:g}s, for all banks together, for it to end before it "
                    "starts"
                )
        busy = [
            b
            for b in plan["banks"]
            if (b["pending"] or b["processing"]) and b["bank"] not in engine_work
        ]
        if busy:
            listing = ", ".join(
                f"{b['bank']} ({b['pending']} pending, {b['processing']} processing)" for b in busy
            )
            message = (
                f"the engine still has queued or running operations: {listing}. A queued "
                "retain is not in an export, so it would be lost with the old bank"
            )
            if self._allow_pending:
                warnings.append(
                    message + ". --allow-pending: going ahead; whatever those operations have "
                    "not written when a bank is exported is not carried over, and a bank that "
                    "moves after its export is replaced with a warning instead of a stop"
                )
            else:
                refuse(
                    "pending_operations",
                    message + ". Wait for them (`hal0 memory ops list` shows them), cancel them "
                    "(DELETE /api/memory/banks/<bank>/operations/<id>), or pass --allow-pending "
                    "to go ahead without them",
                )

        # Disk and time.
        plan["disk"] = self._disk_plan(hs_dir, refuse, warnings)
        embedded = plan["totals"]["facts"] + plan["totals"]["observations"]
        banks = len(plan["banks"])
        plan["estimate"] = {
            "restarts_s": round(RESTARTS_PER_RUN * RESTART_S),
            "per_bank_s": round(banks * (1 + RESTORES_PER_BANK) * OPERATION_FLOOR_S),
            "embedding_s": round(RESTORES_PER_BANK * embedded / em.RESTORE_FACTS_PER_S),
        }
        plan["estimate_s"] = sum(plan["estimate"].values())
        if target != configured:
            warnings.append(
                f"[memory.models] asks for {describe_embedder(*configured)}; after the switch "
                f"set embeddings_provider/embeddings_model to {describe_embedder(*target)} so "
                "config and store agree"
            )
        return plan

    @staticmethod
    def _disk_plan(
        hs_dir: Path, refuse: Callable[[str, str], None], warnings: list[str]
    ) -> dict[str, Any]:
        """Free space on the two filesystems a run fills: the engine's and the archives'."""
        store_bytes = em.du_bytes(hs_dir / em.PG_DIR)
        store_at = em.nearest_existing(hs_dir / em.PG_DIR)
        archive_at = em.nearest_existing(em.archive_root())
        store_free = shutil.disk_usage(store_at).free
        archive_free = shutil.disk_usage(archive_at).free
        store_need = int(store_bytes * STORE_FREE_FACTOR)
        archive_need = int(store_bytes * ARCHIVE_FREE_FACTOR)
        same = em.device(store_at) == em.device(archive_at)
        disk = {
            "archive_root": str(em.archive_root()),
            "store_bytes": store_bytes,
            "same_filesystem": same,
            "store_free_bytes": store_free,
            "store_needed_bytes": store_need + (archive_need if same else 0),
            "archive_free_bytes": archive_free,
            "archive_needed_bytes": archive_need,
        }
        if store_bytes == 0:
            warnings.append(
                f"could not measure the engine's data directory ({hs_dir}/{em.PG_DIR}); "
                "free space was not checked"
            )
            return disk
        if store_free < disk["store_needed_bytes"]:
            refuse(
                "disk_space",
                f"{store_free // 2**20} MiB free on the filesystem holding the engine's data "
                f"({store_at}), {disk['store_needed_bytes'] // 2**20} MiB wanted: the rebuilt "
                "banks sit beside the originals until the swap"
                + (", and the archives go on the same filesystem" if same else ""),
            )
        if not same and archive_free < archive_need:
            refuse(
                "disk_space",
                f"{archive_free // 2**20} MiB free under {archive_at} for the archives, "
                f"{archive_need // 2**20} MiB wanted",
            )
        return disk

    # ── the run ─────────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Preflight, then the whole switch. Raises :class:`SwitchRefused` or :class:`SwitchError`."""
        with self._lock():
            plan = await self.preflight(apply=True)
            if plan["refusals"]:
                raise SwitchRefused(plan["refusals"][0]["message"], plan)
            assert self._target is not None
            # Every refusal has been evaluated; only now is anything written.
            embedder_record.ensure_embedder_record(
                self._configured_embedder(), hs_dir=self._engine_home(), evidence=self._evidence()
            )
            previous = embedder_record.load_embedder_record()
            if previous is None:
                raise SwitchRefused("the embedder record could not be written down", plan)
            run_id = secrets.token_hex(4)
            try:
                archive_dir = self._make_archive_dir(run_id)
            except OSError as exc:
                raise SwitchRefused(
                    f"could not create the archive directory under {em.archive_root()}: {exc}", plan
                ) from exc
            journal = Journal(
                run_id=run_id,
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                pid=os.getpid(),
                phase=PHASE_PAUSED,
                target=self._target._asdict(),
                previous={
                    "provider": previous.provider,
                    "model": previous.model,
                    "source": previous.source,
                    "mixed": previous.mixed,
                    "conflict": previous.conflict,
                },
                archive_dir=str(archive_dir),
                allow_pending=self._allow_pending,
                private_port=self._pick_port(),
            )
            self._run_journal = journal
            self._save(journal)
            try:
                await self._pause(journal)
                await self._isolate_engine(journal)
                await self._export_all(journal)
                await self._flip(journal)
                await self._restore_all(journal)
                await self._check_before_swap(journal)
            except BaseException as exc:
                cause = (
                    exc.message if isinstance(exc, SwitchError) else f"{type(exc).__name__}: {exc}"
                )
                await self._rollback(journal, cause)
                if (
                    isinstance(exc, SwitchError)
                    and "foreign_port" in exc.details
                    and not journal.flip_started
                ):
                    # The unit was stopped for the foreign holder and the
                    # rollback has since put the engine back: the run is over.
                    raise SwitchError(
                        f"127.0.0.1:{exc.details['foreign_port']}, the engine's private port "
                        f"for this run, is held by {exc.details['foreign_holder']}, not by "
                        f"{em.SERVICE}. Nothing was exported; the run is undone and the engine is "
                        "back at its normal address. Stop that process (or wait for it to let "
                        "the port go) and run the switch again: it picks a new port each run"
                    ) from exc
                raise
            await self._swap_all(journal, rebuild=False)
            return await self._finish(journal)

    async def resume(self) -> dict[str, Any]:
        """Finish an interrupted run from its journal.

        A run that stopped inside the swap is carried forward; one that
        stopped anywhere earlier is rolled back (its originals are intact).
        The journal says which run it was and what each bank should match;
        what to do with each bank is decided from the engine as it is now.
        """
        with self._lock():
            found = locate_journal()
            journal: Journal | None = found["journal"]
            if journal is None:
                detail = await self._where_the_data_is()
                if found["error"]:
                    raise SwitchError(
                        f"the switch journal is not usable ({found['error']}) and no usable "
                        f"mirror of it was found. {detail} Nothing was changed"
                    )
                if write_gate.read_gate() is not None or self._isolated_port() is not None:
                    raise SwitchRefused(
                        "memory writes are paused, or the engine is on a private port, but no "
                        f"journal of a switch was found, so there is nothing to resume from. "
                        f"{detail} See `hal0 memory embedder switch --status`; `--clear-gate` "
                        "returns the engine to its normal address and lifts the pause once the "
                        "banks are in order"
                    )
                raise SwitchRefused("there is no interrupted embedder switch to resume")
            if found["error"]:
                self._say(f"the journal was read from its mirror {found['source']}")
            self._run_journal = journal
            target = Embedder(journal.target["provider"], journal.target["model"])
            if self._target is not None and self._target != target:
                raise SwitchRefused(
                    f"the interrupted switch was to {describe_embedder(*target)}, not "
                    f"{describe_embedder(*self._target)}; run --resume without a model"
                )
            if not self._privileged():
                raise SwitchRefused("run this as root (sudo)")
            self._target = target
            self._allow_pending = journal.allow_pending
            if write_gate.read_gate() is None:
                # Someone removed the pause by hand. Nothing below is safe
                # with writes coming in, so it goes back first.
                write_gate.engage_gate(run_id=journal.run_id, archive_dir=journal.archive_dir)
            self._save(journal)
            if journal.phase != PHASE_SWAPPING:
                await self._rollback(journal, journal.error or "the run was interrupted")
                return {
                    "status": "rolled_back",
                    "run_id": journal.run_id,
                    "archive_dir": journal.archive_dir,
                    "to": journal.previous,
                }
            try:
                await self._ensure_engine(
                    target, port=journal.private_port, run_id=journal.run_id, journal=journal
                )
            except SwitchError as exc:
                journal.error = exc.message
                self._save(journal)
                exc.resumable = True
                raise
            await self._swap_all(journal, rebuild=True)
            return await self._finish(journal)

    async def _where_the_data_is(self) -> str:
        """One sentence naming the banks and temporary ids present, for a run with no journal."""
        self._use_port(self._isolated_port() or None)
        try:
            ids = sorted(await self._bank_ids())
        except SwitchError:
            return "The engine does not answer, so the banks it holds could not be listed."
        temps = [b for b in ids if is_switch_temp(b)]
        return (
            f"Banks on the engine: {', '.join(b for b in ids if b not in temps) or 'none'}. "
            f"Banks under temporary ids from a switch: {', '.join(temps) or 'none'}."
        )

    async def _isolate_engine(self, journal: Journal) -> None:
        """Take the engine away from every other client, then list the banks this run rebuilds.

        The engine comes back on a private loopback port, still on its
        original embedder. From here to the end of the run nothing but this
        process can reach it, so the list taken now is complete and nothing
        can change under an export.
        """
        previous = Embedder(str(journal.previous["provider"]), str(journal.previous["model"]))
        self._say("moving the engine to a private port; recall is unavailable until the run ends")
        journal.isolated = True
        self._save(journal)
        await self._start_engine(
            previous, port=journal.private_port, run_id=journal.run_id, journal=journal
        )
        self._hook("isolated")
        # Whatever was accepted in the instant before the restart finishes now.
        states = await self._settle(journal)
        journal.banks = [
            BankEntry(
                bank=bank,
                temp=temp_bank_id(bank, journal.run_id),
                counts=states[bank]["counts"],
                links=states[bank]["links"],
            )
            for bank in states
        ]
        journal.phase = PHASE_EXPORTING
        self._save(journal)

    async def _stray_banks(self, journal: Journal) -> list[str]:
        """Banks on the engine that this run neither lists nor created."""
        known = {e.bank for e in journal.banks} | journal.own_ids()
        return [b for b in await self._bank_ids() if b not in known]

    async def _export_all(self, journal: Journal) -> None:
        for index, entry in enumerate(journal.banks):
            self._say(f"exporting {entry.bank} ({index + 1}/{len(journal.banks)})")
            await self._export_verified(journal, entry, index)
            self._hook(f"exported:{entry.bank}")
        # Before the embedder is flipped, while undoing the run still leaves
        # the store exactly as it was.
        strays = await self._stray_banks(journal)
        if strays:
            raise SwitchError(
                f"bank(s) {', '.join(strays)} appeared after the switch listed the banks, "
                "although the engine was isolated. Something on this box reaches the engine's "
                "private port. Stop it and run the switch again",
                details={"stray_banks": strays},
            )

    async def _flip(self, journal: Journal) -> None:
        assert self._target is not None
        target = self._target
        self._hook("before_flip")
        self._say(f"moving the engine to {describe_embedder(*target)}")
        journal.phase = PHASE_FLIPPING
        journal.flip_started = True
        journal.flipped_at = self._now().astimezone(UTC).isoformat()
        self._save(journal)
        # ``mixed`` until the swap is done: from here the engine embeds with
        # the target while every bank still holds the old model's vectors.
        embedder_record.save_embedder_record(
            EmbedderRecord(
                provider=target.provider, model=target.model, source=SOURCE_SWITCH, mixed=True
            )
        )
        await self._start_engine(
            target, port=journal.private_port, run_id=journal.run_id, journal=journal
        )
        self._hook("flipped")

    async def _restore_all(self, journal: Journal) -> None:
        journal.phase = PHASE_RESTORING
        self._save(journal)
        for index, entry in enumerate(journal.banks):
            self._say(f"rebuilding {entry.bank} ({index + 1}/{len(journal.banks)})")
            await self._restore_checked(journal, entry)
            entry.step = STEP_RESTORED
            self._save(journal)
            self._hook(f"restored:{entry.bank}")

    async def _original_moved(self, entry: BankEntry, *, parked_ok: bool = False) -> str:
        """How the original differs from what its archive was taken from; empty when it does not.

        With the engine isolated this should never be anything but empty. It
        is read once the engine is quiet, and observations are not part of
        it: the engine's own consolidation may add some, and the rebuilt bank
        derives them again from the same facts.

        With ``parked_ok`` the engine's own work it has parked past the wait
        is not waited for and is not a difference: it has not started
        (:meth:`~hal0.memory.engine_maintenance.EngineMaintenance._quiet_state`).
        """
        state = await self._quiet_state(entry.bank, parked_ok=parked_ok)
        reasons: list[str] = []
        differs = em.diff(entry.counts, state["counts"])
        if differs:
            reasons.append(em.diff_text(differs))
        signals = em.signals_moved(entry.fingerprint, state["fingerprint"])
        if signals:
            reasons.append("changed since its export: " + ", ".join(signals))
        pending = state["pending"] - len(state.get("parked") or [])
        if pending or state["processing"]:
            reasons.append(f"{pending} pending and {state['processing']} processing operations")
        return "; ".join(reasons)

    async def _live_operations(self, bank: str) -> list[dict[str, Any]]:
        """:meth:`EngineMaintenance._live_operations`, noting the engine's own work on an original.

        Once the engine has been restarted on the target, every refresh or
        consolidation seen live on an original, whatever its status, may
        write vectors of the target model there (a mental model's embedding,
        a knowledge page, an observation). The engine's other work
        (vector-index and graph maintenance) embeds nothing and is not
        recorded. It is recorded on the bank's entry and saved, so a
        rollback marks the store as mixed for that bank whether or not the
        work is still live by then (:meth:`_taint`). Recording goes on after
        a rollback has put the engine back: that can only add a mark, and a
        mark costs only a second run of the switch.
        """
        live = await super()._live_operations(bank)
        journal = self._run_journal
        if journal is None or not journal.flip_started:
            # Before the flip: the work runs on the previous embedder.
            return live
        entry = next((e for e in journal.banks if e.bank == bank), None)
        if entry is None or entry.step not in _BEFORE_DELETE:
            # Not an original, or one the swap has already replaced.
            return live
        known = {str(r.get("id")) for r in entry.engine_work}
        added = [
            em.operation_ref(bank, op)
            for op in live
            if em.operation_type(op) in em.ENGINE_EMBEDDING_TYPES
            and str(op.get("operation_id") or op.get("id")) not in known
        ]
        if added:
            entry.engine_work.extend(added)
            self._save(journal)
        return live

    async def _taint(self, journal: Journal, entry: BankEntry) -> str:
        """Why the original may hold vectors of the target model; empty when it cannot.

        For the rollback of a run that restarted the engine on the target,
        which must finish, so the rule is simple and fails closed: any doubt
        counts against the bank. The engine's operation rows cannot say
        whether a refresh or a consolidation wrote before it was deferred (a
        consolidation commits each sub-batch, and a quota reset or store
        backpressure then leaves the row ``pending`` with ``retry_count`` 0),
        so the bank is marked when any of these holds:

        * a row of an :data:`~hal0.memory.engine_maintenance.ENGINE_EMBEDDING_TYPES`
          type on it, of any status, was created or updated at or after
          ``journal.flipped_at``
          (:meth:`~hal0.memory.engine_maintenance.EngineMaintenance._engine_work_since`);
        * such work was seen live on it after the flip (``entry.engine_work``);
        * :meth:`_original_moved` gives a reason, its read failing included.

        Over-marking is cheap: a mixed record makes doctor warn and the
        operator run the switch again, which clears it. Under-marking would
        quietly degrade recall. Work done after the rollback restarted the
        engine on the previous embedder still marks: its row is not told
        apart. The rows are read after :meth:`_original_moved` has waited for
        the bank to go quiet; work the engine has parked is not waited for.

        A journal written before ``flipped_at`` existed counts from
        ``started_at``, the time the run began, before the pause: it can mark
        a bank for work that ran on the old embedder before the flip, but it
        never misses work that ran on the target.
        """
        moved = ""
        if not entry.engine_work:
            try:
                moved = await self._original_moved(entry, parked_ok=True)
            except SwitchError as exc:
                moved = f"it could not be checked ({exc.message})"
        if entry.engine_work:
            return "the engine ran its own work on it on the target embedder: " + ", ".join(
                f"{r.get('type')} (operation {r.get('id')})" for r in entry.engine_work
            )
        since = _parse_time(journal.flipped_at or journal.started_at)
        try:
            ran = await self._engine_work_since(entry.bank, since)
        except SwitchError as exc:
            return f"its operations could not be read ({exc.message})"
        if ran:
            entry.engine_work = [em.operation_ref(entry.bank, op) for op in ran]
            self._save(journal)
            return "the engine's own work on it may have run after the flip: " + em.operations_text(
                entry.bank, ran
            )
        return moved

    @staticmethod
    def _originals(journal: Journal) -> list[BankEntry]:
        """The banks a rollback hands back as they are: every one exported."""
        return [e for e in journal.banks if e.step != STEP_PLANNED]

    async def _last_look(self, journal: Journal) -> None:
        """Read every original's operations once more, after the engine is back on the old model.

        The per-bank check (:meth:`_taint`) takes the banks one at a time and
        each may wait for its bank to go quiet, so a refresh or consolidation
        can still run on a bank already found clean while a later one is
        checked, on the target. This one read of every original, wherever
        the engine now answers, marks a bank with any such row since
        ``journal.flipped_at`` (``started_at`` for an older journal). It
        over-counts on purpose: work the engine did after the rollback put it
        back on the previous embedder has a row too and marks as well, which
        costs only a second run of the switch. When the read fails, every
        original not marked yet is marked (fail closed) with the reason.
        Each verdict is saved.
        """
        since = _parse_time(journal.flipped_at or journal.started_at)
        originals = self._originals(journal)
        for entry in originals:
            try:
                ran = await self._engine_work_since(entry.bank, since)
            except SwitchError as exc:
                why = f"the rollback's last read of its operations failed ({exc.message})"
                for other in originals:
                    if not (other.taint or {}).get("mixed"):
                        _mark(other, why)
                self._save(journal)
                return
            if ran:
                _mark(
                    entry,
                    "the engine's own work on it may have run after the flip: "
                    + em.operations_text(entry.bank, ran),
                )
        self._save(journal)

    async def _check_before_swap(self, journal: Journal) -> None:
        """The last look before anything is deleted.

        No bank has appeared that this run does not rebuild, and every
        original still is what its archive was taken from.
        """
        strays = await self._stray_banks(journal)
        if strays:
            raise SwitchError(
                f"bank(s) {', '.join(strays)} appeared after the switch listed the banks, "
                "although the engine was isolated. Something on this box reaches the engine's "
                "private port. Stop it and run the switch again",
                details={"stray_banks": strays},
            )
        for entry in journal.banks:
            moved = await self._original_moved(entry)
            if not moved:
                continue
            if journal.allow_pending:
                entry.note = f"changed after its export and not carried over: {moved}"
                self._say(f"warning: {entry.bank} {entry.note}")
                continue
            raise SwitchError(
                f"bank {entry.bank} changed while the engine was isolated ({moved}). Something "
                "on this box reaches the engine's private port. Stop it and run the switch again",
                bank=entry.bank,
            )
        self._save(journal)

    # ── swap ────────────────────────────────────────────────────────────

    def _way_out(self, journal: Journal, bank: str) -> str:
        """What the operator does about a bank the switch will not touch by itself."""
        return (
            "look at it on the engine's private port, then delete the remains of the original "
            f"(`curl -X DELETE http://127.0.0.1:{journal.private_port}{em.bank_path(bank)}`); "
            "`hal0 memory embedder switch --resume` then restores the already-verified "
            "archive in its place"
        )

    async def _rebuilt_differs(self, entry: BankEntry, bank_id: str) -> str:
        """How the rebuilt bank, under ``bank_id``, differs from its archive; empty when not.

        Read once the engine is quiet. It may hold more observations than its
        archive (the engine consolidates what was not consolidated yet), never
        fewer. Its content (:data:`CONTENT_SIGNALS`) is compared with the
        original as it was exported: the copy keeps the original's document
        and mental-model ids, so a restore that loses content is found here,
        before the original is deleted, and not only once it is gone.
        """
        state = await self._quiet_state(bank_id)
        reasons: list[str] = []
        differs = em.diff(entry.counts, state["counts"], observation_floor=True)
        if differs:
            reasons.append(em.diff_text(differs))
        signals = em.signals_moved(entry.restored_fingerprint, state["fingerprint"])
        if signals:
            reasons.append("differs from the bank that was rebuilt in: " + ", ".join(signals))
        if entry.contents:
            lost = _contents_moved(entry.contents, await self.content_state(bank_id))
            if lost:
                reasons.append("differs from the original as it was archived: " + "; ".join(lost))
        return "; ".join(reasons)

    async def _rebuild(self, journal: Journal, entry: BankEntry, index: int) -> None:
        """Take the original as it is now and rebuild its replacement from that."""
        self._say(f"exporting and rebuilding {entry.bank} again")
        await self._delete_bank(entry.temp)
        entry.step = STEP_PLANNED
        entry.note = None
        self._save(journal)
        await self._export_verified(journal, entry, index)
        try:
            await self._restore_checked(journal, entry)
        except SwitchError as exc:
            if "contents" in exc.details:
                # Rebuilt from the original as it is now, and still short:
                # the restore itself drops this, and another resume will too.
                exc.message += (
                    ". It was exported and rebuilt again just now, so the restore itself "
                    "gives this result: if --resume reports it again, stop running it. "
                    f"{entry.bank} still holds everything and nothing will delete it; keep the "
                    "journal and the pause and report this with the output of "
                    "`hal0 memory embedder switch --status`"
                )
                exc.args = (exc.message,)
            raise
        entry.step = STEP_RESTORED
        self._save(journal)

    async def _placed_differs(self, entry: BankEntry) -> str:
        """How the bank under its own id differs from its original as archived; empty when not.

        Unlike the rebuilt copy, the bank restored into its own id must come
        back as the original was, ids included: every fingerprint signal is
        compared with the original's at its export, the knowledge-base tree
        and the directives (both digested with their ids) among them. Read
        once the engine is quiet.
        """
        state = await self._quiet_state(entry.bank)
        reasons: list[str] = []
        differs = em.diff(entry.counts, state["counts"], observation_floor=True)
        if differs:
            reasons.append(em.diff_text(differs))
        signals = em.signals_moved(entry.fingerprint, state["fingerprint"])
        if entry.contents:
            signals += _contents_moved(entry.contents, await self.content_state(entry.bank))
        if signals:
            reasons.append("differs from the original as it was archived: " + ", ".join(signals))
        return "; ".join(reasons)

    def _place_repeats_message(self, journal: Journal, entry: BankEntry, wrong: str) -> str:
        """What the operator is told once restores into the own id keep failing the same way."""
        return (
            f"bank {entry.bank} was restored into its own id {entry.place_repeats} times and "
            f"failed its check the same way each time ({wrong}), although its rebuilt copy "
            "matched the original, content included, before the original was deleted. "
            "Restoring it again gives the same result, so the switch will not delete or "
            f"restore {entry.bank} again: stop running --resume. What it holds of the bank: "
            f"{entry.bank} as the engine last restored it; the rebuilt copy {entry.temp}, "
            "kept if it is still listed; and the archive "
            f"{journal.archive_dir}/{entry.archive}, taken before anything was deleted. "
            "Writes stay paused. Keep the journal as it is and report this with the output "
            "of `hal0 memory embedder switch --status`"
        )

    async def _ensure_scratch(self, journal: Journal) -> None:
        """Create the empty bank a restore into a bank's own id is recorded against.

        The engine records a restore against the bank in the URL and refuses
        a target that exists, so the URL cannot be the bank being restored.
        """
        if journal.scratch not in set(await self._bank_ids()):
            await self._json(
                "PUT",
                em.bank_path(journal.scratch),
                body={},
                what="creating the scratch bank",
                bank=journal.scratch,
            )

    async def _place(self, journal: Journal, entry: BankEntry) -> None:
        """Restore ``entry``'s archive into the bank's own id, which must not exist.

        The step is saved as ``placing`` before the restore is sent, so a bank
        found under that id later is known to be this run's. A restore this
        run sent earlier and never saw finish is waited for first, never
        cancelled: two restores must not write the same bank.
        """
        await self._await_restore(journal, entry)
        await self._ensure_scratch(journal)
        self._hook(f"before_place:{entry.bank}")
        entry.step = STEP_PLACING
        self._save(journal)
        await self._restore(journal, entry, entry.bank, embedder=self._target, via=journal.scratch)

    async def _swap_one(
        self, journal: Journal, entry: BankEntry, index: int, *, rebuild: bool
    ) -> None:
        """Put the bank back under its own id, with its ids. Safe to run again after any stop.

        What to do comes from :func:`swap_action`: the ids the engine has now,
        with the journal step as tie-breaker. Each destructive call is
        preceded by a synced journal save naming it. The rebuilt copy is
        deleted only once the bank under its own id matches the original as
        archived. On a resume (``rebuild``) anything that can be repaired from
        the original or from the archive is repaired, so the same resume never
        fails twice for a reason the switch could have fixed.
        """
        bank, temp = entry.bank, entry.temp
        existing = set(await self._bank_ids())
        action = swap_action(entry.step, bank in existing, temp in existing)

        if action == ACTION_FINAL:
            return
        if action == ACTION_RECREATED:
            raise SwitchError(
                f"bank {bank} exists again after the switch deleted it, beside its rebuilt "
                f"copy {temp}. The switch will not delete it a second time. To go on: "
                + self._way_out(journal, bank),
                bank=bank,
            )
        if action == ACTION_DELETE_SENT:
            raise SwitchError(
                f"the run stopped while deleting bank {bank}, and {bank} is still there: the "
                "delete may have been sent and may have removed part of it, so the switch will "
                "neither delete it again nor export it again. Its archive, taken before the "
                f"delete, is {journal.archive_dir}/{entry.archive}. To go on: "
                + self._way_out(journal, bank),
                bank=bank,
            )
        if action == ACTION_REBUILD:
            if not rebuild:
                raise SwitchError(
                    f"the rebuilt bank {temp} is gone and {bank} still exists; nothing was "
                    f"deleted. Run --resume, which rebuilds it from {bank}",
                    bank=bank,
                )
            await self._rebuild(journal, entry, index)
            action = ACTION_SWAP

        if action == ACTION_SWAP:
            # Never delete an original without a whole replacement in hand,
            # and never one that is no longer what was archived.
            wrong = await self._rebuilt_differs(entry, temp)
            moved = "" if wrong else await self._original_moved(entry)
            if moved and journal.allow_pending:
                entry.note = f"changed after its export and not carried over: {moved}"
                self._say(f"warning: {bank} {entry.note}")
                moved = ""
            if (wrong or moved) and rebuild:
                await self._rebuild(journal, entry, index)
                wrong = await self._rebuilt_differs(entry, temp)
                moved = "" if wrong or journal.allow_pending else await self._original_moved(entry)
            if wrong:
                again = (
                    "It was rebuilt again just now and still does not match, so the restore "
                    "itself gives this result: stop running --resume, which would only rebuild "
                    f"it once more. {bank} is untouched and still holds everything; nothing "
                    "will delete it. Keep the journal and the pause as they are and report "
                    "this with the output of `hal0 memory embedder switch --status`"
                    if rebuild
                    else "Run --resume, which rebuilds it"
                )
                raise SwitchError(
                    f"the rebuilt bank {temp} does not match the archive of {bank} ({wrong}); "
                    f"{bank} was not touched. {again}",
                    bank=bank,
                )
            if moved:
                raise SwitchError(
                    f"bank {bank} changed after its archive was taken ({moved}), so it was not "
                    f"deleted, although the engine was isolated. {bank} and every bank after "
                    "it are still on their originals; the banks before it are already "
                    "rebuilt. Find what reaches the engine's private port and stop it, then "
                    f"run --resume, which exports and rebuilds {bank} again from what it holds "
                    "now and carries on",
                    bank=bank,
                )
            self._hook(f"before_delete:{bank}")
            entry.step = STEP_DELETING
            self._save(journal)
            await self._delete_bank(bank)
            entry.step = STEP_DELETED
            self._save(journal)
            self._hook(f"deleted:{bank}")
            action = ACTION_PLACE

        if action == ACTION_CHECK_PLACED:
            # A restore into the own id that this run sent and did not see
            # through: let it end, then keep the bank only if it is whole.
            await self._await_restore(journal, entry)
            wrong = await self._placed_differs(entry)
            if wrong and entry.place_repeats >= 2 and wrong == entry.place_wrong:
                raise SwitchError(self._place_repeats_message(journal, entry, wrong), bank=bank)
            if wrong:
                self._say(f"{bank} under its own id is not whole ({wrong}); restoring it again")
                await self._delete_bank(bank)
                action = ACTION_PLACE

        if action == ACTION_PLACE:
            self._say(f"restoring {bank} into its own id")
            await self._place(journal, entry)
            wrong = await self._placed_differs(entry)
            if wrong:
                if wrong == entry.place_wrong:
                    entry.place_repeats += 1
                else:
                    entry.place_wrong, entry.place_repeats = wrong, 1
                self._save(journal)
                if entry.place_repeats >= 2:
                    raise SwitchError(self._place_repeats_message(journal, entry, wrong), bank=bank)
                kept = (
                    f"its rebuilt copy {temp} is kept"
                    if temp in set(await self._bank_ids())
                    else "it has no rebuilt copy"
                )
                raise SwitchError(
                    f"bank {bank}, restored into its own id, does not match the original as it "
                    f"was archived ({wrong}); {kept}, and its archive is "
                    f"{journal.archive_dir}/{entry.archive}. Run --resume, which restores it "
                    "again once; if it fails the same way, the switch stops trying",
                    bank=bank,
                )
        elif action == ACTION_VERIFY:
            # A bank under the own id after its original was deleted, which
            # this run did not put there.
            wrong = await self._placed_differs(entry)
            if wrong:
                raise SwitchError(
                    f"bank {bank} is under its final id but does not match its archive "
                    f"({wrong}). Nothing more was changed. Its archive is "
                    f"{journal.archive_dir}/{entry.archive}. To go on: "
                    + self._way_out(journal, bank),
                    bank=bank,
                )

        if entry.step != STEP_PLACED:
            entry.step = STEP_PLACED
            self._save(journal)
            self._hook(f"placed:{bank}")
        # The bank is under its own id and matched: its rebuilt copy has done its job.
        if temp in set(await self._bank_ids()):
            await self._wait_quiet(temp)
            await self._delete_bank(temp)

    async def _swap_all(self, journal: Journal, *, rebuild: bool) -> None:
        # The journal of per-bank swaps goes to disk before any original is touched.
        journal.phase = PHASE_SWAPPING
        journal.error = None
        self._save(journal)
        self._hook("before_swap")
        for index, entry in enumerate(journal.banks):
            self._say(f"swapping {entry.bank} into place ({index + 1}/{len(journal.banks)})")
            try:
                # A bank journalled as placed goes through this too: the step
                # is only believed once the engine agrees.
                await self._swap_one(journal, entry, index, rebuild=rebuild)
            except SwitchError as exc:
                journal.error = exc.message
                self._save(journal)
                exc.resumable = True
                raise

    async def _finish(self, journal: Journal) -> dict[str, Any]:
        """Close the run: record the embedder, give the engine back, then let writes in."""
        self._hook("before_finish")
        target = Embedder(journal.target["provider"], journal.target["model"])
        try:
            present = set(await self._bank_ids())
            if journal.scratch in present:
                await self._delete_bank(journal.scratch)
            strays = await self._stray_banks(journal)
            journal.stray_banks = strays
            embedder_record.save_embedder_record(
                EmbedderRecord(
                    provider=target.provider,
                    model=target.model,
                    source=SOURCE_SWITCH,
                    mixed=bool(strays),
                )
            )
            self._say("returning the engine to its normal address")
            await self._start_engine(target, port=None, run_id=journal.run_id, journal=journal)
        except SwitchError as exc:
            journal.error = exc.message
            self._save(journal)
            exc.resumable = True
            raise
        journal.isolated = False
        journal.phase = PHASE_COMPLETE
        # The journal first, then the gate: the other order would let writes
        # in under a journal that still says the run is unfinished.
        self._retire_journal(journal)
        write_gate.lift_gate()
        self._say("memory writes resumed")
        warnings = [f"{e.bank}: {e.note}" for e in journal.banks if e.note]
        if strays:
            warnings.append(
                f"bank(s) {', '.join(strays)} appeared during the switch and were not rebuilt, "
                "so the store is recorded as mixed. Run `hal0 memory embedder switch "
                f"{target.model}` again: a mixed store may be switched to the model it is on, "
                "and that run rebuilds every bank"
            )
        configured = self._configured_embedder()
        config_note = None
        if configured != target:
            config_note = (
                f"hal0.toml still names {describe_embedder(*configured)} under [memory.models]. "
                "That is fine: the engine runs the embedder the store was built with, whatever "
                "the config says, and nothing will change it back. To make the two agree, set "
                f'embeddings_provider = "{target.provider}" and embeddings_model = '
                f'"{target.model}" under [memory.models]'
            )
        return {
            "status": "switched",
            "run_id": journal.run_id,
            "from": {k: journal.previous.get(k) for k in ("provider", "model")},
            "to": journal.target,
            "mixed": bool(strays),
            "stray_banks": strays,
            "banks": [
                {"bank": e.bank, "counts": e.counts, "links": e.links, "note": e.note}
                for e in journal.banks
            ],
            "warnings": warnings,
            "config_note": config_note,
            "archive_dir": journal.archive_dir,
            "delete_archives": f"rm -r {journal.archive_dir}",
        }

    # ── rollback ────────────────────────────────────────────────────────

    async def _await_restores(self, journal: Journal) -> None:
        """Wait until every restore that may still run has ended (see :meth:`_await_restore`)."""
        for entry in journal.banks:
            await self._await_restore(journal, entry)

    async def _sweep(self, journal: Journal) -> None:
        """Delete every id this run created that the engine still has."""
        own = journal.own_ids()
        for bank in await self._bank_ids():
            if bank in own:
                await self._delete_bank(bank)

    async def _rollback(self, journal: Journal, cause: str) -> None:
        """Undo a run that has not reached the swap. Safe to run again.

        The run's leftovers are removed while the engine is still private.
        The engine is then returned to its normal address on the previous
        embedder, and only once it is confirmed running that embedder is the
        previous record written back; until then the record keeps saying the
        target, ``mixed``. The journal is retired before the gate is lifted.

        Raises :class:`SwitchError` (``resumable``) when a step cannot be
        undone; the pause then stays and ``--resume`` tries again.
        """
        previous = Embedder(str(journal.previous["provider"]), str(journal.previous["model"]))
        self._say(f"undoing the switch: {cause}")
        try:
            journal.phase = PHASE_ROLLING_BACK
            journal.error = cause
            self._save(journal)
            tainted: list[str] = []
            reasons: list[str] = []
            if journal.isolated and self._isolated_port() == journal.private_port:
                self._use_port(journal.private_port)
                # /health on the private port proves only that something
                # answers there; it is the engine when the unit holds the socket.
                owner = self._listener_owner(journal.private_port or 0).get("owner")
                private = await self._healthy() and owner == "unit"
                taken = (
                    not private
                    and journal.engine_stopped
                    and not journal.flip_started
                    and owner == "other"
                )
                if taken:
                    # Stopped for a foreign holder before anything ran on the
                    # target: nothing to check on that port. The engine goes
                    # back to its normal address below, where the sweep runs.
                    self._say("the private port is still held by another process; skipping it")
                elif not private and not await self._public_answers():
                    # Not answering anywhere: bring it up where the run left
                    # it, on the previous embedder, so the cleanup is isolated.
                    await self._start_engine(
                        previous,
                        port=journal.private_port,
                        run_id=journal.run_id,
                        journal=journal,
                    )
                    private = True
                # Still answering at its normal address means the isolation
                # never took, and then nothing was ever done behind it.
                if private:
                    await self._await_restores(journal)
                    await self._sweep(journal)
                if private and journal.flip_started and journal.banks:
                    # The engine ran the target. A bank this run does not know,
                    # or an original that changed meanwhile, may hold vectors
                    # of the target model in a store going back to the old one.
                    # Each verdict is saved as it is reached: the engine leaves
                    # this port below, and a resume cannot check here again.
                    strays = await self._stray_banks(journal)
                    if strays:
                        journal.stray_banks = list(dict.fromkeys([*journal.stray_banks, *strays]))
                        self._save(journal)
                    for entry in journal.banks:
                        if entry.step == STEP_PLANNED or entry.taint is not None:
                            continue
                        why = await self._taint(journal, entry)
                        _mark(entry, why, mixed=bool(why))
                        self._save(journal)
            if journal.flip_started:
                # An original whose check never completed, here or in an
                # earlier attempt, counts against the store: fail closed.
                for entry in self._originals(journal):
                    if entry.taint is None:
                        _mark(entry, "the rollback could not check it on the engine's private port")
                self._save(journal)
            if journal.isolated:
                await self._ensure_engine(
                    previous, port=None, run_id=journal.run_id, journal=journal
                )
                await self._sweep(journal)
            if journal.flip_started:
                await self._last_look(journal)
                # Work the journal saw on an original after the flip counts even
                # when the engine no longer lists its row.
                for entry in self._originals(journal):
                    if entry.engine_work:
                        _mark(entry, "the engine's own work was seen on it after the flip")
                self._save(journal)
                marked = [e for e in self._originals(journal) if (e.taint or {}).get("mixed")]
                tainted = list(dict.fromkeys([*journal.stray_banks, *(e.bank for e in marked)]))
                reasons = [f"{e.bank}: {e.taint['reason']}" for e in marked if e.taint["reason"]]
                # Only now: the engine is confirmed on the previous embedder.
                embedder_record.save_embedder_record(
                    self._record_from(
                        journal.previous,
                        mixed=bool(journal.previous.get("mixed")) or bool(tainted),
                    )
                )
                if tainted and not journal.previous.get("mixed"):
                    self._say(
                        "warning: " + ", ".join(tainted) + " appeared, changed or had the "
                        "engine's own work run on it while the engine ran the target embedder, "
                        "so it may hold vectors of that model; the store is now recorded as "
                        "mixed" + (f" ({'; '.join(reasons)})" if reasons else "")
                    )
            journal.stray_banks = tainted
            journal.isolated = False
            journal.phase = PHASE_ROLLED_BACK
            # The journal first, then the gate.
            self._retire_journal(journal)
            write_gate.lift_gate()
            self._say("memory writes resumed; the store is on its original embedder")
        except BaseException as exc:
            detail = exc.message if isinstance(exc, SwitchError) else f"{type(exc).__name__}: {exc}"
            journal.phase = PHASE_ROLLBACK_FAILED
            journal.error = f"{cause}; and undoing it failed: {detail}"
            with contextlib.suppress(OSError):
                self._save(journal)
            if isinstance(exc, Exception):
                where = (
                    f"on the journalled private port 127.0.0.1:{journal.private_port}"
                    if journal.private_port is not None
                    else "from the journal"
                )
                raise SwitchError(
                    f"the switch failed ({cause}) and then the rollback failed too ({detail}). "
                    f"The journal is in phase {PHASE_ROLLBACK_FAILED}; the write pause is still "
                    "set and recall stays unavailable while the engine is isolated. Fix that, "
                    f"then run `hal0 memory embedder switch --resume`: it retries the rollback "
                    f"{where}. `--status` shows where things stand",
                    resumable=True,
                ) from exc
            raise

    # ── status and the escape hatch ─────────────────────────────────────

    async def _assess(
        self,
        journal: Journal,
        entry: BankEntry,
        present: set[str],
        live: Mapping[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """What ``--resume`` would do for one bank, with the checks it would run.

        Each check reads the bank once and does not wait for the engine
        (``status`` runs it with :attr:`_no_wait`). ``live`` is every live
        operation by bank, as ``status`` listed them: a problem found on a
        bank the engine is still working on says so, since ``--resume`` waits
        for the engine's own work before it checks.
        """
        action = swap_action(entry.step, entry.bank in present, entry.temp in present)
        problem: str | None = None
        operator: str | None = None
        try:
            if action == ACTION_SWAP:
                wrong = await self._rebuilt_differs(entry, entry.temp)
                moved = "" if wrong else await self._original_moved(entry)
                if wrong:
                    problem = f"the rebuilt bank does not match its archive ({wrong})"
                    operator = "nothing: --resume rebuilds it from the original"
                elif moved:
                    problem = f"the original changed after its archive was taken ({moved})"
                    operator = (
                        "nothing: --resume replaces it as archived, with a warning"
                        if journal.allow_pending
                        else "stop whatever writes to the engine's private port; --resume then "
                        "exports and rebuilds it again"
                    )
            elif action == ACTION_VERIFY:
                wrong = await self._placed_differs(entry)
                if wrong:
                    problem = f"the bank under its final id does not match its archive ({wrong})"
                    operator = self._way_out(journal, entry.bank)
            elif action == ACTION_CHECK_PLACED:
                wrong = await self._placed_differs(entry)
                if wrong:
                    problem = f"the bank under its own id is not whole yet ({wrong})"
                    operator = (
                        self._place_repeats_message(journal, entry, wrong)
                        if entry.place_repeats >= 2 and wrong == entry.place_wrong
                        else "nothing: --resume deletes it and restores it again"
                    )
            elif action == ACTION_RECREATED:
                problem = "the original id exists again beside the rebuilt bank"
                operator = self._way_out(journal, entry.bank)
            elif action == ACTION_DELETE_SENT:
                problem = "the run stopped while deleting the original, which is still there"
                operator = self._way_out(journal, entry.bank)
        except SwitchError as exc:
            problem = exc.message
        working = [r for b in (entry.bank, entry.temp) for r in (live or {}).get(b, [])]
        own = [r for r in working if r["type"] in em.ENGINE_OPERATION_TYPES]
        if problem and working:
            problem += (
                " (read while "
                + ", ".join(
                    f"{r['type']} on {r['bank']} (operation {r['id']}, {r['status']})"
                    for r in working
                )
                + " was live"
                + (
                    f"; --resume waits up to {self.t.engine_ops_s:g}s for the engine's own work "
                    "to end before it checks"
                    if own
                    else ""
                )
                + ")"
            )
        return {
            "bank": entry.bank,
            "temp": entry.temp,
            "step": entry.step,
            "operations": working,
            "original_present": entry.bank in present,
            "rebuilt_present": entry.temp in present,
            "action": action,
            "problem": problem,
            "operator": operator,
        }

    async def status(self) -> dict[str, Any]:
        """Where a switch stands: journal, pause, engine, banks, and what ``--resume`` would do.

        Reads only, and never waits: each bank is read once, quiet or not, and
        the operations live on it (the engine's own refreshes and
        consolidations among them) are listed in ``live_operations`` and per
        bank rather than waited for. It runs the same checks a resume would,
        so a bank that moved or does not match is reported here first. Works
        without a journal and without the engine.
        """
        self._no_wait = True
        try:
            return await self._status()
        finally:
            self._no_wait = False

    async def _status(self) -> dict[str, Any]:
        found = locate_journal()
        journal: Journal | None = found["journal"]
        port = self._isolated_port()
        out: dict[str, Any] = {
            "journal": journal.to_dict() if journal is not None else None,
            "journal_source": found["source"],
            "journal_error": found["error"],
            "gate": write_gate.read_gate(),
            "lock_held": lock_held(),
            "engine": {"reachable": False, "live": None, "private_port": port},
            "record": None,
            "banks": [],
            "live_operations": [],
            "resume": [],
            "summary": "",
        }
        live = em.evidence_pair(self._evidence().get("live"))
        out["engine"]["live"] = live._asdict() if live is not None else None
        try:
            record = embedder_record.load_embedder_record()
        except embedder_record.EmbedderRecordError:
            record = None
        if record is not None:
            # While a run is unfinished the record and the engine may differ;
            # which one is right is the journal's to say, and it is shown here.
            out["record"] = {
                **record.embedder._asdict(),
                "mixed": record.mixed,
                "agrees_with_engine": live is None or live == record.embedder,
            }
        self._use_port(port or None)
        entries = {e.bank: e for e in journal.banks} if journal is not None else {}
        temps = {e.temp: e for e in journal.banks} if journal is not None else {}
        present: set[str] = set()
        live_ops: dict[str, list[dict[str, Any]]] = {}
        holder: Mapping[str, Any] | None = None
        owner: Any = None
        if port is not None:
            # Whatever answers on the private port is the engine only if the
            # engine's own unit holds the socket (the check a resume runs).
            seen = self._listener_owner(port)
            owner = seen.get("owner")
            if owner == "other":
                holder = seen
        # The journal's flag is a note about the past; the live listener says
        # whether the engine is asked: not when a foreign process holds the
        # port or nothing listens on it.
        stopped = journal is not None and journal.engine_stopped
        out["engine"]["stopped_by_switch"] = stopped
        if owner == "unknown":
            out["engine"]["owner_unconfirmed"] = True
        out["engine"]["port_holder"] = holder.get("holder") if holder is not None else None
        if holder is not None:
            out["engine"]["error"] = (
                f"port {port} held by {holder.get('holder')}, not by {em.SERVICE}"
            )
        try:
            if holder is not None or (stopped and owner == "none"):
                raise _EngineNotAsked
            for bank in await self._bank_ids():
                present.add(bank)
                if bank in entries:
                    role = "original or rebuilt"
                elif bank in temps:
                    role = f"temporary (rebuilt copy of {temps[bank].bank})"
                elif journal is not None and bank == journal.scratch:
                    role = "scratch"
                elif is_switch_temp(bank):
                    role = "temporary (not from the journalled run)"
                else:
                    role = "not in the journal" if journal is not None else "bank"
                try:
                    ops: list[dict[str, Any]] | None = [
                        em.operation_ref(bank, op) for op in await self._live_operations(bank)
                    ]
                except SwitchError:
                    ops = None
                if ops:
                    live_ops[bank] = ops
                    out["live_operations"] += ops
                out["banks"].append(
                    {
                        "bank": bank,
                        "role": role,
                        "counts": (await self.bank_state(bank))["counts"],
                        "operations": ops,
                    }
                )
            out["engine"]["reachable"] = True
            if stopped:
                out["engine"]["stopped_by_switch"] = False
                out["engine"]["running_again"] = True
        except _EngineNotAsked:
            pass
        except SwitchError as exc:
            out["engine"]["error"] = exc.message
        held = (
            f" The engine is isolated on 127.0.0.1:{port} and memory writes are paused; both "
            "stay so until then, and recall is unavailable."
            if port is not None
            else ""
        )
        if journal is not None and port is None and journal.isolated:
            # The run released the engine (its last restart, at finish or at
            # rollback) and stopped before it lifted the gate.
            out["engine"]["released_early"] = True
            held = (
                " The engine's isolation drop-in is already gone, so the engine is at its "
                "normal address (or returns there at its next start): other clients can reach "
                "it and recall works, but memory writes through hal0 stay paused. --resume "
                "isolates the engine again before it checks or changes anything, then finishes "
                "and lifts the pause."
            )
        if journal is None:
            if found["error"]:
                out["summary"] = (
                    "the journal cannot be used and no mirror was found; --resume will refuse. "
                    "The banks above are where the data is." + held
                )
            elif out["gate"] is not None or port is not None:
                out["summary"] = (
                    "memory writes are paused or the engine is on a private port, but there is "
                    "no journal: nothing to resume. --clear-gate returns the engine to its "
                    "normal address and lifts the pause once the banks above are in order"
                )
            else:
                out["summary"] = "no switch is in progress"
            return out
        if journal.phase != PHASE_SWAPPING:
            out["summary"] = (
                f"the run stopped in phase {journal.phase}, before any original was replaced: "
                "--resume undoes it (temporary banks deleted, engine back at its normal address "
                f"on {journal.previous.get('model')}, pause lifted)." + held
            )
            return out
        if out["engine"]["reachable"]:
            for entry in journal.banks:
                out["resume"].append(await self._assess(journal, entry, present, live_ops))
        blocked = [
            r for r in out["resume"] if r["operator"] and not r["operator"].startswith("nothing")
        ]
        out["summary"] = (
            "the run stopped inside the swap: --resume finishes it bank by bank as listed, "
            "then returns the engine to its normal address and lifts the pause."
            + (f" {len(blocked)} bank(s) need the operator first, as listed." if blocked else "")
            + held
        )
        return out

    async def clear_gate(self) -> dict[str, Any]:
        """Lift the write pause, and return the engine to its normal address, by hand.

        For the one case nothing else covers: the pause is set, or the engine
        is on a private port, and no journal of a run survives anywhere.
        Refuses while any run is unfinished or running. It checks nothing
        about the banks.
        """
        if lock_held():
            raise SwitchRefused("a `hal0 memory embedder switch` is running; it owns the pause")
        found = locate_journal()
        if found["journal"] is not None:
            raise SwitchRefused(
                f"an embedder switch (run {found['journal'].run_id}, phase "
                f"{found['journal'].phase}) is unfinished; lifting the pause now would let "
                "writes into banks that are about to be replaced. Run "
                "`hal0 memory embedder switch --resume`"
            )
        if found["error"]:
            raise SwitchRefused(
                f"a switch journal exists but cannot be used ({found['error']}), so an "
                "unfinished swap cannot be ruled out. Check `hal0 memory embedder switch "
                "--status`, put the banks in order, remove that file, then run this again"
            )
        gate = write_gate.read_gate()
        port = self._isolated_port()
        if gate is None and port is None:
            raise SwitchRefused(
                "memory writes are not paused and the engine is at its normal address"
            )
        if port is not None:
            if not self._privileged():
                raise SwitchRefused("run this as root (sudo): the engine has to be restarted")
            record = embedder_record.load_embedder_record()
            embedder = (
                record.embedder
                if record is not None
                else em.evidence_pair(self._evidence().get("live")) or self._configured_embedder()
            )
            await self._start_engine(embedder, port=None, run_id="clear-gate")
        if gate is not None:
            write_gate.lift_gate()
        log.warning("hal0.memory.write_gate_cleared_by_hand", run_id=(gate or {}).get("run_id"))
        return {"status": "cleared", "gate": gate, "was_private_on": port}


__all__ = [
    "ACTION_CHECK_PLACED",
    "ACTION_DELETE_SENT",
    "ACTION_DROP_COPY",
    "ACTION_FINAL",
    "ACTION_PLACE",
    "ACTION_REBUILD",
    "ACTION_RECREATED",
    "ACTION_SWAP",
    "ACTION_VERIFY",
    "CONTENT_SIGNALS",
    "HOOK_POINTS",
    "IDS_ACROSS_SWITCH",
    "NAMES",
    "RESTORES_PER_BANK",
    "BankEntry",
    "EmbedderSwitch",
    "Journal",
    "SwitchError",
    "SwitchRefused",
    "blocking_reason",
    "interrupted_switch",
    "is_switch_temp",
    "isolated_port",
    "isolation_drop_in_path",
    "journal_path",
    "load_journal",
    "locate_journal",
    "lock_held",
    "lock_path",
    "scratch_bank_id",
    "swap_action",
    "temp_bank_id",
]
