"""The speculation decision for one llama-server launch (ADR-0031 §3).

One function, :func:`effective_speculation`, decides which draft a launch uses
and returns a :class:`SpecPlan` that every layer of the argv reads:

* model layer   ``--spec-type <spec_type>`` and ``--spec-draft-model <draft_model>``
* profile layer the per-kind ``bundle`` (before the profile's own flags)
* slot layer    ``--spec-draft-device`` for embedded MTP heads only; an external
                drafter follows the model's devices (no pin)

The order is the ADR's::

    speculation == "off" or embedding/reranking mode  -> none
    speculation == "ngram"                           -> ngram
    a paired drafter the runner image can run        -> draft
    MTP heads and a runner that drafts with them     -> mtp
    else                                             -> none

A drafter the runner image cannot run sets ``unavailable_reason`` whether the
plan falls back to MTP heads or to none.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from hal0.config.schema import DRAFT_BUNDLE, NGRAM_BUNDLE
from hal0.model_meta import model_is_mtp_eligible
from hal0.runners import DRAFT_KIND_MIN_BUILD, llama_build_from_tag, runner_draft_kinds

log = logging.getLogger(__name__)

SpecMode = Literal["draft", "mtp", "ngram", "none"]

#: ``--spec-draft-n-max`` for a DFlash drafter whose block size is unknown.
_DFLASH_DEFAULT_N_MAX = 6
#: llama.cpp clamps the DFlash draft length here anyway.
_DFLASH_N_MAX_CAP = 8


@dataclass(frozen=True)
class SpecPlan:
    """What speculation a launch uses, and the argv pieces that spell it."""

    mode: SpecMode
    spec_type: str | None = None
    draft_model: str | None = None
    bundle: str = ""
    kind: str | None = None
    variant: str | None = None
    unavailable_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_ngram_bundle() -> tuple[str, str]:
    """``NGRAM_BUNDLE`` as ``(spec_type, the rest)``: the type goes to the model
    layer (one owner of ``--spec-type``), the tune to the profile layer."""
    tokens = shlex.split(NGRAM_BUNDLE)
    i = tokens.index("--spec-type")
    spec_type = tokens[i + 1]
    rest = tokens[:i] + tokens[i + 2 :]
    return spec_type, shlex.join(rest)


_NGRAM_SPEC_TYPE, _NGRAM_TUNE = _split_ngram_bundle()


def _draft_bundle(kind: str, meta: Mapping[str, Any]) -> str:
    bundle = DRAFT_BUNDLE[kind]
    if kind != "dflash":
        return bundle
    try:
        block_size = int(meta.get("block_size") or 0)
    except (TypeError, ValueError):
        block_size = 0
    if block_size < 1:  # unknown (or nonsense): the default draft length
        n_max = _DFLASH_DEFAULT_N_MAX
    else:
        # block_size - 1 drafted tokens, capped where llama.cpp clamps; at least
        # one, so a block of 1 still drafts a token rather than none.
        n_max = max(1, min(block_size - 1, _DFLASH_N_MAX_CAP))
    return bundle.format(n_max=n_max)


def _unavailable_reason(runner: Any, needed: str, image_tag: str | None) -> str:
    """Why ``runner`` on ``image_tag`` cannot run a ``needed`` drafter."""
    static: frozenset[str] = getattr(getattr(runner, "supports", None), "draft_kinds", frozenset())
    floor = DRAFT_KIND_MIN_BUILD.get(needed)
    build = llama_build_from_tag(image_tag)
    if needed in static and floor is not None and build is not None:
        return f"drafter needs llama.cpp b{floor} or newer; this runner image is b{build}"
    return f"runner {getattr(runner, 'key', '?')} does not support {needed} drafters"


def _model_name(model_info: Mapping[str, Any]) -> str:
    return str(model_info.get("_model_key") or model_info.get("path") or "")


def effective_speculation(
    model_info: Mapping[str, Any],
    runner: Any,
    profile: Any,
    *,
    embed_mode: bool,
    image_tag: str | None,
    log_launch: bool = False,
) -> SpecPlan:
    """The :class:`SpecPlan` for ``model_info`` on ``runner`` (image ``image_tag``)
    under ``profile``'s ``speculation``.

    ``log_launch`` marks the real launch: only then are the
    ``speculation.draft_unsupported`` and ``mtp.auto_off_model_ineligible``
    breadcrumbs logged, so preview and status renders stay quiet.
    """
    speculation = str(getattr(profile, "speculation", "auto") or "auto")
    if speculation == "off" or embed_mode:
        return SpecPlan(mode="none")
    if speculation == "ngram":
        return SpecPlan(mode="ngram", spec_type=_NGRAM_SPEC_TYPE, bundle=_NGRAM_TUNE)

    unavailable: str | None = None
    draft = model_info.get("draft")
    kind = model_info.get("draft_kind")
    if draft:
        meta = (model_info.get("metadata") or {}).get("draft")
        if not isinstance(meta, Mapping):
            meta = {}
        variant = meta.get("variant")
        if kind not in DRAFT_BUNDLE:
            # A row written without a classified kind: nothing says which
            # --spec-type the file needs, so it is never guessed.
            unavailable = "drafter kind unknown; re-pair the drafter"
        else:
            kinds = runner_draft_kinds(runner, image_tag)
            needed = "lilicorr" if kind in kinds and variant == "lilicorr" else str(kind)
            if needed in kinds:
                return SpecPlan(
                    mode="draft",
                    spec_type=f"draft-{kind}",
                    draft_model=str(draft),
                    bundle=_draft_bundle(str(kind), meta),
                    kind=str(kind),
                    variant=str(variant) if variant else None,
                )
            unavailable = _unavailable_reason(runner, needed, image_tag)
        if log_launch:
            log.info(
                "speculation.draft_unsupported",
                extra={
                    "model": _model_name(model_info),
                    "kind": str(kind) if kind else None,
                    "variant": str(variant) if variant else None,
                    "reason": unavailable,
                },
            )

    if not model_is_mtp_eligible(model_info):
        if log_launch and unavailable is None:
            log.info(
                "mtp.auto_off_model_ineligible",
                extra={
                    "model": _model_name(model_info),
                    "hint": "tag the model 'mtp' (or set its MTP heads fact) to draft",
                },
            )
        return SpecPlan(mode="none", unavailable_reason=unavailable)
    if not bool(getattr(getattr(runner, "supports", None), "mtp", False)):
        return SpecPlan(mode="none", unavailable_reason=unavailable)
    return SpecPlan(
        mode="mtp",
        spec_type="draft-mtp",
        bundle=DRAFT_BUNDLE["mtp"],
        kind="mtp",
        unavailable_reason=unavailable,
    )


__all__ = ["SpecMode", "SpecPlan", "effective_speculation"]
