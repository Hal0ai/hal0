"""Load the hand-written setup catalog and the generated model ladder.

The catalog holds every word the wizard shows, the component list, the presets
and the answer-key names. The model ladder comes from a separate generated
file and is empty when that file is absent.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_VERSION = 1
KNOWN_NEEDS = frozenset({"podman", "gpu", "npu_device", "flm_build", "memory_python"})
REQUIRED_PRESETS = ("recommended", "minimal")
REQUIRED_QUESTIONS = ("setup_type", "cpu_only", "model_store")  # the screens index these
CHOICE_QUESTIONS = ("cpu_only", "setup_type")  # the screens show these as a list of choices


class CatalogError(ValueError):
    """A catalog or models file cannot be used. The message is one line."""


@dataclass(frozen=True)
class Option:
    value: object
    label: str
    help: str


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    help: str
    answer_key: str
    options: tuple[Option, ...]


@dataclass(frozen=True)
class Component:
    id: str
    label: str
    help: str
    answer_key: str
    on: object
    off: object
    download_gb: float | None
    needs: tuple[str, ...]


@dataclass(frozen=True)
class ModelRow:
    role: str
    model: str
    label: str
    lane: str
    download_gb: float | None
    min_gpu_pool_gb: float | None
    min_ram_gb: float | None
    needs_account: bool


@dataclass(frozen=True)
class Catalog:
    questions: dict[str, Question]
    components: tuple[Component, ...]
    model_ladder: tuple[ModelRow, ...]
    presets: dict[str, dict[str, object]]
    reasons: dict[str, str]


def _read_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CatalogError(f"{path}: file not found") from None
    except OSError as exc:
        raise CatalogError(f"{path}: cannot be read ({exc.strerror or exc})") from None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CatalogError(f"{path}: not valid JSON ({str(exc).splitlines()[0]})") from None
    if not isinstance(data, dict):
        raise CatalogError(f"{path}: top level must be an object")
    if data.get("version") != CATALOG_VERSION:
        raise CatalogError(
            f"{path}: unsupported version {data.get('version')}, expected {CATALOG_VERSION}"
        )
    where = _not_finite(data)
    if where is not None:
        raise CatalogError(f"{path}: {where} must be a finite number")
    return data


def _not_finite(data: object) -> str | None:
    """The key of the first NaN or infinity in a loaded file, or None."""
    stack: list[tuple[str, object]] = [("", data)]
    while stack:
        where, value = stack.pop()
        if isinstance(value, float) and not math.isfinite(value):
            return where
        if isinstance(value, dict):
            stack.extend((f"{where}.{k}" if where else str(k), v) for k, v in value.items())
        elif isinstance(value, list):
            stack.extend((f"{where}[{i}]", v) for i, v in enumerate(value))
    return None


def _field(path: Path, where: str, item: object, key: str, kind: type) -> Any:
    if not isinstance(item, dict) or not isinstance(item.get(key), kind):
        raise CatalogError(f"{path}: {where} needs a {kind.__name__} '{key}'")
    return item[key]


def _number(path: Path, where: str, item: dict[str, Any], key: str) -> float | None:
    value = item.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise CatalogError(f"{path}: {where} {key} must be null or a number >= 0")
    return float(value)


def _question(path: Path, raw: object) -> Question:
    qid = _field(path, "a question", raw, "id", str)
    where = f"question '{qid}'"
    assert isinstance(raw, dict)
    options: list[Option] = []
    raw_options = raw.get("options", [])
    if not isinstance(raw_options, list):
        raise CatalogError(f"{path}: {where} options must be a list")
    for opt in raw_options:
        if not isinstance(opt, dict) or "value" not in opt:
            raise CatalogError(f"{path}: {where} has an option without a value")
        options.append(
            Option(
                opt["value"],
                _field(path, f"{where} option", opt, "label", str),
                _field(path, f"{where} option", opt, "help", str),
            )
        )
    return Question(
        qid,
        _field(path, where, raw, "title", str),
        _field(path, where, raw, "help", str),
        _field(path, where, raw, "answer_key", str),
        tuple(options),
    )


def _component(path: Path, raw: object) -> Component:
    cid = _field(path, "a component", raw, "id", str)
    where = f"component '{cid}'"
    assert isinstance(raw, dict)
    needs = raw.get("needs", [])
    if not isinstance(needs, list):
        raise CatalogError(f"{path}: {where} needs must be a list")
    for need in needs:
        if need not in KNOWN_NEEDS:
            raise CatalogError(f"{path}: {where} has unknown need '{need}'")
    if "on" not in raw or "off" not in raw:
        raise CatalogError(f"{path}: {where} needs 'on' and 'off' values")
    return Component(
        cid,
        _field(path, where, raw, "label", str),
        _field(path, where, raw, "help", str),
        _field(path, where, raw, "answer_key", str),
        raw["on"],
        raw["off"],
        _number(path, where, raw, "download_gb"),
        tuple(needs),
    )


def _model_row(path: Path, raw: object) -> ModelRow:
    if not isinstance(raw, dict):
        raise CatalogError(f"{path}: a model row must be an object")
    where = f"model row '{raw.get('model')}'"
    lane = raw.get("lane", "")
    if not isinstance(lane, str):
        raise CatalogError(f"{path}: {where} lane must be a string")
    return ModelRow(
        _field(path, where, raw, "role", str),
        _field(path, where, raw, "model", str),
        _field(path, where, raw, "label", str),
        lane,
        _number(path, where, raw, "download_gb"),
        _number(path, where, raw, "min_gpu_pool_gb"),
        _number(path, where, raw, "min_ram_gb"),
        bool(raw.get("needs_account", False)),
    )


def _unique(path: Path, kind: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            raise CatalogError(f"{path}: duplicate {kind} id '{item}'")
        seen.add(item)


def _list(path: Path, data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise CatalogError(f"{path}: '{key}' must be a list")
    return value


def _ladder(models_path: Path | None) -> tuple[ModelRow, ...]:
    if models_path is None or not models_path.exists():
        return ()
    data = _read_object(models_path)
    return tuple(_model_row(models_path, r) for r in _list(models_path, data, "model_ladder"))


def load_catalog(path: Path, models_path: Path | None = None) -> Catalog:
    """Read and check the catalog; raise `CatalogError` naming the file and item."""
    data = _read_object(path)
    questions = [_question(path, q) for q in _list(path, data, "questions")]
    _unique(path, "question", [q.id for q in questions])
    for qid in REQUIRED_QUESTIONS:
        if all(q.id != qid for q in questions):
            raise CatalogError(f"{path}: question '{qid}' is missing")
    for question in questions:
        if question.id in CHOICE_QUESTIONS and not question.options:
            raise CatalogError(f"{path}: question '{question.id}' needs at least one option")
    components = [_component(path, c) for c in _list(path, data, "components")]
    _unique(path, "component", [c.id for c in components])

    presets = data.get("presets", {})
    if not isinstance(presets, dict):
        raise CatalogError(f"{path}: 'presets' must be an object")
    for name in REQUIRED_PRESETS:
        if not isinstance(presets.get(name), dict):
            raise CatalogError(f"{path}: preset '{name}' is missing")
    for name, preset in presets.items():
        if not isinstance(preset, dict):
            raise CatalogError(f"{path}: preset '{name}' must be an object")

    reasons = data.get("reasons", {})
    if not isinstance(reasons, dict):
        raise CatalogError(f"{path}: 'reasons' must be an object")
    for need in sorted(KNOWN_NEEDS):
        if not isinstance(reasons.get(need), str):
            raise CatalogError(f"{path}: reason for '{need}' is missing")

    return Catalog(
        {q.id: q for q in questions},
        tuple(components),
        _ladder(models_path),
        {str(k): dict(v) for k, v in presets.items()},
        {str(k): v for k, v in reasons.items()},
    )
