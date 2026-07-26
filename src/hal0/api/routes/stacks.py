"""Stack catalog + apply endpoints.

Mounted under /api/stacks (spec §8 of
docs/superpowers/specs/2026-06-19-stacks-design.md):

    GET    ""                  — list every stack (+ active flag + drift status)
    POST   ""                  — create a stack (hand-built or from a snapshot payload) (201)
    GET    "/{slug}"           — stack detail (+ active flag + drift status)
    PUT    "/{slug}"           — replace a custom stack (200)
    DELETE "/{slug}"           — delete a custom stack (204)
    POST   "/{slug}/apply"     — ?dry_run=true → diff preview; else commit + converge
    POST   "/{slug}/export"    — build the portable .hal0stack.json envelope
    POST   "/import"           — {dry_run?, slug?, envelope} → resolve report or create
    POST   "/snapshot"         — build a StackConfig from current live config

Seed stacks (defined in ``schema.SEED_STACKS``) are immutable via the API; the
catalog owns the seed guard, slug validation, and atomic full-catalog writes.

The route layer is a thin adapter: catalog CRUD goes through
:class:`hal0.stacks.StacksCatalog`; the declarative apply (Phase A config +
Phase B lifecycle convergence) goes through
:class:`hal0.stacks.apply.StackApplyEngine`; export/import/snapshot go through
:mod:`hal0.stacks.portable`. Wall-clock-dependent values (``exported_at``,
``applied_at``) are stamped here — the engine and portable layers stay pure.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from hal0.api._audit import record_action
from hal0.config import paths
from hal0.config.schema import StackConfig
from hal0.errors import BadRequest
from hal0.model_meta import DEVICE_TO_DEFAULT_PROFILE
from hal0.profiles import ProfileCatalog
from hal0.stacks import ResolvedStack, StacksCatalog
from hal0.stacks.apply import StackApplyEngine
from hal0.stacks.portable import (
    export_envelope,
    import_stack,
    parse_envelope,
    resolve_models,
    snapshot_live_stack,
    verify_checksum,
)

router = APIRouter()


# ── request models ────────────────────────────────────────────────────────────


class StackCreateBody(BaseModel):
    """Body for POST /api/stacks — slug + the full stack body.

    The ``stack`` payload is whatever ``POST /snapshot`` returns or the editor
    assembled, so create and snapshot-then-save share one shape.
    """

    model_config = {"extra": "forbid"}

    slug: str = Field(..., description="Stack key (kebab-case, ≤32 chars, leading alphanumeric).")
    stack: StackConfig = Field(..., description="The stack body to persist under ``slug``.")


class SnapshotBody(BaseModel):
    """Body for POST /api/stacks/snapshot.

    With no ``slug`` the snapshot is returned un-persisted for the editor to
    review and name; with ``slug`` it is also created in the catalog.
    """

    model_config = {"extra": "forbid"}

    name: str = Field(default="", description="Display name for the snapshot stack.")
    description: str = Field(default="", description="What this snapshot captures.")
    slug: str | None = Field(
        default=None,
        description="When set, persist the snapshot under this slug (else return it un-saved).",
    )


# ── serialization helpers ──────────────────────────────────────────────────────


def _stack_to_dict(
    s: ResolvedStack, *, active: bool = False, drift: str | None = None
) -> dict[str, Any]:
    """Render a ResolvedStack as a JSON object for list/detail responses."""
    return {
        "slug": s.slug,
        "name": s.name,
        "description": s.description,
        "author": s.author,
        "icon": s.icon,
        "tags": list(s.tags),
        "schema_version": s.schema_version,
        "hal0_version": s.hal0_version,
        "seed": s.seed,
        "slots": [e.model_dump(mode="json") for e in s.slots],
        "profiles": {k: v.model_dump(mode="json") for k, v in s.profiles.items()},
        "models": {k: v.model_dump(mode="json") for k, v in s.models.items()},
        "active": active,
        "drift": drift,
    }


def _config_of(s: ResolvedStack) -> StackConfig:
    """Rebuild the persisted StackConfig from a ResolvedStack (drops derived fields).

    Mirrors the reconstruction the apply engine's ``drift_status`` already does;
    the apply/export paths need a StackConfig, the catalog hands back a
    ResolvedStack.
    """
    return StackConfig(
        name=s.name,
        description=s.description,
        author=s.author,
        icon=s.icon,
        tags=list(s.tags),
        schema_version=s.schema_version,
        hal0_version=s.hal0_version,
        slots=list(s.slots),
        profiles=dict(s.profiles),
        models=dict(s.models),
    )


def _registry(request: Request) -> Any:
    """The live ModelRegistry from app.state (populated by the lifespan)."""
    reg = getattr(request.app.state, "model_registry", None)
    if reg is None:  # pragma: no cover — lifespan always sets it in real runs
        raise BadRequest(
            "model registry not initialized",
            code="stacks.registry_unavailable",
        )
    return reg


# A stack may name a slot that doesn't exist yet (e.g. a "quick" coder slot in a
# cloned/imported stack). Apply must create it before the converge load, the way
# the spec's seed copy expects. Without a profile a fresh slot fails to load
# ("profile '' not found"), so default a coherent profile from the device via
# the canonical map (hal0.model_meta.DEVICE_TO_DEFAULT_PROFILE) — this replaced
# a local copy whose ``cpu`` entry was ``""``, which left a cpu-device stack
# slot born broken with exactly that "profile '' not found" error; the
# canonical ``cpu → "cpu-llm"`` seed profile is the deliberate unification.


def _slot_toml_exists(slot: str) -> bool:
    return (paths.slots_config_dir() / f"{slot}.toml").exists()


def _missing_slot_names(cfg: StackConfig) -> list[str]:
    """Stack slots (with a model) whose TOML doesn't exist yet."""
    return [e.slot for e in cfg.slots if e.model and not _slot_toml_exists(e.slot)]


