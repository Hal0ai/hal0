"""Fold the old banks into ``hal0:shared`` and ``hal0:private`` (ADR-0058 decision 7a).

``hal0 memory migrate fold-in`` is the one converter from a store on the old
layout (``bank_layout.LAYOUT_LEGACY``: a platform bank, often ``shared``,
beside ``coding-agent::<repo>``, ``pi-coder``, ``Hal0::Agents`` and any
``private__<agent>`` bank) to the two banks of decision 7. It is a dry run by
default and additive: it never deletes, replaces or renames a bank. Like the
embedder switch (:mod:`hal0.memory.embedder_switch`) it runs on the box, as
root, with the engine isolated on a private loopback port and memory writes
through hal0 paused for the whole run; that machinery is
:class:`~hal0.memory.engine_maintenance.EngineMaintenance`'s, under this
command's own :data:`NAMES`.

What hindsight-api 1.2.0 gives it (read in its source, not assumed):

* ``POST /v1/default/banks/{bank}/transfer/import?mode=merge&document_conflict=skip``
  folds an archive's documents into an existing bank (``api/http.py``
  ``api_bank_transfer_import``; ``engine/transfer/importer.py``
  ``import_documents``). A merge takes the documents with their chunks and
  facts, the observations whose every source fact it imported in the same
  call (``_import_observations``), and the archive's mental models and
  knowledge pages. It takes no directives and no bank config. ``skip`` leaves
  a document whose id the bank already has alone (``_resolve_target_id``).
* An import cannot add or change tags, so the tags are rewritten in the
  archive before it is imported (:func:`rewrite_archive`).
* An observation names its source facts by ``(document_id, fact_index)``
  within the archive (``engine/transfer/schema.py``
  ``TransferObservationSource``). That is what routes an observation to the
  bank its facts go to.
* A chunk carries only its ordinal (``TransferChunk``); the engine builds the
  chunk id from the target document id, so a rewritten document id needs no
  chunk rewrite.
* Mental models are keyed ``(bank_id, id)`` with an id of at most 64
  characters, and a merge inserts them ``ON CONFLICT DO NOTHING``
  (``_restore_rows``): a model whose id the target already has is dropped
  without a word. So every model from a per-repository bank is given a new id
  (:func:`model_id_for`), and the counts are checked after.
* A knowledge-page id held by another bank gets a fresh id on import
  (``_restore_knowledge_pages``). The source bank keeps its pages, so a
  folded page keeps its id only when the archive gives it one no bank holds.
  A node's ``parent_id`` is kept only when the parent is in the same archive
  (else it lands at the root), and a page's name is unique in its folder
  (``uq_kp_folder_pagename``, a partial index ``WHERE kind = 'page'``: folder
  names are not unique; a clash is dropped ``ON CONFLICT DO NOTHING``).
  So a coding bank's pages are given new ids and hung in their project's
  folder in the archive itself (:func:`_place_pages`), the folder being the
  target's own, swapped in just before the send
  (:meth:`FoldIn._rehome_folders`), and their old ids are kept in the page
  map that ``/api/memory/compat`` reads (:func:`record_page_map`).

First the pre-check, for every bank of the run (:meth:`FoldIn._precheck_all`),
importing nothing: export the bank into the run's archive directory; rewrite
the archive into a shared part and a private part (:func:`rewrite_archive`,
pure); check, on the files, that every row the source marks private is on
the private side (:func:`routing_problems`, judged on the source archive,
not on the tags the rewrite wrote) and that the shared part holds no private
tag or id (:func:`shared_archive_problems`); and check each part against its
target (:meth:`FoldIn._clashes`): no id another bank put there, and nothing
an earlier run sent that has changed (a document's text, facts or tags, a
mental model's content or tags), gone from the old bank, or no longer goes
to that target (a document re-tagged ``visibility:private``). Then across
banks, no two may send one id to one target. It is all or nothing: when any
bank fails its pre-check, no bank is imported, every bank is left as it was,
and the run ends without a cutover. A bank an earlier run folded is never
taken on trust: it goes through the whole pre-check again on what it holds
now, because a re-tag moves neither its counts nor its last write time.

Only then the imports, bank by bank (:meth:`FoldIn._fold`): merge each part
into its target, sending only what the target is missing, and check each
target holds every id the part recorded and grew by exactly what was sent.
A bank whose import fails or lands wrong is ``left``: it stays exactly as it
was, a readable old bank, and the run carries on with the next. A partial
merge is not undone (nothing here deletes); a later run sends only what is
missing, with ``skip``.

No cutover while any bank is left (ADR-0058 decision 7a). When every old bank
is folded, and every other bank on the engine is a target, a switch's
scratch bank or named with ``--skip``, the cutover (:meth:`FoldIn._cutover`)
returns the engine to its normal address, writes the layout record as
``two_bank``, restarts hal0-api so it reads the record, retires the journal
and only then lifts the write gate. From then on hal0 reads only the two
banks; the old ones stay on the engine, untouched and unread. When any bank
is left, or was not part of the run (``--bank``), the run returns the engine
to its normal address, retires the journal and lifts the gate without
touching the layout (:meth:`FoldIn._end_without_cutover`): the box stays on
the old layout and writes go on to the old banks. The ledger
(:func:`load_ledger`) keeps what each run folded and sent, and the run's
``--map`` and ``--skip``, so the next run re-checks a folded bank instead of
importing it again, sends only what an old bank gained since, and refuses a
document or model that changed after it was folded.

Operator decisions taken on 2026-11-12 (ADR-0058 decision 7a):

1. An old coding bank's facts are given an owner from the harness that wrote
   them: ``--owner HARNESS=CLIENT``, else a bridge client of the harness's
   own name, else the reserved pseudo-owner :data:`PSEUDO_OWNER_PREFIX` + harness
   (:func:`pseudo_owner`), which no client can take.
2. ``Hal0::Agents`` and ``pi-coder`` are coding banks whose project comes
   from each fact's own tags.
3. Pre-#2017 GUID documents are carried as they are.
4. The dashboard shows where a run stands and the CLI command; it does not
   start one.

The interlocks: a fold-in and an embedder switch never overlap (each one's
preflight asks the other's ``blocking_reason``), and ``hal0 update``, the
engine upgrade and bank seeding wait while either is unfinished.

``hook(point)`` is called at every step boundary (:data:`HOOK_POINTS`) so a
test can stop a run exactly there.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import structlog

from hal0.memory import bank_layout, write_gate
from hal0.memory import engine_maintenance as em
from hal0.memory.document_namespace import (
    CLIENT_DOCUMENT_ID_KEY,
    is_namespaced,
    stored_document_id,
)
from hal0.memory.embedder_record import Embedder, describe_embedder
from hal0.memory.envelope import (
    AGENT_TAG_PREFIX,
    PROJECT_TAG_PREFIX,
    PROJECT_UNRESOLVED_TAG,
    UNKNOWN_AGENT,
    apply_envelope,
    resolve_observation_scopes,
)
from hal0.memory.projects import (
    ProjectRegistry,
    resolve_project,
    strip_legacy_bank_prefix,
)
from hal0.memory.read_scope import VISIBILITY_PRIVATE_TAG

log = structlog.get_logger(__name__)

JOURNAL_VERSION = 1

#: The drop-in that isolates the engine during a fold-in. Its own name, so a
#: switch interrupted by an older build is still found by the switch's.
ISOLATION_DROP_IN_NAME = "zzzz-hal0-memory-fold-in.conf"

#: Everything this command is called, on disk and in what it says. Fixed once
#: shipped: an interrupted run is found again by these names.
NAMES = em.MaintenanceNames(
    command="hal0 memory migrate fold-in",
    actor="the fold-in",
    journal="memory-fold-in.json",
    lock="memory-fold-in.lock",
    archive_prefix="memory-fold-in-",
    drop_in=ISOLATION_DROP_IN_NAME,
    # Not ``journal.json``, the switch's mirror: each command finds an
    # unfinished run through the archive directory the write gate names, and
    # must never read the other's journal there.
    mirror="fold-in-journal.json",
    gate_reason=write_gate.REASON_FOLD_IN,
    log_event="hal0.memory.fold_in",
)

#: The reserved pseudo-owner of an old coding bank's facts when no client is
#: known for the harness that wrote them (operator decision 1). ``hal0``
#: names are reserved for hal0 (``hal0.bridge.keys.RESERVED_CLIENT_PREFIXES``),
#: so no client can read what is filed under one.
PSEUDO_OWNER_PREFIX = "hal0-legacy-"  # scar-ok: the reserved owner name of operator decision 1
#: The pseudo-owner's harness part for a private fact with no owner at all.
UNOWNED = "unowned"

#: Harness -> owner known without being told: Pi's memory identity is the
#: hal0-managed bridge client ``pi-coder`` (#3613), its harness name ``pi``.
DEFAULT_OWNERS: dict[str, str] = {"pi": "pi-coder"}

# Bank classes.
CLASS_PLATFORM = "platform"
CLASS_PRIVATE = "private"
CLASS_CODING = "coding"
CLASS_PROJECT = "project"
CLASS_TARGET = "target"
CLASS_EXCLUDED = "excluded"
#: A temporary bank of an embedder switch: scratch, never part of a fold-in.
CLASS_SCRATCH = "scratch"
CLASS_UNCLASSIFIED = "unclassified"
FOLDABLE = (CLASS_PLATFORM, CLASS_PRIVATE, CLASS_CODING, CLASS_PROJECT)

#: Coding banks whose project comes from each fact's own tags (operator
#: decision 2), with the harness that wrote them.
TAG_PROJECT_CODING_BANKS: dict[str, str] = {"Hal0::Agents": "hal0-agents", "pi-coder": "pi"}

#: The retired ``agents`` bank (ADR-0052); ``hal0 memory migrate platform`` folds it.
AGENTS_BANK = "agents"

_PRIVATE_BANK_PREFIX = "private__"
_PRIVATE_ID_PREFIX = "private:"

# Per-bank steps.
STEP_PLANNED = "planned"
STEP_EXPORTED = "exported"
STEP_REWRITTEN = "rewritten"
STEP_IMPORTING = "importing"
STEP_IMPORTED = "imported"
STEP_FOLDED = "folded"
STEP_LEFT = "left"
_STEPS = (
    STEP_PLANNED,
    STEP_EXPORTED,
    STEP_REWRITTEN,
    STEP_IMPORTING,
    STEP_IMPORTED,
    STEP_FOLDED,
    STEP_LEFT,
)
_DONE_STEPS = (STEP_FOLDED, STEP_LEFT)

# Run phases.
PHASE_PAUSED = "paused"
PHASE_FOLDING = "folding"
PHASE_CUTTING_OVER = "cutting_over"
#: The run ends without a cutover (a bank is not folded): the engine goes back
#: to its normal address and writes resume on the old layout.
PHASE_RELEASING = "releasing"
PHASE_COMPLETE = "complete"
#: Ended without a cutover; the ledger says what is left.
PHASE_INCOMPLETE = "incomplete"
PHASE_ABANDONED = "abandoned"
_PHASES = (
    PHASE_PAUSED,
    PHASE_FOLDING,
    PHASE_CUTTING_OVER,
    PHASE_RELEASING,
    PHASE_COMPLETE,
    PHASE_INCOMPLETE,
    PHASE_ABANDONED,
)
_FINISHED_PHASES = (PHASE_COMPLETE, PHASE_INCOMPLETE, PHASE_ABANDONED)

PART_SHARED = "shared"
PART_PRIVATE = "private"
_PARTS = (PART_SHARED, PART_PRIVATE)

#: Where ``hook`` is called. ``<bank>`` is the bank's id, ``<part>`` shared or private.
HOOK_POINTS: tuple[str, ...] = (
    "paused",
    "isolated",
    "exported:<bank>",
    "rewritten:<bank>",
    "placed:<bank>",
    "importing:<bank>:<part>",
    "imported:<bank>:<part>",
    "folded:<bank>",
    "before_cutover",
    "before_release",
    "released",
    "recorded",
    "retired",
)

#: The mental-model id column is ``VARCHAR(64)`` in hindsight-api 1.2.0.
MODEL_ID_MAX = 64

#: Free space wanted, as multiples of the engine's data directory: the merged
#: copies sit beside the old banks, and every archive is kept twice (as
#: exported and as rewritten).
STORE_FREE_FACTOR = 1.0
ARCHIVE_FREE_FACTOR = 1.0

#: The mental-model columns that hold what a refresh generated. A model from a
#: bank that held private facts is carried with these cleared, so nothing it
#: synthesised from them reaches the shared bank; the engine refreshes it from
#: what the shared bank holds.
_GENERATED_MODEL_KEYS = (
    "content",
    "reflect_response",
    "structured_content",
    "last_refreshed_at",
    "last_memory_seen_at",
    "last_full_refreshed_at",
    "last_refresh_failed_at",
    "history",
)

#: What :data:`_GENERATED_MODEL_KEYS` are set to when a model is carried with
#: its generated content cleared. The engine's merge import writes a model row
#: verbatim (``_restore_rows`` in ``engine/transfer/importer.py``), so a key
#: set to ``None`` is an explicit NULL and the column default does not apply.
#: In hindsight-api 1.2.0 ``mental_models.content`` and
#: ``mental_models.last_refreshed_at`` are ``NOT NULL`` (created as
#: ``pinned_reflections`` in alembic ``n9i0j1k2l3m4``, renamed since); the
#: other timestamps and ``reflect_response`` / ``structured_content`` are
#: nullable, and ``history`` is no longer a column (alembic ``a7b8c9d0e1f2``
#: dropped it; the import ignores keys that are not columns). A cleared model
#: says it was last refreshed at the epoch: it holds nothing generated, and the
#: target treats it as stale; it is refreshed when its trigger fires.
NEVER_REFRESHED_AT = "1970-01-01T00:00:00+00:00"
_CLEARED_MODEL_VALUES: dict[str, Any] = {
    "content": "",
    "history": [],
    "last_refreshed_at": NEVER_REFRESHED_AT,
}

#: The ``mental_models`` columns hindsight-api 1.2.0 declares ``NOT NULL`` that
#: an exported row carries. A model sent with any of them set to ``None``
#: fails the engine's insert after the part's documents landed (the import
#: runs no transaction around models), so the rewrite refuses such a bank.
MODEL_NOT_NULL_KEYS = (
    "id",
    "bank_id",
    "subtype",
    "name",
    "description",
    "source_query",
    "content",
    "max_tokens",
    "trigger",
    "last_refreshed_at",
    "created_at",
)


def model_null_problems(models: Iterable[Mapping[str, Any]]) -> list[str]:
    """One line per model row that sets a :data:`MODEL_NOT_NULL_KEYS` column to ``None``."""
    out = []
    for model in models:
        nulls = [k for k in MODEL_NOT_NULL_KEYS if k in model and model[k] is None]
        if nulls:
            out.append(f"mental model {model.get('id')}: {', '.join(nulls)} would be NULL")
    return out


_CONTENT_PAGE = 500

_API_SERVICE = "hal0-api.service"


class FoldInError(em.MaintenanceError):
    """A step of the fold-in failed. The message is written for the operator.

    ``resumable`` is true when writes stay paused and ``--resume`` carries on.
    """


class FoldInRefused(FoldInError):
    """Refused before anything was changed. ``plan`` is the preflight report, when there is one."""

    def __init__(self, message: str, plan: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.plan = plan or {}


class BankLeft(FoldInError):
    """This bank cannot be folded now; it stays as it was and the run goes on."""


# ── classification ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class BankClass:
    """What one bank on the engine is to the fold-in.

    ``harness`` is who wrote a coding bank (the first part of
    ``<harness>::<repo>``, ``pi`` for ``pi-coder``); ``agent`` the agent of a
    ``private__<agent>`` bank; ``project`` the project a bank id names, when
    it names one. ``tag_project`` is true for a coding bank whose project comes
    from each fact's tags (operator decision 2).
    """

    bank: str
    cls: str
    harness: str | None = None
    agent: str | None = None
    project: str | None = None
    tag_project: bool = False
    reason: str = ""

    @property
    def foldable(self) -> bool:
        return self.cls in FOLDABLE

    @property
    def coding(self) -> bool:
        return self.cls == CLASS_CODING


def _slug_of(text: str) -> str:
    """A lower-case ``[a-z0-9_-]`` slug of ``text`` (a harness or bank name)."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", text.strip().lower()).strip("-_")
    return slug or "unknown"


def classify_bank(
    bank: str,
    *,
    platform_bank: str,
    targets: Iterable[str],
    maps: Mapping[str, str] | None = None,
) -> BankClass:
    """Classify one bank id (pure).

    ``platform_bank`` is the old layout's bank behind ``shared``; ``targets``
    the two banks; ``maps`` the operator's ``--map BANK=HARNESS`` (a bank to
    fold as a coding bank written by ``HARNESS``, project from its facts' tags).
    """
    from hal0.memory.embedder_switch import is_switch_temp

    if bank in set(targets):
        return BankClass(bank, CLASS_TARGET, reason="a target of the fold-in")
    if bank == AGENTS_BANK:
        return BankClass(
            bank,
            CLASS_EXCLUDED,
            reason="the retired agents bank: `hal0 memory migrate platform` folds it",
        )
    if is_switch_temp(bank):
        return BankClass(bank, CLASS_SCRATCH, reason="a temporary bank of an embedder switch")
    if bank == platform_bank:
        return BankClass(bank, CLASS_PLATFORM)
    if bank.startswith(_PRIVATE_BANK_PREFIX) and len(bank) > len(_PRIVATE_BANK_PREFIX):
        return BankClass(bank, CLASS_PRIVATE, agent=bank[len(_PRIVATE_BANK_PREFIX) :])
    if maps and bank in maps:
        return BankClass(bank, CLASS_CODING, harness=_slug_of(maps[bank]), tag_project=True)
    if bank in TAG_PROJECT_CODING_BANKS:
        return BankClass(
            bank, CLASS_CODING, harness=TAG_PROJECT_CODING_BANKS[bank], tag_project=True
        )
    slug = strip_legacy_bank_prefix(bank)
    if slug is not None:
        if bank.startswith("project__"):
            return BankClass(bank, CLASS_PROJECT, project=slug)
        return BankClass(bank, CLASS_CODING, harness=bank.split("::", 1)[0], project=slug)
    return BankClass(
        bank,
        CLASS_UNCLASSIFIED,
        reason="not a bank the fold-in knows; name its harness with --map BANK=HARNESS to "
        "fold it as a coding bank whose project comes from its facts' tags",
    )


def pseudo_owner(harness: str) -> str:
    """:data:`PSEUDO_OWNER_PREFIX` + ``<harness>``, fitting a bridge-client name's grammar."""
    return (PSEUDO_OWNER_PREFIX + _slug_of(harness))[:64].rstrip("-_")


def resolve_owner(
    candidate: str | None,
    *,
    owners: Mapping[str, str],
    known: Iterable[str],
    keep_unknown: bool = False,
) -> tuple[str, str]:
    """``(owner, source)`` for the harness or agent that wrote a fact (operator decision 1).

    ``owners`` is the harness map (``--owner``, then :data:`DEFAULT_OWNERS`),
    ``known`` the bridge clients that hold the memory scope. ``source`` is
    ``map``, ``client``, ``agent`` or ``pseudo``. The name is lower-cased first.

    ``keep_unknown`` is for an agent id hal0's own provider stamped (a
    private fact in the platform bank, a project bank or a
    ``private__<agent>`` bank): that id is the agent's own, so a name the map
    and the clients do not know is kept as it is (``agent``) rather than
    filed under a pseudo-owner it could not read.
    """
    name = (candidate or "").strip().lower()
    if not name or name in (UNKNOWN_AGENT, "anonymous"):
        return pseudo_owner(UNOWNED), "pseudo"
    if name in owners:
        return owners[name], "map"
    if name in set(known):
        return name, "client"
    if keep_unknown:
        return name, "agent"
    return pseudo_owner(name), "pseudo"


# ── the rewrite (pure) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RewritePlan:
    """Everything :func:`rewrite_archive` needs to know about one bank."""

    bank: BankClass
    owners: Mapping[str, str] = field(default_factory=dict)
    known_clients: frozenset[str] = frozenset()
    registry: ProjectRegistry = field(default_factory=ProjectRegistry)
    shared_prefixes: tuple[str, ...] = ()


@dataclass
class RewriteResult:
    """The two archives of one bank and what each must add to its target."""

    shared: bytes | None
    private: bytes | None
    expected: dict[str, dict[str, int]]
    report: dict[str, Any]
    #: Document, mental-model and page (backing model) ids per part, for the
    #: checks after the import, and each model's :func:`model_digest`.
    ids: dict[str, dict[str, Any]]
    #: Where every row of each part came from in the source archive, and what
    #: was dropped: what :func:`routing_problems` judges.
    provenance: dict[str, Any] = field(default_factory=dict)
    #: Per part, :func:`document_digest` of each document's source, by new id.
    digests: dict[str, dict[str, str]] = field(default_factory=dict)


def _lower(tag: Any) -> str:
    return str(tag).strip().lower()


def _is_private_tag(tag: Any) -> bool:
    return _lower(tag) == VISIBILITY_PRIVATE_TAG


def _agent_of(tags: Iterable[Any]) -> str | None:
    for tag in tags or ():
        text = str(tag).strip()
        if text.lower().startswith(AGENT_TAG_PREFIX) and text[len(AGENT_TAG_PREFIX) :].strip():
            return text[len(AGENT_TAG_PREFIX) :].strip()
    return None


def _caller_tags(tags: Iterable[Any]) -> list[str]:
    """Tags without the ones the provider owns (``agent:``, ``visibility:``)."""
    return [
        str(t)
        for t in tags or ()
        if isinstance(t, str) and not _lower(t).startswith(("agent:", "visibility:"))
    ]


def _project_tags(tags: Iterable[Any]) -> list[str]:
    return [t for t in tags if isinstance(t, str) and t.startswith(PROJECT_TAG_PREFIX)]


@dataclass
class _DocRoute:
    part: str
    owner: str
    owner_source: str
    candidate: str | None
    new_id: str
    slug: str | None
    coding: bool
    digested: bool = False
    #: A private document whose facts name more than one owner (item 9 of the
    #: review): it cannot be filed under one, so its bank is refused.
    owners_conflict: tuple[str, ...] = ()


def _document_private(plan: RewritePlan, doc: Mapping[str, Any]) -> bool:
    """A document goes to the private bank when anything about it says it is private.

    A ``private__<agent>`` bank, a ``private:`` id, or a ``visibility:private``
    tag on the document or on any of its facts. A document with one private
    fact is private whole: the split is per document, because the engine
    stores a document and its facts together.
    """
    if plan.bank.cls == CLASS_PRIVATE:
        return True
    if str(doc.get("id") or "").startswith(_PRIVATE_ID_PREFIX):
        return True
    if any(_is_private_tag(t) for t in doc.get("tags") or ()):
        return True
    return any(
        _is_private_tag(t) for fact in doc.get("facts") or () for t in fact.get("tags") or ()
    )


def _id_owner(doc_id: str) -> str | None:
    """``<agent>`` of a ``private:<agent>:<id>`` document id."""
    if not doc_id.startswith(_PRIVATE_ID_PREFIX):
        return None
    rest = doc_id[len(_PRIVATE_ID_PREFIX) :]
    agent, sep, _ = rest.partition(":")
    return agent if sep and agent else None


def _doc_tags_union(doc: Mapping[str, Any]) -> list[str]:
    out: list[str] = [str(t) for t in doc.get("tags") or () if isinstance(t, str)]
    for fact in doc.get("facts") or ():
        out.extend(str(t) for t in fact.get("tags") or () if isinstance(t, str))
    return list(dict.fromkeys(out))


def _document_slug(plan: RewritePlan, doc: Mapping[str, Any]) -> str | None:
    """The one project of a document, for its namespaced id; ``None`` for none or several."""
    bank = plan.bank
    if bank.project is not None:
        resolution = resolve_project(
            bank_segment=bank.project, registry=plan.registry, coding=bank.coding
        )
    else:
        resolution = resolve_project(
            tags=_doc_tags_union(doc), registry=plan.registry, coding=bank.coding
        )
    return resolution.slug


def _private_owner(
    plan: RewritePlan, doc: Mapping[str, Any]
) -> tuple[str, str, str | None, tuple[str, ...]]:
    """``(owner, source, candidate, conflict)`` of a private document.

    A ``private__<agent>`` bank's documents are that agent's: the bank is the
    provider's record of whose they are. Anywhere else the owner comes from
    what the document says (its ``private:<agent>:`` id, its own ``agent:``
    tag, each fact's ``agent:`` tag), every name resolved the same way. When
    those name more than one owner, ``conflict`` lists them: the document
    cannot be filed under one owner without handing one agent's fact to
    another, so the bank is refused (fail closed).
    """
    bank = plan.bank
    doc_id = str(doc.get("id") or "")
    if bank.cls == CLASS_PRIVATE:
        owner, source = resolve_owner(
            bank.agent, owners=plan.owners, known=plan.known_clients, keep_unknown=True
        )
        return owner, source, bank.agent, ()
    names = [_id_owner(doc_id), _agent_of(doc.get("tags") or ())]
    names += [_agent_of(f.get("tags") or ()) for f in doc.get("facts") or ()]
    named = [n for n in names if n and n.strip().lower() not in (UNKNOWN_AGENT, "anonymous")]
    resolved: dict[str, tuple[str, str, str]] = {}
    for name in named:
        if bank.coding:
            owner, source = resolve_owner(name, owners=plan.owners, known=plan.known_clients)
        else:
            owner, source = resolve_owner(
                name, owners=plan.owners, known=plan.known_clients, keep_unknown=True
            )
        resolved.setdefault(owner, (owner, source, name))
    if len(resolved) > 1:
        first = next(iter(resolved.values()))
        return first[0], first[1], first[2], tuple(sorted(resolved))
    if resolved:
        owner, source, name = next(iter(resolved.values()))
        return owner, source, name, ()
    if bank.coding:
        owner, source = resolve_owner(bank.harness, owners=plan.owners, known=plan.known_clients)
        return owner, source, None, ()
    return pseudo_owner(UNOWNED), "pseudo", None, ()


