"""``PiDriver.upgrade``: re-pin pi, redeploy hal0's own extensions, point Pi's
memory client at /api/memory/compat — without touching what the operator added.

A box with an older pi used to have only uninstall + reinstall. ``upgrade``
re-runs the installer, redeploys the two extensions and the theme, replaces
the managed ``pi-mcp-adapter`` pin, refreshes the MCP entry and the marker,
and refuses to run as a different user than the one the marker names."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.agents.manager import AgentError
from hal0.agents.pi_coder import PiDriver
from hal0.agents.pi_coder import driver as driver_mod
from hal0.memory import compat_client

_REAL_MANAGED_COMPAT_KEY = PiDriver._managed_compat_key
_REAL_KNOWN_HOMES = driver_mod._known_homes


class _FakeRunner:
    """Stands in for subprocess: records (argv, kwargs), never spawns."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))

        class _Done:
            returncode = 0

        return _Done()


def _no_key(prefer: str = "admin") -> str:
    raise OSError("no service key on this box")


@pytest.fixture
def fake_runner() -> _FakeRunner:
    return _FakeRunner()


def _driver_with_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: _FakeRunner,
    *,
    marker_home: str | None = None,
) -> PiDriver:
    """A PiDriver whose HOME and data dir live under ``tmp_path`` and whose
    marker names ``marker_home`` (default: this HOME)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    script = tmp_path / "installer" / "agents" / "pi.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(driver_mod, "installer_script_path", lambda name: script)
    # Best-effort token self-resolve must not reach for a real box key.
    monkeypatch.setattr("hal0.service_identity.service_key", _no_key)

    data_dir = tmp_path / "var_lib" / "agents" / "pi"
    driver = PiDriver(runner=runner)
    monkeypatch.setattr(driver, "_data_dir", lambda: data_dir)
    data_dir.mkdir(parents=True)
    marker = {
        "version": 1,
        "home": marker_home if marker_home is not None else str(tmp_path),
        "profiled_at": "t",
    }
    driver._profile_marker_path().write_text(json.dumps(marker), encoding="utf-8")
    return driver


def test_upgrade_reruns_installer_and_redeploys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    d._settings_file().parent.mkdir(parents=True, exist_ok=True)
    d._settings_file().write_text(
        json.dumps({"packages": ["npm:pi-mcp-adapter@2.31.0", "npm:other@1"]})
    )

    result = d.upgrade()

    # No coding-agent.json on this box: nothing to point at the endpoint.
    assert result == {"memory_compat": "skipped"}

    assert fake_runner.calls[0][0][:2] == ["bash", str(driver_mod.installer_script_path("pi"))]
    # Same env contract as install(): the data dir and API URL reach the script.
    env = fake_runner.calls[0][1]["env"]
    assert env["HAL0_AGENT_DATA_DIR"] == str(d._data_dir())
    assert "HAL0_BEARER_TOKEN" not in env
    assert any(argv == ["npm", "install", "--omit=dev"] for argv, _ in fake_runner.calls)
    assert json.loads(d._settings_file().read_text())["packages"] == [
        "npm:other@1",
        "npm:pi-mcp-adapter@5.3.0",
    ]
    assert d._provider_ext_dst().exists() and d._theme_dst().exists()
    assert d._hindsight_ext_dst().exists()
    mcp = json.loads(d._mcp_config_path().read_text())
    assert mcp["mcpServers"]["hal0-memory"]["url"].endswith("/mcp/memory/mcp")
    assert json.loads(d._profile_marker_path().read_text())["home"] == str(tmp_path)
    assert json.loads(d._profile_marker_path().read_text())["profiled_at"] != "t"


def test_upgrade_refuses_wrong_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner, marker_home="/somewhere/else")
    with pytest.raises(AgentError, match="run this as the user who installed pi") as err:
        d.upgrade()
    # The error names the way out: the home the marker holds, or a reinstall.
    assert "sudo HOME=/somewhere/else hal0 update component pi" in str(err.value)
    assert "uninstall and reinstall pi" in str(err.value)
    assert fake_runner.calls == []
    assert not d._settings_file().exists()


def test_upgrade_refuses_when_pi_was_never_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    d._profile_marker_path().unlink()
    with pytest.raises(AgentError, match="not installed"):
        d.upgrade()
    assert fake_runner.calls == []


def test_upgrade_leaves_operator_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    extra = d._pi_agent_dir() / "extensions" / "shepherd"
    extra.mkdir(parents=True)
    (extra / "index.ts").write_text("x")

    d.upgrade()

    assert (extra / "index.ts").read_text() == "x"


def test_upgrade_keeps_operator_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    """install() points pi at hal0; upgrade() only re-pins the managed package.
    A default provider, model or theme the operator switched to afterwards
    must survive."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    d._settings_file().parent.mkdir(parents=True, exist_ok=True)
    d._settings_file().write_text(
        json.dumps(
            {
                "defaultProvider": "openrouter",
                "defaultModel": "x",
                "theme": "y",
                "packages": ["npm:pi-mcp-adapter@2.31.0"],
            }
        )
    )

    d.upgrade()

    settings = json.loads(d._settings_file().read_text())
    assert settings["defaultProvider"] == "openrouter"
    assert settings["defaultModel"] == "x"
    assert settings["theme"] == "y"
    assert settings["packages"] == ["npm:pi-mcp-adapter@5.3.0"]


