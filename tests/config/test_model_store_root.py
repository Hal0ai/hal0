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


# ── model_asset_dir / model_asset_dirs — O26c: resolve assets across roots ──


def _cfg_roots(monkeypatch, store: str, pull_root: str = "") -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", store)
    monkeypatch.setattr(loader, "load_hal0_config", lambda: _Cfg("ignored", pull_root=pull_root))


def test_asset_dir_found_under_pull_root_when_store_empty(monkeypatch, tmp_path) -> None:
    """store is default/empty, the asset tree lives under pull_root → resolved
    to the pull_root dir (the O26c regression: reverting store blanked it)."""
    store = tmp_path / "store"
    pull = tmp_path / "pull"
    (pull / "chat-templates").mkdir(parents=True)
    _cfg_roots(monkeypatch, str(store), str(pull))
    assert paths.model_asset_dir("chat-templates") == pull / "chat-templates"


def test_asset_dir_prefers_store_then_falls_back_to_store_for_writes(monkeypatch, tmp_path) -> None:
    store = tmp_path / "store"
    pull = tmp_path / "pull"
    (store / "chat-templates").mkdir(parents=True)
    (pull / "chat-templates").mkdir(parents=True)
    _cfg_roots(monkeypatch, str(store), str(pull))
    # store is first in model_mount_roots → wins on a cross-root presence tie.
    assert paths.model_asset_dir("chat-templates") == store / "chat-templates"
    # nothing exists → the canonical store dir is returned (write target).
    assert paths.model_asset_dir("comfyui/workflows") == store / "comfyui" / "workflows"


def test_asset_dirs_unions_existing_roots(monkeypatch, tmp_path) -> None:
    store = tmp_path / "store"
    pull = tmp_path / "pull"
    (store / "chat-templates").mkdir(parents=True)
    (pull / "chat-templates").mkdir(parents=True)
    _cfg_roots(monkeypatch, str(store), str(pull))
    assert paths.model_asset_dirs("chat-templates") == [
        store / "chat-templates",
        pull / "chat-templates",
    ]


def test_asset_dirs_dedup_when_store_equals_pull_root(monkeypatch, tmp_path) -> None:
    root = tmp_path / "ai-models"
    (root / "chat-templates").mkdir(parents=True)
    _cfg_roots(monkeypatch, str(root), str(root))
    # store == pull_root → model_mount_roots collapses to one → no double.
    assert paths.model_asset_dirs("chat-templates") == [root / "chat-templates"]


def test_asset_dirs_empty_when_no_root_has_subdir(monkeypatch, tmp_path) -> None:
    store = tmp_path / "store"
    pull = tmp_path / "pull"
    store.mkdir()
    pull.mkdir()
    _cfg_roots(monkeypatch, str(store), str(pull))
    assert paths.model_asset_dirs("chat-templates") == []
