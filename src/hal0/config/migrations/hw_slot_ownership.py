"""One-shot migrator — unwind the flags-fold, hardware sticks to SLOTS.

spec-hw-slot-ownership §6. Reverses the shipped spec-flags-ownership §5 fold
(``slot_flags_fold``) along the physical axis: the model goes back to being
purely logical/device-agnostic, and each slot re-acquires its own typed
hardware grid ``[device · NGL · THREADS · BINARY]`` + optional ``image_pin``.

Four folds, all onto the SLOT (spec §6):

1. ``model.defaults.n_gpu_layers`` (the DB ``model.n_gpu_layers`` column the §5
   fold populated) AND a slot's own nested ``[model].n_gpu_layers`` →
   slot top-level **NGL** (:attr:`SlotConfig.n_gpu_layers`). A slot's own nested
   value wins over the model default (the more specific override). The nested
   ``[model].n_gpu_layers`` is then dropped (sunset).
2. ``model.preferred_runner`` (the DB ``model.preferred_runner`` column) → slot
   **BINARY** (:attr:`SlotConfig.binary`).
3. ``profile.image`` **deliberate** pins → ``image_pin`` of every referencing
   slot (fold-to-slots, each gets its own copy). Former-default **debris** —
   an image in :data:`~hal0.runners.STALE_RUNNER_IMAGE_REFS` — is dropped, never
   folded. A profile whose deliberate pin has **zero** referencing slots logs
   the lost pin. ``profile.image`` is deleted either way.
4. ``slot.image`` / ``[slot].image`` → slot **image_pin**; the ``image`` key is
   collapsed away. Same deliberate-vs-debris rule as #3. A slot's own image wins
   over its profile's (old ``_resolve_image_ref`` precedence: slot → profile).

Reuses the :func:`hal0.updater.retag_stale_slot_images` machinery's
deliberate-pin-vs-former-default-debris distinction
(:data:`~hal0.runners.STALE_RUNNER_IMAGE_REFS`) on both slot and profile images.

Idempotent / re-runnable / snapshot-first:

* A slot fold writes NGL/BINARY/image_pin only when that slot field is still
  UNSET (NGL: the ``-1``/absent sentinel; BINARY: ``""``; image_pin: absent) —
  so a second pass finds them set and does nothing.
* After folding, the migration NULLs the model ``n_gpu_layers`` /
  ``preferred_runner`` columns (debris left by the §5 fold), so a re-run has
  nothing to fold and the columns are clean for their eventual schema drop.
* Snapshot the config dir + registry DB first — the standard migration-window
  rule — before a non-dry-run pass.

DEPLOY-WINDOW GATED, dry-run by default — same contract as
:mod:`hal0.config.migrations.slot_flags_fold`. It is NOT wired into the
automatic ``hal0.config.migrations`` schema-version runner and it does NOT run
on boot: a real write needs BOTH ``deploy_window=True`` and ``dry_run=False``.

Design mirrors ``slot_flags_fold``: a filesystem-free **planner**
(:func:`plan_unfold`, golden-testable) + a gated **applier**
(:func:`apply_unfold_plan`, raw-TOML surgery + DB column NULL) + a live-IO
entrypoint (:func:`collect_inputs` / :func:`run_migration`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── inputs ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelHw:
    """The two old physical facts a §5-migrated model row still carries.

    Read RAW from the ``model`` table's kept-but-nulled-by-new-code
    ``n_gpu_layers`` / ``preferred_runner`` columns (see
    ``hal0.db.repository`` — both are written NULL from the current schema but
    old values persist on live boxes until this migration folds them out).
    """

    n_gpu_layers: int | None
    preferred_runner: str | None


# ── plan ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotFold:
    """The per-slot mutation the applier writes to one slot TOML."""

    slot_name: str
    #: Write slot top-level ``n_gpu_layers`` (NGL) — None = leave as-is.
    set_ngl: int | None = None
    #: Write slot top-level ``binary`` (BINARY) — None = leave as-is.
    set_binary: str | None = None
    #: Write slot top-level ``image_pin`` — None = leave as-is.
    set_image_pin: str | None = None
    #: Remove the sunset nested ``[model].n_gpu_layers`` key.
    drop_nested_ngl: bool = False
    #: Remove the slot's own ``image`` key (top-level or ``[slot]``-nested).
    drop_slot_image: bool = False

    @property
    def is_noop(self) -> bool:
        return (
            self.set_ngl is None
            and self.set_binary is None
            and self.set_image_pin is None
            and not self.drop_nested_ngl
            and not self.drop_slot_image
        )


@dataclass(frozen=True)
class ProfileAction:
    """What happens to one ``[profile.<name>].image`` pin.

    ``disposition``:
      * ``"folded"``  — deliberate pin copied onto ≥1 referencing slot, deleted.
      * ``"debris"``  — a stale former default (STALE_RUNNER_IMAGE_REFS), deleted.
      * ``"lost"``    — deliberate pin with NO referencing slot; logged + deleted.
    """

    profile_name: str
    old_image: str
    disposition: str
    slot_names: tuple[str, ...] = ()


@dataclass
class UnfoldPlan:
    """Result of :func:`plan_unfold`."""

    slot_folds: list[SlotFold] = field(default_factory=list)
    profile_actions: list[ProfileAction] = field(default_factory=list)
    #: model ids whose ``n_gpu_layers``/``preferred_runner`` columns are
    #: non-NULL debris to clear after folding.
    model_nulls: list[str] = field(default_factory=list)

    def slot_fold(self, name: str) -> SlotFold | None:
        return next((f for f in self.slot_folds if f.slot_name == name), None)


# ── slot-shape helpers (flat top-level vs nested ``[slot]`` table) ─────────────


def _slot_name(cfg: Mapping[str, Any]) -> str:
    """A slot's stable key for reference-matching (explicit name, else id)."""
    for key in ("name", "id"):
        v = cfg.get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v)
    slot = cfg.get("slot")
    if isinstance(slot, Mapping):
        for key in ("name", "id"):
            v = slot.get(key)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v)
    return "?"


