"""One-shot migrator — fold slot mtp/enable_thinking/vision onto the model.

spec-hw-slot-ownership §1 (model owns tuning). ``mtp`` was already partially
model-owned (``ModelDefaults.mtp`` existed and was consulted AFTER an
explicit slot override — see the pre-fix ``providers.container._effective_mtp``
precedence); this migration finishes the job for all three typed
capabilities the model now owns exclusively: ``mtp``, ``enable_thinking``
(reasoning), and ``vision`` (#901). The former ``SlotConfig.mtp`` /
``.enable_thinking`` / ``.vision`` fields are REMOVED from the schema in the
same lane (not sunset-shimmed for a release — a pre-migration slot TOML still
round-trips its stale keys harmlessly via ``SlotConfig``'s ``extra="allow"``,
and ``hal0.slots.config_write.MODEL_OWNED_SLOT_KEYS`` hard-rejects any NEW
write of them), so a live box's still-materialized slot values must be folded
onto the bound model's defaults exactly once before those keys are dropped
from disk, or the override is silently lost.

Design mirrors ``slot_flags_fold``/``hw_slot_ownership`` (this rework's
established migrator shape):

* **Pure planner** (:func:`plan_tune_fold`) — filesystem-free: takes slot cfg
  dicts + the current model ``defaults`` map, returns a :class:`FoldPlan` of
  per-model folds, divergent-share refusals, and a per-slot key-drop list.
  Golden-testable without touching disk or the registry.
* **Divergent-share refusal** — a MODEL's fold vote pool is every referencing
  slot's explicit (non-None) value for a field PLUS the model's own
  pre-existing explicit default (the exact "two writers disagree" bug this
  migration exists to fix: a slot pill and the model drawer persisting
  different values for the same fact). Two or more DISTINCT values for one
  field refuse that model (all three fields checked; any one diverging
  refuses the whole model, matching ``slot_flags_fold``'s whole-model
  granularity) and are reported, never silently picked. A slot whose target
  model is refused is excluded from the key-drop list too — the operator
  needs the raw TOML in place to resolve the conflict.
* **Idempotent** — a model whose ``defaults`` already equal the folded
  target is a no-op skip. A slot with none of the three keys present
  contributes nothing and isn't touched.
* **Dry-run** — :func:`apply_fold_plan` defaults to ``dry_run=True`` (report
  the plan, write nothing). Callers opt in to writes explicitly.

DEPLOY-WINDOW GATED, dry-run by default — same contract as its siblings. It
is NOT wired into the automatic ``hal0.config.migrations`` schema-version
runner and does NOT run on boot: a real write needs BOTH
``deploy_window=True`` and ``dry_run=False`` (see ``hal0 slot migrate-tune``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: The three typed capabilities the model owns exclusively now (§1). Kept as
#: a tuple (not re-derived from MODEL_OWNED_SLOT_KEYS) so this planner has no
#: import-time dependency on hal0.slots.config_write — it only needs the
#: field NAMES, and staying decoupled keeps the golden-tested planner free of
#: any live-guard side effects.
TUNE_FIELDS: tuple[str, ...] = ("mtp", "enable_thinking", "vision")


# ── plan ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotTune:
    """One slot's explicit (non-None) contribution to a model's fold."""

    slot_name: str
    model_id: str
    values: dict[str, bool]


@dataclass(frozen=True)
class DivergentRefusal:
    """A MODEL received 2+ distinct values for one field — refused, reported.

    ``votes`` maps each distinct voter (a slot name, or the literal
    ``"<model default>"`` for the model's own pre-existing explicit value) to
    the value it wants, so the operator sees exactly where the disagreement
    comes from.
    """

    model_id: str
    field: str
    votes: dict[str, bool]


@dataclass(frozen=True)
class ModelFold:
    """A model that will receive the resolved (consensus) tune values."""

    model_id: str
    updates: dict[str, bool]
    #: The complete new ``defaults`` dict to persist (existing merged with fold).
    new_defaults: dict[str, Any]
    slot_names: tuple[str, ...]


@dataclass
class FoldPlan:
    """Result of :func:`plan_tune_fold`."""

    folds: list[ModelFold] = field(default_factory=list)
    refusals: list[DivergentRefusal] = field(default_factory=list)
    #: (model_id, reason) for models with nothing to fold / already folded.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: slot_name -> the on-disk keys to drop (tune keys the slot TOML still
    #: carries). Populated for EVERY slot carrying any of the three keys,
    #: except one feeding a refused model (left alone for the operator).
    slot_key_drops: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when nothing was refused — safe to apply the whole plan."""
        return not self.refusals


def _slot_name(cfg: Mapping[str, Any]) -> str:
    for key in ("name", "id"):
        v = cfg.get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v)
    return "?"


