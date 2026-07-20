"""model_store_root() — thin shim over hal0.config.store.store_root().

Precedence (ML-3, unified resolver): HAL0_MODEL_STORE env >
[models].effective_store() (store or pull_root) > paths.models_dir().
This is what makes a custom model directory actually reach slot
containers (providers mount this, the registry/pull engine resolves the
same store) — read and write now agree by construction.
"""

from __future__ import annotations

from hal0.config import loader, paths


class _Models:
    def __init__(self, effective: str, pull_root: str = "") -> None:
        self._effective = effective
        self.pull_root = pull_root

    def effective_store(self) -> str:
        return self._effective


class _Cfg:
    def __init__(self, effective: str, pull_root: str = "") -> None:
        self.models = _Models(effective, pull_root)


def test_env_var_wins(monkeypatch) -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", "/srv/ggufs")
    # config store is ignored when the env override is present
    monkeypatch.setattr(loader, "load_hal0_config", lambda: _Cfg("/data/models"))
    assert paths.model_store_root() == "/srv/ggufs"


def test_config_store_used_when_no_env(monkeypatch) -> None:
    monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)
    monkeypatch.setattr(loader, "load_hal0_config", lambda: _Cfg("/home/cuken/ai/models"))
    assert paths.model_store_root() == "/home/cuken/ai/models"


def test_default_when_store_empty(monkeypatch) -> None:
    monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)
    monkeypatch.setattr(loader, "load_hal0_config", lambda: _Cfg(""))
    # ML-3: default now aligns with the write side (paths.models_dir()),
    # not the old /mnt/ai-models mount-only fallback.
    assert paths.model_store_root() == str(paths.models_dir())


def test_default_when_config_unreadable(monkeypatch) -> None:
    monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)

    def _boom() -> object:
        raise RuntimeError("no config on a fresh box")

    monkeypatch.setattr(loader, "load_hal0_config", _boom)
    assert paths.model_store_root() == str(paths.models_dir())


def test_env_is_stripped(monkeypatch) -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", "  /srv/ggufs  ")
    assert paths.model_store_root() == "/srv/ggufs"


# ── model_mount_roots() — O25: mount store AND pull_root, deduped ──────────


def test_mount_roots_distinct_store_and_pull_root(monkeypatch) -> None:
    """store != pull_root → BOTH roots mount so a model file under pull_root
    (the external /mnt/ai-models tree) is reachable in-container (O25)."""
    monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)
    monkeypatch.setattr(
        loader,
        "load_hal0_config",
        lambda: _Cfg("/var/lib/hal0/models", pull_root="/mnt/ai-models"),
    )
    assert paths.model_mount_roots() == ["/var/lib/hal0/models", "/mnt/ai-models"]


def test_mount_roots_dedup_when_store_equals_pull_root(monkeypatch) -> None:
    """store == pull_root → exactly ONE mount (no duplicate Volume)."""
    monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)
    monkeypatch.setattr(
        loader,
        "load_hal0_config",
        lambda: _Cfg("/mnt/ai-models", pull_root="/mnt/ai-models"),
    )
    assert paths.model_mount_roots() == ["/mnt/ai-models"]


def test_mount_roots_dedup_nested(monkeypatch) -> None:
    """A root nested under another collapses to the covering ancestor."""
    monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)
    monkeypatch.setattr(
        loader,
        "load_hal0_config",
        lambda: _Cfg("/mnt/ai-models/sub", pull_root="/mnt/ai-models"),
    )
    assert paths.model_mount_roots() == ["/mnt/ai-models"]


def test_mount_roots_trailing_slash_not_double_dropped(monkeypatch) -> None:
    """normpath collapses "/mnt/ai-models/" == "/mnt/ai-models" to one entry
    (a mutual-cover tie must not drop both)."""
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models/")
    monkeypatch.setattr(
        loader,
        "load_hal0_config",
        lambda: _Cfg("ignored", pull_root="/mnt/ai-models"),
    )
    assert paths.model_mount_roots() == ["/mnt/ai-models"]
