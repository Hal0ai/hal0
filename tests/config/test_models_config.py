"""Tests for ModelsConfig — schema defaults + absolute-path validator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import Hal0Config, ModelsConfig


class TestModelsConfigDefaults:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAL0_HOME", raising=False)
        cfg = ModelsConfig()
        assert cfg.roots == ["/var/lib/hal0/models"]
        assert cfg.auto_scan_on_start is True
        assert ".gguf" in cfg.file_extensions
        assert ".safetensors" in cfg.file_extensions

    def test_attached_to_hal0_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAL0_HOME", raising=False)
        top = Hal0Config()
        assert isinstance(top.models, ModelsConfig)
        assert top.models.roots == ["/var/lib/hal0/models"]


class TestRootsValidator:
    def test_absolute_path_accepted(self) -> None:
        cfg = ModelsConfig(roots=["/mnt/ai-models", "/var/lib/hal0/models"])
        assert cfg.roots == ["/mnt/ai-models", "/var/lib/hal0/models"]

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ModelsConfig(roots=["models"])
        msg = str(ei.value)
        assert "absolute" in msg
        assert "models" in msg

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ModelsConfig(roots=[""])
        assert "empty" in str(ei.value)

    def test_dot_relative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelsConfig(roots=["./local-models"])


class TestScanRoots:
    """scan_roots() folds the effective store/pull_root into the scanned set so
    a headless install (--models-dir → pull_root, but roots left default) still
    discovers its models. Regression for the fresh-install scan gap."""

    def test_store_folded_into_scan_roots(self) -> None:
        cfg = ModelsConfig(roots=["/var/lib/hal0/models"], store="/mnt/ai-models")
        assert cfg.scan_roots() == ["/var/lib/hal0/models", "/mnt/ai-models"]

    def test_pull_root_folded_when_store_unset(self) -> None:
        # The exact CT107 shape: --models-dir wrote pull_root, roots left default.
        cfg = ModelsConfig(roots=["/var/lib/hal0/models"], pull_root="/mnt/ai-models")
        assert "/mnt/ai-models" in cfg.scan_roots()

    def test_store_wins_over_pull_root(self) -> None:
        cfg = ModelsConfig(roots=["/a"], store="/store", pull_root="/pull")
        assert cfg.scan_roots() == ["/a", "/store"]
        assert "/pull" not in cfg.scan_roots()

    def test_no_duplicate_when_store_already_in_roots(self) -> None:
        cfg = ModelsConfig(roots=["/mnt/ai-models"], store="/mnt/ai-models")
        assert cfg.scan_roots() == ["/mnt/ai-models"]

    def test_default_config_scans_pull_root_default(self) -> None:
        # Even a bare ModelsConfig() scans somewhere (pull_root defaults to models_dir).
        roots = ModelsConfig().scan_roots()
        assert roots  # non-empty
        assert all(r for r in roots)
