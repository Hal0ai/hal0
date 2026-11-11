"""The memory write envelope (ADR-0058, decisions 4 and 4a).

Every write that reaches the Hindsight engine goes through
:func:`apply_envelope` first. The envelope normalises the caller's tags; it
never rejects a write. It is applied by ``HindsightProvider`` only: the
volatile in-memory fallback provider stores tags as given.

Why it exists: the engine builds observation scopes from tag sets, so a tag
that changes on every call (a session id, a lifecycle word such as
``checkpoint``) turns each session into its own consolidation silo and starves
anything that reads by tag. The envelope keeps such values, but in document
metadata rather than in the tag space.

The rules, in the order they are applied to each caller tag:

1. A caller-supplied ``agent:`` tag is dropped; the resolved identity is
   stamped as ``agent:<id>``. An identity that cannot be resolved becomes
   :data:`UNKNOWN_AGENT`.
2. A ``kind:durable`` or ``kind:ephemeral`` tag is taken as the caller's
   choice of kind and re-emitted once, in canonical lower case. An explicit
   choice wins; if both are sent, ``ephemeral`` wins.
3. A session id (see :func:`session_id_of`) is removed from the tags and
   written to metadata: ``session_id`` when there is one, ``session_ids``
   (a list) when there are several.
4. A bare lifecycle word (:data:`LIFECYCLE_WORDS`) is removed from the tags
   and recorded in metadata as ``lifecycle``; with no kind sent it also makes
   the write ``kind:ephemeral``. The exception is a write the caller marked
   ``kind:durable`` (and not also ``kind:ephemeral``): the caller has said it
   is durable content, so the word is an ordinary topic tag and stays.
5. Every other tag passes through untouched. That includes the namespaces the
   system already uses (``visibility:``, ``source:``, ``knowledge:``,
   ``origin:``, ``harness:``, ``migrated:``) and unknown bare words, which are
   the user's own vocabulary.

Then the project (decision 4a). The write's project is decided by the
resolver (:func:`hal0.memory.projects.resolve_project`) and arrives as a
``ProjectResolution``; with none given, the envelope runs the resolver over
the caller's tags itself, against the shipped default registry. Every
``project:`` tag the caller sent is removed, in every case: a write carries
only the project tags the resolver produced, so never a denied name and never
a spelling the resolver would not write. The first rule that applies decides:

6. One resolved project is stamped as exactly one ``project:<slug>`` tag.
7. Two or more distinct projects, which only the caller's own tags can name,
   are stamped as one ``project:<slug>`` tag each, in the caller's order.
8. A project that was named and refused (the deny list, or a name with
   nothing left after normalising) is "no project", and rules 9 to 11 decide
   the rest.
9. A write from a coding client with no project gets ``project:unresolved``,
   whatever its kind, and no ``scope:global``.
10. A durable write with no project and no ``scope:`` tag of the caller's own
    gets ``scope:global``. "No project" and "global" are then different facts:
    the first is the absence of a tag, the second is a tag.
11. An ephemeral write with no project gets neither.

A ``scope:global`` tag the caller sent is kept, once, after the envelope's
own tags. A ``project:unresolved`` tag the caller sent is dropped: it is the
envelope's marker, stamped by rule 9 only. What the envelope decided is
recorded in metadata as ``project`` and ``project_source``, unless the caller
already set those keys.

The result always carries exactly one ``agent:<id>`` tag and exactly one of
``kind:durable`` / ``kind:ephemeral``.

The module also chooses the observation scope a write is sent with, from its
final tags (:func:`resolve_observation_scopes`): one scope per project when
the write carries a real ``project:`` tag, the one untagged scope when it does
not. ``project:unresolved`` is not a project there.

This module is pure: no I/O, no engine client, no provider state. The
``visibility:private`` stamp and its read-side enforcement (ADR-0005) stay in
:mod:`hal0.memory.hindsight_provider`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from hal0.memory.projects import (
    PROJECT_TAG_PREFIX,
    UNRESOLVED_SLUG,
    ProjectRegistry,
    ProjectResolution,
    resolve_project,
)

#: The ``agent:<id>`` a write gets when no identity could be resolved.
UNKNOWN_AGENT = "unknown"

#: The front-door sentinel for a missing ``X-hal0-Agent`` header. It is not an
#: identity, so it resolves to :data:`UNKNOWN_AGENT`.
ANONYMOUS_AGENT = "anonymous"

AGENT_TAG_PREFIX = "agent:"
KIND_TAG_PREFIX = "kind:"
SCOPE_TAG_PREFIX = "scope:"

#: The tag of a durable write that belongs to no project.
SCOPE_GLOBAL_TAG = f"{SCOPE_TAG_PREFIX}global"

#: The tag of a write from a coding client that named no usable project. It
#: marks the write and nothing more: it is not a project for observation
#: scoping, because every repository's unresolved writes would then
#: consolidate into one set of beliefs about no repository in particular.
PROJECT_UNRESOLVED_TAG = f"{PROJECT_TAG_PREFIX}{UNRESOLVED_SLUG}"

KIND_DURABLE = "durable"
KIND_EPHEMERAL = "ephemeral"
KINDS = frozenset({KIND_DURABLE, KIND_EPHEMERAL})

#: Bare tags that describe where in a session's life a write happened, not what
#: it is about. Matched stripped and case-insensitively, as whole tags only.
LIFECYCLE_WORDS = frozenset(
    {
        "checkpoint",
        "session-end",
        "pre-compress",
        "continuity",
        "bootstrap",
        "chat",
    }
)

#: Metadata keys the envelope writes.
SESSION_ID_KEY = "session_id"
SESSION_IDS_KEY = "session_ids"
LIFECYCLE_KEY = "lifecycle"
PROJECT_KEY = "project"
PROJECT_SOURCE_KEY = "project_source"

#: ``project_source`` values the envelope writes besides the resolver's own
#: sources (``field``, ``bank``, ``tag``, ``namespace``).
PROJECT_SOURCE_DENIED = "denied"
PROJECT_SOURCE_NONE = "none"

#: The observation scope of a write that carries no project, unless the caller
#: chose another. An engine ``RetainRequest`` item accepts ``"per_tag"``,
#: ``"combined"``, ``"all_combinations"``, ``"shared"`` or a list of tag lists;
#: ``"shared"`` consolidates into one untagged scope regardless of the item's
#: tags. See :func:`resolve_observation_scopes`.
DEFAULT_OBSERVATION_SCOPES = "shared"

#: The engine's own default mode: one scope made of all the item's tags.
COMBINED_OBSERVATION_SCOPES = "combined"

# ``session:<id>``, ``session_id:<id>``, ``session-id:<id>``, ``sessionid:<id>``.
# The id is everything after the first colon and must not be blank.
_SESSION_TAG_RE = re.compile(r"session(?:[-_]?id)?:(?P<id>.*\S.*)", re.IGNORECASE | re.DOTALL)

# A bare UUID in the canonical 8-4-4-4-12 form, any version and variant.
# A bare run of hexadecimal digits is not a session id at any length: an MD5
# checksum, a commit hash and a digest are all legitimate tags.
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class WriteEnvelope:
    """The outcome of :func:`apply_envelope`.

    ``tags`` is the final tag list. ``metadata`` holds only the keys the
    envelope adds; the caller merges it into the document's metadata.
    """

    tags: tuple[str, ...]
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


def coerce_tags(tags: Any) -> list[str]:
    """The caller's tags as a list of non-empty strings.

    ``None`` is no tags. A bare string is one tag, not a sequence of
    characters. Any other iterable yields one tag per item, with non-string
    items rendered by ``str``. Blank items are dropped. Nothing is rejected.
    """
    if tags is None:
        return []
    single = isinstance(tags, str) or not isinstance(tags, Iterable)
    items: Iterable[Any] = [tags] if single else tags
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item)
        if text.strip():
            out.append(text)
    return out


def resolve_agent(identity: str | None) -> str:
    """The id stamped as ``agent:<id>``: the identity, or :data:`UNKNOWN_AGENT`.

    A missing identity and the :data:`ANONYMOUS_AGENT` sentinel both resolve to
    :data:`UNKNOWN_AGENT`, so an absent identity can never match a real agent.
    """
    if not identity or identity == ANONYMOUS_AGENT:
        return UNKNOWN_AGENT
    return identity


def session_id_of(tag: str) -> str | None:
    """The session id a tag carries, or ``None`` when it is not a session tag.

    Two shapes count, each judged on the whole stripped tag, ignoring case:

    * ``session:<id>`` (also ``session_id:``, ``session-id:``, ``sessionid:``)
      with a non-blank id, which is returned without the prefix;
    * a bare UUID in the 8-4-4-4-12 form, any version.

    Any bare UUID counts, whatever it identifies: a tag cannot say whether a
    UUID names a session, a document or a request, and none of them belongs in
    the tag space. A bare run of hexadecimal digits without dashes does not
    count at any length.
    """
    text = tag.strip()
    prefixed = _SESSION_TAG_RE.fullmatch(text)
    if prefixed:
        return prefixed.group("id").strip()
    if _UUID_RE.fullmatch(text):
        return text
    return None


def _kind_of(tag: str) -> str | None:
    """``durable`` or ``ephemeral`` when the tag is that ``kind:`` tag."""
    folded = tag.strip().lower()
    if not folded.startswith(KIND_TAG_PREFIX):
        return None
    value = folded[len(KIND_TAG_PREFIX) :]
    return value if value in KINDS else None


def lifecycle_word_of(tag: str) -> str | None:
    """The lifecycle word a tag is, in lower case, or ``None``."""
    word = tag.strip().lower()
    return word if word in LIFECYCLE_WORDS else None


def resolve_observation_scopes(requested: Any, tags: Iterable[str]) -> Any:
    """The observation scope a write is sent with, given its final tags.

    A scope the caller chose stands. Otherwise:

    * a write that carries ``project:`` tags consolidates into one scope per
      project, ``[["project:<slug>"], ...]``, so what is learned in one
      project does not become a belief about another;
    * a write with no project consolidates into the one untagged scope,
      :data:`DEFAULT_OBSERVATION_SCOPES`.

    ``project:unresolved`` is not a project here. A write that carries it is
    scoped exactly as a write with no project tag would be: a scope of its own
    would pool the unresolved writes of every repository into one set of
    observations about none of them.

    The engine writes an explicit scope verbatim: the observation carries
    exactly the scope's tags, whatever other tags the write has. It reads a
    ``*`` in a scope entry as a glob, though, so a project tag containing one
    cannot be named literally; such a write falls back to
    :data:`COMBINED_OBSERVATION_SCOPES`, which keeps the project tag on the
    observation along with the write's other tags.

    A ``project:`` tag is recognised exactly as the read filter recognises it:
    a string that starts with the lower-case prefix.
    """
    if requested is not None:
        return requested
    projects = list(
        dict.fromkeys(
            tag
            for tag in tags
            if isinstance(tag, str)
            and tag.startswith(PROJECT_TAG_PREFIX)
            and tag != PROJECT_UNRESOLVED_TAG
        )
    )
    if not projects:
        return DEFAULT_OBSERVATION_SCOPES
    if any("*" in tag for tag in projects):
        return COMBINED_OBSERVATION_SCOPES
    return [[tag] for tag in projects]


def _is_project_tag(tag: str) -> bool:
    return tag.strip().lower().startswith(PROJECT_TAG_PREFIX)


def apply_envelope(
    tags: Any,
    *,
    agent: str | None,
    project_resolution: ProjectResolution | None = None,
    coding: bool = False,
    stamp_global: bool = True,
    metadata: Mapping[str, Any] | None = None,
) -> WriteEnvelope:
    """Normalise one write's tags. See the module docstring for the rules.

    ``agent`` is the resolved caller identity. ``project_resolution`` is the
    resolver's decision for this write
    (:func:`hal0.memory.projects.resolve_project`); with ``None`` the envelope
    resolves the caller's own ``project:`` tags against the shipped default
    registry, so a caller without a registry still gets resolver-made tags.
    Nothing is derived from free text, and a bare word is never rewritten into
    a ``project:`` tag. ``coding`` says the caller is a coding client, which gets
    ``project:unresolved`` when it has no project. ``stamp_global=False`` is
    for a write whose project is carried by something other than a tag (a
    per-project bank): it gets no ``scope:global``. ``metadata`` is the
    caller's metadata, read only to avoid overwriting a key the caller already
    set.
    """
    caller_metadata = metadata or {}
    caller_tags = coerce_tags(tags)

    # The caller's choice of kind is read first, because it decides what a
    # lifecycle word means wherever the two sit in the list. Between two
    # caller-supplied kinds the narrower claim wins: nothing the caller called
    # ephemeral is promoted. A write marked durable and nothing else keeps its
    # lifecycle words as ordinary topic tags.
    caller_kinds = {kind for kind in map(_kind_of, caller_tags) if kind is not None}
    explicitly_durable = caller_kinds == {KIND_DURABLE}

    kept: list[str] = []
    seen: set[str] = set()
    session_ids: list[str] = []
    lifecycle: list[str] = []
    caller_global = False

    for tag in caller_tags:
        if tag.strip().lower().startswith(AGENT_TAG_PREFIX) or _kind_of(tag) is not None:
            continue
        if tag.strip().lower() == SCOPE_GLOBAL_TAG:
            # Kept, but re-emitted once after the envelope's own tags, so the
            # output is the same when the envelope is applied to it again.
            caller_global = True
            continue
        if tag.strip().lower() == PROJECT_UNRESOLVED_TAG:
            # The envelope's own marker, never a project a caller can claim:
            # dropped here and stamped again below when it applies.
            continue
        session_id = session_id_of(tag)
        if session_id is not None:
            if session_id not in session_ids:
                session_ids.append(session_id)
            continue
        word = None if explicitly_durable else lifecycle_word_of(tag)
        if word is not None:
            if word not in lifecycle:
                lifecycle.append(word)
            continue
        if tag not in seen:
            seen.add(tag)
            kept.append(tag)

    ephemeral = bool(lifecycle) or KIND_EPHEMERAL in caller_kinds
    kind = KIND_EPHEMERAL if ephemeral else KIND_DURABLE

    # The project. The resolver's decision is the only source of project tags:
    # whatever ``project:`` tags the caller sent are removed here, and the
    # ones the resolver made of them (if any) are stamped below.
    if project_resolution is None:
        project_resolution = resolve_project(
            tags=caller_tags, registry=ProjectRegistry(), coding=coding
        )
    kept = [tag for tag in kept if not _is_project_tag(tag)]
    projects = project_resolution.project_slugs
    refused = project_resolution.denied
    caller_scoped = caller_global or any(
        tag.strip().lower().startswith(SCOPE_TAG_PREFIX) for tag in kept
    )

    kept.append(f"{AGENT_TAG_PREFIX}{resolve_agent(agent)}")
    kept.append(f"{KIND_TAG_PREFIX}{kind}")

    decided: dict[str, Any] = {}
    if projects:
        kept.extend(f"{PROJECT_TAG_PREFIX}{slug}" for slug in projects)
        decided = {
            PROJECT_KEY: ",".join(projects),
            PROJECT_SOURCE_KEY: project_resolution.source,
        }
    elif coding or project_resolution.unresolved:
        kept.append(PROJECT_UNRESOLVED_TAG)
        decided = {
            PROJECT_KEY: UNRESOLVED_SLUG,
            PROJECT_SOURCE_KEY: PROJECT_SOURCE_DENIED if refused else PROJECT_SOURCE_NONE,
        }
    else:
        if refused:
            decided = {PROJECT_SOURCE_KEY: PROJECT_SOURCE_DENIED}
        if kind == KIND_DURABLE and stamp_global and not caller_scoped:
            kept.append(SCOPE_GLOBAL_TAG)
    if caller_global:
        kept.append(SCOPE_GLOBAL_TAG)

    additions: dict[str, Any] = {
        key: value for key, value in decided.items() if key not in caller_metadata
    }
    # Truthiness, as the provider tests it when it derives the document id:
    # an empty ``session_id`` is no session id, and a tag-derived one fills it.
    existing_session = caller_metadata.get(SESSION_ID_KEY)
    if existing_session:
        # The caller's own session id stands; only ids it does not already
        # name are recorded beside it.
        session_ids = [s for s in session_ids if s != str(existing_session)]
        if session_ids and SESSION_IDS_KEY not in caller_metadata:
            additions[SESSION_IDS_KEY] = session_ids
    elif len(session_ids) == 1:
        additions[SESSION_ID_KEY] = session_ids[0]
    elif session_ids and SESSION_IDS_KEY not in caller_metadata:
        additions[SESSION_IDS_KEY] = session_ids
    if lifecycle and LIFECYCLE_KEY not in caller_metadata:
        additions[LIFECYCLE_KEY] = ",".join(lifecycle)

    return WriteEnvelope(tags=tuple(kept), kind=kind, metadata=additions)


__all__ = [
    "AGENT_TAG_PREFIX",
    "ANONYMOUS_AGENT",
    "COMBINED_OBSERVATION_SCOPES",
    "DEFAULT_OBSERVATION_SCOPES",
    "KINDS",
    "KIND_DURABLE",
    "KIND_EPHEMERAL",
    "KIND_TAG_PREFIX",
    "LIFECYCLE_KEY",
    "LIFECYCLE_WORDS",
    "PROJECT_KEY",
    "PROJECT_SOURCE_DENIED",
    "PROJECT_SOURCE_KEY",
    "PROJECT_SOURCE_NONE",
    "PROJECT_TAG_PREFIX",
    "PROJECT_UNRESOLVED_TAG",
    "SCOPE_GLOBAL_TAG",
    "SCOPE_TAG_PREFIX",
    "SESSION_IDS_KEY",
    "SESSION_ID_KEY",
    "UNKNOWN_AGENT",
    "WriteEnvelope",
    "apply_envelope",
    "coerce_tags",
    "lifecycle_word_of",
    "resolve_agent",
    "resolve_observation_scopes",
]
