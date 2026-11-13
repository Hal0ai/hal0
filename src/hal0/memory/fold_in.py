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
  folded page always gets a new id.

Per bank, one at a time (:meth:`FoldIn._fold`): export it into the run's
archive directory; rewrite the archive into a shared part and a private part
(:func:`rewrite_archive`, pure); check that the shared part holds no private
fact (:func:`shared_archive_problems`); merge each part into its target; and
compare each target with what it held before plus what the part carries.
A bank whose counts do not match is ``left``: it stays exactly as it was, a
readable old bank, and the run carries on with the next. A partial merge is
not undone (the engine has a delete per document, but nothing here deletes);
a re-import with ``skip`` completes it.

When every bank is folded or left, the cutover (:meth:`FoldIn._cutover`)
returns the engine to its normal address, writes the layout record as
``two_bank`` with the folded banks, restarts hal0-api so it reads the record,
retires the journal and only then lifts the write gate.

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
import hashlib
import io
import json
import os
import re
import secrets
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
PHASE_COMPLETE = "complete"
PHASE_ABANDONED = "abandoned"
_PHASES = (PHASE_PAUSED, PHASE_FOLDING, PHASE_CUTTING_OVER, PHASE_COMPLETE, PHASE_ABANDONED)
_FINISHED_PHASES = (PHASE_COMPLETE, PHASE_ABANDONED)

PART_SHARED = "shared"
PART_PRIVATE = "private"
_PARTS = (PART_SHARED, PART_PRIVATE)

