"""Tests for ``hal0 migrate model-layout`` — PR-7 of v0.2 Lemonade migration.

Covers the behaviour contract from lemonade-adoption-plan §11 PR-7:

* Default is dry-run; ``--apply`` mutates.
* Idempotent: second ``--apply`` is a no-op.
* Registry-driven classification; on-disk fallback via v0.1.x dir layout.
* Refuses to overwrite differing symlinks without ``--force``.
* HF cache (``huggingface/``) left untouched.
* Atomic write via tempfile + ``os.replace``.

The fixture simulates ``/mnt/ai-models/`` and ``/var/lib/hal0/models/``
under ``tmp_path``; the real script never gets to touch a live install.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w
from typer.testing import CliRunner

from hal0.cli.migrate_commands import (
    CANONICAL_LEAVES,
    SymlinkAction,
    _atomic_symlink,
    _classify_registry_entry,
    _pick_capability,
    plan_migration,
)
from hal0.cli.migrate_commands import (
    app as migrate_app,
)

runner = CliRunner()


# ── Fixture helpers ───────────────────────────────────────────────────────────


def _make_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return ``(mount_root, canonical_root, registry_path)`` under ``tmp_path``."""
    mount_root = tmp_path / "mnt" / "ai-models"
    canonical_root = tmp_path / "var" / "lib" / "hal0" / "models"
    registry_path = tmp_path / "var" / "lib" / "hal0" / "registry" / "registry.toml"
    mount_root.mkdir(parents=True)
    return mount_root, canonical_root, registry_path


