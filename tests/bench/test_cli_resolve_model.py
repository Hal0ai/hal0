"""_resolve_model_id — map a queued roster reference to a registry id (#2346).

Per-model directories store every pull as ``<dir>/model.gguf``, so the basename
is shared by many registry models. A basename match must never pre-empt an
exact id/path match, and must only be trusted when it names one model.
"""

from __future__ import annotations

from hal0.bench.cli import _resolve_model_id

REGISTRY = [
    {"id": "a", "path": "/m/A/model.gguf"},
    {"id": "b", "path": "/m/B/model.gguf"},
    {"id": "q", "path": "/m/chat/Qwen3-8B-Q4_K_M.gguf"},
]


def test_v1_path_like_id_resolves_to_its_own_dir_not_the_first_basename_match() -> None:
    assert _resolve_model_id("B/model.gguf", REGISTRY) == "b"


def test_exact_id_wins_over_an_earlier_basename_match() -> None:
    assert _resolve_model_id("b", REGISTRY) == "b"


def test_full_path_resolves() -> None:
    assert _resolve_model_id("/m/B/model.gguf", REGISTRY) == "b"


def test_unique_basename_still_falls_back() -> None:
    assert _resolve_model_id("chat/Qwen3-8B-Q4_K_M.gguf", REGISTRY) == "q"
    assert _resolve_model_id("Qwen3-8B-Q4_K_M.gguf", REGISTRY) == "q"


def test_ambiguous_basename_alone_is_left_unchanged() -> None:
    assert _resolve_model_id("model.gguf", REGISTRY) == "model.gguf"


def test_unknown_reference_is_left_unchanged() -> None:
    assert _resolve_model_id("nope", REGISTRY) == "nope"