#: Where ``hook`` is called. ``<bank>`` is the bank's id, ``<part>`` shared or private.
HOOK_POINTS: tuple[str, ...] = (
    "paused",
    "isolated",
    "exported:<bank>",
    "rewritten:<bank>",
    "importing:<bank>:<part>",
    "imported:<bank>:<part>",
    "folded:<bank>",
    "before_cutover",
    "released",
    "recorded",
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
        return BankClass(bank, CLASS_EXCLUDED, reason="a temporary bank of an embedder switch")
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
    candidate: str | None, *, owners: Mapping[str, str], known: Iterable[str]
) -> tuple[str, str]:
    """``(owner, source)`` for the harness or agent that wrote a fact (operator decision 1).

    ``owners`` is the harness map (``--owner``, then :data:`DEFAULT_OWNERS`),
    ``known`` the bridge clients that hold the memory scope. ``source`` is
    ``map``, ``client`` or ``pseudo``.
    """
    name = (candidate or "").strip().lower()
    if not name or name in (UNKNOWN_AGENT, "anonymous"):
        return pseudo_owner(UNOWNED), "pseudo"
    if name in owners:
        return owners[name], "map"
    if name in set(known):
        return name, "client"
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
    #: Document and mental-model ids per part, for the check after the import.
    ids: dict[str, dict[str, list[str]]]


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
        candidate = bank.agent or _id_owner(doc_id) or doc_agent
        if bank.coding:
            owner, source = resolve_owner(
                candidate or bank.harness, owners=plan.owners, known=plan.known_clients
            )
        elif candidate and candidate.lower() not in (UNKNOWN_AGENT, "anonymous"):
            owner, source = candidate, "agent"
        else:
            owner, source = pseudo_owner(UNOWNED), "pseudo"
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
        return _DocRoute(PART_PRIVATE, owner, source, candidate, new_id, slug, bank.coding)
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
        return {
            "manifest": manifest if isinstance(manifest, dict) else {},
            "documents": documents,
            "observations": rows("observations.json"),
            "mental_models": rows("mental_models.json"),
            "knowledge_pages": rows("knowledge_pages.json"),
            "directives": rows("directives.json"),
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
        },
        "mental_models": {
            "carried": 0,
            "renamed": 0,
            "dropped_private": 0,
            "content_cleared": 0,
        },
        "knowledge_pages": {"carried": 0, "dropped": 0},
        "directives_not_carried": [],
        "private_facts": 0,
        "ownerless_facts": 0,
        "owners": {},
        "projects": {},
        "document_ids_rewritten": 0,
        "document_ids_digested": 0,
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
    sides, or under two owners, is dropped and counted. Mental models go to
    the shared side only, re-keyed for a per-repository bank
    (:func:`model_id_for`) and tagged with its project; a bank that held
    private facts gives them without what they generated. Knowledge pages
    follow their models. Directives are not carried (a merge never imports
    them); they are listed in the report.

    Raises :class:`BankLeft` when the shared archive it built would hold
    anything private (:func:`shared_archive_problems`) or a private fact has
    no owner (:func:`private_archive_problems`).
    """
    archive = _read_archive(data)
    report = _new_report()
    parts: dict[str, list[dict[str, Any]]] = {PART_SHARED: [], PART_PRIVATE: []}
    routes: dict[str, _DocRoute] = {}
    fact_tags: dict[str, list[list[str]]] = {}
    held_private = False

    for doc in archive["documents"]:
        route = _route_document(plan, doc)
        old_id = str(doc.get("id") or "")
        routes[old_id] = route
        held_private = held_private or route.part == PART_PRIVATE
        out = dict(doc)
        out["id"] = route.new_id
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
        out["facts"] = new_facts
        fact_tags[old_id] = tag_rows
        parts[route.part].append(out)
        report["documents"][route.part] += 1
        report["facts"][route.part] += len(new_facts)
        if route.part == PART_PRIVATE:
            report["private_facts"] += len(new_facts)
        _tally_owner(report, route, len(new_facts))

    observations: dict[str, list[dict[str, Any]]] = {PART_SHARED: [], PART_PRIVATE: []}
    for obs in archive["observations"]:
        sources = obs.get("sources") or []
        located = [routes.get(str(s.get("document_id"))) for s in sources]
        if not sources or any(r is None for r in located):
            report["observations"]["dropped_unrouted"] += 1
            continue
        sides = {(r.part, r.owner if r.part == PART_PRIVATE else None) for r in located if r}
        if len(sides) != 1:
            report["observations"]["dropped_split"] += 1
            continue
        part, owner = next(iter(sides))
        new_sources = []
        projects: list[str] = []
        for source, route in zip(sources, located, strict=True):
            assert route is not None
            new_sources.append({**source, "document_id": route.new_id})
            index = source.get("fact_index")
            rows = fact_tags.get(str(source.get("document_id")), [])
            if isinstance(index, int) and 0 <= index < len(rows):
                projects.extend(_project_tags(rows[index]))
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
        report["observations"][part] += 1

    models: list[dict[str, Any]] = []
    renamed: dict[str, str] = {}
    prefix = _model_prefix(plan.bank)
    held_private = held_private or bool(observations[PART_PRIVATE])
    for row in archive["mental_models"]:
        if not isinstance(row, dict):
            continue
        if plan.bank.cls == CLASS_PRIVATE or any(_is_private_tag(t) for t in row.get("tags") or ()):
            report["mental_models"]["dropped_private"] += 1
            continue
        model = dict(row)
        old = str(model.get("id"))
        if prefix is not None:
            model["id"] = model_id_for(prefix, old)
            report["mental_models"]["renamed"] += 1
        renamed[old] = str(model["id"])
        tags = [str(t) for t in model.get("tags") or () if isinstance(t, str)]
        if plan.bank.project and f"{PROJECT_TAG_PREFIX}{plan.bank.project}" not in tags:
            tags.append(f"{PROJECT_TAG_PREFIX}{plan.bank.project}")
        model["tags"] = tags
        if held_private:
            for key in _GENERATED_MODEL_KEYS:
                if key in model:
                    model[key] = [] if key == "history" else ("" if key == "content" else None)
            report["mental_models"]["content_cleared"] += 1
        models.append(model)
        report["mental_models"]["carried"] += 1

    pages = _carried_pages(archive["knowledge_pages"], renamed, report)
    report["directives_not_carried"] = [
        str(d.get("name") or d.get("id") or "(unnamed)")
        for d in archive["directives"]
        if isinstance(d, dict)
    ]

    bank_id = plan.bank.bank
    shared = (
        _write_archive(bank_id, parts[PART_SHARED], observations[PART_SHARED], models, pages)
        if parts[PART_SHARED] or models
        else None
    )
    private = (
        _write_archive(bank_id, parts[PART_PRIVATE], observations[PART_PRIVATE], [], [])
        if parts[PART_PRIVATE]
        else None
    )
    problems = shared_archive_problems(shared) if shared is not None else []
    problems += private_archive_problems(private) if private is not None else []
    if problems:
        raise BankLeft(
            f"bank {bank_id}: the rewritten archives failed their safety check: "
            + "; ".join(problems[:5]),
            bank=bank_id,
            details={"problems": problems},
        )
    expected = {
        PART_SHARED: _counts_of(parts[PART_SHARED], observations[PART_SHARED], models),
        PART_PRIVATE: _counts_of(parts[PART_PRIVATE], observations[PART_PRIVATE], []),
    }
    ids = {
        PART_SHARED: {
            "documents": [str(d["id"]) for d in parts[PART_SHARED]],
            "mental_models": [str(m["id"]) for m in models],
        },
        PART_PRIVATE: {"documents": [str(d["id"]) for d in parts[PART_PRIVATE]]},
    }
    return RewriteResult(shared, private, expected, report, ids)


def _carried_pages(
    pages: list[Any], renamed: Mapping[str, str], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Pages whose model is carried (re-pointed at its new id), and the folders above them."""
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
    for page in rows:
        if str(page.get("id")) not in keep:
            report["knowledge_pages"]["dropped"] += 1
            continue
        copy = dict(page)
        if copy.get("mental_model_id"):
            copy["mental_model_id"] = renamed[str(copy["mental_model_id"])]
        out.append(copy)
        report["knowledge_pages"]["carried"] += 1
    return out


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
    plan: RewritePlan, documents: list[Mapping[str, Any]], facts: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """What :func:`rewrite_archive` would report for a bank, from its listings (pure).

    The dry run writes nothing, not even an export, so it reads the bank's
    document and fact listings instead and routes them by the same rules.
    Observations and mental models are counted by :meth:`FoldIn.bank_state`.
    """
    by_doc: dict[str, dict[str, Any]] = {}
    for row in documents:
        doc_id = str(row.get("id") or "")
        by_doc[doc_id] = {"id": doc_id, "tags": list(row.get("tags") or []), "facts": []}
    for fact in facts:
        doc_id = str(fact.get("document_id") or "")
        doc = by_doc.setdefault(doc_id, {"id": doc_id, "tags": [], "facts": []})
        doc["facts"].append({"tags": list(fact.get("tags") or [])})
    report = _new_report()
    for doc in by_doc.values():
        route = _route_document(plan, doc)
        report["documents"][route.part] += 1
        report["facts"][route.part] += len(doc["facts"])
        if route.new_id != doc["id"]:
            report["document_ids_rewritten"] += 1
        if route.digested:
            report["document_ids_digested"] += 1
        if route.part == PART_PRIVATE:
            report["private_facts"] += len(doc["facts"])
        _tally_owner(report, route, len(doc["facts"]))
        for fact in doc["facts"]:
            resolution = _fact_resolution(plan, doc, _caller_tags(fact["tags"]))
            tags, _ = _retag(plan, route, fact["tags"], resolution, None)
            _tally_projects(report, tags)
    return report


