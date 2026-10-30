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


def test_config_list_keys_default_is_shared() -> None:
    keys = hp._config_list_keys(
        terminal_enabled=False, existing_disabled=[], mcp_servers=hp._default_mcp_servers()
    )
    for name in ("hal0-admin", "hal0-memory"):
        assert keys["mcp_servers"][name]["headers"]["X-hal0-Private"] == "0"


def _run_config_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: dict, overrides: Path | None = None
) -> dict:
    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    state = hp.BootstrapState(hermes_home=str(home))
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_k: {"model": "m", "base_url": _HAL0_V1, "context_length": 32768},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", overrides or tmp_path / "no-overrides.yaml")
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


# ── one opt-in rule: absent/null/false/"false|no|0|off" shared, else private ──


@pytest.mark.parametrize(
    ("value", "private"),
    [
        (True, True),
        (False, False),
        (None, False),
        ("true", True),
        ("yes", True),
        ("1", True),
        (1, True),
        ("false", False),
        (" No ", False),
        ("0", False),
        (0, False),
        ("OFF", False),
        ("garbage", True),
        ([], True),
    ],
)
def test_profile_memory_private_rule(value, private) -> None:
    assert hp.profile_memory_private({"hal0": {"memory_private": value}}) is private


@pytest.mark.parametrize("cfg", [{}, {"hal0": None}, {"hal0": {}}, {"hal0": "x"}, None])
def test_profile_memory_private_absent_is_shared(cfg) -> None:
    assert hp.profile_memory_private(cfg) is False


@pytest.mark.parametrize(
    ("value", "boolean"),
    [(True, True), (False, True), (None, True), ("true", False), (1, False), ("off", False)],
)
def test_memory_private_value_is_boolean(value, boolean) -> None:
    assert hp.memory_private_is_boolean({"hal0": {"memory_private": value}}) is boolean
    assert hp.memory_private_is_boolean({}) is True


# ── convergence reads the MERGED result, and honours wire_mcp: false ─────────


def test_update_honours_an_opt_in_that_only_overrides_yaml_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One pass: the opt-in comes from overrides.yaml, not the pre-merge file."""
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(yaml.safe_dump({"hal0": {"memory_private": True}}))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"mcp_servers": _servers("0", "0")}))
    assert hp.apply_release_config_invariants(tmp_path, overrides_path=overrides) is True
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert _privates(cfg) == ("1", "1")
    assert hp.apply_release_config_invariants(tmp_path, overrides_path=overrides) is False


def test_an_explicit_header_in_overrides_still_wins(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        yaml.safe_dump({"mcp_servers": {"hal0-memory": {"headers": {"X-hal0-Private": "1"}}}})
    )
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"mcp_servers": _servers("0", "0")}))
    hp.apply_release_config_invariants(tmp_path, overrides_path=overrides)
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert _privates(cfg) == ("0", "1")


def test_reprovision_honours_an_opt_in_that_only_overrides_yaml_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "overrides.yaml").write_text(yaml.safe_dump({"hal0": {"memory_private": True}}))
    cfg = _run_config_write(
        tmp_path,
        monkeypatch,
        {"mcp_servers": _servers("0", "0")},
        overrides=tmp_path / "overrides.yaml",
    )
    assert _privates(cfg) == ("1", "1")


def test_update_leaves_the_header_alone_when_the_root_opted_out_of_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"hal0": {"wire_mcp": False}, "mcp_servers": _servers("1", "1")})
    )
    hp.apply_release_config_invariants(tmp_path)
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert _privates(cfg) == ("1", "1")


def test_reprovision_leaves_the_header_alone_when_the_root_opted_out_of_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _run_config_write(
        tmp_path,
        monkeypatch,
        {"hal0": {"wire_mcp": False}, "mcp_servers": _servers("1", "1")},
    )
    assert _privates(cfg) == ("1", "1")