def _top_or_slot(cfg: Mapping[str, Any], key: str) -> Any:
    """Read ``key`` top-level, else from a nested ``[slot]`` table."""
    if key in cfg:
        return cfg[key]
    slot = cfg.get("slot")
    if isinstance(slot, Mapping) and key in slot:
        return slot[key]
    return None


def _slot_own_image(cfg: Mapping[str, Any]) -> str | None:
    """The slot's own image REF — a top-level or ``[slot]``-nested STRING.

    The ``[image]`` TOML table (image-gen settings, #599) shares the key and
    is deliberately ignored (same trap as
    ``providers.container._resolve_image_ref`` /
    ``updater.retag_stale_slot_images``): only a string value is an image pin.
    """
    if isinstance(cfg.get("image"), str):
        return cfg["image"]
    slot = cfg.get("slot")
    if isinstance(slot, Mapping) and isinstance(slot.get("image"), str):
        return slot["image"]
    return None


def _ngl_set(val: Any) -> int | None:
    """Coerce an NGL value to a *set* int (>=0), else None.

    ``-1`` (== "all layers") is the schema sentinel and is treated as UNSET —
    folding it is launch-inert (a slot with no explicit NGL already offloads
    all layers), matching ``slot_flags_fold``'s ``floor=0`` convention.
    """
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


# ── planner (filesystem-free) ─────────────────────────────────────────────────


