"""The spelling rule for a project name, and the names that are never projects.

A leaf module on purpose: it imports nothing from hal0, so the config schema
(``[memory.projects]``) can use the rule without importing ``hal0.memory``,
whose package ``__init__`` builds the memory providers. The resolver that
applies these lives in :mod:`hal0.memory.projects`, which re-exports every
name here; import from there unless you are the schema.

Pure: no I/O.
"""

from __future__ import annotations

import re
from typing import Any

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

_SEPARATORS = "-._"

# A run that becomes one ``-``: any character outside ``[a-z0-9._-]``, or two
# or more dots in a row, together with the hyphens touching it. Dots are kept
# one at a time only, so a slug never contains ``..``.
_SEPARATOR_RUN_RE = re.compile(r"-*(?:(?:[^a-z0-9._-]|\.{2,})-*)+")


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


__all__ = [
    "DEFAULT_DENY",
    "MAX_SLUG_LENGTH",
    "RESERVED_SLUGS",
    "UNRESOLVED_SLUG",
    "normalise_slug",
]
