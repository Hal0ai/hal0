"""Portable stacks — export/import/snapshot (spec §3/§4/§6).

Export embeds a stack's referenced profiles + model METADATA (never weights,
never host paths) into a self-contained ``.hal0stack.json`` envelope with a
content checksum. Import validates + classifies model refs (present / pullable
/ unresolvable) + reconciles profiles + creates the stack. Snapshot reads the
live config into a StackConfig. Pure functions — the caller stamps ``exported_at``
and injects the registry, so there is no clock or hidden global here.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hal0 import __version__
from hal0.capabilities.config import load_capabilities_config
from hal0.config import paths
from hal0.config.loader import (
    list_slots,
    load_profiles_config,
    save_profiles_config,
)
from hal0.config.schema import (
    _VALID_DEVICES,
    STACK_SCHEMA_VERSION_CURRENT,
    StackCapabilityRow,
    StackConfig,
    StackModelMeta,
    StackSlotEntry,
)
from hal0.errors import BadRequest
from hal0.registry.curated import get_curated
from hal0.registry.store import ModelRegistry

ENVELOPE_KIND = "hal0.stack"


def _referenced_model_ids(stack: StackConfig) -> set[str]:
    """Every model id a stack references — slot primaries + capability rows."""
    ids: set[str] = set()
    for entry in stack.slots:
        if entry.model:
            ids.add(entry.model)
        for row in entry.capabilities:
            if row.model:
                ids.add(row.model)
    return ids


def _referenced_profile_names(stack: StackConfig) -> set[str]:
    """Every profile name a stack's slots reference."""
    return {entry.profile for entry in stack.slots if entry.profile}


def embed_references(
    stack: StackConfig,
    *,
    registry: ModelRegistry,
    profiles_path: Path | None = None,
) -> StackConfig:
    """Return a copy of ``stack`` with ``models``/``profiles`` populated.

    Model metadata is the transport-safe subset of the registry ``Model``;
    ``mmproj`` is reduced to a presence marker so a host path never travels.
    Models absent from the registry are embedded as a bare-id ``StackModelMeta``
    so the importer still sees the reference (and reports it unresolvable).
    Referenced profiles are embedded verbatim from the live profile catalog.
    ``hal0_version`` is stamped for provenance.
    """
    models: dict[str, StackModelMeta] = {}
    for mid in sorted(_referenced_model_ids(stack)):
        if registry.has(mid):
            m = registry.get(mid)
            # A registry row can carry empty coords (e.g. auto-scanned with no
            # curated match at scan time). Fall back to the curated catalogue so
            # an exported stack still carries pullable coords when we know them.
            hf_repo = m.hf_repo or ""
            hf_filename = m.hf_filename or ""
            if not (hf_repo and hf_filename):
                cur = get_curated(mid)
                if cur is not None:
                    hf_repo = hf_repo or cur.hf_repo
                    hf_filename = hf_filename or cur.hf_file
            models[mid] = StackModelMeta(
                id=m.id,
                name=m.name,
                hf_repo=hf_repo,
                hf_filename=hf_filename,
                size_bytes=m.size_bytes,
                capabilities=list(m.capabilities),
                backends=list(m.backends),
                mmproj="present" if m.mmproj else None,
            )
        else:
            models[mid] = StackModelMeta(id=mid)

    pcfg = load_profiles_config(profiles_path)
    profiles = {
        name: pcfg.profile[name]
        for name in sorted(_referenced_profile_names(stack))
        if name in pcfg.profile
    }

    return stack.model_copy(
        update={"profiles": profiles, "models": models, "hal0_version": __version__}
    )