def test_write_pi_settings_keeps_object_form_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    """pi accepts ``{"source": ..., "skills": []}`` package entries. They are the
    operator's: kept as they are, unless the source is one hal0 manages."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    d._settings_file().parent.mkdir(parents=True, exist_ok=True)
    d._settings_file().write_text(
        json.dumps(
            {
                "packages": [
                    "npm:other@1",
                    {"source": "npm:my-package", "skills": []},
                    {"source": "npm:pi-mcp-adapter@2.31.0"},
                    {"source": "extensions/hindsight"},
                ]
            }
        )
    )

    d._write_pi_settings(defaults=False)

    assert json.loads(d._settings_file().read_text())["packages"] == [
        "npm:other@1",
        {"source": "npm:my-package", "skills": []},
        "npm:pi-mcp-adapter@5.3.0",
    ]


class _NpmFailsRunner(_FakeRunner):
    def run(self, argv, **kwargs):
        if argv[0] == "npm":
            raise subprocess.CalledProcessError(1, argv)
        return super().run(argv, **kwargs)


def test_upgrade_wraps_a_failing_hindsight_npm_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, _NpmFailsRunner())
    with pytest.raises(AgentError, match="npm install for the hindsight extension failed") as err:
        d.upgrade()
    assert "sudo hal0 update component pi" in str(err.value)
    assert isinstance(err.value.__cause__, subprocess.CalledProcessError)


@pytest.mark.parametrize(
    ("filename", "content"),
    [("settings.json", "[]"), ("mcp.json", "{not json")],
)
def test_upgrade_refuses_to_replace_an_unusable_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_runner: _FakeRunner,
    filename: str,
    content: str,
) -> None:
    """install() starts over from an unusable settings.json / mcp.json; an
    upgrade must not silently discard what the operator had in it."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    path = d._pi_agent_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

    with pytest.raises(AgentError, match="is not a JSON object; fix or remove it") as err:
        d.upgrade()

    assert str(path) in str(err.value)
    assert path.read_text() == content
    assert fake_runner.calls == []  # refused before the installer ran


# ── autoUpdate off for the Pi harness only (#3165) ─────────────────────────


def test_seeded_hindsight_config_turns_auto_update_off_for_pi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    assert not cfg.exists()

    d._write_hindsight_config()

    data = json.loads(cfg.read_text())
    assert data["harnesses"] == {"pi": {"autoUpdate": False}}
    assert "autoUpdate" not in data  # other tools sharing the file keep the default
    assert d._ensure_hindsight_auto_update_off() is False  # already off: no rewrite


