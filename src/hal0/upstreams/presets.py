"""Provider presets: the built-in list of known upstream providers.

The data lives in ``hal0/config/data/provider_presets.toml`` and is read
through ``importlib.resources`` so an editable checkout and an installed
wheel behave identically. Presets are never mutated at runtime; user
configuration lives in ``upstreams.toml``. API keys are never stored in
presets.

Auth styles (each has a branch in ``UpstreamRegistry.auth_headers()``;
tests/security/test_upstream_auth_contract.py keeps the two in step):
    bearer     Authorization: Bearer <key>
    anthropic  x-api-key + anthropic-version (native Anthropic protocol)
    header     custom header name
    none       no auth header
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

_PACKAGE = "hal0.config.data"
_FILENAME = "provider_presets.toml"

_CATEGORIES = frozenset({"cloud", "local", "peer", "custom"})
_AUTH_STYLES = frozenset({"bearer", "anthropic", "header", "none"})
_MODEL_TYPES = frozenset({"chat", "embed", "rerank", "tts", "stt", "image"})
_PRICE_UNITS = frozenset({"Mtok", "Mchar", "image", "1k_search"})
_RATE_LIMIT_KEYS = frozenset(
    {"remaining_requests", "limit_requests", "remaining_tokens", "limit_tokens", "retry_after"}
)


class PresetError(ValueError):
    """Raised when the preset data file is malformed."""


@dataclass(frozen=True)
class PresetModel:
    id: str
    type: str  # chat|embed|rerank|tts|stt|image
    context: int | None = None
    price_in: float | None = None
    price_out: float | None = None
    price_unit: str = "Mtok"
    recommended: bool = False


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    category: str  # cloud|local|peer|custom
    base_url: str
    auth: str  # bearer|anthropic|header|none
    auth_header_name: str = ""
    docs_url: str = ""
    notes: str = ""
    models: tuple[PresetModel, ...] = ()
    rate_limit_headers: dict[str, str] = field(default_factory=dict)
    quirks: tuple[str, ...] = ()  # consumed by the dispatcher; data only here
    tools: bool = True  # False renders "chat only, no tools"


def _read_text() -> str:
    return resources.files(_PACKAGE).joinpath(_FILENAME).read_text(encoding="utf-8")


def _num(value: Any, where: str, *, integer: bool = False) -> Any:
    ok = isinstance(value, int) if integer else isinstance(value, int | float)
    if not ok or isinstance(value, bool):
        raise PresetError(f"{where}: expected a number, got {value!r}")
    return value


def _str(row: dict[str, Any], key: str, where: str, default: str | None = None) -> str:
    value = row.get(key, default)
    if not isinstance(value, str):
        raise PresetError(f"{where}: {key} must be a string")
    return value


def _parse_model(pid: str, row: Any) -> PresetModel:
    if not isinstance(row, dict):
        raise PresetError(f"preset {pid!r}: model entries must be tables")
    mid = _str(row, "id", f"preset {pid!r} model", "")
    where = f"preset {pid!r} model {mid!r}"
    if not mid:
        raise PresetError(f"preset {pid!r}: model without an id")
    mtype = _str(row, "type", where)
    if mtype not in _MODEL_TYPES:
        raise PresetError(f"{where}: invalid type {mtype!r}")
    unit = _str(row, "price_unit", where, "Mtok")
    if unit not in _PRICE_UNITS:
        raise PresetError(f"{where}: invalid price_unit {unit!r}")
    context = row.get("context")
    if context is not None:
        _num(context, f"{where} context", integer=True)
    prices = {}
    for key in ("price_in", "price_out"):
        value = row.get(key)
        if value is not None:
            _num(value, f"{where} {key}")
            value = float(value)
        prices[key] = value
    recommended = row.get("recommended", False)
    if not isinstance(recommended, bool):
        raise PresetError(f"{where}: recommended must be a boolean")
    return PresetModel(
        id=mid,
        type=mtype,
        context=context,
        price_unit=unit,
        recommended=recommended,
        **prices,
    )


def _parse_preset(pid: str, row: Any) -> Preset:
    if not isinstance(row, dict):
        raise PresetError(f"preset {pid!r}: must be a table")
    where = f"preset {pid!r}"
    name = _str(row, "name", where, "")
    if not name:
        raise PresetError(f"{where}: missing name")
    category = _str(row, "category", where)
    if category not in _CATEGORIES:
        raise PresetError(f"{where}: invalid category {category!r}")
    auth = _str(row, "auth", where)
    if auth not in _AUTH_STYLES:
        raise PresetError(f"{where}: invalid auth style {auth!r}")
    base_url = _str(row, "base_url", where, "")
    if category == "cloud" and not base_url.startswith("https://"):
        raise PresetError(f"{where}: cloud presets need an https:// base_url")
    headers = row.get("rate_limit_headers", {})
    if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
        raise PresetError(f"{where}: rate_limit_headers must map names to strings")
    unknown = set(headers) - _RATE_LIMIT_KEYS
    if unknown:
        raise PresetError(f"{where}: unknown rate_limit_headers keys {sorted(unknown)}")
    quirks = row.get("quirks", [])
    if not isinstance(quirks, list) or not all(isinstance(q, str) for q in quirks):
        raise PresetError(f"{where}: quirks must be a list of strings")
    tools = row.get("tools", True)
    if not isinstance(tools, bool):
        raise PresetError(f"{where}: tools must be a boolean")
    models = row.get("models", [])
    if not isinstance(models, list):
        raise PresetError(f"{where}: models must be an array of tables")
    parsed = tuple(_parse_model(pid, m) for m in models)
    seen: set[str] = set()
    for m in parsed:
        if m.id in seen:
            raise PresetError(f"{where}: model {m.id!r} is listed twice")
        seen.add(m.id)
    return Preset(
        id=pid,
        name=name,
        category=category,
        base_url=base_url,
        auth=auth,
        auth_header_name=_str(row, "auth_header_name", where, ""),
        docs_url=_str(row, "docs_url", where, ""),
        notes=_str(row, "notes", where, ""),
        models=parsed,
        rate_limit_headers=dict(headers),
        quirks=tuple(quirks),
        tools=tools,
    )


def _parse(text: str) -> dict[str, Preset]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PresetError(f"provider presets are not valid TOML: {exc}") from exc
    table = data.get("preset")
    if not isinstance(table, dict) or not table:
        raise PresetError("provider presets: missing [preset.*] tables")
    return {pid: _parse_preset(pid, row) for pid, row in table.items()}


@cache
def load_presets() -> dict[str, Preset]:
    """Return every shipped preset keyed by id. Raises PresetError if malformed."""
    return _parse(_read_text())


def get_preset(preset_id: str) -> Preset | None:
    return load_presets().get(preset_id)


def preset_to_api(p: Preset) -> dict[str, Any]:
    """JSON shape for ``GET /api/providers/catalog``.

    Keeps every key the UI already reads (``CatalogEntry`` in
    ui/src/api/hooks/useUpstreams.ts) alongside the richer preset fields.
    """
    caps: list[str] = []
    for m in p.models:
        if m.type not in caps:
            caps.append(m.type)
    if not caps:
        caps.append("chat")
    if p.tools:
        caps.append("tools")
    recommended = next((m.id for m in p.models if m.recommended), "")
    header = p.auth_header_name or {"bearer": "Authorization", "anthropic": "x-api-key"}.get(
        p.auth, ""
    )
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "base_url": p.base_url,
        "auth": p.auth,
        "auth_header_name": header,
        "models_path": "/models",
        "default_models": [m.id for m in p.models if m.type == "chat"],
        "default_model": recommended,
        "capabilities": caps,
        "docs_url": p.docs_url,
        "notes": p.notes,
        "tools": p.tools,
        "quirks": list(p.quirks),
        "rate_limit_headers": dict(p.rate_limit_headers),
        "models": [
            {
                "id": m.id,
                "type": m.type,
                "context": m.context,
                "price_in": m.price_in,
                "price_out": m.price_out,
                "price_unit": m.price_unit,
                "recommended": m.recommended,
            }
            for m in p.models
        ],
    }


__all__ = [
    "Preset",
    "PresetError",
    "PresetModel",
    "get_preset",
    "load_presets",
    "preset_to_api",
]
