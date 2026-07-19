"""One-shot migrator — fold each slot's flag/tune surface into its model.

FLAGS-own (spec-flags-ownership §5). Launch flags now attach ONLY to models
(see :func:`hal0.providers.container._llama_argv_segments`, which no longer
reads a profile or slot flag segment). Live boxes still have slot TOMLs
carrying ``[server].extra_args`` / ``[model].n_gpu_layers`` / ``[model].
context_size`` / ``parallel`` overrides plus a ``profile`` reference whose
bench-tuned ``flags`` used to layer in at launch. This migrator materializes
that effective tune into the bound model's ``defaults`` so the model owns its
full launch tune as plain text — the copy-on-stamp end state.

DEPLOY-WINDOW GATED. Per spec §5 the execution rides the P2-config/deploy
window, AFTER SLOT increment B. It is NOT wired into the automatic
``hal0.config.migrations`` schema-version runner and it does NOT run on boot.
:func:`apply_fold_plan` refuses to write unless the caller passes
``deploy_window=True`` (an explicit "I am in the migration window" ack).

Design:

* **Pure planner** (:func:`plan_slot_flags_fold`) — filesystem-free: takes slot
  cfg dicts + the profile flag map + the current model ``defaults`` map, returns
  a :class:`FoldPlan` of per-model folds, divergent-share refusals, and no-op
  skips. Golden-testable without touching disk or the registry.
* **Divergent-share refusal** (spec §5.2) — when two or more slots reference one
  model with DIVERGENT folded tunes, the migrator REFUSES that model and reports
  it (operator resolves: pick one, or split model rows). It never silently picks
  a winner. A sole-referencing slot (or several slots that fold to the IDENTICAL
  tune) auto-folds.
* **Idempotent** — a model whose ``defaults`` already equal the folded target is
  a no-op skip, so the migrator is safely re-runnable.
* **Dry-run** — :func:`apply_fold_plan` defaults to ``dry_run=True`` (report the
  plan, write nothing). Callers opt in to writes explicitly.

Managed-flag split: the effective tune's ``-ngl``/``--n-gpu-layers`` folds to
the TYPED ``defaults.n_gpu_layers`` (a trusted, managed field), NOT into
``defaults.extra_args`` — because ``extra_args`` rides the screened
``model_extra_args`` launch segment where ``-ngl`` is a §21.7 managed-arg
denylist violation. The remainder (``-fa``, ``-b/-ub``, ``--threads``, KV-quant,
``--parallel``/``--kv-unified``, …) becomes ``defaults.extra_args``.

``-c``/``--ctx-size`` is deliberately DROPPED, not folded. At launch a slot's
own ``[model].context_size`` always wins over ``model.defaults.context_size``
(:func:`hal0.providers.container._resolve_context_size` returns the explicit
slot value whenever it is set, before the model default is ever consulted;
``ContainerProvider.container_spec`` also passes the resolved ``context_size``
into the ``base`` argv segment directly — ``model.defaults.context_size``
never even reaches ``_llama_argv_segments``). So a slot's ctx is
launch-shadowed by design: folding it into ``defaults`` would write a value
with no launch effect, and comparing on it would refuse migrations for slots
that only disagree on their own context window (e.g. a qtest slot at ctx4096
vs a smoke slot at ctx8192 sharing one model). ``--ctx-size``/``-c`` tokens
are still stripped out of the merged stream before it becomes ``extra_args``
(they're still on the §21.7 denylist), the extracted value is just discarded
instead of returned.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hal0.slots.argv import FLAG_ALIASES, normalize_argv

# Canonical (long) spellings the fold routes into TYPED model.defaults fields
# instead of the freeform extra_args string.
_NGL_CANON = "--n-gpu-layers"
_CTX_CANON = "--ctx-size"


def _canon(flag: str) -> str:
    return FLAG_ALIASES.get(flag, flag)


@dataclass(frozen=True)
class FoldedTune:
    """The materialized tune a slot contributes to its model's ``defaults``.

    Value-equality is what the divergent-share check and the idempotence check
    compare on, so this is a frozen dataclass with the fold outputs that
    actually affect launch. ``context_size`` is deliberately NOT one of them:
    a slot's own ``[model].context_size`` always wins over
    ``model.defaults.context_size`` at launch (see module docstring /
    :func:`hal0.providers.container._resolve_context_size`), so folding it in
    would be launch-inert and comparing on it would spuriously refuse slots
    that only diverge on their own context window.
    """

    extra_args: str | None
    n_gpu_layers: int | None

    def as_defaults_updates(self) -> dict[str, Any]:
        """The ``defaults`` sub-keys this fold sets (omitting ``None``)."""
        out: dict[str, Any] = {}
        if self.extra_args is not None:
            out["extra_args"] = self.extra_args
        if self.n_gpu_layers is not None:
            out["n_gpu_layers"] = self.n_gpu_layers
        return out


@dataclass(frozen=True)
class SlotRef:
    """A slot's identity + the flag surface it contributes to the fold."""

    slot_name: str
    model_id: str
    profile: str | None
    folded: FoldedTune


