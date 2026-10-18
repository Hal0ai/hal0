"""The speculation decision (ADR-0031 §3): which draft, if any, a launch uses.

``effective_speculation`` is the one decision behind the model layer's
``--spec-type``/``--spec-draft-model``, the profile layer's per-kind bundle and
the slot layer's ``--spec-draft-device`` (embedded MTP heads only). A paired drafter wins over embedded
MTP heads; ``off`` and embedding mode never draft; ``ngram`` ignores the
drafter; a drafter the runner image cannot run falls back (and says why).
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

import pytest

from hal0.config.schema import DRAFT_BUNDLE, NGRAM_BUNDLE, ProfileConfig
from hal0.providers.speculation import SpecPlan, effective_speculation
from hal0.runners import RUNNER_IMAGES

_STANDARD = RUNNER_IMAGES["standard"]
_CUDA = RUNNER_IMAGES["cuda"]
_NEW_TAG = "ghcr.io/hal0ai/hal0-runner-standard:b11716-r52bc433"
_OLD_TAG = "ghcr.io/hal0ai/hal0-runner-standard:b11596-r6d1fb9e"


def _profile(speculation: str = "auto") -> ProfileConfig:
    return ProfileConfig(flags="-fa on", speculation=speculation)


def _model(
    *,
    draft: str | None = "/models/q/draft.gguf",
    kind: str | None = "dflash",
    heads: bool = False,
    **meta: Any,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "_model_key": "qwen",
        "path": "/models/q/qwen.gguf",
        "tags": ["mtp"] if heads else [],
        "draft": draft,
        "draft_kind": kind if draft else None,
        "metadata": {"draft": meta} if meta else {},
    }
    return info


def _plan(model: dict[str, Any], **kw: Any) -> SpecPlan:
    kw.setdefault("embed_mode", False)
    kw.setdefault("image_tag", _NEW_TAG)
    runner = kw.pop("runner", _STANDARD)
    profile = kw.pop("profile", _profile())
    return effective_speculation(model, runner, profile, **kw)


def test_draft_wins_over_embedded_mtp() -> None:
    plan = _plan(_model(heads=True, block_size=8))
    assert plan.mode == "draft"
    assert plan.spec_type == "draft-dflash"
    assert plan.draft_model == "/models/q/draft.gguf"
    assert plan.kind == "dflash"
    assert plan.bundle == DRAFT_BUNDLE["dflash"].format(n_max=7)
    assert plan.unavailable_reason is None


@pytest.mark.parametrize(
    ("block_size", "n_max"), [(16, 8), (None, 6), (4, 3), (2, 1), (1, 1), (0, 6)]
)
def test_dflash_n_max_follows_block_size(block_size: int | None, n_max: int) -> None:
    meta = {"block_size": block_size} if block_size is not None else {}
    plan = _plan(_model(**meta))
    tokens = shlex.split(plan.bundle)
    assert tokens[tokens.index("--spec-draft-n-max") + 1] == str(n_max)


@pytest.mark.parametrize("kind", ["eagle3", "simple"])
def test_other_kinds_use_their_bundle(kind: str) -> None:
    plan = _plan(_model(kind=kind))
    assert plan.spec_type == f"draft-{kind}"
    assert plan.bundle == DRAFT_BUNDLE[kind]


def test_mtp_heads_when_no_draft() -> None:
    plan = _plan(_model(draft=None, heads=True))
    assert plan.mode == "mtp"
    assert plan.spec_type == "draft-mtp"
    assert plan.draft_model is None
    assert plan.bundle == DRAFT_BUNDLE["mtp"]
    assert plan.unavailable_reason is None


def test_off_disables_everything() -> None:
    plan = _plan(_model(heads=True), profile=_profile("off"))
    assert plan.mode == "none"
    assert plan.spec_type is None and plan.draft_model is None and plan.bundle == ""


def test_ngram_ignores_draft() -> None:
    plan = _plan(_model(heads=True), profile=_profile("ngram"))
    assert plan.mode == "ngram"
    assert plan.spec_type == "ngram-mod"
    assert plan.draft_model is None
    # The bundle is the n-gram tune; its --spec-type is the plan's spec_type
    # (the model layer emits it, so the screened profile layer never does).
    assert "--spec-type" not in shlex.split(plan.bundle)
    assert ["--spec-type", "ngram-mod", *shlex.split(plan.bundle)] == shlex.split(NGRAM_BUNDLE)


def test_embed_mode_never_drafts() -> None:
    plan = _plan(_model(heads=True), embed_mode=True)
    assert plan.mode == "none"
    assert plan.spec_type is None and plan.draft_model is None


def test_lilicorr_on_b11596_image_is_unavailable() -> None:
    plan = _plan(_model(variant="lilicorr", block_size=8), image_tag=_OLD_TAG)
    assert plan.mode == "none"  # the fixture has no MTP heads to fall back to
    assert plan.draft_model is None
    assert plan.unavailable_reason is not None
    assert "b11669" in plan.unavailable_reason
    assert plan.unavailable_reason == (
        "drafter needs llama.cpp b11669 or newer; this runner image is b11596"
    )


def test_lilicorr_on_a_new_image_drafts() -> None:
    plan = _plan(_model(variant="lilicorr"), image_tag=_NEW_TAG)
    assert plan.mode == "draft"
    assert plan.variant == "lilicorr"


def test_unavailable_drafter_falls_back_to_mtp_heads_and_keeps_the_reason() -> None:
    plan = _plan(_model(variant="lilicorr", heads=True), image_tag=_OLD_TAG)
    assert plan.mode == "mtp"
    assert plan.spec_type == "draft-mtp"
    assert plan.draft_model is None
    assert plan.unavailable_reason is not None


def test_cuda_runner_has_no_draft() -> None:
    plan = _plan(_model(heads=True), runner=_CUDA)
    assert plan.mode == "none"
    assert plan.draft_model is None
    assert plan.unavailable_reason == "runner cuda does not support dflash drafters"


def test_a_drafter_of_unknown_kind_is_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="hal0.providers.speculation"):
        plan = _plan(_model(kind=None), log_launch=True)
    assert plan.mode == "none"
    assert plan.draft_model is None
    assert plan.unavailable_reason == "drafter kind unknown; re-pair the drafter"
    (rec,) = [r for r in caplog.records if r.getMessage() == "speculation.draft_unsupported"]
    assert rec.kind is None  # type: ignore[attr-defined]


def test_unsupported_drafter_logs_once_when_asked(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="hal0.providers.speculation"):
        _plan(_model(variant="lilicorr"), image_tag=_OLD_TAG, log_launch=True)
        _plan(_model(variant="lilicorr"), image_tag=_OLD_TAG)
    records = [r for r in caplog.records if r.getMessage() == "speculation.draft_unsupported"]
    assert len(records) == 1
    rec = records[0]
    assert rec.model == "qwen"  # type: ignore[attr-defined]
    assert rec.kind == "dflash"  # type: ignore[attr-defined]
    assert rec.variant == "lilicorr"  # type: ignore[attr-defined]
    assert "b11669" in rec.reason  # type: ignore[attr-defined]
