"""model_store_root() — the single source of truth for the model-store mount.

Precedence: HAL0_MODEL_STORE env > [models].store config > /mnt/ai-models.
This is what makes a custom model directory actually reach slot containers
(providers mount this, the registry/pull engine resolves the same store).
"""

from __future__ import annotations

from hal0.config import loader, paths


class _Models:
    def __init__(self, store: str) -> None:
        self.store = store


class _Cfg:
    def __init__(self, store: str) -> None:
        self.models = _Models(store)


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
    assert paths.model_store_root() == paths.DEFAULT_MODEL_STORE == "/mnt/ai-models"


def test_default_when_config_unreadable(monkeypatch) -> None:
    monkeypatch.delenv("HAL0_MODEL_STORE", raising=False)

    def _boom() -> object:
        raise RuntimeError("no config on a fresh box")

    monkeypatch.setattr(loader, "load_hal0_config", _boom)
    assert paths.model_store_root() == "/mnt/ai-models"


def test_env_is_stripped(monkeypatch) -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", "  /srv/ggufs  ")
    assert paths.model_store_root() == "/srv/ggufs"
