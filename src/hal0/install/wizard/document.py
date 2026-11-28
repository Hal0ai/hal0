"""The answers document: build it from layers, read it and write it safely.

Layers, lowest first: the preset, the saved document, the given document, the
person's choices, then flag and variable overrides. A key set in a higher layer
wins; a layer never replaces a whole sub-object with a partial one.

Security rules that live here: the file is written with mode 0600 through a
temporary file and a rename, a symlink target is refused, the Hermes terminal
tool is never switched on from a saved document, and an upgrade never gains an
admin key from the preset.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog

DOCUMENT_VERSION = 1
TERMINAL_KEY = "apps.hermes.terminal_tool"
ADMIN_KEY = "access.admin_key"
TYPE_KEY = "install.type"

Document = dict[str, object]


class DocumentError(ValueError):
    """An answers document, flag or variable cannot be used. The message is one line."""


@dataclass(frozen=True)
class Override:
    key: str
    value: object
    source: str


def _parts(key: str) -> list[str]:
    parts = key.split(".")
    if not key or not all(parts):
        raise DocumentError(f"invalid answer key '{key}'")
    return parts


def get(doc: Mapping[str, object], key: str) -> object | None:
    """Read a dotted key; None when any step is missing or is not an object."""
    node: object = doc
    for part in _parts(key):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def put(doc: Document, key: str, value: object) -> None:
    """Set a dotted key, replacing any non-object found on the way."""
    parts = _parts(key)
    node = doc
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _layer_of(items: Sequence[tuple[str, object]]) -> Document:
    layer: Document = {}
    for key, value in items:
        nested: Document = {}
        put(nested, key, value)
        _merge_replacing(layer, nested)
    return layer


def _merge_replacing(base: Document, layer: Mapping[str, object]) -> None:
    for name, value in layer.items():
        current = base.get(name)
        if isinstance(value, dict) and isinstance(current, dict):
            _merge_replacing(current, value)
        else:
            base[name] = copy.deepcopy(value)


def preset(catalog: Catalog, install_type: str) -> Document:
    """The catalog preset as a nested document. "custom" is recommended, typed custom."""
    name = "recommended" if install_type == "custom" else install_type
    flat = catalog.presets.get(name)
    if flat is None:
        raise DocumentError(f"unknown install type '{install_type}'")
    doc: Document = {}
    for key, value in flat.items():
        put(doc, key, copy.deepcopy(value))
    if install_type == "custom":
        put(doc, TYPE_KEY, "custom")
    return doc


_FLAG_NO_VALUE: dict[str, tuple[tuple[str, object], ...]] = {
    "--no-owui": (("apps.openwebui.enabled", False),),
    "--no-comfyui": (("gen.mode", "off"),),
    "--no-hermes": (("apps.hermes.enabled", False),),
    "--no-pi": (("apps.pi.enabled", False),),
    "--no-agents": (("apps.hermes.enabled", False), ("apps.pi.enabled", False)),
    "--no-memory": (("memory.enabled", False),),
    "--no-brain-model": (("models.brain", "none"),),
    "--bare": ((TYPE_KEY, "minimal"),),
    "--bare-inference": ((TYPE_KEY, "minimal"),),
    "--dev": (),
    "--no-start": (),
    "--help": (),
    "-h": (),
}
_FLAG_OPTIONAL_VALUE = frozenset({"--summary-json"})

_SKIP_VARIABLES: tuple[tuple[str, str], ...] = (
    ("HAL0_SKIP_OPENWEBUI", "apps.openwebui.enabled"),
    ("HAL0_SKIP_HERMES", "apps.hermes.enabled"),
    ("HAL0_SKIP_PI", "apps.pi.enabled"),
    ("HAL0_SKIP_HINDSIGHT", "memory.enabled"),
)


def _port(text: str) -> int:
    if not (text.isascii() and text.isdigit()) or not 1 <= int(text) <= 65535:
        raise DocumentError(f"HAL0_PORT must be a whole number from 1 to 65535, got '{text}'")
    return int(text)


def _variable_overrides(environ: Mapping[str, str]) -> list[Override]:
    out: list[Override] = []

    def value(name: str) -> str:
        return environ.get(name, "").strip()

    def add(key: str, val: object, source: str) -> None:
        out.append(Override(key, val, source))

    for name, key in _SKIP_VARIABLES:
        if value(name) == "1":
            add(key, False, name)
    if value("HAL0_INSTALL_PI") == "1" and value("HAL0_SKIP_PI") != "1":
        add("apps.pi.enabled", True, "HAL0_INSTALL_PI")
    images = value("HAL0_IMAGE_MODELS")
    if images in ("none", "0"):
        add("gen.mode", "off", "HAL0_IMAGE_MODELS")
    elif images:
        add("gen.mode", "scaffold_and_download", "HAL0_IMAGE_MODELS")
        # An empty list means the recommended set to the installer.
        listed = [] if images == "recommended" else images.replace(",", " ").split()
        add("gen.models", listed, "HAL0_IMAGE_MODELS")
    if value("HAL0_SKIP_BRAIN_MODEL") == "1":
        add("models.brain", "none", "HAL0_SKIP_BRAIN_MODEL")
    elif value("HAL0_BRAIN_MODEL"):
        add("models.brain", value("HAL0_BRAIN_MODEL"), "HAL0_BRAIN_MODEL")
    pull = value("HAL0_PULL_AGENT_MODEL")
    if pull in ("0", "1"):
        add("models.starter", "none" if pull == "0" else "auto", "HAL0_PULL_AGENT_MODEL")
    if value("HAL0_AGENT_MODEL"):
        add("models.starter", value("HAL0_AGENT_MODEL"), "HAL0_AGENT_MODEL")
    if value("HAL0_MODELS_DIR"):
        add("model_store.path", value("HAL0_MODELS_DIR"), "HAL0_MODELS_DIR")
    if value("HAL0_FLM_MODELS_DIR"):
        add("model_store.flm_path", value("HAL0_FLM_MODELS_DIR"), "HAL0_FLM_MODELS_DIR")
    if value("HAL0_PORT"):
        add("network.port", _port(value("HAL0_PORT")), "HAL0_PORT")
    if value("HAL0_PUBLIC_URL"):
        add("network.public_url", value("HAL0_PUBLIC_URL"), "HAL0_PUBLIC_URL")
    if value("HAL0_ALLOW_CPU_ONLY") == "1":
        add("hardware.allow_cpu_only", True, "HAL0_ALLOW_CPU_ONLY")
    terminal = value("HAL0_HERMES_TERMINAL")
    if terminal in ("0", "1"):
        add(TERMINAL_KEY, terminal == "1", "HAL0_HERMES_TERMINAL")
    return out


def _flag_overrides(flags: Sequence[str]) -> list[Override]:
    out: list[Override] = []
    for flag in flags:
        name, has_value, text = flag.partition("=")
        if name == "--models-dir":
            if not text:
                raise DocumentError("--models-dir needs a path (--models-dir=PATH)")
            out.append(Override("model_store.path", text, name))
        elif name in _FLAG_OPTIONAL_VALUE:
            continue
        elif name in _FLAG_NO_VALUE and not has_value:
            out.extend(Override(key, val, name) for key, val in _FLAG_NO_VALUE[name])
        elif name in _FLAG_NO_VALUE:
            raise DocumentError(f"{name} does not take a value")
        else:
            raise DocumentError(f"unknown flag {name}")
    return out


def overrides(flags: Sequence[str], environ: Mapping[str, str]) -> tuple[Override, ...]:
    """Turn installer flags and variables into overrides; flags come last and win."""
    return (*_variable_overrides(environ), *_flag_overrides(flags))


def _require_object(name: str, layer: object) -> Document:
    if not isinstance(layer, dict):
        raise DocumentError(f"the {name} answers must be an object")
    return layer


def _chosen_items(chosen: Mapping[str, object]) -> list[tuple[str, object]]:
    return [(key, value) for key, value in chosen.items() if not key.startswith("_")]


def _install_type(layers: Sequence[tuple[str, Document]]) -> tuple[str, str]:
    """The install type and the name of the highest layer that names one."""
    for name, layer in reversed(layers):
        named = get(layer, TYPE_KEY)
        if named is not None:
            if not isinstance(named, str):
                if name == "saved":
                    return "recommended", name
                raise DocumentError(f"{TYPE_KEY} must be text")
            return named, name
    return "recommended", "preset"


def _drop_gone_starter(catalog: Catalog, saved: Document, higher: Sequence[Document]) -> None:
    """Forget a saved chat model that the model list no longer offers.

    Only when no higher layer names the key, and only when the list has chat
    models at all: an old choice must not stop an upgrade, but a choice the person
    just made, gave or fixed is theirs to keep.
    """
    question = catalog.questions.get("starter_model")
    if question is None:
        return
    key = question.answer_key
    value = get(saved, key)
    offered = {r.model for r in catalog.model_ladder if r.role == "starter"}
    if not isinstance(value, str) or value in ("auto", "none") or not offered:
        return
    if value in offered or any(get(layer, key) is not None for layer in higher):
        return
    parent = get(saved, key.rpartition(".")[0]) if "." in key else saved
    if isinstance(parent, dict):
        parent.pop(key.rpartition(".")[2], None)


def effective(
    catalog: Catalog,
    *,
    chosen: Mapping[str, object],
    saved: Document | None,
    given: Document | None,
    over: Sequence[Override],
    hal0_version: str,
    now: str,
    install_state: str = "fresh",
) -> Document:
    """Layer preset, saved, given, chosen and overrides into one answers document."""
    saved_layer = copy.deepcopy(_require_object("saved", saved)) if saved is not None else {}
    given_layer = _require_object("given", given) if given is not None else {}
    apps = saved_layer.get("apps")
    if isinstance(apps, dict):
        hermes = apps.get("hermes")
        if isinstance(hermes, dict):
            hermes.pop("terminal_tool", None)
    chosen_layer = _layer_of(_chosen_items(chosen))
    over_layer = _layer_of([(o.key, o.value) for o in over])
    _drop_gone_starter(catalog, saved_layer, (given_layer, chosen_layer, over_layer))

    install_type, source = _install_type(
        [
            ("saved", saved_layer),
            ("given", given_layer),
            ("chosen", chosen_layer),
            ("override", over_layer),
        ]
    )
    if source == "saved" and install_type not in (*catalog.presets, "custom"):
        # An older or newer saved document loads tolerantly: use the defaults.
        install_type = "recommended"
    doc = preset(catalog, install_type)
    if install_state != "fresh":
        access = doc.get("access")
        if isinstance(access, dict):
            access.pop("admin_key", None)
    for layer in (saved_layer, given_layer):
        _merge_layer(doc, layer)
    # Choices and overrides always replace what a document holds at a key.
    for layer in (chosen_layer, over_layer):
        _merge_replacing(doc, layer)
    doc["version"] = DOCUMENT_VERSION
    doc["meta"] = {"saved_at": now, "saved_by": hal0_version}
    return doc


def _merge_layer(doc: Document, layer: Mapping[str, object]) -> None:
    """Higher layers win key by key; a non-object over an object is ignored."""
    for name, value in layer.items():
        current = doc.get(name)
        if isinstance(value, dict) and isinstance(current, dict):
            _merge_layer(current, value)
        elif isinstance(current, dict) and not isinstance(value, dict):
            continue
        else:
            doc[name] = copy.deepcopy(value)


def load_document(path: Path) -> Document:
    """Read an answers document. Unknown keys and other versions are kept as they are."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DocumentError(f"{path}: file not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        reason = getattr(exc, "strerror", None) or type(exc).__name__
        raise DocumentError(f"{path}: cannot be read ({reason})") from None
    try:
        data = json.loads(text)
    except RecursionError:
        raise DocumentError(f"{path}: not valid JSON (nested too deeply)") from None
    except ValueError as exc:
        raise DocumentError(f"{path}: not valid JSON ({str(exc).splitlines()[0]})") from None
    if not isinstance(data, dict):
        raise DocumentError(f"{path}: top level must be an object")
    return data


def write_document(doc: Document, path: Path) -> None:
    """Write the document as JSON, mode 0600, through a temporary file and a rename."""
    try:
        text = json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError, RecursionError) as exc:
        raise DocumentError(f"{path}: document cannot be written as JSON ({exc})") from None
    write_private_text(text, path)


def write_private_text(text: str, path: Path) -> None:
    """Write text with mode 0600 through a temporary file and a rename; never through a symlink."""
    if path.is_symlink():
        raise DocumentError(f"{path}: refusing to write through a symlink")
    temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise DocumentError(f"{path}: cannot be written ({exc.strerror or exc})") from None
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