async def _create_missing_slots(
    cfg: StackConfig, slot_manager: Any
) -> tuple[list[str], list[dict[str, str]]]:
    """Create any stack slot whose TOML is absent, via the live slot-create path.

    Reuses ``routes/slots._normalize_create_body`` (flat→nested + free-port
    assignment) and ``SlotManager.create`` so a created slot is identical to one
    made through POST /api/slots. Per-slot failures are collected, not raised, so
    one bad entry doesn't sink the whole apply. Returns ``(created, errors)``.
    """
    from hal0.api.routes.slots import _normalize_create_body

    created: list[str] = []
    errors: list[dict[str, str]] = []
    for entry in cfg.slots:
        if not entry.model or _slot_toml_exists(entry.slot):
            continue
        device = entry.device or "gpu-vulkan"
        profile = entry.profile or DEVICE_TO_DEFAULT_PROFILE.get(device, "")
        # spec-hw-slot-ownership §1: vision/mtp are model-owned typed
        # capabilities now, so a stack-created slot must not be born carrying
        # them. `SlotManager.create` doesn't route through
        # `reconcile_slot_updates`, so the MODEL_OWNED_SLOT_KEYS guard can't
        # catch this path — dropping them here is what keeps stack-created
        # slots on the right side of the partition. `entry.vision`/`entry.mtp`
        # stay on the stack schema for back-compat but no longer project onto
        # the slot (matching `stacks.apply._reconciled_stack_slot`).
        body: dict[str, Any] = {
            "name": entry.slot,
            "type": "llm",
            "model": entry.model,
            "device": device,
            "profile": profile,
        }
        if entry.server_extra_args:
            body["server"] = {"extra_args": entry.server_extra_args}
        try:
            await slot_manager.create(entry.slot, _normalize_create_body(body))
            created.append(entry.slot)
        except Exception as exc:
            errors.append({"target": entry.slot, "error": str(exc)})
    return created, errors


