"""The memory project resolver and registry (ADR-0058, decision 4a).

The decision makes the project something hal0 resolves and stamps, not a tag
an agent writes by hand. This module turns a project name into one slug, or
into "no project", and the write envelope (:mod:`hal0.memory.envelope`)
stamps the result. The provider takes a named project already; no REST or MCP
entry point passes one yet, because under the unified bank a project-tagged
memory is hidden from recall, search and list until decision 7b lands. Until
then the only source that reaches the resolver is a caller's own ``project:``
tags.

Three things live here:

* :func:`normalise_slug`, the one spelling rule for a project name (defined
  in :mod:`hal0.project_slug`, a leaf module the config schema can import
  without importing this package, and re-exported here);
* :func:`strip_legacy_bank_prefix`, which reads a project name out of a
  per-repository bank id written by a coding-agent client;
* :func:`resolve_project`, which picks the name from the request (field, bank
  segment or the caller's ``project:`` tags), applies the registry's aliases,
  and then its deny list.

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

from hal0.project_slug import (
    DEFAULT_DENY,
    MAX_SLUG_LENGTH,
    RESERVED_SLUGS,
    UNRESOLVED_SLUG,
    normalise_slug,
)

PROJECT_TAG_PREFIX = "project:"

_UNRESOLVED_TAG = f"{PROJECT_TAG_PREFIX}{UNRESOLVED_SLUG}"

#: Where a resolved (or refused) project name came from.
SOURCE_FIELD = "field"
SOURCE_BANK = "bank"
SOURCE_TAG = "tag"
#: The retired ``project:<id>`` namespace (ADR-0005), which only a caller that
#: constructs the provider directly can still name. Set by the provider, never
#: by :func:`resolve_project`.
SOURCE_NAMESPACE = "namespace"
SOURCE_NONE = "none"

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

    ``slug`` is the project, or ``None`` when there is not exactly one.
    ``source`` says where the name came from (``field``, ``bank``, ``tag``,
    ``namespace``) or is ``none`` when the request named nothing. ``denied``
    is true when a name was given and nothing usable was left of it: the deny
    list refused it, or it normalised to nothing; ``source`` then still names
    where it came from. ``unresolved`` is true only for a coding client that
    ended up with no project. ``registered`` is true when the operator has
    confirmed the slug (every slug, when there are several) or made it the
    target of an alias.

    ``slugs`` is the one case with more than one project: the caller's tags
    named two or more distinct projects, each of which resolved. They are held
    in the caller's order, and ``slug`` is ``None``.
    """

    slug: str | None = None
    source: str = SOURCE_NONE
    unresolved: bool = False
    registered: bool = False
    denied: bool = False
    slugs: tuple[str, ...] = ()

    @property
    def tag(self) -> str | None:
        """The ``project:<slug>`` tag, or ``None`` when there is not exactly one."""
        return f"{PROJECT_TAG_PREFIX}{self.slug}" if self.slug else None

    @property
    def project_slugs(self) -> tuple[str, ...]:
        """Every project of the write: the one slug, or the several."""
        return (self.slug,) if self.slug else self.slugs


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

    1. ``field``: the project a caller named outside its tags;
    2. ``bank_segment``: the project part of a per-repository bank id (see
       :func:`strip_legacy_bank_prefix`);
    3. ``tags``: the caller's ``project:`` tags.

    A ``field`` or ``bank_segment`` that normalises to nothing
    (:func:`normalise_slug`) counts as absent, and the next source is tried.
    The chosen name goes through the registry's aliases, and the result
    through its deny list. A denied name is "no project", not an error, and
    resolution stops there: a lower source is not consulted, because the
    caller did name a project and it was refused.

    Every ``project:`` tag is judged the same way, one by one: normalised,
    aliased, and dropped when it is denied or when nothing is left of it.
    What survives decides:

    * one distinct project: that is the project (``slug``);
    * two or more distinct projects: the write is filed under each
      (``slugs``), in the caller's order;
    * none, although the caller sent at least one tag: "no project", reported
      as ``denied``.

    So a ``project:`` tag never reaches a write in any spelling but the one
    this function produces, and a denied name never reaches it at all.

    ``coding`` says the caller is a coding client, which is expected to work
    inside a project. ``unresolved`` is true only for such a caller that ends
    with no project.
    """
    for source, raw in ((SOURCE_FIELD, field), (SOURCE_BANK, bank_segment)):
        if not isinstance(raw, str):
            continue
        name = normalise_slug(_strip_tag_prefix(raw))
        if name is None:
            continue
        slug = registry.canonical(name)
        if registry.is_denied(slug):
            return ProjectResolution(source=source, unresolved=coding, denied=True)
        return ProjectResolution(slug=slug, source=source, registered=registry.is_registered(slug))

    caller_projects = project_tags_of(tags)
    if not caller_projects:
        return ProjectResolution(unresolved=coding)
    valid: list[str] = []
    for raw in caller_projects:
        name = normalise_slug(_strip_tag_prefix(raw))
        if name is None:
            continue
        slug = registry.canonical(name)
        if not registry.is_denied(slug) and slug not in valid:
            valid.append(slug)
    if not valid:
        return ProjectResolution(source=SOURCE_TAG, unresolved=coding, denied=True)
    registered = all(registry.is_registered(slug) for slug in valid)
    if len(valid) == 1:
        return ProjectResolution(slug=valid[0], source=SOURCE_TAG, registered=registered)
    return ProjectResolution(source=SOURCE_TAG, registered=registered, slugs=tuple(valid))


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
