"""The memory project resolver and registry (ADR-0058, decision 4a).

A project is a request field, not a tag an agent writes by hand. A caller
names the project it is working in; this module turns that name into one
slug, or into "no project", and the write envelope
(:mod:`hal0.memory.envelope`) stamps the result.

Three things live here:

* :func:`normalise_slug`, the one spelling rule for a project name;
* :func:`strip_legacy_bank_prefix`, which reads a project name out of a
  per-repository bank id written by a coding-agent client;
* :func:`resolve_project`, which picks the name from the request (field, bank
  segment or a single ``project:`` tag), applies the registry's aliases, and
  then its deny list.

The registry (:class:`ProjectRegistry`) is the operator's ``[memory.projects]``
table: aliases, names that are never projects, and names the operator has
confirmed.

This module is pure: no I/O, no engine client, no configuration loading.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

PROJECT_TAG_PREFIX = "project:"

#: The longest slug :func:`normalise_slug` returns. 100 is the longest
#: repository name GitHub accepts, so a slug derived from a repository name on
#: the most common host is never cut; a longer name (a deep directory path,
#: pasted text) is cut to this many characters so one request cannot mint an
#: unbounded tag.
MAX_SLUG_LENGTH = 100

#: The slug of the ``project:unresolved`` tag: a write from a coding client
#: that named no usable project. It is never a real project, so the resolver
#: refuses it whatever the registry says.
UNRESOLVED_SLUG = "unresolved"

_UNRESOLVED_TAG = f"{PROJECT_TAG_PREFIX}{UNRESOLVED_SLUG}"

#: Names that are never projects, whatever the configured deny list says.
RESERVED_SLUGS = frozenset({UNRESOLVED_SLUG})

#: The shipped ``[memory.projects].deny``: directory names a client reports
#: when it is not inside a repository at all.
DEFAULT_DENY: tuple[str, ...] = (
    "tmp",
    "temp",
    "root",
    "home",
    "workdir",
    "work",
    "scratch",
    "scratchpad",
    "unknown",
    "default",
)

#: Where a resolved (or refused) project name came from.
SOURCE_FIELD = "field"
SOURCE_BANK = "bank"
SOURCE_TAG = "tag"
#: The retired ``project:<id>`` namespace (ADR-0005), which only a caller that
#: constructs the provider directly can still name. Set by the provider, never
#: by :func:`resolve_project`.
SOURCE_NAMESPACE = "namespace"
SOURCE_NONE = "none"

_SEPARATORS = "-._"

# A run that becomes one ``-``: any character outside ``[a-z0-9._-]``, or two
# or more dots in a row, together with the hyphens touching it. Dots are kept
# one at a time only, so a slug never contains ``..``.
_SEPARATOR_RUN_RE = re.compile(r"-*(?:(?:[^a-z0-9._-]|\.{2,})-*)+")

# The agent half of a ``<agent>::<project>`` bank id: a lower-case identifier,
# which is what the coding-agent integrations use for a harness name.
_HARNESS_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")

# hal0's own spelling of a per-project bank from before ADR-0005
# (``namespace_to_bank("project:<id>")``).
_PROJECT_BANK_PREFIX = "project__"

#: Second halves of a two-part bank id that name a kind of bank, not a
#: repository (``<owner>::agents``, ``<owner>::shared``). A bank id ending in
#: one of these is not project-shaped.
ROLE_WORDS = frozenset(
    {
        "agent",
        "agents",
        "shared",
        "private",
        "platform",
        "memory",
        "system",
        "default",
        "unknown",
    }
)


def normalise_slug(text: Any) -> str | None:
    """The one spelling of a project name, or ``None`` when nothing is left.

    Lower case; the characters ``[a-z0-9._-]`` are kept; every run of anything
    else becomes a single ``-``; leading and trailing ``-``, ``.`` and ``_``
    are removed; the result is cut to :data:`MAX_SLUG_LENGTH`. Two or more dots
    in a row count as "anything else", so ``..`` never survives and a path such
    as ``a/../b`` becomes ``a-b``. A character outside ASCII is "anything
    else" too: ``café`` becomes ``caf`` and a name with no ASCII letter or
    digit at all becomes ``None``.

    The function is idempotent, and the result is safe as a tag value, a URL
    path segment and a file name.
    """
    if not isinstance(text, str):
        return None
    slug = _SEPARATOR_RUN_RE.sub("-", text.strip().lower()).strip(_SEPARATORS)
    if len(slug) > MAX_SLUG_LENGTH:
        slug = slug[:MAX_SLUG_LENGTH].rstrip(_SEPARATORS)
    return slug or None


def strip_legacy_bank_prefix(bank_id: Any) -> str | None:
    """The project a per-repository bank id names, or ``None``.

    Such banks predate decision 7 of ADR-0058, which files every project's
    memory in one bank under a ``project:`` tag.

    Two shapes are project-shaped:

    * ``<agent>::<project>``, the id a coding-agent integration composes from
      its harness name and the repository's directory name. Exactly two parts,
      and the agent part is a lower-case identifier. ``coding-agent::myrepo``
      gives ``myrepo``.
    * ``project__<id>``, hal0's own per-project bank from before ADR-0005.

    Everything else gives ``None``: an id with no ``::`` (``shared``,
    ``default``, a single-word harness bank), an id with three or more parts
    (the position of the project is a client setting and cannot be read from
    the id), an id whose first part is not a lower-case identifier (operators
    name purpose banks in title case, ``Team::Agents``), and an id whose second
    part is a role word (:data:`ROLE_WORDS`), which names a kind of bank.

    The result is normalised. It has not been through the registry: pass it to
    :func:`resolve_project` as ``bank_segment`` for aliases and the deny list.
    """
    if not isinstance(bank_id, str):
        return None
    text = bank_id.strip()
    if text.startswith(_PROJECT_BANK_PREFIX):
        candidate = text[len(_PROJECT_BANK_PREFIX) :]
    else:
        parts = text.split("::")
        if len(parts) != 2 or not _HARNESS_RE.fullmatch(parts[0]):
            return None
        candidate = parts[1]
    slug = normalise_slug(candidate)
    if slug is None or slug in ROLE_WORDS:
        return None
    return slug


@dataclass(frozen=True, slots=True)
class ProjectRegistry:
    """The operator's ``[memory.projects]`` table, as the resolver reads it.

    ``aliases`` maps a name to the project it is another spelling of.
    ``deny`` holds names that are never projects. ``registered`` holds the
    names the operator has confirmed. All three hold normalised slugs; build
    one from configuration with :func:`registry_from_config`.
    """

    aliases: Mapping[str, str] = dataclass_field(default_factory=dict)
    deny: frozenset[str] = frozenset(DEFAULT_DENY)
    registered: frozenset[str] = frozenset()

    def canonical(self, slug: str) -> str:
        """``slug`` with aliases applied. A chain is followed to its end."""
        seen = {slug}
        while slug in self.aliases:
            slug = self.aliases[slug]
            if slug in seen:  # a cycle; configuration validation refuses one
                break
            seen.add(slug)
        return slug

    def is_denied(self, slug: str) -> bool:
        return slug in RESERVED_SLUGS or slug in self.deny

    def is_registered(self, slug: str) -> bool:
        """True for a confirmed name and for the project an alias ends at."""
        return slug in self.registered or any(self.canonical(name) == slug for name in self.aliases)

    def is_known(self, slug: str) -> bool:
        """True when the operator has named ``slug`` anywhere in the table:
        as a confirmed project, as an alias, or as the target of one."""
        return self.is_registered(slug) or slug in self.aliases


def registry_from_config(projects: Any) -> ProjectRegistry:
    """A :class:`ProjectRegistry` from a ``[memory.projects]`` config object.

    ``projects`` is read by attribute (``aliases``, ``deny``, ``registered``)
    and every entry is normalised again, so a hand-built object works as well
    as the validated model. ``None`` gives the shipped defaults.
    """
    if projects is None:
        return ProjectRegistry()
    aliases: dict[str, str] = {}
    for name, target in dict(getattr(projects, "aliases", None) or {}).items():
        key, value = normalise_slug(name), normalise_slug(target)
        if key and value and key != value:
            aliases[key] = value
    deny = getattr(projects, "deny", None)
    return ProjectRegistry(
        aliases=aliases,
        deny=_slugs(DEFAULT_DENY if deny is None else deny),
        registered=_slugs(getattr(projects, "registered", None) or ()),
    )


def _slugs(names: Iterable[Any]) -> frozenset[str]:
    return frozenset(slug for slug in map(normalise_slug, names) if slug)


@dataclass(frozen=True, slots=True)
class ProjectResolution:
    """What :func:`resolve_project` decided for one write or read.

    ``slug`` is the project, or ``None`` for "no project". ``source`` says
    where the name came from (``field``, ``bank``, ``tag``, ``namespace``) or
    is ``none`` when the request named nothing. ``denied`` is true when a name
    was given and the deny list refused it; ``source`` then still names where
    it came from. ``unresolved`` is true only for a coding client that ended
    up with no project. ``registered`` is true when the operator has confirmed
    the slug or made it the target of an alias.
    """

    slug: str | None = None
    source: str = SOURCE_NONE
    unresolved: bool = False
    registered: bool = False
    denied: bool = False

    @property
    def tag(self) -> str | None:
        """The ``project:<slug>`` tag, or ``None`` when there is no project."""
        return f"{PROJECT_TAG_PREFIX}{self.slug}" if self.slug else None


def _strip_tag_prefix(text: str) -> str:
    """``text`` without one leading ``project:``, compared ignoring case."""
    stripped = text.strip()
    if stripped.lower().startswith(PROJECT_TAG_PREFIX):
        return stripped[len(PROJECT_TAG_PREFIX) :]
    return stripped


def project_tags_of(tags: Iterable[Any]) -> list[str]:
    """The ``project:`` tags among ``tags``, in order, without duplicates.

    A tag counts when, stripped and lower-cased, it starts with ``project:``.
    ``project:unresolved`` does not count: it is the write envelope's marker
    for "no project", not a name.
    """
    found: list[str] = []
    for tag in tags or ():
        if not isinstance(tag, str) or tag in found:
            continue
        folded = tag.strip().lower()
        if folded.startswith(PROJECT_TAG_PREFIX) and folded != _UNRESOLVED_TAG:
            found.append(tag)
    return found


def resolve_project(
    *,
    field: Any = None,
    bank_segment: Any = None,
    tags: Iterable[Any] = (),
    registry: ProjectRegistry,
    coding: bool = False,
) -> ProjectResolution:
    """Decide the project of one request.

    The name is taken from the first of these that gives one:

    1. ``field``: the project the caller named in a request field or header;
    2. ``bank_segment``: the project part of a per-repository bank id (see
       :func:`strip_legacy_bank_prefix`);
    3. ``tags``: a ``project:`` tag, when the caller's tags carry exactly one.
       Two or more are ambiguous and name nothing.

    A source whose value normalises to nothing (:func:`normalise_slug`) counts
    as absent, and the next one is tried. The chosen name then goes through
    the registry's aliases, and the result through its deny list. A denied
    name is "no project", not an error, and resolution stops there: a lower
    source is not consulted, because the caller did name a project and it was
    refused.

    ``coding`` says the caller is a coding client, which is expected to work
    inside a project. ``unresolved`` is true only for such a caller that ends
    with no project.
    """
    candidates = [(SOURCE_FIELD, field), (SOURCE_BANK, bank_segment)]
    caller_projects = project_tags_of(tags)
    if len(caller_projects) == 1:
        candidates.append((SOURCE_TAG, caller_projects[0]))

    for source, raw in candidates:
        if not isinstance(raw, str):
            continue
        name = normalise_slug(_strip_tag_prefix(raw))
        if name is None:
            continue
        slug = registry.canonical(name)
        if registry.is_denied(slug):
            return ProjectResolution(source=source, unresolved=coding, denied=True)
        return ProjectResolution(slug=slug, source=source, registered=registry.is_registered(slug))
    return ProjectResolution(unresolved=coding)


__all__ = [
    "DEFAULT_DENY",
    "MAX_SLUG_LENGTH",
    "PROJECT_TAG_PREFIX",
    "RESERVED_SLUGS",
    "ROLE_WORDS",
    "SOURCE_BANK",
    "SOURCE_FIELD",
    "SOURCE_NAMESPACE",
    "SOURCE_NONE",
    "SOURCE_TAG",
    "UNRESOLVED_SLUG",
    "ProjectRegistry",
    "ProjectResolution",
    "normalise_slug",
    "project_tags_of",
    "registry_from_config",
    "resolve_project",
    "strip_legacy_bank_prefix",
]
