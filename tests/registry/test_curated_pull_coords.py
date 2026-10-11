"""Tests for the custom-GGUF curated coords added in fix/stack-model-pull-coords.

These ids are referenced by exported stacks but were originally auto-scanned
into the registry with EMPTY hf_repo/hf_file. (The ROCmFP4 chadrock/halostrix
rows are gone: no hal0 runner can load them, ADR-0028.) Registering their HF coordinates
in the curated catalogue makes pull-by-id (via ``get_curated`` fallback in
``_resolve_pull_source``), scan-backfill, and stack export all resolve.
"""

from __future__ import annotations

import pytest

from hal0.registry.curated import get_curated
from hal0.registry.discover import _match_curated

# (registry id, hf_repo, hf_file) for each of the plain-GGUF custom entries.
_EXPECTED: list[tuple[str, str, str]] = [
    (
        "qwopus3-6-27b-coder-mtp-q6-k",
        "Jackrong/Qwopus3.6-27B-Coder-MTP-GGUF",
        "Qwopus3.6-27B-Coder-MTP-Q6_K.gguf",
    ),
    (
        "qwopus3-5-9b-coder-mtp-q6-k",
        "Jackrong/Qwopus3.5-9B-Coder-MTP-GGUF",
        "Qwopus3.5-9B-Coder-MTP-Q6_K.gguf",
    ),
    (
        "qwopus3-5-4b-coder-mtp-q6-k",
        "Jackrong/Qwopus3.5-4B-Coder-MTP-GGUF",
        "Qwopus3.5-4B-Coder-MTP-Q6_K.gguf",
    ),
    (
        "qwen3-5-9b-deepseek-v4-flash-mtp-q6-k",
        "Jackrong/Qwen3.5-9B-DeepSeek-V4-Flash-MTP-GGUF",
        "Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q6_K.gguf",
    ),
    (
        "gemma-4-12b-it-ud-q4-k-xl",
        "unsloth/gemma-4-12b-it-GGUF",
        "gemma-4-12b-it-UD-Q4_K_XL.gguf",
    ),
]


@pytest.mark.parametrize(("model_id", "hf_repo", "hf_file"), _EXPECTED)
def test_get_curated_resolves_exact_coords(model_id: str, hf_repo: str, hf_file: str) -> None:
    """Each new id resolves via get_curated() to the EXACT hf_repo/hf_file.

    This is what makes pull-by-id work for a coord-less registry row:
    ``_resolve_pull_source`` falls back to ``get_curated(model_id)``.
    """
    entry = get_curated(model_id)
    assert entry is not None, f"{model_id} not in curated catalogue"
    assert entry.hf_repo == hf_repo
    assert entry.hf_file == hf_file
    assert entry.capability == "chat"
    assert entry.backend == "llamacpp"


@pytest.mark.parametrize(("model_id", "hf_repo", "hf_file"), _EXPECTED)
def test_match_curated_by_filename(model_id: str, hf_repo: str, hf_file: str) -> None:
    """The on-disk filename resolves back to the curated entry (scan-backfill)."""
    entry = _match_curated(hf_file)
    assert entry is not None, f"no curated match for {hf_file}"
    assert entry.id == model_id


def test_match_curated_seed_stack_files() -> None:
    """The seed-stack models match their curated entries by filename."""
    saber = _match_curated("Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf")
    assert saber is not None
    assert saber.id == "Qwen3.6-35B-A3B-MTP-GGUF"

    pi_utility = _match_curated("Qwen3.8-27B-UD-Q4_K_XL.gguf")
    assert pi_utility is not None
    assert pi_utility.id == "qwen3.8-27b"

    coder = _match_curated("Qwopus3.6-27B-Coder-MTP-Q6_K.gguf")
    assert coder is not None
    assert coder.id == "qwopus3-6-27b-coder-mtp-q6-k"


_RETIRED: list[tuple[str, str]] = [
    (
        "qwen3-6-35b-a3b-nsc-ace-saber-mtp-f16-to-rocmfp4-strix-lean",
        "Qwen3.6-35B-A3B-NSC-ACE-SABER-MTP-F16-to-ROCmFP4-STRIX_LEAN.gguf",
    ),
    (
        "chadrock3-6-27b-pi-agent-mtp-rocmfp4-strix-lean",
        "CHADROCK3.6-27B-Pi-Agent-MTP-ROCmFP4-STRIX_LEAN.gguf",
    ),
    (
        "chadrock3-6-35b-uncensored-mtp-strix-lean",
        "CHADROCK3.6-35B-UNCENSORED-MTP-STRIX-LEAN.gguf",
    ),
    ("qwen3-6-35b-a3b-halostrix-dyn-mtp-v7", "Qwen3.6-35B-A3B-HaloStrix-Dyn-MTP-v7.gguf"),
    (
        "qwopus3-6-27b-v2-mtp-bf16-to-rocmfp4-strix-lean",
        "Qwopus3.6-27B-v2-MTP-BF16-to-ROCmFP4-STRIX_LEAN.gguf",
    ),
]


@pytest.mark.parametrize(("model_id", "hf_file"), _RETIRED)
def test_retired_rocmfp4_rows_are_not_curated(model_id: str, hf_file: str) -> None:
    """The fork-only ROCmFP4 rows left the catalogue: neither the id nor the
    on-disk filename resolves, so pull-by-id and scan-backfill cannot offer a
    model no runner can load."""
    assert get_curated(model_id) is None
    assert _match_curated(hf_file) is None


def test_no_duplicate_ids() -> None:
    """The new ids do not collide with any existing curated id."""
    from hal0.registry.curated import CURATED_BY_ID, CURATED_MODELS

    assert len(CURATED_BY_ID) == len(CURATED_MODELS), "duplicate curated id detected"
    for model_id, _, _ in _EXPECTED:
        assert model_id in CURATED_BY_ID