def test_auto_update_off_is_added_to_existing_file_without_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    original = {
        "apiUrl": "http://other:9177",
        "apiToken": "secret",
        "banks": {"x": {"bank": "y"}},
        "harnesses": {"claude-code": {"autoUpdate": True}},
    }
    cfg.write_text(json.dumps(original))
    os.chmod(cfg, 0o640)

    assert d._ensure_hindsight_auto_update_off() is True

    data = json.loads(cfg.read_text())
    assert data["harnesses"] == {
        "claude-code": {"autoUpdate": True},
        "pi": {"autoUpdate": False},
    }
    assert {k: v for k, v in data.items() if k != "harnesses"} == {
        k: v for k, v in original.items() if k != "harnesses"
    }
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o640
    assert d._ensure_hindsight_auto_update_off() is False


@pytest.mark.parametrize(
    "operator",
    [
        {"autoUpdate": True},
        {"autoUpdate": False},
        {"harnesses": {"pi": {"autoUpdate": True}}},
        {"harnesses": {"pi": {"autoUpdate": False}}},
    ],
)
def test_auto_update_off_never_overrides_an_operator_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_runner: _FakeRunner,
    operator: dict[str, Any],
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    text = json.dumps({"apiUrl": "http://other:9177", **operator}, indent=4)
    cfg.write_text(text)

    assert d._ensure_hindsight_auto_update_off() is False

    assert cfg.read_text() == text  # byte-for-byte: not even reformatted


@pytest.mark.parametrize(
    "content", [None, "{not json", "[1, 2]", '{"harnesses": "x"}', '{"harnesses": {"pi": 3}}']
)
def test_auto_update_off_leaves_unusable_files_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_runner: _FakeRunner,
    content: str | None,
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    if content is not None:
        cfg.parent.mkdir(parents=True)
        cfg.write_text(content)

    assert d._ensure_hindsight_auto_update_off() is False

    if content is None:
        assert not cfg.exists()
    else:
        assert cfg.read_text() == content


def test_upgrade_turns_auto_update_off_in_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"apiUrl": "http://other:9177"}))

    assert d.upgrade() == {"memory_compat": "skipped"}

    data = json.loads(cfg.read_text())
    assert data["apiUrl"] == "http://other:9177"
    assert data["harnesses"] == {"pi": {"autoUpdate": False}}


# ── Pi's memory client onto /api/memory/compat (ADR-0058 decision 4b) ──────

_COMPAT = "http://127.0.0.1:8080/api/memory/compat"


@pytest.fixture(autouse=True)
def _no_real_compat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """No test reaches a real bridge store or another home. The default: the
    store cannot be asked, so no key is checked or minted. Returns the
    candidate lists the driver asked about."""
    minted: list[list[str]] = []

    def no_key(self: PiDriver, candidates: list[str]) -> dict[str, Any]:
        minted.append(list(candidates))
        return {"status": "error", "key": None, "detail": "stub"}

    monkeypatch.setattr(driver_mod, "_known_homes", lambda: [])
    monkeypatch.setattr(PiDriver, "_managed_compat_key", no_key)
    # tests/agents/conftest.py fakes euid 0 for the whole directory; the
    # shared file is written as the suite's own user unless a test says not.
    monkeypatch.setattr(compat_client, "_is_root", lambda: False)
    # No real state dir: the wiring lock is exercised by its own tests.
    monkeypatch.setattr(driver_mod._paths, "var_lib", lambda: tmp_path / "no-state-dir")
    monkeypatch.delenv("HAL0_API_URL", raising=False)
    return minted


def _key(monkeypatch: pytest.MonkeyPatch, status: str = "minted", key: str = "hal0bk_pi-coder.new"):
    monkeypatch.setattr(
        PiDriver,
        "_managed_compat_key",
        lambda self, candidates: {"status": status, "key": key},
    )


def _shared_config(d: PiDriver, data: dict[str, Any], mode: int = 0o644) -> Path:
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data))
    os.chmod(cfg, mode)
    return cfg


