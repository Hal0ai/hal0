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

_REAL_MANAGED_COMPAT_KEY = PiDriver._managed_compat_key


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
def _no_real_compat(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No test reaches a real hal0-api or bridge store. The default: the token
    cannot be checked, and no key can be minted. Returns the mint calls."""
    minted: list[str] = []

    def no_key(self: PiDriver) -> dict[str, Any]:
        minted.append("called")
        return {"status": "error", "key": None, "detail": "stub"}

    monkeypatch.setattr(driver_mod, "_compat_accepts", lambda url, token: None)
    monkeypatch.setattr(PiDriver, "_managed_compat_key", no_key)
    monkeypatch.delenv("HAL0_API_URL", raising=False)
    return minted


def _key(monkeypatch: pytest.MonkeyPatch, status: str = "minted", key: str = "hal0bk_pi-coder.new"):
    monkeypatch.setattr(
        PiDriver, "_managed_compat_key", lambda self: {"status": status, "key": key}
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
    chowned: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: chowned.append((uid, gid)))
    _key(monkeypatch)

    d._wire_memory_compat()

    assert cfg.is_symlink() and cfg.resolve() == real
    assert json.loads(real.read_text())["harnesses"]["pi"]["apiUrl"] == _COMPAT
    assert chowned == [(st.st_uid, st.st_gid)]


@pytest.mark.parametrize("accepted", [True, None])
def test_a_working_or_uncheckable_token_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_runner: _FakeRunner,
    _no_real_compat: list[str],
    accepted: bool | None,
) -> None:
    """A key is never replaced on a guess: hal0-api down keeps it too."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.old"}}})
    seen: list[tuple[str, str]] = []

    def accepts(url: str, token: str) -> bool | None:
        seen.append((url, token))
        return accepted

    monkeypatch.setattr(driver_mod, "_compat_accepts", accepts)

    assert d._wire_memory_compat() == driver_mod.COMPAT_KEPT

    assert seen == [(_COMPAT, "hal0bk_pi-coder.old")]
    assert _no_real_compat == []  # nothing minted or rotated
    assert json.loads(cfg.read_text())["harnesses"]["pi"] == {
        "serverMode": "self-hosted",
        "apiToken": "hal0bk_pi-coder.old",
        "apiUrl": _COMPAT,
    }


def test_a_rejected_token_rotates_the_managed_client_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner, capsys
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = _shared_config(d, {"harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.old"}}})
    monkeypatch.setattr(driver_mod, "_compat_accepts", lambda url, token: False)
    _key(monkeypatch, status="rotated", key="hal0bk_pi-coder.rotated")

    assert d._wire_memory_compat() == driver_mod.COMPAT_ROTATED

    assert json.loads(cfg.read_text())["harnesses"]["pi"]["apiToken"] == ("hal0bk_pi-coder.rotated")
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "rotated" in err and "pi-coder" in err
    assert "hal0bk_" not in err  # the key is never printed


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

    answer = PiDriver(runner=runner)._managed_compat_key()

    assert answer == {"status": "minted", "key": "hal0bk_pi-coder.k"}
    argv, kwargs = runner.calls[0]
    assert argv[0] in ("runuser", "setpriv", "sudo") and "hal0" in argv
    assert argv[-4:] == ["hal0.bridge.managed_mint", "pi-coder", "system:pi-coder", "--rotate"]
    assert kwargs["capture_output"] is True
    assert "input" not in kwargs and "env" not in kwargs  # no key in, only the pipe out


def test_a_helper_with_no_answer_is_an_error_not_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_mint(monkeypatch, euid=0)
    answer = PiDriver(runner=_MintRunner("", returncode=1))._managed_compat_key()
    assert answer["status"] == "error" and "key" not in answer


def test_no_hal0_user_means_no_mint(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.system import seam

    _real_mint(monkeypatch, euid=0)
    monkeypatch.setattr(seam, "hal0_service_account", lambda: None)
    runner = _MintRunner("")
    answer = PiDriver(runner=runner)._managed_compat_key()
    assert answer["status"] == "error" and runner.calls == []


def test_another_user_cannot_mint(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_mint(monkeypatch, euid=1000)
    runner = _MintRunner("")
    assert PiDriver(runner=runner)._managed_compat_key()["status"] == "error"
    assert runner.calls == []


def test_the_hal0_user_mints_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.bridge import managed_mint

    _real_mint(monkeypatch, euid=990, hal0_user=True)
    seen: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        managed_mint,
        "mint",
        lambda name, by, *, rotate: seen.append((name, by, rotate)) or {"status": "minted"},
    )
    runner = _MintRunner("")
    assert PiDriver(runner=runner)._managed_compat_key() == {"status": "minted"}
    assert seen == [("pi-coder", "system:pi-coder", True)] and runner.calls == []
