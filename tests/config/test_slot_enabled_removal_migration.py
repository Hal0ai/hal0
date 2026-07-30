"""One-shot migration: sweep ``enabled`` off slot TOMLs (#1369).

The pure transform is table-driven here because the interesting behaviour is
entirely "which of the five input shapes gets what" — the file walk around it
is thin. The load-bearing case is ``enabled = false`` on a slot that still has
a model bound: leaving the key would be harmless debris, but leaving the MODEL
would silently *activate* a slot the operator had switched off, which is the
one way this change could surprise someone.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.migrations.slot_enabled_removal import (
    migrate_slot_dir,
    migrate_slot_toml,
)


class TestMigrateSlotToml:
    def test_no_enabled_key_is_a_no_op(self) -> None:
        """``None`` means "nothing to write" — the caller skips the file."""
        assert migrate_slot_toml({"name": "a", "model": {"default": "m"}}) is None

    def test_enabled_true_only_drops_the_key(self) -> None:
        out = migrate_slot_toml({"name": "a", "enabled": True, "model": {"default": "m"}})
        assert out == {"name": "a", "model": {"default": "m"}}

    def test_enabled_false_without_a_model_only_drops_the_key(self) -> None:
        """The shipped-seed shape: both signals already said "off"."""
        out = migrate_slot_toml({"name": "a", "enabled": False, "model": {"default": ""}})
        assert out == {"name": "a", "model": {"default": ""}}

    def test_enabled_false_with_a_model_clears_the_model(self) -> None:
        """The operator said off; model-presence must not re-say on."""
        out = migrate_slot_toml(
            {
                "name": "a",
                "enabled": False,
                "model": {"default": "qwen3-4b", "context_size": 8192},
            }
        )
        assert out == {"name": "a", "model": {"default": "", "context_size": 8192}}

    def test_enabled_false_with_no_model_table_synthesizes_an_empty_one(self) -> None:
        out = migrate_slot_toml({"name": "a", "enabled": False})
        assert out == {"name": "a", "model": {"default": ""}}

    def test_npu_trio_shadow_keeps_its_placeholder_model(self) -> None:
        """A disabled shadow must NOT lose its placeholder model id.

        Shadows are records for the anchor's single FLM process; their model is
        structural (FLM's bundled whisper / embed-gemma), and their real gate is
        the anchor's ``[npu]`` table. Clearing it would strand the record with
        nothing to re-derive the id from.
        """
        for slot_type in ("transcription", "embedding"):
            out = migrate_slot_toml(
                {
                    "name": f"flm-{slot_type}",
                    "device": "npu",
                    "type": slot_type,
                    "enabled": False,
                    "model": {"default": "whisper-v3:turbo"},
                }
            )
            assert out is not None
            assert out["model"]["default"] == "whisper-v3:turbo"
            assert "enabled" not in out

    def test_npu_llm_anchor_is_not_treated_as_a_shadow(self) -> None:
        """``device=npu, type=llm`` is the anchor — a real operator choice."""
        out = migrate_slot_toml(
            {
                "name": "flm",
                "device": "npu",
                "type": "llm",
                "enabled": False,
                "model": {"default": "gemma3:1b"},
            }
        )
        assert out is not None
        assert out["model"]["default"] == ""

    def test_does_not_mutate_its_input(self) -> None:
        raw = {"name": "a", "enabled": False, "model": {"default": "m"}}
        migrate_slot_toml(raw)
        assert raw == {"name": "a", "enabled": False, "model": {"default": "m"}}

    def test_is_idempotent(self) -> None:
        """Second pass finds no key and reports nothing to do."""
        out = migrate_slot_toml({"name": "a", "enabled": False, "model": {"default": "m"}})
        assert out is not None
        assert migrate_slot_toml(out) is None


class TestMigrateSlotDir:
    def _write(self, root: Path, name: str, body: str) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{name}.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_rewrites_only_the_files_that_carry_the_key(self, tmp_path: Path) -> None:
        root = tmp_path / "slots"
        stale = self._write(
            root, "chat", 'name = "chat"\nport = 8081\nenabled = false\n\n[model]\ndefault = "m"\n'
        )
        clean = self._write(
            root, "coder", 'name = "coder"\nport = 8082\n\n[model]\ndefault = "c"\n'
        )
        clean_bytes = clean.read_bytes()

        migrated = migrate_slot_dir(root)

        assert migrated == ["chat"]
        assert clean.read_bytes() == clean_bytes, "a clean file must not be rewritten"
        raw = tomllib.loads(stale.read_text(encoding="utf-8"))
        assert "enabled" not in raw
        assert raw["model"]["default"] == ""
        assert raw["port"] == 8081

    def test_missing_dir_is_a_no_op(self, tmp_path: Path) -> None:
        assert migrate_slot_dir(tmp_path / "nope") == []

    def test_unparseable_file_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """Boot integration means one corrupt file must not abort the sweep."""
        root = tmp_path / "slots"
        self._write(root, "broken", "this is not = = toml\n")
        good = self._write(root, "chat", 'name = "chat"\nport = 8081\nenabled = true\n')

        assert migrate_slot_dir(root) == ["chat"]
        assert "enabled" not in tomllib.loads(good.read_text(encoding="utf-8"))

    def test_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        root = tmp_path / "slots"
        path = self._write(
            root, "chat", 'name = "chat"\nport = 8081\nenabled = false\n\n[model]\ndefault = "m"\n'
        )
        assert migrate_slot_dir(root) == ["chat"]
        after_bytes = path.read_bytes()
        assert migrate_slot_dir(root) == []
        assert path.read_bytes() == after_bytes


def test_api_boot_sweeps_a_stale_disabled_slot(tmp_hal0_home: str) -> None:
    """The sweep is boot-integrated, so an in-place upgrade fixes itself.

    It must also run BEFORE the other reconcile passes: they read
    ``model.default`` to decide what is configured, so a slot the operator had
    disabled would otherwise be treated as live for one boot.
    """
    from fastapi.testclient import TestClient

    from hal0.api import create_app

    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    path = slots_dir / "chat.toml"
    path.write_text(
        'name = "chat"\nport = 8081\ntype = "llm"\nenabled = false\n'
        '\n[model]\ndefault = "qwen3-4b"\ncontext_size = 8192\n',
        encoding="utf-8",
    )

    with TestClient(create_app()):
        pass

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "enabled" not in raw
    assert raw["model"]["default"] == ""
    assert raw["model"]["context_size"] == 8192


@pytest.mark.parametrize("name", ["agent", "brain", "flm", "utility"])
def test_shipped_seeds_need_no_migration(name: str) -> None:
    """The seeds already dropped the key, so the sweep must find nothing."""
    repo_root = Path(__file__).resolve().parents[2]
    raw = tomllib.loads(
        (repo_root / "installer" / "etc-hal0" / "slots" / f"{name}.toml").read_text(
            encoding="utf-8"
        )
    )
    assert migrate_slot_toml(raw) is None