# ── the journal ──────────────────────────────────────────────────────────


@dataclass
class Part:
    """One of a bank's two archives and its import into its target."""

    target: str
    archive: str
    expected: dict[str, int]
    #: The target's counts just before this part was first sent.
    before: dict[str, int] | None = None
    #: True once the import was sent (saved before the request).
    sent: bool = False
    #: True once it was sent a second time (a resume), which changes how it is checked.
    resent: bool = False
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
    archive: str | None = None
    step: str = STEP_PLANNED
    parts: dict[str, dict[str, Any]] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    last_document_at: str | None = None
    note: str | None = None
    #: Parts an earlier run sent and did not finish (a bank it left): their
    #: documents may already be in the target, so they are not a clash.
    prior_sent: list[str] = field(default_factory=list)
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
        """The parts sent to their target in this run or an earlier one."""
        sent = {n for n, raw in self.parts.items() if raw.get("sent")}
        return sorted(sent | set(self.prior_sent))


@dataclass
class Journal:
    """The state of one run, on disk from the pause to the cutover."""

    run_id: str
    started_at: str
    pid: int
    phase: str
    archive_dir: str
    embedder: dict[str, str]
    targets: dict[str, str]
    owners: dict[str, str] = field(default_factory=dict)
    maps: dict[str, str] = field(default_factory=dict)
    #: The old layout's platform bank, kept in the record for a later ``--bank`` retry.
    platform_bank: str | None = None
    #: True for a run that retries banks on a store already on the two banks.
    retry: bool = False
    allow_pending: bool = False
    private_port: int | None = None
    isolated: bool = False
    engine_stopped: bool = False
    #: True once any import was sent: from then on the run is carried on, never abandoned.
    sent: bool = False
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
            "journal; see `hal0 memory migrate fold-in --status`"
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