def plan_tune_fold(
    slots: Sequence[Mapping[str, Any]],
    model_defaults: Mapping[str, Mapping[str, Any] | None],
) -> FoldPlan:
    """Compute the fold plan without touching disk or the registry.

    Args:
        slots: raw slot cfg dicts (as loaded from slot TOMLs, RAW — not
            through ``SlotConfig``, so a pre-migration ``mtp``/
            ``enable_thinking``/``vision`` key at the top level survives even
            after the schema field is removed).
        model_defaults: model-id -> the model's current ``defaults`` dict (or
            ``None``). A model absent here folds onto an empty defaults.

    Returns:
        A :class:`FoldPlan`. When 2+ voters (referencing slots, or the
        model's own pre-existing default) disagree on one field for one
        model, that model is a :class:`DivergentRefusal` (not folded), and
        none of its referencing slots have their keys dropped.
    """
    by_model: dict[str, list[SlotTune]] = {}
    slots_with_keys: dict[str, tuple[str, ...]] = {}

    for cfg in slots:
        name = _slot_name(cfg)
        present = tuple(k for k in TUNE_FIELDS if k in cfg)
        if present:
            slots_with_keys[name] = present

        model_tbl = cfg.get("model")
        model_tbl = model_tbl if isinstance(model_tbl, Mapping) else {}
        model_id = model_tbl.get("default")
        if not model_id:
            continue
        model_id = str(model_id)

        values = {k: cfg[k] for k in TUNE_FIELDS if isinstance(cfg.get(k), bool)}
        if not values:
            continue
        by_model.setdefault(model_id, []).append(
            SlotTune(slot_name=name, model_id=model_id, values=values)
        )

    plan = FoldPlan()
    refused_models: set[str] = set()

    # Every model with EITHER a referencing slot's tune OR its own existing
    # explicit tune needs a resolve pass — a model nobody points at anymore
    # (existing-only) is untouched (nothing to fold from a slot).
    for model_id, refs in sorted(by_model.items()):
        existing = model_defaults.get(model_id)
        existing = existing if isinstance(existing, Mapping) else {}
        resolved: dict[str, bool] = {}
        divergent = False
        for f in TUNE_FIELDS:
            votes: dict[str, bool] = {}
            ex = existing.get(f)
            if isinstance(ex, bool):
                votes["<model default>"] = ex
            for r in refs:
                if f in r.values:
                    votes[r.slot_name] = r.values[f]
            distinct = set(votes.values())
            if len(distinct) > 1:
                plan.refusals.append(DivergentRefusal(model_id=model_id, field=f, votes=votes))
                divergent = True
                continue
            if len(distinct) == 1:
                resolved[f] = next(iter(distinct))
        if divergent:
            refused_models.add(model_id)
            continue

        new_defaults = dict(existing)
        new_defaults.update(resolved)
        if all(existing.get(k) == v for k, v in resolved.items()):
            plan.skipped.append((model_id, "already folded (no-op)"))
        else:
            plan.folds.append(
                ModelFold(
                    model_id=model_id,
                    updates=resolved,
                    new_defaults=new_defaults,
                    slot_names=tuple(r.slot_name for r in refs),
                )
            )

    # Every slot carrying any of the three keys gets them dropped, UNLESS its
    # target model was refused (operator needs the raw TOML to resolve).
    slot_model: dict[str, str] = {}
    for model_id, refs in by_model.items():
        for r in refs:
            slot_model[r.slot_name] = model_id
    for name, keys in slots_with_keys.items():
        if slot_model.get(name) in refused_models:
            continue
        plan.slot_key_drops[name] = keys

    return plan


# ── applier (deploy-window gated, dry-run by default) ─────────────────────────


class DeployWindowRequired(RuntimeError):
    """Raised when a real write is attempted without the deploy-window ack."""


def _drop_slot_keys(
    slot_key_drops: Mapping[str, tuple[str, ...]], *, job_id: str | None = None
) -> None:
    """Strip the folded tune keys from each slot's TOML in place.

    Best-effort per file: a slot TOML that vanished or fails to parse/write
    is logged and skipped rather than aborting the whole migration.
    """
    import tomllib

    from hal0.config.loader import write_toml_atomic
    from hal0.config.paths import slots_config_dir

    slots_dir = slots_config_dir()
    if not slots_dir.is_dir():
        return
    by_stem = {p.stem: p for p in slots_dir.glob("*.toml")}
    for name, keys in slot_key_drops.items():
        toml_path = by_stem.get(name)
        if toml_path is None:
            continue
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("migrate.model_tune.slot_unreadable", slot=name, error=str(exc))
            continue
        changed = False
        for key in keys:
            if key in raw:
                del raw[key]
                changed = True
        if not changed:
            continue
        try:
            write_toml_atomic(toml_path, raw)
        except Exception as exc:
            log.warning("migrate.model_tune.slot_write_failed", slot=name, error=str(exc))
            continue
        log.warning(
            "migrate.model_tune.slot_keys_dropped", job_id=job_id, slot=name, keys=list(keys)
        )