@dataclass(frozen=True)
class ModelFold:
    """A model that will receive one slot's (or a consensus) folded tune."""

    model_id: str
    source_profile: str | None
    folded: FoldedTune
    #: The complete new ``defaults`` dict to persist (existing merged with fold).
    new_defaults: dict[str, Any]
    slot_names: tuple[str, ...]


@dataclass(frozen=True)
class DivergentRefusal:
    """Two+ slots point at one model with divergent folds — refused, reported."""

    model_id: str
    #: slot_name -> the FoldedTune it wanted, so the operator sees the conflict.
    slot_tunes: dict[str, FoldedTune]


@dataclass
class FoldPlan:
    """Result of :func:`plan_slot_flags_fold`."""

    folds: list[ModelFold] = field(default_factory=list)
    refusals: list[DivergentRefusal] = field(default_factory=list)
    #: (model_id, reason) for slots that contribute nothing / already-folded.
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing was refused — safe to apply the whole plan."""
        return not self.refusals


# ── effective-tune computation ────────────────────────────────────────────────


def _int_or_none(val: Any, *, floor: int) -> int | None:
    """Coerce ``val`` to int, returning None if unset/below ``floor``/invalid."""
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    return n if n >= floor else None


def _slot_flag_tokens(slot_cfg: Mapping[str, Any]) -> tuple[list[str], int | None, int | None]:
    """Extract a slot's flag-surface contribution as (extra_tokens, ngl, ctx).

    Reads the same fields the pre-FLAGS-own launch path did: ``[server].
    extra_args`` (freeform), ``[model].n_gpu_layers`` (schema default ``-1`` =
    unset sentinel), ``[model].context_size``, and ``parallel`` (which becomes
    ``--parallel N`` + ``--kv-unified`` when N>1, matching the retired
    slot_overrides segment). ``extra_tokens`` carries the freeform args plus the
    parallel expansion; ``ngl``/``ctx`` are the typed slot overrides.
    """
    server = slot_cfg.get("server")
    server = server if isinstance(server, Mapping) else {}
    extra_args = server.get("extra_args")
    extra_tokens = shlex.split(str(extra_args)) if extra_args and str(extra_args).strip() else []

    model_tbl = slot_cfg.get("model")
    model_tbl = model_tbl if isinstance(model_tbl, Mapping) else {}
    ngl = _int_or_none(model_tbl.get("n_gpu_layers"), floor=0)  # -1 = unset sentinel
    ctx = _int_or_none(model_tbl.get("context_size"), floor=1)

    parallel = _int_or_none(slot_cfg.get("parallel"), floor=1)
    if parallel is not None:
        extra_tokens += ["--parallel", str(parallel)]
        if parallel > 1:
            extra_tokens += ["--kv-unified"]

    return extra_tokens, ngl, ctx


def compute_folded_tune(
    slot_cfg: Mapping[str, Any],
    profile_flags: str,
    model_defaults: Mapping[str, Any] | None,
) -> FoldedTune:
    """Materialize the effective tune a slot+profile fold into the model.

    Reconstructs the OLD launch precedence (verbatim, spec §5.1) as ONE
    last-wins token stream, lowest→highest — mirroring the retired
    ``_llama_argv_segments`` order::

        profile.flags  <  model.defaults.extra_args  <  model typed -ngl
                       <  slot typed -ngl  <  slot extra_args (+ parallel)
                       <  slot typed -ctx

    then splits ``-ngl`` OUT of the text into the typed ``n_gpu_layers`` output
    (it cannot ride ``extra_args`` — the launch denylist rejects ``-ngl`` on the
    screened ``model_extra_args`` segment) and leaves the rest as
    ``extra_args``. ``-c``/``--ctx-size`` is also stripped out of the text
    (same denylist) but its value is discarded rather than folded — a slot's
    own context_size always wins at launch, so the model-level value is
    launch-irrelevant (see module docstring). The model's own existing
    ``defaults`` participate (they are already the model's tune) so re-running
    is a no-op.
    """
    md = model_defaults if isinstance(model_defaults, Mapping) else {}

    prof_tokens = shlex.split(profile_flags) if profile_flags and profile_flags.strip() else []
    md_extra = md.get("extra_args")
    md_extra_tokens = shlex.split(str(md_extra)) if md_extra and str(md_extra).strip() else []
    md_ngl = _int_or_none(md.get("n_gpu_layers"), floor=0)
    md_ctx = _int_or_none(md.get("context_size"), floor=1)
    slot_extra_tokens, slot_ngl, slot_ctx = _slot_flag_tokens(slot_cfg)

    # Build the full precedence-ordered stream so normalize_argv's last-wins
    # picks the right ngl/ctx (e.g. a slot -ngl 30 beats a profile -ngl 999).
    stream: list[str] = list(prof_tokens)
    stream += md_extra_tokens
    if md_ngl is not None:
        stream += ["-ngl", str(md_ngl)]
    if md_ctx is not None:
        stream += ["--ctx-size", str(md_ctx)]
    if slot_ngl is not None:
        stream += ["-ngl", str(slot_ngl)]
    stream += slot_extra_tokens
    if slot_ctx is not None:
        stream += ["--ctx-size", str(slot_ctx)]

    merged = normalize_argv(stream).argv

    ngl: int | None = None
    remainder: list[str] = []
    i = 0
    while i < len(merged):
        tok = merged[i]
        canon = _canon(tok)
        if canon in (_NGL_CANON, _CTX_CANON) and i + 1 < len(merged):
            num = _int_or_none(merged[i + 1], floor=-1 if canon == _NGL_CANON else 1)
            if num is None:
                # Non-numeric value — leave it in the text rather than guess.
                remainder.extend([tok, merged[i + 1]])
            elif canon == _NGL_CANON:
                ngl = num
            # canon == _CTX_CANON: still stripped out of the text (§21.7
            # denylist), but the value is discarded — a slot's own ctx always
            # wins at launch, so it never becomes part of the fold (see
            # module docstring / FoldedTune docstring).
            i += 2
            continue
        remainder.append(tok)
        i += 1

    extra_args = " ".join(shlex.quote(t) for t in remainder) if remainder else None
    return FoldedTune(extra_args=extra_args, n_gpu_layers=ngl)


def _merge_new_defaults(existing: Mapping[str, Any] | None, folded: FoldedTune) -> dict[str, Any]:
    """Produce the complete new ``defaults`` dict (existing ⊕ fold outputs)."""
    out: dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
    out.update(folded.as_defaults_updates())
    return out


def _is_noop(existing: Mapping[str, Any] | None, new_defaults: Mapping[str, Any]) -> bool:
    """True when applying ``new_defaults`` changes nothing (idempotence)."""
    cur = dict(existing) if isinstance(existing, Mapping) else {}
    return all(cur.get(k) == v for k, v in new_defaults.items())


# ── planner ───────────────────────────────────────────────────────────────────


def plan_slot_flags_fold(
    slots: Sequence[Mapping[str, Any]],
    profile_flags: Mapping[str, str],
    model_defaults: Mapping[str, Mapping[str, Any] | None],
) -> FoldPlan:
    """Compute the fold plan without touching disk or the registry.

    Args:
        slots: raw slot cfg dicts (as loaded from slot TOMLs). A slot with no
            bound model (no ``[model].default``) is ignored — nothing to fold.
        profile_flags: profile-name -> that profile's ``flags`` string. A slot
            whose profile is absent here folds with an empty profile tune (the
            profile contributed no flags / is gone).
        model_defaults: model-id -> the model's current ``defaults`` dict (or
            ``None``). Missing ids fold onto an empty defaults.

    Returns:
        A :class:`FoldPlan`. When two+ slots fold DIVERGENT tunes onto one
        model, that model is a :class:`DivergentRefusal` (not a fold).
    """
    # 1. Compute every slot's fold, grouped by target model.
    by_model: dict[str, list[SlotRef]] = {}
    for slot_cfg in slots:
        model_tbl = slot_cfg.get("model")
        model_tbl = model_tbl if isinstance(model_tbl, Mapping) else {}
        model_id = model_tbl.get("default")
        if not model_id:
            continue
        model_id = str(model_id)
        slot_name = str(slot_cfg.get("name") or slot_cfg.get("id") or "?")
        profile = slot_cfg.get("profile")
        profile = str(profile) if profile else None
        pflags = profile_flags.get(profile, "") if profile else ""
        folded = compute_folded_tune(slot_cfg, pflags, model_defaults.get(model_id))
        by_model.setdefault(model_id, []).append(
            SlotRef(slot_name=slot_name, model_id=model_id, profile=profile, folded=folded)
        )

    plan = FoldPlan()

    # 2. Resolve each model: sole/consensus → fold; divergent → refuse.
    for model_id, refs in sorted(by_model.items()):
        distinct = {r.folded for r in refs}
        existing = model_defaults.get(model_id)
        if len(distinct) > 1:
            plan.refusals.append(
                DivergentRefusal(
                    model_id=model_id,
                    slot_tunes={r.slot_name: r.folded for r in refs},
                )
            )
            continue

        folded = next(iter(distinct))
        new_defaults = _merge_new_defaults(existing, folded)
        if _is_noop(existing, new_defaults):
            plan.skipped.append((model_id, "already folded (no-op)"))
            continue
        plan.folds.append(
            ModelFold(
                model_id=model_id,
                source_profile=next((r.profile for r in refs if r.profile), None),
                folded=folded,
                new_defaults=new_defaults,
                slot_names=tuple(r.slot_name for r in refs),
            )
        )

    return plan


# ── applier (deploy-window gated, dry-run by default) ─────────────────────────


class DeployWindowRequired(RuntimeError):
    """Raised when a write is attempted without the deploy-window ack."""


def apply_fold_plan(
    plan: FoldPlan,
    registry: Any,
    *,
    deploy_window: bool = False,
    dry_run: bool = True,
) -> list[str]:
    """Apply a :class:`FoldPlan` to the model registry.

    Args:
        plan: the plan from :func:`plan_slot_flags_fold`.
        registry: a ``hal0.registry.store.ModelRegistry`` (needs ``.update``).
        deploy_window: explicit ack that this runs inside the P2-config/deploy
            window (spec §5). WITHOUT it, any non-dry-run write raises
            :class:`DeployWindowRequired` — the migrator does not run on boot.
        dry_run: when True (default) nothing is written; the returned lines
            describe what WOULD happen.

    Returns:
        Human-readable report lines (folds applied/planned, refusals, skips).

    Raises:
        DeployWindowRequired: a real write was requested without ``deploy_window``.
        RuntimeError: the plan has divergent-share refusals — refuse to apply a
            partial fold; the operator must resolve the conflicts first.
    """
    lines: list[str] = []

    if plan.refusals:
        for r in plan.refusals:
            tunes = "; ".join(f"{s}={t!r}" for s, t in sorted(r.slot_tunes.items()))
            lines.append(f"REFUSE model {r.model_id!r}: divergent slot tunes → {tunes}")
        # Fold-what-you-can is unsafe: refuse the whole run so the operator sees
        # every conflict and resolves it before any model is rewritten.
        raise RuntimeError(
            f"slot_flags_fold refuses {len(plan.refusals)} model(s) with divergent "
            "slot overrides; resolve (pick one tune or split model rows) and re-run. "
            + " | ".join(lines)
        )

    for m in plan.skipped:
        lines.append(f"skip model {m[0]!r}: {m[1]}")

    for fold in plan.folds:
        verb = "would fold" if dry_run else "fold"
        lines.append(
            f"{verb} {fold.model_id!r} <- profile={fold.source_profile!r} "
            f"slots={list(fold.slot_names)} defaults={fold.new_defaults}"
        )
        if dry_run:
            continue
        if not deploy_window:
            raise DeployWindowRequired(
                "slot_flags_fold.apply_fold_plan: refusing to write outside the "
                "deploy window — pass deploy_window=True to acknowledge (spec §5)."
            )
        registry.update(fold.model_id, {"defaults": fold.new_defaults})

    return lines


# ── live IO entrypoint (deploy-window use) ────────────────────────────────────


def collect_inputs() -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any], Any]:
    """Load the planner inputs from the live system (slots, profiles, registry).

    Returns ``(slot_dicts, profile_flags, model_defaults, registry)``. Kept
    separate from :func:`plan_slot_flags_fold` so the planner stays
    filesystem-free and unit-testable; only :func:`run_migration` needs disk.
    """
    from hal0.config.loader import list_slots, load_profiles_config, load_slot_config
    from hal0.registry.store import ModelRegistry

    slots: list[dict[str, Any]] = []
    for name in list_slots():
        try:
            cfg = load_slot_config(name)
        except Exception:
            continue
        slots.append(cfg.model_dump(by_alias=True))

    profiles = load_profiles_config()
    profile_flags = {name: p.flags for name, p in profiles.profile.items()}

    registry = ModelRegistry()
    model_defaults: dict[str, Any] = {}
    for m in registry.list():
        d = m.defaults
        model_defaults[m.id] = d.model_dump() if d is not None else None

    return slots, profile_flags, model_defaults, registry


def run_migration(*, deploy_window: bool = False, dry_run: bool = True) -> list[str]:
    """One-shot: plan the fold from live state and (optionally) apply it.

    DEPLOY-WINDOW GATED and dry-run by default (spec §5). A real write needs
    BOTH ``deploy_window=True`` and ``dry_run=False``. Snapshot the config/
    registry first — standard migration-window rule — before a non-dry-run pass.

    Returns the report lines. Raises on divergent-share refusal (see
    :func:`apply_fold_plan`).
    """
    slots, profile_flags, model_defaults, registry = collect_inputs()
    plan = plan_slot_flags_fold(slots, profile_flags, model_defaults)
    return apply_fold_plan(plan, registry, deploy_window=deploy_window, dry_run=dry_run)


__all__ = [
    "DeployWindowRequired",
    "DivergentRefusal",
    "FoldPlan",
    "FoldedTune",
    "ModelFold",
    "SlotRef",
    "apply_fold_plan",
    "collect_inputs",
    "compute_folded_tune",
    "plan_slot_flags_fold",
    "run_migration",
]
