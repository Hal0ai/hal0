"""Headless answer-file loader for ``hal0 setup --answers`` (issue #1115).

Spec: ``handoffs/hal0-setup-answers-spec-2026-07-05.md``. This module
implements ONLY the subset of the schema that maps to
:class:`hal0.install.orchestrate.Selections` today (§5 "wired now" column):
``model_store.path``, ``slots[]``, ``apps.*.enabled`` + ``gen.mode``
(→ ``extensions``), ``npu.opt_in``, and ``gen.capabilities``
(→ ``comfyui_defaults``). Every other top-level key (``network``,
``huggingface``, ``apps.*.when``, ``apps.hermes.gateway``, the download side
of ``gen.mode: scaffold_and_download``, ``slots[].context_size``,
``slots[].enabled_on_pull``) is accepted and warned about, not applied — their
workstreams wire them in later per the field→destination map (§5).

``auto`` values resolve through the SAME resolvers ``build_auto_selections``
uses (``suggest_models``, ``derive_device``/``derive_profile`` via
``apply_setup``), so an all-``auto`` file is equivalent to today's
``--auto`` path (spec §3).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import yaml

from hal0.config.schema import HardwareInfo
from hal0.install.extensions import EXTENSIONS
from hal0.install.orchestrate import Selections, SlotSelection
from hal0.install.suggest import suggest_models

#: Top-level keys this loader recognizes at all (wired or not-yet-wired).
#: Anything outside this set is "unknown" (§4: reject unless strict: false).
_KNOWN_TOP_LEVEL = frozenset(
    {
        "version",
        "strict",
        "network",
        "model_store",
        "huggingface",
        "slots",
        "npu",
        "gen",
        "apps",
    }
)

#: capabilities valid for a slot entry (spec §3/§8: "chat" | "coder").
_VALID_SLOT_CAPABILITIES = frozenset({"chat", "coder"})

#: gen.mode values that enable the comfyui extension.
_GEN_MODES_ON = frozenset({"scaffold_only", "scaffold_and_download"})
_GEN_MODES = _GEN_MODES_ON | {"off"}


class AnswersError(ValueError):
    """Raised when the answer file fails validation."""


def _warn(msg: str) -> None:
    warnings.warn(msg, stacklevel=3)


def _yaml_word(value: Any) -> Any:
    """Undo PyYAML's YAML-1.1 bareword-bool coercion for our own enum words.

    The schema uses bare ``off``/``on`` as enum values (``gen.mode: off``,
    ``gen.capabilities.<cap>: off``), but ``yaml.safe_load`` parses unquoted
    ``off``/``no``/``false`` as Python ``False`` and ``on``/``yes``/``true`` as
    ``True`` (YAML 1.1 bool resolver). Map those back to the words the spec
    documents so downstream comparisons see ``"off"``/``"on"`` as intended.
    """
    if value is False:
        return "off"
    if value is True:
        return "on"
    return value


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise AnswersError(f"answer file must be a mapping at the top level, got {type(data)!r}")
    return data


def _check_version(doc: dict[str, Any]) -> None:
    version = doc.get("version")
    if version != 1:
        raise AnswersError(
            f"answer file must declare 'version: 1' (got {version!r}); "
            "this loader only understands schema version 1"
        )


def _check_top_level_keys(doc: dict[str, Any]) -> None:
    strict = bool(doc.get("strict", False))
    unknown = set(doc.keys()) - _KNOWN_TOP_LEVEL
    if not unknown:
        return
    if strict:
        raise AnswersError(f"unknown top-level key(s) in answer file: {sorted(unknown)}")
    for key in sorted(unknown):
        _warn(f"answer file key '{key}' is not recognized and was ignored (not yet applied)")


def _resolve_storage_dir(doc: dict[str, Any]) -> str:
    model_store = doc.get("model_store") or {}
    if not isinstance(model_store, dict):
        raise AnswersError("model_store must be a mapping")
    path = model_store.get("path")
    if not path or not isinstance(path, str):
        raise AnswersError("model_store.path is required and must be a non-empty string")
    if not path.startswith("/"):
        raise AnswersError(f"model_store.path must be absolute, got {path!r}")
    return path


def _resolve_slots(doc: dict[str, Any], hw: HardwareInfo) -> list[SlotSelection]:
    raw_slots = doc.get("slots") or []
    if not isinstance(raw_slots, list):
        raise AnswersError("slots must be a list")

    slots: list[SlotSelection] = []
    seen_ports: dict[int, str] = {}
    for i, entry in enumerate(raw_slots):
        if not isinstance(entry, dict):
            raise AnswersError(f"slots[{i}] must be a mapping")

        capability = entry.get("capability")
        if capability not in _VALID_SLOT_CAPABILITIES:
            raise AnswersError(
                f"slots[{i}].capability must be one of "
                f"{sorted(_VALID_SLOT_CAPABILITIES)}, got {capability!r}"
            )

        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise AnswersError(f"slots[{i}].name is required and must be a non-empty string")

        port = entry.get("port")
        if not isinstance(port, int) or isinstance(port, bool):
            raise AnswersError(f"slots[{i}].port must be an integer, got {port!r}")
        if port in seen_ports:
            raise AnswersError(
                f"slots[{i}].port {port} collides with slot {seen_ports[port]!r} "
                "(slot ports must be unique)"
            )
        seen_ports[port] = name

        model_id_raw = entry.get("model_id", "auto")
        if model_id_raw == "auto":
            picks = suggest_models(capability, hw, limit=1, prefer_coder=(capability == "coder"))
            if not picks:
                _warn(
                    f"slot '{name}' ({capability}): no model suggestion available for this "
                    "hardware; skipping slot"
                )
                continue
            model_id = picks[0].model_id
        else:
            model_id = model_id_raw or None

        # Not-yet-wired per-slot keys (spec §5, WS-E / Q7).
        if "context_size" in entry:
            _warn(f"slots[{i}].context_size is not yet applied (#1108)")
        if "enabled_on_pull" in entry:
            _warn(f"slots[{i}].enabled_on_pull is not yet applied (#1108)")

        device = entry.get("device")
        device = None if device in (None, "auto") else device
        profile = entry.get("profile")
        profile = None if profile in (None, "auto") else profile

        slots.append(
            SlotSelection(
                capability=capability,
                slot_name=name,
                port=port,
                model_id=model_id,
                device=device,
                profile=profile,
            )
        )
    return slots


def _resolve_npu_opt_in(doc: dict[str, Any], hw: HardwareInfo) -> bool:
    npu = doc.get("npu") or {}
    if not isinstance(npu, dict):
        raise AnswersError("npu must be a mapping")
    opt_in = npu.get("opt_in", "auto")
    if opt_in == "auto":
        return bool(hw.npu.present)
    if isinstance(opt_in, bool):
        return opt_in
    raise AnswersError(f"npu.opt_in must be true, false, or 'auto', got {opt_in!r}")


def _resolve_gen(doc: dict[str, Any]) -> tuple[bool, tuple[tuple[str, str], ...]]:
    """Return (comfyui_enabled, comfyui_defaults)."""
    from hal0.comfyui.capabilities import CAPABILITIES

    gen = doc.get("gen") or {}
    if not isinstance(gen, dict):
        raise AnswersError("gen must be a mapping")

    mode = _yaml_word(gen.get("mode", "off"))
    if mode not in _GEN_MODES:
        raise AnswersError(f"gen.mode must be one of {sorted(_GEN_MODES)}, got {mode!r}")
    comfyui_enabled = mode in _GEN_MODES_ON

    if mode == "scaffold_and_download":
        _warn(
            "gen.mode: scaffold_and_download — the download side is not yet applied (#1108); "
            "only scaffolding is performed"
        )

    caps_raw = gen.get("capabilities") or {}
    if not isinstance(caps_raw, dict):
        raise AnswersError("gen.capabilities must be a mapping")

    defaults: list[tuple[str, str]] = []
    for cap_id, family in caps_raw.items():
        if cap_id not in CAPABILITIES:
            raise AnswersError(
                f"gen.capabilities key {cap_id!r} is not a known ComfyUI capability "
                f"(known: {sorted(CAPABILITIES)})"
            )
        family = _yaml_word(family)
        if family == "off":
            continue
        if family == "auto":
            family = CAPABILITIES[cap_id].default_family
        elif not isinstance(family, str):
            raise AnswersError(
                f"gen.capabilities[{cap_id!r}] must be a family string, 'auto', or 'off', "
                f"got {family!r}"
            )
        defaults.append((cap_id, family))

    return comfyui_enabled, tuple(defaults)


def _resolve_extensions(doc: dict[str, Any], comfyui_enabled: bool) -> dict[str, bool]:
    apps = doc.get("apps") or {}
    if not isinstance(apps, dict):
        raise AnswersError("apps must be a mapping")

    known_ids = {e.id for e in EXTENSIONS}
    unknown_ids = set(apps.keys()) - known_ids
    if unknown_ids:
        raise AnswersError(f"apps has unknown extension id(s): {sorted(unknown_ids)}")

    extensions: dict[str, bool] = {}
    for ext in EXTENSIONS:
        if ext.id == "comfyui":
            # gen.mode drives the comfyui extension (spec §3 note); don't
            # read apps.comfyui even if present.
            extensions["comfyui"] = comfyui_enabled
            continue
        entry = apps.get(ext.id)
        if entry is None:
            extensions[ext.id] = ext.default_enabled
            continue
        if not isinstance(entry, dict):
            raise AnswersError(f"apps.{ext.id} must be a mapping")
        enabled = entry.get("enabled", ext.default_enabled)
        if not isinstance(enabled, bool):
            raise AnswersError(f"apps.{ext.id}.enabled must be a boolean, got {enabled!r}")
        extensions[ext.id] = enabled

        if "when" in entry:
            _warn(f"apps.{ext.id}.when is not yet applied (#1108)")
        if ext.id == "hermes" and "gateway" in entry:
            _warn("apps.hermes.gateway is not yet applied (#1108)")

    return extensions


def _warn_not_yet_wired(doc: dict[str, Any]) -> None:
    """Warn about known-but-not-yet-wired top-level blocks (spec §5)."""
    if "network" in doc:
        _warn("network is not yet applied (#1108)")
    if "huggingface" in doc:
        _warn(
            "huggingface token settings are not yet applied (#1108); "
            "set HF_TOKEN/HUGGING_FACE_HUB_TOKEN in the environment instead"
        )


def load_answers(path: str, hw: HardwareInfo) -> Selections:
    """Load a ``hal0-setup.yaml`` answer file and resolve it into a
    :class:`~hal0.install.orchestrate.Selections`.

    Only the subset that maps onto ``Selections`` today is applied; other
    keys are accepted and warned about (forward-compatible, spec §4).
    """
    doc = _load_yaml(path)
    _check_version(doc)
    _check_top_level_keys(doc)
    _warn_not_yet_wired(doc)

    storage_dir = _resolve_storage_dir(doc)
    slots = _resolve_slots(doc, hw)
    npu_opt_in = _resolve_npu_opt_in(doc, hw)
    comfyui_enabled, comfyui_defaults = _resolve_gen(doc)
    extensions = _resolve_extensions(doc, comfyui_enabled)

    return Selections(
        storage_dir=storage_dir,
        slots=slots,
        extensions=extensions,
        npu_opt_in=npu_opt_in,
        comfyui_defaults=comfyui_defaults,
    )


def dump_answers(sel: Selections) -> dict[str, Any]:
    """Serialize a resolved :class:`~hal0.install.orchestrate.Selections` back
    into the ``hal0-setup.yaml`` schema (v1, spec §3) that :func:`load_answers`
    reads — the ``--emit-answers`` half of the round trip (spec §2, §9.3).

    Emits CONCRETE resolved values (no ``auto`` literals): a slot's
    ``model_id``/``device``/``profile`` are whatever ``sel`` already carries,
    including ``None`` for a pick-free empty-scaffold slot (``load_answers``
    treats an explicit ``model_id: null`` the same as omission — round-trips
    to ``None``, not re-resolved to a suggestion).

    Only slot capabilities the loader accepts (``chat``/``coder`` — spec §3/§5)
    are written; any other capability in ``sel.slots`` (e.g. the ``embed``/
    ``rerank``/``stt``/``tts``/``vision`` scaffold slots ``build_auto_selections``
    also produces) is not yet part of the answer-file slots schema, so it is
    skipped with a warning rather than emitting a file ``load_answers`` would
    reject.

    SECURITY (spec §8): NEVER inlines a Hugging Face token. Only records which
    env var to read at apply time (``huggingface.token_env``) — matching
    ``load_answers``' env-only handling of ``huggingface.*`` today. No secret
    material is ever written to the emitted file.
    """
    slots: list[dict[str, Any]] = []
    for s in sel.slots:
        if s.capability not in _VALID_SLOT_CAPABILITIES:
            _warn(
                f"dump_answers: slot '{s.slot_name}' has capability {s.capability!r}, "
                f"which the answer-file slots schema does not yet support "
                f"({sorted(_VALID_SLOT_CAPABILITIES)}); omitted from the emitted file."
            )
            continue
        entry: dict[str, Any] = {
            "capability": s.capability,
            "name": s.slot_name,
            "port": s.port,
            "model_id": s.model_id,
        }
        if s.device is not None:
            entry["device"] = s.device
        if s.profile is not None:
            entry["profile"] = s.profile
        slots.append(entry)

    comfyui_enabled = bool(sel.extensions.get("comfyui", False))
    apps = {
        ext_id: {"enabled": enabled}
        for ext_id, enabled in sel.extensions.items()
        if ext_id != "comfyui"  # gen.mode drives the comfyui extension (spec §3 note)
    }

    return {
        "version": 1,
        "model_store": {"path": sel.storage_dir},
        # Never inline a token (spec §8) — only the env var name to read.
        "huggingface": {"token_env": "HF_TOKEN"},
        "slots": slots,
        "npu": {"opt_in": sel.npu_opt_in},
        "gen": {
            "mode": "scaffold_only" if comfyui_enabled else "off",
            "capabilities": dict(sel.comfyui_defaults),
        },
        "apps": apps,
    }


def write_answers(sel: Selections, path: str) -> None:
    """Write ``dump_answers(sel)`` to *path* as ``hal0-setup.yaml``.

    Prefixes a header comment noting the file was resolved against the
    hardware detected at write time (values are concrete, not ``auto``).
    """
    doc = dump_answers(sel)
    header = (
        "# hal0-setup.yaml — generated by `hal0 setup --emit-answers`\n"
        "# Values below are resolved against the hardware detected at write\n"
        "# time (concrete, not `auto`). Replay with:\n"
        "#   hal0 setup --auto --answers <this file>\n"
    )
    body = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + body, encoding="utf-8")


__all__ = ["AnswersError", "dump_answers", "load_answers", "write_answers"]
