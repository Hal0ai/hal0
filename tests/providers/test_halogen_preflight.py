"""``HalogenProvider.preflight`` and ``gtt_leak_guard``: what a load refuses before any unit exists.

The generic seam (``ContainerProvider.load_sync``) hands the hook the host's
KFD gfx targets; halogen's rules are gfx1151 only, a checkpoint on disk and the
tokenizer beside it. ``kfd_gfx_target_versions`` returns integers (``110501`` is
gfx1151; a CPU node reports ``0`` and is in the list), and ``[]`` means the
topology could not be read, which is "cannot tell", never a refusal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hal0.errors import Hal0Error
from hal0.providers import halogen as halogen_module
from hal0.providers.container import ContainerProvider, _spec_provider_for
from hal0.providers.halogen import HalogenProvider
from hal0.slots.state import SlotConfigError, SlotHostUnsupported

GFX1151 = 110501
CKPT_NAME = "qwen38-flash-next-ht43.hgn"


@pytest.fixture(autouse=True)
def _isolated(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Seeded ``halogen`` profile from an isolated home; no cache dir is ever created."""
    calls: list[str] = []
    monkeypatch.setattr(halogen_module, "ensure_shared_dir", lambda path: calls.append(str(path)))
    return calls


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A complete checkpoint directory: the checkpoint and its tokenizer."""
    (tmp_path / CKPT_NAME).write_bytes(b"HGN1")
    (tmp_path / "tokenizer").mkdir()
    (tmp_path / "tokenizer" / "tokenizer.json").write_text("{}")
    return tmp_path


def _cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "flash",
        "port": 8099,
        "device": "gpu-rocm",
        "type": "llm",
        "runtime": "container",
        "profile": "halogen",
        "model": {"default": "qwen3.8-flash-next-ht43"},
    }
    base.update(overrides)
    return base


def _info(store: Path, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_model_key": "qwen3.8-flash-next-ht43",
        "path": str(store / CKPT_NAME),
        "backends": ["halogen"],
        "provider": "halogen",
    }
    base.update(overrides)
    return base


def _preflight(store: Path, gfx: list[Any], **info: Any) -> None:
    HalogenProvider().preflight(_cfg(), _info(store, **info), gfx_versions=gfx)


# ── the leak guard flag ─────────────────────────────────────────────────────


def test_halogen_asks_for_the_gtt_leak_check() -> None:
    assert HalogenProvider.gtt_leak_guard is True
    # the provider the manager actually resolves for a halogen slot
    assert getattr(_spec_provider_for(_cfg()), "gtt_leak_guard", False) is True


# ── the host ────────────────────────────────────────────────────────────────


def test_an_unreadable_topology_skips_the_host_check(store: Path) -> None:
    _preflight(store, [])


@pytest.mark.parametrize(
    "gfx",
    [
        pytest.param([GFX1151], id="the-igpu"),
        pytest.param([0, GFX1151], id="cpu-node-then-igpu"),
        pytest.param([str(GFX1151)], id="as-text"),
    ],
)
def test_gfx1151_passes(store: Path, gfx: list[Any]) -> None:
    _preflight(store, gfx)


@pytest.mark.parametrize(
    "gfx",
    [
        pytest.param([110000], id="gfx1100"),
        pytest.param([0, 110000], id="cpu-node-and-gfx1100"),
        pytest.param([0], id="cpu-node-only"),
        pytest.param(["110000"], id="as-text"),
    ],
)
def test_another_gfx_target_is_refused(store: Path, gfx: list[Any]) -> None:
    with pytest.raises(SlotHostUnsupported) as exc:
        _preflight(store, gfx)
    assert exc.value.code == "slot.host_unsupported"
    assert exc.value.status == 409
    assert "gfx1151" in str(exc.value)
    assert exc.value.details["slot"] == "flash"


def test_the_refusal_names_the_target_the_host_reported(store: Path) -> None:
    with pytest.raises(SlotHostUnsupported) as exc:
        _preflight(store, [0, 110000])
    assert "gfx1100" in str(exc.value)


def test_a_guest_with_only_cpu_nodes_is_told_no_gfx1151_gpu_is_visible(store: Path) -> None:
    """An LXC guest with no GPU passthrough reports just this: say what that means."""
    with pytest.raises(SlotHostUnsupported) as exc:
        _preflight(store, [0])
    assert "no gfx1151 GPU is visible to this guest" in str(exc.value)


def test_the_refusal_is_a_slot_config_error() -> None:
    """Callers that already handle a refused slot config keep handling this one."""
    assert issubclass(SlotHostUnsupported, SlotConfigError)


def test_the_host_is_judged_before_the_files(tmp_path: Path) -> None:
    """On the wrong host a missing file is not the news: the host is."""
    info = _info(tmp_path)  # nothing on disk
    with pytest.raises(SlotHostUnsupported):
        HalogenProvider().preflight(_cfg(), info, gfx_versions=[110000])


# ── the files ───────────────────────────────────────────────────────────────


def test_a_missing_checkpoint_is_refused_naming_the_path(tmp_path: Path) -> None:
    info = _info(tmp_path)
    with pytest.raises(Hal0Error) as exc:
        HalogenProvider().preflight(_cfg(), info, gfx_versions=[GFX1151])
    assert exc.value.code == "slot.model_missing"
    assert info["path"] in str(exc.value)


def test_a_missing_checkpoint_is_refused_even_when_the_host_cannot_be_read(
    tmp_path: Path,
) -> None:
    with pytest.raises(Hal0Error) as exc:
        HalogenProvider().preflight(_cfg(), _info(tmp_path), gfx_versions=[])
    assert exc.value.code == "slot.model_missing"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"NOPE" + b"\x00" * 60, id="garbage"),
        pytest.param(b"HG", id="truncated"),
        pytest.param(b"", id="empty"),
    ],
)
def test_a_file_without_the_magic_is_refused_naming_the_path(store: Path, content: bytes) -> None:
    """The engine's entrypoint exits on it, so it must not get as far as evicting for it."""
    (store / CKPT_NAME).write_bytes(content)
    with pytest.raises(Hal0Error) as exc:
        _preflight(store, [GFX1151])
    assert exc.value.code == "slot.model_missing"
    assert str(store / CKPT_NAME) in str(exc.value)
    assert "HGN1" in str(exc.value)
    assert exc.value.details["path"] == str(store / CKPT_NAME)


