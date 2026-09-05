"""Schema-driven settings field metadata (#2108, #1967, #2195, #2203, #1511).

hal0's Settings pages used to be hand-built: each page authored its own
FormRow list, so a schema field the page's author didn't know about (or
added after the page shipped) had no dashboard path at all — the origin
of #2108's ``[brain_chat].tool_model`` gap. ODS's dashboard-api instead
derives every settings row from the schema itself
(``ods/extensions/services/dashboard-api/settings.py:194-241``,
``_build_env_fields``): whatever the schema declares is automatically a
labelled, described row.

This module is that projection for ``Hal0Config``: :func:`walk_settings_schema`
recursively walks every nested pydantic model under it and returns one
:class:`SettingsFieldSpec` per LEAF field (skipping the container models
themselves), driven entirely by ``pydantic.fields.FieldInfo`` — no
hand-maintained field list to fall out of sync with the schema.

Every leaf's ``Field(description=...)`` is REQUIRED — a missing one raises
:class:`SettingsFieldSchemaError` at wall-time, which
``tests/api/test_settings_fields.py`` turns into a completeness ratchet
(the same shape as the exposure-classification ratchet in
``tests/security/test_exposure.py``): a new config field with no
description fails CI immediately instead of shipping as an unlabelled or
silently-hidden row.
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from typing import Any, Final, get_args, get_origin

import pydantic
from pydantic.fields import FieldInfo

from hal0.api._redact import is_sensitive_key, redact_config
from hal0.api._settings_apply import REGISTRY
from hal0.config.schema import Hal0Config

# Segments that should render fully upper-case in a humanized label
# (acronyms a Title Case pass would otherwise mangle to "Tts"/"Vad").
_ACRONYM_WORDS: Final[frozenset[str]] = frozenset(
    {"id", "url", "tts", "stt", "vad", "npu", "mcp", "gpu", "flm"}
)


class SettingsFieldSchemaError(RuntimeError):
    """A leaf settings field is missing schema metadata the renderer needs.

    Raised for a missing ``description`` — every operator-editable config
    key must document its consequence (CONTRIBUTING.md's "no ghost-doc
    citations" rule, applied to the schema itself: an undocumented field
    is worse than a wrong doc, because there is nothing to catch it).
    """


def _humanize(leaf_name: str) -> str:
    """``"port_range_start"`` -> ``"Port range start"``; acronym segments upper-cased."""
    words = leaf_name.replace("_", " ").split()
    out = []
    for w in words:
        out.append(w.upper() if w.lower() in _ACRONYM_WORDS else w)
    if out:
        out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


def _unwrap_optional(annotation: Any) -> Any:
    """Strip a bare ``X | None`` down to ``X`` for type/enum classification.

    ``int | None`` (PEP 604) and ``Optional[int]`` (``typing.Union``) are
    two distinct origins in Python's typing machinery even though schema.py
    uses them interchangeably (``str | None`` throughout,
    ``list[str] | tuple[str, ...]`` elsewhere) — both must unwrap the same
    way or a PEP-604 optional silently falls through to the ``string``
    default below instead of its real type.
    """
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _field_type(annotation: Any) -> tuple[str, list[str] | None]:
    """Classify a field's annotation into the renderer's small type vocabulary.

    Returns ``(type, enum_values | None)``. ``enum`` covers ``Literal[...]``
    (e.g. ``ReleaseKind``); everything else maps to one of
    ``boolean`` / ``number`` / ``string`` / ``string[]`` / ``map``.
    """
    ann = _unwrap_optional(annotation)
    origin = get_origin(ann)
    if origin is typing.Literal:
        return "enum", [str(v) for v in get_args(ann)]
    if ann is bool:
        return "boolean", None
    if ann in (int, float):
        return "number", None
    if ann is str:
        return "string", None
    if origin in (list,):
        return "string[]", None
    if origin in (dict,):
        return "map", None
    return "string", None


def _constraints(field: FieldInfo) -> dict[str, float]:
    """Extract ``ge``/``le``/``gt``/``lt`` numeric bounds from pydantic metadata."""
    out: dict[str, float] = {}
    for constraint in field.metadata:
        for attr in ("ge", "le", "gt", "lt"):
            value = getattr(constraint, attr, None)
            if value is not None:
                out[attr] = value
    return out


@dataclass(frozen=True)
class SettingsFieldSpec:
    """One operator-editable leaf of ``Hal0Config``, schema metadata only.

    ``group`` is the top-level ``Hal0Config`` section name the field lives
    under (``"brain_chat"``, ``"memory.embedding"``, ...) — which Settings
    page renders which group is a frontend concern (``ui/src/dash/settings/
    data/fieldGroups.ts``), kept out of this module so re-organizing pages
    never needs a backend redeploy.
    """

    path: str
    group: str
    label: str
    description: str
    type: str
    enum: list[str] | None
    constraints: dict[str, float]
    default: Any
    secret: bool


def walk_settings_schema(
    model_cls: type[pydantic.BaseModel] = Hal0Config,
    prefix: str = "",
) -> list[SettingsFieldSpec]:
    """Recursively project every leaf field of ``model_cls`` into a spec.

    A field whose annotation is itself a nested ``BaseModel`` is recursed
    into (not emitted as a row) — the schema's grouping structure IS the
    settings' row structure, so no separate "which keys are leaves" list
    needs maintaining alongside ``Hal0Config``.
    """
    out: list[SettingsFieldSpec] = []
    for name, field in model_cls.model_fields.items():
        path = f"{prefix}{name}"
        unwrapped = _unwrap_optional(field.annotation)
        is_nested_model = isinstance(unwrapped, type) and issubclass(unwrapped, pydantic.BaseModel)
        if is_nested_model:
            out.extend(walk_settings_schema(unwrapped, f"{path}."))
            continue
        if not field.description:
            raise SettingsFieldSchemaError(
                f"{model_cls.__module__}.{model_cls.__qualname__}.{name} "
                f"({path!r}) has no Field(description=...) — every "
                "operator-editable settings key must document its "
                "consequence before it can render as a row."
            )
        type_, enum = _field_type(field.annotation)
        group = prefix.rstrip(".") or path
        out.append(
            SettingsFieldSpec(
                path=path,
                group=group,
                label=_humanize(name),
                description=field.description,
                type=type_,
                enum=enum,
                constraints=_constraints(field),
                default=field.get_default(call_default_factory=True),
                secret=is_sensitive_key(name),
            )
        )
    return out


def _get_in(node: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def build_settings_fields(cfg: Hal0Config) -> list[dict[str, Any]]:
    """Project every ``Hal0Config`` leaf into the one payload Settings pages render.

    Joins three single-owner facts with no duplication of any of them:

      * schema metadata (group/label/description/type/enum/constraints/default)
        from :func:`walk_settings_schema`, owned by the ``Field(description=...)``
        calls in :mod:`hal0.config.schema`;
      * the reload classification (``apply_class``/``services``), owned by
        :data:`hal0.api._settings_apply.REGISTRY` — same key name that
        endpoint's own response uses, so a caller reading both never sees
        two names for the same concept;
      * the current value, owned by ``cfg`` itself — read through
        :func:`hal0.api._redact.redact_config` so a ``secret`` row's current
        value is never echoed in the clear (mirrors ``_config_to_dict`` in
        ``routes/settings.py``, which every other config-echoing endpoint
        routes through).

    ``test_every_leaf_field_is_classified_in_the_apply_registry``
    (``tests/api/test_settings_fields.py``) already guarantees every row
    returned by :func:`walk_settings_schema` has a ``REGISTRY`` entry, so
    ``apply_class`` is never the ``None`` placeholder here — that only
    happens for a touched key on the raw ``PUT`` body (e.g. a typo, or a
    forward-compat top-level table), not for a schema-declared row.

    Pure and synchronous — deliberately does not know about live slot state,
    so it stays trivially testable without a running app. The route
    (``GET /api/settings/fields``) is the one that also asks "does this
    field's current value resolve to a loaded slot right now" (#2108's
    fresh-install ``tool_model`` gap) and merges that in as ``live_target``,
    because that question needs a live ``Request`` this function never sees.
    """
    redacted = redact_config(cfg.model_dump(mode="json"))
    out: list[dict[str, Any]] = []
    for row in walk_settings_schema():
        entry = REGISTRY.get(row.path)
        out.append(
            {
                "path": row.path,
                "group": row.group,
                "label": row.label,
                "description": row.description,
                "type": row.type,
                "enum": row.enum,
                "constraints": row.constraints,
                "default": row.default,
                "current": _get_in(redacted, row.path),
                "apply_class": entry["apply_class"] if entry else None,
                "services": list(entry["services"]) if entry else [],
                "secret": row.secret,
            }
        )
    return out


__all__ = [
    "SettingsFieldSchemaError",
    "SettingsFieldSpec",
    "build_settings_fields",
    "walk_settings_schema",
]
