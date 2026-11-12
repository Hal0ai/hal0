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
(:func:`unit_refusals`) and refuses, before anything changes, an
``ExecStart`` it cannot rewrite word for word (a shell wrapper, an unkept
prefix, an expanded host or port), a later-sorting drop-in that would win
over this one, and an ``ExecStartPre``/``Post``/``Condition``/``Reload`` that
names the normal port. After each restart onto the private port the run
confirms that a process of the engine's unit owns the listening socket
(:func:`loopback_listener`), that ``/version`` reports the engine the
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
facts, mental models, directives and documents (:data:`HARD_COUNTS`).
Observations are not: the engine's consolidation moves them by itself and an
export leaves out an observation whose source facts are gone, so a rebuilt
bank may hold more observations than its archive, never fewer. The
fingerprint (:data:`FINGERPRINT_SIGNALS`) is kept to what cannot move by
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
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import structlog

from hal0.config.paths import var_lib
from hal0.install import perms
from hal0.memory import embedder_record, write_gate
from hal0.memory.embedder_record import (
    ENGINE_DEFAULT_EMBEDDER,
    ENGINE_DEFAULT_PROVIDER,
    SOURCE_CONFIGURED,
    SOURCE_SWITCH,
    SOURCE_UNAVAILABLE,
    Embedder,
    EmbedderRecord,
    describe_embedder,
)
from hal0.security import nofollow

log = structlog.get_logger(__name__)

#: Version 2 is the delete-and-restore swap that keeps ids (#3597). A
#: version 1 journal was written by the rename-based swap before it, whose
#: steps (``renaming``, ``renamed``) mean nothing to this one: it is refused,
#: never guessed at.
JOURNAL_VERSION = 2

#: The counts a restored bank must match, in the order they are reported.
COUNT_KEYS: tuple[str, ...] = ("facts", "observations", "mental_models", "directives", "documents")

#: What a bank's fingerprint is made of, and where each signal comes from.
#:
#: The engine is isolated for the whole run (see the module docstring), so the
#: fingerprint is an assertion, not a guard: it is kept to the signals that
#: cannot move by themselves. Everything the engine changes on its own
#: (consolidation and the timestamps, link tallies and operations it leaves
#: behind, a mental model's refreshed content) is left out, because a signal
#: that moves by itself turns a healthy run into one that can never finish.
FINGERPRINT_SIGNALS: dict[str, str] = {
    "directives": "a digest of every directive, inactive ones included",
    "config": "a digest of the bank's configuration overrides",
    "mental_models": "a digest of every mental model's definition (id, name, query, tags, "
    "trigger, token budget), not its generated content",
    "document": "the bank list's last_document_at: when a document was last ingested",
    "knowledge": "a digest of the knowledge-base tree: every folder and page with its id, "
    "kind, name, parent and backing mental model",
}

#: What the bank restored into its own id is checked by besides its counts
#: and :data:`FINGERPRINT_SIGNALS`, against its original once it was exported
#: (#3597). Each is exact: a lossy restore with the right counts fails it.
CONTENT_SIGNALS: dict[str, str] = {
    "documents": "a digest of every document id with its content hash",
    "facts": "a digest of every world and experience fact's text, tags, type and document "
    "(observations are left out: the engine consolidates them by itself)",
    "mental-model content": "each mental model's generated content, while its "
    "last_refreshed_at is the original's",
}

#: What a knowledge-base node is compared by: its place in the tree, not its
#: generated content or its timestamps.
_KNOWLEDGE_NODE_KEYS = ("id", "kind", "name", "parent_id", "mental_model_id")

#: Counts only a writer can move. ``observations`` is not among them: the
#: engine's consolidation adds and merges observations by itself, and an
#: export leaves out an observation whose source facts are gone.
HARD_COUNTS: tuple[str, ...] = ("facts", "mental_models", "directives", "documents")

_MODEL_DEFINITION_KEYS = ("id", "name", "source_query", "tags", "trigger", "max_tokens")

#: Measured on hindsight-api 1.2.0, CPU embedder, 2026-11-10 (ADR-0058 "Open
#: questions"): a bank restore re-embeds roughly this many facts per second.
RESTORE_FACTS_PER_S = 60.0

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
        "document ids with their content hashes, and every world and experience fact's "
        "text and tags, by digest",
        "each mental model's generated content, unless the engine refreshed it after the restore",
    ],
    "note": "each bank is restored into its own id once its original is deleted, and is "
    "compared with the original as it was exported before its rebuilt copy is deleted, so "
    "links and agents that hold a page or directive id keep working. A fact id written down "
    "before the switch (a dashboard fact link, an id kept for memory_delete) no longer "
    "resolves after it. With --allow-pending the document, fact and mental-model content "
    "checks are skipped: the bank was still being written when it was exported",
}

#: A successful run restarts the engine three times: onto its private port,
#: onto the target embedder, and back to its normal address.
RESTARTS_PER_RUN = 3

SERVICE = "hindsight-api.service"
#: The drop-in that isolates the engine. It sorts after every hal0 drop-in and
#: after any name an operator is likely to choose, and it replaces
#: ``ExecStart``, so it wins over an operator's own ``ExecStart`` drop-in.
ISOLATION_DROP_IN_NAME = "zzzz-hal0-embedder-switch.conf"

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

_PG_DIR = ".pg0"

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

_OP_LIVE = ("pending", "processing")
_OP_FAILED = ("failed", "cancelled", "not_found")
_PAGE = 100
#: Page size for the content listings read only for a bank restored into its
#: own id: documents and facts can run to thousands, and the engine's list
#: routes take any limit.
_CONTENT_PAGE = 500