def test_compat_wiring_writes_only_pis_section_and_keeps_the_flat_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    """The file is shared by every harness on the box: the flat apiUrl,
    apiToken and banks, and every other harness section, are left as they are."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    original = {
        "apiUrl": "http://127.0.0.1:9177",
        "apiToken": "operator-token",
        "banks": {"x": {"bank": "y"}},
        "harnesses": {"claude-code": {"apiUrl": "http://elsewhere", "autoUpdate": True}},
    }
    cfg = _shared_config(d, original)
    _key(monkeypatch)

    assert d._wire_memory_compat() == driver_mod.COMPAT_MINTED

    data = json.loads(cfg.read_text())
    assert {k: v for k, v in data.items() if k != "harnesses"} == {
        k: v for k, v in original.items() if k != "harnesses"
    }
    assert data["harnesses"]["claude-code"] == original["harnesses"]["claude-code"]
    assert data["harnesses"]["pi"] == {
        "serverMode": "self-hosted",
        "apiUrl": _COMPAT,
        "apiToken": "hal0bk_pi-coder.new",
    }


def test_compat_wiring_follows_hal0_api_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {})
    _key(monkeypatch)
    monkeypatch.setenv("HAL0_API_URL", "http://box:9000/")

    d._wire_memory_compat()

    assert json.loads(cfg.read_text())["harnesses"]["pi"]["apiUrl"] == (
        "http://box:9000/api/memory/compat"
    )


def test_compat_wiring_narrows_the_file_to_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {}, mode=0o644)
    _key(monkeypatch)

    d._wire_memory_compat()

    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600


def test_compat_wiring_keeps_the_file_owner_and_writes_through_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    real = tmp_path / "dotfiles" / "coding-agent.json"
    real.parent.mkdir()
    real.write_text("{}")
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.symlink_to(real)
    st = os.stat(real)
    _key(monkeypatch)

    d._wire_memory_compat()

    assert cfg.is_symlink() and cfg.resolve() == real
    assert json.loads(real.read_text())["harnesses"]["pi"]["apiUrl"] == _COMPAT
    assert os.stat(real).st_uid == st.st_uid


def test_root_writes_pis_section_in_an_operator_home_as_the_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    # `sudo HOME=<operator home> hal0 update component pi`: the home is not
    # root's, so every read and write of the shared file there runs as its
    # owner, never as root.
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"flat": 1})
    dropped: list[tuple[int, int]] = []

    def run(uid: int, gid: int, fn):
        dropped.append((uid, gid))
        return fn()

    monkeypatch.setattr(compat_client, "_is_root", lambda: True)
    monkeypatch.setattr(compat_client, "_run_as_owner", run)
    _key(monkeypatch)

    assert d._wire_memory_compat() == driver_mod.COMPAT_MINTED

    st = os.stat(tmp_path)
    assert dropped and set(dropped) == {(st.st_uid, st.st_gid)}
    assert json.loads(cfg.read_text())["harnesses"]["pi"]["apiToken"] == "hal0bk_pi-coder.new"
    assert os.stat(cfg).st_uid == st.st_uid


def test_a_key_the_store_verifies_is_kept_and_nothing_is_minted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.old"}}})
    asked: list[list[str]] = []

    def verify(self: PiDriver, candidates: list[str]) -> dict[str, Any]:
        asked.append(list(candidates))
        return {"status": "valid", "index": 0, "key": None}

    monkeypatch.setattr(PiDriver, "_managed_compat_key", verify)

    assert d._wire_memory_compat() == driver_mod.COMPAT_KEPT

    assert asked == [["hal0bk_pi-coder.old"]]
    assert json.loads(cfg.read_text())["harnesses"]["pi"] == {
        "apiToken": "hal0bk_pi-coder.old",
        "serverMode": "self-hosted",
        "apiUrl": _COMPAT,
    }


@pytest.mark.parametrize("status", ["error", "disabled"])
def test_a_store_that_cannot_be_asked_keeps_the_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_runner: _FakeRunner,
    capsys,
    status: str,
) -> None:
    """bridge.db unreadable, or the bridge off: a key is never replaced on that,
    whatever hal0-api answers over HTTP (it 401s a valid key in both cases)."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.old"}}})
    _key(monkeypatch, status=status, key=None)

    assert d._wire_memory_compat() == driver_mod.COMPAT_KEPT

    assert json.loads(cfg.read_text())["harnesses"]["pi"]["apiToken"] == "hal0bk_pi-coder.old"
    assert "kept it" in capsys.readouterr().err