def _archive_name(index: int, bank: str, part: str) -> str:
    return f"{index:03d}-{re.sub(r'[^A-Za-z0-9._-]', '_', bank)[:80]}.{part}.zip"


class FoldIn(em.EngineMaintenance):
    """One run of the fold-in against one engine.

    The engine session, its isolation and identity checks, the queue waits,
    the export and the journal on disk are
    :class:`~hal0.memory.engine_maintenance.EngineMaintenance`'s, run under
    :data:`NAMES`; this class is the fold-in's plan and sequence.

    ``owners`` is the operator's ``--owner HARNESS=CLIENT`` map, ``maps``
    ``--map BANK=HARNESS``, ``only`` the banks of ``--bank``. Host seams
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
        self._maps = dict(maps or {})
        self._only = list(dict.fromkeys(only))
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

    def _rewrite_plan(self, entry: BankEntry | BankClass, journal: Journal | None) -> RewritePlan:
        from hal0.memory.projects import registry_from_config

        memory_cfg = self._memory_config()
        owners = {**DEFAULT_OWNERS, **(journal.owners if journal else self._owners)}
        compat = getattr(memory_cfg, "compat", None)
        prefixes = tuple(getattr(compat, "shared_document_prefixes", None) or ())
        try:
            known = frozenset(self._known_clients())
        except Exception as exc:  # the store cannot be read: owners fall back, never fail
            log.warning("hal0.memory.fold_in_clients_unread", error=str(exc))
            known = frozenset()
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
            "totals": dict.fromkeys(em.COUNT_KEYS, 0),
            "disk": None,
            "estimate_s": None,
            "direct_clients": [],
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
        if gate is not None and switch is None:
            refuse(
                "gate_set",
                f"memory writes are already paused (reason {gate.get('reason')}, run "
                f"{gate.get('run_id')})",
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
        refusals.extend(em.unit_refusals(NAMES, self._unit_text(), self._normal_port()))

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
        folded_before: set[str] = set()
        recorded_platform: Any = None
        if layout.two_bank:
            if not self._only:
                refuse(
                    "already_two_bank",
                    "the store is already on the two banks; to retry a bank that was left "
                    "over, run with --bank <id>",
                )
            try:
                record = bank_layout.load_fold_in_record() or {}
            except bank_layout.LayoutRecordError as exc:
                refuse("layout_undecided", f"the layout record cannot be read: {exc}")
                return plan
            folded_before = {str(b.get("bank")) for b in record.get("folded") or []}
            recorded_platform = record.get("platform_bank")
            plan["sent_before"] = {
                str(b.get("bank")): list(b.get("sent_parts") or [])
                for b in record.get("left") or []
            }
        platform = (
            layout.legacy_platform_bank
            or (recorded_platform if isinstance(recorded_platform, str) else None)
            or bank_layout.LEGACY_PLATFORM_BANK
        )
        plan["platform_bank"] = platform
        if platform in targets.values():
            refuse(
                "target_is_old_bank",
                f"[memory] names {platform}, the old platform bank, as a target. Set [memory] "
                f'bank = "{bank_layout.TWO_BANK_PLATFORM_BANK}" (or remove the key), then run '
                "the fold-in again",
            )

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
                    bank, platform_bank=platform, targets=targets.values(), maps=self._maps
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
                if self._only and bank not in self._only:
                    row["action"] = "none"
                    row["reason"] = "not named with --bank"
                elif bank in folded_before:
                    row["action"] = "none"
                    row["reason"] = "folded by an earlier run"
                if row["action"] == "fold" or cls.cls == CLASS_UNCLASSIFIED:
                    state = await self.bank_state(bank)
                    if state["pending"] or state["processing"]:
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
                            row["fold"] = await self._analyse(cls)
                plan["banks"].append(row)
        except FoldInError as exc:
            refuse("engine_error", exc.message)
            return plan
        for missing in [b for b in self._only if b not in ids]:
            refuse("no_such_bank", f"--bank {missing}: the engine has no such bank")
        for row in plan["banks"]:
            named = row["bank"] in self._only
            if row["class"] == CLASS_UNCLASSIFIED and (named or not self._only):
                refuse(
                    "unclassified_bank",
                    f"bank {row['bank']} ({row.get('counts', {}).get('facts', 0)} facts) is "
                    f"{row['reason']}. Nothing is folded while a bank is left unaccounted for",
                )
            elif named and row["action"] == "none":
                refuse(
                    "not_foldable",
                    f"--bank {row['bank']}: {row['reason'] or 'not a bank the fold-in moves'}",
                )
        busy = [
            b
            for b in plan["banks"]
            if (b.get("pending") or b.get("processing")) and b["bank"] not in engine_work
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
                refuse(
                    "engine_operations",
                    f"the engine's own work did not finish: {listing}. Let it finish, then run "
                    "the fold-in again",
                )
            else:
                warnings.append(
                    f"the engine is running its own work: {listing}. --apply waits up to "
                    f"{self.t.engine_ops_s:g}s for it"
                )
        if not any(b["action"] == "fold" for b in plan["banks"]) and not refusals:
            refuse("nothing_to_fold", "there is no old bank to fold in")

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

    async def _analyse(self, cls: BankClass) -> dict[str, Any]:
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
        return analyse_listing(
            self._rewrite_plan(cls, None),
            [d for d in documents if isinstance(d, dict)],
            [f for f in facts if isinstance(f, dict)],
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
                retry=(plan["layout"] or {}).get("layout") == bank_layout.LAYOUT_TWO_BANK,
                private_port=self._pick_port(),
                banks=[
                    BankEntry(
                        bank=row["bank"],
                        cls=row["class"],
                        harness=row["harness"],
                        agent=row["agent"],
                        project=row["project"],
                        tag_project=row["bank"] in TAG_PROJECT_CODING_BANKS
                        or row["bank"] in self._maps,
                        prior_sent=list((plan.get("sent_before") or {}).get(row["bank"], [])),
                    )
                    for row in plan["banks"]
                    if row["action"] == "fold"
                ],
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
            return await self._cutover(journal)

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
                raise FoldInRefused("there is no interrupted fold-in to resume")
            if not self._privileged():
                raise FoldInRefused("run this as root (sudo)")
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
            except FoldInError as exc:
                journal.error = exc.message
                with contextlib.suppress(OSError):
                    self._save(journal)
                exc.resumable = True
                raise
            return await self._cutover(journal)

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
        write_gate.lift_gate()

    async def _fold_all(self, journal: Journal) -> None:
        for index, entry in enumerate(journal.banks):
            if entry.step in _DONE_STEPS:
                continue
            self._say(f"folding in {entry.bank} ({index + 1}/{len(journal.banks)})")
            try:
                await self._fold(journal, entry, index)
            except BankLeft as exc:
                entry.step = STEP_LEFT
                entry.note = exc.message
                self._save(journal)
                self._say(f"{entry.bank} is left as it was: {exc.message}")

    async def _fold(self, journal: Journal, entry: BankEntry, index: int) -> None:
        if entry.step == STEP_PLANNED:
            await self._export(journal, entry, index)
            self._hook(f"exported:{entry.bank}")
        if entry.step == STEP_EXPORTED:
            self._rewrite(journal, entry, index)
            self._hook(f"rewritten:{entry.bank}")
        if entry.step in (STEP_REWRITTEN, STEP_IMPORTING):
            for name in _PARTS:
                part = entry.part(name)
                if part is not None and not part.done:
                    await self._import_part(journal, entry, name, part)
            entry.step = STEP_IMPORTED
            self._save(journal)
        if entry.step == STEP_IMPORTED:
            entry.step = STEP_FOLDED
            self._save(journal)
            self._hook(f"folded:{entry.bank}")

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
        for name, blob in ((PART_SHARED, result.shared), (PART_PRIVATE, result.private)):
            if blob is None:
                continue
            file_name = _archive_name(index, entry.bank, name)
            self._write_archive_file(journal, file_name, blob)
            ids_name = file_name[: -len(".zip")] + ".ids.json"
            self._write_archive_file(journal, ids_name, json.dumps(result.ids[name]).encode())
            entry.put(
                name,
                Part(
                    target=journal.targets[name], archive=file_name, expected=result.expected[name]
                ),
            )
        entry.report = result.report
        entry.step = STEP_REWRITTEN
        self._save(journal)

    def _part_ids(self, journal: Journal, part: Part) -> dict[str, list[str]]:
        raw = self._read_archive_file(journal, part.archive[: -len(".zip")] + ".ids.json")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    async def _ensure_target(self, target: str) -> None:
        """Create a target bank the engine does not have yet (a merge imports into an existing bank)."""
        if target in await self._bank_ids():
            return
        await self._json(
            "PUT", em.bank_path(target), body={}, what="creating the bank", bank=target
        )

    async def _document_ids(self, bank: str) -> set[str]:
        rows = await self._list_all(
            f"{em.bank_path(bank)}/documents",
            key="items",
            what="listing documents",
            bank=bank,
            page_size=_CONTENT_PAGE,
        )
        return {str(r.get("id")) for r in rows if isinstance(r, dict)}

    async def _model_ids(self, bank: str) -> set[str]:
        rows = await self._list_all(
            f"{em.bank_path(bank)}/mental-models",
            {"detail": "content"},
            key="items",
            what="reading mental models",
            bank=bank,
        )
        return {str(r.get("id")) for r in rows if isinstance(r, dict)}

    async def _import_part(self, journal: Journal, entry: BankEntry, name: str, part: Part) -> None:
        """Merge one part into its target, then check the target holds all of it."""
        target = part.target
        data = self._read_archive_file(journal, part.archive)
        if name == PART_SHARED:
            problems = shared_archive_problems(data)
            if problems:
                raise BankLeft(
                    f"bank {entry.bank}: its shared archive holds private memory "
                    f"({'; '.join(problems[:3])}); it is not imported",
                    bank=entry.bank,
                )
        ids = self._part_ids(journal, part)
        await self._ensure_target(target)
        if part.sent:
            await self._await_part(journal, entry, part)
            if await self._part_problems(entry, part, ids, strict=not part.resent) == []:
                part.done = True
                entry.put(name, part)
                self._save(journal)
                return
            # Sent before, not all there: send again; ``skip`` leaves what landed alone.
            data = await self._remainder(data, target, ids)
            part.resent = True
        elif name in entry.prior_sent:
            # An earlier run sent this part and left the bank: what landed then
            # is in the target, so it is checked by presence, not by growth.
            state = await self._quiet_state(target)
            part.before = dict(state["counts"])
            data = await self._remainder(data, target, ids)
            part.resent = True
        else:
            state = await self._quiet_state(target)
            part.before = dict(state["counts"])
            present = await self._document_ids(target)
            clash = sorted(set(ids.get("documents", [])) & present)
            models = sorted(set(ids.get("mental_models", [])) & await self._model_ids(target))
            if clash or models:
                listing = ", ".join([*clash[:3], *models[:3]])
                raise BankLeft(
                    f"bank {entry.bank}: {target} already holds {len(clash)} of its document id(s) "
                    f"and {len(models)} of its mental-model id(s) ({listing}). A merge would skip "
                    "them and the bank would fold in only in part",
                    bank=entry.bank,
                    details={"documents": clash, "mental_models": models},
                )
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
        problems = await self._part_problems(entry, part, ids, strict=not part.resent)
        if problems:
            entry.put(name, part)
            self._save(journal)
            raise BankLeft(
                f"bank {entry.bank} did not fold into {target} whole: {'; '.join(problems)}",
                bank=entry.bank,
                details={"problems": problems},
            )
        part.done = True
        entry.put(name, part)
        self._save(journal)

    def _import_budget(self, part: Part) -> float:
        embedded = part.expected.get("facts", 0) + part.expected.get("observations", 0)
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
        later ``--bank`` run sends the part again with ``skip``. One still running
        after its budget stops the run, resumably.
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
                    f"`hal0 memory migrate fold-in --apply --bank {entry.bank}` sends it again",
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

    async def _remainder(self, data: bytes, target: str, ids: Mapping[str, list[str]]) -> bytes:
        """``data`` without the mental models (and their pages) the target already has.

        Documents need no filtering: ``skip`` leaves the ones that landed.
        Models are inserted ``ON CONFLICT DO NOTHING``, but a page is given a
        fresh id when another bank holds it, so pages that landed would land
        twice. When any model of the part landed, its pages are taken as landed.
        """
        present = await self._model_ids(target)
        if not present & set(ids.get("mental_models", [])):
            return data
        archive = _read_archive(data)
        models = [m for m in archive["mental_models"] if str(m.get("id")) not in present]
        return _write_archive(
            str(archive["manifest"].get("source_bank_id") or ""),
            archive["documents"],
            archive["observations"],
            models,
            [],
        )

    async def _part_problems(
        self, entry: BankEntry, part: Part, ids: Mapping[str, list[str]], *, strict: bool
    ) -> list[str]:
        """What is wrong with ``part``'s target after its import; empty when it folded in.

        Every document and mental model of the part must be in the target.
        The engine writes a document with its chunks and facts in one
        transaction, so a document that is there is there with its facts.
        After the first import the target must also have grown by exactly
        what the part carries (observations by at least as many); after a
        resend it must not have grown by more.
        """
        state = await self._quiet_state(part.target)
        problems: list[str] = []
        missing = sorted(set(ids.get("documents", [])) - await self._document_ids(part.target))
        if missing:
            problems.append(f"{len(missing)} document(s) missing ({', '.join(missing[:3])})")
        models = set(ids.get("mental_models", []))
        if models:
            gone = sorted(models - await self._model_ids(part.target))
            if gone:
                problems.append(f"{len(gone)} mental model(s) missing ({', '.join(gone[:3])})")
        before = part.before or dict.fromkeys(em.COUNT_KEYS, 0)
        delta = {k: int(state["counts"][k]) - int(before.get(k, 0)) for k in em.COUNT_KEYS}
        if strict:
            differs = em.diff(part.expected, delta, observation_floor=True)
            if differs:
                problems.append(
                    f"{part.target} grew by "
                    + em.diff_text(differs).replace("expected", "expected +")
                )
        else:
            over = [
                f"{k.replace('_', ' ')} +{delta[k]} (at most +{part.expected.get(k, 0)})"
                for k in em.HARD_COUNTS
                if delta[k] > int(part.expected.get(k, 0))
            ]
            if over:
                problems.append(
                    f"{part.target} grew by more than the part carries: {', '.join(over)}"
                )
        return problems

    async def _cutover(self, journal: Journal) -> dict[str, Any]:
        """Close the run: engine back, layout record two_bank, hal0-api restarted, then writes."""
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
            folded = [
                {
                    "bank": e.bank,
                    "counts": e.counts,
                    "last_document_at": e.last_document_at,
                    "run_id": journal.run_id,
                }
                for e in journal.banks
                if e.step == STEP_FOLDED
            ]
            left = [
                {
                    "bank": e.bank,
                    "note": e.note,
                    "run_id": journal.run_id,
                    "sent_parts": e.sent_parts(),
                }
                for e in journal.banks
                if e.step == STEP_LEFT
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
                    "left": left,
                },
            )
            self._hook("recorded")
            if not journal.retry:
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
        write_gate.lift_gate()
        self._say("memory writes resumed")
        return {
            "status": "folded" if not left else "folded_with_left",
            "run_id": journal.run_id,
            "targets": journal.targets,
            "embedder": describe_embedder(*embedder),
            "banks": [
                {
                    "bank": e.bank,
                    "class": e.cls,
                    "step": e.step,
                    "counts": e.counts,
                    "parts": e.parts,
                    "report": e.report,
                    "note": e.note,
                }
                for e in journal.banks
            ],
            "folded": [f["bank"] for f in folded],
            "left": [b["bank"] for b in left],
            "archive_dir": journal.archive_dir,
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
        out: dict[str, Any] = {
            "journal": journal.to_dict() if journal is not None else None,
            "journal_source": found["source"],
            "journal_error": found["error"],
            "gate": write_gate.read_gate(),
            "lock_held": lock_held(),
            "private_port": self._isolated_port(),
            "record": record,
            "summary": "",
        }
        if journal is None:
            if found["error"]:
                out["summary"] = "the journal cannot be used and no mirror was found"
            elif out["gate"] is not None or out["private_port"] is not None:
                out["summary"] = (
                    "memory writes are paused or the engine is on a private port, but there is "
                    "no fold-in journal"
                )
            elif record:
                out["summary"] = (
                    f"no fold-in is in progress; the last one folded "
                    f"{len(record.get('folded') or [])} bank(s) and left "
                    f"{len(record.get('left') or [])}"
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
            + "): --resume carries it on bank by bank, then cuts over to the two banks"
        )
        return out


__all__ = [
    "CLASS_CODING",
    "CLASS_EXCLUDED",
    "CLASS_PLATFORM",
    "CLASS_PRIVATE",
    "CLASS_PROJECT",
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
    "interrupted_fold_in",
    "isolated_port",
    "isolation_drop_in_path",
    "journal_path",
    "load_journal",
    "locate_journal",
    "lock_held",
    "lock_path",
    "model_id_for",
    "private_archive_problems",
    "pseudo_owner",
    "resolve_owner",
    "rewrite_archive",
    "shared_archive_problems",
]
