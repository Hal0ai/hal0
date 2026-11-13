"""Knowledge pages in the platform bank: the project folder, a page's scope, and folded ids.

One rule, two writers. ``/api/memory/compat`` serves the coding-agents
plugin's knowledge base from one folder per project at the root of the
platform bank's tree, ``project:<slug>``, and shows a page only when its
scope is one hal0 wrote: the project scope, no ``visibility:private`` memory,
no other mental model in the refresh (:func:`page_scope_held`). The fold-in
(:mod:`hal0.memory.fold_in`) carries an old coding bank's pages into that
folder and has to stamp them the same way, so both build a page's labels
(:func:`page_tags`) and its trigger (:func:`page_trigger`) here, and both find
a project's folder with :func:`project_folders`.

The fold-in gives every page it carries from a coding bank a new id
(:func:`folded_node_id`) and records old id -> new id in the page map
(:data:`PAGE_MAP_NAME`), which compat reads to answer a request that names a
page by the id it had in the old bank (:func:`resolve_folded_node`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import structlog

from hal0.memory.envelope import (
    AGENT_TAG_PREFIX,
    KIND_TAG_PREFIX,
    PROJECT_TAG_PREFIX,
    SCOPE_TAG_PREFIX,
    apply_envelope,
    coerce_tags,
)
from hal0.memory.read_scope import VISIBILITY_PRIVATE_TAG, project_scope_groups, tags_readable

log = structlog.get_logger(__name__)

#: The name of a project's folder at the root of the platform bank's tree.
KNOWLEDGE_FOLDER_PREFIX = PROJECT_TAG_PREFIX
#: The provenance stamp of the bridge's own memory tool: never a page's tag.
BRIDGE_SOURCE_TAG_PREFIX = "source:bridge:"
#: The refresh settings a page's writer may choose. ``tag_groups``,
#: ``exclude_mental_models`` and ``exclude_mental_model_ids`` are hal0's: they
#: are the page's scope.
CALLER_TRIGGER_FIELDS = frozenset(
    {
        "budget",
        "mode",
        "refresh_after_consolidation",
        "refresh_cron",
        "full_refresh_cron",
        "min_refresh_interval_seconds",
        "fact_types",
        "tags_match",
    }
)
#: The scope group that keeps every ``visibility:private`` memory out.
NOT_PRIVATE_GROUP: dict[str, Any] = {
    "not": {"tags": [VISIBILITY_PRIVATE_TAG], "match": "any_strict"}
}
#: Tag namespaces that are the envelope's labels, not a page's scope.
LABEL_TAG_PREFIXES = (AGENT_TAG_PREFIX, KIND_TAG_PREFIX, PROJECT_TAG_PREFIX, SCOPE_TAG_PREFIX)


def project_folder_name(slug: str) -> str:
    """``project:<slug>``: the folder at the root of the tree that holds a project's pages."""
    return f"{KNOWLEDGE_FOLDER_PREFIX}{slug}"


def project_folders(roots: Any, slug: str) -> list[dict[str, Any]]:
    """The root folders named ``project:<slug>``, in id order.

    The engine names a folder ``kf-<uuid4 hex>`` and does not return a node's
    creation time, so this is not creation order: it is an order every reader
    agrees on, so compat and the fold-in put new nodes in the same folder
    (the first).
    """
    name = project_folder_name(slug)
    mine = [
        node
        for node in (roots if isinstance(roots, list) else [])
        if isinstance(node, dict)
        and node.get("kind") == "folder"
        and node.get("name") == name
        and isinstance(node.get("id"), str)
    ]
    mine.sort(key=lambda node: node["id"])
    return mine


def dropped_tag(tag: str) -> bool:
    """A caller tag hal0 never writes on a memory or a page.

    ``scope:`` would make the write global (``scope:global`` is read by every
    project and every unresolved caller), and ``source:bridge:`` is the
    provenance stamp of the bridge's own memory tool. The envelope drops or
    replaces ``agent:``, ``visibility:`` and ``project:``. ``kind:`` stays the
    caller's choice: it widens nothing.
    """
    folded = tag.strip().lower()
    return folded.startswith((SCOPE_TAG_PREFIX, BRIDGE_SOURCE_TAG_PREFIX))


