"""Tests for hal0.registry.discover — filesystem scan + auto-register."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.schema import ModelsConfig
from hal0.registry.discover import (
    backfill_coordless,
    find_candidates,
    prune_missing,
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


def test_capability_guess_classifies_diffusion_media() -> None:
    """Clearly-diffusion media files classify as image/video, not the chat
    default — keeps them out of the chat fallback pool (#940 hardening)."""
    from hal0.registry.discover import _guess_capability

    assert _guess_capability("ltx-2-19b-dev-fp8.gguf") == "video"
    assert _guess_capability("wan2.1-t2v-14b.gguf") == "video"
    assert _guess_capability("flux1-dev.gguf") == "image"
    assert _guess_capability("sdxl-base-1.0.safetensors") == "image"
    # A plain chat model is still 'chat' — the new branches are last-resort.
    assert _guess_capability("qwen3-4b-instruct-q4_k_m.gguf") == "chat"


def test_capability_guess_classifies_rerankers() -> None:
    """MR-3: a reranker filename must classify as 'rerank', not the old 'chat'
    default. rerank is checked before embed so a "bge-reranker" name (which
    also carries the embed token "bge") doesn't fall through to embed."""
    from hal0.registry.discover import _guess_capability

    # The bug: these auto-scanned as 'chat' before the shared-table fix.
    assert _guess_capability("bge-reranker-v2-m3-Q8_0.gguf") == "rerank"
    assert _guess_capability("bge-reranker-base-f16.gguf") == "rerank"
    # bge non-reranker stays embed (rerank precedence didn't swallow it).
    assert _guess_capability("bge-base-en-v1.5.gguf") == "embed"
    # No vocabulary regression for the other families.
    assert _guess_capability("ltx-2-19b-dev-fp8.gguf") == "video"
    assert _guess_capability("nomic-embed-text-v1.gguf") == "embed"
    assert _guess_capability("whisper-large-v3.gguf") == "asr"
    assert _guess_capability("llama-3.1-8b-instruct.gguf") == "chat"


def test_scan_and_register_reranker_capability(tmp_path: Path, registry: ModelRegistry) -> None:
    """End-to-end: a NON-curated reranker gguf under a scan root registers with
    capabilities == ['rerank'] (it registered as ['chat'] before MR-3), while
    an EXACT curated reranker filename still registers 'rerank' via the curated
    override branch."""
    root = tmp_path / "models"
    root.mkdir()
    # Non-curated reranker (Q8_0 is not a curated hf_file) → capability_guess.
    non_curated = root / "bge-reranker-v2-m3-Q8_0.gguf"
    non_curated.write_bytes(b"r" * 64)
    # Exact curated reranker filename → curated-override branch.
    curated = root / "bge-reranker-v2-m3-Q4_K_M.gguf"
    curated.write_bytes(b"c" * 64)

    cfg = ModelsConfig(roots=[str(root)])
    scan_and_register(registry, cfg)
    models = {Path(m.path).name: m for m in registry.list()}

    assert models["bge-reranker-v2-m3-Q8_0.gguf"].capabilities == ["rerank"]
    # The curated entry masks discover and still yields 'rerank'.
    curated_model = models["bge-reranker-v2-m3-Q4_K_M.gguf"]
    assert curated_model.capabilities == ["rerank"]
    assert curated_model.id == "bge-reranker-v2-m3-q4_k_m"


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


def test_register_candidate_comfyui_checkpoint_tagged_image(
    tmp_path: Path, registry: ModelRegistry
) -> None:
    """A checkpoint under the ComfyUI models tree registers as image/comfyui,
    not the chat fallback — so it lands on the dashboard's image surface and
    stays out of the chat fallback pool."""
    ckpt_dir = tmp_path / "comfyui" / "models" / "checkpoints"
    ckpt_dir.mkdir(parents=True)
    (ckpt_dir / "dreamshaper_8.safetensors").write_bytes(b"x" * 256)
    candidates = find_candidates(roots=[tmp_path], extensions=[".safetensors"], known_paths=set())
    cand = next(c for c in candidates if c.path.name == "dreamshaper_8.safetensors")
    model = register_candidate(registry, cand)
    assert model.capabilities == ["image"]
    assert model.backends == ["comfyui"]


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
    # The configured (missing) root is scanned silently. scan_roots() also folds
    # in the effective store/pull_root, so this is a membership check, not exact.
    assert str(tmp_path / "does-not-exist") in result["scanned_roots"]


def test_backfill_coordless_fills_from_curated(registry: ModelRegistry) -> None:
    """An existing registry row with empty coords whose on-disk filename matches
    a curated entry is repaired in place; the id is unchanged."""
    mid = "qwen3-6-35b-a3b-nsc-ace-saber-mtp-f16-to-rocmfp4-strix-lean"
    fname = "Qwen3.6-35B-A3B-NSC-ACE-SABER-MTP-F16-to-ROCmFP4-STRIX_LEAN.gguf"
    registry.add(
        Model(
            id=mid,
            path=f"/models/{fname}",
            name="",
            hf_repo="",
            hf_filename="",
            capabilities=["chat"],
        )
    )
    repaired = backfill_coordless(registry)
    assert repaired == [mid]
    row = registry.get(mid)
    assert row.id == mid  # id never changes
    assert row.hf_repo == "jcbtc/chadrock-35b-ace-saber-rocmfp4-mtp"
    assert row.hf_filename == fname
    assert row.name  # display name filled


def test_backfill_coordless_is_idempotent(registry: ModelRegistry) -> None:
    """A second backfill pass is a no-op once coords are present."""
    fname = "Qwopus3.6-27B-Coder-MTP-Q6_K.gguf"
    mid = "qwopus3-6-27b-coder-mtp-q6-k"
    registry.add(Model(id=mid, path=f"/models/{fname}", hf_repo="", hf_filename=""))
    assert backfill_coordless(registry) == [mid]
    assert backfill_coordless(registry) == []


def test_backfill_coordless_skips_rows_with_coords(registry: ModelRegistry) -> None:
    """A row that already carries coords is never touched, even with a curated
    match by filename."""
    fname = "Qwopus3.6-27B-Coder-MTP-Q6_K.gguf"
    registry.add(
        Model(
            id="qwopus3-6-27b-coder-mtp-q6-k",
            path=f"/models/{fname}",
            hf_repo="someone/custom-repo",
            hf_filename=fname,
        )
    )
    assert backfill_coordless(registry) == []
    assert registry.get("qwopus3-6-27b-coder-mtp-q6-k").hf_repo == "someone/custom-repo"


def test_backfill_coordless_no_curated_match_left_alone(registry: ModelRegistry) -> None:
    """A coord-less row with no curated filename match is left as-is."""
    registry.add(Model(id="mystery", path="/models/mystery.gguf", hf_repo="", hf_filename=""))
    assert backfill_coordless(registry) == []


def test_find_candidates_groups_complete_shard_set(tmp_path: Path) -> None:
    """ML-2 behaviour flip (plan §7.1c a4): a complete shard set is now
    GROUPED into one candidate (shard-1 as entry point, aggregate size,
    ordered ``shards`` list) instead of being silently dropped."""
    root = tmp_path / "models"
    root.mkdir()
    (root / "big-model-00001-of-00002.gguf").write_bytes(b"a" * 100)
    (root / "big-model-00002-of-00002.gguf").write_bytes(b"b" * 50)
    candidates = find_candidates(roots=[root], extensions=[".gguf"], known_paths=set())
    names = {c.path.name for c in candidates}
    assert "big-model-00001-of-00002.gguf" in names
    assert "big-model-00002-of-00002.gguf" not in names  # not its own candidate
    cand = next(c for c in candidates if c.path.name == "big-model-00001-of-00002.gguf")
    assert cand.shards is not None
    assert [p.name for p in cand.shards] == [
        "big-model-00001-of-00002.gguf",
        "big-model-00002-of-00002.gguf",
    ]
    assert cand.size_bytes == 150  # aggregate across the whole set


def test_find_candidates_drops_incomplete_shard_set(tmp_path: Path) -> None:
    """An incomplete group (no shard-1) is still dropped — llama-server
    can't load a set missing its entry point."""
    root = tmp_path / "models"
    root.mkdir()
    (root / "big-model-00002-of-00003.gguf").write_bytes(b"a" * 100)
    (root / "big-model-00003-of-00003.gguf").write_bytes(b"b" * 50)
    candidates = find_candidates(roots=[root], extensions=[".gguf"], known_paths=set())
    assert candidates == []


def test_register_candidate_writes_shard_model_file_rows(
    tmp_path: Path, registry: ModelRegistry
) -> None:
    """A grouped shard candidate populates `model_file` rows (ML-2 is the
    first writer of that table) alongside the registry row itself."""
    from hal0.db.connection import connect

    root = tmp_path / "models"
    root.mkdir()
    (root / "shardy-00001-of-00002.gguf").write_bytes(b"a" * 100)
    (root / "shardy-00002-of-00002.gguf").write_bytes(b"b" * 50)
    candidates = find_candidates(roots=[root], extensions=[".gguf"], known_paths=set())
    cand = next(c for c in candidates if c.shards is not None)
    model = register_candidate(registry, cand)
    assert model.metadata["shards"]
    with connect(registry.db_path) as conn:
        rows = conn.execute(
            "SELECT rel, role, shard_index FROM model_file WHERE model_id = ? ORDER BY shard_index",
            (model.id,),
        ).fetchall()
    assert [dict(r) for r in rows] == [
        {"rel": "shardy-00001-of-00002.gguf", "role": "shard", "shard_index": 1},
        {"rel": "shardy-00002-of-00002.gguf", "role": "shard", "shard_index": 2},
    ]


def test_scan_and_register_backfills_existing_coordless_row(
    tmp_path: Path, registry: ModelRegistry
) -> None:
    """End-to-end: scan_and_register repairs an existing coord-less row whose
    file is already registered (so it never re-surfaces as a candidate)."""
    root = tmp_path / "models"
    root.mkdir()
    fname = "Qwopus3.6-27B-Coder-MTP-Q6_K.gguf"
    fpath = root / fname
    fpath.write_bytes(b"x" * 64)
    mid = "qwopus3-6-27b-coder-mtp-q6-k"
    registry.add(
        Model(id=mid, path=str(fpath.resolve()), hf_repo="", hf_filename="", capabilities=["chat"])
    )
    cfg = ModelsConfig(roots=[str(root)])
    result = scan_and_register(registry, cfg)
    assert mid in result["backfilled"]
    row = registry.get(mid)
    assert row.hf_repo == "Jackrong/Qwopus3.6-27B-Coder-MTP-GGUF"
    assert row.hf_filename == fname


# ── prune_missing / reconcile ──────────────────────────────────────────────


def test_prune_missing_removes_unprotected_missing_row(
    tmp_path: Path, registry: ModelRegistry
) -> None:
    """A row whose backing file no longer exists and is NOT referenced by any
    slot/stack is removed from the registry and reported under 'pruned'."""
    gone = tmp_path / "deleted-model-q4.gguf"
    gone.write_bytes(b"x" * 64)
    registry.add(Model(id="gone", path=str(gone.resolve()), capabilities=["chat"]))
    gone.unlink()  # weights deleted from disk → row is now stale

    result = prune_missing(registry, protected_ids=set())
    assert result["pruned"] == ["gone"]
    assert result["missing_referenced"] == []
    assert result["kept"] == 0
    assert not registry.has("gone")


def test_prune_missing_protects_referenced_missing_row(
    tmp_path: Path, registry: ModelRegistry
) -> None:
    """A row whose file is missing but whose id is in protected_ids is NOT
    removed — it is reported under 'missing_referenced' for repair instead."""
    gone = tmp_path / "slot-model-q4.gguf"
    gone.write_bytes(b"x" * 64)
    registry.add(Model(id="slot-model", path=str(gone.resolve()), capabilities=["chat"]))
    gone.unlink()

    result = prune_missing(registry, protected_ids={"slot-model"})
    assert result["pruned"] == []
    assert result["missing_referenced"] == ["slot-model"]
    assert result["kept"] == 0
    # Still present — functional drift the operator must repair, not delete.
    assert registry.has("slot-model")


def test_prune_missing_leaves_present_rows(tmp_path: Path, registry: ModelRegistry) -> None:
    """A row whose backing file still exists is untouched and counts as kept —
    even when its size_bytes metadata is 0 (stale meta is not authoritative)."""
    live = tmp_path / "live-model-q4.gguf"
    live.write_bytes(b"x" * 64)
    # size_bytes intentionally 0 (stale metadata) — must NOT trigger a prune.
    registry.add(Model(id="live", path=str(live.resolve()), size_bytes=0, capabilities=["chat"]))

    result = prune_missing(registry, protected_ids=set())
    assert result["pruned"] == []
    assert result["missing_referenced"] == []
    assert result["kept"] == 1
    assert registry.has("live")


def test_prune_missing_dry_run_does_not_remove(tmp_path: Path, registry: ModelRegistry) -> None:
    """dry_run reports what would be pruned without mutating the registry."""
    gone = tmp_path / "dry-model-q4.gguf"
    gone.write_bytes(b"x" * 64)
    registry.add(Model(id="dry", path=str(gone.resolve()), capabilities=["chat"]))
    gone.unlink()

    result = prune_missing(registry, protected_ids=set(), dry_run=True)
    assert result["pruned"] == ["dry"]
    assert registry.has("dry")  # unchanged


def test_scan_and_register_prune_true_prunes_and_returns_new_keys(
    tmp_path: Path, registry: ModelRegistry
) -> None:
    """scan_and_register(prune=True) folds prune results into its return under
    'pruned'/'missing_referenced' and removes the unprotected stale row."""
    root = tmp_path / "models"
    root.mkdir()
    # A live model that stays registered on-disk.
    live = root / "qwen3-4b-instruct-q4_k_m.gguf"
    live.write_bytes(b"y" * 128)
    # A stale row whose file was deleted.
    gone = root / "old-model-q4.gguf"
    gone.write_bytes(b"z" * 64)
    registry.add(Model(id="stale", path=str(gone.resolve()), capabilities=["chat"]))
    gone.unlink()

    cfg = ModelsConfig(roots=[str(root)])
    result = scan_and_register(registry, cfg, prune=True, protected_ids=set())
    assert "pruned" in result and "missing_referenced" in result
    assert result["pruned"] == ["stale"]
    assert result["missing_referenced"] == []
    assert not registry.has("stale")


def test_scan_and_register_default_does_not_prune(tmp_path: Path, registry: ModelRegistry) -> None:
    """Default (prune=False) never removes stale rows and returns empty
    'pruned'/'missing_referenced' so the return shape stays stable."""
    root = tmp_path / "models"
    root.mkdir()
    gone = root / "old-model-q4.gguf"
    gone.write_bytes(b"z" * 64)
    registry.add(Model(id="stale", path=str(gone.resolve()), capabilities=["chat"]))
    gone.unlink()

    cfg = ModelsConfig(roots=[str(root)])
    result = scan_and_register(registry, cfg)
    assert result["pruned"] == []
    assert result["missing_referenced"] == []
    # Stale row is left alone — add-only behavior preserved by default.
    assert registry.has("stale")
