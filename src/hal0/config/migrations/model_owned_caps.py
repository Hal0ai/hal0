"""One-shot migrator — mtp/enable_thinking/vision stick to the MODEL.

spec-hw-slot-ownership §1: the slot owns the physical/placement layer; the
model owns the logical, device-agnostic tune, including the typed
capability fields ``mtp``, ``jinja``, ``chat_template``, modality — and now
also ``enable_thinking`` (reasoning) and ``vision``. ``mtp`` was already
model-eligible (a slot override coexisted); this migration removes the
coexistence for all three:

1. A slot's raw ``mtp`` / ``enable_thinking`` / ``vision`` value (tri-state
   bool, present only when the operator explicitly set it — TOML has no
   null, so "absent" already means AUTO) folds into the bound model's
   ``defaults.mtp`` / ``.enable_thinking`` / ``.vision`` — but ONLY when the
   model does not already carry an explicit opinion for that field (a
   curator-set model default always wins over slot debris; idempotent: a
   second pass finds the model field set and does nothing).
2. Multiple slots bound to the SAME model may disagree (one slot forced
   ``mtp=true``, another ``mtp=false``) — the one-owner rule means the model
   can only take ONE value. The first slot encountered (stable file-sort
   order) wins; a divergent later slot is reported (never silently
   dropped) so the operator can review. Mirrors the ``chat_template`` fold's
   "divergent-share refusal" spirit, but reports rather than refuses, since
   forcing a manual resolution for MTP/vision (usually harmless either way)
   would block an otherwise-safe migration.
3. The slot's own ``mtp`` / ``enable_thinking`` / ``vision`` key is dropped
   from its TOML either way (folded or not) — ``SlotConfig`` no longer
   declares these fields, so leaving them on disk is pure debris; the API
   boundary also hard-rejects any new write of them (see
   ``hal0.slot_config.MODEL_OWNED_SLOT_KEYS``).

Idempotent / re-runnable / snapshot-first — same contract as
:mod:`hal0.config.migrations.hw_slot_ownership`:

* A model fold writes a field only when the model's current value is
  ``None`` (no opinion) — a re-run finds it set and does nothing.
* A slot drop only touches slots that still carry one of the three keys —
  a re-run finds nothing to drop.
* DEPLOY-WINDOW GATED, dry-run by default. NOT wired into any automatic
  boot/update path — the CLI command ``hal0 slot migrate-caps`` is the
  operator-run entrypoint (mirrors ``hal0 slot migrate-hw``).

Design mirrors ``hw_slot_ownership``: a filesystem-free **planner**
(:func:`plan_fold`, golden-testable) + a gated **applier**
(:func:`apply_fold_plan`, raw-TOML surgery + registry writes) + a live-IO
entrypoint (:func:`collect_inputs` / :func:`run_migration`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: The three tri-state keys this migration folds off the slot.
CAP_KEYS: tuple[str, ...] = ("mtp", "enable_thinking", "vision")


# ── plan ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotCapDrop:
    """One slot's ``mtp``/``enable_thinking``/``vision`` keys to remove."""

    slot_name: str
    drop_keys: tuple[str, ...]


@dataclass(frozen=True)
class ModelCapFold:
    """One model's ``defaults.<key>`` values to set (only currently-unset keys)."""

    model_id: str
    updates: Mapping[str, bool]


@dataclass(frozen=True)
class DivergentCap:
    """A cap key where ≥2 referencing slots disagreed — reported, not silent."""

    model_id: str
    key: str
    chosen_slot: str
    chosen_value: bool
    conflicting_slot: str
    conflicting_value: bool


@dataclass
class CapFoldPlan:
    """Result of :func:`plan_fold`."""

    slot_drops: list[SlotCapDrop] = field(default_factory=list)
    model_folds: list[ModelCapFold] = field(default_factory=list)
    divergent: list[DivergentCap] = field(default_factory=list)


def _slot_name(cfg: Mapping[str, Any]) -> str:
    for key in ("name", "id"):
        v = cfg.get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v)
    return "?"


# ── planner (filesystem-free) ─────────────────────────────────────────────────


