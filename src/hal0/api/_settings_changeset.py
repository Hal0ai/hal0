"""One ChangeSet computation for hal0.toml settings preview and apply.

Four issues found in adjacent parts of the codebase share one root cause:
a "what changed" diff computed twice, by two call sites that can drift.

  * #1967 — ``save_slot_config``'s round-trip silently drops keys nobody
    told it to keep, because nothing forces the read path and the write
    path to agree on the field set.
  * #2195 — the stack-apply preview bills added/changed flags but not
    removed ones, because the preview's copy-scope was hand-picked rather
    than derived from the one diff the apply itself uses.
  * #2203 — ``POST /api/models/{id}/seed-profile`` emits a hardcoded
    ``changed_fields`` list instead of comparing before/after, so a no-op
    re-stamp still claims two fields changed.
  * #1511 — the stack Load dialog previews what will be loaded but never
    what converge will silently unload, because the preview and the apply
    read different projections of the same operation.

:func:`compute_settings_changeset` is hal0's general-settings answer:
``POST /api/settings/preview`` and ``PUT /api/settings``
(``hal0.api.routes.settings``) both call this ONE function over the same
``(current config, patch body)`` pair. Preview literally cannot show
something apply wouldn't do, because they are the same computation.

Design choices that keep the four bug shapes from recurring here:

  * A touched key whose value did not actually change is dropped from
    ``changes`` (never a phantom "changed" entry — the #2203 shape).
  * A key is classified ``removed`` when its value goes missing (a dict
    entry cleared via the ``null``-clears idiom, e.g.
    ``[slots].default_images``) rather than folded into ``changed`` or
    silently absent (the #2195 shape).
  * Every touched leaf is either classified via
    :data:`hal0.api._settings_apply.REGISTRY` or explicitly listed in
    ``unknown`` — never dropped without a trace (the #1967 shape).
  * ``services`` (the #1511 shape: what a save affects beyond the value
    itself) rides on every change entry, not bolted on after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hal0.api._redact import redact_config
from hal0.api._settings_apply import REGISTRY, ApplyPlanResult, apply_plan
from hal0.config.schema import Hal0Config


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge: patch wins, but nested dicts are merged not replaced.

    Lists and scalars are replaced wholesale (no append/extend semantics)
    because the schema doesn't define list identities — the caller's intent
    when sending ``{"slots": {"port_range_end": 8090}}`` is to set that
    one knob, not to clobber the rest of ``slots``.
    """
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def dotted_leaf_keys(node: dict[str, Any], prefix: str = "") -> list[str]:
    """Flatten a nested PATCH body into dotted leaf paths (``"slots.max_slots"``).

    Recurses into every dict value, including a runtime dict-shaped field
    like ``[slots].default_images`` — a PATCH clearing one family
    (``{"slots": {"default_images": {"rocmfpx": null}}}``) flattens to
    ``"slots.default_images.rocmfpx"``, which is exactly the granularity
    :func:`compute_settings_changeset` needs to report that one family's
    removal rather than folding it into a blob-level "changed".
    """
    out: list[str] = []
    for k, v in node.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out.extend(dotted_leaf_keys(v, f"{path}."))
        else:
            out.append(path)
    return out


def dotted_get(tree: dict[str, Any], path: str) -> Any:
    """Walk ``tree`` by a dotted leaf path (``"memory.embedding.rerank_model"``)."""
    node: Any = tree
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@dataclass(frozen=True)
class SettingsKeyChange:
    """One leaf's before/after, with its reload consequence attached.

    ``kind`` is ``"added"`` (no prior value — a fresh dict entry or a
    forward-compat key), ``"removed"`` (the value is now missing — a dict
    entry cleared via ``null``), or ``"changed"`` (both sides present and
    different). A leaf whose value is unchanged never gets an entry — see
    the module docstring's #2203 note.
    """

    path: str
    before: Any
    after: Any
    kind: str
    apply_class: str | None
    services: list[str]


@dataclass(frozen=True)
class SettingsChangeSet:
    """The one diff both preview and apply render from.

    ``plan`` is byte-identical to ``apply_plan(touched_keys)`` over the
    raw patch's flattened keys (see ``tests/api/test_settings_apply.py``)
    — kept for the existing ``_hal0.apply_plan`` response contract, which
    classifies every TOUCHED key regardless of whether its value actually
    changed. ``changes`` is the newer, stricter view: only leaves whose
    value differs, each carrying its own before/after and reload class.
    """

    merged: Hal0Config
    changes: tuple[SettingsKeyChange, ...]
    unknown: tuple[str, ...]
    plan: ApplyPlanResult

    @property
    def changed(self) -> bool:
        """True when applying this ChangeSet would alter any value."""
        return bool(self.changes)


def compute_settings_changeset(current: Hal0Config, patch: dict[str, Any]) -> SettingsChangeSet:
    """Diff ``current`` deep-merged with ``patch`` against ``current`` itself.

    Raises ``pydantic.ValidationError`` when the merged result fails schema
    validation — same failure the caller (``update_settings``) already
    turns into ``ConfigInvalidError``; this function doesn't wrap it so
    both the preview and apply routes can share one ``except`` clause.

    Both routes pass the SAME two inputs through the SAME function — that
    is the whole point (see module docstring): preview cannot show a
    different plan than apply computes, because there is only one
    computation.
    """
    merged_raw = deep_merge(current.model_dump(mode="python"), patch)
    merged = Hal0Config.model_validate(merged_raw)

    before_view = redact_config(current.model_dump(mode="json"))
    after_view = redact_config(merged.model_dump(mode="json"))

    touched = dotted_leaf_keys(patch)
    changes: list[SettingsKeyChange] = []
    unknown: list[str] = []

    for path in touched:
        before_v = dotted_get(before_view, path)
        after_v = dotted_get(after_view, path)
        if before_v == after_v:
            continue
        kind = "added" if before_v is None else ("removed" if after_v is None else "changed")
        entry = REGISTRY.get(path)
        if entry is None:
            unknown.append(path)
        changes.append(
            SettingsKeyChange(
                path=path,
                before=before_v,
                after=after_v,
                kind=kind,
                apply_class=entry["apply_class"] if entry else None,
                services=list(entry["services"]) if entry else [],
            )
        )

    return SettingsChangeSet(
        merged=merged,
        changes=tuple(changes),
        unknown=tuple(sorted(unknown)),
        plan=apply_plan(touched),
    )


def changeset_payload(cs: SettingsChangeSet) -> dict[str, Any]:
    """Render a :class:`SettingsChangeSet` into the wire shape both
    ``POST /api/settings/preview`` and ``PUT /api/settings`` (under
    ``_hal0.changeset``) return."""
    return {
        "changes": [
            {
                "path": c.path,
                "before": c.before,
                "after": c.after,
                "kind": c.kind,
                "apply_class": c.apply_class,
                "services": c.services,
            }
            for c in cs.changes
        ],
        "unknown": list(cs.unknown),
    }


__all__ = [
    "SettingsChangeSet",
    "SettingsKeyChange",
    "changeset_payload",
    "compute_settings_changeset",
    "deep_merge",
    "dotted_get",
    "dotted_leaf_keys",
]