def plan_unfold(
    slots: Sequence[Mapping[str, Any]],
    model_hw: Mapping[str, ModelHw],
    profile_images: Mapping[str, str],
    *,
    stale_refs: frozenset[str],
) -> UnfoldPlan:
    """Compute the unfold plan without touching disk or the DB.

    Args:
        slots: raw slot cfg dicts (as decoded from slot TOMLs), each expected
            to carry its own ``name``/``id`` and (optionally) ``[model]``,
            ``profile``, ``image`` and the new grid fields.
        model_hw: model-id → :class:`ModelHw` (raw old NGL/runner columns). A
            model absent here (or with both fields None) contributes nothing.
        profile_images: profile-name → its ``image`` pin string.
        stale_refs: :data:`~hal0.runners.STALE_RUNNER_IMAGE_REFS` — an image in
            this set is former-default debris (dropped, never folded).

    Returns:
        An :class:`UnfoldPlan`.
    """
    plan = UnfoldPlan()

    # 1. Which profiles carry a deliberate pin, and which slots reference each.
    #    Referencing is by slot ``profile`` == profile name.
    refs_by_profile: dict[str, list[str]] = {}
    for cfg in slots:
        prof = cfg.get("profile") or (
            cfg.get("slot", {}).get("profile") if isinstance(cfg.get("slot"), Mapping) else None
        )
        if isinstance(prof, str) and prof:
            refs_by_profile.setdefault(prof, []).append(_slot_name(cfg))

    #: slot_name → the deliberate profile image it should inherit (before the
    #: slot-own-image override, applied per-slot below).
    profile_pin_for_slot: dict[str, str] = {}
    for pname, image in sorted(profile_images.items()):
        if not image:
            continue
        referents = refs_by_profile.get(pname, [])
        if image in stale_refs:
            plan.profile_actions.append(ProfileAction(pname, image, "debris", tuple(referents)))
            continue
        if not referents:
            plan.profile_actions.append(ProfileAction(pname, image, "lost"))
            continue
        plan.profile_actions.append(ProfileAction(pname, image, "folded", tuple(referents)))
        for sname in referents:
            profile_pin_for_slot.setdefault(sname, image)

    # 2. Per-slot folds.
    for cfg in slots:
        sname = _slot_name(cfg)
        model_tbl = cfg.get("model")
        model_tbl = model_tbl if isinstance(model_tbl, Mapping) else {}
        model_id = model_tbl.get("default")
        model_id = str(model_id) if model_id else ""
        mhw = model_hw.get(model_id) if model_id else None

        # ── Fold 1: NGL. Slot's own nested [model].n_gpu_layers wins over the
        #    model default. Only write when the slot top-level NGL is unset.
        nested_ngl = _ngl_set(model_tbl.get("n_gpu_layers"))
        model_ngl = _ngl_set(mhw.n_gpu_layers) if mhw else None
        effective_ngl = nested_ngl if nested_ngl is not None else model_ngl
        cur_top_ngl = _ngl_set(_top_or_slot(cfg, "n_gpu_layers"))
        set_ngl = effective_ngl if (cur_top_ngl is None and effective_ngl is not None) else None
        drop_nested_ngl = "n_gpu_layers" in model_tbl

        # ── Fold 2: BINARY from model.preferred_runner, when slot binary unset.
        cur_binary = _top_or_slot(cfg, "binary")
        cur_binary = cur_binary if isinstance(cur_binary, str) else ""
        pref = mhw.preferred_runner if mhw else None
        set_binary = pref if (not cur_binary and isinstance(pref, str) and pref) else None

        # ── Folds 3+4: image_pin. Precedence: an already-set image_pin wins
        #    (idempotent skip) > slot's own deliberate image > profile pin.
        cur_pin = _top_or_slot(cfg, "image_pin")
        own_image = _slot_own_image(cfg)
        drop_slot_image = own_image is not None
        set_image_pin: str | None = None
        if not (isinstance(cur_pin, str) and cur_pin):
            if own_image is not None and own_image not in stale_refs:
                set_image_pin = own_image
            elif profile_pin_for_slot.get(sname):
                set_image_pin = profile_pin_for_slot[sname]

        fold = SlotFold(
            slot_name=sname,
            set_ngl=set_ngl,
            set_binary=set_binary,
            set_image_pin=set_image_pin,
            drop_nested_ngl=drop_nested_ngl,
            drop_slot_image=drop_slot_image,
        )
        if not fold.is_noop:
            plan.slot_folds.append(fold)

    # 3. Model columns to clear (debris the §5 fold left behind).
    for mid, hw in sorted(model_hw.items()):
        if hw.n_gpu_layers is not None or (hw.preferred_runner or "").strip():
            plan.model_nulls.append(mid)

    return plan


# ── applier (deploy-window gated, dry-run by default) ─────────────────────────


class DeployWindowRequired(RuntimeError):
    """Raised when a real write is attempted without the deploy-window ack."""


def _apply_slot_fold(raw: dict[str, Any], fold: SlotFold) -> bool:
    """Mutate a raw slot-TOML dict in place per ``fold``. Returns changed?."""
    changed = False
    if fold.set_ngl is not None:
        raw["n_gpu_layers"] = fold.set_ngl
        changed = True
    if fold.set_binary is not None:
        raw["binary"] = fold.set_binary
        changed = True
    if fold.set_image_pin is not None:
        raw["image_pin"] = fold.set_image_pin
        changed = True
    if fold.drop_nested_ngl:
        model_tbl = raw.get("model")
        if isinstance(model_tbl, dict) and "n_gpu_layers" in model_tbl:
            del model_tbl["n_gpu_layers"]
            changed = True
    if fold.drop_slot_image:
        # Collapse whichever holder carried the STRING image ref (never the
        # [image] table).
        if isinstance(raw.get("image"), str):
            del raw["image"]
            changed = True
        slot = raw.get("slot")
        if isinstance(slot, dict) and isinstance(slot.get("image"), str):
            del slot["image"]
            changed = True
    return changed