def plan_fold(
    slots: Sequence[Mapping[str, Any]],
    model_defaults: Mapping[str, Mapping[str, Any]],
) -> CapFoldPlan:
    """Compute the fold plan without touching disk or the DB.

    Args:
        slots: raw slot cfg dicts (as decoded from slot TOMLs), sorted in a
            stable order (caller's responsibility — :func:`collect_inputs`
            sorts by filename) so divergence resolution is deterministic.
        model_defaults: model-id → its current ``defaults`` dict (only
            ``mtp``/``enable_thinking``/``vision`` are consulted; a model
            absent here, or with the key absent/None, has no opinion yet).

    Returns:
        A :class:`CapFoldPlan`.
    """
    plan = CapFoldPlan()
    # model_id -> key -> (value, slot_name) of the first slot that set it.
    chosen: dict[str, dict[str, tuple[bool, str]]] = {}

    for cfg in slots:
        sname = _slot_name(cfg)
        drop_keys = tuple(k for k in CAP_KEYS if k in cfg and isinstance(cfg[k], bool))
        if drop_keys:
            plan.slot_drops.append(SlotCapDrop(sname, drop_keys))

        model_tbl = cfg.get("model")
        model_tbl = model_tbl if isinstance(model_tbl, Mapping) else {}
        model_id = model_tbl.get("default")
        model_id = str(model_id) if model_id else ""
        if not model_id:
            continue

        for key in drop_keys:
            val = cfg[key]
            bucket = chosen.setdefault(model_id, {})
            if key in bucket:
                prev_val, prev_slot = bucket[key]
                if prev_val != val:
                    plan.divergent.append(
                        DivergentCap(
                            model_id=model_id,
                            key=key,
                            chosen_slot=prev_slot,
                            chosen_value=prev_val,
                            conflicting_slot=sname,
                            conflicting_value=val,
                        )
                    )
                continue
            bucket[key] = (val, sname)

    for model_id, keys in sorted(chosen.items()):
        existing = model_defaults.get(model_id) or {}
        updates = {key: val for key, (val, _slot) in keys.items() if existing.get(key) is None}
        if updates:
            plan.model_folds.append(ModelCapFold(model_id, updates))

    return plan


# ── applier (deploy-window gated, dry-run by default) ─────────────────────────


class DeployWindowRequired(RuntimeError):
    """Raised when a real write is attempted without the deploy-window ack."""


def apply_fold_plan(
    plan: CapFoldPlan,
    *,
    deploy_window: bool = False,
    dry_run: bool = True,
    job_id: str | None = None,
) -> list[str]:
    """Apply a :class:`CapFoldPlan` to the model registry and slot TOMLs.

    Model folds are applied FIRST (so a subsequent slot-TOML write failure
    never leaves a model fold half-applied without its slot debris cleared —
    the reverse order risks the opposite: a dropped slot key with the value
    never landing anywhere). Both legs are independently idempotent, so a
    partial apply is safely re-runnable regardless of ordering.

    Returns human-readable report lines. Raises :class:`DeployWindowRequired`
    for a real write requested without ``deploy_window=True``.
    """
    import tomllib

    from hal0.config.loader import write_toml_atomic
    from hal0.config.paths import slots_config_dir

    lines: list[str] = []

    if not dry_run and not deploy_window:
        raise DeployWindowRequired(
            "model_owned_caps.apply_fold_plan: refusing to write outside the "
            "deploy window — pass deploy_window=True to acknowledge "
            "(spec-hw-slot-ownership §1)."
        )

    for act in plan.divergent:
        log.warning(
            "migrate.model_caps.divergent",
            job_id=job_id,
            model=act.model_id,
            key=act.key,
            chosen_slot=act.chosen_slot,
            chosen_value=act.chosen_value,
            conflicting_slot=act.conflicting_slot,
            conflicting_value=act.conflicting_value,
            note=(
                "two slots bound to the same model disagreed on this "
                "tri-state key; the first slot (stable order) won — review "
                "and adjust the model's defaults by hand if the other value "
                "was intended"
            ),
        )
        lines.append(
            f"! divergent {act.key!r} for model {act.model_id!r}: "
            f"{act.chosen_slot!r}={act.chosen_value} won over "
            f"{act.conflicting_slot!r}={act.conflicting_value}"
        )

    # ── model registry — fold first ──────────────────────────────────────────
    if plan.model_folds:
        verb = "would fold" if dry_run else "fold"
        for mf in plan.model_folds:
            lines.append(f"{verb} model {mf.model_id!r}: defaults {dict(mf.updates)}")
        if not dry_run:
            _apply_model_folds(plan.model_folds, job_id=job_id)

    # ── slot TOMLs — drop the debris ─────────────────────────────────────────
    slots_dir = slots_config_dir()
    by_name = {d.slot_name: d for d in plan.slot_drops}
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("migrate.model_caps.slot_unreadable", slot=toml_path.stem, error=str(exc))
            continue
        drop = by_name.get(_slot_name(raw)) or by_name.get(toml_path.stem)
        if drop is None:
            continue
        verb = "would drop" if dry_run else "drop"
        lines.append(f"{verb} slot {drop.slot_name!r} keys {list(drop.drop_keys)}")
        if dry_run:
            continue
        changed = False
        for key in drop.drop_keys:
            if key in raw:
                del raw[key]
                changed = True
        if not changed:
            continue
        try:
            write_toml_atomic(toml_path, raw)
        except Exception as exc:
            log.warning("migrate.model_caps.slot_write_failed", slot=toml_path.stem, error=str(exc))
            continue
        log.warning(
            "migrate.model_caps.slot_dropped",
            job_id=job_id,
            slot=drop.slot_name,
            keys=list(drop.drop_keys),
        )

    return lines