def _checksum(stack_body: dict[str, Any]) -> str:
    """sha256 over the canonical stack body — deterministic, order-independent."""
    payload = json.dumps(stack_body, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def export_envelope(
    stack: StackConfig,
    *,
    exported_at: str,
    registry: ModelRegistry,
    profiles_path: Path | None = None,
) -> dict[str, Any]:
    """Build the ``.hal0stack.json`` envelope dict for ``stack``.

    ``exported_at`` is caller-supplied (no clock here). The checksum covers the
    embedded stack body only — re-exporting the same stack yields the same
    checksum regardless of ``exported_at``.
    """
    embedded = embed_references(stack, registry=registry, profiles_path=profiles_path)
    body = embedded.model_dump(mode="python", exclude_none=True)
    return {
        "kind": ENVELOPE_KIND,
        "schema_version": embedded.schema_version,
        "hal0_version": embedded.hal0_version,
        "exported_at": exported_at,
        "checksum": _checksum(body),
        "stack": body,
    }


# ── import ───────────────────────────────────────────────────────────────────


class StackEnvelope(BaseModel):
    """Parsed ``.hal0stack.json`` wire shape. ``extra="ignore"`` keeps a newer
    producer's extra envelope keys from breaking import; the inner StackConfig
    still forbids unknown fields."""

    model_config = {"extra": "ignore"}

    kind: str
    schema_version: int = STACK_SCHEMA_VERSION_CURRENT
    hal0_version: str = ""
    exported_at: str = ""
    checksum: str = ""
    stack: StackConfig


def parse_envelope(data: Any) -> StackEnvelope:
    """Validate the wire shape. Raises BadRequest on a non-envelope/invalid input."""
    if not isinstance(data, dict) or data.get("kind") != ENVELOPE_KIND:
        raise BadRequest(
            "not a hal0.stack envelope",
            code="stacks.bad_envelope",
            details={"kind": (data.get("kind") if isinstance(data, dict) else None)},
        )
    try:
        return StackEnvelope.model_validate(data)
    except Exception as exc:
        raise BadRequest(
            f"invalid stack envelope: {exc}",
            code="stacks.bad_envelope",
            details={"reason": str(exc)},
        ) from exc


def verify_checksum(envelope: dict[str, Any]) -> bool:
    """True when the envelope's checksum matches its stack body."""
    body = envelope.get("stack")
    if not isinstance(body, dict):
        return False
    return envelope.get("checksum") == _checksum(body)


@dataclass(frozen=True)
class ModelResolution:
    """How one referenced model id resolves against the local registry."""

    model_id: str
    status: str  # "present" | "pullable" | "unresolvable"
    hf_repo: str = ""
    hf_filename: str = ""


@dataclass
class ResolveReport:
    """Per-model resolution + convenience buckets for the import UI."""

    resolutions: list[ModelResolution] = field(default_factory=list)

    @property
    def present(self) -> list[str]:
        return [r.model_id for r in self.resolutions if r.status == "present"]

    @property
    def pullable(self) -> list[str]:
        return [r.model_id for r in self.resolutions if r.status == "pullable"]

    @property
    def unresolvable(self) -> list[str]:
        return [r.model_id for r in self.resolutions if r.status == "unresolvable"]


def resolve_models(stack: StackConfig, registry: ModelRegistry) -> ResolveReport:
    """Classify each referenced model id: present / pullable / unresolvable."""
    resolutions: list[ModelResolution] = []
    for mid in sorted(_referenced_model_ids(stack)):
        if registry.has(mid):
            resolutions.append(ModelResolution(mid, "present"))
            continue
        meta = stack.models.get(mid)
        if meta is not None and meta.hf_repo and meta.hf_filename:
            resolutions.append(ModelResolution(mid, "pullable", meta.hf_repo, meta.hf_filename))
        else:
            resolutions.append(ModelResolution(mid, "unresolvable"))
    return ResolveReport(resolutions)


def _reconcile_profiles(stack: StackConfig, profiles_path: Path | None = None) -> None:
    """Add the stack's embedded profiles that don't already exist locally.

    Name collisions keep the LOCAL profile (the importer never silently
    overwrites a profile the user already tuned).
    """
    if not stack.profiles:
        return
    pcfg = load_profiles_config(profiles_path)
    changed = False
    for name, profile in stack.profiles.items():
        if name not in pcfg.profile:
            pcfg.profile[name] = profile
            changed = True
    if changed:
        save_profiles_config(pcfg, profiles_path)


def import_stack(
    data: Any,
    slug: str,
    catalog: Any,
    *,
    registry: ModelRegistry,
    profiles_path: Path | None = None,
) -> tuple[Any, ResolveReport]:
    """Validate, reconcile profiles, create the stack, and report model resolution.

    ``catalog`` is a StacksCatalog (duck-typed: needs ``create(slug, StackConfig)``).
    Raises BadRequest for a bad/too-new envelope; the catalog raises Conflict on a
    duplicate slug.
    """
    env = parse_envelope(data)
    if env.stack.schema_version > STACK_SCHEMA_VERSION_CURRENT:
        raise BadRequest(
            f"stack schema v{env.stack.schema_version} is newer than supported "
            f"v{STACK_SCHEMA_VERSION_CURRENT}",
            code="stacks.envelope_too_new",
            details={"got": env.stack.schema_version, "supported": STACK_SCHEMA_VERSION_CURRENT},
        )
    # (forward-compat seam: older schema_version would migrate here; only v1 exists.)
    _reconcile_profiles(env.stack, profiles_path)
    resolved = catalog.create(slug, env.stack)
    report = resolve_models(env.stack, registry)
    return resolved, report


# ── snapshot from live ───────────────────────────────────────────────────────


def _read_slot_raw(slot_name: str) -> dict[str, Any] | None:
    """Tolerantly read a slot TOML into the fields a snapshot needs.

    Deliberately does NOT go through :func:`load_slot_config` /
    ``SlotConfig`` validation: the strict loader assumes the legacy nested
    ``[slot]`` shape and rejects the live flat shape the runtime writes (it
    drops ``port`` and raises) — which silently emptied every snapshot. A
    snapshot only needs the inference-surface fields, so we read the raw dict
    and accept BOTH shapes: top-level keys (flat) and a ``[slot]`` section
    (nested). ``[model]`` is a top-level section in both. Returns ``None`` only
    when the file is absent or unparseable.
    """
    path = paths.slots_config_dir() / f"{slot_name}.toml"
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None

    slot_sec = raw.get("slot") if isinstance(raw.get("slot"), dict) else {}

    def g(key: str, default: Any = None) -> Any:
        return raw.get(key, slot_sec.get(key, default))

    model_tbl = raw.get("model")
    if not isinstance(model_tbl, dict):
        model_tbl = slot_sec.get("model") if isinstance(slot_sec.get("model"), dict) else {}
    model = (model_tbl.get("default") if isinstance(model_tbl, dict) else None) or None

    server = raw.get("server") if isinstance(raw.get("server"), dict) else {}
    device = g("device")
    return {
        # id-aware (P3-runtime-db inc4): on an id-keyed box *slot_name* is the
        # digit stem, not the display name — prefer the TOML's own embedded
        # ``name`` (bilingual writers always set it) and only fall back to the
        # stem for a name-keyed (or genuinely nameless) file, where they're
        # identical anyway.
        "name": g("name", slot_name),
        "model": model,
        # device must be a canonical _VALID_DEVICES value or None — a legacy
        # backend string (e.g. "rocm") would fail StackSlotEntry validation.
        "device": device if device in _VALID_DEVICES else None,
        "provider": g("provider"),
        "profile": g("profile"),
        "vision": bool(g("vision", False)),
        "mtp": g("mtp"),
        "enable_thinking": g("enable_thinking"),
        "server_extra_args": server.get("extra_args"),
    }


def snapshot_live_stack(
    *,
    name: str = "",
    description: str = "",
    registry: ModelRegistry,
    profiles_path: Path | None = None,
) -> StackConfig:
    """Build a StackConfig from the current on-disk slots + capabilities.

    Reads ``/etc/hal0/slots/*.toml`` and ``capabilities.toml`` (HAL0_HOME-aware)
    and projects each configured slot into a StackSlotEntry. Empty seeded slots
    (no model, no capability rows) are skipped so the snapshot stays clean.
    Blank-picker capability selections (device unset) are dropped — they would
    fail StackCapabilityRow validation and carry no real config. Slot TOMLs are
    read tolerantly (see :func:`_read_slot_raw`) so the live flat shape is
    captured, not silently dropped. The result is run through
    :func:`embed_references` so it is self-contained.
    """
    caps = load_capabilities_config()
    entries: list[StackSlotEntry] = []

    for slot_name in list_slots():
        sc = _read_slot_raw(slot_name)
        if sc is None:
            # A missing/unparseable slot TOML never breaks the whole snapshot.
            continue
        display_name = sc["name"]

        rows: list[StackCapabilityRow] = []
        # capabilities.toml is never touched by the id-keying migration — it
        # stays keyed by the real slot name, so the lookup must too.
        for child, sel in caps.selections.get(display_name, {}).items():
            if sel.device not in _VALID_DEVICES:
                continue  # unset / blank-picker selection
            rows.append(
                StackCapabilityRow(
                    child=child,
                    device=sel.device,
                    provider=sel.provider,
                    model=sel.model,
                    enabled=sel.enabled,
                )
            )

        model = sc["model"]
        if model is None and not rows:
            continue  # empty seeded slot

        entries.append(
            StackSlotEntry(
                slot=display_name,
                model=model,
                device=sc["device"],
                provider=sc["provider"],
                vision=sc["vision"],
                mtp=sc["mtp"],
                enable_thinking=sc["enable_thinking"],
                server_extra_args=sc["server_extra_args"],
                profile=sc["profile"],
                capabilities=rows,
            )
        )

    stack = StackConfig(name=name, description=description, slots=entries)
    return embed_references(stack, registry=registry, profiles_path=profiles_path)