def apply_unfold_plan(
    plan: UnfoldPlan,
    *,
    deploy_window: bool = False,
    dry_run: bool = True,
    job_id: str | None = None,
) -> list[str]:
    """Apply an :class:`UnfoldPlan` to slot TOMLs, profiles.toml, and the DB.

    Args:
        plan: the plan from :func:`plan_unfold`.
        deploy_window: explicit ack that this runs inside the deploy window
            (spec §6). WITHOUT it any non-dry-run write raises
            :class:`DeployWindowRequired`.
        dry_run: when True (default) nothing is written; the returned lines
            describe what WOULD happen.
        job_id: optional breadcrumb for structured-log tracing.

    Returns:
        Human-readable report lines.

    Raises:
        DeployWindowRequired: a real write was requested without ``deploy_window``.
    """
    import tomllib

    from hal0.config.loader import write_toml_atomic
    from hal0.config.paths import profiles_toml, slots_config_dir

    lines: list[str] = []

    if not dry_run and not deploy_window:
        raise DeployWindowRequired(
            "hw_slot_ownership.apply_unfold_plan: refusing to write outside the "
            "deploy window — pass deploy_window=True to acknowledge (spec §6)."
        )

    # ── slot TOMLs ──────────────────────────────────────────────────────────
    slots_dir = slots_config_dir()
    by_name = {f.slot_name: f for f in plan.slot_folds}
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("migrate.hw_slot.slot_unreadable", slot=toml_path.stem, error=str(exc))
            continue
        fold = by_name.get(_slot_name(raw)) or by_name.get(toml_path.stem)
        if fold is None:
            continue
        verb = "would fold" if dry_run else "fold"
        lines.append(
            f"{verb} slot {fold.slot_name!r}: ngl={fold.set_ngl} binary={fold.set_binary!r} "
            f"image_pin={fold.set_image_pin!r} drop_nested_ngl={fold.drop_nested_ngl} "
            f"drop_image={fold.drop_slot_image}"
        )
        if dry_run:
            continue
        if not _apply_slot_fold(raw, fold):
            continue
        try:
            write_toml_atomic(toml_path, raw)
        except Exception as exc:
            log.warning("migrate.hw_slot.slot_write_failed", slot=toml_path.stem, error=str(exc))
            continue
        log.warning(
            "migrate.hw_slot.slot_folded",
            job_id=job_id,
            slot=fold.slot_name,
            ngl=fold.set_ngl,
            binary=fold.set_binary,
            image_pin=fold.set_image_pin,
        )

    # ── profiles.toml — delete every processed image pin ────────────────────
    if plan.profile_actions:
        prof_path = profiles_toml()
        prof_raw: dict[str, Any] | None = None
        if prof_path.exists():
            try:
                prof_raw = tomllib.loads(prof_path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.warning("migrate.hw_slot.profiles_unreadable", error=str(exc))
        prof_changed = False
        for act in plan.profile_actions:
            if act.disposition == "lost":
                log.warning(
                    "migrate.hw_slot.profile_pin_lost",
                    job_id=job_id,
                    profile=act.profile_name,
                    image=act.old_image,
                    note=(
                        "deliberate profile image pin had no referencing slot; the "
                        "pin is dropped and not preserved on any slot (spec §6.3)"
                    ),
                )
            verb = "would drop" if dry_run else "drop"
            lines.append(
                f"{verb} profile {act.profile_name!r} image={act.old_image!r} "
                f"[{act.disposition}] slots={list(act.slot_names)}"
            )
            if dry_run or prof_raw is None:
                continue
            entry = (prof_raw.get("profile") or {}).get(act.profile_name)
            if isinstance(entry, dict) and "image" in entry:
                del entry["image"]
                prof_changed = True
        if prof_changed and prof_raw is not None:
            try:
                write_toml_atomic(prof_path, prof_raw)
            except Exception as exc:
                log.warning("migrate.hw_slot.profiles_write_failed", error=str(exc))

    # ── DB — clear the folded-out model columns (debris) ────────────────────
    if plan.model_nulls:
        verb = "would null" if dry_run else "null"
        lines.append(f"{verb} model columns (ngl/preferred_runner) for {plan.model_nulls}")
        if not dry_run:
            _null_model_hw_columns(plan.model_nulls, job_id=job_id)

    return lines


def _null_model_hw_columns(model_ids: Sequence[str], *, job_id: str | None = None) -> None:
    """NULL the ``n_gpu_layers``/``preferred_runner`` columns for ``model_ids``.

    Best-effort: a box without a registry DB (or a locked/broken one) logs and
    skips — a migration must never wedge on the debris-cleanup tail. The columns
    are existence-guarded so this is a clean no-op once a later schema drop has
    removed them.
    """
    try:
        from hal0.db.connection import connect, tx
        from hal0.registry.sqlite_store import SqliteModelRegistry

        db_path = SqliteModelRegistry().db_path
        if not db_path.exists():
            return
        with connect(db_path) as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(model)")}
            sets = [c for c in ("n_gpu_layers", "preferred_runner") if c in cols]
            if not sets:
                return  # columns already dropped — nothing to clear
            assignment = ", ".join(f"{c} = NULL" for c in sets)
            with tx(conn):
                conn.executemany(
                    f"UPDATE model SET {assignment} WHERE id = ?",
                    [(mid,) for mid in model_ids],
                )
    except Exception as exc:
        log.warning("migrate.hw_slot.model_null_failed", job_id=job_id, error=str(exc))


# ── live IO entrypoint (deploy-window use) ────────────────────────────────────


def collect_inputs() -> tuple[list[dict[str, Any]], dict[str, ModelHw], dict[str, str]]:
    """Load the planner inputs from the live system.

    Returns ``(slot_raws, model_hw, profile_images)``:

    * ``slot_raws`` — each slot TOML decoded RAW (``tomllib``, NOT through
      ``SlotConfig``) so the sunset ``[model].n_gpu_layers`` and the raw
      ``image`` key survive — with its file ``name`` (stem) injected.
    * ``model_hw`` — model-id → :class:`ModelHw`, read RAW from the ``model``
      table's kept ``n_gpu_layers``/``preferred_runner`` columns (which the
      current schema no longer maps onto ``Model``). Columns are existence-
      guarded: once a later schema drop removes them this returns ``{}``.
    * ``profile_images`` — profile-name → its ``image`` pin (raw ``profiles.toml``).

    Kept separate from :func:`plan_unfold` so the planner stays filesystem-free
    and unit-testable; only this and :func:`run_migration` touch disk/DB. The DB
    is read via a RAW connection (no ``migrate()``), so the read can never race
    an accompanying column-drop migration.
    """
    import tomllib

    from hal0.config.paths import profiles_toml, slots_config_dir

    slot_raws: list[dict[str, Any]] = []
    slots_dir = slots_config_dir()
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw.setdefault("name", toml_path.stem)
        slot_raws.append(raw)

    model_hw: dict[str, ModelHw] = {}
    try:
        from hal0.config.paths import db_path
        from hal0.db.connection import connect

        dbp = db_path()
        if dbp.exists():
            with connect(dbp) as conn:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(model)")}
                has_runner = "preferred_runner" in cols
                has_ngl = "n_gpu_layers" in cols
                if has_runner or has_ngl:
                    sel_runner = "preferred_runner" if has_runner else "NULL"
                    sel_ngl = "n_gpu_layers" if has_ngl else "NULL"
                    for row in conn.execute(
                        f"SELECT id, {sel_runner} AS pr, {sel_ngl} AS ngl FROM model"
                    ):
                        pr = row["pr"] if isinstance(row["pr"], str) and row["pr"].strip() else None
                        ngl = row["ngl"] if isinstance(row["ngl"], int) else None
                        if pr is not None or ngl is not None:
                            model_hw[row["id"]] = ModelHw(n_gpu_layers=ngl, preferred_runner=pr)
    except Exception as exc:
        log.warning("migrate.hw_slot.model_read_failed", error=str(exc))

    profile_images: dict[str, str] = {}
    prof_path = profiles_toml()
    if prof_path.exists():
        try:
            praw = tomllib.loads(prof_path.read_text(encoding="utf-8"))
        except Exception:
            praw = {}
        for name, entry in (praw.get("profile") or {}).items():
            if isinstance(entry, dict) and isinstance(entry.get("image"), str):
                profile_images[name] = entry["image"]

    return slot_raws, model_hw, profile_images


def run_migration(*, deploy_window: bool = False, dry_run: bool = True) -> list[str]:
    """One-shot: plan the unfold from live state and (optionally) apply it.

    DEPLOY-WINDOW GATED and dry-run by default (spec §6). A real write needs
    BOTH ``deploy_window=True`` and ``dry_run=False``. Snapshot the config dir
    + registry DB first — standard migration-window rule.

    Returns the report lines.
    """
    from hal0.runners import STALE_RUNNER_IMAGE_REFS

    slots, model_hw, profile_images = collect_inputs()
    plan = plan_unfold(slots, model_hw, profile_images, stale_refs=STALE_RUNNER_IMAGE_REFS)
    return apply_unfold_plan(plan, deploy_window=deploy_window, dry_run=dry_run)


__all__ = [
    "DeployWindowRequired",
    "ModelHw",
    "ProfileAction",
    "SlotFold",
    "UnfoldPlan",
    "apply_unfold_plan",
    "collect_inputs",
    "plan_unfold",
    "run_migration",
]