def apply_fold_plan(
    plan: FoldPlan,
    registry: Any,
    *,
    deploy_window: bool = False,
    dry_run: bool = True,
) -> list[str]:
    """Apply a :class:`FoldPlan` to the model registry and slot TOMLs.

    Args:
        plan: the plan from :func:`plan_tune_fold`.
        registry: a ``hal0.registry.store.ModelRegistry`` (needs ``.update``).
        deploy_window: explicit ack that this runs inside the deploy window
            (spec §6-style contract). WITHOUT it, any non-dry-run write
            raises :class:`DeployWindowRequired`.
        dry_run: when True (default) nothing is written; the returned lines
            describe what WOULD happen.

    Returns:
        Human-readable report lines (folds applied/planned, refusals, skips,
        slot key drops).

    Raises:
        DeployWindowRequired: a real write was requested without ``deploy_window``.
        RuntimeError: the plan has divergent-share refusals — refuse to apply
            a partial fold; the operator must resolve the conflicts first.
    """
    lines: list[str] = []

    if plan.refusals:
        for r in plan.refusals:
            votes = ", ".join(f"{s}={v}" for s, v in sorted(r.votes.items()))
            lines.append(f"REFUSE model {r.model_id!r} field {r.field!r}: divergent -> {votes}")
        raise RuntimeError(
            f"model_tune_ownership refuses {len({r.model_id for r in plan.refusals})} "
            "model(s) with divergent slot/model tune values; resolve (pick one "
            "value, or edit the model directly) and re-run. " + " | ".join(lines)
        )

    for m in plan.skipped:
        lines.append(f"skip model {m[0]!r}: {m[1]}")

    for fold in plan.folds:
        verb = "would fold" if dry_run else "fold"
        lines.append(
            f"{verb} {fold.model_id!r} <- slots={list(fold.slot_names)} updates={fold.updates}"
        )
        if dry_run:
            continue
        if not deploy_window:
            raise DeployWindowRequired(
                "model_tune_ownership.apply_fold_plan: refusing to write outside "
                "the deploy window — pass deploy_window=True to acknowledge."
            )
        registry.update(fold.model_id, {"defaults": fold.new_defaults})

    if plan.slot_key_drops:
        verb = "would drop" if dry_run else "drop"
        for name, keys in sorted(plan.slot_key_drops.items()):
            lines.append(f"{verb} slot {name!r} keys={list(keys)}")
        if not dry_run:
            if not deploy_window:
                raise DeployWindowRequired(
                    "model_tune_ownership.apply_fold_plan: refusing to write "
                    "outside the deploy window — pass deploy_window=True to "
                    "acknowledge."
                )
            _drop_slot_keys(plan.slot_key_drops)

    return lines


# ── live IO entrypoint (deploy-window use) ────────────────────────────────────


def collect_inputs() -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
    """Load the planner inputs from the live system (slots + registry).

    Returns ``(slot_raws, model_defaults, registry)``. ``slot_raws`` reads
    each slot TOML RAW (``tomllib``, not through ``SlotConfig``) so a
    pre-migration ``mtp``/``enable_thinking``/``vision`` key survives even
    once the schema field is removed. Kept separate from
    :func:`plan_tune_fold` so the planner stays filesystem-free and
    unit-testable; only this and :func:`run_migration` touch disk/the
    registry.
    """
    import tomllib

    from hal0.config.paths import slots_config_dir
    from hal0.registry.store import ModelRegistry

    slot_raws: list[dict[str, Any]] = []
    slots_dir = slots_config_dir()
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw.setdefault("name", toml_path.stem)
        slot_raws.append(raw)

    registry = ModelRegistry()
    model_defaults: dict[str, Any] = {}
    for m in registry.list():
        d = m.defaults
        model_defaults[m.id] = d.model_dump() if d is not None else None

    return slot_raws, model_defaults, registry


def run_migration(*, deploy_window: bool = False, dry_run: bool = True) -> list[str]:
    """One-shot: plan the fold from live state and (optionally) apply it.

    DEPLOY-WINDOW GATED and dry-run by default. A real write needs BOTH
    ``deploy_window=True`` and ``dry_run=False``. Snapshot the config dir +
    registry first — standard migration-window rule — before a non-dry-run
    pass (see ``hal0 slot migrate-tune``, which takes the backup).

    Returns the report lines. Raises on divergent-share refusal (see
    :func:`apply_fold_plan`).
    """
    slots, model_defaults, registry = collect_inputs()
    plan = plan_tune_fold(slots, model_defaults)
    return apply_fold_plan(plan, registry, deploy_window=deploy_window, dry_run=dry_run)


__all__ = [
    "TUNE_FIELDS",
    "DeployWindowRequired",
    "DivergentRefusal",
    "FoldPlan",
    "ModelFold",
    "SlotTune",
    "apply_fold_plan",
    "collect_inputs",
    "plan_tune_fold",
    "run_migration",
]