def test_a_checkpoint_this_account_cannot_open_is_refused_naming_the_path(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``is_file`` answers from the directory, so a file with the wrong mode gets this far."""

    def denied(path: Path, *_a: Any, **_k: Any) -> Any:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(Hal0Error) as exc:
        _preflight(store, [GFX1151])
    assert exc.value.code == "slot.model_missing"
    assert str(store / CKPT_NAME) in str(exc.value)


def test_a_bad_magic_on_the_wrong_host_is_still_about_the_host(store: Path) -> None:
    (store / CKPT_NAME).write_bytes(b"NOPE")
    with pytest.raises(SlotHostUnsupported):
        _preflight(store, [110000])


def test_a_missing_tokenizer_is_refused_naming_the_path(store: Path) -> None:
    (store / "tokenizer" / "tokenizer.json").unlink()
    with pytest.raises(Hal0Error) as exc:
        _preflight(store, [GFX1151])
    assert exc.value.code == "slot.model_missing"
    assert str(store / "tokenizer" / "tokenizer.json") in str(exc.value)


def test_a_model_row_with_no_path_is_refused(store: Path) -> None:
    with pytest.raises(Hal0Error) as exc:
        _preflight(store, [GFX1151], path="")
    assert exc.value.code == "slot.model_missing"


def test_a_directory_is_not_a_checkpoint(store: Path) -> None:
    (store / "dir.hgn").mkdir()
    with pytest.raises(Hal0Error) as exc:
        _preflight(store, [GFX1151], path=str(store / "dir.hgn"))
    assert exc.value.code == "slot.model_missing"


def _unreadable(*names: str) -> Any:
    """``Path.is_file`` as it behaves under a parent directory the service cannot read."""
    real = Path.is_file

    def is_file(path: Path, **kwargs: Any) -> bool:
        if path.name in names:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, **kwargs)

    return is_file


def test_a_checkpoint_under_an_unreadable_parent_is_refused_naming_the_path(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "is_file", _unreadable(CKPT_NAME))
    with pytest.raises(Hal0Error) as exc:
        _preflight(store, [GFX1151])
    assert exc.value.code == "slot.model_missing"
    assert str(store / CKPT_NAME) in str(exc.value)
    assert exc.value.details["path"] == str(store / CKPT_NAME)


def test_a_tokenizer_under_an_unreadable_parent_is_refused_naming_the_path(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "is_file", _unreadable("tokenizer.json"))
    with pytest.raises(Hal0Error) as exc:
        _preflight(store, [GFX1151])
    assert exc.value.code == "slot.model_missing"
    assert str(store / "tokenizer" / "tokenizer.json") in str(exc.value)


def test_preflight_never_touches_the_engine(store: Path) -> None:
    """Files and a sysfs list only: no socket is opened."""
    with patch("socket.socket", side_effect=AssertionError("network")):
        _preflight(store, [GFX1151])


# ── at the load seam ────────────────────────────────────────────────────────


def test_load_refuses_the_wrong_host_before_any_unit_is_written(
    store: Path, _isolated: list[str]
) -> None:
    provider = ContainerProvider()
    with (
        patch("hal0.providers.container._SYSTEMCTL_SEAM") as seam,
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch("hal0.providers.container.kfd_gfx_target_versions", return_value=[0, 110000]),
        pytest.raises(SlotHostUnsupported),
    ):
        provider.load_sync(_cfg(), _info(store))
    seam.write_quadlet.assert_not_called()
    seam.systemctl.assert_not_called()
    assert _isolated == []  # prepare_load (the cache dir) is not reached either


def test_load_refuses_a_missing_tokenizer_before_any_unit_is_written(
    store: Path,
) -> None:
    (store / "tokenizer" / "tokenizer.json").unlink()
    provider = ContainerProvider()
    with (
        patch("hal0.providers.container._SYSTEMCTL_SEAM") as seam,
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch("hal0.providers.container.kfd_gfx_target_versions", return_value=[GFX1151]),
        pytest.raises(Hal0Error) as exc,
    ):
        provider.load_sync(_cfg(), _info(store))
    assert exc.value.code == "slot.model_missing"
    seam.write_quadlet.assert_not_called()
    seam.systemctl.assert_not_called()


def test_load_refuses_a_garbage_checkpoint_before_any_unit_is_written(store: Path) -> None:
    (store / CKPT_NAME).write_bytes(b"NOPE" + b"\x00" * 60)
    provider = ContainerProvider()
    with (
        patch("hal0.providers.container._SYSTEMCTL_SEAM") as seam,
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch("hal0.providers.container.kfd_gfx_target_versions", return_value=[GFX1151]),
        pytest.raises(Hal0Error) as exc,
    ):
        provider.load_sync(_cfg(), _info(store))
    assert exc.value.code == "slot.model_missing"
    seam.write_quadlet.assert_not_called()
    seam.systemctl.assert_not_called()


def test_load_reads_the_hosts_targets_and_proceeds_on_gfx1151(store: Path) -> None:
    provider = ContainerProvider()
    with (
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch("hal0.providers.container.kfd_gfx_target_versions", return_value=[0, GFX1151]) as gfx,
        patch.object(provider, "_render_quadlet_text", return_value="unit"),
        patch.object(provider, "_write_and_start_unit") as start,
    ):
        provider.load_sync(_cfg(), _info(store))
    gfx.assert_called_once_with()
    start.assert_called_once()
