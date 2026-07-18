"""hal0.config.store — the unified model-store resolver (ML-3).

Covers: resolver precedence (env / effective_store / models_dir fallback,
read == write agreement), assert_under_store's SPLIT severity (fail-fast
vs warn), the repo/revision layout derivation, the by-id pointer, and NFS
relabel omission (mocked statfs via /proc/mounts).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import loader, store

#: Captured at import time — BEFORE tests/conftest.py's suite-wide
#: `_store_not_nfs_by_default` autouse fixture patches the `is_nfs_path`
#: NAME on this module. The two tests that need the genuine /proc/mounts
#: parsing logic restore this real function first.
_real_is_nfs_path = store.is_nfs_path


class _Models:
    def __init__(self, effective: str) -> None:
        self._effective = effective

    def effective_store(self) -> str:
        return self._effective


class _Cfg:
    def __init__(self, effective: str) -> None:
        self.models = _Models(effective)


class TestStoreRootPrecedence:
    def test_env_wins_over_config(self, monkeypatch) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", "/srv/ggufs")
        monkeypatch.setattr(loader, "load_hal0_config", lambda: _Cfg("/data/models"))
        assert store.store_root() == Path("/srv/ggufs")

    def test_effective_store_used_when_no_env(self, monkeypatch) -> None:
        monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)
        monkeypatch.setattr(loader, "load_hal0_config", lambda: _Cfg("/data/models"))
        assert store.store_root() == Path("/data/models")

    def test_default_is_models_dir_not_mnt_ai_models(self, monkeypatch, tmp_path) -> None:
        """The core fix: the old reader defaulted to /mnt/ai-models while the
        writer defaulted to models_dir() — that divergence was the "🔴
        dual-resolver store trap". Both now fall back to the SAME default."""
        monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)
        monkeypatch.setattr(loader, "load_hal0_config", lambda: _Cfg(""))
        monkeypatch.setenv("HAL0_HOME", str(tmp_path))
        from hal0.config import paths

        assert store.store_root() == paths.models_dir()
        assert store.store_root() != Path("/mnt/ai-models")

    def test_default_on_config_load_failure(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)
        monkeypatch.setenv("HAL0_HOME", str(tmp_path))

        def _boom():
            raise RuntimeError("no config yet")

        monkeypatch.setattr(loader, "load_hal0_config", _boom)
        from hal0.config import paths

        assert store.store_root() == paths.models_dir()

    def test_read_equals_write(self, monkeypatch) -> None:
        """The whole point of ML-3: paths.model_store_root() (the historic
        READ side) and store.store_root() (the WRITE side) must always
        agree — there is only one resolver now."""
        monkeypatch.setenv("HAL0_MODEL_STORE", "/srv/ggufs")
        from hal0.config import paths

        assert paths.model_store_root() == str(store.store_root())


class TestAssertUnderStore:
    def test_fail_severity_raises_on_escape(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path / "store"))
        with pytest.raises(store.StorePathEscape):
            store.assert_under_store(tmp_path / "elsewhere" / "file.gguf", severity="fail")

    def test_fail_is_the_default_severity(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path / "store"))
        with pytest.raises(store.StorePathEscape):
            store.assert_under_store(tmp_path / "elsewhere" / "file.gguf")

    def test_warn_severity_never_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path / "store"))
        escaping = tmp_path / "elsewhere" / "file.gguf"
        result = store.assert_under_store(escaping, severity="warn")
        assert result == escaping.resolve()

    def test_path_under_store_passes_both_severities(self, monkeypatch, tmp_path) -> None:
        root = tmp_path / "store"
        root.mkdir()
        monkeypatch.setenv("HAL0_MODEL_STORE", str(root))
        inside = root / "models--org--repo" / "snapshots" / "abc123" / "model.gguf"
        assert store.assert_under_store(inside, severity="fail") == inside.resolve()
        assert store.assert_under_store(inside, severity="warn") == inside.resolve()