def _apply_model_folds(folds: Sequence[ModelCapFold], *, job_id: str | None = None) -> None:
    """Write each :class:`ModelCapFold` onto its model's ``defaults`` via the registry.

    Uses the public :class:`~hal0.registry.model.ModelRegistry` (not raw SQL)
    so the write goes through the same validation/merge path as any other
    model update. Best-effort per model — a missing/broken model must never
    wedge the whole migration; it is logged and skipped (a later manual
    ``defaults`` edit or a re-run once the model exists recovers it).
    """
    from hal0.registry.model import ModelDefaults
    from hal0.registry.store import ModelNotFound, ModelRegistry

    registry = ModelRegistry()
    for mf in folds:
        try:
            model = registry.get(mf.model_id)
        except ModelNotFound:
            log.warning("migrate.model_caps.model_missing", job_id=job_id, model=mf.model_id)
            continue
        except Exception as exc:
            log.warning(
                "migrate.model_caps.model_read_failed",
                job_id=job_id,
                model=mf.model_id,
                error=str(exc),
            )
            continue
        current = model.defaults.model_dump() if model.defaults is not None else {}
        merged = {**current, **mf.updates}
        try:
            registry.update(mf.model_id, {"defaults": ModelDefaults(**merged)})
        except Exception as exc:
            log.warning(
                "migrate.model_caps.model_write_failed",
                job_id=job_id,
                model=mf.model_id,
                error=str(exc),
            )


# ── live IO entrypoint (deploy-window use) ────────────────────────────────────


def collect_inputs() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load the planner inputs from the live system.

    Returns ``(slot_raws, model_defaults)``:

    * ``slot_raws`` — each slot TOML decoded RAW (``tomllib``, NOT through
      ``SlotConfig``, since the field is no longer declared and ``extra=
      "allow"`` would not distinguish a real bool from other extras cleanly)
      with its file ``name`` (stem) injected, sorted by filename for
      deterministic divergence resolution.
    * ``model_defaults`` — model-id → its current ``defaults`` dict (``{}``
      for a model with no defaults row).
    """
    import tomllib

    from hal0.config.paths import slots_config_dir

    slot_raws: list[dict[str, Any]] = []
    slots_dir = slots_config_dir()
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw.setdefault("name", toml_path.stem)
        slot_raws.append(raw)

    model_defaults: dict[str, dict[str, Any]] = {}
    try:
        from hal0.registry.store import ModelRegistry

        registry = ModelRegistry()
        for model in registry.list():
            model_defaults[model.id] = model.defaults.model_dump() if model.defaults else {}
    except Exception as exc:
        log.warning("migrate.model_caps.model_list_failed", error=str(exc))

    return slot_raws, model_defaults


def run_migration(*, deploy_window: bool = False, dry_run: bool = True) -> list[str]:
    """One-shot: plan the fold from live state and (optionally) apply it.

    DEPLOY-WINDOW GATED and dry-run by default. A real write needs BOTH
    ``deploy_window=True`` and ``dry_run=False``. Snapshot the config dir +
    registry DB first — standard migration-window rule (the CLI command
    takes the snapshot before calling this).

    Returns the report lines.
    """
    slots, model_defaults = collect_inputs()
    plan = plan_fold(slots, model_defaults)
    return apply_fold_plan(plan, deploy_window=deploy_window, dry_run=dry_run)


__all__ = [
    "CAP_KEYS",
    "CapFoldPlan",
    "DeployWindowRequired",
    "DivergentCap",
    "ModelCapFold",
    "SlotCapDrop",
    "apply_fold_plan",
    "collect_inputs",
    "plan_fold",
    "run_migration",
]