class SwitchError(Exception):
    """A step of the switch failed. The message is written for the operator.

    ``resumable`` is true when the run stopped inside the swap, or a rollback
    could not finish: writes stay paused and ``--resume`` carries on.
    """

    def __init__(
        self,
        message: str,
        *,
        bank: str | None = None,
        details: dict[str, Any] | None = None,
        resumable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.bank = bank
        self.details = details or {}
        self.resumable = resumable


class _EngineNotAsked(Exception):
    """``status`` does not query an engine that is stopped or that another process answers for."""


class SwitchRefused(SwitchError):
    """Refused before anything was changed. ``plan`` is the preflight report, when there is one."""

    def __init__(self, message: str, plan: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.plan = plan or {}


@dataclass(frozen=True)
class Timeouts:
    """Bounds on each wait. Tests shrink them; the CLI uses the defaults."""

    poll_s: float = 2.0
    #: One bank's export operation, and the archive download after it.
    export_s: float = 1800.0
    #: Queues draining after writes are paused.
    settle_s: float = 300.0
    #: The engine answering ``/health`` after a restart. The first start on a
    #: new embedder may download its weights.
    health_s: float = 600.0
    #: A restore is given this long, or ``restore_factor`` times its estimate
    #: at :data:`RESTORE_FACTS_PER_S`, whichever is more. A restore found
    #: still running on a resume is waited for twice that, never cancelled.
    restore_floor_s: float = 900.0
    restore_factor: float = 6.0
    #: A bank's queue going quiet before it is counted or deleted.
    quiet_s: float = 120.0
    delete_s: float = 3600.0
    http_s: float = 120.0


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
    #: ``{"bank", "id", "target", "engine_pid"}`` of a restore that may still
    #: be running: the bank it is recorded against, its operation id, the
    #: bank it writes, and the engine process it was sent to.
    restore_op: dict[str, Any] | None = None
    note: str | None = None


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
    return var_lib() / "state" / "memory-embedder-switch.json"


def lock_path() -> Path:
    return var_lib() / "state" / "memory-embedder-switch.lock"


def archive_root() -> Path:
    """``/var/lib/hal0/backups``: where hal0 keeps restore points, one directory per run here."""
    return var_lib() / "backups"


def mirror_name() -> str:
    return "journal.json"


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


def _read_journal_file(path: Path) -> tuple[Journal | None, str | None]:
    """``(journal, None)``, ``(None, None)`` when absent, ``(None, why)`` when unusable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path} cannot be read ({type(exc).__name__})"
    try:
        return _parse_journal(raw), None
    except (ValueError, TypeError) as exc:
        return None, f"{path} is not a usable journal ({exc})"


def _unfinished_mirrors() -> list[Path]:
    """Mirrors of runs that neither finished nor were rolled back, newest first."""
    found: list[tuple[str, Path]] = []
    try:
        candidates = sorted(archive_root().glob(f"memory-embedder-switch-*/{mirror_name()}"))
    except OSError:
        return []
    for path in candidates:
        journal, _ = _read_journal_file(path)
        if journal is not None and journal.phase not in _FINISHED_PHASES:
            found.append((journal.started_at, path))
    return [path for _, path in sorted(found, reverse=True)]


def locate_journal() -> dict[str, Any]:
    """Find the journal of an unfinished run wherever a copy survives.

    The primary copy first. When it is missing or unusable, the mirror in the
    archive directory the write gate names, then any unfinished mirror under
    ``backups/``. Returns ``{"journal", "source", "error"}``: ``error`` is set
    when a copy exists but none can be used, and when the primary was unusable
    and a mirror stood in for it. Never raises.
    """
    primary, problem = _read_journal_file(journal_path())
    if primary is not None:
        if primary.phase in _FINISHED_PHASES:
            return {"journal": None, "source": None, "error": None}
        return {"journal": primary, "source": str(journal_path()), "error": None}
    mirrors: list[Path] = []
    gate = write_gate.read_gate() or {}
    if isinstance(gate.get("archive_dir"), str) and gate["archive_dir"]:
        mirrors.append(Path(gate["archive_dir"]) / mirror_name())
    mirrors.extend(p for p in _unfinished_mirrors() if p not in mirrors)
    for path in mirrors:
        journal, why = _read_journal_file(path)
        if journal is not None and journal.phase not in _FINISHED_PHASES:
            return {"journal": journal, "source": str(path), "error": problem}
        problem = problem or why
    return {"journal": None, "source": None, "error": problem}


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
    try:
        fd = os.open(lock_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)
    return False


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


def update_in_progress() -> str | None:
    """The id of a queued or running ``hal0 update`` job, read from its on-disk mirror."""
    try:
        jobs = sorted((var_lib() / "update-jobs").glob("*.json"))
    except OSError:
        return None
    for path in jobs:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(job, dict) and job.get("state") in ("queued", "running"):
            return str(job.get("id") or path.stem)
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


def _bank_path(bank: str) -> str:
    # Colons are legal in a path segment and bank ids carry them
    # (``Hal0::Agents``); everything else that could change the path is escaped.
    return f"/v1/default/banks/{quote(bank, safe=':')}"


def _du_bytes(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _device(path: Path) -> int:
    """The filesystem ``path`` is on."""
    return os.stat(path).st_dev


def _nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path("/")


def _knowledge_nodes(tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every folder and page in a ``knowledge-base/tree`` answer, flattened."""
    out: list[dict[str, Any]] = []
    stack = list(tree.get("roots") or tree.get("nodes") or [])
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        out.append({k: node.get(k) for k in _KNOWLEDGE_NODE_KEYS})
        stack.extend(node.get("children") or [])
    return out


def _diff(
    expected: Mapping[str, int], got: Mapping[str, int], *, observation_floor: bool = False
) -> dict[str, dict[str, int]]:
    """The counts that differ and matter (:data:`HARD_COUNTS`).

    With ``observation_floor``, fewer observations than expected also counts:
    a rebuilt bank may gain observations from the engine's own consolidation,
    but must not have lost any its archive carried.
    """
    keys = list(HARD_COUNTS)
    if observation_floor and int(got.get("observations", 0)) < int(expected.get("observations", 0)):
        keys.append("observations")
    return {
        key: {"expected": int(expected.get(key, 0)), "got": int(got.get(key, 0))}
        for key in COUNT_KEYS
        if key in keys and int(expected.get(key, 0)) != int(got.get(key, 0))
    }


def _diff_text(diff: Mapping[str, Mapping[str, int]]) -> str:
    return ", ".join(
        f"{key.replace('_', ' ')} {d['got']} (expected {d['expected']})" for key, d in diff.items()
    )


def _signals_moved(
    before: Mapping[str, Any] | None, now: Mapping[str, Any], keys: tuple[str, ...] | None = None
) -> list[str]:
    """Names of the fingerprint signals that differ. An absent baseline compares equal."""
    if not before:
        return []
    return [k for k in (keys or tuple(FINGERPRINT_SIGNALS)) if before.get(k) != now.get(k)]


def _contents_moved(before: Mapping[str, Any], now: Mapping[str, Any]) -> list[str]:
    """Names of the :data:`CONTENT_SIGNALS` that differ.

    A mental model's content is compared only while its ``last_refreshed_at``
    is the original's: the engine regenerates content by itself, and a model
    it refreshed after the restore was not lost by it.
    """
    moved = [k for k in ("documents", "facts") if before.get(k) != now.get(k)]
    then, current = before.get("model_content") or {}, now.get("model_content") or {}
    if any(
        mid in current
        and current[mid].get("refreshed") == old.get("refreshed")
        and current[mid].get("content") != old.get("content")
        for mid, old in then.items()
    ):
        moved.append("mental-model content")
    return moved


def _strip(value: Any) -> Any:
    """``value`` without the keys that differ between a bank and its rebuilt copy by design."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in ("bank_id", "is_stale")}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _digest(value: Any) -> str:
    """A short digest of ``value``; a list is digested as a set of its rows."""
    if isinstance(value, list):
        value = sorted(json.dumps(_strip(v), sort_keys=True, default=str) for v in value)
    text = json.dumps(_strip(value), sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _pair(raw: Mapping[str, Any] | None) -> Embedder | None:
    """A raw evidence layer as a pair; an unset key is the engine default."""
    if raw is None:
        return None
    return Embedder(
        raw.get("provider") or ENGINE_DEFAULT_PROVIDER,
        raw.get("model") or ENGINE_DEFAULT_EMBEDDER,
    )


def _write_synced(path: Path, text: str, *, mode: int) -> None:
    """Replace ``path`` atomically and durably: temp file, fsync, rename, fsync the directory."""
    nofollow.write_file(path, text, mode=mode, owner=perms.state_root_owner())
    fd = nofollow.open_dir(path.parent)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def isolation_drop_in_path() -> Path:
    from hal0.memory import extraction_env

    return extraction_env.DROP_IN_DIR / ISOLATION_DROP_IN_NAME


def _unit_assignments(unit_text: str) -> list[tuple[str, str, str]]:
    """``(file, key, value)`` for every assignment in ``systemctl cat`` output, in order.

    ``file`` is the path in the ``# /…`` header that precedes it. A line
    ending in a backslash continues on the next, as in systemd; comments and
    section headers are skipped.
    """
    out: list[tuple[str, str, str]] = []
    source = ""
    carried = ""
    for raw in unit_text.splitlines():
        if raw.startswith("# /"):
            source = raw[2:].strip()
            carried = ""
            continue
        line = carried + raw.strip()
        if line.endswith("\\"):
            carried = line[:-1].rstrip() + " "
            continue
        carried = ""
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.append((source, key.strip(), value.strip()))
    return out


def _is_ours(source: str) -> bool:
    return Path(source).name == ISOLATION_DROP_IN_NAME


def exec_start_source(unit_text: str) -> tuple[str | None, str | None]:
    """``(command, file)``: the ``ExecStart`` the unit runs without this command's drop-in.

    The last ``ExecStart=`` wins and an empty one clears what came before, as
    in systemd, so an operator's own drop-in that replaces ``ExecStart`` (to
    bind the LAN, say) is what comes back, with the file that set it.
    """
    current: tuple[str | None, str | None] = (None, None)
    for source, key, value in _unit_assignments(unit_text):
        if key == "ExecStart" and not _is_ours(source):
            current = (value or None, source if value else None)
    return current


def effective_exec_start(unit_text: str) -> str | None:
    """The command the engine's unit starts, from ``systemctl cat`` output (:func:`exec_start_source`)."""
    return exec_start_source(unit_text)[0]


def later_exec_start_dropins(unit_text: str) -> list[tuple[str, str]]:
    """``(file, ExecStart)`` for each drop-in that would win over :data:`ISOLATION_DROP_IN_NAME`.

    systemd applies drop-ins in the order of their file names, whichever
    directory they are in, so one whose name sorts after this command's and
    that sets ``ExecStart`` replaces the private command: the engine would
    come back at its normal address.
    """
    found: dict[str, str] = {}
    for source, key, value in _unit_assignments(unit_text):
        name = Path(source).name
        if key == "ExecStart" and source.endswith(".conf") and name > ISOLATION_DROP_IN_NAME:
            found[source] = value or found.get(source, "")
    return sorted(found.items(), key=lambda item: Path(item[0]).name)


class UnsupportedExecStart(ValueError):
    """An ``ExecStart`` this command will not rewrite; the message says why and what to do."""


#: Programs that run their arguments as a script: the address inside cannot be
#: rewritten reliably, so a command started through one is refused.
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ash", "ksh", "busybox", "fish"})
#: systemd's ``ExecStart`` prefixes. ``-`` (a failure exit is ignored) and ``:``
#: (no environment variable expansion) leave the command line itself alone and
#: are kept; the others change how systemd runs it or which word is the
#: program, and are refused.
_KEPT_PREFIXES = frozenset("-:")
_ALL_PREFIXES = frozenset("-@+!:|")


@dataclass(frozen=True)
class _Word:
    raw: str
    value: str


def _split_exec(line: str) -> list[_Word]:
    """Split ``line`` into words the way systemd does, for the subset this command accepts.

    Words are separated by whitespace; a word may be quoted with ``"`` or
    ``'`` (also in part, ``--host="a"``), and the quotes are removed from its
    value. systemd also reads C-style backslash escapes, which ``shlex`` does
    not follow, so a backslash anywhere is refused rather than guessed at.
    Each word keeps its original text, so a word this command does not
    change is written back exactly as it was.
    """
    if "\\" in line:
        raise UnsupportedExecStart("it contains a backslash escape")
    words: list[_Word] = []
    i, n = 0, len(line)
    while i < n:
        while i < n and line[i] in " \t":
            i += 1
        if i >= n:
            break
        start, value = i, []
        while i < n and line[i] not in " \t":
            if line[i] in "\"'":
                quote, i = line[i], i + 1
                end = line.find(quote, i)
                if end < 0:
                    raise UnsupportedExecStart("it has an unterminated quote")
                value.append(line[i:end])
                i = end + 1
            else:
                value.append(line[i])
                i += 1
        words.append(_Word(line[start:i], "".join(value)))
    return words


def private_exec_start(line: str, port: int) -> str:
    """``line`` with its listen address replaced by ``127.0.0.1:<port>``.

    Everything else in the operator's command is kept word for word. An
    address the command does not name (the engine then reads it from its
    environment or uses its default) is appended, and an argument beats the
    environment. Raises :class:`UnsupportedExecStart`, before anything is
    written, for a command this cannot rewrite safely: one started through a
    shell, one with a prefix other than ``-`` or ``:``, one whose host or port
    argument uses a ``%`` specifier or ``$`` expansion, and one systemd quotes
    or escapes in ways this does not follow.
    """
    words = _split_exec(line)
    if not words:
        raise UnsupportedExecStart("it is empty")
    first = words[0]
    prefix_len = 0
    while prefix_len < len(first.value) and first.value[prefix_len] in _ALL_PREFIXES:
        prefix_len += 1
    prefix = first.value[:prefix_len]
    if set(prefix) - _KEPT_PREFIXES:
        raise UnsupportedExecStart(
            f"it starts with the prefix {prefix!r}, which changes how systemd runs it "
            "(only '-' and ':' are kept)"
        )
    program = first.value[prefix_len:]
    if not program:
        raise UnsupportedExecStart("it names no program")
    if Path(program).name in _SHELLS:
        raise UnsupportedExecStart(
            f"it runs the engine through a shell ({program}), so the address is inside a script"
        )
    out = [w.raw for w in words]
    seen = {"--host": False, "--port": False}
    wanted = {"--host": "127.0.0.1", "--port": str(port)}
    i = 1
    while i < len(words):
        value = words[i].value
        for flag in seen:
            if value == flag:
                if i + 1 >= len(words):
                    raise UnsupportedExecStart(f"{flag} is the last word and has no value")
                arg = words[i + 1].value
                _check_address_value(flag, arg)
                out[i + 1] = wanted[flag]
                seen[flag] = True
                i += 1
                break
            if value.startswith(flag + "="):
                _check_address_value(flag, value[len(flag) + 1 :])
                out[i] = f"{flag}={wanted[flag]}"
                seen[flag] = True
                break
        i += 1
    for flag in ("--host", "--port"):
        if not seen[flag]:
            out += [flag, wanted[flag]]
    return " ".join(out)


def _check_address_value(flag: str, value: str) -> None:
    if "%" in value or "$" in value:
        raise UnsupportedExecStart(
            f"its {flag} value {value!r} is expanded by systemd (a % specifier or $ variable), "
            "so the address it binds cannot be read from the unit"
        )


def exec_start_refusal(line: str, source: str | None) -> str | None:
    """Why the switch will not rewrite ``line``, with the way on, or ``None`` when it can."""
    try:
        private_exec_start(line, 1)
    except UnsupportedExecStart as exc:
        where = f" (set in {source})" if source else ""
        return (
            f"the engine's unit starts `{line}`{where}, which the switch cannot move to a "
            f"private port: {exc}. Make ExecStart a direct command line that names the "
            "engine's binary, with --host and --port as plain values (an ExecStart= drop-in "
            "that sorts before " + ISOLATION_DROP_IN_NAME + " is fine), run "
            "`systemctl daemon-reload`, then run the switch again"
        )
    return None


#: Unit keys whose command runs around the engine's start or reload.
_SIDE_COMMANDS = ("ExecStartPre", "ExecStartPost", "ExecCondition", "ExecReload")


def side_commands_on_port(unit_text: str, port: int) -> list[tuple[str, str, str]]:
    """``(file, key, command)`` for start/reload commands that name the engine's normal port.

    While the engine is private such a command (a health probe, say) cannot
    reach it, so its start would fail every time and the run would time out
    and roll back. An empty assignment clears the list before it, as in
    systemd; this command's own drop-in sets none.
    """
    mention = re.compile(rf"(?<![0-9]){port}(?![0-9])")
    active: dict[str, list[tuple[str, str]]] = {k: [] for k in _SIDE_COMMANDS}
    for source, key, value in _unit_assignments(unit_text):
        if key not in active or _is_ours(source):
            continue
        if not value:
            active[key] = []
        else:
            active[key].append((source, value))
    return [
        (source, key, value)
        for key in _SIDE_COMMANDS
        for source, value in active[key]
        if mention.search(value)
    ]


def unit_refusals(unit_text: str | None, normal_port: int | None) -> list[dict[str, str]]:
    """The preflight's look at the engine's unit: every reason it cannot be isolated safely."""
    if unit_text is None:
        return [
            {
                "code": "unit_unreadable",
                "message": f"`systemctl cat {SERVICE}` could not be read, so the switch cannot "
                "tell which command to move to a private port",
            }
        ]
    refusals: list[dict[str, str]] = []
    line, source = exec_start_source(unit_text)
    if line is None:
        refusals.append(
            {
                "code": "unit_unreadable",
                "message": f"`systemctl cat {SERVICE}` names no ExecStart",
            }
        )
    else:
        why = exec_start_refusal(line, source)
        if why:
            refusals.append({"code": "exec_start_unsupported", "message": why})
    for path, command in later_exec_start_dropins(unit_text):
        refusals.append(
            {
                "code": "exec_start_overridden",
                "message": f"{path} sorts after {ISOLATION_DROP_IN_NAME} and sets ExecStart "
                f"(`{command or '(cleared)'}`), so it would win over the switch's private "
                "command and the engine would stay reachable at its normal address. Rename it "
                f"so it sorts before {ISOLATION_DROP_IN_NAME} (any name not starting with "
                "'zzzz-' does), run `systemctl daemon-reload`, then run the switch again",
            }
        )
    if normal_port is not None:
        for path, key, command in side_commands_on_port(unit_text, normal_port):
            refusals.append(
                {
                    "code": "exec_probe_normal_port",
                    "message": f"{path} sets {key}=`{command}`, which names the engine's "
                    f"normal port {normal_port}. While the switch runs, the engine listens only "
                    "on a private port, so this command would fail at every restart. Remove it "
                    "(or move it to a separate unit), run `systemctl daemon-reload`, then run "
                    "the switch again",
                }
            )
    return refusals


def read_unit_text(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """``systemctl cat`` of the engine's unit, or ``None`` when it cannot be read."""
    try:
        proc = runner(
            ["systemctl", "cat", SERVICE], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout or "" if proc.returncode == 0 else None


_HOST_ARG = re.compile(r"(--host)(=|\s+)(\S+)")
_PORT_ARG = re.compile(r"(--port)(=|\s+)(\S+)")


def isolation_body(exec_start: str, run_id: str) -> str:
    """The isolation drop-in: the unit's own command on a private port, consolidation off."""
    return (
        f"# Written by `hal0 memory embedder switch` (run {run_id}). While this file exists\n"
        "# the memory engine listens only on a private loopback port, so that nothing but\n"
        "# the switch can reach it, and does not consolidate by itself. The switch removes\n"
        "# this file when the run ends. Do not edit or delete it by hand: run\n"
        "# `hal0 memory embedder switch --status`.\n"
        "[Service]\n"
        "ExecStart=\n"
        f"ExecStart={exec_start}\n"
        "Environment=HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION=false\n"
    )


def write_isolation_dropin(
    port: int,
    run_id: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    path: Path | None = None,
) -> dict[str, Any]:
    """Write the isolation drop-in for ``port``. Root only; never raises.

    Reads the unit as systemd sees it (``systemctl cat``), so the command it
    rewrites is the one in force, an operator's ``ExecStart`` drop-in
    included. Writes nothing for a unit the preflight would refuse
    (:func:`unit_refusals`). Returns ``{"error", "exec_start"}``; the caller
    reloads and restarts.
    """
    text = read_unit_text(runner)
    if text is None:
        return {"error": f"could not read the engine's unit (`systemctl cat {SERVICE}`)"}
    line, source = exec_start_source(text)
    if not line:
        return {"error": f"could not find an ExecStart in `systemctl cat {SERVICE}`"}
    later = later_exec_start_dropins(text)
    if later:
        return {
            "error": f"{later[0][0]} sorts after {ISOLATION_DROP_IN_NAME} and sets ExecStart "
            f"(`{later[0][1] or '(cleared)'}`), so the engine cannot be isolated"
        }
    try:
        private = private_exec_start(line, port)
    except UnsupportedExecStart:
        return {"error": exec_start_refusal(line, source)}
    target = path if path is not None else isolation_drop_in_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".conf.tmp")
        tmp.write_text(isolation_body(private, run_id), encoding="utf-8")
        os.chmod(tmp, 0o644)
        tmp.replace(target)
    except OSError as exc:
        return {"error": f"could not write {target}: {exc}"}
    return {"error": None, "exec_start": line}


def remove_isolation_dropin(path: Path | None = None) -> dict[str, Any]:
    """Remove the isolation drop-in. Never raises; the caller reloads and restarts."""
    target = path if path is not None else isolation_drop_in_path()
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        return {"error": f"could not remove {target}: {exc}"}
    return {"error": None}


def isolated_port(path: Path | None = None) -> int | None:
    """The private port the isolation drop-in pins the engine to; ``None`` when there is none.

    ``0`` for a drop-in that is there and names no port this can read: the
    engine is then to be treated as isolated, on a port unknown.
    """
    target = path if path is not None else isolation_drop_in_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return 0
    found = 0
    for line in text.splitlines():
        match = _PORT_ARG.search(line) if line.startswith("ExecStart=") else None
        if match and match.group(3).isdigit():
            found = int(match.group(3))
    return found


def free_loopback_port() -> int:
    """A loopback port nothing listens on now, picked by the kernel."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_PROC = Path("/proc")
_CGROUP_ROOT = Path("/sys/fs/cgroup")
#: ``/proc/net/tcp`` state of a listening socket.
_TCP_LISTEN = "0A"


def _listening_inodes(port: int, proc: Path = _PROC) -> set[str]:
    """Socket inodes listening on TCP ``port`` (any local address), from ``/proc/net/tcp{,6}``."""
    found: set[str] = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = (proc / "net" / name).read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for row in lines:
            fields = row.split()
            if len(fields) < 10 or fields[3] != _TCP_LISTEN:
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == port and fields[9] != "0":
                found.add(fields[9])
    return found


def _pid_sockets(pid: int, proc: Path = _PROC) -> set[str]:
    """Socket inodes ``pid`` holds open. Empty when its fds cannot be read."""
    out: set[str] = set()
    try:
        fds = list((proc / str(pid) / "fd").iterdir())
    except OSError:
        return out
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            out.add(target[8:-1])
    return out


def _comm(pid: int, proc: Path = _PROC) -> str:
    try:
        return (proc / str(pid) / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        return "?"


def _unit_pids(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    cgroup_root: Path = _CGROUP_ROOT,
) -> set[int]:
    """The engine unit's processes: its ``MainPID`` and everything in its control group."""
    try:
        proc = runner(
            ["systemctl", "show", "-p", "MainPID", "-p", "ControlGroup", SERVICE],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    props = dict(line.split("=", 1) for line in (proc.stdout or "").splitlines() if "=" in line)
    pids: set[int] = set()
    main = props.get("MainPID", "0").strip()
    if main.isdigit() and int(main) > 0:
        pids.add(int(main))
    group = props.get("ControlGroup", "").strip()
    if group.startswith("/"):
        try:
            text = (cgroup_root / group.lstrip("/") / "cgroup.procs").read_text(encoding="ascii")
        except OSError:
            text = ""
        pids.update(int(p) for p in text.split() if p.isdigit())
    return pids


def loopback_listener(
    port: int,
    *,
    unit_pids: Callable[[], set[int]] | None = None,
    proc: Path = _PROC,
) -> dict[str, Any]:
    """Who listens on TCP ``port``: ``{"owner", "holder", "pid"}``.

    ``owner`` is ``"none"`` (nothing listens), ``"unit"`` (a process of the
    engine's unit owns the listening socket), ``"other"`` (another process
    does; ``holder`` names it) or ``"unknown"`` (a socket listens but no
    process could be matched to it, typically for want of root). Read from
    ``/proc/net/tcp`` and the socket inodes in ``/proc/<pid>/fd``; no new
    dependency, and nothing is written.
    """
    inodes = _listening_inodes(port, proc)
    if not inodes:
        return {"owner": "none", "holder": None, "pid": None}
    for pid in sorted((unit_pids or _unit_pids)()):
        if _pid_sockets(pid, proc) & inodes:
            return {"owner": "unit", "holder": f"pid {pid} ({_comm(pid, proc)})", "pid": pid}
    try:
        candidates = sorted(int(p.name) for p in proc.iterdir() if p.name.isdigit())
    except OSError:
        candidates = []
    for pid in candidates:
        if _pid_sockets(pid, proc) & inodes:
            return {"owner": "other", "holder": f"pid {pid} ({_comm(pid, proc)})", "pid": pid}
    return {
        "owner": "unknown",
        "holder": "a process this account cannot see (run as root)",
        "pid": None,
    }


def stop_engine_unit() -> dict[str, Any]:
    """``systemctl stop`` the engine's unit. ``{"error": ...}`` when it could not be stopped."""
    try:
        proc = subprocess.run(
            ["systemctl", "stop", SERVICE],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        detail = " ".join((proc.stderr or proc.stdout or "").split())[:160]
        return {"error": detail or f"systemctl exited {proc.returncode}"}
    return {}


def http_detail(text: str, limit: int = 120) -> str:
    """A short, single-line, markup-safe summary of an HTTP error body.

    A foreign server's HTML page is never echoed: it is named, not shown.
    """
    stripped = (text or "").lstrip()
    if stripped[:1] == "<":
        return "(a non-JSON page, not shown)"
    flat = "".join(ch if ch.isprintable() else " " for ch in stripped)
    flat = " ".join(flat.split())
    for ch in "<>[]":
        flat = flat.replace(ch, "")
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


class EmbedderSwitch:
    """One run of the switch against one engine.

    ``client`` is a :class:`hal0.memory.hindsight_client.HindsightRestClient`
    pointed at the engine itself, not at hal0-api: the switch's own imports
    and deletes must not meet the pause it sets.

    Everything that touches the host is injectable, and defaults to the real
    thing: ``apply_dropin(embedder)`` renders the managed drop-in for that
    pair and restarts the engine (returning the ``apply_showcase_dropin``
    dict), ``evidence()`` is :func:`embedder_record.engine_evidence`,
    ``isolate(port, run_id)`` and ``release()`` write and remove the isolation drop-in
    (:func:`write_isolation_dropin`, :func:`remove_isolation_dropin`; the
    restart that follows either is ``apply_dropin``'s), ``isolated_port()``
    reads it back, ``client_for(base_url)`` builds the client for the private
    port, ``listener(port)`` says which process listens on a loopback port
    (:func:`loopback_listener`), ``unit_text()`` is ``systemctl cat`` of the
    engine's unit (:func:`read_unit_text`), ``privileged()`` says whether
    this process may do all of that, and ``hook(point)`` is the test hook
    described in the module docstring.
    """

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
        pick_port: Callable[[], int] = free_loopback_port,
        hs_dir: Path | None = None,
        timeouts: Timeouts | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        progress: Callable[[str], None] | None = None,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self._public_client = client
        self._private_client: Any | None = None
        self._isolate = isolate or write_isolation_dropin
        self._release = release or remove_isolation_dropin
        self._stop_unit = stop_unit or stop_engine_unit
        self._isolated_port = isolated_port or globals()["isolated_port"]
        self._client_for = client_for or self._default_client_for
        self._listener = listener or loopback_listener
        self._unit_text = unit_text or read_unit_text
        #: ``api_version`` the preflight read; every restart must bring back the same.
        self._api_version: str | None = None
        self._pick_port = pick_port
        self._target = target
        self._allow_pending = allow_pending
        self._configured = configured
        self._apply_dropin = apply_dropin or self._default_apply_dropin
        self._evidence = evidence or embedder_record.engine_evidence
        self._privileged = privileged or self._default_privileged
        self._hs_dir = hs_dir
        self.t = timeouts or Timeouts()
        self._sleep = sleep
        self._clock = clock
        self._progress = progress or (lambda _msg: None)
        self._hook = hook or (lambda _point: None)

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

    def _default_client_for(self, base_url: str) -> Any:
        from hal0.memory.hindsight_client import HindsightRestClient

        return HindsightRestClient(base_url=base_url, api_key=self._public_client.api_key)

    @property
    def _client(self) -> Any:
        """The client for where the engine is now: its private port, else its normal address."""
        return self._private_client if self._private_client is not None else self._public_client

    def _use_port(self, port: int | None) -> None:
        """Talk to the engine on its private ``port``, or on its normal address for ``None``."""
        if port is None:
            self._private_client = None
        elif (
            self._private_client is None
            or self._private_client.base_url != f"http://127.0.0.1:{port}"
        ):
            self._private_client = self._client_for(f"http://127.0.0.1:{port}")

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

    def _say(self, message: str) -> None:
        log.info("hal0.memory.embedder_switch", step=message)
        self._progress(message)

    # ── engine calls ────────────────────────────────────────────────────

    def _auth(self) -> dict[str, str]:
        return {k: v for k, v in self._client._headers().items() if k.lower() != "content-type"}

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any | None = None,
        what: str,
        bank: str | None = None,
        timeout_s: float | None = None,
    ) -> Any:
        try:
            return await self._client.request_json(
                method, path, params=params, json_body=body, timeout_s=timeout_s or self.t.http_s
            )
        except httpx.HTTPStatusError as exc:
            raise SwitchError(
                f"{what}: the engine answered HTTP {exc.response.status_code} "
                f"{http_detail(exc.response.text)}".rstrip(),
                bank=bank,
            ) from exc
        except httpx.HTTPError as exc:
            raise SwitchError(
                f"{what}: the engine did not answer ({type(exc).__name__}: {exc})", bank=bank
            ) from exc

    async def _healthy(self) -> bool:
        try:
            await self._client.request_json("GET", "/health", timeout_s=5.0)
        except (httpx.HTTPError, ValueError):
            return False
        return True

    async def _wait_health(self) -> bool:
        deadline = self._clock() + self.t.health_s
        while True:
            if await self._healthy():
                return True
            if self._clock() >= deadline:
                return False
            await self._sleep(self.t.poll_s)

    async def _list_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        key: str,
        what: str,
        bank: str | None = None,
        page_size: int = _PAGE,
    ) -> list[Any]:
        """Every row of a paged engine list.

        Pages until the engine's own ``total`` is reached or a page comes back
        empty; a short page is never taken for the end. When the engine gives
        a total, the rows collected must add up to it.
        """
        rows: list[Any] = []
        total: int | None = None
        while True:
            page = await self._json(
                "GET",
                path,
                params={**(params or {}), "limit": page_size, "offset": len(rows)},
                what=what,
                bank=bank,
            )
            page = page if isinstance(page, dict) else {}
            if isinstance(page.get("total"), int):
                total = page["total"]
            chunk = page.get(key)
            chunk = chunk if isinstance(chunk, list) else []
            rows.extend(chunk)
            if not chunk or (total is not None and len(rows) >= total):
                break
        if total is not None and len(rows) != total:
            raise SwitchError(
                f"{what}: the engine reports {total} but listed {len(rows)}; the listing "
                "changed while it was read",
                bank=bank,
            )
        return rows

    async def _bank_rows(self) -> list[dict[str, Any]]:
        rows = await self._list_all("/v1/default/banks", key="banks", what="listing banks")
        return [r for r in rows if isinstance(r, dict) and "bank_id" in r]

    async def _bank_ids(self) -> list[str]:
        return [str(r["bank_id"]) for r in await self._bank_rows()]

    async def _bank_row(self, bank: str) -> dict[str, Any]:
        rows = await self._list_all(
            "/v1/default/banks", {"q": bank}, key="banks", what="reading the bank", bank=bank
        )
        for row in rows:
            if isinstance(row, dict) and row.get("bank_id") == bank:
                return row
        return {}

    async def bank_state(self, bank: str) -> dict[str, Any]:
        """Counts, queue depth and fingerprint for one bank, uncached.

        Facts are every memory unit that is not an observation
        (``nodes_by_fact_type``); mental models include the ones that back a
        knowledge page; directives include inactive ones. The fingerprint is
        described by :data:`FINGERPRINT_SIGNALS`.
        """
        base = _bank_path(bank)
        stats = await self._json(
            "GET", f"{base}/stats", params={"refresh": "true"}, what="reading stats", bank=bank
        )
        stats = stats if isinstance(stats, dict) else {}
        by_type = stats.get("nodes_by_fact_type")
        by_type = by_type if isinstance(by_type, dict) else {}
        observations = int(stats.get("total_observations") or by_type.get("observation") or 0)
        facts = sum(int(v or 0) for k, v in by_type.items() if k != "observation")
        ops = stats.get("operations_by_status")
        ops = ops if isinstance(ops, dict) else {}
        models = await self._list_all(
            f"{base}/mental-models",
            {"detail": "content"},
            key="items",
            what="reading mental models",
            bank=bank,
        )
        directives = await self._list_all(
            f"{base}/directives",
            {"active_only": "false"},
            key="items",
            what="reading directives",
            bank=bank,
        )
        try:
            config = await self._json("GET", f"{base}/config", what="reading the config", bank=bank)
        except SwitchError:
            # The per-bank config API can be switched off (features.bank_config_api).
            config = "unavailable"
        try:
            tree = await self._json(
                "GET", f"{base}/knowledge-base/tree", what="reading the knowledge base", bank=bank
            )
            knowledge: Any = sorted(
                (_knowledge_nodes(tree if isinstance(tree, dict) else {})),
                key=lambda n: str(n.get("id")),
            )
        except SwitchError:
            knowledge = "unavailable"
        row = await self._bank_row(bank)
        definitions = [
            {k: m.get(k) for k in _MODEL_DEFINITION_KEYS} for m in models if isinstance(m, dict)
        ]
        return {
            "counts": {
                "facts": facts,
                "observations": observations,
                "mental_models": len(models),
                "directives": len(directives),
                "documents": int(stats.get("total_documents") or 0),
            },
            "links": int(stats.get("total_links") or 0),
            "pending": int(ops.get("pending") or 0),
            "processing": int(ops.get("processing") or 0),
            "pending_consolidation": int(stats.get("pending_consolidation") or 0),
            "listed_facts": row.get("fact_count"),
            "nodes_total": int(stats.get("total_nodes") or 0),
            "fingerprint": {
                "directives": _digest(directives),
                "config": _digest(config),
                "mental_models": _digest(definitions),
                "document": row.get("last_document_at"),
                "knowledge": _digest(knowledge),
            },
        }

    async def content_state(self, bank: str) -> dict[str, Any]:
        """What ``bank`` holds, digested, as :data:`CONTENT_SIGNALS` describes.

        Read only for a bank's original once it is exported and for the bank
        restored into its own id: unlike :meth:`bank_state` it lists every
        document and fact, which a bank of a thousand documents answers in a
        few dozen requests.
        """
        base = _bank_path(bank)
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
        return {
            "documents": _digest(
                [
                    {"id": d.get("id"), "hash": d.get("content_hash")}
                    for d in documents
                    if isinstance(d, dict)
                ]
            ),
            "facts": _digest(
                [
                    {
                        "type": f.get("fact_type"),
                        "document": f.get("document_id"),
                        "text": f.get("text"),
                        "tags": sorted(str(t) for t in (f.get("tags") or [])),
                    }
                    for f in facts
                    if isinstance(f, dict)
                ]
            ),
            "model_content": {
                str(m.get("id")): {
                    "refreshed": m.get("last_refreshed_at"),
                    "content": _digest(m.get("content")),
                }
                for m in models
                if isinstance(m, dict)
            },
        }

    async def _quiet_state(self, bank: str) -> dict[str, Any]:
        """:meth:`bank_state` once the engine has stopped working on the bank.

        Quiet is no pending or processing operation and a consolidation queue
        that has stopped moving between two reads. Counts taken while the
        engine is still consolidating describe a bank that is about to
        change. Bounded: after ``quiet_s`` the last read is returned as it is,
        and the comparisons made on it treat observations as the one count
        the engine may still move.
        """
        deadline = self._clock() + self.t.quiet_s
        last: int | None = None
        while True:
            state = await self.bank_state(bank)
            busy = state["pending"] or state["processing"]
            if (not busy and state["pending_consolidation"] == last) or self._clock() >= deadline:
                return state
            last = state["pending_consolidation"]
            await self._sleep(self.t.poll_s)

    async def _wait_operation(
        self, bank: str, operation_id: str, *, timeout_s: float, what: str
    ) -> dict[str, Any]:
        deadline = self._clock() + timeout_s
        path = f"{_bank_path(bank)}/operations/{quote(operation_id, safe='')}"
        # A small bank's export or restore is done in well under a second, so
        # the first looks come quickly and only a long one settles at poll_s.
        delay = min(0.2, self.t.poll_s)
        while True:
            op = await self._json("GET", path, what=what, bank=bank)
            op = op if isinstance(op, dict) else {}
            status = op.get("status")
            if status == "completed":
                return op
            if status in _OP_FAILED:
                raise SwitchError(
                    f"{what} ended as {status}: {op.get('error_message') or 'no error message'}",
                    bank=bank,
                )
            if self._clock() >= deadline:
                raise SwitchError(
                    f"{what} did not finish within {timeout_s:g}s (last status: {status})",
                    bank=bank,
                    details={"operation_id": operation_id},
                )
            await self._sleep(delay)
            delay = min(self.t.poll_s, delay * 1.5)

    async def _delete_bank(self, bank: str) -> None:
        """Delete ``bank``. A bank that is already gone is a success."""
        try:
            await self._client.request_json("DELETE", _bank_path(bank), timeout_s=self.t.delete_s)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return
            raise SwitchError(
                f"deleting the bank: the engine answered HTTP {exc.response.status_code} "
                f"{http_detail(exc.response.text)}".rstrip(),
                bank=bank,
            ) from exc
        except httpx.HTTPError as exc:
            raise SwitchError(
                f"deleting the bank: the engine did not answer ({type(exc).__name__}: {exc})",
                bank=bank,
            ) from exc

    async def _wait_quiet(self, bank: str) -> None:
        """Wait until ``bank`` has no pending or processing operation, before it is deleted."""
        deadline = self._clock() + self.t.quiet_s
        while True:
            state = await self.bank_state(bank)
            if not state["pending"] and not state["processing"]:
                return
            if self._clock() >= deadline:
                raise SwitchError(
                    f"bank {bank} still has {state['pending']} pending and {state['processing']} "
                    f"processing operations after {self.t.quiet_s:g}s; it is not deleted "
                    "until they finish",
                    bank=bank,
                )
            await self._sleep(self.t.poll_s)

    # ── the engine: where it listens and what it runs ───────────────────

    async def _public_answers(self) -> bool:
        """True when anything answers at the engine's normal address.

        Only a refused connection means nothing listens there. Anything else,
        a connection that was accepted and then timed out or was reset
        included, means something holds the normal address, so the engine is
        not isolated.
        """
        try:
            await self._public_client.request_json("GET", "/health", timeout_s=3.0)
        except httpx.ConnectError:
            return False
        except (httpx.HTTPError, ValueError):
            return True
        return True

    def _normal_port(self) -> int | None:
        try:
            return urlsplit(str(self._public_client.base_url)).port
        except (AttributeError, ValueError):
            return None

    def _leak_message(self) -> str:
        """Why the engine still answers at its normal address, naming the drop-in when one wins."""
        text = self._unit_text()
        later = later_exec_start_dropins(text) if text else []
        if later:
            path, command = later[0]
            return (
                f"the engine still answers at its normal address, so it is not isolated: "
                f"{path} sorts after {ISOLATION_DROP_IN_NAME} and sets ExecStart "
                f"(`{command or '(cleared)'}`), which replaces the switch's private command. "
                f"Rename it so it sorts before {ISOLATION_DROP_IN_NAME}, run "
                "`systemctl daemon-reload`, then run the switch again"
            )
        return (
            "the engine still answers at its normal address, so it is not isolated: a "
            f"drop-in that sorts after {ISOLATION_DROP_IN_NAME} replaces ExecStart, or "
            "something else listens there. The switch does not run on an engine other "
            "clients can reach"
        )

    def _listener_owner(self, port: int) -> Mapping[str, Any]:
        try:
            return self._listener(port)
        except Exception as exc:  # a /proc read gone wrong is "cannot tell", not a crash
            return {"owner": "unknown", "holder": f"unreadable ({type(exc).__name__})"}

    async def _foreign(
        self, port: int, seen: Mapping[str, Any], journal: Journal | None = None
    ) -> SwitchError:
        """Stop the engine's unit, leave it stopped, and say what to do.

        The unit was started on a port someone else holds, so it cannot bind
        and systemd would restart it every few seconds for ever (each boot
        outlasts ``StartLimitIntervalSec``, so the start limit never trips).
        The gate stays set and the journal stays; ``--resume`` starts it again.
        """
        stopped = await asyncio.to_thread(self._stop_unit)
        error = stopped.get("error")
        if journal is not None:
            journal.engine_stopped = not error
            with contextlib.suppress(OSError):
                self._save(journal)
        who = seen.get("holder") or "another process"
        facts = {"foreign_port": port, "foreign_holder": who}
        held = f"127.0.0.1:{port}, the engine's private port, is held by {who}, not by {SERVICE}. "
        if error:
            return SwitchError(
                held + f"{SERVICE} could not be stopped ({error}), so it may be restarting into "
                f"the taken port: run `systemctl stop {SERVICE}`, free the port, then run "
                "`hal0 memory embedder switch --resume`. Memory writes stay paused",
                details=facts,
                resumable=True,
            )
        return SwitchError(
            held + f"{SERVICE} was stopped so it does not restart into the taken port; it stays "
            "stopped, memory writes and recall stay paused. Free the port (stop that process), "
            "then run `hal0 memory embedder switch --resume`",
            details=facts,
            resumable=True,
        )

    async def _confirm_version(self, where: str) -> None:
        """``GET /version`` answers as the engine the preflight saw, with export and import on."""
        try:
            version = await self._client.request_json("GET", "/version", timeout_s=10.0)
        except (httpx.HTTPError, ValueError) as exc:
            raise SwitchError(
                f"the process on {where} answers /health but not /version "
                f"({type(exc).__name__}), so it is not confirmed to be the memory engine"
            ) from exc
        version = version if isinstance(version, dict) else {}
        features = version.get("features") if isinstance(version.get("features"), dict) else {}
        api = version.get("api_version")
        if not api or (self._api_version is not None and api != self._api_version):
            raise SwitchError(
                f"the process on {where} reports api_version {api!r}, not "
                f"{self._api_version or 'an engine version'!r}: it is not the engine the "
                "switch checked before it started"
            )
        if not features.get("document_export_api") or not features.get("document_import_api"):
            raise SwitchError(
                f"the engine on {where} has its export or import API off "
                "(features on /version) after the restart"
            )

    async def _start_engine(
        self,
        embedder: Embedder,
        *,
        port: int | None,
        run_id: str,
        journal: Journal | None = None,
    ) -> None:
        """Restart the engine on ``embedder``, private on ``port`` or at its normal address.

        Writes or removes the isolation drop-in, renders the embedder drop-in,
        reloads and restarts once, then confirms from the engine itself: it
        answers where it should, its environment names ``embedder``, and when
        private, nothing answers at the normal address any more.
        """
        if port is not None:
            placed = await asyncio.to_thread(self._isolate, port, run_id)
        else:
            placed = await asyncio.to_thread(self._release)
        if placed.get("error"):
            raise SwitchError(
                f"could not {'isolate' if port is not None else 'release'} the engine: "
                f"{placed['error']}"
            )
        self._use_port(port)
        result = await asyncio.to_thread(self._apply_dropin, embedder)
        if result.get("error"):
            raise SwitchError(f"could not restart the engine: {result['error']}")
        where = f"its private port {port}" if port is not None else "its normal address"
        deadline = self._clock() + self.t.health_s
        while True:
            if port is not None:
                seen = self._listener_owner(port)
                if seen.get("owner") == "other":
                    # Something else took the port: fail now, not after the timeout.
                    raise await self._foreign(port, seen, journal)
            if await self._healthy():
                break
            # Up, but where it always was: do not wait out the whole timeout.
            if port is not None and await self._public_answers():
                raise SwitchError(self._leak_message())
            if self._clock() >= deadline:
                raise SwitchError(
                    f"the engine did not answer /health on {where} within {self.t.health_s:g}s "
                    f"of its restart on {describe_embedder(*embedder)} "
                    "(see `journalctl -u hindsight-api`)"
                )
            await self._sleep(self.t.poll_s)
        if port is not None:
            # /health on the port proves only that something answers there.
            seen = self._listener_owner(port)
            if seen.get("owner") == "other":
                raise await self._foreign(port, seen, journal)
            if seen.get("owner") != "unit":
                holder = seen.get("holder") or "no listener found"
                raise SwitchError(
                    f"something answers on 127.0.0.1:{port}, but the listening socket could not "
                    f"be matched to a process of {SERVICE} ({holder}), so it is not confirmed "
                    "to be the engine. Run the switch as root"
                )
        await self._confirm_version(where)
        live: Embedder | None = None
        for _ in range(10):
            # Read from the unit's main process; on a private port that is the
            # process confirmed above to own the listening socket.
            live = _pair(self._evidence().get("live"))
            if live == embedder:
                break
            await self._sleep(self.t.poll_s)
        else:
            runs = (
                describe_embedder(*live) if live is not None else "an environment hal0 cannot read"
            )
            raise SwitchError(
                f"the engine is up but runs {runs}, not {describe_embedder(*embedder)}: another "
                "drop-in, or the unit's EnvironmentFile, overrides the embedder"
            )
        if port is not None and await self._public_answers():
            raise SwitchError(self._leak_message())
        if journal is not None and journal.engine_stopped:
            journal.engine_stopped = False
            with contextlib.suppress(OSError):
                self._save(journal)

    async def _ensure_engine(
        self,
        embedder: Embedder,
        *,
        port: int | None,
        run_id: str,
        journal: Journal | None = None,
    ) -> None:
        """Do nothing when the engine is where and what it should be; otherwise put it there.

        A restore embeds with whatever the engine runs at that moment, so this
        is asked before every restore and at the start of every resume, from
        the engine itself and not from the record or the journal.
        """
        self._use_port(port)
        if (
            self._isolated_port() == port
            and await self._healthy()
            and _pair(self._evidence().get("live")) == embedder
            and not (port is not None and await self._public_answers())
            and (port is None or self._listener_owner(port).get("owner") == "unit")
        ):
            if port is not None and journal is not None and journal.engine_stopped:
                # Started again outside the switch: the unit is healthy and holds the port.
                journal.engine_stopped = False
                with contextlib.suppress(OSError):
                    self._save(journal)
            return
        self._say(
            f"putting the engine on {describe_embedder(*embedder)}, "
            + ("isolated" if port is not None else "at its normal address")
        )
        await self._start_engine(embedder, port=port, run_id=run_id, journal=journal)

    # ── archives ────────────────────────────────────────────────────────

    def _make_archive_dir(self, run_id: str) -> Path:
        """Create this run's ``0700`` archive directory under the shared backups directory."""
        from hal0.config.migrations.runner_system import _backups_dir

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = _backups_dir() / f"memory-embedder-switch-{stamp}-{run_id}"
        nofollow.ensure_dir(run_dir, mode=0o700, owner=perms.state_root_owner(), create=True)
        return run_dir

    @contextlib.contextmanager
    def _archive_dir_fd(self, journal: Journal) -> Iterator[int]:
        fd = nofollow.open_dir(journal.archive_dir)
        try:
            yield fd
        finally:
            os.close(fd)

    async def _export_bank(self, journal: Journal, entry: BankEntry, index: int) -> dict[str, int]:
        """Export one bank into the run's archive directory; returns the archive's counts."""
        bank = entry.bank
        submit = await self._json(
            "POST",
            f"{_bank_path(bank)}/transfer/export",
            params={
                "include_data": "true",
                "include_bank_config": "true",
                "include_history": "false",
            },
            what="starting the export",
            bank=bank,
        )
        operation_id = str((submit or {}).get("operation_id") or "")
        if not operation_id:
            raise SwitchError("the export returned no operation id", bank=bank)
        op = await self._wait_operation(
            bank, operation_id, timeout_s=self.t.export_s, what="the export"
        )
        meta = op.get("result_metadata")
        url = self._download_path(meta if isinstance(meta, dict) else {}, bank)
        name = f"{index:03d}-{re.sub(r'[^A-Za-z0-9._-]', '_', bank)[:80]}.zip"
        owner = perms.state_root_owner()
        with self._archive_dir_fd(journal) as dfd:
            part = f".{name}.part"
            with contextlib.suppress(FileNotFoundError):
                os.unlink(part, dir_fd=dfd)
            fd = os.open(
                part,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dfd,
            )
            try:
                with os.fdopen(fd, "wb") as fh:
                    if owner is not None and os.geteuid() == 0:
                        os.fchown(fh.fileno(), *owner)
                    try:
                        async with self._client._http.stream(
                            "GET",
                            url,
                            headers=self._auth(),
                            timeout=httpx.Timeout(self.t.export_s, connect=3.0),
                        ) as resp:
                            resp.raise_for_status()
                            async for chunk in resp.aiter_bytes():
                                fh.write(chunk)
                    except httpx.HTTPError as exc:
                        raise SwitchError(
                            f"downloading the archive failed ({type(exc).__name__}: {exc})",
                            bank=bank,
                        ) from exc
                    fh.flush()
                    os.fsync(fh.fileno())
                # A bank exported again within a run (it moved, or it is
                # being rebuilt) never overwrites its earlier archive.
                kept = 0
                while self._exists_in(dfd, name):
                    kept += 1
                    earlier = f"{name[:-4]}.earlier-{kept}.zip"
                    if not self._exists_in(dfd, earlier):
                        os.rename(name, earlier, src_dir_fd=dfd, dst_dir_fd=dfd)
                os.replace(part, name, src_dir_fd=dfd, dst_dir_fd=dfd)
                os.fsync(dfd)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(part, dir_fd=dfd)
                raise
        entry.archive = name
        return self._archive_counts(journal, entry)

    @staticmethod
    def _exists_in(dir_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _download_path(meta: Mapping[str, Any], bank: str) -> str:
        """The archive's path on the engine. Never a URL on another host."""
        raw = meta.get("download_url")
        if isinstance(raw, str) and raw.startswith("/v1/default/files/download/"):
            return raw
        key = meta.get("storage_key")
        if isinstance(key, str) and key and ".." not in key.split("/"):
            return "/v1/default/files/download/" + quote(key, safe="/")
        raise SwitchError(
            "the export finished without a download location on the engine", bank=bank
        )

    def _archive_counts(self, journal: Journal, entry: BankEntry) -> dict[str, int]:
        """What the archive says it holds. Raises :class:`SwitchError` for an unusable one."""
        bank = entry.bank
        try:
            with self._archive_dir_fd(journal) as dfd:
                fd = os.open(str(entry.archive), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                with os.fdopen(fd, "rb") as fh:
                    if os.fstat(fh.fileno()).st_size == 0:
                        raise SwitchError("the archive is empty", bank=bank)
                    with zipfile.ZipFile(fh) as zf:
                        manifest = json.loads(zf.read("manifest.json"))
                        models: int | None = None
                        if "mental_models.json" in zf.namelist():
                            rows = json.loads(zf.read("mental_models.json"))
                            models = len(rows) if isinstance(rows, list) else None
        except SwitchError:
            raise
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise SwitchError(
                f"the archive is not a readable transfer archive ({type(exc).__name__}: {exc})",
                bank=bank,
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("archive_type") != "bank":
            raise SwitchError("the archive is not a whole-bank archive", bank=bank)
        if manifest.get("source_bank_id") != bank:
            raise SwitchError(
                f"the archive is of bank {manifest.get('source_bank_id')!r}, not this one",
                bank=bank,
            )
        if models is None:
            # ``mental_model_count`` leaves out the models that back a
            # knowledge page; the pages are counted separately.
            models = int(manifest.get("mental_model_count") or 0) + int(
                manifest.get("knowledge_page_count") or 0
            )
        return {
            "facts": int(manifest.get("fact_count") or 0),
            "observations": int(manifest.get("observation_count") or 0),
            "mental_models": models,
            "directives": int(manifest.get("directive_count") or 0),
            "documents": int(manifest.get("document_count") or 0),
        }

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
            moved = _diff(before["counts"], after["counts"]) or _signals_moved(
                before["fingerprint"], after["fingerprint"]
            )
            differs = _diff(after["counts"], archived)
            extra = archived["observations"] - after["counts"]["observations"]
            if journal.allow_pending:
                # Operations may still be writing, so the archive, not the
                # moving bank, is what the rebuilt bank must match.
                moved, differs, extra = {}, {}, 0
            if moved:
                last = "it changed while it was being exported"
                continue
            if differs or extra > 0:
                shown = _diff_text(differs) or f"observations {archived['observations']}"
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

    async def _restore(
        self, journal: Journal, entry: BankEntry, target_id: str, *, via: str | None = None
    ) -> dict[str, Any]:
        """Restore ``entry``'s archive into ``target_id``; returns that bank's state.

        The engine records a restore against the bank named in the URL, which
        must therefore exist while the target must not: ``via`` is that bank,
        the original by default.
        """
        assert self._target is not None
        await self._ensure_engine(
            self._target, port=journal.private_port, run_id=journal.run_id, journal=journal
        )
        url_bank = via or entry.bank
        try:
            with self._archive_dir_fd(journal) as dfd:
                fd = os.open(str(entry.archive), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                with os.fdopen(fd, "rb") as fh:
                    resp = await self._client._http.post(
                        f"{_bank_path(url_bank)}/transfer/import",
                        headers=self._auth(),
                        params={"mode": "restore", "target_bank_id": target_id},
                        files={"file": (str(entry.archive), fh, "application/zip")},
                        timeout=httpx.Timeout(self.t.export_s, connect=3.0),
                    )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SwitchError(
                f"starting the restore into {target_id}: the engine answered HTTP "
                f"{exc.response.status_code} {http_detail(exc.response.text)}".rstrip(),
                bank=entry.bank,
            ) from exc
        except (OSError, httpx.HTTPError) as exc:
            raise SwitchError(
                f"starting the restore into {target_id} failed ({type(exc).__name__}: {exc})",
                bank=entry.bank,
            ) from exc
        body = resp.json() if resp.content else {}
        operation_id = str((body or {}).get("operation_id") or "")
        if not operation_id:
            raise SwitchError("the restore returned no operation id", bank=entry.bank)
        entry.restore_op = {
            "bank": url_bank,
            "id": operation_id,
            "target": target_id,
            "engine_pid": self._engine_pid(journal),
        }
        self._save(journal)
        budget = self._restore_budget(entry)
        try:
            await self._wait_operation(
                url_bank, operation_id, timeout_s=budget, what=f"the restore into {target_id}"
            )
        except SwitchError as exc:
            # Recorded against the scratch bank for a restore into the own
            # id; it is still this bank's failure.
            exc.bank = entry.bank
            raise
        entry.restore_op = None
        # Counted once the engine has stopped working on the new bank.
        return await self._quiet_state(target_id)

    def _restore_budget(self, entry: BankEntry) -> float:
        embedded = entry.counts.get("facts", 0) + entry.counts.get("observations", 0)
        return max(self.t.restore_floor_s, self.t.restore_factor * embedded / RESTORE_FACTS_PER_S)

    def _engine_pid(self, journal: Journal) -> int | None:
        """The process that holds the engine's private port, when it is the engine's unit."""
        if journal.private_port is None:
            return None
        seen = self._listener_owner(journal.private_port)
        pid = seen.get("pid")
        return pid if seen.get("owner") == "unit" and isinstance(pid, int) else None

    async def _restore_checked(
        self, journal: Journal, entry: BankEntry, *, via: str | None = None
    ) -> None:
        """Restore into the temporary id and require the result to match the archive."""
        state = await self._restore(journal, entry, entry.temp, via=via)
        entry.restored_counts = state["counts"]
        entry.restored_fingerprint = state["fingerprint"]
        differs = _diff(entry.counts, state["counts"], observation_floor=True)
        if differs:
            self._save(journal)
            raise SwitchError(
                f"bank {entry.bank} did not come back whole: {_diff_text(differs)}",
                bank=entry.bank,
                details={"differs": differs},
            )

    # ── journal, lock, record ───────────────────────────────────────────

    def _save(self, journal: Journal) -> None:
        """Write the journal and its mirror, each atomically and synced to disk."""
        journal.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        text = json.dumps(journal.to_dict(), indent=2) + "\n"
        _write_synced(journal_path(), text, mode=0o644)
        _write_synced(Path(journal.archive_dir) / mirror_name(), text, mode=0o600)

    def _retire_journal(self, journal: Journal) -> None:
        """Close a finished run: the mirror keeps the last word, the primary goes."""
        journal.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        text = json.dumps(journal.to_dict(), indent=2) + "\n"
        # Best effort: a mirror that cannot be written (its directory was
        # removed) must not keep a finished run looking unfinished for ever.
        with contextlib.suppress(OSError):
            _write_synced(Path(journal.archive_dir) / mirror_name(), text, mode=0o600)
        journal_path().unlink(missing_ok=True)

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        """Hold the one switch lock for the life of the block.

        An ``flock`` on a file under ``state/``: the kernel drops it when the
        process ends, so a killed run never leaves a stale lock behind.
        """
        path = lock_path()
        os.close(nofollow.open_dir(path.parent, create=True))
        # Read-only is enough for ``flock``, and it means a lock file left
        # root-owned by one run never locks the service account out of the next.
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SwitchRefused(
                    "another `hal0 memory embedder switch` is running on this box "
                    f"(it holds {path}); wait for it to finish"
                ) from None
            owner = perms.state_root_owner()
            if owner is not None:
                with contextlib.suppress(OSError):
                    os.fchown(fd, *owner)
            yield
        finally:
            os.close(fd)

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
            "totals": dict.fromkeys(COUNT_KEYS, 0),
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
        job = update_in_progress()
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
        refusals.extend(unit_refusals(self._unit_text(), self._normal_port()))

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

        # The banks.
        try:
            for bank in await self._bank_ids():
                state = await self.bank_state(bank)
                plan["banks"].append(
                    {k: state[k] for k in ("counts", "links", "pending", "processing")}
                    | {"bank": bank}
                )
                for key in COUNT_KEYS:
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
            refuse("engine_error", exc.message)
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
        busy = [b for b in plan["banks"] if b["pending"] or b["processing"]]
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
            "embedding_s": round(RESTORES_PER_BANK * embedded / RESTORE_FACTS_PER_S),
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
        store_bytes = _du_bytes(hs_dir / _PG_DIR)
        store_at = _nearest_existing(hs_dir / _PG_DIR)
        archive_at = _nearest_existing(archive_root())
        store_free = shutil.disk_usage(store_at).free
        archive_free = shutil.disk_usage(archive_at).free
        store_need = int(store_bytes * STORE_FREE_FACTOR)
        archive_need = int(store_bytes * ARCHIVE_FREE_FACTOR)
        same = _device(store_at) == _device(archive_at)
        disk = {
            "archive_root": str(archive_root()),
            "store_bytes": store_bytes,
            "same_filesystem": same,
            "store_free_bytes": store_free,
            "store_needed_bytes": store_need + (archive_need if same else 0),
            "archive_free_bytes": archive_free,
            "archive_needed_bytes": archive_need,
        }
        if store_bytes == 0:
            warnings.append(
                f"could not measure the engine's data directory ({hs_dir}/{_PG_DIR}); "
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
                    f"could not create the archive directory under {archive_root()}: {exc}", plan
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
                        f"{SERVICE}. Nothing was exported; the run is undone and the engine is "
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

    async def _settle(self, journal: Journal) -> dict[str, dict[str, Any]]:
        """Wait for every bank's queue to drain; returns each bank's state."""
        deadline = self._clock() + self.t.settle_s
        while True:
            ids = [b for b in await self._bank_ids() if b not in journal.own_ids()]
            states = {bank: await self.bank_state(bank) for bank in ids}
            busy = {b: s for b, s in states.items() if s["pending"] or s["processing"]}
            if not busy or journal.allow_pending:
                return states
            if self._clock() >= deadline:
                listing = ", ".join(
                    f"{bank} ({s['pending']} pending, {s['processing']} processing)"
                    for bank, s in busy.items()
                )
                raise SwitchError(
                    f"operations queued before the pause did not finish within "
                    f"{self.t.settle_s:g}s: {listing}"
                )
            await self._sleep(self.t.poll_s)

    async def _pause(self, journal: Journal) -> None:
        """Pause writes through hal0 and let the queues drain before the engine is restarted."""
        self._say("pausing memory writes")
        write_gate.engage_gate(run_id=journal.run_id, archive_dir=journal.archive_dir)
        self._hook("paused")
        await self._settle(journal)

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

    async def _original_moved(self, entry: BankEntry) -> str:
        """How the original differs from what its archive was taken from; empty when it does not.

        With the engine isolated this should never be anything but empty. It
        is read once the engine is quiet, and observations are not part of
        it: the engine's own consolidation may add some, and the rebuilt bank
        derives them again from the same facts.
        """
        state = await self._quiet_state(entry.bank)
        reasons: list[str] = []
        differs = _diff(entry.counts, state["counts"])
        if differs:
            reasons.append(_diff_text(differs))
        signals = _signals_moved(entry.fingerprint, state["fingerprint"])
        if signals:
            reasons.append("changed since its export: " + ", ".join(signals))
        if state["pending"] or state["processing"]:
            reasons.append(
                f"{state['pending']} pending and {state['processing']} processing operations"
            )
        return "; ".join(reasons)

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
            f"look at it on the engine's private port, and if it is not wanted delete it "
            f"(`curl -X DELETE http://127.0.0.1:{journal.private_port}{_bank_path(bank)}`); then "
            "`hal0 memory embedder switch --resume` carries on, restoring a bank that is "
            "missing from its archive"
        )

    async def _rebuilt_differs(self, entry: BankEntry, bank_id: str) -> str:
        """How the rebuilt bank, under ``bank_id``, differs from its archive; empty when not.

        Read once the engine is quiet. It may hold more observations than its
        archive (the engine consolidates what was not consolidated yet), never
        fewer.
        """
        state = await self._quiet_state(bank_id)
        reasons: list[str] = []
        differs = _diff(entry.counts, state["counts"], observation_floor=True)
        if differs:
            reasons.append(_diff_text(differs))
        signals = _signals_moved(entry.restored_fingerprint, state["fingerprint"])
        if signals:
            reasons.append("differs from the bank that was rebuilt in: " + ", ".join(signals))
        return "; ".join(reasons)

    async def _rebuild(self, journal: Journal, entry: BankEntry, index: int) -> None:
        """Take the original as it is now and rebuild its replacement from that."""
        self._say(f"exporting and rebuilding {entry.bank} again")
        await self._delete_bank(entry.temp)
        entry.step = STEP_PLANNED
        entry.note = None
        self._save(journal)
        await self._export_verified(journal, entry, index)
        await self._restore_checked(journal, entry)
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
        differs = _diff(entry.counts, state["counts"], observation_floor=True)
        if differs:
            reasons.append(_diff_text(differs))
        signals = _signals_moved(entry.fingerprint, state["fingerprint"])
        if entry.contents:
            signals += _contents_moved(entry.contents, await self.content_state(entry.bank))
        if signals:
            reasons.append("differs from the original as it was archived: " + ", ".join(signals))
        return "; ".join(reasons)

    async def _ensure_scratch(self, journal: Journal) -> None:
        """Create the empty bank a restore into a bank's own id is recorded against.

        The engine records a restore against the bank in the URL and refuses
        a target that exists, so the URL cannot be the bank being restored.
        """
        if journal.scratch not in set(await self._bank_ids()):
            await self._json(
                "PUT",
                _bank_path(journal.scratch),
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
        await self._restore(journal, entry, entry.bank, via=journal.scratch)

    async def _await_restore(self, journal: Journal, entry: BankEntry) -> None:
        """Wait until ``entry``'s restore, if one may still run, has ended. Never cancel it.

        Hindsight 1.2.0 cancels cooperatively: ``DELETE`` on an operation flips
        it to ``cancelled`` at once, but a bank restore never looks at that, so
        its worker goes on writing, and the ``completed`` or ``failed`` it
        writes at the end is suppressed once the row says ``cancelled``
        (``cancel_operation`` and ``_mark_operation_*`` in ``memory_engine.py``).
        A cancelled restore therefore looks ended while it still writes, and
        nothing the engine reports says when it really stops. So the switch
        never cancels a restore; it waits for ``completed`` or ``failed``, the
        worker's own last word. A restore found ``cancelled`` (by someone
        else) or gone from the engine is believed ended only when the engine
        process it was sent to is gone: a restart of the unit ends its worker,
        and the engine's start-up recovery requeues only ``processing`` rows.
        Anything else is refused, and nothing is deleted or restored again.
        """
        op = entry.restore_op
        if not op:
            return
        path = f"{_bank_path(op['bank'])}/operations/{quote(op['id'], safe='')}"
        target = op.get("target") or entry.bank
        budget = 2 * self._restore_budget(entry)
        deadline = self._clock() + budget
        delay = min(0.2, self.t.poll_s)
        while True:
            found = await self._json("GET", path, what="reading the restore", bank=entry.bank)
            status = found.get("status") if isinstance(found, dict) else None
            if status in ("completed", "failed"):
                break
            if status not in _OP_LIVE:
                sent_to, now = op.get("engine_pid"), self._engine_pid(journal)
                if sent_to is not None and now is not None and now != sent_to:
                    break
                raise SwitchError(
                    f"the restore of {entry.bank} into {target} (operation {op['id']}) is "
                    f"recorded as {status or 'unknown'}. Hindsight lets a cancelled restore "
                    "go on writing and never reports when it stops, so the switch cannot "
                    f"tell whether {target} is still being written; nothing was deleted or "
                    "restored again. Restart the engine (`systemctl restart "
                    f"{SERVICE}`), which ends that restore, then run "
                    "`hal0 memory embedder switch --resume`",
                    bank=entry.bank,
                    details={"operation_id": op["id"], "status": status},
                )
            if self._clock() >= deadline:
                raise SwitchError(
                    f"the restore of {entry.bank} into {target} (operation {op['id']}) is "
                    f"still {status} after {budget:g}s. The switch does not cancel a restore "
                    "(Hindsight would mark it cancelled and let it go on writing), and "
                    f"nothing was deleted or restored again. Let it finish (`journalctl -u "
                    f"{SERVICE}`), then run `hal0 memory embedder switch --resume` again",
                    bank=entry.bank,
                    details={"operation_id": op["id"], "status": status},
                )
            await self._sleep(delay)
            delay = min(self.t.poll_s, delay * 1.5)
        entry.restore_op = None
        self._save(journal)

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
                raise SwitchError(
                    f"the rebuilt bank {temp} does not match the archive of {bank} ({wrong}); "
                    f"{bank} was not touched. Run --resume, which rebuilds it",
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
            if wrong:
                self._say(f"{bank} under its own id is not whole ({wrong}); restoring it again")
                await self._delete_bank(bank)
                action = ACTION_PLACE

        if action == ACTION_PLACE:
            self._say(f"restoring {bank} into its own id")
            await self._place(journal, entry)
            wrong = await self._placed_differs(entry)
            if wrong:
                kept = (
                    f"its rebuilt copy {temp} is kept"
                    if temp in set(await self._bank_ids())
                    else "it has no rebuilt copy"
                )
                raise SwitchError(
                    f"bank {bank}, restored into its own id, does not match the original as it "
                    f"was archived ({wrong}); {kept}, and its archive is "
                    f"{journal.archive_dir}/{entry.archive}. Run --resume, which restores it "
                    "again",
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
                    tainted = await self._stray_banks(journal)
                    for entry in journal.banks:
                        if entry.step != STEP_PLANNED and await self._original_moved(entry):
                            tainted.append(entry.bank)
            if journal.isolated:
                await self._ensure_engine(
                    previous, port=None, run_id=journal.run_id, journal=journal
                )
                await self._sweep(journal)
            if journal.flip_started:
                # Only now: the engine is confirmed on the previous embedder.
                embedder_record.save_embedder_record(
                    self._record_from(
                        journal.previous,
                        mixed=bool(journal.previous.get("mixed")) or bool(tainted),
                    )
                )
                if tainted and not journal.previous.get("mixed"):
                    self._say(
                        "warning: " + ", ".join(tainted) + " appeared or changed while the "
                        "engine ran the target embedder; the store is now recorded as mixed"
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
        self, journal: Journal, entry: BankEntry, present: set[str]
    ) -> dict[str, Any]:
        """What ``--resume`` would do for one bank, with the checks it would run."""
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
                    operator = "nothing: --resume deletes it and restores it again"
            elif action == ACTION_RECREATED:
                problem = "the original id exists again beside the rebuilt bank"
                operator = self._way_out(journal, entry.bank)
            elif action == ACTION_DELETE_SENT:
                problem = "the run stopped while deleting the original, which is still there"
                operator = self._way_out(journal, entry.bank)
        except SwitchError as exc:
            problem = exc.message
        return {
            "bank": entry.bank,
            "temp": entry.temp,
            "step": entry.step,
            "original_present": entry.bank in present,
            "rebuilt_present": entry.temp in present,
            "action": action,
            "problem": problem,
            "operator": operator,
        }

    async def status(self) -> dict[str, Any]:
        """Where a switch stands: journal, pause, engine, banks, and what ``--resume`` would do.

        Reads only. It runs the same checks a resume would, so a bank that
        moved or does not match is reported here first. Works without a
        journal and without the engine.
        """
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
            "resume": [],
            "summary": "",
        }
        live = _pair(self._evidence().get("live"))
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
            out["engine"]["error"] = f"port {port} held by {holder.get('holder')}, not by {SERVICE}"
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
                out["banks"].append(
                    {"bank": bank, "role": role, "counts": (await self.bank_state(bank))["counts"]}
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
                out["resume"].append(await self._assess(journal, entry, present))
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
                else _pair(self._evidence().get("live")) or self._configured_embedder()
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
    "COUNT_KEYS",
    "FINGERPRINT_SIGNALS",
    "HARD_COUNTS",
    "HOOK_POINTS",
    "IDS_ACROSS_SWITCH",
    "RESTORES_PER_BANK",
    "RESTORE_FACTS_PER_S",
    "BankEntry",
    "EmbedderSwitch",
    "Journal",
    "SwitchError",
    "SwitchRefused",
    "Timeouts",
    "archive_root",
    "blocking_reason",
    "effective_exec_start",
    "free_loopback_port",
    "interrupted_switch",
    "is_switch_temp",
    "isolated_port",
    "isolation_body",
    "isolation_drop_in_path",
    "journal_path",
    "load_journal",
    "locate_journal",
    "lock_held",
    "lock_path",
    "private_exec_start",
    "remove_isolation_dropin",
    "scratch_bank_id",
    "swap_action",
    "temp_bank_id",
    "update_in_progress",
    "write_isolation_dropin",
]
