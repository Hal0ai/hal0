"""Speculative-decoding drafter classification and fit check (ADR-0031 §2).

Pure logic over GGUF header dicts as returned by
:func:`hal0.registry.gguf_header.read_gguf_header`. A drafter is paired with a
target model as a file fact; before it is written or downloaded,
:func:`screen_draft_fit` decides from the two headers alone whether it fits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hal0.errors import UnprocessableEntity

#: Maximum ``vocab_size`` difference a classic drafter may have against its
#: target. The first part of llama.cpp's own check; the token-string
#: comparison needs the vocab block and is left to llama.cpp.
_SIMPLE_VOCAB_DELTA_MAX = 128

_NEXTN_TENSOR = re.compile(r"^blk\.\d+\.nextn\.")


@dataclass(frozen=True)
class DrafterInfo:
    """What a drafter file is, read from its header.

    ``embedding_length`` is the target-embedding width the drafter was built
    for (``dflash.embedding_length`` / ``eagle3.target_hidden_size``), or the
    drafter's own ``<arch>.embedding_length`` for ``mtp`` / ``simple``. The
    tokenizer fields feed the ``simple`` fit check and are not persisted.
    """

    kind: str  # dflash | eagle3 | mtp | simple
    variant: str
    block_size: int | None
    embedding_length: int | None
    target_layers: list[int]
    vocab_size: int | None
    base_model: str | None
    architecture: str
    tokenizer_model: str | None = field(default=None, compare=False)
    bos_token_id: int | None = field(default=None, compare=False)
    eos_token_id: int | None = field(default=None, compare=False)

    def as_metadata(self) -> dict[str, Any]:
        """The ``metadata.draft`` shape (ADR-0031 §1), without size/sha."""
        meta: dict[str, Any] = {
            "variant": self.variant,
            "block_size": self.block_size,
            "embedding_length": self.embedding_length,
            "target_layers": self.target_layers,
            "vocab_size": self.vocab_size,
            "base_model": self.base_model,
        }
        return {k: v for k, v in meta.items() if v is not None}


def _int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, int)]
    return []


def _pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def classify_drafter(header: dict[str, Any]) -> DrafterInfo | None:
    """Classify a GGUF header as a drafter; ``None`` if it is not one."""
    arch = header.get("general.architecture")
    if not isinstance(arch, str) or not arch:
        return None
    names = header.get("tensor_names_prefix") or []
    base = header.get("general.base_model.0.name")
    common: dict[str, Any] = {
        "base_model": base if isinstance(base, str) else None,
        "architecture": arch,
        "tokenizer_model": header.get("tokenizer.ggml.model"),
        "bos_token_id": header.get("tokenizer.ggml.bos_token_id"),
        "eos_token_id": header.get("tokenizer.ggml.eos_token_id"),
    }

    if arch == "dflash":
        if _pos_int(header.get("lilicorr.candidate_topk")):
            variant = "lilicorr"
        elif _pos_int(header.get("dflash.selector_top_k")):
            variant = "dflash2"
        elif "markov_w1.weight" in names:
            variant = "dspark"
        else:
            variant = "dflash"
        return DrafterInfo(
            kind="dflash",
            variant=variant,
            block_size=header.get("dflash.block_size"),
            embedding_length=header.get("dflash.embedding_length"),
            target_layers=_int_list(header.get("dflash.target_layers")),
            vocab_size=header.get("dflash.vocab_size"),
            **common,
        )
    if arch == "eagle3":
        return DrafterInfo(
            kind="eagle3",
            variant="eagle3",
            block_size=None,
            embedding_length=header.get("eagle3.target_hidden_size"),
            target_layers=_int_list(header.get("eagle3.target_layers")),
            vocab_size=header.get("eagle3.vocab_size"),
            **common,
        )

    own = {
        "block_size": None,
        "embedding_length": header.get(f"{arch}.embedding_length"),
        "target_layers": [],
        "vocab_size": header.get(f"{arch}.vocab_size"),
    }
    nextn = header.get(f"{arch}.nextn_predict_layers")
    if _pos_int(nextn) and any(_NEXTN_TENSOR.match(n) for n in names):
        return DrafterInfo(kind="mtp", variant="mtp", **own, **common)
    if isinstance(header.get("tokenizer.ggml.model"), str):
        return DrafterInfo(kind="simple", variant="simple", **own, **common)
    return None


def _simple_identity_errors(target: dict[str, Any], draft: DrafterInfo) -> list[str]:
    """Tokenizer identity a ``simple`` drafter must share with its target."""
    return [
        f"{label} {mine} != target {theirs}"
        for label, mine, theirs in (
            ("tokenizer.ggml.model", draft.tokenizer_model, target.get("tokenizer.ggml.model")),
            ("bos_token_id", draft.bos_token_id, target.get("tokenizer.ggml.bos_token_id")),
            ("eos_token_id", draft.eos_token_id, target.get("tokenizer.ggml.eos_token_id")),
        )
        if mine != theirs
    ]


def draft_fit_errors(target: dict[str, Any], draft: DrafterInfo) -> list[str]:
    """Reasons ``draft`` does not fit ``target`` (empty = fits)."""
    t_arch = target.get("general.architecture")
    t_emb = target.get(f"{t_arch}.embedding_length")
    t_blocks = target.get(f"{t_arch}.block_count")
    t_vocab = target.get(f"{t_arch}.vocab_size")
    errors: list[str] = []

    def check_layers(key: str) -> None:
        if not isinstance(t_blocks, int):
            errors.append(f"target block_count unknown; cannot check {key}")
            return
        bad = [n for n in draft.target_layers if n >= t_blocks]
        if bad:
            errors.append(f"{key} {bad} not below target block_count {t_blocks}")

    def check_vocab() -> None:
        if isinstance(draft.vocab_size, int) and isinstance(t_vocab, int):
            delta = abs(draft.vocab_size - t_vocab)
            limit = 0 if draft.kind == "eagle3" else _SIMPLE_VOCAB_DELTA_MAX
            if delta > limit:
                errors.append(f"vocab_size {draft.vocab_size} vs target {t_vocab}")

    if draft.kind in ("dflash", "eagle3"):
        key = "dflash.embedding_length" if draft.kind == "dflash" else "eagle3.target_hidden_size"
        # Both widths must be known ints: an unknown on both sides is not a match.
        if not (
            isinstance(draft.embedding_length, int)
            and isinstance(t_emb, int)
            and draft.embedding_length == t_emb
        ):
            errors.append(f"{key} {draft.embedding_length} != target embedding_length {t_emb}")
        check_layers(f"{draft.kind}.target_layers")
        if draft.kind == "eagle3":
            check_vocab()
    elif draft.kind == "mtp":
        if draft.architecture != t_arch:
            errors.append(f"architecture {draft.architecture} != target architecture {t_arch}")
    elif draft.kind == "simple":
        errors += _simple_identity_errors(target, draft)
        check_vocab()
    return errors


def screen_draft_fit(target: dict[str, Any], draft_header: dict[str, Any]) -> DrafterInfo:
    """Classify ``draft_header`` and refuse it unless it fits ``target``.

    Raises :class:`UnprocessableEntity` with ``model.draft_not_a_drafter`` or
    ``model.draft_mismatch`` (``details["reasons"]``).
    """
    info = classify_drafter(draft_header)
    if info is None:
        arch = draft_header.get("general.architecture")
        raise UnprocessableEntity(
            f"not a speculative-decoding drafter (architecture {arch!r})",
            code="model.draft_not_a_drafter",
            details={"reasons": [f"architecture {arch!r} is not a drafter"]},
        )
    if info.kind == "simple":
        # Any arch-plus-tokenizer header classifies as ``simple``; one whose
        # tokenizer is not the target's is not a drafter for this model at
        # all. Only a vocab delta between same-tokenizer models is a mismatch.
        identity = _simple_identity_errors(target, info)
        if identity:
            raise UnprocessableEntity(
                "not a speculative-decoding drafter for this model: " + "; ".join(identity),
                code="model.draft_not_a_drafter",
                details={"reasons": identity},
            )
    reasons = draft_fit_errors(target, info)
    if reasons:
        raise UnprocessableEntity(
            "drafter does not fit this model: " + "; ".join(reasons),
            code="model.draft_mismatch",
            details={"reasons": reasons},
        )
    return info