def _touch_file(path: Path, *, size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


def _write_registry(path: Path, models: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump({"models": models}, f)


def _invoke(
    *,
    mount_root: Path,
    canonical_root: Path,
    registry_path: Path,
    extra_args: list[str] | None = None,
) -> object:
    args = [
        "model-layout",
        "--mount-root",
        str(mount_root),
        "--canonical-root",
        str(canonical_root),
        "--registry",
        str(registry_path),
    ]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(migrate_app, args)


# ── Pure-function tests (classification + capability picking) ─────────────────


def test_pick_capability_prefers_rerank_over_chat() -> None:
    assert _pick_capability(["chat", "rerank"]) == "rerank"


def test_pick_capability_prefers_embed_over_chat() -> None:
    assert _pick_capability(["chat", "embed"]) == "embed"


def test_pick_capability_chat_when_only_chat() -> None:
    assert _pick_capability(["chat"]) == "chat"


def test_pick_capability_none_when_no_known() -> None:
    assert _pick_capability(["unknown", "weirdcap"]) is None
    assert _pick_capability([]) is None


def test_classify_llamacpp_chat_entry() -> None:
    entry = {"capabilities": ["chat"], "backends": ["vulkan", "rocm"]}
    assert _classify_registry_entry(entry) == ("llamacpp", "chat")


def test_classify_llamacpp_embed_entry() -> None:
    entry = {"capabilities": ["embed"], "backends": ["llamacpp"]}
    assert _classify_registry_entry(entry) == ("llamacpp", "embed")


def test_classify_llamacpp_rerank_entry() -> None:
    entry = {"capabilities": ["rerank"], "backends": ["vulkan"]}
    assert _classify_registry_entry(entry) == ("llamacpp", "rerank")


def test_classify_flm_wins_when_in_backends() -> None:
    entry = {"capabilities": ["chat"], "backends": ["vulkan", "flm"]}
    assert _classify_registry_entry(entry) == ("flm", "chat")


def test_classify_moonshine_becomes_whispercpp() -> None:
    entry = {"capabilities": ["asr"], "backends": ["moonshine"]}
    assert _classify_registry_entry(entry) == ("whispercpp", "stt")


def test_classify_kokoro_tts() -> None:
    entry = {"capabilities": ["tts"], "backends": ["kokoro"]}
    assert _classify_registry_entry(entry) == ("kokoro", "tts")


def test_classify_image_sdcpp() -> None:
    entry = {"capabilities": ["image"], "backends": ["sdcpp"]}
    assert _classify_registry_entry(entry) == ("sd-cpp", "img")


def test_classify_returns_none_when_no_capability() -> None:
    entry = {"capabilities": [], "backends": ["vulkan"]}
    assert _classify_registry_entry(entry) is None


def test_classify_falls_back_to_default_recipe_when_backend_unknown() -> None:
    # No recognised backend; capability=tts → default recipe is kokoro.
    entry = {"capabilities": ["tts"], "backends": ["mystery"]}
    assert _classify_registry_entry(entry) == ("kokoro", "tts")


# ── plan_migration unit tests ─────────────────────────────────────────────────


def test_plan_creates_symlink_for_registered_chat_model(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "qwen3-4b-q4_k_m.gguf")
    _write_registry(
        registry_path,
        {
            "qwen3-4b-q4_k_m": {
                "path": str(gguf),
                "capabilities": ["chat"],
                "backends": ["vulkan"],
            }
        },
    )

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    creates = [a for a in report.actions if a.kind == "create"]
    assert len(creates) == 1
    act = creates[0]
    assert act.link_path == canonical_root / "llamacpp" / "chat" / "qwen3-4b-q4_k_m.gguf"
    assert act.target_path == gguf


def test_plan_classifies_non_registry_files_by_dir(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    # Empty registry — everything falls into source-dir classification.
    voice = _touch_file(mount_root / "voices" / "af_heart.bin")
    moonshine = _touch_file(mount_root / "moonshine_voice" / "moonshine-base.bin")

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    creates_by_target = {a.target_path: a for a in report.actions if a.kind == "create"}
    assert voice in creates_by_target
    assert creates_by_target[voice].link_path == canonical_root / "kokoro" / "tts" / "af_heart.bin"
    assert moonshine in creates_by_target
    assert (
        creates_by_target[moonshine].link_path
        == canonical_root / "whispercpp" / "moonshine" / "moonshine-base.bin"
    )


def test_plan_reports_local_dir_as_unclassified_when_no_registry(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    rogue = _touch_file(mount_root / "local" / "mystery.gguf")

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    assert rogue in report.unclassified
    unclassified_actions = [a for a in report.actions if a.kind == "unclassified"]
    assert any(a.target_path == rogue for a in unclassified_actions)


def test_plan_skips_existing_correct_symlink(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )
    link = canonical_root / "llamacpp" / "chat" / "m.gguf"
    link.parent.mkdir(parents=True)
    os.symlink(gguf, link)

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    skips = [a for a in report.actions if a.kind == "skip-exists"]
    assert len(skips) == 1
    assert skips[0].link_path == link


def test_plan_refuses_to_overwrite_differing_symlink_without_force(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    wrong = _touch_file(tmp_path / "somewhere-else" / "decoy.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )
    link = canonical_root / "llamacpp" / "chat" / "m.gguf"
    link.parent.mkdir(parents=True)
    os.symlink(wrong, link)

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    blocked = [a for a in report.actions if a.kind == "would-overwrite"]
    assert len(blocked) == 1
    assert "refusing to overwrite" in blocked[0].reason


def test_plan_overwrites_differing_symlink_with_force(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    wrong = _touch_file(tmp_path / "somewhere-else" / "decoy.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )
    link = canonical_root / "llamacpp" / "chat" / "m.gguf"
    link.parent.mkdir(parents=True)
    os.symlink(wrong, link)

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=True,
    )

    overwrites = [a for a in report.actions if a.kind == "overwrite"]
    assert len(overwrites) == 1
    assert overwrites[0].target_path == gguf


def test_plan_refuses_when_real_file_occupies_canonical_path(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )
    # Plant a real file (not a symlink) where the migration wants to write.
    _touch_file(canonical_root / "llamacpp" / "chat" / "m.gguf")

    # Even with --force, a real file is not overwritten.
    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=True,
    )

    blocked = [a for a in report.actions if a.kind == "would-overwrite"]
    assert len(blocked) == 1
    assert "non-symlink" in blocked[0].reason


def test_plan_leaves_huggingface_cache_alone(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    hf_blob = _touch_file(
        mount_root
        / "huggingface"
        / "hub"
        / "models--owner--repo"
        / "snapshots"
        / "abc"
        / "config.json"
    )

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    # HF cache must not produce ANY actions.
    for action in report.actions:
        assert action.target_path != hf_blob
        if action.target_path is not None:
            assert "huggingface" not in str(action.target_path)


def test_plan_handles_already_canonical_layout(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    # Disk already in the v0.2 layout.
    gguf = _touch_file(mount_root / "llamacpp" / "chat" / "qwen.gguf")

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    creates = [a for a in report.actions if a.kind == "create"]
    assert len(creates) == 1
    assert creates[0].link_path == canonical_root / "llamacpp" / "chat" / "qwen.gguf"
    assert creates[0].target_path == gguf


def test_plan_treats_flm_model_dir_as_one_entity(tmp_path: Path) -> None:
    """FLM models live as directories with a ``config.json`` sentinel."""
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    model_dir = mount_root / "flm-ubuntu" / "gemma3-1b"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")
    (model_dir / "weights.bin").write_bytes(b"\x00" * 32)

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    creates = [a for a in report.actions if a.kind == "create"]
    # Should produce ONE link to the directory, not one per file inside.
    assert len(creates) == 1
    assert creates[0].link_path == canonical_root / "flm" / "chat" / "gemma3-1b"
    assert creates[0].target_path == model_dir


def test_plan_dedupes_registry_and_disk_scan(tmp_path: Path) -> None:
    """A registry entry covers the same file the disk scan would surface."""
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    # File lives under flm-ubuntu (v0.1.x dir) AND is in the registry.
    gguf = _touch_file(mount_root / "flm-ubuntu" / "model.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["flm"]}},
    )

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    # Only ONE create action, not two.
    creates = [a for a in report.actions if a.kind == "create"]
    assert len(creates) == 1
    assert creates[0].link_path == canonical_root / "flm" / "chat" / "model.gguf"


# ── CLI dry-run / apply / idempotency ────────────────────────────────────────


def test_cli_dry_run_does_not_write(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )

    result = _invoke(
        mount_root=mount_root,
        canonical_root=canonical_root,
        registry_path=registry_path,
    )

    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    # Canonical tree wasn't created (mkdir only happens on --apply).
    assert not canonical_root.exists() or not any(canonical_root.iterdir())


def test_cli_apply_creates_symlinks(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )

    result = _invoke(
        mount_root=mount_root,
        canonical_root=canonical_root,
        registry_path=registry_path,
        extra_args=["--apply"],
    )

    assert result.exit_code == 0, result.output
    link = canonical_root / "llamacpp" / "chat" / "m.gguf"
    assert link.is_symlink()
    assert Path(os.readlink(link)) == gguf
    # And the canonical tree got mkdir -p'd in full.
    for recipe, capability in CANONICAL_LEAVES:
        assert (canonical_root / recipe / capability).is_dir()


def test_cli_apply_is_idempotent(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )

    first = _invoke(
        mount_root=mount_root,
        canonical_root=canonical_root,
        registry_path=registry_path,
        extra_args=["--apply"],
    )
    assert first.exit_code == 0, first.output

    # Capture state.
    link = canonical_root / "llamacpp" / "chat" / "m.gguf"
    before_inode = os.lstat(link).st_ino

    second = _invoke(
        mount_root=mount_root,
        canonical_root=canonical_root,
        registry_path=registry_path,
        extra_args=["--apply"],
    )
    assert second.exit_code == 0, second.output

    after_inode = os.lstat(link).st_ino
    # Symlink wasn't replaced (skip-exists path taken).
    assert before_inode == after_inode
    assert "skip-exists" in second.output or "applied 0" in second.output


def test_cli_refuses_overwrite_without_force_in_apply(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    wrong = _touch_file(tmp_path / "decoy" / "wrong.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )
    link = canonical_root / "llamacpp" / "chat" / "m.gguf"
    link.parent.mkdir(parents=True)
    os.symlink(wrong, link)

    result = _invoke(
        mount_root=mount_root,
        canonical_root=canonical_root,
        registry_path=registry_path,
        extra_args=["--apply"],
    )

    # --apply with blocked actions exits non-zero so CI catches it.
    assert result.exit_code == 1, result.output
    # Original symlink untouched.
    assert Path(os.readlink(link)) == wrong


def test_cli_force_overwrites_differing_symlink_in_apply(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    gguf = _touch_file(mount_root / "local" / "m.gguf")
    wrong = _touch_file(tmp_path / "decoy" / "wrong.gguf")
    _write_registry(
        registry_path,
        {"m": {"path": str(gguf), "capabilities": ["chat"], "backends": ["vulkan"]}},
    )
    link = canonical_root / "llamacpp" / "chat" / "m.gguf"
    link.parent.mkdir(parents=True)
    os.symlink(wrong, link)

    result = _invoke(
        mount_root=mount_root,
        canonical_root=canonical_root,
        registry_path=registry_path,
        extra_args=["--apply", "--force"],
    )

    assert result.exit_code == 0, result.output
    assert Path(os.readlink(link)) == gguf


def test_cli_refuses_symlinked_canonical_root(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    # Make canonical_root itself a symlink.
    real = tmp_path / "real-canonical"
    real.mkdir(parents=True)
    canonical_root.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(real, canonical_root)

    result = _invoke(
        mount_root=mount_root,
        canonical_root=canonical_root,
        registry_path=registry_path,
    )

    assert result.exit_code != 0
    # Typer BadParameter shows up either in stdout or stderr; the runner
    # collates both into ``result.output``.
    assert "symlink" in result.output.lower() or "bad" in result.output.lower()


def test_cli_empty_mount_root_is_noop(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    # mount_root exists but empty.

    result = _invoke(
        mount_root=mount_root,
        canonical_root=canonical_root,
        registry_path=registry_path,
        extra_args=["--apply"],
    )

    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.output or "applied 0" in result.output


# ── Atomicity ─────────────────────────────────────────────────────────────────


def test_atomic_symlink_leaves_no_tempfile_on_success(tmp_path: Path) -> None:
    target = _touch_file(tmp_path / "target.bin")
    link = tmp_path / "linkdir" / "link"

    _atomic_symlink(link, target)

    assert link.is_symlink()
    assert Path(os.readlink(link)) == target
    # No leftover .tmp files in the link's parent.
    leftover = list(link.parent.glob(".link.*.lnk.tmp"))
    assert leftover == []


def test_atomic_symlink_cleans_up_on_replace_failure(tmp_path: Path) -> None:
    target = _touch_file(tmp_path / "target.bin")
    link = tmp_path / "linkdir" / "link"
    link.parent.mkdir(parents=True)

    # Force os.replace to fail so we exercise the cleanup branch.
    with (
        patch("hal0.cli.migrate_commands.os.replace", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        _atomic_symlink(link, target)

    # No partial state: the tempfile is cleaned up, the final link
    # wasn't created.
    leftover = list(link.parent.glob(".link.*.lnk.tmp"))
    assert leftover == []
    assert not link.exists() and not link.is_symlink()


def test_execute_plan_crash_midway_leaves_no_broken_state(tmp_path: Path) -> None:
    """Simulate a crash partway through ``execute_plan``: completed
    symlinks survive, the rest are simply not created, and the canonical
    tree never contains a half-built tempfile.
    """
    from hal0.cli.migrate_commands import execute_plan

    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    g1 = _touch_file(mount_root / "local" / "a.gguf")
    g2 = _touch_file(mount_root / "local" / "b.gguf")
    g3 = _touch_file(mount_root / "local" / "c.gguf")
    _write_registry(
        registry_path,
        {
            "a": {"path": str(g1), "capabilities": ["chat"], "backends": ["vulkan"]},
            "b": {"path": str(g2), "capabilities": ["chat"], "backends": ["vulkan"]},
            "c": {"path": str(g3), "capabilities": ["chat"], "backends": ["vulkan"]},
        },
    )
    # The script's apply path mkdir -ps the canonical tree before
    # writing symlinks; replicate that here.
    for recipe, capability in CANONICAL_LEAVES:
        (canonical_root / recipe / capability).mkdir(parents=True, exist_ok=True)

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    # Make the 3rd os.replace blow up.
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("simulated crash")
        real_replace(src, dst)

    with (
        patch("hal0.cli.migrate_commands.os.replace", side_effect=flaky_replace),
        pytest.raises(OSError, match="simulated crash"),
    ):
        execute_plan(report)

    # The first two symlinks survived; the third never made it but
    # also didn't leave a tempfile behind.
    chat_dir = canonical_root / "llamacpp" / "chat"
    survivors = sorted(p.name for p in chat_dir.iterdir() if p.is_symlink())
    assert survivors == ["a.gguf", "b.gguf"]
    leftover = list(chat_dir.glob(".*.lnk.tmp"))
    assert leftover == []


# ── Reporting + structure sanity ─────────────────────────────────────────────


def test_report_by_leaf_counts_creates_only(tmp_path: Path) -> None:
    mount_root, canonical_root, registry_path = _make_tree(tmp_path)
    g1 = _touch_file(mount_root / "local" / "a.gguf")
    g2 = _touch_file(mount_root / "local" / "e.gguf")
    _write_registry(
        registry_path,
        {
            "a": {"path": str(g1), "capabilities": ["chat"], "backends": ["vulkan"]},
            "e": {"path": str(g2), "capabilities": ["embed"], "backends": ["vulkan"]},
        },
    )

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=False,
    )

    leaves = report.by_leaf()
    assert leaves[("llamacpp", "chat")] == 1
    assert leaves[("llamacpp", "embed")] == 1


def test_action_dataclass_is_frozen() -> None:
    action = SymlinkAction(
        kind="create",
        link_path=Path("/x"),
        target_path=Path("/y"),
        source="registry:m",
    )
    # frozen dataclass → FrozenInstanceError (subclass of AttributeError)
    with pytest.raises(AttributeError):
        action.kind = "skip-exists"  # type: ignore[misc]
