"""Tests for hal0.registry.discover — filesystem scan + auto-register."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.schema import ModelsConfig
from hal0.registry.discover import (
    find_candidates,
    register_candidate,
    scan_and_register,
)
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry


def test_model_mmproj_defaults_none() -> None:
    """A Model with no sidecar carries mmproj=None (the registry contract
    the llama-server provider reads via model_info.get('mmproj'))."""
    m = Model(id="x", path="/tmp/x.gguf")
    assert m.mmproj is None


@pytest.fixture
def model_root(tmp_path: Path) -> Path:
    """A temp tree with a mix of model files and noise."""
    root = tmp_path / "models"
    root.mkdir()
    # Chat model — must show up.
    (root / "qwen3-4b-instruct-q4_k_m.gguf").write_bytes(b"x" * 128)
    # Safetensors variant — must show up.
    (root / "v1-5-pruned-emaonly.safetensors").write_bytes(b"y" * 256)
    # Embed model (capability_guess should be 'embed').
    (root / "nomic-embed-text-v1.gguf").write_bytes(b"z" * 64)
    # mmproj projector — must be skipped.
    (root / "mmproj-F16.gguf").write_bytes(b"a" * 32)
    # .tmp partial — must be skipped.
    (root / "incomplete.gguf.tmp").write_bytes(b"b" * 16)
    # Dotfile — must be skipped.
    (root / ".hidden.gguf").write_bytes(b"c" * 8)
    # Unrelated suffix — must be skipped.
    (root / "readme.txt").write_text("hi")
    # Whisper ASR — capability_guess should be 'asr'.
    sub = root / "whisper"
    sub.mkdir()
    (sub / "ggml-whisper-large-v3.bin").write_bytes(b"d" * 8)  # wrong ext, skipped
    (sub / "whisper-large-v3.gguf").write_bytes(b"e" * 64)
    return root


@pytest.fixture
def registry(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=tmp_path / "registry")


def test_find_candidates_skips_noise(model_root: Path) -> None:
    candidates = find_candidates(
        roots=[model_root],
        extensions=[".gguf", ".safetensors"],
        known_paths=set(),
    )
    names = {c.path.name for c in candidates}
    assert "qwen3-4b-instruct-q4_k_m.gguf" in names
    assert "v1-5-pruned-emaonly.safetensors" in names
    assert "nomic-embed-text-v1.gguf" in names
    assert "whisper-large-v3.gguf" in names
    assert "mmproj-F16.gguf" not in names
    assert "incomplete.gguf.tmp" not in names
    assert ".hidden.gguf" not in names
    assert "readme.txt" not in names
    assert "ggml-whisper-large-v3.bin" not in names


def test_suggested_id_normalisation(model_root: Path) -> None:
    candidates = find_candidates(
        roots=[model_root],
        extensions=[".gguf", ".safetensors"],
        known_paths=set(),
    )
    by_name = {c.path.name: c for c in candidates}
    # underscores, dots, mixed case all collapse to single hyphens.
    assert by_name["qwen3-4b-instruct-q4_k_m.gguf"].suggested_id == "qwen3-4b-instruct-q4-k-m"
    assert by_name["v1-5-pruned-emaonly.safetensors"].suggested_id == "v1-5-pruned-emaonly"


def test_capability_guess(model_root: Path) -> None:
    candidates = find_candidates(
        roots=[model_root],
        extensions=[".gguf", ".safetensors"],
        known_paths=set(),
    )
    caps = {c.path.name: c.capability_guess for c in candidates}
    assert caps["nomic-embed-text-v1.gguf"] == "embed"
    assert caps["whisper-large-v3.gguf"] == "asr"
    # Default for a plain chat filename is 'chat'.
    assert caps["qwen3-4b-instruct-q4_k_m.gguf"] == "chat"


def test_curated_match_by_filename(model_root: Path) -> None:
    """A discovered file whose name matches a curated entry's hf_file
    must surface that curated entry on the candidate."""
    candidates = find_candidates(
        roots=[model_root],
        extensions=[".gguf", ".safetensors"],
        known_paths=set(),
    )
    by_name = {c.path.name: c for c in candidates}
    # qwen3-4b-instruct-q4_k_m.gguf is the curated qwen3-4b's hf_file.
    qwen = by_name["qwen3-4b-instruct-q4_k_m.gguf"]
    assert qwen.curated_match is not None
    assert qwen.curated_match.id == "qwen3-4b"
    # No curated entry for the embed model in CURATED_MODELS.
    embed = by_name["nomic-embed-text-v1.gguf"]
    assert embed.curated_match is None


def test_known_paths_short_circuit(model_root: Path) -> None:
    """Files already in known_paths must not appear in the candidate list."""
    already = (model_root / "qwen3-4b-instruct-q4_k_m.gguf").resolve()
    candidates = find_candidates(
        roots=[model_root],
        extensions=[".gguf", ".safetensors"],
        known_paths={str(already)},
    )
    names = {c.path.name for c in candidates}
    assert "qwen3-4b-instruct-q4_k_m.gguf" not in names


def test_register_candidate_curated_uses_curated_id(
    model_root: Path, registry: ModelRegistry
) -> None:
    candidates = find_candidates(
        roots=[model_root],
        extensions=[".gguf", ".safetensors"],
        known_paths=set(),
    )
    qwen = next(c for c in candidates if c.path.name == "qwen3-4b-instruct-q4_k_m.gguf")
    model = register_candidate(registry, qwen)
    assert model.id == "qwen3-4b"
    assert "Qwen3" in model.name
    assert model.hf_repo  # populated from the curated entry


def test_register_candidate_non_curated_uses_suggested_id(
    model_root: Path, registry: ModelRegistry
) -> None:
    candidates = find_candidates(
        roots=[model_root],
        extensions=[".gguf", ".safetensors"],
        known_paths=set(),
    )
    embed = next(c for c in candidates if c.path.name == "nomic-embed-text-v1.gguf")
    model = register_candidate(registry, embed)
    assert model.id == "nomic-embed-text-v1"
    assert "embed" in model.capabilities


def test_scan_and_register_idempotent(model_root: Path, registry: ModelRegistry) -> None:
    cfg = ModelsConfig(roots=[str(model_root)])
    first = scan_and_register(registry, cfg)
    assert len(first["added"]) >= 3
    # Second run finds no new files.
    second = scan_and_register(registry, cfg)
    assert second["added"] == []


@pytest.fixture
def vision_root(tmp_path: Path) -> Path:
    """A model directory laid out like the real chat model + mmproj sidecar."""
    root = tmp_path / "models"
    root.mkdir()
    vision_dir = root / "qwopus3.6-27b-v2"
    vision_dir.mkdir()
    (vision_dir / "qwopus3.6-27b-v2.STRIX_LEAN.gguf").write_bytes(b"m" * 256)
    # The sidecar — note the .mmproj extension is NOT in file_extensions,
    # so association must key on the filename, not the suffix.
    (vision_dir / "mmproj-F32.mmproj").write_bytes(b"p" * 64)
    # A plain chat model with no sidecar in its own directory.
    plain_dir = root / "plain-chat"
    plain_dir.mkdir()
    (plain_dir / "plain-chat-q4_k_m.gguf").write_bytes(b"c" * 128)
    return root


def test_find_candidates_associates_sidecar(vision_root: Path) -> None:
    """A *mmproj* file beside a main model attaches to that model's
    candidate; the sidecar itself is never emitted as a candidate."""
    candidates = find_candidates(
        roots=[vision_root],
        extensions=[".gguf", ".safetensors"],
        known_paths=set(),
    )
    by_name = {c.path.name: c for c in candidates}
    # The sidecar is not a routable candidate.
    assert "mmproj-F32.mmproj" not in by_name
    # The main model carries the sidecar's resolved path.
    main = by_name["qwopus3.6-27b-v2.STRIX_LEAN.gguf"]
    assert main.mmproj is not None
    assert Path(main.mmproj).name == "mmproj-F32.mmproj"
    assert Path(main.mmproj).is_file()


def test_find_candidates_no_sidecar_is_none(vision_root: Path) -> None:
    """A model in a directory with no sidecar resolves mmproj=None
    (no false positives leaking across directories)."""
    candidates = find_candidates(
        roots=[vision_root],
        extensions=[".gguf", ".safetensors"],
        known_paths=set(),
    )
    by_name = {c.path.name: c for c in candidates}
    assert by_name["plain-chat-q4_k_m.gguf"].mmproj is None


def test_scan_and_register_attaches_and_omits_sidecar(
    vision_root: Path, registry: ModelRegistry
) -> None:
    """End-to-end: the registered main model resolves its mmproj path, and
    no standalone model is registered for the sidecar."""
    cfg = ModelsConfig(roots=[str(vision_root)])
    scan_and_register(registry, cfg)
    models = registry.list()
    # No registered model points at the sidecar.
    assert all("mmproj" not in Path(m.path).name.lower() for m in models)
    # The main vision model carries its sidecar path.
    main = next(m for m in models if m.path.endswith("STRIX_LEAN.gguf"))
    assert main.mmproj is not None
    assert Path(main.mmproj).name == "mmproj-F32.mmproj"
    # The plain model has no sidecar.
    plain = next(m for m in models if m.path.endswith("plain-chat-q4_k_m.gguf"))
    assert plain.mmproj is None


def test_scan_and_register_missing_root_is_silent(tmp_path: Path, registry: ModelRegistry) -> None:
    cfg = ModelsConfig(roots=[str(tmp_path / "does-not-exist")])
    result = scan_and_register(registry, cfg)
    assert result["added"] == []
    assert result["scanned_roots"] == [str(tmp_path / "does-not-exist")]