def _known_profile_names() -> set[str]:
    """Profile names in the local catalog (seed-merged); empty on read failure.

    Feeds ``StackApplyEngine.validate`` so apply can flag a slot pinned to a
    profile this host doesn't have. Best-effort: a malformed profiles.toml must
    not sink the apply preview.
    """
    try:
        return {p.name for p in ProfileCatalog().list()}
    except Exception:  # pragma: no cover — advisory validation, never fatal
        return set()


def _known_model_ids(request: Request) -> set[str]:
    """Model ids in the live registry; empty when the registry is unavailable.

    Feeds ``StackApplyEngine.validate`` so apply can flag a slot whose model id
    isn't registered on this host.
    """
    try:
        return {m.id for m in _registry(request).list()}
    except Exception:  # pragma: no cover — advisory validation, never fatal
        return set()


def _diff_rows(plan: Any) -> list[dict[str, Any]]:
    """Per-slot before→after model summary for the dry-run preview UI."""
    rows: list[dict[str, Any]] = []
    for before, after in zip(plan.change_set.before, plan.change_set.after, strict=True):
        b_model = (before.data or {}).get("model", {}).get("default") if before.data else None
        a_model = (after.data or {}).get("model", {}).get("default") if after.data else None
        rows.append(
            {
                "slot": after.path.stem,
                "before_model": b_model,
                "after_model": a_model,
                "changed": before.data != after.data,
            }
        )
    return rows


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("")
def list_stacks() -> dict[str, Any]:
    """List every stack, annotated with the active stack + its drift status.

    Shape::

        {
          "stacks": [ {slug, name, ..., seed, active, drift}, ... ],
          "active": "coding" | null,
          "drift":  "clean" | "degraded" | "modified" | "none"
        }

    ``drift`` is meaningful for the active stack; the matching item also carries
    ``active: true`` and its own ``drift`` value.
    """
    catalog = StacksCatalog()
    drift = StackApplyEngine().drift_status(catalog)
    active_slug = drift.get("active")
    status = drift.get("status", "none")
    items = [
        _stack_to_dict(
            s,
            active=(s.slug == active_slug),
            drift=(status if s.slug == active_slug else None),
        )
        for s in catalog.list()
    ]
    return {"stacks": items, "active": active_slug, "drift": status}


@router.post("", status_code=201)
async def create_stack(body: StackCreateBody, request: Request) -> dict[str, Any]:
    """Create a stack from a slug + full stack body.

    Raises:
        409 stacks.exists: slug already exists.
        409 stacks.invalid_name: slug isn't kebab-case ≤32 chars.
        422: pydantic validation failure on the stack body.
    """
    async with record_action(
        request, category="stack", action="stack.create", target=body.slug
    ) as rec:
        resolved = StacksCatalog().create(body.slug, body.stack)
        rec.after = {"slug": body.slug, "name": body.stack.name, "slots": len(body.stack.slots)}
    return _stack_to_dict(resolved)


@router.get("/{slug}")
def get_stack(slug: str) -> dict[str, Any]:
    """Return one stack with its active flag + drift status.

    Raises:
        404 stacks.not_found: no such stack.
    """
    catalog = StacksCatalog()
    resolved = catalog.resolve(slug)
    drift = StackApplyEngine().drift_status(catalog)
    is_active = drift.get("active") == slug
    return _stack_to_dict(
        resolved,
        active=is_active,
        drift=(drift.get("status") if is_active else None),
    )


@router.put("/{slug}")
async def update_stack(slug: str, body: StackConfig, request: Request) -> dict[str, Any]:
    """Replace a custom stack's body wholesale (PUT semantics).

    Raises:
        409 stacks.seed_immutable: slug is a seed stack.
        404 stacks.not_found: custom stack not found.
        422: pydantic validation failure.
    """
    catalog = StacksCatalog()
    before = None
    try:
        before = _stack_to_dict(catalog.resolve(slug))
    except Exception:
        before = None
    async with record_action(
        request, category="stack", action="stack.update", target=slug, before=before
    ) as rec:
        resolved = catalog.update(slug, body)
        rec.after = {"slug": slug, "name": body.name, "slots": len(body.slots)}
    return _stack_to_dict(resolved)


