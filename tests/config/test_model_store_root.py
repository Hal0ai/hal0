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
    def __init__(self, effective: str) -> None:
        self._effective = effective

    def effective_store(self) -> str:
        return self._effective


class _Cfg:
    def __init__(self, effective: str) -> None:
        self.models = _Models(effective)


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
