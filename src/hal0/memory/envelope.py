"""The memory write envelope (ADR-0058, decision 4).

Every memory write goes through :func:`apply_envelope` before it reaches the
engine. The envelope normalises the caller's tags; it never rejects a write.

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
   choice of kind and re-emitted once, in canonical lower case.
3. A session id (see :func:`session_id_of`) is removed from the tags and
   written to metadata: ``session_id`` when there is one, ``session_ids``
   (a list) when there are several.
4. A bare lifecycle word (:data:`LIFECYCLE_WORDS`) is removed from the tags,
   recorded in metadata as ``lifecycle``, and forces ``kind:ephemeral``.
5. Every other tag passes through untouched. That includes the namespaces the
   system already uses (``visibility:``, ``source:``, ``knowledge:``,
   ``origin:``, ``harness:``, ``migrated:``, ``project:``) and unknown bare
   words, which are the user's own vocabulary.

The result always carries exactly one ``agent:<id>`` tag and exactly one of
``kind:durable`` / ``kind:ephemeral``.

This module is pure: no I/O, no engine client, no provider state. The
``visibility:private`` stamp and its read-side enforcement (ADR-0005) stay in
:mod:`hal0.memory.hindsight_provider`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: The ``agent:<id>`` a write gets when no identity could be resolved.
UNKNOWN_AGENT = "unknown"

#: The front-door sentinel for a missing ``X-hal0-Agent`` header. It is not an
#: identity, so it resolves to :data:`UNKNOWN_AGENT`.
ANONYMOUS_AGENT = "anonymous"

AGENT_TAG_PREFIX = "agent:"
KIND_TAG_PREFIX = "kind:"
PROJECT_TAG_PREFIX = "project:"

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

#: The observation scope a write is sent with unless the caller chose another.
#: An engine ``RetainRequest`` item accepts ``"per_tag"``, ``"combined"``,
#: ``"all_combinations"``, ``"shared"`` or a list of tag lists; ``"shared"``
#: consolidates into one untagged scope regardless of the item's tags.
DEFAULT_OBSERVATION_SCOPES = "shared"

# ``session:<id>``, ``session_id:<id>``, ``session-id:<id>``, ``sessionid:<id>``.
# The id is everything after the first colon and must not be blank.
_SESSION_TAG_RE = re.compile(r"session(?:[-_]?id)?:(?P<id>.*\S.*)", re.IGNORECASE | re.DOTALL)

# A bare UUID in the canonical 8-4-4-4-12 form, any version and variant.
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)

# A bare run of 32 or more hexadecimal digits: a UUID without dashes, or a
# longer digest-shaped id. Shorter runs (a PR number, a date, an abbreviated
# commit hash) are ordinary tags.
_LONG_HEX_RE = re.compile(r"[0-9a-f]{32,}", re.IGNORECASE)


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

    Three shapes count, each judged on the whole stripped tag, ignoring case:

    * ``session:<id>`` (also ``session_id:``, ``session-id:``, ``sessionid:``)
      with a non-blank id, which is returned without the prefix;
    * a bare UUID in the 8-4-4-4-12 form, any version;
    * a bare run of 32 or more hexadecimal digits.
    """
    text = tag.strip()
    prefixed = _SESSION_TAG_RE.fullmatch(text)
    if prefixed:
        return prefixed.group("id").strip()
    if _UUID_RE.fullmatch(text) or _LONG_HEX_RE.fullmatch(text):
        return text
    return None


def lifecycle_word_of(tag: str) -> str | None:
    """The lifecycle word a tag is, in lower case, or ``None``."""
    word = tag.strip().lower()
    return word if word in LIFECYCLE_WORDS else None


def resolve_observation_scopes(requested: Any) -> Any:
    """The caller's observation scopes, or the default when none was given."""
    return DEFAULT_OBSERVATION_SCOPES if requested is None else requested


def apply_envelope(
    tags: Any,
    *,
    agent: str | None,
    project: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WriteEnvelope:
    """Normalise one write's tags. See the module docstring for the rules.

    ``agent`` is the resolved caller identity. ``project`` is a project slug
    the caller named in a structured field; it becomes a ``project:<slug>``
    tag. Nothing is derived from free text, and a bare word is never rewritten
    into a ``project:`` tag. ``metadata`` is the caller's metadata, read only
    to avoid overwriting a key the caller already set.
    """
    caller_metadata = metadata or {}
    kept: list[str] = []
    seen: set[str] = set()
    session_ids: list[str] = []
    lifecycle: list[str] = []
    caller_kinds: set[str] = set()

    for tag in coerce_tags(tags):
        folded = tag.strip().lower()
        if folded.startswith(AGENT_TAG_PREFIX):
            continue
        if folded.startswith(KIND_TAG_PREFIX) and folded[len(KIND_TAG_PREFIX) :] in KINDS:
            caller_kinds.add(folded[len(KIND_TAG_PREFIX) :])
            continue
        session_id = session_id_of(tag)
        if session_id is not None:
            if session_id not in session_ids:
                session_ids.append(session_id)
            continue
        word = lifecycle_word_of(tag)
        if word is not None:
            if word not in lifecycle:
                lifecycle.append(word)
            continue
        if tag not in seen:
            seen.add(tag)
            kept.append(tag)

    # A lifecycle word always wins. Between two caller-supplied kinds the
    # narrower claim wins: nothing the caller called ephemeral is promoted.
    ephemeral = bool(lifecycle) or KIND_EPHEMERAL in caller_kinds
    kind = KIND_EPHEMERAL if ephemeral else KIND_DURABLE

    kept.append(f"{AGENT_TAG_PREFIX}{resolve_agent(agent)}")
    kept.append(f"{KIND_TAG_PREFIX}{kind}")

    slug = (project or "").strip()
    if slug.lower().startswith(PROJECT_TAG_PREFIX):
        slug = slug[len(PROJECT_TAG_PREFIX) :].strip()
    if slug and f"{PROJECT_TAG_PREFIX}{slug}" not in kept:
        kept.append(f"{PROJECT_TAG_PREFIX}{slug}")

    additions: dict[str, Any] = {}
    existing_session = caller_metadata.get(SESSION_ID_KEY)
    if existing_session is not None:
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
    "DEFAULT_OBSERVATION_SCOPES",
    "KINDS",
    "KIND_DURABLE",
    "KIND_EPHEMERAL",
    "KIND_TAG_PREFIX",
    "LIFECYCLE_KEY",
    "LIFECYCLE_WORDS",
    "PROJECT_TAG_PREFIX",
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