def test_a_key_the_store_does_not_know_rotates_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner, capsys
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.old"}}})
    _key(monkeypatch, status="rotated", key="hal0bk_pi-coder.rotated")

    assert d._wire_memory_compat() == driver_mod.COMPAT_ROTATED

    assert json.loads(cfg.read_text())["harnesses"]["pi"]["apiToken"] == ("hal0bk_pi-coder.rotated")
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "rotated" in err and "pi-coder" in err
    assert "hal0bk_" not in err  # the key is never printed


def test_a_failed_write_after_a_rotate_says_the_old_key_is_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner, capsys
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.old"}}})
    _key(monkeypatch, status="rotated", key="hal0bk_pi-coder.rotated")

    def fail(self, path, data, *, max_mode=None):
        raise OSError("disk full")

    monkeypatch.setattr(PiDriver, "_store_hindsight_config", fail)

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED

    err = capsys.readouterr().err
    assert "old key no longer works" in err and "sudo hal0 agent install pi" in err
    assert "hal0bk_" not in err


def test_the_hal0_user_does_not_rotate_an_active_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner, capsys
) -> None:
    """The UI install runs as hal0 and cannot see root's key: it warns instead
    of rotating a client root's profile may be using."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    text = json.dumps({"harnesses": {"pi": {"autoUpdate": False}}})
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(text)
    _key(monkeypatch, status="existing", key=None)

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED

    assert cfg.read_text() == text
    assert "sudo hal0 agent install pi" in capsys.readouterr().err


@pytest.mark.parametrize(
    "section",
    [
        {"apiToken": "operator-token"},
        {"apiToken": "hal0bk_someone.else"},
        {"apiUrl": "http://my-own-hindsight:8888"},
        {"apiUrl": "http://my-own-hindsight:8888", "apiToken": "hal0bk_pi-coder.x"},
    ],
)
def test_an_operator_pi_section_is_left_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_runner: _FakeRunner,
    _no_real_compat: list[list[str]],
    capsys,
    section: dict[str, str],
) -> None:
    """Only a ``pi-coder`` key and hal0's endpoint are hal0's: anything else in
    Pi's section is the operator's, and a foreign token is never sent to the
    store or pointed at the endpoint."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"harnesses": {"pi": section}})
    text = cfg.read_text()
    _no_real_compat.clear()

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED

    assert cfg.read_text() == text
    assert _no_real_compat == []
    assert "hal0 did not write" in capsys.readouterr().err


# ── two homes: root's profile and the hal0 account's share one key ─────────


def _other_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pi: dict[str, Any]) -> Path:
    other = tmp_path / "other-home"
    cfg = other / ".hindsight" / "coding-agent.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"flat": 1, "harnesses": {"pi": pi}}))
    monkeypatch.setattr(driver_mod, "_known_homes", lambda: [other, tmp_path])
    return cfg


def test_a_valid_key_in_the_other_home_is_reused_not_rotated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {})
    _other_home(tmp_path, monkeypatch, {"apiUrl": _COMPAT, "apiToken": "hal0bk_pi-coder.hal0s"})
    asked: list[list[str]] = []

    def verify(self: PiDriver, candidates: list[str]) -> dict[str, Any]:
        asked.append(list(candidates))
        return {"status": "valid", "index": candidates.index("hal0bk_pi-coder.hal0s")}

    monkeypatch.setattr(PiDriver, "_managed_compat_key", verify)

    assert d._wire_memory_compat() == driver_mod.COMPAT_REUSED

    assert asked == [["hal0bk_pi-coder.hal0s"]]
    assert json.loads(cfg.read_text())["harnesses"]["pi"]["apiToken"] == "hal0bk_pi-coder.hal0s"