def _route_document(plan: RewritePlan, doc: Mapping[str, Any]) -> _DocRoute:
    bank = plan.bank
    doc_id = str(doc.get("id") or "")
    private = _document_private(plan, doc)
    first_fact_agent = next(
        (a for a in (_agent_of(f.get("tags") or ()) for f in doc.get("facts") or ()) if a), None
    )
    doc_agent = _agent_of(doc.get("tags") or ()) or first_fact_agent
    slug = _document_slug(plan, doc)
    if private:
        owner, source, candidate, conflict = _private_owner(plan, doc)
        prefix = f"{_PRIVATE_ID_PREFIX}{owner}:"
        own = _id_owner(doc_id)
        if doc_id.startswith(prefix):
            new_id = doc_id
        elif own is not None:
            # Another agent's prefix on a document this one owns cannot
            # happen through hal0; the id keeps what it says after the prefix.
            new_id = prefix + doc_id[len(_PRIVATE_ID_PREFIX) + len(own) + 1 :]
        else:
            new_id = prefix + doc_id
        return _DocRoute(
            PART_PRIVATE,
            owner,
            source,
            candidate,
            new_id,
            slug,
            bank.coding,
            owners_conflict=conflict,
        )
    if bank.coding:
        candidate = doc_agent or bank.harness
        owner, source = resolve_owner(candidate, owners=plan.owners, known=plan.known_clients)
        if is_namespaced(doc_id):
            new_id, digested = doc_id, False
        else:
            new_id = stored_document_id(slug, owner, doc_id, plan.shared_prefixes)
            digested = not new_id.endswith(doc_id)
        return _DocRoute(PART_SHARED, owner, source, candidate, new_id, slug, True, digested)
    return _DocRoute(
        PART_SHARED, doc_agent or UNKNOWN_AGENT, "agent", doc_agent, doc_id, slug, False
    )


def _fact_resolution(plan: RewritePlan, doc: Mapping[str, Any], fact_tags: list[str]) -> Any:
    bank = plan.bank
    if bank.project is not None:
        return resolve_project(
            bank_segment=bank.project, registry=plan.registry, coding=bank.coding
        )
    tags = fact_tags if _project_tags(fact_tags) else _caller_tags(doc.get("tags") or ())
    return resolve_project(tags=tags, registry=plan.registry, coding=bank.coding)


def _string_metadata(values: Mapping[str, Any]) -> dict[str, str]:
    """Envelope metadata as the archive's ``dict[str, str]``."""
    out: dict[str, str] = {}
    for key, value in values.items():
        out[key] = ",".join(map(str, value)) if isinstance(value, list) else str(value)
    return out


def _private_scopes(tags: list[str]) -> Any:
    from hal0.memory.hindsight_provider import _private_observation_scopes

    return _private_observation_scopes(tags)


def _retag(
    plan: RewritePlan,
    route: _DocRoute,
    tags: Iterable[Any],
    resolution: Any,
    metadata: Mapping[str, Any] | None,
) -> tuple[list[str], dict[str, str]]:
    """One write's tags through the envelope, as the provider writes them now."""
    agent = route.owner if (route.part == PART_PRIVATE or route.coding) else None
    if agent is None:
        agent = _agent_of(tags) or route.owner
    envelope = apply_envelope(
        _caller_tags(tags),
        agent=agent,
        project_resolution=resolution,
        coding=route.coding,
        metadata=metadata or {},
    )
    out = list(envelope.tags)
    if route.part == PART_PRIVATE:
        out.append(VISIBILITY_PRIVATE_TAG)
    return out, _string_metadata(envelope.metadata)


def model_id_for(prefix: str, model_id: str) -> str:
    """``<prefix>-<id>``, lower case, or a digest form that fits :data:`MODEL_ID_MAX`."""
    candidate = re.sub(r"[^a-z0-9-]+", "-", f"{prefix}-{model_id}".lower()).strip("-")
    if 0 < len(candidate) <= MODEL_ID_MAX:
        return candidate
    digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:16]
    return f"{re.sub(r'[^a-z0-9-]+', '-', prefix.lower())[:40].strip('-')}-{digest}"


def _model_prefix(bank: BankClass) -> str | None:
    """What a bank's mental models are re-keyed with; ``None`` keeps their ids."""
    if bank.cls == CLASS_PLATFORM:
        return None
    return bank.project or _slug_of(bank.bank)


def _read_archive(data: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))

        def rows(name: str) -> list[Any]:
            if name not in names:
                return []
            value = json.loads(zf.read(name))
            return value if isinstance(value, list) else []

        documents = [
            json.loads(zf.read(n))
            for n in sorted(n for n in names if n.startswith("documents/") and n.endswith(".json"))
        ]
        manifest = manifest if isinstance(manifest, dict) else {}

        def carried(key: str, name: str) -> int:
            return max(int(manifest.get(key) or 0), len(rows(name)))

        return {
            "manifest": manifest,
            "documents": documents,
            "observations": rows("observations.json"),
            "mental_models": rows("mental_models.json"),
            "knowledge_pages": rows("knowledge_pages.json"),
            "directives": rows("directives.json"),
            # What a merge import does not carry (``import_documents`` in
            # hindsight-api 1.2.0 reads none of these): counted, never read.
            "attachments": carried("attachment_count", "attachments.json"),
            "invalidated": carried(
                "invalidated_memory_count", "data/invalidated_memory_units.json"
            ),
            "model_history": len(rows("mental_model_history.json")),
        }


