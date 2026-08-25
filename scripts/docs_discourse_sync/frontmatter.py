"""Parse the Starlight frontmatter block off an MDX doc.

Every published doc starts ``---\\n<yaml>\\n---\\n`` (title, description,
``sidebar.order``, and optionally a ``short_title`` override or a
``version`` field this tool uses to emit an "Applies to" notice). This is a
thin, dependency-free split — PyYAML is already a core hal0 dependency —
rather than pulling in ``python-frontmatter`` for a two-field extraction.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*\n?", re.DOTALL)


class FrontmatterError(ValueError):
    """A doc's frontmatter block is missing, malformed, or missing a required key."""


@dataclass(frozen=True, slots=True)
class Frontmatter:
    title: str
    description: str | None
    short_title: str | None
    version: str | None
    # Starlight's ``sidebar.order``. Files without an explicit order sort
    # after every ordered file within their section (Starlight's own
    # autogenerate behaviour), hence +inf rather than 0.
    sidebar_order: float
    raw: dict[str, Any]


def split_frontmatter(text: str, *, source: str) -> tuple[Frontmatter, str, int]:
    """Split *text* into ``(frontmatter, body, body_start_line)``.

    ``body_start_line`` is the 1-indexed line number, in the *original*
    file, of the first line of ``body`` — callers doing further
    line-accurate diagnostics on the body add this offset (minus 1) to a
    0-indexed line computed within ``body``.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise FrontmatterError(f"{source}: missing '---' frontmatter block")

    raw_yaml = match.group(1)
    body = text[match.end() :]
    body_start_line = text.count("\n", 0, match.end()) + 1

    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"{source}: invalid frontmatter YAML: {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontmatterError(f"{source}: frontmatter must be a YAML mapping")

    title = data.get("title")
    if not title or not isinstance(title, str):
        raise FrontmatterError(f"{source}: frontmatter is missing a required string 'title'")

    sidebar = data.get("sidebar")
    order: float = math.inf
    if isinstance(sidebar, dict) and sidebar.get("order") is not None:
        try:
            order = float(sidebar["order"])
        except (TypeError, ValueError) as exc:
            raise FrontmatterError(f"{source}: sidebar.order is not numeric") from exc

    short_title = data.get("short_title")
    if short_title is not None and not isinstance(short_title, str):
        raise FrontmatterError(f"{source}: short_title must be a string")

    version = data.get("version")
    if version is not None:
        version = str(version)

    description = data.get("description")
    if description is not None:
        description = str(description)

    fm = Frontmatter(
        title=title,
        description=description,
        short_title=short_title,
        version=version,
        sidebar_order=order,
        raw=data,
    )
    return fm, body, body_start_line