def test_a_new_key_is_shared_with_the_other_homes_pi_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.dead"}}})
    other = _other_home(tmp_path, monkeypatch, {"apiToken": "hal0bk_pi-coder.dead2"})
    _key(monkeypatch, status="rotated", key="hal0bk_pi-coder.rotated")

    assert d._wire_memory_compat() == driver_mod.COMPAT_ROTATED

    data = json.loads(other.read_text())
    assert data["flat"] == 1
    assert data["harnesses"]["pi"] == {
        "apiToken": "hal0bk_pi-coder.rotated",
        "serverMode": "self-hosted",
        "apiUrl": _COMPAT,
    }
    assert stat.S_IMODE(os.stat(other).st_mode) == 0o600


def test_an_operator_section_in_the_other_home_is_neither_used_nor_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    _shared_config(d, {})
    other = _other_home(tmp_path, monkeypatch, {"apiToken": "operator-token"})
    text = other.read_text()
    asked: list[list[str]] = []

    def mint(self: PiDriver, candidates: list[str]) -> dict[str, Any]:
        asked.append(list(candidates))
        return {"status": "minted", "key": "hal0bk_pi-coder.new"}

    monkeypatch.setattr(PiDriver, "_managed_compat_key", mint)

    assert d._wire_memory_compat() == driver_mod.COMPAT_MINTED
    assert asked == [[]] and other.read_text() == text


# ── writing the shared file safely as root ─────────────────────────────────


def test_a_symlinked_target_leaving_the_home_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner, capsys
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    monkeypatch.setenv("HOME", str(home))
    outside = tmp_path / "etc-shadow"
    outside.write_text("{}")
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.symlink_to(outside)
    _key(monkeypatch)

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED

    assert outside.read_text() == "{}"
    assert "not safe to write" in capsys.readouterr().err


def test_a_symlinked_tmp_file_is_never_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {})
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    cfg.with_suffix(".tmp").symlink_to(victim)
    _key(monkeypatch)

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED

    assert victim.read_text() == "untouched"
    assert cfg.with_suffix(".tmp").is_symlink()
    assert "harnesses" not in json.loads(cfg.read_text())


def test_a_stale_regular_tmp_file_of_ours_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {})
    cfg.with_suffix(".tmp").write_text("half a write")
    _key(monkeypatch)

    assert d._wire_memory_compat() == driver_mod.COMPAT_MINTED
    assert not cfg.with_suffix(".tmp").exists()


def test_a_target_owned_by_someone_else_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    from hal0.memory import compat_client

    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {})
    monkeypatch.setattr(compat_client, "_allowed_owners", lambda home: {os.getuid() + 4242})
    _key(monkeypatch)

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED
    assert cfg.read_text() == "{}"


def test_no_key_leaves_pi_on_its_current_config_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner, capsys
) -> None:
    """No hal0 user, no bridge store, the bridge off, or a foreign client of
    that name: warn and change nothing, never fail the install."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    text = json.dumps({"apiUrl": "http://127.0.0.1:9177"})
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(text)

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED

    assert cfg.read_text() == text
    assert "Pi stays on its current memory config" in capsys.readouterr().err


def test_pis_section_undoes_the_old_hal0_mono_alias_for_pi_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    """On the endpoint the bank only carries the project: Pi's writes from the
    hal0 repo must be project:hal0, not project:hal0-mono."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    flat = {"coding-agent::hal0": {"bank": "coding-agent::hal0-mono"}}
    cfg = _shared_config(d, {"banks": flat})
    _key(monkeypatch)

    d._wire_memory_compat()

    data = json.loads(cfg.read_text())
    assert data["banks"] == flat
    assert data["harnesses"]["pi"]["banks"] == {
        "coding-agent::hal0": {"bank": "coding-agent::hal0"}
    }


def test_an_operator_alias_is_not_shadowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"banks": {"coding-agent::hal0": {"bank": "team::shared"}}})
    _key(monkeypatch)

    d._wire_memory_compat()

    assert "banks" not in json.loads(cfg.read_text())["harnesses"]["pi"]


@pytest.mark.parametrize(
    "content", ["{not json", "[1, 2]", '{"harnesses": "x"}', '{"harnesses": {"pi": 3}}']
)
def test_compat_wiring_leaves_unusable_files_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner, content: str
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(content)
    _key(monkeypatch)

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED
    assert cfg.read_text() == content