def _write_archive(
    source_bank: str,
    documents: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    models: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> bytes:
    """A merge-import archive (``archive_type: documents``) holding exactly these rows."""
    page_models = {p.get("mental_model_id") for p in pages if p.get("mental_model_id")}
    manifest = {
        "schema_version": 1,
        "source_bank_id": source_bank,
        "exported_at": datetime.now(UTC).isoformat(),
        "archive_type": "documents",
        "document_count": len(documents),
        "fact_count": sum(len(d.get("facts") or []) for d in documents),
        "observation_count": len(observations),
        "mental_model_count": sum(1 for m in models if m.get("id") not in page_models),
        "knowledge_page_count": len(pages),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for index, doc in enumerate(documents):
            zf.writestr(f"documents/{index:06d}.json", json.dumps(doc))
        if observations:
            zf.writestr("observations.json", json.dumps(observations))
        if models:
            zf.writestr("mental_models.json", json.dumps(models))
            zf.writestr("knowledge_pages.json", json.dumps(pages))
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue()


def _counts_of(
    documents: list[dict[str, Any]], observations: list[Any], models: list[Any]
) -> dict[str, int]:
    return {
        "facts": sum(len(d.get("facts") or []) for d in documents),
        "observations": len(observations),
        "mental_models": len(models),
        "directives": 0,
        "documents": len(documents),
    }


def _new_report() -> dict[str, Any]:
    return {
        "documents": dict.fromkeys(_PARTS, 0),
        "facts": dict.fromkeys(_PARTS, 0),
        "observations": {
            PART_SHARED: 0,
            PART_PRIVATE: 0,
            "dropped_split": 0,
            "dropped_unrouted": 0,
            "dropped_private_tag": 0,
        },
        "mental_models": {
            "carried": 0,
            "renamed": 0,
            "private": 0,
            "dropped_private": 0,
            "content_cleared": 0,
        },
        "knowledge_pages": {
            "carried": 0,
            "dropped": 0,
            #: Placed pages carried with their generated content cleared, so
            #: each is rebuilt under its project scope (:func:`_place_pages`):
            #: every one from a bank whose project comes from tags, and any
            #: other whose old trigger was not already scoped to its project.
            "content_cleared_tag_project": 0,
            "content_cleared_unscoped": 0,
        },
        "directives_not_carried": [],
        "private_facts": 0,
        "ownerless_facts": 0,
        "unreadable_shared_facts": 0,
        "mixed_owner_documents": [],
        "owners": {},
        "projects": {},
        "document_ids_rewritten": 0,
        "document_ids_digested": 0,
        #: ``mental_model_history`` rows in the export: a merge import does not
        #: carry them, and nothing reads them but the history view of a model.
        "mental_model_history_not_carried": 0,
    }


def _tally_owner(report: dict[str, Any], route: _DocRoute, facts: int) -> None:
    key = route.candidate or "(none)"
    row = report["owners"].setdefault(
        key, {"owner": route.owner, "source": route.owner_source, "facts": 0, "part": route.part}
    )
    row["facts"] += facts
    if route.owner == pseudo_owner(UNOWNED):
        report["ownerless_facts"] += facts


def _tally_projects(report: dict[str, Any], tags: list[str]) -> None:
    slugs = [t[len(PROJECT_TAG_PREFIX) :] for t in _project_tags(tags)] or ["(none)"]
    for slug in slugs:
        report["projects"][slug] = report["projects"].get(slug, 0) + 1


def _unreadable_shared(route: _DocRoute, tags: list[str]) -> bool:
    """A shared fact no agent can read: ``project:unresolved`` under a pseudo-owner.

    An unresolved fact is read only by its writer (decision 4a), and a
    pseudo-owner (:data:`PSEUDO_OWNER_PREFIX`) is no client.
    """
    if route.part != PART_SHARED or PROJECT_UNRESOLVED_TAG not in tags:
        return False
    return (_agent_of(tags) or "").startswith(PSEUDO_OWNER_PREFIX)


#: Fact fields the engine rewrites by itself, left out of :func:`document_digest`.
_VOLATILE_FACT_KEYS = frozenset(
    {"source_id", "created_at", "consolidated_at", "consolidation_failed_at", "entities"}
)


def document_digest(doc: Mapping[str, Any]) -> str:
    """A digest of what a source document says, as the old bank exported it.

    The id, the text, the tags, the chunks, and every fact without the fields
    the engine moves by itself (its ids, consolidation stamps and entity
    names). Taken from the export, before the rewrite, so a later run with a
    changed project registry still recognises a document it folded.
    """
    facts = [
        {k: v for k, v in f.items() if k not in _VOLATILE_FACT_KEYS}
        for f in doc.get("facts") or ()
        if isinstance(f, dict)
    ]
    body = {
        "id": doc.get("id"),
        "original_text": doc.get("original_text"),
        "tags": sorted(str(t) for t in doc.get("tags") or ()),
        "chunks": doc.get("chunks") or [],
        "facts": facts,
    }
    text = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def model_digest(model: Mapping[str, Any]) -> str:
    """The digest an earlier build recorded for a model: name, query, content, tags, trigger.

    Kept to read ``.ids.json`` files written before the digest was split
    (:func:`model_spec_digest`, :func:`model_content_digest`): a model whose
    combined digest is unchanged is still the fold-in's own copy.
    """
    body = {
        "name": model.get("name"),
        "source_query": model.get("source_query"),
        "content": model.get("content"),
        "tags": sorted(str(t) for t in model.get("tags") or ()),
        "trigger": _decoded(model.get("trigger")),
    }
    text = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def model_spec_digest(model: Mapping[str, Any]) -> str:
    """What a mental model is, as the fold-in sends it: name, query, tags and trigger.

    Taken from the rewritten model. A change here (a re-tag, a new scope)
    changes who may read the model, so a model whose spec changed in the old
    bank since an earlier run sent it is refused (:meth:`FoldIn._clashes`).
    """
    body = {
        "name": model.get("name"),
        "source_query": model.get("source_query"),
        "tags": sorted(str(t) for t in model.get("tags") or ()),
        "trigger": _decoded(model.get("trigger")),
    }
    text = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def model_content_digest(model: Mapping[str, Any]) -> str:
    """What a refresh generated for a mental model: its content, structured content and history.

    The engine's refresh timestamps are left out. An empty value and one the
    rewrite cleared digest the same (:data:`CLEARED_CONTENT_DIGEST`).
    """
    body = {
        "content": model.get("content") or "",
        "reflect_response": _decoded(model.get("reflect_response")) or None,
        "structured_content": _decoded(model.get("structured_content")) or None,
        "history": _decoded(model.get("history")) or [],
    }
    text = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def observation_digest(obs: Mapping[str, Any]) -> str:
    """A short digest of an observation's text: how a sent observation is known again."""
    return hashlib.sha256(str(obs.get("text")).encode("utf-8")).hexdigest()[:24]


def _decoded(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _clear_generated(model: dict[str, Any]) -> None:
    """Set every :data:`_GENERATED_MODEL_KEYS` key ``model`` has to its cleared value, in place."""
    for key in _GENERATED_MODEL_KEYS:
        if key in model:
            model[key] = copy.deepcopy(_CLEARED_MODEL_VALUES.get(key))


def _generated_kept(model: Mapping[str, Any]) -> list[str]:
    """The :data:`_GENERATED_MODEL_KEYS` ``model`` still holds something in."""
    return [
        k
        for k in _GENERATED_MODEL_KEYS
        if model.get(k) not in (None, "", [], {}, _CLEARED_MODEL_VALUES.get(k))
    ]


#: :func:`model_content_digest` of a model with nothing generated (as the
#: rewrite carries a model from a bank that holds private facts).
CLEARED_CONTENT_DIGEST = model_content_digest({})


def _private_model(row: Mapping[str, Any], owner: str) -> dict[str, Any] | None:
    """A ``private__<agent>`` bank's mental model, filed in the private bank for its owner.

    Re-keyed ``<owner>-<id>``; tagged ``agent:<owner>`` and
    ``visibility:private``; its scope confined to its owner, so the
    provider's read rule (:func:`hal0.memory.read_scope.model_scope_confined_to`
    with :meth:`HindsightProvider._is_visible_to`) gives it to that agent
    alone and a refresh reads nothing else: ``trigger.exclude_mental_models``
    and a ``trigger.tag_groups`` that ANDs the model's own scope with a strict
    ``agent:<owner>`` leaf. ``None`` when that cannot be built, and the model
    is dropped.
    """
    from hal0.memory.read_scope import model_scope_confined_to

    own = f"{AGENT_TAG_PREFIX}{owner}"
    model = dict(row)
    model["id"] = model_id_for(owner, str(row.get("id")))
    kept = [
        str(t)
        for t in row.get("tags") or ()
        if isinstance(t, str) and not _lower(t).startswith(("agent:", "visibility:"))
    ]
    model["tags"] = list(dict.fromkeys([*kept, own, VISIBILITY_PRIVATE_TAG]))
    trigger = _decoded(row.get("trigger"))
    trigger = dict(trigger) if isinstance(trigger, dict) else {}
    groups = trigger.get("tag_groups")
    if groups is None:
        groups = (
            [{"tags": kept, "match": trigger.get("tags_match") or "all_strict"}] if kept else []
        )
    if not isinstance(groups, list):
        return None
    confine = {"tags": [own, VISIBILITY_PRIVATE_TAG], "match": "all_strict"}
    trigger["tag_groups"] = [*groups, confine]
    trigger["exclude_mental_models"] = True
    model["trigger"] = trigger
    return model if model_scope_confined_to(model, owner) else None


def rewrite_archive(data: bytes, plan: RewritePlan) -> RewriteResult:
    """Split one bank's export into its shared and private archives (pure).

    Every document goes whole to one side (:func:`_document_private`), with
    its tags and its facts' tags put through the write envelope
    (:func:`hal0.memory.envelope.apply_envelope`) as the provider writes them
    today: the owner as ``agent:``, one ``kind:``, the project from the bank
    id or the facts' own ``project:`` tags through the registry, session ids
    and lifecycle words into metadata, ``scope:global`` or
    ``project:unresolved`` where no project applies. A private document is
    stamped ``visibility:private`` and given the id ``private:<owner>:<id>``;
    a shared one from a coding bank the namespaced id a compat client of that
    owner would have written (:func:`hal0.memory.document_namespace.stored_document_id`).

    An observation follows its source facts; one whose sources fall on both
    sides, or under two owners, or that is itself tagged ``visibility:private``
    while its sources are shared, is dropped and counted. Mental models of a
    shared bank go to the shared side, re-keyed for a per-repository bank
    (:func:`model_id_for`) and tagged with its project; a bank that held
    private facts gives them without what they generated. A
    ``private__<agent>`` bank's models go to the private side, confined to
    their owner (:func:`_private_model`). Knowledge pages follow shared
    models; a private bank's pages, and the models behind them, are dropped
    and counted. A coding bank's pages get new ids and go to their project's
    folder, stamped with the scope ``/api/memory/compat`` shows a page by
    (:func:`_place_pages`). Directives are not carried (a merge never imports them);
    they are listed in the report.

    Raises :class:`BankLeft` when a private document names more than one
    owner, when the shared archive it built holds anything private
    (:func:`shared_archive_problems`), when anything a source row marks
    private did not end up on the private side or counted as dropped
    (:func:`routing_problems`), or when a private fact has no owner
    (:func:`private_archive_problems`).
    """
    archive = _read_archive(data)
    uncarried = {k: archive[k] for k in ("attachments", "invalidated") if archive[k]}
    if uncarried:
        # The merge import (``import_documents``) restores neither attachment
        # bytes nor the curation archive of invalidated facts; only a
        # whole-bank restore does. Folding the bank would lose them silently.
        shown = ", ".join(
            f"{n} {'attachment(s)' if k == 'attachments' else 'invalidated fact(s)'}"
            for k, n in uncarried.items()
        )
        raise BankLeft(
            f"bank {plan.bank.bank} holds {shown}, which a merge import does not carry "
            "(hindsight-api 1.2.0 restores them only in a whole-bank restore). Folding it would "
            "lose them without a word, so it is not folded. Remove them from the old bank "
            "(or revert the invalidated facts) if they may go, then run the fold-in again",
            bank=plan.bank.bank,
        )
    report = _new_report()
    report["mental_model_history_not_carried"] = archive["model_history"]
    parts: dict[str, list[dict[str, Any]]] = {PART_SHARED: [], PART_PRIVATE: []}
    routes: dict[str, _DocRoute] = {}
    fact_tags: dict[str, list[list[str]]] = {}
    held_private = False
    provenance: dict[str, Any] = {
        part: {"documents": {}, "observations": [], "mental_models": {}, "pages": []}
        for part in _PARTS
    }
    provenance["dropped"] = {"observations": [], "mental_models": [], "pages": []}
    digests: dict[str, dict[str, str]] = {part: {} for part in _PARTS}

    for doc in archive["documents"]:
        route = _route_document(plan, doc)
        old_id = str(doc.get("id") or "")
        routes[old_id] = route
        if route.owners_conflict:
            report["mixed_owner_documents"].append(
                {"document": old_id, "owners": list(route.owners_conflict)}
            )
        held_private = held_private or route.part == PART_PRIVATE
        out = dict(doc)
        out["id"] = route.new_id
        provenance[route.part]["documents"][route.new_id] = old_id
        digests[route.part][route.new_id] = document_digest(doc)
        if route.new_id != old_id:
            report["document_ids_rewritten"] += 1
        if route.digested:
            report["document_ids_digested"] += 1
        doc_resolution = _fact_resolution(plan, doc, _caller_tags(doc.get("tags") or ()))
        out["tags"], _meta = _retag(plan, route, doc.get("tags") or (), doc_resolution, None)
        new_facts = []
        tag_rows: list[list[str]] = []
        for fact in doc.get("facts") or []:
            meta = dict(fact.get("metadata") or {})
            resolution = _fact_resolution(plan, doc, _caller_tags(fact.get("tags") or ()))
            tags, additions = _retag(plan, route, fact.get("tags") or (), resolution, meta)
            meta.update({k: v for k, v in additions.items() if k not in meta})
            if route.digested:
                meta.setdefault(CLIENT_DOCUMENT_ID_KEY, old_id)
            new_fact = dict(fact)
            new_fact["tags"] = tags
            new_fact["metadata"] = meta
            new_fact["observation_scopes"] = (
                _private_scopes(tags)
                if route.part == PART_PRIVATE
                else resolve_observation_scopes(None, tags)
            )
            new_facts.append(new_fact)
            tag_rows.append(tags)
            _tally_projects(report, tags)
            if _unreadable_shared(route, tags):
                report["unreadable_shared_facts"] += 1
        out["facts"] = new_facts
        fact_tags[old_id] = tag_rows
        parts[route.part].append(out)
        report["documents"][route.part] += 1
        report["facts"][route.part] += len(new_facts)
        if route.part == PART_PRIVATE:
            report["private_facts"] += len(new_facts)
        _tally_owner(report, route, len(new_facts))

    observations: dict[str, list[dict[str, Any]]] = {PART_SHARED: [], PART_PRIVATE: []}
    for index, obs in enumerate(archive["observations"]):
        if not isinstance(obs, dict):
            report["observations"]["dropped_unrouted"] += 1
            provenance["dropped"]["observations"].append(index)
            continue
        sources = obs.get("sources") or []
        located = [routes.get(str(s.get("document_id"))) for s in sources]
        if not sources or any(r is None for r in located):
            report["observations"]["dropped_unrouted"] += 1
            provenance["dropped"]["observations"].append(index)
            continue
        sides = {(r.part, r.owner if r.part == PART_PRIVATE else None) for r in located if r}
        if len(sides) != 1:
            report["observations"]["dropped_split"] += 1
            provenance["dropped"]["observations"].append(index)
            continue
        part, owner = next(iter(sides))
        if part == PART_SHARED and any(_is_private_tag(t) for t in obs.get("tags") or ()):
            # Its sources are shared but it says it is private. It cannot go
            # private (the engine imports an observation only with its source
            # facts, which are not there), and stripping the stamp would
            # publish it: dropped, and consolidation builds what it should be.
            report["observations"]["dropped_private_tag"] += 1
            provenance["dropped"]["observations"].append(index)
            continue
        new_sources = []
        projects: list[str] = []
        for source, route in zip(sources, located, strict=True):
            assert route is not None
            new_sources.append({**source, "document_id": route.new_id})
            fact_index = source.get("fact_index")
            rows = fact_tags.get(str(source.get("document_id")), [])
            if isinstance(fact_index, int) and 0 <= fact_index < len(rows):
                projects.extend(_project_tags(rows[fact_index]))
        kept = [
            str(t)
            for t in obs.get("tags") or ()
            if isinstance(t, str)
            and not _lower(t).startswith(("agent:", "visibility:", PROJECT_TAG_PREFIX))
        ]
        tags = list(dict.fromkeys([*kept, *projects]))
        if part == PART_PRIVATE:
            tags += [f"{AGENT_TAG_PREFIX}{owner}", VISIBILITY_PRIVATE_TAG]
            scopes = _private_scopes(tags)
        else:
            scopes = resolve_observation_scopes(None, tags)
        observations[part].append(
            {**obs, "sources": new_sources, "tags": tags, "observation_scopes": scopes}
        )
        provenance[part]["observations"].append(index)
        report["observations"][part] += 1

    models: list[dict[str, Any]] = []
    private_models: list[dict[str, Any]] = []
    renamed: dict[str, str] = {}
    prefix = _model_prefix(plan.bank)
    private_bank = plan.bank.cls == CLASS_PRIVATE
    bank_owner = (
        resolve_owner(
            plan.bank.agent, owners=plan.owners, known=plan.known_clients, keep_unknown=True
        )[0]
        if private_bank
        else None
    )
    page_models = {
        str(p.get("mental_model_id"))
        for p in archive["knowledge_pages"]
        if isinstance(p, dict) and p.get("mental_model_id")
    }
    held_private = held_private or bool(observations[PART_PRIVATE])
    for row in archive["mental_models"]:
        if not isinstance(row, dict):
            continue
        old = str(row.get("id"))
        if private_bank:
            assert bank_owner is not None
            private_model = None if old in page_models else _private_model(row, bank_owner)
            if private_model is None:
                report["mental_models"]["dropped_private"] += 1
                provenance["dropped"]["mental_models"].append(old)
                continue
            private_models.append(private_model)
            provenance[PART_PRIVATE]["mental_models"][str(private_model["id"])] = old
            report["mental_models"]["private"] += 1
            continue
        if any(_is_private_tag(t) for t in row.get("tags") or ()):
            report["mental_models"]["dropped_private"] += 1
            provenance["dropped"]["mental_models"].append(old)
            continue
        model = dict(row)
        if prefix is not None:
            model["id"] = model_id_for(prefix, old)
            report["mental_models"]["renamed"] += 1
        renamed[old] = str(model["id"])
        provenance[PART_SHARED]["mental_models"][str(model["id"])] = old
        tags = [str(t) for t in model.get("tags") or () if isinstance(t, str)]
        if plan.bank.project and f"{PROJECT_TAG_PREFIX}{plan.bank.project}" not in tags:
            tags.append(f"{PROJECT_TAG_PREFIX}{plan.bank.project}")
        model["tags"] = tags
        if held_private:
            _clear_generated(model)
            report["mental_models"]["content_cleared"] += 1
        models.append(model)
        report["mental_models"]["carried"] += 1

    pages, carried_pages, dropped_pages = _carried_pages(
        archive["knowledge_pages"], renamed, report
    )
    provenance["dropped"]["pages"] = dropped_pages
    if plan.bank.coding:
        refreshed = {
            renamed[str(m.get("id"))]: m.get("last_refreshed_at")
            for m in archive["mental_models"]
            if isinstance(m, dict) and str(m.get("id")) in renamed
        }
        placement = _place_pages(plan, pages, models, report, refreshed)
        pages = placement.rows
        provenance[PART_SHARED]["pages"] = placement.provenance
        provenance[PART_SHARED]["project_folders"] = dict(placement.folders)
    else:
        placement = None
        provenance[PART_SHARED]["pages"] = carried_pages
    report["directives_not_carried"] = [
        str(d.get("name") or d.get("id") or "(unnamed)")
        for d in archive["directives"]
        if isinstance(d, dict)
    ]

    bank_id = plan.bank.bank
    if report["mixed_owner_documents"]:
        owners = sorted({o for row in report["mixed_owner_documents"] for o in row["owners"]})
        raise BankLeft(
            f"bank {bank_id}: {len(report['mixed_owner_documents'])} private document(s) hold "
            f"facts of more than one owner ({', '.join(owners)}). A private document is filed "
            "under one owner, and filing it under either would give one agent's private fact to "
            "another, so the bank is not folded",
            bank=bank_id,
            details={"mixed_owner_documents": report["mixed_owner_documents"]},
        )
    shared = (
        _write_archive(bank_id, parts[PART_SHARED], observations[PART_SHARED], models, pages)
        if parts[PART_SHARED] or models
        else None
    )
    private = (
        _write_archive(bank_id, parts[PART_PRIVATE], observations[PART_PRIVATE], private_models, [])
        if parts[PART_PRIVATE] or private_models
        else None
    )
    problems = shared_archive_problems(shared) if shared is not None else []
    problems += model_null_problems([*models, *private_models])
    problems += routing_problems(
        data, shared, private, provenance, bank_cls=plan.bank.cls, tag_project=plan.bank.tag_project
    )
    problems += private_archive_problems(private) if private is not None else []
    if problems:
        raise BankLeft(
            f"bank {bank_id}: the rewritten archives failed their safety check "
            f"({len(problems)} problem(s)); nothing of it is sent",
            bank=bank_id,
            details={"problems": problems},
        )
    expected = {
        PART_SHARED: _counts_of(parts[PART_SHARED], observations[PART_SHARED], models),
        PART_PRIVATE: _counts_of(parts[PART_PRIVATE], observations[PART_PRIVATE], private_models),
    }
    page_model_ids = [
        str(p["mental_model_id"])
        for p in pages
        if p.get("kind") == "page" and p.get("mental_model_id")
    ]
    ids = {
        PART_SHARED: {
            "documents": [str(d["id"]) for d in parts[PART_SHARED]],
            "mental_models": [str(m["id"]) for m in models],
            "model_digests": {str(m["id"]): model_digest(m) for m in models},
            "model_spec_digests": {str(m["id"]): model_spec_digest(m) for m in models},
            "model_content_digests": {str(m["id"]): model_content_digest(m) for m in models},
            "pages": page_model_ids,
            "observations": sorted({observation_digest(o) for o in observations[PART_SHARED]}),
            **(
                placement.ids()
                if placement is not None
                else {"page_names": _platform_page_names(pages)}
            ),
        },
        PART_PRIVATE: {
            "documents": [str(d["id"]) for d in parts[PART_PRIVATE]],
            "mental_models": [str(m["id"]) for m in private_models],
            "model_digests": {str(m["id"]): model_digest(m) for m in private_models},
            "model_spec_digests": {str(m["id"]): model_spec_digest(m) for m in private_models},
            "model_content_digests": {
                str(m["id"]): model_content_digest(m) for m in private_models
            },
            "pages": [],
            "observations": sorted({observation_digest(o) for o in observations[PART_PRIVATE]}),
        },
    }
    return RewriteResult(shared, private, expected, report, ids, provenance, digests)


def _carried_pages(
    pages: list[Any], renamed: Mapping[str, str], report: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Pages whose model is carried (re-pointed at its new id), and the folders above them.

    Returns the rows, their ids, and the ids of the rows dropped.
    """
    rows = [p for p in pages if isinstance(p, dict)]
    by_id = {str(p.get("id")): p for p in rows}
    keep: set[str] = set()
    for page in rows:
        if page.get("kind") != "page" or str(page.get("mental_model_id")) not in renamed:
            continue
        node: Mapping[str, Any] | None = page
        while node is not None and str(node.get("id")) not in keep:
            keep.add(str(node.get("id")))
            parent = node.get("parent_id")
            node = by_id.get(str(parent)) if parent else None
    out = []
    carried: list[str] = []
    dropped: list[str] = []
    for page in rows:
        if str(page.get("id")) not in keep:
            report["knowledge_pages"]["dropped"] += 1
            dropped.append(str(page.get("id")))
            continue
        copy = dict(page)
        if copy.get("mental_model_id"):
            copy["mental_model_id"] = renamed[str(copy["mental_model_id"])]
        out.append(copy)
        carried.append(str(page.get("id")))
        report["knowledge_pages"]["carried"] += 1
    return out, carried, dropped


#: A ``[[page:<id>]]`` link, as the coding-agents plugin writes one into a
#: marker's retain context and a page synthesis repeats it in the page.
_PAGE_LINK = re.compile(r"\[\[page:([^\]\s]+)\]\]")

#: The model columns a page link can sit in. ``content`` is the page's
#: markdown; ``structured_content`` (schema v2, alembic ``d1e2f3a4b5c6``) holds
#: each block as a verbatim markdown fragment, the source of truth for a delta
#: refresh; ``reflect_response`` is the refresh's JSON payload (``based_on``,
#: ``trace``, ``structured_output``), whose text fields can quote the page.
_LINKED_MODEL_KEYS = ("content", "structured_content", "reflect_response")


def _relinked(value: Any, new_of: Mapping[str, str]) -> Any:
    """``value`` with every ``[[page:<old>]]`` link in it naming the new id (pure).

    Strings are rewritten wherever they sit in a JSON value. A JSON column
    the export gave as a string stays a string: it is decoded, rewritten and
    encoded again, and a string that is not JSON is rewritten as text.
    """

    def text(raw: str) -> str:
        return _PAGE_LINK.sub(lambda m: f"[[page:{new_of.get(m.group(1), m.group(1))}]]", raw)

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return text(node)
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        return node

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return text(value)
        if isinstance(decoded, (dict, list)):
            return json.dumps(walk(decoded))
        return text(value)
    return walk(value)


@dataclass
class _Placement:
    """Where a coding bank's carried knowledge-base nodes go (:func:`_place_pages`)."""

    #: The archive's ``knowledge_pages`` rows: the carried nodes under their
    #: new ids, and one placeholder folder row per project.
    rows: list[dict[str, Any]]
    #: New node id -> old node id, for :func:`routing_problems`.
    provenance: dict[str, str]
    #: Placeholder folder id -> project slug.
    folders: dict[str, str]
    #: Old node id -> ``{"id", "kind", "name", "project"}``; ``project`` is
    #: ``None`` for a node left outside every project folder.
    nodes: dict[str, dict[str, Any]]
    #: ``[slug, lower-cased name, new id]`` of every page directly in a project
    #: folder: only a page's name is unique in a folder (``uq_kp_folder_pagename``
    #: is ``WHERE kind = 'page'``), so folders are not listed.
    names: list[list[str]]

    def ids(self) -> dict[str, Any]:
        return {
            "page_nodes": self.nodes,
            "project_folders": self.folders,
            "page_names": self.names,
        }


def _page_owner(plan: RewritePlan, tags: Iterable[Any]) -> str:
    """Who a carried page of a coding bank is filed under: its own ``agent:`` tag or
    the bank's harness, resolved as the bank's documents are (operator decision 1)."""
    return resolve_owner(
        _agent_of(tags) or plan.bank.harness, owners=plan.owners, known=plan.known_clients
    )[0]


def _tally_page_owner(report: dict[str, Any], owner: str, name: str) -> None:
    """Count a page under its owner; list it when that owner is a pseudo-owner no client holds."""
    pages = report["knowledge_pages"]
    owners = pages.setdefault("owners", {})
    owners[owner] = owners.get(owner, 0) + 1
    if owner.startswith(PSEUDO_OWNER_PREFIX):
        pages.setdefault("pseudo_owned", []).append(name)


def _page_resolution(plan: RewritePlan, tags: Iterable[Any]) -> Any:
    """The project a carried page belongs to: the bank's, or for a bank whose
    project comes from each fact's tags (operator decision 2), the page's own."""
    if plan.bank.project is not None:
        return resolve_project(bank_segment=plan.bank.project, registry=plan.registry, coding=True)
    return resolve_project(tags=_caller_tags(tags), registry=plan.registry, coding=True)


def _place_pages(
    plan: RewritePlan,
    pages: list[dict[str, Any]],
    models: list[dict[str, Any]],
    report: dict[str, Any],
    refreshed: Mapping[str, Any] | None = None,
) -> _Placement:
    """Put a coding bank's carried pages in their project's folder, stamped as compat stamps them.

    ``/api/memory/compat`` shows the coding-agents plugin one folder per
    project, ``project:<slug>`` at the root of the platform bank's tree, and
    in it only pages whose scope hal0 wrote
    (:func:`hal0.memory.knowledge_pages.page_scope_held`). So, in the archive
    itself (the merge import keeps a node's ``parent_id`` when its parent is
    in the archive, and writes a model row verbatim):

    * every carried node gets the id :func:`~hal0.memory.knowledge_pages.folded_node_id`
      gives it, the same on every run and resend;
    * each subtree at the bank's root whose pages all belong to one project
      is hung under a placeholder folder for that project
      (:func:`~hal0.memory.knowledge_pages.placeholder_folder_id`), which the
      fold-in swaps for the target's own folder before it sends the archive
      (:meth:`FoldIn._rehome_folders`);
    * each page in it has its backing model's tags put through the write
      envelope with the owner the documents of the bank get (the harness, or
      the page's own ``agent:`` tag, through ``--owner`` and the known
      clients), and its trigger rebuilt from the refresh settings it chose
      plus hal0's scope (:func:`~hal0.memory.knowledge_pages.page_trigger`);
    * what the page's model generated (:data:`_GENERATED_MODEL_KEYS`) is
      cleared, as for a bank that held private facts, when the bank's project
      comes from tags (``Hal0::Agents``, ``pi-coder``, ``--map``) or the old
      trigger was not already scoped to the page's project
      (:func:`~hal0.memory.knowledge_pages.trigger_scope_held`): it was
      synthesised under that trigger and may rest on another project's
      memory, so the page is rebuilt under its new trigger. Both are counted
      (``content_cleared_tag_project``, ``content_cleared_unscoped``);
    * in a page that keeps its content, ``[[page:<id>]]`` links in
      ``content``, ``structured_content`` and ``reflect_response``
      (:data:`_LINKED_MODEL_KEYS`) name the new ids.

    The page's name and source query otherwise stay as they are; a model
    whose bank held private facts was already cleared. A subtree whose
    pages name no project, or several, is carried under its new ids at the
    root, as before, where compat does not show it.
    """
    from hal0.memory.knowledge_pages import (
        caller_trigger_settings,
        folded_node_id,
        page_tags,
        page_trigger,
        placeholder_folder_id,
        project_folder_name,
        trigger_scope_held,
    )

    bank = plan.bank.bank
    by_old = {str(p.get("id")): p for p in pages}
    new_of = {old: folded_node_id(bank, old, str(p.get("kind"))) for old, p in by_old.items()}
    model_by_id = {str(m.get("id")): m for m in models}

    def top_of(old: str) -> str:
        seen: set[str] = set()
        node = by_old[old]
        while True:
            parent = node.get("parent_id")
            if not parent or str(parent) not in by_old or str(parent) in seen:
                return str(node.get("id"))
            seen.add(str(parent))
            node = by_old[str(parent)]

    resolutions: dict[str, Any] = {}
    for old, page in by_old.items():
        if page.get("kind") == "page":
            model = model_by_id.get(str(page.get("mental_model_id"))) or {}
            resolutions[old] = _page_resolution(plan, model.get("tags") or ())
    slugs_of_top: dict[str, set[str | None]] = {}
    for old, resolution in resolutions.items():
        slugs_of_top.setdefault(top_of(old), set()).add(resolution.slug)
    project_of_top = {
        top: next(iter(slugs)) for top, slugs in slugs_of_top.items() if len(slugs) == 1
    }

    rows: list[dict[str, Any]] = []
    folders: dict[str, str] = {}
    nodes: dict[str, dict[str, Any]] = {}
    names: list[list[str]] = []
    placed = unplaced = 0
    for old, page in by_old.items():
        top = top_of(old)
        slug = project_of_top.get(top)
        row = dict(page)
        row["id"] = new_of[old]
        parent = page.get("parent_id")
        if old != top:
            row["parent_id"] = new_of[str(parent)]
        elif slug is not None:
            folder = placeholder_folder_id(slug)
            folders[folder] = slug
            row["parent_id"] = folder
            if page.get("kind") == "page":
                names.append([slug, str(page.get("name") or "").lower(), new_of[old]])
        else:
            row["parent_id"] = None
        rows.append(row)
        nodes[old] = {
            "id": new_of[old],
            "kind": str(page.get("kind")),
            "name": str(page.get("name") or ""),
            "project": slug,
            # The source model's, before any clearing: a tie-break when two
            # banks bring one page name to one folder (:meth:`FoldIn._settle_page_names`).
            "refreshed_at": (refreshed or {}).get(str(page.get("mental_model_id")))
            if page.get("kind") == "page"
            else None,
        }
        if page.get("kind") != "page":
            continue
        if slug is None:
            unplaced += 1
            continue
        placed += 1
        model = model_by_id.get(str(page.get("mental_model_id")))
        if model is None:
            continue
        tags = [str(t) for t in model.get("tags") or () if isinstance(t, str)]
        owner = _page_owner(plan, tags)
        _tally_page_owner(report, owner, str(page.get("name") or ""))
        labels, leaf = page_tags(_caller_tags(tags), agent=owner, resolution=resolutions[old])
        model["tags"] = labels
        old_trigger = _decoded(model.get("trigger"))
        chosen = caller_trigger_settings(old_trigger)
        model["trigger"] = page_trigger(chosen, leaf, slug)
        # What the page holds was synthesised under its old trigger. Unless
        # that trigger already read one project, the way compat scopes a
        # page, it may rest on another project's memory: the content is
        # cleared and the page is rebuilt under its new, scoped trigger. A
        # bank whose project comes from tags is always cleared, because its
        # pages were written without a project bound to them.
        if plan.bank.tag_project:
            _clear_generated(model)
            report["knowledge_pages"]["content_cleared_tag_project"] += 1
        elif not trigger_scope_held(old_trigger, slug):
            _clear_generated(model)
            report["knowledge_pages"]["content_cleared_unscoped"] += 1
        else:
            for key in _LINKED_MODEL_KEYS:
                if model.get(key):
                    model[key] = _relinked(model[key], new_of)
    for folder, slug in sorted(folders.items()):
        rows.insert(
            0,
            {
                "id": folder,
                "kind": "folder",
                "name": project_folder_name(slug),
                "parent_id": None,
                "mental_model_id": None,
                "sort_order": 0,
                "managed": False,
            },
        )
    report["knowledge_pages"]["placed"] = placed
    report["knowledge_pages"]["unplaced"] = unplaced
    return _Placement(
        rows=rows,
        provenance={new_of[old]: old for old in by_old},
        folders=folders,
        nodes=nodes,
        names=names,
    )


def _platform_page_names(pages: list[dict[str, Any]]) -> list[list[str]]:
    """``[slug, lower-cased name, id]`` of every page a platform bank carries directly
    in a root ``project:<slug>`` folder: what a coding bank's pages may not clash with."""
    from hal0.memory.knowledge_pages import KNOWLEDGE_FOLDER_PREFIX

    folders = {
        str(p.get("id")): str(p.get("name"))[len(KNOWLEDGE_FOLDER_PREFIX) :]
        for p in pages
        if p.get("kind") == "folder"
        and not p.get("parent_id")
        and str(p.get("name") or "").startswith(KNOWLEDGE_FOLDER_PREFIX)
    }
    return [
        [folders[str(p["parent_id"])], str(p.get("name") or "").lower(), str(p.get("id"))]
        for p in pages
        if p.get("kind") == "page" and p.get("parent_id") and str(p.get("parent_id")) in folders
    ]


#: ``mental_models.name`` is ``VARCHAR(256)`` in hindsight-api 1.2.0 (alembic
#: ``n9i0j1k2l3m4``, created as ``pinned_reflections``), and a page's model
#: carries the page's name; ``knowledge_pages.name`` is ``TEXT``.
PAGE_NAME_MAX = 256


def renamed_page_name(name: str, bank: str) -> str:
    """``<name> (<bank>)``, the name cut so the whole fits :data:`PAGE_NAME_MAX`."""
    suffix = f" ({bank})"
    if len(suffix) >= PAGE_NAME_MAX:
        return f"{name}{suffix}"[:PAGE_NAME_MAX]
    return name[: PAGE_NAME_MAX - len(suffix)].rstrip() + suffix


def _refreshed_epoch(value: Any) -> float:
    """A ``last_refreshed_at`` as seconds; one that is missing or unreadable is the oldest."""
    try:
        return datetime.fromisoformat(str(value)).timestamp() if value else float("-inf")
    except ValueError:
        return float("-inf")


def rehome_folders(data: bytes, folders: Mapping[str, str]) -> bytes:
    """``data`` with each placeholder folder id of ``folders`` replaced by its real id (pure).

    The folder row and every ``parent_id`` that names it are rewritten;
    nothing else in the archive changes.
    """
    archive = _read_archive(data)
    pages = []
    for page in archive["knowledge_pages"]:
        row = dict(page)
        if str(row.get("id")) in folders:
            row["id"] = folders[str(row["id"])]
        if row.get("parent_id") and str(row["parent_id"]) in folders:
            row["parent_id"] = folders[str(row["parent_id"])]
        pages.append(row)
    return _write_archive(
        str(archive["manifest"].get("source_bank_id") or ""),
        archive["documents"],
        archive["observations"],
        archive["mental_models"],
        pages,
    )


def record_page_map(target: str, rows: Mapping[str, Mapping[str, Any]]) -> None:
    """Merge ``rows`` (old node id -> where it is now) into ``target``'s part of the page map.

    The map (:data:`hal0.memory.knowledge_pages.PAGE_MAP_NAME`) sits beside
    the ledger in the state directory, 0644, so hal0-api reads it
    (:func:`hal0.memory.knowledge_pages.resolve_folded_node`). A row is
    replaced when a later run records the same old id again.
    """
    from hal0.memory.knowledge_pages import PAGE_MAP_VERSION, load_page_map, page_map_path

    if not rows:
        return
    path = page_map_path()
    try:
        data = load_page_map(path)
    except (OSError, ValueError) as exc:
        raise FoldInError(
            f"the page map {path} cannot be read ({type(exc).__name__}: {exc}); move it aside "
            "and run `hal0 memory migrate fold-in --resume`",
            resumable=True,
        ) from exc
    bank_rows = data["targets"].setdefault(target, {})
    bank_rows.update({str(k): dict(v) for k, v in rows.items()})
    data["version"] = PAGE_MAP_VERSION
    data["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    em.write_synced(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o644)


def _empty_archive() -> dict[str, Any]:
    return {
        "manifest": {},
        "documents": [],
        "observations": [],
        "mental_models": [],
        "knowledge_pages": [],
        "directives": [],
        "attachments": 0,
        "invalidated": 0,
        "model_history": 0,
    }


def routing_problems(
    source: bytes,
    shared: bytes | None,
    private: bytes | None,
    provenance: Mapping[str, Any],
    *,
    bank_cls: str,
    tag_project: bool = False,
) -> list[str]:
    """Everything a source row marks private that is not on the private side (empty when none).

    Judged on the source archive, independently of how the rewrite routed it
    and of the tags it wrote. A row is private when its bank is a
    ``private__<agent>`` bank, or:

    * a document, when its id starts ``private:`` or it or any of its facts
      is tagged ``visibility:private`` (a fact goes where its document goes);
    * an observation, when it is tagged so or any source fact is private;
    * a mental model, when it is tagged so;
    * a knowledge page, when its backing mental model is private.

    And when any source document is private, no shared mental model may keep
    what a refresh generated (:data:`_GENERATED_MODEL_KEYS`): it may rest on
    the private facts.

    Every such document must be in the private archive, under the new id the
    rewrite recorded for it (``provenance``), with all its facts, and in the
    shared archive under none; every such observation and mental model must
    be in the private archive or recorded as dropped, never in the shared
    one; every such page must be dropped. Every row of either archive must
    come from a source row, so nothing reaches the shared archive unaccounted
    for.

    A page in a project folder must have its model scoped as compat scopes a
    page, and may keep generated content only when its source model's
    trigger already was (:func:`~hal0.memory.knowledge_pages.trigger_scope_held`
    on the source row) and its bank's project does not come from tags
    (``tag_project``).
    """
    src = _read_archive(source)
    out = {
        PART_SHARED: _read_archive(shared) if shared is not None else _empty_archive(),
        PART_PRIVATE: _read_archive(private) if private is not None else _empty_archive(),
    }
    whole = bank_cls == CLASS_PRIVATE
    dropped = provenance.get("dropped") or {}
    problems: list[str] = []

    def doc_marked(doc: Mapping[str, Any]) -> bool:
        return (
            whole
            or str(doc.get("id") or "").startswith(_PRIVATE_ID_PREFIX)
            or any(_is_private_tag(t) for t in doc.get("tags") or ())
            or any(
                _is_private_tag(t)
                for f in doc.get("facts") or ()
                if isinstance(f, dict)
                for t in f.get("tags") or ()
            )
        )

    source_docs = {str(d.get("id") or ""): d for d in src["documents"]}
    marked = {i for i, d in source_docs.items() if doc_marked(d)}
    old_of: dict[str, dict[str, str]] = {}
    where: dict[str, tuple[str, dict[str, Any]]] = {}
    for part in _PARTS:
        mapping = dict((provenance.get(part) or {}).get("documents") or {})
        old_of[part] = mapping
        listed = {str(d.get("id") or ""): d for d in out[part]["documents"]}
        for new_id, row in listed.items():
            old = mapping.get(new_id)
            if old is None or old not in source_docs:
                problems.append(f"{part} document {new_id} comes from no source document")
                continue
            if old in where:
                problems.append(f"source document {old} is in the archives twice")
            where[old] = (part, row)
        for new_id in mapping:
            if new_id not in listed:
                problems.append(f"{part} document {new_id} is recorded but not in its archive")
    for old, doc in source_docs.items():
        if old not in where:
            problems.append(f"source document {old} is in neither archive")
            continue
        part, row = where[old]
        if old in marked and part != PART_PRIVATE:
            problems.append(
                f"document {old} is private but is in the shared archive as {row.get('id')}"
            )
        elif old in marked and len(row.get("facts") or []) != len(doc.get("facts") or []):
            problems.append(f"private document {old} lost facts on its way to the private archive")

    def source_doc_private(new_id: str, part: str) -> bool:
        old = old_of[part].get(new_id)
        return old is None or old in marked

    source_obs = src["observations"]

    def obs_marked(index: int) -> bool:
        obs = source_obs[index] if 0 <= index < len(source_obs) else None
        if not isinstance(obs, dict):
            return True
        return (
            whole
            or any(_is_private_tag(t) for t in obs.get("tags") or ())
            or any(str(s.get("document_id")) in marked for s in obs.get("sources") or ())
        )

    placed: dict[int, str] = {}
    for part in _PARTS:
        indices = list((provenance.get(part) or {}).get("observations") or [])
        rows = out[part]["observations"]
        if len(indices) != len(rows):
            problems.append(f"{part} observations do not match their record")
            continue
        for index, row in zip(indices, rows, strict=True):
            placed[int(index)] = part
            if part != PART_SHARED:
                continue
            if obs_marked(int(index)):
                problems.append(f"observation {index} is private but is in the shared archive")
            for src_ref in row.get("sources") or ():
                if source_doc_private(str(src_ref.get("document_id")), PART_SHARED):
                    problems.append(
                        f"shared observation {index} rests on a private or unknown document"
                    )
    dropped_obs = {int(i) for i in dropped.get("observations") or ()}
    for index in range(len(source_obs)):
        if index not in placed and index not in dropped_obs:
            problems.append(f"observation {index} is in neither archive and not counted as dropped")
        elif obs_marked(index) and placed.get(index, PART_PRIVATE) != PART_PRIVATE:
            problems.append(f"observation {index} is private but is not on the private side")

    source_models = {str(m.get("id")): m for m in src["mental_models"] if isinstance(m, dict)}

    def model_marked(old: str) -> bool:
        model = source_models.get(old)
        return whole or model is None or any(_is_private_tag(t) for t in model.get("tags") or ())

    model_part: dict[str, str] = {}
    for part in _PARTS:
        mapping = dict((provenance.get(part) or {}).get("mental_models") or {})
        listed = {str(m.get("id")) for m in out[part]["mental_models"] if isinstance(m, dict)}
        if listed != set(mapping):
            problems.append(f"{part} mental models do not match their record")
        for new_id, old in mapping.items():
            model_part[str(old)] = part
            if part == PART_SHARED and model_marked(str(old)):
                problems.append(
                    f"mental model {old} is private but is in the shared archive as {new_id}"
                )
    dropped_models = {str(m) for m in dropped.get("mental_models") or ()}
    for old in source_models:
        if model_marked(old) and model_part.get(old) != PART_PRIVATE and old not in dropped_models:
            problems.append(f"mental model {old} is private but is not on the private side")

    if marked:
        # The bank held private facts: what a refresh generated may rest on
        # them, so no shared model may carry it.
        for model in out[PART_SHARED]["mental_models"]:
            if not isinstance(model, dict):
                continue
            kept = _generated_kept(model)
            if kept:
                problems.append(
                    f"shared mental model {model.get('id')} keeps {', '.join(kept)} although its "
                    "bank held private facts"
                )

    marked_models = {old for old in source_models if model_marked(old)}
    source_pages = {str(p.get("id")): p for p in src["knowledge_pages"] if isinstance(p, dict)}

    def page_marked(page_id: str) -> bool:
        page = source_pages.get(page_id)
        return whole or page is None or str(page.get("mental_model_id")) in marked_models

    shared_record = provenance.get(PART_SHARED) or {}
    recorded = shared_record.get("pages") or ()
    # New id -> source id. A coding bank's nodes are carried under new ids
    # (:func:`_place_pages`); any other bank's keep theirs.
    carried = (
        {str(k): str(v) for k, v in recorded.items()}
        if isinstance(recorded, Mapping)
        else {str(p): str(p) for p in recorded}
    )
    placeholders = {str(k): str(v) for k, v in (shared_record.get("project_folders") or {}).items()}
    shared_nodes = {
        str(p.get("id")): p for p in out[PART_SHARED]["knowledge_pages"] if isinstance(p, dict)
    }
    shared_models = {
        str(m.get("id")): m for m in out[PART_SHARED]["mental_models"] if isinstance(m, dict)
    }
    model_source = {str(k): str(v) for k, v in (shared_record.get("mental_models") or {}).items()}
    for page_id, page in shared_nodes.items():
        if page_id in placeholders:
            if page.get("kind") != "folder" or page.get("mental_model_id"):
                problems.append(f"project folder {page_id} of the shared archive is not a folder")
            continue
        if page_id not in carried:
            problems.append(f"shared knowledge page {page_id} comes from no source page")
        elif page_marked(carried[page_id]):
            problems.append(f"knowledge page {page_id} is private but is in the shared archive")
    # A page in a project folder is one compat shows: its model must carry the
    # scope compat writes, which keeps private memory and other models out.
    from hal0.memory.knowledge_pages import trigger_scope_held

    for page_id, page in shared_nodes.items():
        if page.get("kind") != "page":
            continue
        node: Mapping[str, Any] | None = page
        seen: set[str] = set()
        slug = None
        while node is not None and str(node.get("id")) not in seen:
            seen.add(str(node.get("id")))
            parent = node.get("parent_id")
            if parent and str(parent) in placeholders:
                slug = placeholders[str(parent)]
                break
            node = shared_nodes.get(str(parent)) if parent else None
        if slug is None:
            continue
        model = shared_models.get(str(page.get("mental_model_id")))
        if model is None:
            problems.append(f"knowledge page {page_id} has no backing model in the shared archive")
        elif any(_is_private_tag(t) for t in model.get("tags") or ()):
            problems.append(f"knowledge page {page_id} in project {slug} is tagged private")
        elif not trigger_scope_held(_decoded(model.get("trigger")), slug):
            problems.append(
                f"knowledge page {page_id} in project {slug} lacks the project scope and the "
                "private guard compat shows a page by"
            )
        else:
            # What it holds was generated under the source model's trigger:
            # judged on the source row, it may keep that only when the bank's
            # project is not one from tags and that trigger already read this
            # project as compat scopes a page.
            old_model = source_models.get(model_source.get(str(model.get("id")), ""))
            scoped_before = (
                not tag_project
                and old_model is not None
                and trigger_scope_held(_decoded(old_model.get("trigger")), slug)
            )
            kept = [] if scoped_before else _generated_kept(model)
            if kept:
                problems.append(
                    f"knowledge page {page_id} in project {slug} keeps {', '.join(kept)} "
                    "generated under a trigger that was not scoped to that project"
                )
    if out[PART_PRIVATE]["knowledge_pages"]:
        problems.append("the private archive carries knowledge pages, which it never should")
    dropped_pages = {str(p) for p in dropped.get("pages") or ()}
    for page_id in source_pages:
        if page_marked(page_id) and page_id not in dropped_pages:
            problems.append(f"knowledge page {page_id} is private but is not counted as dropped")
    return problems


def shared_archive_problems(data: bytes) -> list[str]:
    """Everything private in an archive bound for the shared bank (empty when none).

    A ``private:`` document id, or ``visibility:private`` on a document, a
    fact, an observation or a mental model. Checked on every shared archive
    before it is imported, whatever built it.
    """
    archive = _read_archive(data)
    problems: list[str] = []
    for doc in archive["documents"]:
        doc_id = str(doc.get("id") or "")
        if doc_id.startswith(_PRIVATE_ID_PREFIX):
            problems.append(f"document {doc_id} has a private id")
        if any(_is_private_tag(t) for t in doc.get("tags") or ()):
            problems.append(f"document {doc_id} is tagged visibility:private")
        for index, fact in enumerate(doc.get("facts") or ()):
            if any(_is_private_tag(t) for t in fact.get("tags") or ()):
                problems.append(f"fact {index} of document {doc_id} is tagged visibility:private")
    for index, obs in enumerate(archive["observations"]):
        if isinstance(obs, dict) and any(_is_private_tag(t) for t in obs.get("tags") or ()):
            problems.append(f"observation {index} is tagged visibility:private")
    for model in archive["mental_models"]:
        if isinstance(model, dict) and any(_is_private_tag(t) for t in model.get("tags") or ()):
            problems.append(f"mental model {model.get('id')} is tagged visibility:private")
    return problems


def private_archive_problems(data: bytes) -> list[str]:
    """Every fact in a private archive without exactly one owner and the private stamp."""
    problems: list[str] = []
    for doc in _read_archive(data)["documents"]:
        doc_id = str(doc.get("id") or "")
        owner = _id_owner(doc_id)
        if owner is None:
            problems.append(f"private document {doc_id} has no private:<owner>: id")
        for index, fact in enumerate(doc.get("facts") or ()):
            tags = [str(t) for t in fact.get("tags") or ()]
            agents = [t for t in tags if t.startswith(AGENT_TAG_PREFIX)]
            if len(agents) != 1 or agents[0] != f"{AGENT_TAG_PREFIX}{owner}":
                problems.append(f"fact {index} of private document {doc_id} has no single owner")
            if VISIBILITY_PRIVATE_TAG not in tags:
                problems.append(f"fact {index} of private document {doc_id} is not stamped private")
    return problems


def analyse_listing(
    plan: RewritePlan,
    documents: list[Mapping[str, Any]],
    facts: list[Mapping[str, Any]],
    models: list[Mapping[str, Any]] | None = None,
    page_models: Iterable[str] = (),
) -> dict[str, Any]:
    """What :func:`rewrite_archive` would report for a bank, from its listings (pure).

    The dry run writes nothing, not even an export, so it reads the bank's
    document, fact and mental-model listings instead and routes them by the
    same rules (``page_models``: the ids of the models behind knowledge
    pages). Observations are counted by :meth:`FoldIn.bank_state`.
    """
    from hal0.memory.knowledge_pages import trigger_scope_held

    by_doc: dict[str, dict[str, Any]] = {}
    for row in documents:
        doc_id = str(row.get("id") or "")
        by_doc[doc_id] = {"id": doc_id, "tags": list(row.get("tags") or []), "facts": []}
    for fact in facts:
        doc_id = str(fact.get("document_id") or "")
        doc = by_doc.setdefault(doc_id, {"id": doc_id, "tags": [], "facts": []})
        doc["facts"].append({"tags": list(fact.get("tags") or [])})
    report = _new_report()
    held_private = False
    for doc in by_doc.values():
        route = _route_document(plan, doc)
        held_private = held_private or route.part == PART_PRIVATE
        report["documents"][route.part] += 1
        report["facts"][route.part] += len(doc["facts"])
        if route.new_id != doc["id"]:
            report["document_ids_rewritten"] += 1
        if route.digested:
            report["document_ids_digested"] += 1
        if route.part == PART_PRIVATE:
            report["private_facts"] += len(doc["facts"])
        if route.owners_conflict:
            report["mixed_owner_documents"].append(
                {"document": doc["id"], "owners": list(route.owners_conflict)}
            )
        _tally_owner(report, route, len(doc["facts"]))
        for fact in doc["facts"]:
            resolution = _fact_resolution(plan, doc, _caller_tags(fact["tags"]))
            tags, _ = _retag(plan, route, fact["tags"], resolution, None)
            _tally_projects(report, tags)
            if _unreadable_shared(route, tags):
                report["unreadable_shared_facts"] += 1
    backing = set(page_models)
    bank_owner = (
        resolve_owner(
            plan.bank.agent, owners=plan.owners, known=plan.known_clients, keep_unknown=True
        )[0]
        if plan.bank.cls == CLASS_PRIVATE
        else None
    )
    for model in models or ():
        old = str(model.get("id"))
        if bank_owner is not None:
            dropped = old in backing or _private_model(model, bank_owner) is None
            key = "dropped_private" if dropped else "private"
        elif any(_is_private_tag(t) for t in model.get("tags") or ()):
            key = "dropped_private"
        else:
            key = "carried"
            # The rewrite clears a carried model's generated content when the
            # bank holds anything private (an observation goes private only
            # with a private source document, so documents decide it).
            if held_private:
                report["mental_models"]["content_cleared"] += 1
        report["mental_models"][key] += 1
        if plan.bank.coding and old in backing and key == "carried":
            tags = [str(t) for t in model.get("tags") or () if isinstance(t, str)]
            _tally_page_owner(report, _page_owner(plan, tags), str(model.get("name") or old))
            # As :func:`_place_pages` clears a placed page. The listings do not
            # give the tree, so a page counts as placed when its own tags name
            # one project; the rewrite also leaves unplaced a subtree whose
            # pages name several.
            slug = _page_resolution(plan, tags).slug
            if slug is None:
                continue
            if plan.bank.tag_project:
                report["knowledge_pages"]["content_cleared_tag_project"] += 1
            elif not trigger_scope_held(_decoded(model.get("trigger")), slug):
                report["knowledge_pages"]["content_cleared_unscoped"] += 1
    return report


# ── the journal ──────────────────────────────────────────────────────────


@dataclass
class Part:
    """One of a bank's two archives and its import into its target."""

    target: str
    archive: str
    expected: dict[str, int]
    #: The target's counts just before the last send of this part.
    before: dict[str, int] | None = None
    #: What the last send carried: the whole part, or what of it was missing.
    sending: dict[str, int] | None = None
    #: True once an import of this part was sent in this run (saved before the request).
    sent: bool = False
    op_id: str | None = None
    done: bool = False


@dataclass
class BankEntry:
    """One bank's line in the journal."""

    bank: str
    cls: str
    harness: str | None = None
    agent: str | None = None
    project: str | None = None
    tag_project: bool = False
    #: The source bank as its archive was taken.
    counts: dict[str, int] = field(default_factory=dict)
    #: The source bank's own counts (``bank_state``) when it was exported: what
    #: a later run's re-check compares with.
    source_counts: dict[str, int] = field(default_factory=dict)
    archive: str | None = None
    step: str = STEP_PLANNED
    parts: dict[str, dict[str, Any]] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    last_document_at: str | None = None
    note: str | None = None
    #: The ``--owner`` map and the memory-scoped bridge clients this bank is
    #: rewritten with. Frozen when the bank is first planned and kept in the
    #: ledger once anything of it was sent, so every resume and later run
    #: gives its documents the same ids.
    owners: dict[str, str] | None = None
    known_clients: list[str] | None = None
    #: True for a bank an earlier run folded: exported and checked again, and only
    #: what its targets are missing is sent.
    refold: bool = False
    #: Per part, what earlier runs sent of this bank (the ``.ids.json`` files
    #: they wrote): documents present in the target under these ids, with the
    #: same source digest, are the fold-in's own copies, not a clash.
    prior_files: dict[str, list[str]] = field(default_factory=dict)
    #: Never set by the fold-in (it restores nothing); the shared journal protocol has it.
    restore_op: dict[str, Any] | None = None

    def bank_class(self) -> BankClass:
        return BankClass(
            self.bank,
            self.cls,
            harness=self.harness,
            agent=self.agent,
            project=self.project,
            tag_project=self.tag_project,
        )

    def part(self, name: str) -> Part | None:
        raw = self.parts.get(name)
        return Part(**raw) if raw else None

    def put(self, name: str, part: Part) -> None:
        self.parts[name] = asdict(part)

    def sent_parts(self) -> list[str]:
        """The parts sent to their target in this run."""
        return sorted(n for n, raw in self.parts.items() if raw.get("sent"))


@dataclass
class Journal:
    """The state of one run, on disk from the pause to its end."""

    run_id: str
    started_at: str
    pid: int
    phase: str
    archive_dir: str
    embedder: dict[str, str]
    targets: dict[str, str]
    owners: dict[str, str] = field(default_factory=dict)
    maps: dict[str, str] = field(default_factory=dict)
    #: The old layout's platform bank, kept in the record.
    platform_bank: str | None = None
    #: Foldable banks on the engine this run does not fold (not named with
    #: ``--bank``, never folded): while there are any, it does not cut over.
    pending: list[str] = field(default_factory=list)
    allow_pending: bool = False
    #: Banks the operator named with ``--skip``: an unclassified or excluded
    #: bank acknowledged as staying out of the two banks, so it does not hold
    #: the cutover back.
    skips: list[str] = field(default_factory=list)
    #: Banks on the engine that held the cutover back at the end of the run
    #: (:meth:`FoldIn._cutover_blockers`), each with what to do about it.
    blocking: list[dict[str, str]] = field(default_factory=list)
    #: The ``--skip`` banks on the engine at the end of the run, with their fact
    #: and document counts: what the cutover leaves unread (:meth:`FoldIn._cutover`).
    skipped: list[dict[str, Any]] = field(default_factory=list)
    private_port: int | None = None
    isolated: bool = False
    engine_stopped: bool = False
    #: True once any import was sent: from then on the run is carried on, never abandoned.
    sent: bool = False
    #: True once every bank of the run passed its pre-check
    #: (:meth:`FoldIn._precheck_all`); imports start only then.
    checked: bool = False
    #: Per target, ``"<slug>/<lower-cased name>" -> keeper``: who keeps the
    #: plain name of a page that several banks bring to one
    #: project folder (:meth:`FoldIn._settle_page_names`). Kept in the ledger.
    page_keepers: dict[str, dict[str, str]] = field(default_factory=dict)
    banks: list[BankEntry] = field(default_factory=list)
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

    def own_ids(self) -> set[str]:
        """The fold-in creates no bank of its own."""
        return set()


# ── the ledger: what earlier runs folded, across runs ─────────────────────
#
# A run that ends with any bank not folded does not cut over (ADR-0058
# decision 7a, "no cutover while any bank is left"): the box stays on the
# old layout and writes go on to the old banks. The journal is retired at
# the end of every run, so what a run folded, and what it sent of a bank it
# left, is kept here for the next one. One line per bank:
#
#   {"state": "folded" | "left", "run_id", "note", "counts",
#    "last_document_at", "owners", "known_clients",
#    "sent_files": {"shared": [<path>...], "private": [<path>...]}}
#
# ``sent_files`` name the ``.ids.json`` files in the archive directories of
# the runs that sent that part: every document id the part held with the
# digest of its source (:func:`document_digest`), its mental-model ids and
# its pages. They stay in the archive directories (mode 0600) and are not
# copied here. A left bank's ``note`` carries counts and bank names only:
# the ids behind it (changed, gone, clashing, mixed-owner, missing) go to the
# run's ``left-ids.json`` (:data:`LEFT_IDS_NAME`, 0600) and the note names
# that file. So the ledger itself holds no memory ids. ``owners`` and
# ``known_clients`` are set once anything of the bank was sent and never
# change after that.

LEDGER_NAME = "memory-fold-in-ledger.json"
LEDGER_VERSION = 1
LEDGER_FOLDED = "folded"
LEDGER_LEFT = "left"


def ledger_path() -> Path:
    """``/var/lib/hal0/state/memory-fold-in-ledger.json`` (sandboxed under ``HAL0_HOME``)."""
    return journal_path().parent / LEDGER_NAME


def load_ledger(path: Path | None = None) -> dict[str, Any]:
    """The ledger, or an empty one. Raises :class:`FoldInError` for one that cannot be read.

    Fail closed: without it the fold-in cannot tell the copies an earlier run
    made from a clash, so it refuses rather than guess.
    """
    target = path or ledger_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": LEDGER_VERSION, "banks": {}}
    except (OSError, ValueError) as exc:
        raise FoldInError(
            f"the fold-in ledger {target} cannot be read ({type(exc).__name__}: {exc})"
        ) from exc
    if not isinstance(data, dict) or data.get("version") != LEDGER_VERSION:
        raise FoldInError(f"the fold-in ledger {target} is not one this hal0 reads")
    if not isinstance(data.get("banks"), dict):
        data["banks"] = {}
    return data


def _save_ledger(data: dict[str, Any]) -> None:
    data["version"] = LEDGER_VERSION
    data["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    em.write_synced(ledger_path(), json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o644)


def _parse_journal(raw: str) -> Journal:
    """A journal this build carries on. Raises :class:`ValueError` otherwise."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("not a fold-in journal")
    if data.get("version") != JOURNAL_VERSION:
        raise ValueError(
            f"journal version {data.get('version')!r}, written by a different hal0; this one "
            f"carries on only version {JOURNAL_VERSION}"
        )
    if data.get("phase") not in _PHASES:
        raise ValueError(f"unknown phase {data.get('phase')!r}")
    for bank in data.get("banks") or []:
        step = bank.get("step") if isinstance(bank, dict) else None
        if step not in _STEPS:
            raise ValueError(f"a bank is at unknown step {step!r}")
    return Journal.from_dict(data)


def _finished(journal: Journal) -> bool:
    return journal.phase in _FINISHED_PHASES


def journal_path() -> Path:
    """``/var/lib/hal0/state/memory-fold-in.json`` (sandboxed under ``HAL0_HOME``)."""
    return em.journal_path(NAMES)


def lock_path() -> Path:
    return em.lock_path(NAMES)


def isolation_drop_in_path() -> Path:
    return em.isolation_drop_in_path(NAMES)


def isolated_port(path: Path | None = None) -> int | None:
    """The private port the fold-in's isolation drop-in pins the engine to."""
    return em.isolated_port(NAMES, path)


def locate_journal() -> dict[str, Any]:
    return em.locate_journal(NAMES, _parse_journal, _finished)


def load_journal() -> Journal | None:
    """The journal of an unfinished run, or ``None``. Raises :class:`FoldInError` if unusable."""
    found = locate_journal()
    if found["journal"] is None and found["error"]:
        raise FoldInError(f"the fold-in journal is not usable: {found['error']}")
    return found["journal"]


def lock_held() -> bool:
    return em.lock_held(NAMES)


def interrupted_fold_in() -> dict[str, Any] | None:
    """A short description of an unfinished fold-in, else ``None``. Never raises."""
    found = locate_journal()
    journal = found["journal"]
    if journal is None:
        return {"phase": "unreadable", "error": found["error"]} if found["error"] else None
    return {
        "phase": journal.phase,
        "run_id": journal.run_id,
        "started_at": journal.started_at,
        "error": journal.error,
        "archive_dir": journal.archive_dir,
        "source": found["source"],
        "isolated": journal.isolated,
    }


def blocking_reason() -> str | None:
    """Why nothing else may restart, rebuild or seed the memory engine now, or ``None``.

    For the embedder switch, ``hal0 update``, the engine upgrade and bank
    seeding. Never raises.
    """
    if lock_held():
        return "`hal0 memory migrate fold-in` is running on this box"
    left = interrupted_fold_in()
    if left is not None:
        return (
            f"a fold-in of the old memory banks (run {left.get('run_id')}, phase "
            f"{left.get('phase')}) did not finish; run `hal0 memory migrate fold-in --resume` first"
        )
    gate = write_gate.read_gate()
    if gate is not None and gate.get("reason") == write_gate.REASON_FOLD_IN:
        return (
            f"memory writes are paused by a fold-in (run {gate.get('run_id')}) that left no "
            "unfinished journal; `sudo hal0 memory migrate fold-in --clear-gate` lifts the pause "
            "once that run's journal shows it finished (see --status)"
        )
    if isolated_port() is not None:
        return (
            f"the memory engine is isolated by a fold-in ({isolation_drop_in_path()} is "
            "present); see `hal0 memory migrate fold-in --status`"
        )
    return None


# ── host seams (defaults) ────────────────────────────────────────────────


def _default_memory_config() -> Any:
    from hal0.config.loader import load_hal0_config

    return load_hal0_config().memory


def _default_known_clients() -> set[str]:
    """Bridge clients that hold the memory scope and are not revoked."""
    from hal0.bridge.store import BridgeStore
    from hal0.config.paths import bridge_db

    path = bridge_db()
    if not path.exists():
        return set()
    return {
        c.name
        for c in BridgeStore(path).list_clients()
        if c.revoked_at is None and "memory" in c.scopes
    }


def _default_client_configs() -> list[tuple[str, dict[str, Any]]]:
    """Every ``~/.hindsight/coding-agent.json`` a coding agent on this box may read."""
    import pwd

    from hal0.memory.compat_client import read_client_config

    homes: list[Path] = [Path.home()]
    for lookup in (lambda: pwd.getpwuid(0), lambda: pwd.getpwnam("hal0")):
        with contextlib.suppress(KeyError):
            homes.append(Path(lookup().pw_dir))
    out: list[tuple[str, dict[str, Any]]] = []
    for home in dict.fromkeys(homes):
        path = home / ".hindsight" / "coding-agent.json"
        if not path.exists():
            continue
        data = read_client_config(path, home)
        if data is not None:
            out.append((str(path), data))
    return out


def _default_restart_api() -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["systemctl", "restart", _API_SERVICE],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        detail = " ".join((proc.stderr or proc.stdout or "").split())[:160]
        return {"error": detail or f"systemctl exited {proc.returncode}"}
    return {}


def _port_of(url: Any) -> int | None:
    try:
        parts = urlsplit(str(url))
        if parts.port is not None:
            return parts.port
        return {"http": 80, "https": 443}.get(parts.scheme)
    except ValueError:
        return None


def direct_engine_clients(
    configs: list[tuple[str, dict[str, Any]]], engine_port: int | None
) -> list[dict[str, str]]:
    """Each coding-agent harness whose config still points at the engine's own port.

    ``harness`` is ``(default)`` for the file's flat ``apiUrl``. A harness
    section without an ``apiUrl`` reads the flat one, as the Hindsight
    plugin does.
    """
    if engine_port is None:
        return []
    found: list[dict[str, str]] = []
    for path, data in configs:
        flat = data.get("apiUrl")
        if isinstance(flat, str) and _port_of(flat) == engine_port:
            found.append({"path": path, "harness": "(default)", "url": flat})
        harnesses = data.get("harnesses")
        for name, section in (harnesses if isinstance(harnesses, dict) else {}).items():
            if not isinstance(section, dict):
                continue
            url = section.get("apiUrl") or flat
            if isinstance(url, str) and _port_of(url) == engine_port:
                found.append({"path": path, "harness": str(name), "url": url})
    return found


# ── the run ──────────────────────────────────────────────────────────────


#: The run's record of the ids behind each bank it left (0600, in its archive
#: directory): every changed, gone and clashing document or model id, the
#: mixed-owner documents, the routing problems. The ledger and the journal
#: carry only counts and this file's path.
LEFT_IDS_NAME = "left-ids.json"

#: What the cutover means for a ``--skip`` bank, in its result and its output.
SKIPPED_NOTE = (
    "named with --skip: they stay on the engine, untouched, and nothing reads them after the "
    "cutover. Their memory is not in hal0:shared or hal0:private"
)


def _archive_name(index: int, bank: str, part: str) -> str:
    return f"{index:03d}-{re.sub(r'[^A-Za-z0-9._-]', '_', bank)[:80]}.{part}.zip"


def _ids_name(archive_name: str) -> str:
    """The ``.ids.json`` beside a part's archive: its ids, pages and source digests."""
    return archive_name[: -len(".zip")] + ".ids.json"


def _read_nofollow(path: Path) -> bytes:
    """Read a file an earlier run wrote under the archive root, following no link."""
    root = em.archive_root().resolve()
    parent = path.parent.resolve()
    if path.is_symlink() or path.parent.is_symlink() or root not in parent.parents:
        raise OSError(f"{path} is not a file under {root}")
    dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
        with os.fdopen(fd, "rb") as fh:
            return fh.read()
    finally:
        os.close(dfd)


def engine_operations_message(work: Mapping[str, list[Mapping[str, Any]]]) -> str:
    """The ``engine_operations`` refusal: the engine work still live, and the two ways on.

    Either let it finish and run again, or cancel each named operation
    through hal0's engine API (admin): the exact request per operation.
    """
    listing = "; ".join(em.operations_text(b, list(ops)) for b, ops in work.items())
    cancels = ", ".join(
        f"DELETE /api/memory/banks/{quote(bank, safe=':')}/operations/"
        f"{quote(str(op.get('operation_id') or op.get('id')), safe='')}"
        for bank, ops in work.items()
        for op in ops
    )
    return (
        f"the engine's own work did not finish: {listing}. Either let it finish (`hal0 memory "
        "ops list` shows it) and run the fold-in again, or cancel it through hal0's engine API "
        f"as an admin ({cancels}) and run the fold-in again. The engine queues this work by "
        "itself (a mental-model refresh, consolidation of new facts)"
    )


def target_is_old_bank_message(platform: str, parts: Iterable[str]) -> str:
    """The ``target_is_old_bank`` refusal: what names the old bank, and the edit that fixes it.

    An upgraded box's hal0.toml can carry ``[memory] bank = "shared"`` (the
    old platform bank). The cutover would then make the old bank the new
    shared bank, so the run refuses until the config names the two-bank
    targets. The edit is safe to make before the run: while the layout is old
    the platform bank is read from the layout record, never from the
    two-bank default (:func:`hal0.memory.bank_layout.legacy_platform_bank`).
    """
    from hal0.config.paths import hal0_toml

    defaults = {
        PART_SHARED: ("bank", bank_layout.TWO_BANK_PLATFORM_BANK),
        PART_PRIVATE: ("private_bank", bank_layout.TWO_BANK_PRIVATE_BANK),
    }
    named = [defaults[part] for part in parts]
    edits = " and ".join(f'{key} = "{value}"' for key, value in named)
    keys = " and ".join(f"[memory] {key}" for key, _ in named)
    return (
        f"{keys} in {hal0_toml()} names {platform}, the old platform bank, so the cutover "
        f"would make the old bank a target. Edit {hal0_toml()} to set [memory] {edits}, then "
        "run the fold-in again. The edit is safe now: while the store is on the old layout, "
        f"memory keeps reading {platform} from the layout record, and the new name is used "
        "only from the cutover on"
    )


def _blocker(row: Mapping[str, Any], *, skips: Iterable[str]) -> dict[str, str] | None:
    """Why the bank in ``row`` holds the cutover back, and what to do; ``None`` when it does not.

    ``row`` is a plan row or a re-check row: ``bank``, ``class``, and
    ``action`` / ``folded`` for a foldable bank, ``counts`` for an excluded
    one. A foldable bank holds it back until it is folded (a plan row's
    ``action`` is ``recheck`` for a bank an earlier run folded, ``fold`` for
    one this run folds). An unclassified bank holds it back unless named
    with ``--skip``; an excluded one unless it is empty or named so.
    """
    bank, cls = str(row.get("bank")), str(row.get("class"))
    if cls in (CLASS_TARGET, CLASS_SCRATCH) or bank in set(skips):
        return None
    if cls in FOLDABLE:
        if row.get("folded") or row.get("action") in ("fold", "recheck"):
            return None
        return {
            "bank": bank,
            "reason": "not folded",
            "fix": f"fold it: `sudo {NAMES.command} --apply --bank {shlex.quote(bank)}`",
        }
    if cls == CLASS_EXCLUDED:
        counts = row.get("counts")
        if isinstance(counts, Mapping) and not any(int(v or 0) for v in counts.values()):
            return None
        return {
            "bank": bank,
            "reason": f"{row.get('reason') or 'excluded'}, and it is not empty",
            "fix": "fold it with `hal0 memory migrate platform` first, or name it with "
            f"--skip {shlex.quote(bank)} to leave it out of the two banks",
        }
    return {
        "bank": bank,
        "reason": "not a bank the fold-in knows",
        "fix": f"name its harness with --map {shlex.quote(bank)}=HARNESS to fold it, or name "
        f"it with --skip {shlex.quote(bank)} to leave it out of the two banks",
    }


class FoldIn(em.EngineMaintenance):
    """One run of the fold-in against one engine.

    The engine session, its isolation and identity checks, the queue waits,
    the export and the journal on disk are
    :class:`~hal0.memory.engine_maintenance.EngineMaintenance`'s, run under
    :data:`NAMES`; this class is the fold-in's plan and sequence.

    ``owners`` is the operator's ``--owner HARNESS=CLIENT`` map, ``maps``
    ``--map BANK=HARNESS``, ``only`` the banks of ``--bank``, ``skips`` the
    banks of ``--skip`` (an unclassified or excluded bank acknowledged as
    staying out of the two banks). Host seams
    besides the base class's: ``memory_config()`` (``[memory]`` from
    hal0.toml), ``layout()`` (:func:`hal0.memory.bank_layout.current_layout`),
    ``save_layout(layout, fold_in)``, ``known_clients()``, ``client_configs()``
    (every ``coding-agent.json``), ``restart_api()``, ``privileged()``,
    ``pick_port()`` and ``hs_dir``.
    """

    _error_type = FoldInError

    def __init__(
        self,
        client: Any,
        *,
        owners: Mapping[str, str] | None = None,
        maps: Mapping[str, str] | None = None,
        only: Iterable[str] = (),
        skips: Iterable[str] = (),
        forget_skips: Iterable[str] = (),
        allow_exposed_engine: bool = False,
        memory_config: Callable[[], Any] | None = None,
        layout: Callable[[], Any] | None = None,
        save_layout: Callable[[Any, dict[str, Any]], Any] | None = None,
        known_clients: Callable[[], set[str]] | None = None,
        client_configs: Callable[[], list[tuple[str, dict[str, Any]]]] | None = None,
        restart_api: Callable[[], dict[str, Any]] | None = None,
        privileged: Callable[[], bool] | None = None,
        pick_port: Callable[[], int] = em.free_loopback_port,
        hs_dir: Path | None = None,
        apply_dropin: Callable[[Embedder], dict[str, Any]] | None = None,
        evidence: Callable[[], Mapping[str, Any]] | None = None,
        isolate: Callable[[int, str], dict[str, Any]] | None = None,
        release: Callable[[], dict[str, Any]] | None = None,
        stop_unit: Callable[[], dict[str, Any]] | None = None,
        isolated_port: Callable[[], int | None] | None = None,
        client_for: Callable[[str], Any] | None = None,
        listener: Callable[[int], Mapping[str, Any]] | None = None,
        unit_text: Callable[[], str | None] | None = None,
        unit_invocation: Callable[[], str | None] | None = None,
        timeouts: em.Timeouts | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        progress: Callable[[str], None] | None = None,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        from hal0.memory.embedder_switch import EmbedderSwitch

        super().__init__(
            client,
            names=NAMES,
            apply_dropin=apply_dropin or EmbedderSwitch._default_apply_dropin,
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
        self._owners = {k.strip().lower(): v.strip() for k, v in (owners or {}).items()}
        #: ``--map`` and ``--skip`` as given. The preflight adds the ones earlier
        #: runs recorded in the ledger (:func:`load_ledger`) to ``_maps`` and
        #: ``_skips``, so a later run classifies every bank as they did.
        self._given_maps = dict(maps or {})
        self._given_skips = list(dict.fromkeys(skips))
        self._maps = dict(self._given_maps)
        self._only = list(dict.fromkeys(only))
        self._skips = list(self._given_skips)
        #: ``--forget-skip``: recorded skips this run drops from the ledger.
        self._forget_skips = list(dict.fromkeys(forget_skips))
        self._forgotten: list[str] = []
        self._allow_exposed_engine = allow_exposed_engine
        self._memory_config = memory_config or _default_memory_config
        self._layout = layout
        self._save_layout = save_layout or bank_layout.record_fold_in
        self._known_clients = known_clients or _default_known_clients
        self._client_configs = client_configs or _default_client_configs
        self._restart_api = restart_api or _default_restart_api
        self._privileged = privileged or (lambda: os.geteuid() == 0)
        self._pick_port = pick_port
        self._hs_dir = hs_dir

    def _refusal(self, message: str) -> FoldInError:
        return FoldInRefused(message)

    def _engine_home(self) -> Path:
        if self._hs_dir is None:
            from hal0.memory.engine_upgrade import hindsight_dir

            self._hs_dir = hindsight_dir()
        return self._hs_dir

    def _current_layout(self, memory_cfg: Any) -> Any:
        if self._layout is not None:
            return self._layout()
        return bank_layout.current_layout(memory_cfg)

    def _current_known(self) -> list[str]:
        try:
            return sorted(self._known_clients())
        except Exception as exc:  # the store cannot be read: owners fall back, never fail
            log.warning("hal0.memory.fold_in_clients_unread", error=str(exc))
            return []

    def _rewrite_plan(
        self,
        entry: BankEntry | BankClass,
        journal: Journal | None,
        *,
        frozen: Mapping[str, Any] | None = None,
    ) -> RewritePlan:
        """The rewrite plan of one bank.

        A journal entry carries the owner map and known clients it was planned
        with, and those are used, never the current ones, so a resume or a
        later run gives every document the id the first one did. ``frozen``
        is the ledger's line for a bank not in a journal (the dry run).
        """
        from hal0.memory.projects import registry_from_config

        memory_cfg = self._memory_config()
        fixed = entry if isinstance(entry, BankEntry) else None
        explicit: Mapping[str, str] | None = fixed.owners if fixed is not None else None
        known_list: list[str] | None = fixed.known_clients if fixed is not None else None
        if explicit is None and frozen:
            explicit, known_list = frozen.get("owners"), frozen.get("known_clients")
        if explicit is None:
            explicit = journal.owners if journal else self._owners
        owners = {**DEFAULT_OWNERS, **explicit}
        compat = getattr(memory_cfg, "compat", None)
        prefixes = tuple(getattr(compat, "shared_document_prefixes", None) or ())
        known = frozenset(known_list if known_list is not None else self._current_known())
        bank = entry.bank_class() if isinstance(entry, BankEntry) else entry
        return RewritePlan(
            bank=bank,
            owners=owners,
            known_clients=known,
            registry=registry_from_config(getattr(memory_cfg, "projects", None)),
            shared_prefixes=prefixes,
        )

    # ── preflight ───────────────────────────────────────────────────────

    async def preflight(self, *, apply: bool = False) -> dict[str, Any]:
        """Everything a run needs to be true, checked without changing anything.

        Returns the plan: the layout, the targets, every bank on the engine
        with its class, counts and (for a dry run) what folding it would do,
        and ``refusals``. Nothing is written, with or without ``apply``.
        """
        from hal0.memory import embedder_switch

        refusals: list[dict[str, str]] = []
        warnings: list[str] = []

        def refuse(code: str, message: str) -> None:
            refusals.append({"code": code, "message": message})

        plan: dict[str, Any] = {
            "layout": None,
            "targets": None,
            "engine": {"reachable": False, "version": None},
            "embedder": None,
            "banks": [],
            "owners": {**DEFAULT_OWNERS, **self._owners},
            "maps": dict(self._maps),
            "only": list(self._only),
            "skips": list(self._skips),
            "totals": dict.fromkeys(em.COUNT_KEYS, 0),
            "disk": None,
            "estimate_s": None,
            "direct_clients": [],
            "pending": [],
            "blocking": [],
            "cuts_over": False,
            "refusals": refusals,
            "warnings": warnings,
        }
        from hal0.bridge.keys import is_reserved_name, valid_name

        for harness, owner in self._owners.items():
            if not valid_name(owner) or is_reserved_name(owner):
                refuse(
                    "invalid_owner",
                    f"--owner {harness}={owner}: {owner!r} is not a name a bridge client can "
                    "have, so no client could read what is filed under it",
                )

        # Interlocks: the switch, an earlier fold-in, the updater, the engine.
        switch = embedder_switch.blocking_reason()
        if switch is not None:
            refuse("embedder_switch", f"{switch}; the fold-in and the switch never overlap")
        left = interrupted_fold_in()
        if left is not None:
            refuse(
                "interrupted_fold_in",
                f"an earlier fold-in (run {left.get('run_id')}, phase {left.get('phase')}) did "
                "not finish; see `hal0 memory migrate fold-in --status`, then run it with --resume",
            )
        gate = write_gate.read_gate()
        if gate is not None and switch is None and left is None:
            stuck = gate.get("reason") == write_gate.REASON_FOLD_IN
            refuse(
                "gate_set",
                f"memory writes are already paused (reason {gate.get('reason')}, run "
                f"{gate.get('run_id')})"
                + (
                    ", by a fold-in that finished without lifting it: run `sudo "
                    f"{NAMES.command} --clear-gate`"
                    if stuck
                    else ""
                ),
            )
        if not apply and lock_held():
            refuse("locked", "another `hal0 memory migrate fold-in` is running on this box")
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
                f"{hs_dir}/.pg0.restore-failed marks the engine's data directory as untrusted",
            )
        job = em.update_in_progress()
        if job is not None:
            refuse("update_in_progress", f"`hal0 update` job {job} is queued or running")
        if apply and not self._privileged():
            refuse(
                "not_privileged",
                "run this as root (sudo): it moves the memory engine to a private port for the "
                "duration, which means writing a drop-in for the engine's unit and restarting "
                "the engine and hal0-api",
            )
        private = self._isolated_port()
        if private is not None:
            refuse(
                "engine_private",
                f"the memory engine is already isolated on a private port "
                f"({isolation_drop_in_path()} exists); see `hal0 memory migrate fold-in --status`",
            )
            self._use_port(private or None)
        unit_text = self._unit_text()
        refusals.extend(em.unit_refusals(NAMES, unit_text, self._normal_port()))
        self._check_exposed_engine(plan, unit_text, apply=apply, refuse=refuse, warnings=warnings)

        # The layout and the targets.
        memory_cfg = self._memory_config()
        try:
            layout = self._current_layout(memory_cfg)
        except bank_layout.LayoutUndecided as exc:
            refuse("layout_undecided", f"the store's bank layout is not settled: {exc}")
            return plan
        plan["layout"] = {
            "layout": layout.layout,
            "platform_bank": layout.legacy_platform_bank,
            "unified": layout.legacy_unified,
        }
        two = bank_layout.BankLayout(layout=bank_layout.LAYOUT_TWO_BANK).provider_kwargs(memory_cfg)
        targets = {PART_SHARED: str(two["bank"]), PART_PRIVATE: str(two["private_bank"])}
        plan["targets"] = targets
        if targets[PART_SHARED] == targets[PART_PRIVATE]:
            refuse(
                "targets_equal",
                f"[memory] bank and private_bank both name {targets[PART_SHARED]}",
            )
        if layout.two_bank:
            refuse(
                "already_two_bank",
                "the store is already on the two banks: a fold-in cuts over only once every old "
                "bank is folded, so there is nothing left to fold",
            )
        try:
            ledger = load_ledger()
        except FoldInError as exc:
            refuse("ledger_unreadable", exc.message)
            return plan
        earlier: dict[str, dict[str, Any]] = dict(ledger.get("banks") or {})
        self._remembered_choices(ledger, earlier, refuse)
        plan["maps"], plan["skips"] = dict(self._maps), list(self._skips)
        plan["forgotten_skips"] = list(self._forgotten)
        for bank in self._forget_skips:
            if bank not in self._forgotten:
                warnings.append(f"--forget-skip {bank}: no run recorded a --skip for it")
        plan["earlier"] = {
            bank: {k: row.get(k) for k in ("state", "run_id", "note")}
            for bank, row in earlier.items()
        }
        platform = (
            layout.legacy_platform_bank
            or ledger.get("platform_bank")
            or bank_layout.LEGACY_PLATFORM_BANK
        )
        plan["platform_bank"] = platform
        clashing = [part for part, bank in targets.items() if bank == platform]
        if clashing:
            refuse("target_is_old_bank", target_is_old_bank_message(platform, clashing))
        # A target that is the old platform bank is the platform bank, still
        # to fold: the refusal above says how to name the real target.
        bank_targets = [bank for bank in targets.values() if bank != platform]

        # The engine.
        live = em.evidence_pair(self._evidence().get("live"))
        plan["embedder"] = live._asdict() if live is not None else None
        if live is None:
            refuse(
                "engine_environment",
                "the running engine's environment cannot be read (the engine is not running, "
                "or this account may not read it; use sudo). Every import embeds with what the "
                "engine runs, so the fold-in confirms it after each restart",
            )
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
        }
        if not features.get("document_export_api") or not features.get("document_import_api"):
            refuse(
                "transfer_disabled",
                "the engine's export or import API is off (features on /version)",
            )

        # Direct clients: Pi must not write to the engine's own port (#3613).
        try:
            configs = self._client_configs()
        except Exception as exc:
            configs = []
            warnings.append(f"the coding agents' client configs could not be read ({exc})")
        direct = direct_engine_clients(configs, self._normal_port())
        plan["direct_clients"] = direct
        for row in direct:
            message = (
                f"{row['path']}: harness {row['harness']} points at the memory engine's own port "
                f"({row['url']}), so its writes would go on landing in the old banks after the "
                "fold-in"
            )
            if row["harness"] == "pi":
                refuse(
                    "pi_direct_engine",
                    message + ". Run `sudo hal0 update component pi` to move Pi onto "
                    "/api/memory/compat first",
                )
            else:
                warnings.append(message + ". Move it with `hal0 memory agents install <harness>`")

        # The banks.
        engine_work: dict[str, list[dict[str, Any]]] = {}
        deadline = self._clock() + self.t.engine_ops_s
        try:
            ids = await self._bank_ids()
            for bank in ids:
                cls = classify_bank(
                    bank, platform_bank=platform, targets=bank_targets, maps=self._maps
                )
                row: dict[str, Any] = {
                    "bank": bank,
                    "class": cls.cls,
                    "harness": cls.harness,
                    "agent": cls.agent,
                    "project": cls.project,
                    "reason": cls.reason,
                    "action": "fold" if cls.foldable else "none",
                }
                before = earlier.get(bank) if cls.foldable else None
                if before is not None:
                    row["earlier"] = {k: before.get(k) for k in ("state", "run_id", "note")}
                if before is not None and before.get("state") == LEDGER_FOLDED:
                    # Folded by an earlier run: re-checked by every run, named or
                    # not, because the cutover needs every bank folded as it is now.
                    row["action"] = "recheck"
                    row["reason"] = (
                        f"folded by run {before.get('run_id')}; exported and checked again, "
                        "and only what the targets are missing is sent"
                    )
                elif self._only and bank not in self._only:
                    row["action"] = "none"
                    row["reason"] = "not named with --bank"
                    if cls.foldable:
                        plan["pending"].append(bank)
                if bank in self._skips and not cls.foldable:
                    row["action"] = "skip"
                    row["reason"] = (
                        "named with --skip: it stays on the engine, outside the two banks, and "
                        "nothing reads it after the cutover"
                    )
                if before is not None and row["action"] in ("fold", "recheck"):
                    frozen = before.get("owners")
                    if (
                        isinstance(frozen, dict)
                        and self._owners
                        and dict(self._owners) != {k.lower(): v for k, v in frozen.items()}
                    ):
                        refuse(
                            "owners_frozen",
                            f"bank {bank} was planned by run {before.get('run_id')} with --owner "
                            + (", ".join(f"{k}={v}" for k, v in sorted(frozen.items())) or "(none)")
                            + ", and what it sent is filed under those owners. Run again with "
                            "the same --owner flags, or with none to reuse them",
                        )
                if row["action"] in ("fold", "recheck") or cls.cls in (
                    CLASS_UNCLASSIFIED,
                    CLASS_EXCLUDED,
                ):
                    state = await self.bank_state(bank)
                    if cls.foldable and (state["pending"] or state["processing"]):
                        if apply:
                            state, own = await self._await_engine_work(
                                bank, state, deadline=deadline
                            )
                        else:
                            own = await self._engine_work(bank, state)
                        if own:
                            engine_work[bank] = own
                    row.update({k: state[k] for k in ("counts", "pending", "processing")})
                    row["last_document_at"] = state["fingerprint"].get("document")
                    if row["action"] == "fold":
                        for key in em.COUNT_KEYS:
                            plan["totals"][key] += state["counts"][key]
                        if not apply:
                            row["fold"] = await self._analyse(cls, earlier.get(bank))
                plan["banks"].append(row)
        except FoldInError as exc:
            refuse("engine_error", exc.message)
            return plan
        for missing in [b for b in self._only if b not in ids]:
            refuse("no_such_bank", f"--bank {missing}: the engine has no such bank")
        for missing in [b for b in self._given_skips if b not in ids]:
            refuse("no_such_bank", f"--skip {missing}: the engine has no such bank")
        gone_banks = sorted(
            bank
            for bank, row in earlier.items()
            if bank not in ids and isinstance(row, dict) and row.get("sent_files")
        )
        if gone_banks:
            plan["gone_banks"] = gone_banks
            warnings.append(
                f"{len(gone_banks)} bank(s) an earlier run sent memory from are no longer on the "
                f"engine ({', '.join(gone_banks[:5])}): the cutover waits while the targets "
                "still hold any copy of them, because nothing would tell those copies apart from "
                "live memory. Their ids are in the .ids.json files the ledger names"
            )
        # A recorded skip for a bank the engine no longer holds is dropped.
        self._skips = [b for b in self._skips if b in ids]
        plan["skips"] = list(self._skips)
        for row in plan["banks"]:
            if row["bank"] in self._skips and row["class"] not in (
                CLASS_UNCLASSIFIED,
                CLASS_EXCLUDED,
            ):
                refuse(
                    "skip_not_allowed",
                    f"--skip {row['bank']}: a {row['class']} bank is folded, never skipped; "
                    "--skip is for a bank the fold-in does not know or does not move",
                )
            # A foldable bank this run does not fold is in ``pending``.
            blocker = None if row["class"] in FOLDABLE else _blocker(row, skips=self._skips)
            if blocker is not None:
                plan["blocking"].append(blocker)
        for row in plan["banks"]:
            named = row["bank"] in self._only
            if (
                row["class"] == CLASS_UNCLASSIFIED
                and row["bank"] not in self._skips
                and (named or not self._only)
            ):
                refuse(
                    "unclassified_bank",
                    f"bank {row['bank']} ({row.get('counts', {}).get('facts', 0)} facts) is "
                    f"{row['reason']}, or with --skip BANK to leave it out of the two banks. "
                    "Nothing is folded while a bank is left unaccounted for",
                )
            elif named and row["action"] != "fold":
                refuse(
                    "not_foldable",
                    f"--bank {row['bank']}: {row['reason'] or 'not a bank the fold-in moves'}",
                )
        busy = [
            b
            for b in plan["banks"]
            if b["action"] in ("fold", "recheck")
            and (b.get("pending") or b.get("processing"))
            and b["bank"] not in engine_work
        ]
        if busy:
            listing = ", ".join(
                f"{b['bank']} ({b['pending']} pending, {b['processing']} processing)" for b in busy
            )
            refuse(
                "pending_operations",
                f"the engine still has queued or running operations: {listing}. A queued "
                "retain is not in an export. Wait for them (`hal0 memory ops list`), then run "
                "the fold-in again",
            )
        if engine_work:
            listing = "; ".join(em.operations_text(b, ops) for b, ops in engine_work.items())
            if apply:
                refuse("engine_operations", engine_operations_message(engine_work))
            else:
                warnings.append(
                    f"the engine is running its own work: {listing}. --apply waits up to "
                    f"{self.t.engine_ops_s:g}s for it"
                )
        if not any(b["action"] in ("fold", "recheck") for b in plan["banks"]) and not refusals:
            refuse("nothing_to_fold", "there is no old bank to fold in")
        plan["cuts_over"] = not plan["pending"] and not plan["blocking"]
        if plan["blocking"]:
            warnings.append(
                "the cutover waits for "
                + "; ".join(f"{b['bank']}: {b['reason']} ({b['fix']})" for b in plan["blocking"])
            )
        if plan["pending"]:
            warnings.append(
                f"{len(plan['pending'])} old bank(s) are not part of this run "
                f"({', '.join(plan['pending'][:5])}): it folds what it can and then returns the "
                "box to the old layout, because the cutover waits until every bank is folded"
            )

        plan["disk"] = self._disk_plan(hs_dir, refuse, warnings)
        embedded = plan["totals"]["facts"] + plan["totals"]["observations"]
        plan["estimate_s"] = round(embedded / em.RESTORE_FACTS_PER_S + 3 * 25)
        if plan["totals"]["directives"]:
            warnings.append(
                f"{plan['totals']['directives']} directive(s) on the old banks are not carried: "
                "a merge import never takes directives (ADR-0058 decision 7a). They stay on the "
                "old banks; recreate the ones you want on the two banks"
            )
        return plan

    def _check_exposed_engine(
        self,
        plan: dict[str, Any],
        unit_text: str | None,
        *,
        apply: bool,
        refuse: Callable[[str, str], None],
        warnings: list[str],
    ) -> None:
        """Warn, and refuse ``--apply`` unless allowed, when the engine listens beyond loopback.

        :func:`direct_engine_clients` sees only the ``coding-agent.json``
        files on this box. A client on another machine that writes to the
        engine's port directly is invisible to it, and after the cutover it
        goes on writing the old banks, which nothing reads.
        """
        host, where = em.listen_host(NAMES, unit_text) if unit_text is not None else (None, "")
        exposed = host is not None and not em.is_loopback_host(host)
        plan["engine_listen"] = {"host": host, "source": where, "exposed": exposed}
        if not exposed:
            return
        port = self._normal_port() or 9177
        message = (
            f"the memory engine listens on {host} ({where}), not on loopback only. Remote "
            "clients that write to the engine port directly (for example a coding-agents "
            f"plugin on another machine pointed at http://<box>:{port}) keep writing the old "
            "banks after the cutover, and nothing reads them. Repoint them with `hal0 memory "
            "agents install <harness> --bridge-key-file <file>` first"
        )
        if self._allow_exposed_engine:
            warnings.append(message + "; --allow-exposed-engine is given, so the run goes ahead")
        elif apply:
            refuse(
                "engine_exposed",
                message + ", then run again with --allow-exposed-engine",
            )
        else:
            warnings.append(
                message + ". --apply refuses this unless --allow-exposed-engine is given"
            )

    def _remembered_choices(
        self,
        ledger: Mapping[str, Any],
        earlier: Mapping[str, Mapping[str, Any]],
        refuse: Callable[[str, str], None],
    ) -> None:
        """Add the ``--map`` and ``--skip`` choices earlier runs recorded to this run's.

        A bank folded with ``--map`` would otherwise come back unclassified
        on a later run that does not repeat the flag, and hold the cutover
        back for good. A ``--map`` given now wins over a recorded one, except
        for a bank an earlier run folded or sent part of: its documents are
        filed under the owner the classification then gave them, so any
        ``--map`` that differs from what was recorded for it is refused,
        including a first ``--map`` for a bank folded without one. ``--skip``
        is refused for such a bank too: it may have taken writes since, and
        needs a re-check, not a skip. A recorded skip gives way to a ``--map``
        for the same bank, and ``--forget-skip`` drops one.
        """
        recorded = ledger.get("maps")
        saved_maps = (
            {str(k): str(v) for k, v in recorded.items()} if isinstance(recorded, dict) else {}
        )
        saved_skips = [str(b) for b in ledger.get("skips") or [] if isinstance(b, str)]

        def committed(bank: str) -> bool:
            row = earlier.get(bank) or {}
            return row.get("state") == LEDGER_FOLDED or bool(row.get("sent_files"))

        for bank, harness in self._given_maps.items():
            if not committed(bank) or saved_maps.get(bank) == harness:
                continue
            if bank in saved_maps:
                refuse(
                    "map_frozen",
                    f"--map {bank}={harness}: an earlier run folded {bank} (or sent part of it) "
                    f"with --map {bank}={saved_maps[bank]}, and what it sent is filed under that "
                    "harness's owner. Run without --map for it to reuse the recorded one, or "
                    "with the same value",
                )
            else:
                refuse(
                    "map_frozen",
                    f"--map {bank}={harness}: an earlier run folded {bank} (or sent part of it) "
                    "without --map, and what it sent is filed under the owners its own class "
                    "gave it. A --map now would file the rest under another harness's owner. "
                    f"Run without --map for {bank}",
                )
        for bank in self._forget_skips:
            if bank in self._given_skips:
                refuse(
                    "forget_skip_conflict",
                    f"--forget-skip {bank} and --skip {bank} were both given; give one",
                )
        for bank in self._given_skips:
            if committed(bank):
                refuse(
                    "skip_folded",
                    f"--skip {bank}: an earlier run folded {bank} (or sent part of it). It may "
                    f"have taken writes since, so it needs a re-check, not a skip: run without "
                    f"--skip {bank}",
                )
        self._maps = {**saved_maps, **self._given_maps}
        self._forgotten = [b for b in self._forget_skips if b in saved_skips]
        self._skips = list(
            dict.fromkeys(
                [
                    *self._given_skips,
                    *(
                        b
                        for b in saved_skips
                        if b not in self._maps and b not in self._forget_skips
                    ),
                ]
            )
        )

    async def _analyse(self, cls: BankClass, earlier: Mapping[str, Any] | None) -> dict[str, Any]:
        """The dry run's look at one bank: its listings, routed as the rewrite would."""
        base = em.bank_path(cls.bank)
        documents = await self._list_all(
            f"{base}/documents",
            key="items",
            what="listing documents",
            bank=cls.bank,
            page_size=_CONTENT_PAGE,
        )
        facts: list[Any] = []
        for kind in ("world", "experience"):
            facts += await self._list_all(
                f"{base}/memories/list",
                {"type": kind},
                key="items",
                what=f"listing {kind} facts",
                bank=cls.bank,
                page_size=_CONTENT_PAGE,
            )
        models = await self._list_all(
            f"{base}/mental-models",
            {"detail": "content"},
            key="items",
            what="reading mental models",
            bank=cls.bank,
        )
        return analyse_listing(
            self._rewrite_plan(cls, None, frozen=earlier),
            [d for d in documents if isinstance(d, dict)],
            [f for f in facts if isinstance(f, dict)],
            [m for m in models if isinstance(m, dict)],
            await self._page_model_ids(cls.bank),
        )

    @staticmethod
    def _disk_plan(
        hs_dir: Path, refuse: Callable[[str, str], None], warnings: list[str]
    ) -> dict[str, Any]:
        store_bytes = em.du_bytes(hs_dir / em.PG_DIR)
        store_at = em.nearest_existing(hs_dir / em.PG_DIR)
        archive_at = em.nearest_existing(em.archive_root())
        store_free = shutil.disk_usage(store_at).free
        archive_free = shutil.disk_usage(archive_at).free
        same = em.device(store_at) == em.device(archive_at)
        store_need = int(store_bytes * STORE_FREE_FACTOR)
        archive_need = int(store_bytes * ARCHIVE_FREE_FACTOR)
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
                f"{store_free // 2**20} MiB free where the engine keeps its data, "
                f"{disk['store_needed_bytes'] // 2**20} MiB wanted: the folded copies sit beside "
                "the old banks",
            )
        if not same and archive_free < archive_need:
            refuse(
                "disk_space",
                f"{archive_free // 2**20} MiB free under {archive_at} for the archives, "
                f"{archive_need // 2**20} MiB wanted",
            )
        return disk

    # ── run and resume ──────────────────────────────────────────────────

    def _embedder(self, journal: Journal) -> Embedder:
        return Embedder(journal.embedder["provider"], journal.embedder["model"])

    async def run(self) -> dict[str, Any]:
        """Preflight, then the whole fold-in. Raises :class:`FoldInRefused` or :class:`FoldInError`."""
        with self._lock():
            plan = await self.preflight(apply=True)
            if plan["refusals"]:
                raise FoldInRefused(plan["refusals"][0]["message"], plan)
            run_id = secrets.token_hex(4)
            try:
                archive_dir = self._make_archive_dir(run_id)
            except OSError as exc:
                raise FoldInRefused(
                    f"could not create the archive directory under {em.archive_root()}: {exc}",
                    plan,
                ) from exc
            ledger = load_ledger()
            earlier: dict[str, dict[str, Any]] = dict(ledger.get("banks") or {})
            current_known = self._current_known()
            banks: list[BankEntry] = []
            for row in plan["banks"]:
                if row["action"] not in ("fold", "recheck"):
                    continue
                before = earlier.get(row["bank"]) or {}
                frozen_owners = before.get("owners")
                frozen_known = before.get("known_clients")
                banks.append(
                    BankEntry(
                        bank=row["bank"],
                        cls=row["class"],
                        harness=row["harness"],
                        agent=row["agent"],
                        project=row["project"],
                        tag_project=row["bank"] in TAG_PROJECT_CODING_BANKS
                        or row["bank"] in self._maps,
                        owners=dict(frozen_owners)
                        if isinstance(frozen_owners, dict)
                        else dict(self._owners),
                        known_clients=list(frozen_known)
                        if isinstance(frozen_known, list)
                        else list(current_known),
                        refold=row["action"] == "recheck",
                        prior_files={
                            k: list(v)
                            for k, v in (before.get("sent_files") or {}).items()
                            if isinstance(v, list)
                        },
                    )
                )
            journal = Journal(
                run_id=run_id,
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                pid=os.getpid(),
                phase=PHASE_PAUSED,
                archive_dir=str(archive_dir),
                embedder=dict(plan["embedder"]),
                targets=dict(plan["targets"]),
                owners=dict(self._owners),
                maps=dict(self._maps),
                platform_bank=plan["platform_bank"],
                pending=list(plan.get("pending") or []),
                skips=list(self._skips),
                private_port=self._pick_port(),
                banks=banks,
            )
            self._save(journal)
            try:
                await self._pause(journal)
                self._say(
                    "moving the engine to a private port; recall is unavailable until the run ends"
                )
                journal.isolated = True
                self._save(journal)
                await self._start_engine(
                    self._embedder(journal),
                    port=journal.private_port,
                    run_id=run_id,
                    journal=journal,
                )
                self._hook("isolated")
                await self._settle(journal)
                journal.phase = PHASE_FOLDING
                self._save(journal)
                await self._fold_all(journal)
                self._record_ledger(journal)
            except BaseException as exc:
                cause = (
                    exc.message if isinstance(exc, FoldInError) else f"{type(exc).__name__}: {exc}"
                )
                if not journal.sent:
                    await self._abandon(journal, cause)
                else:
                    journal.error = cause
                    with contextlib.suppress(OSError):
                        self._save(journal)
                    if isinstance(exc, FoldInError):
                        exc.resumable = True
                raise
            return await self._finish(journal)

    async def resume(self) -> dict[str, Any]:
        """Carry an interrupted run on from its journal, bank by bank, then cut over."""
        with self._lock():
            found = locate_journal()
            journal: Journal | None = found["journal"]
            if journal is None:
                if found["error"]:
                    raise FoldInError(
                        f"the fold-in journal is not usable ({found['error']}) and no usable "
                        "mirror of it was found. Nothing was changed"
                    )
                gate = write_gate.read_gate()
                if gate is not None and gate.get("reason") == write_gate.REASON_FOLD_IN:
                    # Stopped between retiring its journal and lifting the
                    # pause: nothing is left to carry on but the pause.
                    return self._clear_stuck_gate()
                raise FoldInRefused("there is no interrupted fold-in to resume")
            if not self._privileged():
                raise FoldInRefused("run this as root (sudo)")
            if self._owners and dict(self._owners) != dict(journal.owners):
                raise FoldInRefused(
                    f"run {journal.run_id} was planned with --owner "
                    + (", ".join(f"{k}={v}" for k, v in sorted(journal.owners.items())) or "(none)")
                    + "; a resume keeps the owners it was planned with. Run --resume without "
                    "--owner, or with the same flags"
                )
            if write_gate.read_gate() is None:
                write_gate.engage_gate(
                    reason=write_gate.REASON_FOLD_IN,
                    run_id=journal.run_id,
                    archive_dir=journal.archive_dir,
                )
            journal.error = None
            self._save(journal)
            try:
                if journal.phase == PHASE_CUTTING_OVER and not journal.isolated:
                    return await self._cutover(journal)
                if journal.phase == PHASE_RELEASING and not journal.isolated:
                    return await self._end_without_cutover(journal)
                journal.isolated = True
                self._save(journal)
                await self._ensure_engine(
                    self._embedder(journal),
                    port=journal.private_port,
                    run_id=journal.run_id,
                    journal=journal,
                )
                if journal.phase == PHASE_PAUSED:
                    journal.phase = PHASE_FOLDING
                    self._save(journal)
                if journal.phase == PHASE_FOLDING:
                    await self._fold_all(journal)
                    self._record_ledger(journal)
            except FoldInError as exc:
                journal.error = exc.message
                with contextlib.suppress(OSError):
                    self._save(journal)
                exc.resumable = True
                raise
            if journal.phase == PHASE_CUTTING_OVER:
                return await self._cutover(journal)
            if journal.phase == PHASE_RELEASING:
                return await self._end_without_cutover(journal)
            return await self._finish(journal)

    async def _abandon(self, journal: Journal, cause: str) -> None:
        """Undo a run that sent no import: engine back, journal retired, then the gate lifted.

        Nothing was written to any bank, so there is nothing to keep paused for.
        """
        self._say(f"stopping before any bank was changed: {cause}")
        try:
            if journal.isolated or self._isolated_port() is not None:
                await self._start_engine(
                    self._embedder(journal), port=None, run_id=journal.run_id, journal=journal
                )
                journal.isolated = False
        except FoldInError as exc:
            journal.error = f"{cause}; returning the engine to its normal address failed: {exc}"
            self._save(journal)
            raise
        journal.phase = PHASE_ABANDONED
        journal.error = cause
        self._retire_journal(journal)
        self._hook("retired")
        write_gate.lift_gate()

    async def _fold_all(self, journal: Journal) -> None:
        """Pre-check every bank of the run, then import them, one at a time.

        Nothing is imported until every bank passed its pre-check
        (:meth:`_precheck_all`): when any would be left, none is imported and
        the run ends with the full report. A bank can then be left only by an
        import that fails, and the next run sends what is missing of it.
        """
        if not journal.checked:
            await self._precheck_all(journal)
            failed = [e.bank for e in journal.banks if e.step == STEP_LEFT]
            if failed:
                self._hold_back(journal, failed)
                return
            journal.checked = True
            self._save(journal)
        for index, entry in enumerate(journal.banks):
            if entry.step in _DONE_STEPS:
                continue
            self._say(f"importing {entry.bank} ({index + 1}/{len(journal.banks)})")
            try:
                await self._fold(journal, entry, index)
            except BankLeft as exc:
                self._leave(journal, entry, exc)
            self._record_ledger(journal)

    async def _precheck_all(self, journal: Journal) -> None:
        """Export, rewrite and check every bank of the run, importing nothing.

        Per bank (:meth:`_precheck`): the export, the rewrite with its owner
        and routing checks, the archive's own contents (attachments and
        invalidated facts a merge cannot carry), the routing check again on
        the files on disk, and the clash, changed and gone checks of each
        part against its target. Then across banks: no two banks of the run
        may send the same document or mental-model id to one target
        (:meth:`_check_duplicates`). Every bank is checked, so the report is
        whole.
        """
        for index, entry in enumerate(journal.banks):
            if entry.step in _DONE_STEPS:
                continue
            verb = "re-checking" if entry.refold else "checking"
            self._say(f"{verb} {entry.bank} ({index + 1}/{len(journal.banks)})")
            try:
                await self._precheck(journal, entry, index)
            except BankLeft as exc:
                self._leave(journal, entry, exc)
        await self._settle_page_names(journal)
        for entry in journal.banks:
            if entry.step in _DONE_STEPS:
                continue
            try:
                await self._precheck_targets(journal, entry)
            except BankLeft as exc:
                self._leave(journal, entry, exc)
        self._check_duplicates(journal)

    async def _precheck(self, journal: Journal, entry: BankEntry, index: int) -> None:
        """One bank's export, rewrite and checks. A bank an earlier run folded goes the same way.

        There is no "unchanged" shortcut: counts and the last write time do
        not move when a document is re-tagged ``visibility:private`` or a
        model's tags change, so every folded bank is exported again and its
        routing, clash, changed and gone checks run on what it holds now.
        """
        if entry.step == STEP_PLANNED:
            await self._export(journal, entry, index)
            self._hook(f"exported:{entry.bank}")
        if entry.step == STEP_EXPORTED:
            self._rewrite(journal, entry, index)
            self._hook(f"rewritten:{entry.bank}")
        self._check_routing(journal, entry)

    async def _precheck_targets(self, journal: Journal, entry: BankEntry) -> None:
        """One bank's clash, changed and gone checks against its targets.

        After :meth:`_settle_page_names`, which may rename the bank's pages:
        the checks compare what will be sent.
        """
        for name in _PARTS:
            part = entry.part(name)
            if part is not None and not part.sent:
                await self._clashes(
                    journal, entry, name, part.target, self._part_ids(journal, part)
                )
            elif part is None and entry.prior_files.get(name):
                # An earlier run sent this part, and the bank now has nothing
                # for it (every shared document re-tagged private, say): what
                # it sent must not stay behind unseen.
                await self._clashes(journal, entry, name, journal.targets[name], {})

    async def _settle_page_names(self, journal: Journal) -> None:
        """Give every page bound for one project folder a name of its own.

        A page's name is unique in its folder (``uq_kp_folder_pagename``,
        alembic ``a9b8c7d6e5f4``, a partial index ``WHERE kind = 'page'``),
        and every coding bank of one project brings the plugin's five standard
        pages under the same names. Folder names are not unique, so folders
        are never renamed: two banks' ``Initiatives`` folders land as two
        sibling folders of that name in the project folder. (Merging them into
        one would hang one bank's pages under a folder another bank's archive
        carries, which the merge import cannot do: it keeps a ``parent_id``
        only when the parent is in the same archive, and the page rule would
        then have to run inside every merged folder.) Within one project
        folder of the target:

        * a page already there that no bank of the run sent (one the plugin
          wrote through compat, or the platform bank's), or one the platform
          bank of this run carries there, keeps its name, and every folded
          node of that name is renamed;
        * otherwise one folded page keeps the plain name: the one the ledger
          recorded as keeper, else the one from the bank whose id names the
          project most directly (``<harness>::<slug>``, then a repository
          name the registry maps to it, then a bank whose project comes from
          tags), then the latest ``last_refreshed_at``, then the bank id;
        * the others are renamed ``<name> (<bank>)`` (:func:`renamed_page_name`).

        The choice is recorded per name (:attr:`Journal.page_keepers`, then
        the ledger), so every run makes the same one. A bank is left only by
        a true conflict: two of its own pages with one name in one folder, or
        a renamed name the folder already holds. Renames are written into the
        bank's shared archive and its ids (:meth:`_apply_page_names`) before
        the clash checks, so those compare what will be sent.
        """
        target = journal.targets.get(PART_SHARED)
        if not target:
            return
        from hal0.memory.projects import registry_from_config

        registry = registry_from_config(getattr(self._memory_config(), "projects", None))
        live = [e for e in journal.banks if e.step == STEP_REWRITTEN]
        candidates: dict[tuple[str, str], list[tuple[BankEntry, dict[str, Any]]]] = {}
        fixed: dict[tuple[str, str], str] = {}
        ours: set[str] = set()
        ids_of: dict[str, dict[str, Any]] = {}
        for entry in live:
            part = entry.part(PART_SHARED)
            if part is None or part.sent:
                continue
            ids = self._part_ids(journal, part)
            ids_of[entry.bank] = ids
            nodes = ids.get("page_nodes") or {}
            ours.update(str(r.get("id")) for r in nodes.values() if isinstance(r, dict))
            if entry.cls != CLASS_CODING:
                for slug, lname, _node in ids.get("page_names") or []:
                    fixed.setdefault((str(slug), str(lname)), entry.bank)
                continue
            by_new = {str(r.get("id")): r for r in nodes.values() if isinstance(r, dict)}
            for slug, lname, new_id in ids.get("page_names") or []:
                row = by_new.get(str(new_id))
                if row is not None:
                    candidates.setdefault((str(slug), str(lname)), []).append((entry, row))
        if not candidates:
            return
        present = await self._project_page_names(target)
        #: Per project folder, every lower-cased name that will be in it.
        taken: dict[str, set[str]] = {}
        for (slug, lname), node_id in present.items():
            if node_id not in ours:
                fixed.setdefault((slug, lname), "target")
        for slug, lname in fixed:
            taken.setdefault(slug, set()).add(lname)
        try:
            recorded = (load_ledger().get("page_keepers") or {}).get(target) or {}
        except FoldInError:
            recorded = {}
        keepers = journal.page_keepers.setdefault(target, {})
        renames: dict[str, dict[str, str]] = {}
        conflicts: dict[str, list[str]] = {}
        for (slug, lname), cands in sorted(candidates.items()):
            key = f"{slug}/{lname}"
            banks = [e.bank for e, _ in cands]
            for bank in {b for b in banks if banks.count(b) > 1}:
                conflicts.setdefault(bank, []).append(f"project:{slug}/{lname}")
            if (slug, lname) in fixed:
                keeper = fixed[(slug, lname)]
            elif recorded.get(key) in banks:
                keeper = recorded[key]
            else:
                keeper = min(cands, key=lambda c: self._keeper_rank(c[0], c[1], slug, registry))[
                    0
                ].bank
            keepers[key] = keeper
            for entry, row in cands:
                if entry.bank == keeper:
                    taken.setdefault(slug, set()).add(lname)
                    continue
                renames.setdefault(entry.bank, {})[str(row["id"])] = renamed_page_name(
                    str(row.get("name") or ""), entry.bank
                )
        for entry in live:
            for new_id, name in (renames.get(entry.bank) or {}).items():
                slug = next(
                    str(r[0]) for r in ids_of[entry.bank].get("page_names") or [] if r[2] == new_id
                )
                names = taken.setdefault(slug, set())
                if name.lower() in names or (slug, name.lower()) in candidates:
                    conflicts.setdefault(entry.bank, []).append(f"project:{slug}/{name.lower()}")
                names.add(name.lower())
        self._save(journal)
        for entry in live:
            if entry.bank in conflicts:
                self._leave(
                    journal,
                    entry,
                    BankLeft(
                        f"bank {entry.bank}: {len(conflicts[entry.bank])} knowledge page "
                        "name(s) cannot be given a place of their own in their project "
                        "folder (two of the bank's own pages share a name there, or the renamed "
                        "name is taken too). Rename one in the old bank, then run the fold-in "
                        "again",
                        bank=entry.bank,
                        details={"page_names": sorted(conflicts[entry.bank])},
                    ),
                )
                continue
            if entry.bank in ids_of and entry.cls == CLASS_CODING:
                self._apply_page_names(journal, entry, renames.get(entry.bank) or {})

    @staticmethod
    def _keeper_rank(
        entry: BankEntry, row: Mapping[str, Any], slug: str, registry: Any
    ) -> tuple[int, float, str]:
        """Sort key of a candidate to keep a plain name: lowest wins (:meth:`_settle_page_names`)."""
        if entry.project == slug:
            direct = 0
        elif entry.project is not None and (
            resolve_project(bank_segment=entry.project, registry=registry, coding=True).slug == slug
        ):
            direct = 1
        else:
            direct = 2
        return (direct, -_refreshed_epoch(row.get("refreshed_at")), entry.bank)

    def _apply_page_names(
        self, journal: Journal, entry: BankEntry, renames: Mapping[str, str]
    ) -> None:
        """Write each node's settled name into the bank's shared archive and its ids.

        Idempotent: every placed node gets its rename or, without one, its
        own name again, so a re-check after a kill lands on the same files.
        A page's backing model is named as its page (the engine creates both
        under one name), and its digests are taken again.
        """
        part = entry.part(PART_SHARED)
        assert part is not None
        ids = self._part_ids(journal, part)
        nodes = {
            str(r.get("id")): r
            for r in (ids.get("page_nodes") or {}).values()
            if isinstance(r, dict)
        }
        names = {
            node_id: renames.get(node_id, str(r.get("name") or "")) for node_id, r in nodes.items()
        }
        archive = _read_archive(self._read_archive_file(journal, part.archive))
        pages = []
        model_names: dict[str, str] = {}
        for page in archive["knowledge_pages"]:
            row = dict(page)
            if str(row.get("id")) in names:
                row["name"] = names[str(row["id"])]
                if row.get("kind") == "page" and row.get("mental_model_id"):
                    model_names[str(row["mental_model_id"])] = row["name"]
            pages.append(row)
        models = []
        for model in archive["mental_models"]:
            row = dict(model)
            if str(row.get("id")) in model_names:
                row["name"] = model_names[str(row["id"])]
            models.append(row)
        data = _write_archive(
            str(archive["manifest"].get("source_bank_id") or entry.bank),
            archive["documents"],
            archive["observations"],
            models,
            pages,
        )
        self._write_archive_file(journal, part.archive, data)
        for row in models:
            mid = str(row.get("id"))
            if mid in (ids.get("model_digests") or {}):
                ids["model_digests"][mid] = model_digest(row)
                ids["model_spec_digests"][mid] = model_spec_digest(row)
        ids["page_shown_names"] = {
            k: v for k, v in sorted(names.items()) if v != nodes[k].get("name")
        }
        self._write_archive_file(journal, _ids_name(part.archive), json.dumps(ids).encode())
        if renames:
            entry.report.setdefault("knowledge_pages", {})["renamed"] = len(renames)

    def _check_duplicates(self, journal: Journal) -> None:
        """Leave every pair of banks of this run that would send one id to the same target.

        Two old banks can hold a document under the same id (a platform bank
        and a ``project__`` bank both written with a provider id, for
        example). Both land in one target, where a merge with ``skip`` keeps
        whichever comes first and drops the other without a word. The ids
        are not rewritten: the agents go on writing under them after the
        cutover, and a rewritten copy would no longer be the document they
        update. So both banks are left, and named to each other.
        """
        seen: dict[tuple[str, str, str], list[str]] = {}
        for entry in journal.banks:
            if entry.step != STEP_REWRITTEN:
                continue
            for name in _PARTS:
                part = entry.part(name)
                if part is None:
                    continue
                ids = self._part_ids(journal, part)
                for kind in ("documents", "mental_models"):
                    for item in ids.get(kind) or []:
                        seen.setdefault((part.target, kind, str(item)), []).append(entry.bank)
        pairs: dict[str, dict[str, Any]] = {}
        for (target, kind, item), banks in seen.items():
            banks = list(dict.fromkeys(banks))
            if len(banks) < 2:
                continue
            for bank in banks:
                row = pairs.setdefault(bank, {"with": set(), "ids": {}})
                row["with"].update(b for b in banks if b != bank)
                row["ids"].setdefault(target, {}).setdefault(kind, []).append(item)
        for entry in journal.banks:
            row = pairs.get(entry.bank)
            if row is None:
                continue
            count = sum(len(v) for kinds in row["ids"].values() for v in kinds.values())
            others = ", ".join(sorted(row["with"]))
            self._leave(
                journal,
                entry,
                BankLeft(
                    f"bank {entry.bank}: {count} of its document or mental-model id(s) are also "
                    f"in {others}, and both would go to the same target, where a merge keeps "
                    "only the first. Delete or rename one copy in one of the old banks, then run "
                    "the fold-in again",
                    bank=entry.bank,
                    details={"duplicates_with": sorted(row["with"]), "ids": row["ids"]},
                ),
            )

    def _hold_back(self, journal: Journal, failed: list[str]) -> None:
        """Leave every bank that passed its pre-check: a run imports all of its banks or none."""
        listing = ", ".join(failed)
        for entry in journal.banks:
            if entry.step in _DONE_STEPS:
                continue
            entry.step = STEP_LEFT
            entry.note = (
                f"checked and ready, but not imported: {len(failed)} bank(s) of this run would "
                f"be left ({listing}), and a run imports nothing unless every bank in it passes "
                "its checks"
            )
        self._save(journal)
        self._say(f"nothing was imported: {listing} did not pass the pre-check")

    def _leave(self, journal: Journal, entry: BankEntry, exc: BankLeft) -> None:
        """Mark ``entry`` left; its ids go to the run's 0600 sidecar, never into the note."""
        entry.step = STEP_LEFT
        note = exc.message
        if exc.details:
            path = self._record_left_ids(journal, entry.bank, exc.details)
            note += f". The ids are listed in {path}" if path and path not in note else ""
        entry.note = note
        self._save(journal)
        self._say(f"{entry.bank} is left as it was: {note}")

    def _record_left_ids(self, journal: Journal, bank: str, details: Mapping[str, Any]) -> str:
        """Add ``details`` for ``bank`` to :data:`LEFT_IDS_NAME`; its path, or "" if unwritable."""
        try:
            try:
                current = json.loads(self._read_archive_file(journal, LEFT_IDS_NAME))
            except FileNotFoundError:
                current = {}
            current = current if isinstance(current, dict) else {}
            current[bank] = json.loads(json.dumps(details, default=sorted))
            self._write_archive_file(
                journal, LEFT_IDS_NAME, json.dumps(current, indent=2, sort_keys=True).encode()
            )
        except (OSError, ValueError) as exc:
            log.warning("hal0.memory.fold_in_left_ids_unwritten", bank=bank, error=str(exc))
            return ""
        return str(Path(journal.archive_dir) / LEFT_IDS_NAME)

    async def _fold(self, journal: Journal, entry: BankEntry, index: int) -> None:
        """Import one pre-checked bank's parts and check each target holds all of it."""
        if entry.step in (STEP_PLANNED, STEP_EXPORTED):
            raise BankLeft(
                f"bank {entry.bank} reached the imports without its pre-check; nothing of it is "
                "sent",
                bank=entry.bank,
            )
        if entry.step in (STEP_REWRITTEN, STEP_IMPORTING):
            self._check_routing(journal, entry)
            for name in _PARTS:
                part = entry.part(name)
                if part is not None and not part.done:
                    await self._import_part(journal, entry, name, part)
            shared = entry.part(PART_SHARED)
            if shared is not None and shared.done:
                await self._record_page_map(journal, entry, shared)
            entry.step = STEP_IMPORTED
            self._save(journal)
        if entry.step == STEP_IMPORTED:
            entry.step = STEP_FOLDED
            self._save(journal)
            self._hook(f"folded:{entry.bank}")

    def _prior(self, entry: BankEntry) -> dict[str, dict[str, Any]]:
        """What earlier runs sent of ``entry``, per part, from their ``.ids.json`` files.

        A file that is gone or unreadable leaves the bank (fail closed): the
        fold-in could not tell its own copies in the target from a clash.
        """
        out: dict[str, dict[str, Any]] = {}
        for name, paths in entry.prior_files.items():
            merged: dict[str, Any] = {
                "documents": {},
                "mental_models": set(),
                #: Per sent model, ``{"spec": ..., "content": ...}``
                #: (:func:`model_spec_digest`, :func:`model_content_digest`),
                #: or ``{"combined": ...}`` from a file an earlier build wrote
                #: (:func:`model_digest`). A model with no record at all was
                #: recorded by a build before model digests.
                "model_records": {},
                "pages": set(),
                "observations": set(),
            }
            for path in paths:
                try:
                    data = json.loads(_read_nofollow(Path(path)))
                except (OSError, ValueError) as exc:
                    raise BankLeft(
                        f"bank {entry.bank}: the record of what an earlier run sent of it "
                        f"({path}) cannot be read ({type(exc).__name__}). Without it the "
                        "fold-in cannot tell its own copies in the target from another "
                        "bank's, so it does not fold the bank",
                        bank=entry.bank,
                    ) from exc
                digests = data.get("digests") if isinstance(data, dict) else None
                if not isinstance(digests, dict):
                    raise BankLeft(
                        f"bank {entry.bank}: {path} records no document digests",
                        bank=entry.bank,
                    )
                merged["documents"].update({str(k): str(v) for k, v in digests.items()})
                merged["mental_models"].update(str(m) for m in data.get("mental_models") or ())
                specs = data.get("model_spec_digests")
                contents = data.get("model_content_digests")
                combined = data.get("model_digests")
                if isinstance(specs, dict) and isinstance(contents, dict):
                    for key, spec in specs.items():
                        merged["model_records"][str(key)] = {
                            "spec": str(spec),
                            "content": str(contents.get(key)),
                        }
                elif isinstance(combined, dict):
                    for key, value in combined.items():
                        merged["model_records"][str(key)] = {"combined": str(value)}
                merged["pages"].update(str(m) for m in data.get("pages") or ())
                merged["observations"].update(str(o) for o in data.get("observations") or ())
            out[name] = merged
        return out

    async def _export(self, journal: Journal, entry: BankEntry, index: int) -> None:
        """Export the bank and require the archive to hold all of it."""
        before = await self._quiet_state(entry.bank)
        archived = await self._export_bank(journal, entry, index)
        after = await self.bank_state(entry.bank)
        moved = em.diff(before["counts"], after["counts"]) or em.signals_moved(
            before["fingerprint"], after["fingerprint"]
        )
        if moved:
            raise BankLeft(
                f"bank {entry.bank} changed while it was exported, although the engine was "
                "isolated",
                bank=entry.bank,
            )
        short = {
            key: (after["counts"][key], archived[key])
            for key in ("facts", "documents")
            if archived[key] != after["counts"][key]
        }
        if short:
            shown = ", ".join(
                f"{k}: the bank has {b}, its archive {a}" for k, (b, a) in short.items()
            )
            raise BankLeft(
                f"bank {entry.bank}'s archive does not hold all of it ({shown}). The engine's "
                "export leaves out a fact that belongs to no document, and the fold-in does not "
                "fold a bank it would carry only in part",
                bank=entry.bank,
            )
        entry.counts = archived
        entry.source_counts = dict(after["counts"])
        entry.last_document_at = after["fingerprint"].get("document")
        entry.step = STEP_EXPORTED
        self._save(journal)

    def _read_archive_file(self, journal: Journal, name: str) -> bytes:
        with self._archive_dir_fd(journal) as dfd:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
            with os.fdopen(fd, "rb") as fh:
                return fh.read()

    def _write_archive_file(self, journal: Journal, name: str, data: bytes) -> None:
        from hal0.install import perms

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
            with os.fdopen(fd, "wb") as fh:
                if owner is not None and os.geteuid() == 0:
                    os.fchown(fh.fileno(), *owner)
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(part, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            os.fsync(dfd)

    @staticmethod
    def _provenance_name(entry: BankEntry) -> str:
        assert entry.archive is not None
        return entry.archive[: -len(".zip")] + ".provenance.json"

    def _rewrite(self, journal: Journal, entry: BankEntry, index: int) -> None:
        assert entry.archive is not None
        data = self._read_archive_file(journal, entry.archive)
        try:
            result = rewrite_archive(data, self._rewrite_plan(entry, journal))
        except BankLeft:
            raise
        except (KeyError, ValueError, TypeError, zipfile.BadZipFile) as exc:
            raise BankLeft(
                f"bank {entry.bank}'s archive could not be rewritten ({type(exc).__name__}: {exc})",
                bank=entry.bank,
            ) from exc
        self._write_archive_file(
            journal, self._provenance_name(entry), json.dumps(result.provenance).encode()
        )
        for name, blob in ((PART_SHARED, result.shared), (PART_PRIVATE, result.private)):
            if blob is None:
                continue
            file_name = _archive_name(index, entry.bank, name)
            self._write_archive_file(journal, file_name, blob)
            ids = {**result.ids[name], "digests": result.digests[name]}
            self._write_archive_file(journal, _ids_name(file_name), json.dumps(ids).encode())
            entry.put(
                name,
                Part(
                    target=journal.targets[name], archive=file_name, expected=result.expected[name]
                ),
            )
        entry.report = result.report
        entry.step = STEP_REWRITTEN
        self._save(journal)

    def _check_routing(self, journal: Journal, entry: BankEntry) -> None:
        """:func:`routing_problems` on the files on disk, before anything of the bank is sent.

        The source archive as exported, both rewritten parts and the rewrite's
        record of where each row came from: any private row that is not on
        the private side leaves the bank before an import.
        """
        assert entry.archive is not None
        try:
            source = self._read_archive_file(journal, entry.archive)
            provenance = json.loads(self._read_archive_file(journal, self._provenance_name(entry)))
            blobs: dict[str, bytes | None] = {}
            for name in _PARTS:
                part = entry.part(name)
                blobs[name] = (
                    self._read_archive_file(journal, part.archive) if part is not None else None
                )
            problems = routing_problems(
                source,
                blobs[PART_SHARED],
                blobs[PART_PRIVATE],
                provenance if isinstance(provenance, dict) else {},
                bank_cls=entry.cls,
                tag_project=entry.tag_project,
            )
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            raise BankLeft(
                f"bank {entry.bank}: its archives could not be checked before the import "
                f"({type(exc).__name__}: {exc}); nothing of it is sent",
                bank=entry.bank,
            ) from exc
        if problems:
            raise BankLeft(
                f"bank {entry.bank}: its rewritten archives do not keep its private memory on "
                f"the private side ({len(problems)} problem(s)); nothing of it is sent",
                bank=entry.bank,
                details={"problems": problems},
            )

    def _part_ids(self, journal: Journal, part: Part) -> dict[str, Any]:
        data = json.loads(self._read_archive_file(journal, _ids_name(part.archive)))
        return data if isinstance(data, dict) else {}

    async def _ensure_target(self, target: str) -> None:
        """Create a target bank the engine does not have yet (a merge imports into an existing bank)."""
        if target in await self._bank_ids():
            return
        await self._json(
            "PUT", em.bank_path(target), body={}, what="creating the bank", bank=target
        )

    async def _document_ids(self, bank: str) -> set[str]:
        if bank not in await self._bank_ids():
            return set()
        rows = await self._list_all(
            f"{em.bank_path(bank)}/documents",
            key="items",
            what="listing documents",
            bank=bank,
            page_size=_CONTENT_PAGE,
        )
        return {str(r.get("id")) for r in rows if isinstance(r, dict)}

    async def _model_ids(self, bank: str) -> set[str]:
        if bank not in await self._bank_ids():
            return set()
        rows = await self._list_all(
            f"{em.bank_path(bank)}/mental-models",
            {"detail": "content"},
            key="items",
            what="reading mental models",
            bank=bank,
        )
        return {str(r.get("id")) for r in rows if isinstance(r, dict)}

    async def _page_model_ids(self, bank: str) -> set[str]:
        """The mental-model ids behind the knowledge pages ``bank`` holds.

        A page imported into a bank whose id another bank holds gets a fresh
        id (``_restore_knowledge_pages``), so a carried page is found by the
        model behind it, which keeps its id.
        """
        if bank not in await self._bank_ids():
            return set()
        stack = await self._tree_roots(bank)
        out: set[str] = set()
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("kind") == "page" and node.get("mental_model_id"):
                out.add(str(node["mental_model_id"]))
            stack.extend(node.get("children") or [])
        return out

    async def _tree_roots(self, bank: str) -> list[Any]:
        """The roots of ``bank``'s knowledge-base tree, as the engine nests them."""
        tree = await self._json(
            "GET",
            f"{em.bank_path(bank)}/knowledge-base/tree",
            what="reading the knowledge base",
            bank=bank,
        )
        roots = (tree if isinstance(tree, dict) else {}).get("roots")
        return list(roots) if isinstance(roots, list) else []

    async def _tree_index(self, bank: str) -> dict[str, tuple[dict[str, Any], str]]:
        """Every node of ``bank``'s tree by id, with the id of the root it hangs from."""
        out: dict[str, tuple[dict[str, Any], str]] = {}
        stack: list[tuple[Any, str | None]] = [(n, None) for n in await self._tree_roots(bank)]
        while stack:
            node, root = stack.pop()
            if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                continue
            here = root or node["id"]
            out.setdefault(node["id"], (node, here))
            stack.extend((child, here) for child in node.get("children") or [])
        return out

    async def _project_page_names(self, bank: str) -> dict[tuple[str, str], str]:
        """``(slug, lower-cased name) -> page id`` of every page directly in a root
        ``project:<slug>`` folder of ``bank``: the names a carried page may not take.

        Only pages: ``uq_kp_folder_pagename`` is ``WHERE kind = 'page'``. When
        a project has several root folders of its name (:meth:`_project_folder`),
        the names in all of them count, because compat shows them as one.
        """
        from hal0.memory.knowledge_pages import KNOWLEDGE_FOLDER_PREFIX

        if bank not in await self._bank_ids():
            return {}
        out: dict[tuple[str, str], str] = {}
        for root in await self._tree_roots(bank):
            if not isinstance(root, dict) or root.get("kind") != "folder":
                continue
            name = str(root.get("name") or "")
            if not name.startswith(KNOWLEDGE_FOLDER_PREFIX):
                continue
            slug = name[len(KNOWLEDGE_FOLDER_PREFIX) :]
            for child in root.get("children") or []:
                if (
                    isinstance(child, dict)
                    and child.get("kind") == "page"
                    and isinstance(child.get("id"), str)
                ):
                    out.setdefault((slug, str(child.get("name") or "").lower()), child["id"])
        return out

    async def _project_folder(self, entry: BankEntry, target: str, slug: str) -> str:
        """The id of ``target``'s folder for project ``slug``, created when it has none.

        Found and created exactly as ``/api/memory/compat`` finds and creates
        it (``_parent_folder`` in ``hal0.api.routes.memory_compat``): the
        first root folder named ``project:<slug>`` in id order
        (:func:`hal0.memory.knowledge_pages.project_folders`), else the
        engine's ``POST .../knowledge-base/folders`` with that name.

        The engine does not refuse a second folder of one name
        (``create_knowledge_folder`` checks none). The run isolates the
        engine and pauses memory writes through hal0, so compat should not
        reach it; but a writer that does (compat's own folder create racing
        this one, before the isolation took hold or from outside hal0) can
        create the folder between this tree read and the POST. So a project
        can end up with several root ``project:<slug>`` folders. Compat reads them as one tree and
        puts new nodes in the first in id order, so after a POST the tree is
        read again and that first one is used, whether it is the folder made
        here or the other writer's; the pages and later plugin writes then
        share one folder, and an empty duplicate is left as it is.
        """
        from hal0.memory.knowledge_pages import project_folder_name, project_folders

        found = project_folders(await self._tree_roots(target), slug)
        if found:
            return str(found[0]["id"])
        made = await self._json(
            "POST",
            f"{em.bank_path(target)}/knowledge-base/folders",
            body={"name": project_folder_name(slug)},
            what="creating a project folder",
            bank=target,
        )
        folder = made.get("id") if isinstance(made, dict) else None
        if not isinstance(folder, str) or not folder:
            raise FoldInError(
                f"the memory engine created {project_folder_name(slug)} in {target} without an id",
                bank=entry.bank,
                resumable=True,
            )
        self._say(f"created the folder {project_folder_name(slug)} in {target}")
        again = project_folders(await self._tree_roots(target), slug)
        if len(again) > 1:
            first = str(again[0]["id"])
            self._say(
                f"{target} now has {len(again)} folders named {project_folder_name(slug)} "
                f"(another writer created one too); the pages go in {first}, the first in id "
                "order, which compat reads and writes as the project's folder"
            )
            return first
        return folder

    async def _rehome_folders(
        self, entry: BankEntry, target: str, data: bytes, ids: Mapping[str, Any]
    ) -> bytes:
        """``data`` with each placeholder project folder swapped for the target's own.

        The rewrite hangs a coding bank's pages under a placeholder folder per
        project (:func:`_place_pages`). The merge import keeps a node's
        ``parent_id`` only when the parent is in the same archive, so the
        archive must carry the folder the pages go in. Folder names are not
        unique (``uq_kp_folder_pagename`` is ``WHERE kind = 'page'``), so a
        placeholder sent as it is would land as one more ``project:<slug>``
        folder beside the target's; instead the archive carries the target's
        own folder (:meth:`_project_folder`) under its own id. That row is kept
        as the target has it (``ON CONFLICT DO NOTHING``) and the pages land
        in it.
        """
        folders = {str(k): str(v) for k, v in (ids.get("project_folders") or {}).items()}
        if not folders:
            return data
        archive = _read_archive(data)
        present = {str(p.get("id")) for p in archive["knowledge_pages"] if isinstance(p, dict)}
        wanted = sorted(f for f in folders if f in present)
        if not wanted:
            return data
        real = {f: await self._project_folder(entry, target, folders[f]) for f in wanted}
        self._hook(f"placed:{entry.bank}")
        return rehome_folders(data, real)

    async def _record_page_map(self, journal: Journal, entry: BankEntry, part: Part) -> None:
        """Add where each placed node of ``entry`` is now in its target to the page map.

        Read back from the target's tree, after the part was checked, so the
        map names only nodes that are there and the folder they are in.
        """
        ids = self._part_ids(journal, part)
        nodes = ids.get("page_nodes") or {}
        placed = {
            str(old): row
            for old, row in nodes.items()
            if isinstance(row, dict) and row.get("project")
        }
        if not placed:
            return
        index = await self._tree_index(part.target)
        rows: dict[str, dict[str, Any]] = {}
        for old, row in sorted(placed.items()):
            got = index.get(str(row.get("id")))
            if got is None:
                continue
            rows[old] = {
                "id": str(row["id"]),
                "kind": str(row.get("kind")),
                "folder": got[1],
                "project": str(row["project"]),
                "bank": entry.bank,
            }
        record_page_map(part.target, rows)

    async def _observation_texts(self, bank: str) -> set[str]:
        rows = await self._list_all(
            f"{em.bank_path(bank)}/memories/list",
            {"type": "observation"},
            key="items",
            what="listing observations",
            bank=bank,
            page_size=_CONTENT_PAGE,
        )
        return {str(r.get("text")) for r in rows if isinstance(r, dict)}

    async def _import_part(self, journal: Journal, entry: BankEntry, name: str, part: Part) -> None:
        """Merge one part into its target, then check the target holds all of it.

        The first send of a part is the whole archive, after a clash check: no
        document or mental-model id of the part may be in the target already,
        unless an earlier run of this bank sent it there with the same source
        (:meth:`_clashes`). Any later send (a resume after a send that did not
        land whole, or a later run of a bank that was left or has changed) is
        only what the target is missing of the part (:meth:`_remainder`), and
        is checked like a first send: by presence of every recorded id, and by
        growth of exactly what that send carried.
        """
        target = part.target
        full = self._read_archive_file(journal, part.archive)
        if name == PART_SHARED:
            problems = shared_archive_problems(full)
            if problems:
                raise BankLeft(
                    f"bank {entry.bank}: its shared archive holds private memory "
                    f"({len(problems)} problem(s)); it is not imported",
                    bank=entry.bank,
                    details={"problems": problems},
                )
        ids = self._part_ids(journal, part)
        await self._ensure_target(target)
        if part.sent:
            await self._await_part(journal, entry, part)
            if (await self._part_problems(entry, part, ids))[0] == []:
                part.done = True
                entry.put(name, part)
                self._save(journal)
                return
        else:
            await self._clashes(journal, entry, name, target, ids)
        data, sending, unsent = await self._remainder(full, target, ids)
        if unsent:
            # An observation an earlier send carried must have landed with its
            # documents (item 3 of the review); one the old bank built since
            # rests on documents already folded and cannot be carried: the
            # target's own consolidation builds it from the same facts.
            sent_before = set((self._prior(entry).get(name) or {}).get("observations") or ())
            if part.sent:
                sent_before |= set(ids.get("observations") or [])
            required = [o for o in unsent if observation_digest(o) in sent_before]
            later = len(unsent) - len(required)
            if later:
                observed = entry.report.setdefault("observations", {})
                observed["not_carried_built_since"] = later
                self._say(
                    f"{entry.bank}: {later} observation(s) the old bank built since an earlier "
                    f"run rest on documents already in {target} and are not carried; "
                    f"{target}'s consolidation builds them from the same facts"
                )
            if required:
                await self._require_landed_observations(entry, target, required)
        if data is None:
            # Everything of the part is in the target already (an earlier run sent it).
            part.before, part.sending = None, None
            problems, missing = await self._part_problems(entry, part, ids)
            if problems:
                raise BankLeft(
                    f"bank {entry.bank} is not whole in {target}: {'; '.join(problems)}",
                    bank=entry.bank,
                    details={"missing": missing},
                )
            part.done = True
            entry.put(name, part)
            self._save(journal)
            return
        if name == PART_SHARED and shared_archive_problems(data):
            raise BankLeft(
                f"bank {entry.bank}: what is left to send of its shared archive holds private "
                "memory; it is not imported",
                bank=entry.bank,
            )
        data = await self._rehome_folders(entry, target, data, ids)
        state = await self._quiet_state(target)
        part.before = dict(state["counts"])
        part.sending = sending
        entry.step = STEP_IMPORTING
        part.sent = True
        part.op_id = None
        journal.sent = True
        entry.put(name, part)
        self._save(journal)
        self._hook(f"importing:{entry.bank}:{name}")
        part.op_id = await self._send(target, part.archive, data, entry)
        entry.put(name, part)
        self._save(journal)
        await self._wait_import(entry, part)
        self._hook(f"imported:{entry.bank}:{name}")
        problems, missing = await self._part_problems(entry, part, ids)
        if problems:
            entry.put(name, part)
            self._save(journal)
            raise BankLeft(
                f"bank {entry.bank} did not fold into {target} whole: {'; '.join(problems)}",
                bank=entry.bank,
                details={"missing": missing},
            )
        part.done = True
        entry.put(name, part)
        self._save(journal)

    async def _clashes(
        self,
        journal: Journal,
        entry: BankEntry,
        name: str,
        target: str,
        ids: Mapping[str, Any],
    ) -> None:
        """Leave the bank when its target holds anything of it that is not its current copy.

        A document id the target holds is the fold-in's own copy only when an
        earlier run of this bank sent it (the ledger's ``sent_files``) from a
        source with the same digest (:func:`document_digest`: text, facts and
        tags). One the earlier run sent from a source that has changed since
        is refused: a merge with ``skip`` would keep the older copy and drop
        the change without a word. A document or model the earlier run sent
        to ``target`` that this part no longer carries is refused too: it was
        deleted from the old bank, or it now goes elsewhere (a document
        re-tagged ``visibility:private`` goes to the private bank), and its
        copy in ``target`` would stay readable there.

        Mental models are checked in two parts. A model whose spec
        (:func:`model_spec_digest`: name, query, tags, trigger) changed is
        refused, because who may read it changed. A model whose generated
        content alone changed (the engine refreshed it in the old bank) keeps
        the target's copy, with a warning, and is counted in the report as
        ``model_content_refreshed_kept_target``: the trigger travels with the
        model, so the target refreshes it from its own memory after the
        cutover. Keeping the older content is the privacy-safe side, with one
        exception: when the rewrite now clears a model's content because the
        bank holds private facts, the copy in the target was generated before
        and may draw on them, so it is refused as changed. A model recorded
        by an earlier build (no split digest) is refused unless its combined
        :func:`model_digest` is unchanged. Present and not sent by an earlier
        run of this bank is a clash, because a merge drops it.
        """
        prior = self._prior(entry).get(name) or {"documents": {}, "mental_models": set()}
        digests = dict(ids.get("digests") or {})
        present = await self._document_ids(target)
        mine = set(ids.get("documents") or [])
        clash = sorted((mine & present) - set(prior["documents"]))
        changed = sorted(
            d
            for d in mine & present
            if d in prior["documents"] and prior["documents"][d] != digests.get(d)
        )
        gone = sorted((set(prior["documents"]) & present) - mine)
        my_models = set(ids.get("mental_models") or [])
        specs = dict(ids.get("model_spec_digests") or {})
        contents = dict(ids.get("model_content_digests") or {})
        combined = dict(ids.get("model_digests") or {})
        prior_models = set(prior["mental_models"])
        records: dict[str, dict[str, str]] = dict(prior.get("model_records") or {})
        present_models = await self._model_ids(target) if my_models or prior_models else set()
        models = sorted((my_models & present_models) - prior_models)
        models_changed: list[str] = []
        models_unrecorded: list[str] = []
        refreshed: list[str] = []
        for m in sorted(my_models & present_models & prior_models):
            record = records.get(m) or {}
            if "spec" in record:
                if record["spec"] != specs.get(m):
                    models_changed.append(m)
                elif record.get("content") != contents.get(m):
                    if contents.get(m) == CLEARED_CONTENT_DIGEST:
                        models_changed.append(m)
                    else:
                        refreshed.append(m)
            elif "combined" in record:
                if record["combined"] != combined.get(m):
                    models_unrecorded.append(m)
            else:
                models_unrecorded.append(m)
        models_gone = sorted((prior_models & present_models) - my_models)
        left_ids = Path(journal.archive_dir) / LEFT_IDS_NAME
        if clash or models:
            sent_by = self._sent_by_other_banks(entry, name, {*clash, *models})
            named = sorted(set(sent_by.values()))
            raise BankLeft(
                f"bank {entry.bank}: {target} already holds {len(clash)} of its document id(s) "
                f"and {len(models)} of its mental-model id(s)"
                + (f", sent there by an earlier run of {', '.join(named)}" if named else "")
                + ". A merge would skip them and the bank would fold in only in part. Delete "
                "or rename one copy, in the old bank or in the target, then run the fold-in again",
                bank=entry.bank,
                details={"documents": clash, "mental_models": models, "sent_by": sent_by},
            )
        how = self._delete_howto(target, left_ids)
        if models_unrecorded:
            raise BankLeft(
                f"bank {entry.bank}: {len(models_unrecorded)} mental model(s) in {target} were "
                "recorded by an earlier build of the fold-in, which kept one digest for the "
                "whole model, and that digest no longer matches; this build cannot tell a "
                "refresh from a changed scope. Delete the copies and re-send: " + how,
                bank=entry.bank,
                details={"mental_models_recorded_by_earlier_build": models_unrecorded},
            )
        if changed or models_changed:
            raise BankLeft(
                f"bank {entry.bank}: {len(changed)} document(s) and {len(models_changed)} mental "
                f"model(s) an earlier run folded into {target} have changed in the old bank "
                "since (a document's text, facts or tags; a model's name, query, tags or "
                "trigger, or its content now cleared because the bank holds private facts). A "
                "merge with skip keeps the older copy, so the change would be lost without a "
                f"word. Delete those copies from {target}, then run the fold-in again: it sends "
                "them as they are now. " + how,
                bank=entry.bank,
                details={"changed": changed, "changed_mental_models": models_changed},
            )
        if gone or models_gone:
            raise BankLeft(
                f"bank {entry.bank}: {len(gone)} document(s) and {len(models_gone)} mental "
                f"model(s) an earlier run folded into {target} are no longer in the old bank, or "
                f"no longer go to {target} (one re-tagged visibility:private now goes to the "
                f"private bank). Their copies would stay readable in {target}. The fold-in "
                f"deletes nothing; delete them from {target}, then run again. " + how,
                bank=entry.bank,
                details={"gone": gone, "gone_mental_models": models_gone},
            )
        kept = entry.report.setdefault("model_content_refreshed", {})
        kept[name] = len(refreshed)
        entry.report["model_content_refreshed_kept_target"] = sum(kept.values())
        if refreshed:
            self._say(
                f"warning: {entry.bank}: {len(refreshed)} mental model(s) were refreshed in the "
                f"old bank since an earlier run folded them into {target}; the copies in "
                f"{target} are kept as they are, and are refreshed from {target}'s own memory "
                "when their trigger fires after the cutover"
            )

    @staticmethod
    def _delete_howto(target: str, left_ids: Path) -> str:
        """How to delete the copies a refusal names, and where their ids are."""
        base = em.bank_path(target)
        return (
            f"The full list of ids is in {left_ids} (under this bank's name). Delete each "
            f"copy with the engine's DELETE {base}/documents/<id> and DELETE "
            f"{base}/mental-models/<id> (ids URL-encoded) while no fold-in runs. There is no "
            "option to keep a copy: the fold-in does not cut over while one stays behind"
        )

    def _sent_by_other_banks(self, entry: BankEntry, name: str, wanted: set[str]) -> dict[str, str]:
        """Which other bank an earlier run sent each of ``wanted`` for, as far as the ledger says."""
        out: dict[str, str] = {}
        try:
            banks = load_ledger().get("banks") or {}
        except FoldInError:
            return out
        for bank, row in banks.items():
            if bank == entry.bank or not isinstance(row, dict):
                continue
            for path in (row.get("sent_files") or {}).get(name) or []:
                try:
                    data = json.loads(_read_nofollow(Path(path)))
                except (OSError, ValueError):
                    continue
                data = data if isinstance(data, dict) else {}
                for item in [*(data.get("documents") or []), *(data.get("mental_models") or [])]:
                    if str(item) in wanted:
                        out.setdefault(str(item), bank)
        return out

    async def _remainder(
        self, data: bytes, target: str, ids: Mapping[str, Any]
    ) -> tuple[bytes | None, dict[str, int], list[dict[str, Any]]]:
        """What ``target`` is missing of a part: ``(archive or None, its counts, unsent)``.

        Documents already in the target are left out; ``skip`` would leave
        them anyway. Observations go with the documents they rest on. One
        that rests on a document already in the target cannot be sent: the
        engine imports an observation only when all its source documents
        land in the same call. Those are returned as ``unsent`` for
        :meth:`_require_landed_observations`.

        Mental models are inserted ``ON CONFLICT DO NOTHING``, so a model
        already there is left out and not counted. Knowledge pages are
        checked on their own: hindsight-api 1.2.0 writes models and pages on
        one connection with no transaction around them (``import_documents``
        in ``engine/transfer/importer.py``), so a stopped import can leave a
        model without its page. A page is present when the target holds a
        page backed by its model; a missing one is sent again with the
        folders above it and its backing model (the engine refuses a page
        whose model is not in the archive), which the target keeps as it is.
        A coding bank's folders keep the ids the rewrite gave them, so a
        resent page lands in the folder that landed before; any other bank's
        folders are new rows (the engine gives a node a fresh id when another
        bank holds its id), so such a resent page may sit in a second folder
        of the same name.
        """
        present_docs = await self._document_ids(target) & set(ids.get("documents") or [])
        present_models = await self._model_ids(target)
        present_pages = await self._page_model_ids(target)
        wanted_pages = set(ids.get("pages") or [])
        if (
            not present_docs
            and not (present_models & set(ids.get("mental_models") or []))
            and not (present_pages & wanted_pages)
        ):
            archive = _read_archive(data)
            models = archive["mental_models"]
            return data, _counts_of(archive["documents"], archive["observations"], models), []
        archive = _read_archive(data)
        docs = [d for d in archive["documents"] if str(d.get("id")) not in present_docs]
        sending_ids = {str(d.get("id")) for d in docs}
        observations: list[dict[str, Any]] = []
        unsent: list[dict[str, Any]] = []
        for obs in archive["observations"]:
            sources = {str(s.get("document_id")) for s in obs.get("sources") or ()}
            (observations if sources <= sending_ids else unsent).append(obs)
        by_id = {str(p.get("id")): p for p in archive["knowledge_pages"] if isinstance(p, dict)}
        pages: list[dict[str, Any]] = []
        keep: set[str] = set()
        for page in by_id.values():
            if page.get("kind") != "page" or str(page.get("mental_model_id")) in present_pages:
                continue
            node: Mapping[str, Any] | None = page
            while node is not None and str(node.get("id")) not in keep:
                keep.add(str(node.get("id")))
                parent = node.get("parent_id")
                node = by_id.get(str(parent)) if parent else None
        pages = [p for p in archive["knowledge_pages"] if str(p.get("id")) in keep]
        backing = {str(p.get("mental_model_id")) for p in pages if p.get("mental_model_id")}
        new_models = [m for m in archive["mental_models"] if str(m.get("id")) not in present_models]
        models = new_models + [
            m
            for m in archive["mental_models"]
            if str(m.get("id")) in backing and str(m.get("id")) in present_models
        ]
        sending = _counts_of(docs, observations, new_models)
        if not docs and not new_models and not pages:
            return None, sending, unsent
        rebuilt = _write_archive(
            str(archive["manifest"].get("source_bank_id") or ""),
            docs,
            observations,
            models,
            pages,
        )
        return rebuilt, sending, unsent

    async def _require_landed_observations(
        self, entry: BankEntry, target: str, unsent: list[dict[str, Any]]
    ) -> None:
        """Leave the bank unless every observation that cannot be sent again is in the target.

        They rest on documents that landed in an earlier send; if they did not
        land with them, no merge with ``skip`` can bring them now, and the
        bank is not reported folded. Found by their text.
        """
        texts = await self._observation_texts(target)
        missing = [o for o in unsent if str(o.get("text")) not in texts]
        if not missing:
            return
        docs = sorted({str(s.get("document_id")) for o in missing for s in o.get("sources") or ()})
        raise BankLeft(
            f"bank {entry.bank}: {len(missing)} observation(s) rest on {len(docs)} document(s) "
            f"already in {target} that landed without them, and a merge with skip cannot carry "
            "them now (the engine imports an observation only with all its source documents in "
            f"the same call). To fold the bank whole, delete those documents from {target} and "
            "run the fold-in again",
            bank=entry.bank,
            details={"documents": docs, "observations": len(missing)},
        )

    def _import_budget(self, part: Part) -> float:
        counts = part.sending or part.expected
        embedded = counts.get("facts", 0) + counts.get("observations", 0)
        return max(
            self.t.restore_floor_s, self.t.restore_factor * embedded / em.RESTORE_FACTS_PER_S
        )

    async def _send(self, target: str, name: str, data: bytes, entry: BankEntry) -> str:
        try:
            resp = await self._client._http.post(
                f"{em.bank_path(target)}/transfer/import",
                headers=self._auth(),
                params={"mode": "merge", "document_conflict": "skip"},
                files={"file": (name, data, "application/zip")},
                timeout=httpx.Timeout(self.t.export_s, connect=3.0),
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = (
                f"starting the import of {entry.bank} into {target}: the engine answered HTTP "
                f"{exc.response.status_code} {em.http_detail(exc.response.text)}".rstrip()
            )
            if 400 <= exc.response.status_code < 500:
                # The engine refused the archive: nothing of it was written.
                raise BankLeft(message, bank=entry.bank) from exc
            raise FoldInError(message, bank=entry.bank) from exc
        except httpx.HTTPError as exc:
            raise FoldInError(
                f"starting the import of {entry.bank} into {target} failed "
                f"({type(exc).__name__}: {exc})",
                bank=entry.bank,
            ) from exc
        body = resp.json() if resp.content else {}
        op_id = str((body or {}).get("operation_id") or "")
        if not op_id:
            raise FoldInError("the import returned no operation id", bank=entry.bank)
        return op_id

    async def _wait_import(self, entry: BankEntry, part: Part) -> None:
        """Wait for a sent import to end. Never cancel it.

        One that fails leaves the bank: what it wrote stays in the target, and a
        later run sends what is missing with ``skip``. One still running after
        its budget stops the run, resumably.
        """
        assert part.op_id is not None
        path = f"{em.bank_path(part.target)}/operations/{quote(part.op_id, safe='')}"
        deadline = self._clock() + self._import_budget(part)
        delay = min(0.2, self.t.poll_s)
        while True:
            op = await self._json("GET", path, what="reading the import", bank=entry.bank)
            op = op if isinstance(op, dict) else {}
            status = op.get("status")
            if status == "completed":
                return
            if status in ("failed", "cancelled", "not_found"):
                raise BankLeft(
                    f"the import of {entry.bank} into {part.target} ended as {status}: "
                    f"{op.get('error_message') or 'no error message'}. What it wrote stays; "
                    f"`hal0 memory migrate fold-in --apply --bank {entry.bank}` sends what is "
                    "missing",
                    bank=entry.bank,
                )
            if self._clock() >= deadline:
                raise FoldInError(
                    f"the import of {entry.bank} into {part.target} (operation {part.op_id}) is "
                    f"still {status} after {self._import_budget(part):g}s; it is not cancelled. "
                    "Let it finish, then run `hal0 memory migrate fold-in --resume`",
                    bank=entry.bank,
                    resumable=True,
                )
            await self._sleep(delay)
            delay = min(self.t.poll_s, delay * 1.5)

    async def _await_part(self, journal: Journal, entry: BankEntry, part: Part) -> None:
        """Before a sent part is looked at again: its import, or anything else, must have ended.

        An import is never cancelled. One whose id was not recorded (the run
        stopped between sending it and saving the answer) cannot be asked
        after, so the target must show no live operation but the engine's own.
        """
        if part.op_id:
            path = f"{em.bank_path(part.target)}/operations/{quote(part.op_id, safe='')}"
            deadline = self._clock() + 2 * self._import_budget(part)
            while True:
                op = await self._json("GET", path, what="reading the import", bank=entry.bank)
                status = op.get("status") if isinstance(op, dict) else None
                if status not in ("pending", "processing"):
                    break
                if self._clock() >= deadline:
                    raise FoldInError(
                        f"the import of {entry.bank} into {part.target} (operation "
                        f"{part.op_id}) is still {status}; let it finish, then run --resume",
                        bank=entry.bank,
                    )
                await self._sleep(self.t.poll_s)
        live = [
            op
            for op in await self._live_operations(part.target)
            if em.operation_type(op) not in em.ENGINE_OPERATION_TYPES
        ]
        if live:
            raise FoldInError(
                f"{part.target} has {em.operations_text(part.target, live)}: an import of "
                f"{entry.bank} may still be running. Let it finish (`hal0 memory ops list --bank "
                f"{part.target}`), then run --resume",
                bank=entry.bank,
            )

    async def _part_problems(
        self, entry: BankEntry, part: Part, ids: Mapping[str, Any]
    ) -> tuple[list[str], dict[str, list[str]]]:
        """What is wrong with ``part``'s target after its import; empty when it folded in.

        Every document, mental model and knowledge page of the part must be in
        the target, by its recorded id (a page by the model behind it). The
        engine writes a document with its chunks and facts in one
        transaction, so a document that is there is there with its facts.
        When something was sent in this run, the target must also have grown
        by exactly what that send carried (facts, documents, mental models),
        and by at least its observations: the observation floor holds for a
        resend as for a first send.

        Returns ``(problems, missing)``: the problems hold counts only, the
        ids missing per kind go to the run's sidecar (:data:`LEFT_IDS_NAME`).
        """
        problems: list[str] = []
        lost: dict[str, list[str]] = {}
        missing = sorted(set(ids.get("documents") or []) - await self._document_ids(part.target))
        if missing:
            problems.append(f"{len(missing)} document(s) missing")
            lost["documents"] = missing
        models = set(ids.get("mental_models") or [])
        if models:
            gone = sorted(models - await self._model_ids(part.target))
            if gone:
                problems.append(f"{len(gone)} mental model(s) missing")
                lost["mental_models"] = gone
        pages = set(ids.get("pages") or [])
        if pages:
            absent = sorted(pages - await self._page_model_ids(part.target))
            if absent:
                problems.append(f"{len(absent)} knowledge page(s) missing")
                lost["pages_of_models"] = absent
        placed = {
            str(old): row
            for old, row in (ids.get("page_nodes") or {}).items()
            if isinstance(row, dict) and row.get("project")
        }
        if placed:
            missing_nodes, misplaced = await self._placement_problems(part.target, placed)
            if missing_nodes:
                problems.append(f"{len(missing_nodes)} knowledge-base node(s) missing")
                lost["page_nodes"] = missing_nodes
            if misplaced:
                problems.append(
                    f"{len(misplaced)} knowledge page(s) not in their project folder or without "
                    "its scope"
                )
                lost["misplaced_pages"] = misplaced
        if part.before is not None and part.sending is not None:
            state = await self._quiet_state(part.target)
            delta = {k: int(state["counts"][k]) - int(part.before.get(k, 0)) for k in em.COUNT_KEYS}
            differs = em.diff(part.sending, delta, observation_floor=True)
            if differs:
                problems.append(
                    f"{part.target} grew by "
                    + em.diff_text(differs).replace("expected", "expected +")
                )
        return problems, lost

    async def _placement_problems(
        self, target: str, placed: Mapping[str, Mapping[str, Any]]
    ) -> tuple[list[str], list[str]]:
        """``(missing, misplaced)`` new ids among the nodes the rewrite placed in a project folder.

        A node is placed when it hangs from a root folder named
        ``project:<slug>`` for its project; a page also when the target's
        tree shows it with no private tag and with the scope compat shows a
        page by (:func:`hal0.memory.knowledge_pages.trigger_scope_held`).
        """
        from hal0.memory.knowledge_pages import project_folder_name, trigger_scope_held

        index = await self._tree_index(target)
        missing: list[str] = []
        misplaced: list[str] = []
        for row in placed.values():
            new_id, slug = str(row.get("id")), str(row.get("project"))
            got = index.get(new_id)
            if got is None:
                missing.append(new_id)
                continue
            node, root = got
            folder = index.get(root, ({}, root))[0]
            in_folder = (
                root != new_id
                and folder.get("kind") == "folder"
                and folder.get("name") == project_folder_name(slug)
            )
            unscoped = row.get("kind") == "page" and (
                any(_is_private_tag(t) for t in node.get("tags") or ())
                or not trigger_scope_held(_decoded(node.get("trigger")), slug)
            )
            if not in_folder or unscoped:
                misplaced.append(new_id)
        return sorted(missing), sorted(misplaced)

    def _record_ledger(self, journal: Journal) -> None:
        """Write what this run did to every bank into the ledger, for the runs after it.

        A folded bank is recorded with its counts and ``last_document_at``
        (the re-check), a left one with its note. Either way, every part this
        run sent adds its ``.ids.json`` file to ``sent_files``, and the bank's
        owners and known clients are frozen there once anything was sent.
        A bank this run did not reach keeps its earlier line.
        """
        ledger = load_ledger()
        ledger["platform_bank"] = journal.platform_bank or ledger.get("platform_bank")
        ledger["targets"] = dict(journal.targets)
        # The run's --map and --skip, for the runs after it (:meth:`_remembered_choices`).
        maps = dict(ledger.get("maps") or {})
        maps.update(journal.maps)
        ledger["maps"] = maps
        # Only the run's own skips are kept: it dropped those for banks the
        # engine no longer holds, and those named with --forget-skip, so a
        # bank that comes back later is classified from scratch.
        ledger["skips"] = sorted(set(journal.skips) - set(maps))
        # Who keeps each contested page name (:meth:`_settle_page_names`): page
        # names only, never ids.
        keepers = dict(ledger.get("page_keepers") or {})
        for target, rows in journal.page_keepers.items():
            keepers[target] = {**(keepers.get(target) or {}), **rows}
        if keepers:
            ledger["page_keepers"] = keepers
        banks = ledger.setdefault("banks", {})
        for entry in journal.banks:
            if entry.step not in _DONE_STEPS:
                continue
            row = dict(banks.get(entry.bank) or {})
            files = {k: list(v) for k, v in (row.get("sent_files") or {}).items()}
            for name in entry.sent_parts():
                part = entry.part(name)
                assert part is not None
                path = str(Path(journal.archive_dir) / _ids_name(part.archive))
                files.setdefault(name, [])
                if path not in files[name]:
                    files[name].append(path)
            if entry.step == STEP_FOLDED:
                row.update(
                    {
                        "state": LEDGER_FOLDED,
                        "counts": entry.counts,
                        "source_counts": entry.source_counts,
                        "last_document_at": entry.last_document_at,
                        "note": None,
                    }
                )
                if not entry.refold or entry.sent_parts():
                    row["run_id"] = journal.run_id
            else:
                was_folded = row.get("state") == LEDGER_FOLDED
                row.update({"state": LEDGER_LEFT, "note": entry.note, "run_id": journal.run_id})
                if was_folded:
                    row["folded_before"] = True
            if files:
                row["sent_files"] = files
                row.setdefault("owners", dict(entry.owners or {}))
                row.setdefault("known_clients", list(entry.known_clients or []))
            banks[entry.bank] = row
        _save_ledger(ledger)

    async def _finish(self, journal: Journal) -> dict[str, Any]:
        """Cut over when every old bank is folded; otherwise go back to the old layout.

        No cutover while any bank is left (ADR-0058 decision 7a): a bank left
        now, or one this run did not fold (``journal.pending``), keeps the
        store on the old layout.
        """
        self._record_ledger(journal)
        if not journal.pending and all(e.step == STEP_FOLDED for e in journal.banks):
            # Every bank of the run folded: the engine may still hold a bank
            # no run folded (one not named, unknown, or the retired agents
            # bank with memory in it). Listed afresh, every time.
            journal.blocking = await self._cutover_blockers(journal)
        if journal.pending or journal.blocking or any(e.step != STEP_FOLDED for e in journal.banks):
            journal.phase = PHASE_RELEASING
            self._save(journal)
            return await self._end_without_cutover(journal)
        journal.phase = PHASE_CUTTING_OVER
        self._save(journal)
        return await self._cutover(journal)

    async def _cutover_blockers(self, journal: Journal) -> list[dict[str, str]]:
        """Every bank on the engine that holds the cutover back, with what to do about it.

        Every bank the engine holds is listed and classified again. A target
        or a switch's scratch bank is fine. A foldable bank must be recorded
        as folded in the ledger. An unclassified bank must be named with
        ``--skip`` (or ``--map``, which makes it foldable). An excluded bank
        (the retired ``agents`` bank) must be empty, or named with ``--skip``.
        """
        ledger = load_ledger()
        folded = {
            bank
            for bank, row in (ledger.get("banks") or {}).items()
            if isinstance(row, dict) and row.get("state") == LEDGER_FOLDED
        }
        platform = journal.platform_bank or bank_layout.LEGACY_PLATFORM_BANK
        out: list[dict[str, str]] = []
        skipped: list[dict[str, Any]] = []
        on_engine = await self._bank_ids()
        out += await self._deleted_bank_blockers(journal, ledger, set(on_engine))
        for bank in on_engine:
            cls = classify_bank(
                bank, platform_bank=platform, targets=journal.targets.values(), maps=journal.maps
            )
            row: dict[str, Any] = {"bank": bank, "class": cls.cls, "reason": cls.reason}
            if cls.foldable:
                row["folded"] = bank in folded
            elif cls.cls == CLASS_EXCLUDED or bank in journal.skips:
                state = await self.bank_state(bank)
                row["counts"] = state["counts"]
            if bank in journal.skips and not cls.foldable:
                counts = row.get("counts") or {}
                skipped.append(
                    {
                        "bank": bank,
                        "class": cls.cls,
                        "facts": int(counts.get("facts", 0)),
                        "documents": int(counts.get("documents", 0)),
                    }
                )
            blocker = _blocker(row, skips=journal.skips)
            if blocker is not None:
                out.append(blocker)
        journal.skipped = skipped
        return out

    async def _deleted_bank_blockers(
        self, journal: Journal, ledger: Mapping[str, Any], on_engine: set[str]
    ) -> list[dict[str, str]]:
        """A blocker for each bank an earlier run sent from that is gone, while its copies stay.

        A bank deleted from the engine after a run folded it (or sent part of
        it) is not listed by the engine any more, so nothing else would look
        at it. Its whole content is gone, so every copy it sent that the
        target still holds would stay readable there after the cutover: each
        part goes through the "gone" check of :meth:`_clashes` with nothing
        left to send. Its ids go to :data:`LEFT_IDS_NAME`.
        """
        out: list[dict[str, str]] = []
        for bank, row in sorted((ledger.get("banks") or {}).items()):
            if bank in on_engine or not isinstance(row, dict):
                continue
            files = {
                k: [str(p) for p in v]
                for k, v in (row.get("sent_files") or {}).items()
                if isinstance(v, list) and v
            }
            if not files:
                continue
            ghost = BankEntry(bank=bank, cls=str(row.get("class") or ""), prior_files=files)
            for name in _PARTS:
                if not files.get(name):
                    continue
                target = journal.targets[name]
                try:
                    await self._clashes(journal, ghost, name, target, {})
                except BankLeft as exc:
                    note = exc.message
                    if exc.details:
                        path = self._record_left_ids(journal, bank, exc.details)
                        note += (
                            f". The ids are listed in {path}" if path and path not in note else ""
                        )
                    records = ", ".join(files[name])
                    out.append(
                        {
                            "bank": bank,
                            "reason": (
                                f"deleted from the engine after an earlier run sent it to "
                                f"{target}, whose copies of it are still there: {note}"
                            ),
                            "fix": (
                                f"delete those copies from {target}; what was sent is recorded in "
                                f"{records}"
                            ),
                        }
                    )
                    break
        return out

    async def _end_without_cutover(self, journal: Journal) -> dict[str, Any]:
        """End a run that does not cut over: engine back, journal retired, then writes.

        The layout record is not touched, hal0-api is not restarted (it never
        stopped reading the old layout), and writes resume to the old banks.
        The ledger keeps what this run folded for the next one.
        """
        self._hook("before_release")
        embedder = self._embedder(journal)
        try:
            if journal.isolated or self._isolated_port() is not None:
                self._say("returning the engine to its normal address")
                await self._start_engine(
                    embedder, port=None, run_id=journal.run_id, journal=journal
                )
                journal.isolated = False
                self._save(journal)
            self._hook("released")
        except FoldInError as exc:
            journal.error = exc.message
            self._save(journal)
            exc.resumable = True
            raise
        journal.phase = PHASE_INCOMPLETE
        self._retire_journal(journal)
        self._hook("retired")
        write_gate.lift_gate()
        self._say("memory writes resumed on the old layout")
        left = [e.bank for e in journal.banks if e.step == STEP_LEFT]
        retry = [*left, *journal.pending]
        # The run's --map and --skip go into the command as well: later runs
        # read them from the ledger too, but the command shows what it relies on.
        choices = "".join(
            f" --map {shlex.quote(f'{b}={h}')}" for b, h in sorted(journal.maps.items())
        ) + "".join(f" --skip {shlex.quote(b)}" for b in journal.skips)
        return {
            **self._result(journal, embedder),
            "status": "incomplete",
            "cut_over": False,
            "left": left,
            "pending": list(journal.pending),
            "blocking": list(journal.blocking),
            "retry": f"sudo {NAMES.command} --apply"
            + "".join(f" --bank {shlex.quote(b)}" for b in retry)
            + choices,
        }

    def _result(self, journal: Journal, embedder: Embedder) -> dict[str, Any]:
        return {
            "run_id": journal.run_id,
            "targets": journal.targets,
            "embedder": describe_embedder(*embedder),
            "banks": [
                {
                    "bank": e.bank,
                    "class": e.cls,
                    "step": e.step,
                    "refold": e.refold,
                    "counts": e.counts,
                    "parts": e.parts,
                    "report": e.report,
                    "note": e.note,
                }
                for e in journal.banks
            ],
            "folded": [e.bank for e in journal.banks if e.step == STEP_FOLDED],
            "archive_dir": journal.archive_dir,
        }

    async def _cutover(self, journal: Journal) -> dict[str, Any]:
        """Close the run: engine back, layout record two_bank, hal0-api restarted, then writes.

        Reached only when every old bank is folded (:meth:`_finish`).
        """
        journal.phase = PHASE_CUTTING_OVER
        self._save(journal)
        self._hook("before_cutover")
        embedder = self._embedder(journal)
        try:
            if journal.isolated or self._isolated_port() is not None:
                self._say("returning the engine to its normal address")
                await self._start_engine(
                    embedder, port=None, run_id=journal.run_id, journal=journal
                )
                journal.isolated = False
                self._save(journal)
            self._hook("released")
            ledger = load_ledger()
            folded = [
                {
                    "bank": bank,
                    "counts": row.get("counts"),
                    "last_document_at": row.get("last_document_at"),
                    "run_id": row.get("run_id"),
                }
                for bank, row in sorted((ledger.get("banks") or {}).items())
                if row.get("state") == LEDGER_FOLDED
            ]
            layout = bank_layout.BankLayout(
                layout=bank_layout.LAYOUT_TWO_BANK, source=bank_layout.SOURCE_FOLD_IN
            )
            self._save_layout(
                layout,
                {
                    "run_id": journal.run_id,
                    "platform_bank": journal.platform_bank,
                    "folded": folded,
                    "left": [],
                },
            )
            self._hook("recorded")
            self._say("restarting hal0-api on the two banks")
            restarted = await asyncio.to_thread(self._restart_api)
            if restarted.get("error"):
                raise FoldInError(
                    f"the layout record says two_bank, but hal0-api could not be restarted "
                    f"({restarted['error']}); memory writes stay paused. Run `systemctl "
                    f"restart {_API_SERVICE}`, then `hal0 memory migrate fold-in --resume`",
                    resumable=True,
                )
        except FoldInError as exc:
            journal.error = exc.message
            self._save(journal)
            exc.resumable = True
            raise
        journal.phase = PHASE_COMPLETE
        self._retire_journal(journal)
        self._hook("retired")
        write_gate.lift_gate()
        self._say("memory writes resumed")
        return {
            **self._result(journal, embedder),
            "status": "folded",
            "cut_over": True,
            "left": [],
            "pending": [],
            "folded_all": [f["bank"] for f in folded],
            "skipped": list(journal.skipped),
            "skipped_note": SKIPPED_NOTE if journal.skipped else None,
        }

    # ── a pause left behind ─────────────────────────────────────────────

    def clear_gate(self) -> dict[str, Any]:
        """Lift a ``fold_in`` pause that a finished run left behind (``--clear-gate``).

        For a run stopped between retiring its journal and lifting the pause:
        the journal is retired, so ``--resume`` has nothing to carry on and
        the preflight refuses while the pause is set. Root only.
        """
        with self._lock():
            return self._clear_stuck_gate()

    def _clear_stuck_gate(self) -> dict[str, Any]:
        """Lift the pause when it is the fold-in's, no run is unfinished and the newest one finished.

        Refuses, changing nothing, unless: run as root; the pause's reason is
        ``fold_in``; no fold-in journal or mirror is unfinished (or
        unreadable); the engine is at its normal address; and the newest
        fold-in mirror under ``backups/`` is the paused run's, and finished.
        """
        if not self._privileged():
            raise FoldInRefused("run this as root (sudo)")
        gate = write_gate.read_gate()
        if gate is None or gate.get("reason") != write_gate.REASON_FOLD_IN:
            raise FoldInRefused(
                "memory writes are not paused by a fold-in"
                + (f" (the pause is {gate.get('reason')}'s)" if gate else "")
            )
        found = locate_journal()
        if found["journal"] is not None:
            raise FoldInRefused(
                f"fold-in run {found['journal'].run_id} is unfinished; run "
                f"`sudo {NAMES.command} --resume`, which lifts the pause when it ends"
            )
        if found["error"]:
            raise FoldInRefused(
                f"a fold-in journal exists but cannot be used ({found['error']}), so an "
                f"unfinished run cannot be ruled out; see `{NAMES.command} --status`"
            )
        if self._isolated_port() is not None:
            raise FoldInRefused(
                f"the memory engine is still isolated ({isolation_drop_in_path()} exists); a "
                "finished run returns it first, so this one did not finish"
            )
        newest: Journal | None = None
        try:
            mirrors = sorted(em.archive_root().glob(f"{NAMES.archive_prefix}*/{NAMES.mirror}"))
        except OSError:
            mirrors = []
        for path in mirrors:
            journal, _why = em.read_journal_file(path, _parse_journal)
            if journal is not None and (newest is None or journal.started_at > newest.started_at):
                newest = journal
        if newest is None or not _finished(newest) or newest.run_id != gate.get("run_id"):
            raise FoldInRefused(
                f"the pause names run {gate.get('run_id')}, but the newest fold-in journal under "
                f"{em.archive_root()} is "
                + (f"run {newest.run_id}, phase {newest.phase}" if newest else "missing")
                + "; nothing shows the paused run finished, so the pause stays"
            )
        write_gate.lift_gate()
        log.warning("hal0.memory.fold_in_gate_cleared", run_id=gate.get("run_id"))
        self._say(f"lifted the pause run {newest.run_id} ({newest.phase}) left behind")
        return {
            **self._result(newest, self._embedder(newest)),
            "status": "gate_cleared",
            "phase": newest.phase,
            "cut_over": newest.phase == PHASE_COMPLETE,
        }

    # ── status ──────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Where a fold-in stands, read from disk only: journal, pause, isolation, layout."""
        found = locate_journal()
        journal: Journal | None = found["journal"]
        try:
            record = bank_layout.load_fold_in_record()
        except bank_layout.LayoutRecordError as exc:
            record = {"error": str(exc)}
        try:
            ledger: dict[str, Any] = load_ledger()
        except FoldInError as exc:
            ledger = {"error": exc.message, "banks": {}}
        earlier = ledger.get("banks") or {}
        # Until the cutover, a later run reads what earlier runs sent from the
        # ``.ids.json`` files in their archive directories (:meth:`_prior`):
        # without them it refuses those banks. So they are named here.
        needed: list[str] = []
        if not record or record.get("error"):
            dirs = {
                str(Path(path).parent)
                for row in earlier.values()
                if isinstance(row, dict)
                for paths in (row.get("sent_files") or {}).values()
                for path in paths or ()
            }
            if journal is not None:
                dirs.add(journal.archive_dir)
            needed = sorted(dirs)
        out: dict[str, Any] = {
            "journal": journal.to_dict() if journal is not None else None,
            "journal_source": found["source"],
            "journal_error": found["error"],
            "gate": write_gate.read_gate(),
            "lock_held": lock_held(),
            "private_port": self._isolated_port(),
            "record": record,
            "ledger": {
                bank: {k: row.get(k) for k in ("state", "run_id", "note")}
                for bank, row in sorted(earlier.items())
            },
            "ledger_error": ledger.get("error"),
            "archives_needed": needed,
            "summary": "",
        }
        if journal is None:
            folded = sorted(b for b, r in earlier.items() if r.get("state") == LEDGER_FOLDED)
            left = sorted(b for b, r in earlier.items() if r.get("state") == LEDGER_LEFT)
            if found["error"]:
                out["summary"] = "the journal cannot be used and no mirror was found"
            elif out["gate"] is not None or out["private_port"] is not None:
                out["summary"] = (
                    "memory writes are paused or the engine is on a private port, but there is "
                    "no fold-in journal"
                )
            elif record:
                out["summary"] = (
                    f"no fold-in is in progress; the store is on the two banks, "
                    f"{len(record.get('folded') or [])} old bank(s) folded in"
                )
            elif earlier:
                out["summary"] = (
                    f"no fold-in is in progress; the store is still on the old layout. Earlier "
                    f"runs folded {len(folded)} bank(s) and left {len(left)}"
                    + (f" ({', '.join(left[:5])})" if left else "")
                    + f": `sudo {NAMES.command} --apply` folds the rest and cuts over once "
                    "every bank is folded"
                )
            else:
                out["summary"] = "no fold-in is in progress"
            return out
        steps: dict[str, int] = {}
        for entry in journal.banks:
            steps[entry.step] = steps.get(entry.step, 0) + 1
        out["summary"] = (
            f"run {journal.run_id} stopped in phase {journal.phase} ("
            + ", ".join(f"{n} {s}" for s, n in steps.items())
            + "): --resume carries it on bank by bank, then cuts over to the two banks if "
            "every bank is folded"
        )
        return out


__all__ = [
    "CLASS_CODING",
    "CLASS_EXCLUDED",
    "CLASS_PLATFORM",
    "CLASS_PRIVATE",
    "CLASS_PROJECT",
    "CLASS_SCRATCH",
    "CLASS_TARGET",
    "CLASS_UNCLASSIFIED",
    "DEFAULT_OWNERS",
    "HOOK_POINTS",
    "NAMES",
    "PSEUDO_OWNER_PREFIX",
    "BankClass",
    "BankEntry",
    "BankLeft",
    "FoldIn",
    "FoldInError",
    "FoldInRefused",
    "Journal",
    "Part",
    "RewritePlan",
    "RewriteResult",
    "analyse_listing",
    "blocking_reason",
    "classify_bank",
    "direct_engine_clients",
    "document_digest",
    "engine_operations_message",
    "interrupted_fold_in",
    "isolated_port",
    "isolation_drop_in_path",
    "journal_path",
    "ledger_path",
    "load_journal",
    "load_ledger",
    "locate_journal",
    "lock_held",
    "lock_path",
    "model_id_for",
    "observation_digest",
    "private_archive_problems",
    "pseudo_owner",
    "resolve_owner",
    "rewrite_archive",
    "routing_problems",
    "shared_archive_problems",
    "target_is_old_bank_message",
]
