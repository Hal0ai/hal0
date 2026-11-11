"""Namespaced document ids inside the platform bank (ADR-0058 decisions 4b and 7a).

The memory engine upserts on document id, per bank, and replaces a document
when a retain names an id that exists. A coding-agent client picks its ids as
if it owned a whole bank (``conversation:<session>``, a slug of a document's
title, ``git:<sha>``), and ADR-0058 files every project and every client in
one bank. So that no client can replace, extend or delete a document it did
not write, hal0 stores such a client's documents under a namespaced id and
shows the client its own id back::

    stored id  =  <project> "::" <owner> "::" <the client's id>

    <project>  =  the project slug, or "unresolved" for a client with none
    <owner>    =  the bridge client's name, or "shared"

Each client has a namespace of its own inside a project, and one of its own
for its unresolved writes. ``shared`` holds the ids that every agent working
on one repository derives the same way and is meant to share, so that a
second agent does not ingest the repository's history again: an id that
starts with one of the configured prefixes (``[memory.compat]
shared_document_prefixes``) is stored under ``<project>::shared::``. A client
with no project has no shared namespace.

Why the split is unambiguous: ``::`` cannot occur in either of the first two
parts. A slug is ``[a-z0-9._-]``, a bridge client name ``[a-z0-9_-]``;
``unresolved`` is never a slug (``hal0.project_slug.RESERVED_SLUGS``) and
``shared`` is never a client name (``hal0.bridge.keys``). A stored id is
split at its first two ``::`` and at no other, and an id a client sends that
itself contains ``::`` is the client's id, whole: it is prefixed, never read.

The engine puts no limit on a document id (a text column, any characters).
hal0's own bank routes do: ``/api/memory/banks/{bank}/documents/{id}`` accepts
an id of at most 256 characters from ``[a-zA-Z0-9._:-]`` with no ``..``
(``hal0.api.routes.memory_admin``). A stored id that would not fit keeps its
two-part prefix and carries ``sha256-<hex>`` of the client's id in place of
the id, so an operator can still open and delete the document. The mapping
stays a pure function of the client's id; the id itself is kept in the
document's metadata (:data:`CLIENT_DOCUMENT_ID_KEY`) so it can be shown back.

The fold-in of the old per-repository banks (decision 7a) writes through
:func:`stored_document_id` too, so that a client moved onto the
engine-compatible endpoint finds the documents of its old
``coding-agent::<project>`` bank under the ids it already knows.

Pure: no I/O.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from hal0.project_slug import UNRESOLVED_SLUG

#: Between the parts of a stored id. Not a character sequence of a project
#: slug or of a bridge client name.
SEPARATOR = "::"

#: The owner part of an id every client of a project may write.
SHARED_OWNER = "shared"

#: Document metadata key holding the client's own id when the stored id
#: carries a digest of it instead.
CLIENT_DOCUMENT_ID_KEY = "client_document_id"

#: The longest stored id hal0's bank routes can address.
MAX_STORED_ID_LENGTH = 256

DIGEST_PREFIX = "sha256-"

# What hal0's bank routes accept as a document id path segment.
_ADDRESSABLE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]*")
_PROJECT_PART_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_OWNER_PART_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

# The engine's chunk id is ``<bank>_<document id>_<index>`` with ``~`` and
# ``_`` escaped inside each part (``hindsight_api.engine.chunk_ids``).
_CHUNK_ESCAPES = {"~": "~7E", "_": "~5F"}


def is_shared_id(client_id: str, shared_prefixes: Iterable[str]) -> bool:
    """True when ``client_id`` starts with one of the shared-id prefixes."""
    return any(prefix and client_id.startswith(prefix) for prefix in shared_prefixes)


def id_prefix(slug: str | None, owner: str) -> str:
    """``<project>::<owner>::``, with ``unresolved`` standing for no project."""
    return f"{slug or UNRESOLVED_SLUG}{SEPARATOR}{owner}{SEPARATOR}"


def _owner(slug: str | None, client: str, client_id: str, shared_prefixes: Iterable[str]) -> str:
    return SHARED_OWNER if slug and is_shared_id(client_id, shared_prefixes) else client


def _stored(prefix: str, client_id: str) -> str:
    stored = f"{prefix}{client_id}"
    if (
        client_id
        and len(stored) <= MAX_STORED_ID_LENGTH
        and ".." not in stored
        and _ADDRESSABLE_RE.fullmatch(stored)
    ):
        return stored
    digest = hashlib.sha256(client_id.encode("utf-8", "surrogatepass")).hexdigest()
    return f"{prefix}{DIGEST_PREFIX}{digest}"


def stored_document_id(
    slug: str | None, client: str, client_id: str, shared_prefixes: Iterable[str] = ()
) -> str:
    """The id a client's document is stored under.

    ``slug`` is the client's project, or ``None``. ``client_id`` is taken
    whole, whatever it contains: it is never split and never read as a
    namespace of its own.
    """
    return _stored(id_prefix(slug, _owner(slug, client, client_id, shared_prefixes)), client_id)


def is_digested(
    slug: str | None, client: str, client_id: str, shared_prefixes: Iterable[str] = ()
) -> bool:
    """True when ``client_id`` is stored as a digest, not as itself."""
    prefix = id_prefix(slug, _owner(slug, client, client_id, shared_prefixes))
    return _stored(prefix, client_id) != f"{prefix}{client_id}"


def readable_prefixes(slug: str | None, client: str) -> tuple[str, ...]:
    """The id prefixes ``client`` may list and address in project ``slug``:
    its own namespace and, inside a project, the shared one."""
    own = id_prefix(slug, client)
    return (own, id_prefix(slug, SHARED_OWNER)) if slug else (own,)


def visible_document_id(
    slug: str | None, client: str, stored_id: Any, metadata: Any = None
) -> str | None:
    """The client's own id for a stored document, or ``None``.

    ``None`` means the document is in neither of the client's namespaces: it
    is not this client's to see or address, whatever else is true of it. For
    a digested id the client's id is read from ``metadata`` and returned only
    if it really maps to ``stored_id``; without it the digest form is
    returned, which addresses the same document when the client sends it
    back.
    """
    if not isinstance(stored_id, str):
        return None
    original = metadata.get(CLIENT_DOCUMENT_ID_KEY) if isinstance(metadata, dict) else None
    for prefix in readable_prefixes(slug, client):
        if stored_id.startswith(prefix):
            if isinstance(original, str) and _stored(prefix, original) == stored_id:
                return original
            return stored_id[len(prefix) :]
    return None


def is_namespaced(stored_id: Any) -> bool:
    """True when ``stored_id`` has the shape of a namespaced id, anyone's."""
    if not isinstance(stored_id, str):
        return False
    project, first, rest = stored_id.partition(SEPARATOR)
    owner, second, _client_id = rest.partition(SEPARATOR)
    if not (first and second):
        return False
    return bool(_PROJECT_PART_RE.fullmatch(project) and _OWNER_PART_RE.fullmatch(owner))


def chunk_id_part(document_id: str) -> str:
    """``document_id`` as it appears inside one of the engine's chunk ids."""
    return "".join(_CHUNK_ESCAPES.get(ch, ch) for ch in document_id)


__all__ = [
    "CLIENT_DOCUMENT_ID_KEY",
    "DIGEST_PREFIX",
    "MAX_STORED_ID_LENGTH",
    "SEPARATOR",
    "SHARED_OWNER",
    "chunk_id_part",
    "id_prefix",
    "is_digested",
    "is_namespaced",
    "is_shared_id",
    "readable_prefixes",
    "stored_document_id",
    "visible_document_id",
]