class TestLayoutDerivation:
    def test_repo_dirname_hf_cache_shape(self) -> None:
        assert store.repo_dirname("Qwen/Qwen3-4B-GGUF") == "models--Qwen--Qwen3-4B-GGUF"

    def test_model_dir_and_file_dest(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        d = store.model_dir("org/repo", "deadbeef")
        assert d == tmp_path / "models--org--repo" / "snapshots" / "deadbeef"
        dest = store.file_dest("org/repo", "deadbeef", "model-00001-of-00002.gguf")
        assert dest == d / "model-00001-of-00002.gguf"

    def test_file_dest_rejects_path_escape_in_rel(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        # model_dir() is 3 components below the store root
        # (models--org--repo/snapshots/deadbeef) — 4 "../" is needed to
        # actually land OUTSIDE the root (3 would only cancel back to root).
        with pytest.raises(store.StorePathEscape):
            store.file_dest("org/repo", "deadbeef", "../../../../etc/passwd")


class TestByIdPointer:
    def test_set_and_resolve_pointer(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        target = tmp_path / "models--org--repo" / "snapshots" / "rev1" / "model.gguf"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x")
        store.set_entry_pointer("my-model", target)
        assert store.resolve_entry_pointer("my-model") == target.resolve()

    def test_pointer_flip_is_atomic_across_revisions(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        rev1 = tmp_path / "models--org--repo" / "snapshots" / "rev1" / "model.gguf"
        rev1.parent.mkdir(parents=True)
        rev1.write_bytes(b"v1")
        rev2 = tmp_path / "models--org--repo" / "snapshots" / "rev2" / "model.gguf"
        rev2.parent.mkdir(parents=True)
        rev2.write_bytes(b"v2")

        store.set_entry_pointer("my-model", rev1)
        assert store.resolve_entry_pointer("my-model") == rev1.resolve()
        store.set_entry_pointer("my-model", rev2)
        assert store.resolve_entry_pointer("my-model") == rev2.resolve()

    def test_resolve_missing_pointer_is_none(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        assert store.resolve_entry_pointer("never-registered") is None


class TestMountFor:
    def test_local_fs_keeps_selinux_relabel(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(store, "is_nfs_path", lambda p: False)
        m = store.mount_for(str(tmp_path), read_only=True)
        assert m.selinux == "z"
        assert m.read_only is True
        assert m.source == m.target == str(tmp_path)

    def test_nfs_omits_selinux_relabel(self, monkeypatch, tmp_path) -> None:
        """plan §23.3d: :z/:Z relabel fails on NFS (chcon ENOTSUP) — omit the
        suffix entirely rather than swapping z->Z (both relabel)."""
        monkeypatch.setattr(store, "is_nfs_path", lambda p: True)
        m = store.mount_for(str(tmp_path), read_only=True)
        assert m.selinux == ""
        assert ":z" not in m.render()
        assert ":Z" not in m.render()

    def test_is_nfs_path_reads_proc_mounts_fstype(self, monkeypatch, tmp_path) -> None:
        """Exercises the REAL `is_nfs_path` (bypassing the suite-wide
        `_store_not_nfs_by_default` autouse fixture in tests/conftest.py,
        which patches the `is_nfs_path` NAME itself — restore the genuine
        function, captured at module-import time, so this test proves the
        /proc/mounts parsing logic rather than the autouse stub)."""
        monkeypatch.setattr(store, "is_nfs_path", _real_is_nfs_path)
        fake_mounts = f"tmpfs / tmpfs rw 0 0\n10.0.0.1:/export {tmp_path} nfs4 rw 0 0\n"
        monkeypatch.setattr("builtins.open", _fake_open_factory(fake_mounts))
        assert store.is_nfs_path(tmp_path) is True

    def test_is_nfs_path_false_for_local_fs(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(store, "is_nfs_path", _real_is_nfs_path)
        fake_mounts = f"tmpfs / tmpfs rw 0 0\n/dev/sda1 {tmp_path} ext4 rw 0 0\n"
        monkeypatch.setattr("builtins.open", _fake_open_factory(fake_mounts))
        assert store.is_nfs_path(tmp_path) is False


def _fake_open_factory(content: str):
    import io

    real_open = open

    def _fake_open(path, *args, **kwargs):
        if str(path) == "/proc/mounts":
            return io.StringIO(content)
        return real_open(path, *args, **kwargs)

    return _fake_open