def test_upgrade_points_an_existing_config_at_the_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"apiUrl": "http://other:9177"})
    _key(monkeypatch)

    assert d.upgrade() == {"memory_compat": driver_mod.COMPAT_MINTED}

    data = json.loads(cfg.read_text())
    assert data["apiUrl"] == "http://other:9177"
    assert data["harnesses"]["pi"]["apiUrl"] == _COMPAT


# ── minting as the hal0 user ───────────────────────────────────────────────


class _MintRunner(_FakeRunner):
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        super().__init__()
        self.stdout = stdout
        self.returncode = returncode

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        out, rc = self.stdout, self.returncode

        class _Done:
            returncode = rc
            stdout = out
            stderr = ""

        return _Done()


def _real_mint(monkeypatch: pytest.MonkeyPatch, *, euid: int, hal0_user: bool = False) -> None:
    from hal0.system import seam

    # Put back the real method the autouse fixture stubbed.
    monkeypatch.setattr(PiDriver, "_managed_compat_key", _REAL_MANAGED_COMPAT_KEY)
    monkeypatch.setattr(seam, "hal0_service_account", lambda: seam.ServiceAccount(990, 990, ()))
    monkeypatch.setattr(seam, "is_hal0_service_user", lambda: hal0_user)
    monkeypatch.setattr(os, "geteuid", lambda: euid)


def test_root_mints_through_the_hal0_user_and_reads_the_key_from_the_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_mint(monkeypatch, euid=0)
    runner = _MintRunner('noise\n{"status": "minted", "key": "hal0bk_pi-coder.k"}\n')

    answer = PiDriver(runner=runner)._managed_compat_key(["hal0bk_pi-coder.held"])

    assert answer == {"status": "minted", "key": "hal0bk_pi-coder.k"}
    argv, kwargs = runner.calls[0]
    assert argv[0] in ("runuser", "setpriv", "sudo") and "hal0" in argv
    assert argv[-9:] == [
        "hal0.bridge.managed_mint",
        "pi-coder",
        "system:pi-coder",
        "--rotate",
        "--keys-on-stdin",
        "--harness",
        "pi",
        "--api-url",
        _COMPAT,
    ]
    assert kwargs["capture_output"] is True and "env" not in kwargs
    # The held key goes in on stdin only: never in argv.
    assert json.loads(kwargs["input"]) == {"keys": ["hal0bk_pi-coder.held"]}
    assert not any("hal0bk_" in a for a in argv)


def test_a_helper_with_no_answer_is_an_error_not_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_mint(monkeypatch, euid=0)
    answer = PiDriver(runner=_MintRunner("", returncode=1))._managed_compat_key([])
    assert answer["status"] == "error" and "key" not in answer
    # It ran with --rotate: it may have rotated before it failed.
    assert answer["maybe_rotated"] is True