@router.delete("/{slug}", status_code=204)
async def delete_stack(slug: str, request: Request) -> None:
    """Delete a custom stack.

    Raises:
        409 stacks.seed_immutable: slug is a seed stack.
        404 stacks.not_found: custom stack not found.
    """
    async with record_action(request, category="stack", action="stack.delete", target=slug):
        StacksCatalog().delete(slug)


@router.post("/{slug}/apply")
async def apply_stack(slug: str, request: Request, dry_run: bool = False) -> dict[str, Any]:
    """Apply a stack declaratively.

    ``?dry_run=true`` computes the before→after diff and writes nothing.
    Otherwise: commit the slot-config ChangeSet atomically (Phase A), record the
    active-stack pointer, then converge runtime lifecycle to match (Phase B).
    Phase-B per-slot failures are reported, never raised — a committed config is
    never unwound by a lifecycle hiccup.

    Raises:
        404 stacks.not_found: no such stack.
    """
    catalog = StacksCatalog()
    resolved = catalog.resolve(slug)
    cfg = _config_of(resolved)

    if dry_run:
        engine = StackApplyEngine()
        plan = engine.plan(slug, cfg)
        return {
            "stack": slug,
            "dry_run": True,
            "summary": plan.summary,
            "changes": _diff_rows(plan),
            # Slots the stack names that don't exist yet — apply will create them.
            "creates": _missing_slot_names(cfg),
            # Profile/model refs that won't resolve on this host (silent divergence).
            "warnings": engine.validate(cfg, _known_profile_names(), _known_model_ids(request)),
        }

    slot_manager = getattr(request.app.state, "slot_manager", None)
    orchestrator = getattr(request.app.state, "capability_orchestrator", None)
    engine = StackApplyEngine(slot_manager=slot_manager, orchestrator=orchestrator)

    async with record_action(request, category="stack", action="stack.apply", target=slug) as rec:
        # Create-on-apply: a stack may reference a slot that doesn't exist yet.
        # Create it BEFORE planning so Phase A reconciles it and converge loads
        # it (spec §10 "non-seed slots are created on apply"). Needs a live slot
        # manager; without one (degraded), missing slots are simply skipped.
        created: list[str] = []
        create_errors: list[dict[str, str]] = []
        if slot_manager is not None:
            created, create_errors = await _create_missing_slots(cfg, slot_manager)

        plan = engine.plan(slug, cfg)
        engine.apply_config(plan)
        # converge BEFORE recording active so the pointer carries the Phase-B
        # outcome (PS-5 part 2). A slot-create or per-slot lifecycle failure
        # means disk is honest but runtime never fully came up → converge_ok
        # False so drift_status reports ``degraded`` rather than ``clean``.
        converged: dict[str, Any] = {}
        converge_ok = not create_errors
        if slot_manager is not None and orchestrator is not None:
            report = await engine.converge(cfg)
            converge_ok = converge_ok and not report.errors
            converged = {
                "loaded": report.loaded,
                "swapped": report.swapped,
                "skipped": report.skipped,
                "unloaded": report.unloaded,
                "capabilities_applied": report.capabilities_applied,
                "errors": [{"target": t, "error": e} for t, e in report.errors] + create_errors,
            }
        engine.record_active(plan, applied_at=time.time(), converge_ok=converge_ok)
        rec.after = {
            "slug": slug,
            "changed": sum(1 for r in _diff_rows(plan) if r["changed"]),
            "created": created,
        }

    return {
        "stack": slug,
        "created": created,
        "dry_run": False,
        "summary": plan.summary,
        "changes": _diff_rows(plan),
        "converged": converged,
        # Same pre-apply divergence flags the dry-run preview surfaces.
        "warnings": engine.validate(cfg, _known_profile_names(), _known_model_ids(request)),
    }


