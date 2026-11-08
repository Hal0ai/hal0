"""SlotManager.effective_max_inflight — ADR-0056 admission cap resolution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hal0.slots.manager import SlotManager


def _mgr(tmp_path: Path, files: dict[str, str]) -> SimpleNamespace:
    for name, body in files.items():
        (tmp_path / f"{name}.toml").write_text(body)
    fake = SimpleNamespace(
        _cfg_cache={},
        _resolve_alias=lambda n: n,
        _key=lambda n: n,
        _config_file_for=lambda n: tmp_path / f"{n}.toml",
    )
    fake.effective_max_inflight = SlotManager.effective_max_inflight.__get__(fake)
    return fake


def test_flm_profile_defaults_to_one_on_cold_cache(tmp_path: Path) -> None:
    m = _mgr(tmp_path, {"flm": 'name="flm"\nprofile="flm"\ndevice="npu"\n'})
    assert m.effective_max_inflight("flm") == 1


def test_flm_provider_key_defaults_to_one(tmp_path: Path) -> None:
    m = _mgr(tmp_path, {"npu": 'name="npu"\nprovider="flm"\n'})
    assert m.effective_max_inflight("npu") == 1


def test_explicit_value_wins_and_zero_uncaps(tmp_path: Path) -> None:
    m = _mgr(
        tmp_path,
        {
            "a": 'profile="flm"\nmax_inflight=3\n',
            "b": 'profile="flm"\nmax_inflight=0\n',
        },
    )
    assert m.effective_max_inflight("a") == 3
    assert m.effective_max_inflight("b") is None


def test_non_flm_slot_stays_uncapped(tmp_path: Path) -> None:
    m = _mgr(tmp_path, {"primary": 'profile="hal0-rocm"\ndevice="gpu"\n'})
    assert m.effective_max_inflight("primary") is None


def test_cached_config_preferred_over_disk(tmp_path: Path) -> None:
    m = _mgr(tmp_path, {"flm": 'profile="flm"\n'})
    m._cfg_cache["flm"] = (0, 0, {"profile": "flm", "max_inflight": 2})
    assert m.effective_max_inflight("flm") == 2


def test_missing_or_broken_config_is_uncapped(tmp_path: Path) -> None:
    m = _mgr(tmp_path, {"bad": "this is = = not toml"})
    assert m.effective_max_inflight("bad") is None
    assert m.effective_max_inflight("nope") is None


def test_xdna_runner_defaults_to_one(tmp_path: Path) -> None:
    """ADR-0057: the xdna runner launches llama-server with ``-np 1``, so it is
    a serial backend exactly like FLM and gets the same default cap."""
    m = _mgr(tmp_path, {"npu2": 'name="npu2"\nbinary="xdna"\ndevice="npu"\n'})
    assert m.effective_max_inflight("npu2") == 1


def test_xdna_explicit_value_still_wins(tmp_path: Path) -> None:
    m = _mgr(tmp_path, {"npu2": 'binary="xdna"\nmax_inflight=0\n'})
    assert m.effective_max_inflight("npu2") is None