def page_tags(
    raw: Iterable[Any] | None, *, agent: str, resolution: Any
) -> tuple[list[str], list[str]]:
    """A page's labels and the tags of its scope leaf.

    The labels are the writer's tags through the write envelope, as a retain
    through compat gets them: ``scope:``, ``source:bridge:`` and
    ``visibility:`` tags dropped, exactly one ``agent:<agent>``, ``kind:`` and
    the resolved ``project:``. The scope leaf is what the writer asked the
    page to be built from: its own tags, without the envelope's.
    """
    kept = [
        tag
        for tag in coerce_tags(list(raw or []))
        if not dropped_tag(tag) and not tag.strip().lower().startswith("visibility:")
    ]
    envelope = apply_envelope(kept, agent=agent, project_resolution=resolution, coding=True)
    labels = list(envelope.tags)
    leaf = [tag for tag in labels if not tag.strip().lower().startswith(LABEL_TAG_PREFIXES)]
    return labels, leaf


def caller_trigger_settings(raw: Any) -> dict[str, Any]:
    """The refresh settings of ``raw`` a writer may choose (:data:`CALLER_TRIGGER_FIELDS`)."""
    if not isinstance(raw, Mapping):
        return {}
    return {key: value for key, value in raw.items() if key in CALLER_TRIGGER_FIELDS}


def page_trigger(chosen: Mapping[str, Any], leaf: list[str], slug: str) -> dict[str, Any]:
    """The trigger a page is written with: the writer's refresh settings and hal0's scope.

    The groups are AND-ed: the writer's own tags as one leaf, matched the way
    it asked (the engine's default for a tagged page, ``all_strict``, when it
    did not say); the project scope compat reads with; and no
    ``visibility:private`` memory. ``exclude_mental_models`` keeps the refresh
    from reading other mental models, whose content says nothing of where it
    came from. ``tags_match`` is kept as the writer sent it, because the
    plugin compares it with its own to decide whether a page drifted.
    """
    groups: list[dict[str, Any]] = []
    if leaf:
        groups.append({"tags": leaf, "match": chosen.get("tags_match") or "all_strict"})
    groups.extend(project_scope_groups(slug))
    groups.append(NOT_PRIVATE_GROUP)
    return {**chosen, "tag_groups": groups, "exclude_mental_models": True}


def normal_group(group: Any) -> Any:
    """A tag group in one spelling: a leaf's default match and ``exact``
    resolution written out the way :func:`project_scope_groups` writes them,
    so a group the engine stored compares equal to the one sent."""
    if not isinstance(group, dict):
        return group
    if "tags" in group:
        out: dict[str, Any] = {"tags": group.get("tags"), "match": group.get("match", "any_strict")}
        if group.get("resolve", "exact") != "exact":
            out["resolve"] = group["resolve"]
        return out
    for combinator in ("and", "or"):
        if combinator in group and isinstance(group[combinator], list):
            return {combinator: [normal_group(child) for child in group[combinator]]}
    if "not" in group:
        return {"not": normal_group(group["not"])}
    return group


def trigger_scope_held(trigger: Any, slug: str) -> bool:
    """True when a page's refresh scope is one hal0 wrote for project ``slug``.

    Other mental models left out, and among the AND-ed ``trigger.tag_groups``
    both the project scope and the group that keeps private memory out.
    Groups are AND-ed, so whatever else the scope holds can only narrow it.
    """
    if not isinstance(trigger, dict) or trigger.get("exclude_mental_models") is not True:
        return False
    groups = trigger.get("tag_groups")
    if not isinstance(groups, list):
        return False
    have = [normal_group(group) for group in groups]
    needed = [*project_scope_groups(slug), NOT_PRIVATE_GROUP]
    return all(normal_group(group) in have for group in needed)


def page_scope_held(
    node: Mapping[str, Any], *, read_groups: list[dict[str, Any]], slug: str
) -> bool:
    """True when compat may show a page to a caller of project ``slug``.

    Its tags pass the caller's read groups (visibility and project), and its
    refresh scope is one hal0 wrote (:func:`trigger_scope_held`).
    """
    if not tags_readable(read_groups, node.get("tags")):
        return False
    return trigger_scope_held(node.get("trigger"), slug)