@router.post("/{slug}/export")
def export_stack(slug: str, request: Request) -> dict[str, Any]:
    """Serialize a stack into its portable ``.hal0stack.json`` envelope.

    Embeds referenced profiles + model metadata (never weights, never secrets,
    never host paths) and stamps ``exported_at`` + a content checksum.

    Raises:
        404 stacks.not_found: no such stack.
    """
    resolved = StacksCatalog().resolve(slug)
    cfg = _config_of(resolved)
    exported_at = datetime.now(UTC).isoformat()
    return export_envelope(cfg, exported_at=exported_at, registry=_registry(request))


@router.post("/import")
async def import_stack_route(request: Request) -> dict[str, Any]:
    """Import a stack from an uploaded ``.hal0stack.json`` envelope.

    Body::

        { "envelope": {...}, "slug": "name", "dry_run": false }

    ``dry_run`` validates the envelope + classifies model refs (present /
    pullable / unresolvable) without creating anything. A commit reconciles
    embedded profiles, creates the stack, and returns the same resolve report so
    the UI can offer one-click pulls for missing models.

    Raises:
        400 stacks.bad_envelope: not a valid hal0.stack envelope.
        400 stacks.import_no_slug: commit requested without a slug.
        409 stacks.exists: slug already exists.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            code="request.invalid_json",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")

    envelope = body.get("envelope", body)
    dry_run = bool(body.get("dry_run", False))
    slug = body.get("slug")
    registry = _registry(request)

    if dry_run:
        env = parse_envelope(envelope)
        report = resolve_models(env.stack, registry)
        return {
            "dry_run": True,
            "valid": True,
            "checksum_ok": verify_checksum(envelope) if isinstance(envelope, dict) else False,
            "name": env.stack.name,
            "schema_version": env.stack.schema_version,
            "resolutions": [
                {
                    "model_id": r.model_id,
                    "status": r.status,
                    "hf_repo": r.hf_repo,
                    "hf_filename": r.hf_filename,
                }
                for r in report.resolutions
            ],
            "present": report.present,
            "pullable": report.pullable,
            "unresolvable": report.unresolvable,
        }

    if not slug or not isinstance(slug, str):
        raise BadRequest(
            "import commit requires a 'slug'",
            code="stacks.import_no_slug",
        )

    async with record_action(request, category="stack", action="stack.import", target=slug) as rec:
        resolved, report = import_stack(envelope, slug, StacksCatalog(), registry=registry)
        rec.after = {"slug": slug, "pullable": report.pullable, "unresolvable": report.unresolvable}
    return {
        "dry_run": False,
        "stack": _stack_to_dict(resolved),
        "present": report.present,
        "pullable": report.pullable,
        "unresolvable": report.unresolvable,
    }


@router.post("/snapshot")
async def snapshot_stack(body: SnapshotBody, request: Request) -> dict[str, Any]:
    """Build a stack from the current live slot + capability config.

    Without ``slug`` the snapshot is returned un-persisted for review; with
    ``slug`` it is also created in the catalog.

    Raises:
        409 stacks.exists: slug already exists (commit path).
    """
    registry = _registry(request)
    cfg = snapshot_live_stack(name=body.name, description=body.description, registry=registry)

    if not body.slug:
        return {"created": False, "stack": cfg.model_dump(mode="json")}

    async with record_action(
        request, category="stack", action="stack.snapshot", target=body.slug
    ) as rec:
        resolved = StacksCatalog().create(body.slug, cfg)
        rec.after = {"slug": body.slug, "slots": len(cfg.slots)}
    return {"created": True, "stack": _stack_to_dict(resolved)}


__all__ = ["router"]