def test_no_hal0_user_means_no_mint(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.system import seam

    _real_mint(monkeypatch, euid=0)
    monkeypatch.setattr(seam, "hal0_service_account", lambda: None)
    runner = _MintRunner("")
    answer = PiDriver(runner=runner)._managed_compat_key([])
    assert answer["status"] == "error" and runner.calls == []


def test_another_user_cannot_mint(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_mint(monkeypatch, euid=1000)
    runner = _MintRunner("")
    assert PiDriver(runner=runner)._managed_compat_key([])["status"] == "error"
    assert runner.calls == []


def test_the_hal0_user_mints_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.bridge import managed_mint

    _real_mint(monkeypatch, euid=990, hal0_user=True)
    seen: list[tuple[str, str, bool, list[str]]] = []
    monkeypatch.setattr(
        managed_mint,
        "mint",
        lambda name, by, *, rotate, candidates: (
            seen.append((name, by, rotate, list(candidates))) or {"status": "minted"}
        ),
    )
    runner = _MintRunner("")
    assert PiDriver(runner=runner)._managed_compat_key(["k"]) == {"status": "minted"}
    # In-process as hal0: verify and mint, never rotate.
    assert seen == [("pi-coder", "system:pi-coder", False, ["k"])] and runner.calls == []


def test_a_helper_that_fails_after_rotate_says_the_key_may_be_rotated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner, capsys
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.held"}}})
    before = cfg.read_text()
    _real_mint(monkeypatch, euid=0)
    d._runner = _MintRunner('{"status": "rotated", "key": "hal0bk_pi-coder.n')  # cut off

    assert d._wire_memory_compat() == driver_mod.COMPAT_SKIPPED

    err = capsys.readouterr().err
    assert "may have been rotated" in err and "sudo hal0 agent install pi" in err
    assert cfg.read_text() == before


def test_a_key_the_helper_found_in_hal0s_own_file_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.dead"}}})
    _key(monkeypatch, status="valid", key="hal0bk_pi-coder.hal0s")

    assert d._wire_memory_compat() == driver_mod.COMPAT_REUSED
    assert json.loads(cfg.read_text())["harnesses"]["pi"]["apiToken"] == "hal0bk_pi-coder.hal0s"


def test_root_leaves_hal0s_home_to_the_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    from hal0.system import seam

    hal0_home = tmp_path / "hal0-home"
    hal0_cfg = hal0_home / ".hindsight" / "coding-agent.json"
    hal0_cfg.parent.mkdir(parents=True)
    hal0_cfg.write_text(json.dumps({"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.old"}}}))
    before = hal0_cfg.read_text()
    monkeypatch.setattr(seam, "hal0_service_home", lambda: str(hal0_home))
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    _shared_config(d, {})
    # The real list of other homes: root's only, never hal0's.
    monkeypatch.setattr(driver_mod, "_known_homes", _REAL_KNOWN_HOMES)
    assert hal0_home not in driver_mod._known_homes()
    _real_mint(monkeypatch, euid=0)
    d._runner = _MintRunner(
        '{"status": "rotated", "key": "hal0bk_pi-coder.new", "shared": "written"}\n'
    )

    assert d._wire_memory_compat() == driver_mod.COMPAT_ROTATED

    # The helper (here a stub) owns hal0's file; root did not touch it, and
    # root did not read it for candidates either.
    assert hal0_cfg.read_text() == before
    assert json.loads(d._runner.calls[0][1]["input"]) == {"keys": []}


# ── the wiring lock ────────────────────────────────────────────────────────


def test_the_wiring_lock_is_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(driver_mod._paths, "var_lib", lambda: state)
    monkeypatch.setattr(os, "geteuid", os.getuid)
    lock = state / driver_mod._PI_WIRING_LOCK

    with driver_mod._pi_wiring_lock():
        assert lock.is_file() and stat.S_IMODE(lock.stat().st_mode) == 0o600
        fd = os.open(lock, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    fd = os.open(lock, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released
    finally:
        os.close(fd)


def test_the_wiring_lock_never_follows_a_symlink_or_hangs_on_a_fifo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(driver_mod._paths, "var_lib", lambda: state)
    victim = tmp_path / "victim"
    (state / driver_mod._PI_WIRING_LOCK).symlink_to(victim)

    with driver_mod._pi_wiring_lock():
        pass
    assert not victim.exists()

    (state / driver_mod._PI_WIRING_LOCK).unlink()
    os.mkfifo(state / driver_mod._PI_WIRING_LOCK)
    with driver_mod._pi_wiring_lock():  # O_NONBLOCK: returns, no lock taken
        pass


def test_the_wiring_runs_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    import contextlib

    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    _shared_config(d, {})
    held: list[bool] = []
    state = {"in": False}

    @contextlib.contextmanager
    def lock():
        state["in"] = True
        yield
        state["in"] = False

    def mint(self: PiDriver, candidates: list[str]) -> dict[str, Any]:
        held.append(state["in"])
        return {"status": "minted", "key": "hal0bk_pi-coder.new"}

    monkeypatch.setattr(driver_mod, "_pi_wiring_lock", lock)
    monkeypatch.setattr(PiDriver, "_managed_compat_key", mint)

    d._wire_memory_compat()
    assert held == [True]
