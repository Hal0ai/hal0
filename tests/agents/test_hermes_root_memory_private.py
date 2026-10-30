"""The default (root) Hermes profile honours ``hal0: {memory_private: true}``.

Named profiles read the opt-in through :func:`hp.profile_memory_private`; the
root config.yaml has to as well, on both paths that write it: the full overlay
(``reprovision``) and the release activation ``hal0 update`` runs, which never
reaches the overlay. Shared is the default; "1" only follows the opt-in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp

from ._hermes_fakes import fake_hermes_run

yaml = pytest.importorskip("yaml")

_HAL0_V1 = "http://127.0.0.1:8080/v1"


def _servers(private_admin: str = "0", private_memory: str = "0") -> dict:
    return {
        "hal0-admin": {
            "type": "http",
            "url": "http://127.0.0.1:8080/mcp/admin/mcp",
            "headers": {"X-hal0-Agent": "hermes", "X-hal0-Private": private_admin},
        },
        "hal0-memory": {
            "type": "http",
            "url": "http://127.0.0.1:8080/mcp/memory/mcp",
            "headers": {"X-hal0-Agent": "hermes", "X-hal0-Private": private_memory},
        },
    }


def _privates(cfg: dict) -> tuple[str, str]:
    s = cfg["mcp_servers"]
    return (
        s["hal0-admin"]["headers"]["X-hal0-Private"],
        s["hal0-memory"]["headers"]["X-hal0-Private"],
    )


def test_config_list_keys_memory_private_covers_admin_and_memory() -> None:
    keys = hp._config_list_keys(
        terminal_enabled=False,
        existing_disabled=[],
        mcp_servers=hp._default_mcp_servers(),
        memory_private=True,
    )
    for name in ("hal0-admin", "hal0-memory"):
        assert keys["mcp_servers"][name]["headers"]["X-hal0-Private"] == "1"


def test_config_list_keys_default_is_shared() -> None:
    keys = hp._config_list_keys(
        terminal_enabled=False, existing_disabled=[], mcp_servers=hp._default_mcp_servers()
    )
    for name in ("hal0-admin", "hal0-memory"):
        assert keys["mcp_servers"][name]["headers"]["X-hal0-Private"] == "0"


def _run_config_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: dict) -> dict:
    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    state = hp.BootstrapState(hermes_home=str(home))
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_k: {"model": "m", "base_url": _HAL0_V1, "context_length": 32768},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-overrides.yaml")
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", tmp_path / "seed" / "hermes.toml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.InstallIO(
        fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run()
    )
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    return yaml.safe_load((home / "config.yaml").read_text())


def test_reprovision_keeps_a_root_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _run_config_write(
        tmp_path,
        monkeypatch,
        {"hal0": {"memory_private": True}, "mcp_servers": _servers("1", "1")},
    )
    assert _privates(cfg) == ("1", "1")


def test_reprovision_flips_a_root_without_opt_in_to_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _run_config_write(tmp_path, monkeypatch, {"mcp_servers": _servers("1", "1")})
    assert _privates(cfg) == ("0", "0")


def test_update_converges_the_root_header_to_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"mcp_servers": _servers("1", "1")}))
    assert hp.apply_release_config_invariants(tmp_path) is True
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert _privates(cfg) == ("0", "0")
    assert hp.apply_release_config_invariants(tmp_path) is False


def test_update_keeps_the_root_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"hal0": {"memory_private": True}, "mcp_servers": _servers("0", "0")})
    )
    hp.apply_release_config_invariants(tmp_path)
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert _privates(cfg) == ("1", "1")


def test_update_does_not_invent_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"agent": {"system_prompt": "P"}}))
    hp.apply_release_config_invariants(tmp_path)
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert "mcp_servers" not in cfg


def test_profile_admin_server_carries_the_private_header() -> None:
    shared = hp._build_profile_mcp_servers("hermes__a")
    assert shared["hal0-admin"]["headers"]["X-hal0-Private"] == "0"
    private = hp._build_profile_mcp_servers("hermes__a", private=True)
    assert private["hal0-admin"]["headers"]["X-hal0-Private"] == "1"
    assert private["hal0-memory"]["headers"]["X-hal0-Private"] == "1"