# ── folded page ids ──────────────────────────────────────────────────────

#: The page map, beside the fold-in's ledger in the state directory.
PAGE_MAP_NAME = "memory-fold-in-page-map.json"
PAGE_MAP_VERSION = 1


def folded_node_id(bank: str, old_id: str, kind: str) -> str:
    """The id the fold-in gives a knowledge-base node it carries from old bank ``bank``.

    Shaped like the engine's own (``kp-`` or ``kf-`` and 32 hex digits) and
    derived from the old bank and the old id, so every run and every resend
    sends the same node under the same id. hindsight-api 1.2.0 keeps an
    archive node's id unless another bank holds it
    (``_restore_knowledge_pages`` in ``engine/transfer/importer.py``), and no
    bank holds this one, so it lands as sent.
    """
    prefix = "kp" if kind == "page" else "kf"
    digest = hashlib.sha256(f"hal0-fold-in\0{bank}\0{old_id}".encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


def placeholder_folder_id(slug: str) -> str:
    """The id a rewritten archive names project ``slug``'s folder by.

    It never reaches the engine: before the archive is sent, the fold-in
    swaps it for the id of the target's own ``project:<slug>`` folder,
    created then as compat creates it when the target has none.
    """
    digest = hashlib.sha256(f"hal0-fold-in-project-folder\0{slug}".encode()).hexdigest()[:32]
    return f"kf-{digest}"


def page_map_path() -> Path:
    """``/var/lib/hal0/state/memory-fold-in-page-map.json`` (sandboxed under ``HAL0_HOME``)."""
    from hal0.config.paths import var_lib

    return var_lib() / "state" / PAGE_MAP_NAME


_cache: dict[str, Any] = {"key": None, "data": {}}


def load_page_map(path: Path | None = None) -> dict[str, Any]:
    """The page map: ``{"version": 1, "targets": {bank: {old id: row}}}``.

    A row is ``{"id", "kind", "folder", "project", "bank"}``: the node's new
    id in the target, its kind, the project folder it is in now, its project
    and the old bank it came from. A map that is missing reads as empty; one
    that cannot be read raises :class:`ValueError` or :class:`OSError`.
    """
    target = path or page_map_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": PAGE_MAP_VERSION, "targets": {}}
    if not isinstance(data, dict) or data.get("version") != PAGE_MAP_VERSION:
        raise ValueError(f"{target} is not a page map this hal0 reads")
    if not isinstance(data.get("targets"), dict):
        data["targets"] = {}
    return data


def _cached_map() -> dict[str, Any]:
    path = page_map_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache["key"] != key:
        try:
            data = load_page_map(path)
        except (OSError, ValueError) as exc:
            # Fail closed: an unread map resolves nothing, and an old id is
            # answered as one that does not exist.
            log.warning("hal0.memory.page_map_unreadable", error=str(exc))
            data = {"targets": {}}
        _cache["key"], _cache["data"] = key, data.get("targets") or {}
    return _cache["data"]


def resolve_folded_node(bank: str, node_id: str) -> str | None:
    """The id a node the fold-in carried into ``bank`` has now, from the id it had before.

    ``None`` when the map holds no such id for that bank. The caller still
    checks the node it names exactly as it checks any other: the map only
    says which node an old id meant.
    """
    rows = _cached_map().get(bank)
    row = rows.get(node_id) if isinstance(rows, dict) else None
    new = row.get("id") if isinstance(row, dict) else None
    return new if isinstance(new, str) and new and new != node_id else None


__all__ = [
    "CALLER_TRIGGER_FIELDS",
    "KNOWLEDGE_FOLDER_PREFIX",
    "LABEL_TAG_PREFIXES",
    "NOT_PRIVATE_GROUP",
    "PAGE_MAP_NAME",
    "caller_trigger_settings",
    "dropped_tag",
    "folded_node_id",
    "load_page_map",
    "normal_group",
    "page_map_path",
    "page_scope_held",
    "page_tags",
    "page_trigger",
    "placeholder_folder_id",
    "project_folder_name",
    "project_folders",
    "resolve_folded_node",
    "trigger_scope_held",
]
