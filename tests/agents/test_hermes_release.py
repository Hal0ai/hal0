"""The Hermes release builder (ADR-0032 decisions 4, 6, 7, 10, 11; ADR-0034 1, 3, 4).

Everything runs against a tmp release tree with a fake ``git``/``uv`` runner
and fake unit controller, so no network, no real venv, no systemd.

``PIN`` is 0.19.0 (``3ef6bbd2``), the pin before #2615 PR 4. It stays the
default here as the representative ref capped below 3.14 (3.12 venv, no
lock that serves 3.14, no ``web.mjs``): the autouse fixture vets it again
with its recorded ``requires-python``. ``NEW_PIN`` is the shipped pin,
v0.22.2, and the "PR 4" section covers the move from one to the other.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp
from hal0.agents import hermes_release as rel

PIN = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"
NEW_PIN = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"
URL = "https://github.com/NousResearch/hermes-agent.git"
UV = "/fake/venv/bin/uv"
OK_PROBE: Callable[[Path], dict[str, Any]] = lambda _py: {"ok": True}  # noqa: E731

#: What `uv pip sync` of an export puts in site-packages, per extra.
_EXTRA_PACKAGES = {
    "telegram": ("telegram",),
    "discord": ("discord", "aiohttp"),
    "slack": ("slack_bolt", "aiohttp"),
    "sms": ("aiohttp",),
}


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeTools:
    """Simulates the git / uv / venv-python commands the builder runs."""

    def __init__(
        self,
        *,
        head: str = PIN,
        lock: bool = False,
        web: str | None = None,
        web_ok: bool = True,
        fail: Callable[[list[str]], bool] | None = None,
        minor: str = "3.12",
        requires_python: str | None = None,
    ) -> None:
        self.head = head
        self.lock = lock
        self.web = web  # None | "old" (web/ only) | "new" (web/ + scripts/build/web.mjs)
        self.web_ok = web_ok
        self.fail = fail
        self.minor = minor
        self.requires_python = requires_python
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.exported: list[list[str]] = []

    def __call__(self, argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        self.envs.append(dict(kw.get("env") or {}))
        out = ""
        if self.fail is not None and self.fail(argv):
            return subprocess.CompletedProcess(argv, 1, "", f"simulated failure: {argv[:3]}")
        if argv[0] == "git":
            if "init" in argv:
                mirror = Path(argv[-1])
                mirror.mkdir(parents=True, exist_ok=True)
                (mirror / "HEAD").write_text("ref: refs/heads/main\n")
            elif "worktree" in argv and "add" in argv:
                src = Path(argv[-2])
                (src / "hermes_cli").mkdir(parents=True)
                if self.requires_python:
                    (src / "pyproject.toml").write_text(
                        f'[project]\nname = "hermes-agent"\nrequires-python = "{self.requires_python}"\n'
                    )
                if self.lock:
                    (src / "uv.lock").write_text(
                        'version = 1\nrevision = 3\nrequires-python = ">=3.11"\n'
                    )
                if self.web:
                    (src / "web").mkdir()
                    (src / "web" / "package.json").write_text("{}")
                if self.web == "new":
                    (src / "scripts" / "build").mkdir(parents=True)
                    (src / "scripts" / "build" / "web.mjs").write_text("")
            elif "rev-parse" in argv:
                out = self.head + "\n"
            elif "describe" in argv:
                out = "v2026.7.20\n"
            elif "log" in argv and "--format=%cI" in argv:
                out = "2026-07-20T12:00:00+00:00\n"
        elif argv[:2] == [UV, "export"]:
            self.exported.append([argv[i + 1] for i, a in enumerate(argv) if a == "--extra"])
        elif argv[:3] == [UV, "pip", "sync"]:
            # Install what the export named, as packages in site-packages.
            venv = Path(argv[argv.index("--python") + 1]).parent.parent
            site = venv / "lib" / f"python{self.minor}" / "site-packages"
            site.mkdir(parents=True, exist_ok=True)
            for extra in self.exported[-1]:
                for pkg in _EXTRA_PACKAGES.get(extra, ()):
                    (site / pkg).mkdir(exist_ok=True)
        elif argv[0] == UV and argv[1] == "venv":
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True)
            for name in ("python", "hermes"):
                (venv / "bin" / name).write_text("#!/bin/sh\n")
            (venv / "pyvenv.cfg").write_text(
                f"home = /usr/bin\nversion_info = {self.minor}.9\n", encoding="utf-8"
            )
        elif (
            argv[0].endswith("/bin/python")
            and "-c" in argv
            and "_build_web_ui" in argv[argv.index("-c") + 1]
        ):
            if self.web_ok:
                dist = Path(kw["cwd"]) / "hermes_cli" / "web_dist"
                dist.mkdir(parents=True)
                (dist / "index.html").write_text("<html>")
            else:
                return subprocess.CompletedProcess(argv, 1, "", "npm: not found")
        return subprocess.CompletedProcess(argv, 0, out, "")

    def find(self, pred: Callable[[list[str]], bool]) -> list[list[str]]:
        return [c for c in self.calls if pred(c)]


class FakeUnits:
    """In-memory unit controller: ``state`` is the unit's ActiveState."""

    def __init__(self, active: set[str] | None = None, *, crash_on_start: set[str] | None = None):
        self.state = {
            u: ("active" if u in (active or set()) else "inactive") for u in rel.HERMES_UNITS
        }
        self.restart_count = dict.fromkeys(rel.HERMES_UNITS, 0)
        self.crash_on_start = crash_on_start or set()
        self.log: list[tuple[str, str]] = []

    def active(self, unit: str) -> bool:
        return self.state[unit] == "active"

    def restarts(self, unit: str) -> int | None:
        return self.restart_count[unit]

    def stop(self, unit: str) -> bool:
        self.log.append(("stop", unit))
        self.state[unit] = "inactive"
        return True

    def start(self, unit: str) -> bool:
        self.log.append(("start", unit))
        self.state[unit] = "active"
        return True

    def settle(self, _seconds: float) -> None:
        """The settle period: a crash-looping unit gets restarted by systemd."""
        for unit in self.crash_on_start:
            if self.state[unit] == "active":
                self.restart_count[unit] += 1


@pytest.fixture(autouse=True)
def _vet_the_312_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vet 0.19.0 alongside the shipped pin, with its real requires-python."""
    monkeypatch.setattr(hp, "VETTED_HERMES_REFS", hp.VETTED_HERMES_REFS | {PIN})
    monkeypatch.setitem(hp.VETTED_HERMES_REQUIRES_PYTHON, PIN, ">=3.11,<3.14")


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    var_lib = tmp_path / "var" / "lib" / "hal0"
    venv = var_lib / "venvs" / "hermes"
    home = var_lib / ".hermes"
    home.mkdir(parents=True)
    req = tmp_path / "requirements.txt"
    req.write_text(f"# comment\nhermes-agent[web,mcp] @ git+{URL}@{PIN}\n", encoding="utf-8")
    monkeypatch.setattr(hp, "UV_CACHE_DIR", var_lib / ".cache" / "uv")
    monkeypatch.setattr(hp, "UV_PYTHON_INSTALL_DIR", var_lib / "python")
    monkeypatch.setattr(hp, "HERMES_PYTHON_ENV", tmp_path / "etc" / "hermes-python.env")
    monkeypatch.delenv("HAL0_HERMES_PYTHON", raising=False)
    # The gateway's secrets vault and the default HERMES_HOME, read for the
    # configured platforms (hermes_platforms), stay inside tmp.
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", tmp_path / "secrets" / "hermes.env")
    monkeypatch.setattr(hp, "HERMES_HOME_DEFAULT", home)
    return {
        "venv": venv,
        "home": home,
        "req": req,
        "stamp": var_lib / "state" / "agents" / "hermes" / "venv.pin",
        "unit": tmp_path / "etc" / "systemd" / "system" / "hermes-gateway.service",
        "root": var_lib / "hermes",
        "vault": tmp_path / "secrets" / "hermes.env",
    }


def _ensure(
    tree: dict[str, Path], tools: FakeTools, units: Any = None, **kw: Any
) -> rel.ReleaseOutcome:
    args: dict[str, Any] = {
        "caller": "test",
        "venv": tree["venv"],
        "requirements": tree["req"],
        "hermes_home": tree["home"],
        "units": units if units is not None else FakeUnits(),
        "run": tools,
        "probe": OK_PROBE,
        "python_resolver": lambda m: f"/usr/bin/python{m}",
        "uv": UV,
        "stamp_file": tree["stamp"],
        "gateway_unit": tree["unit"],
        "settle_s": 0,
        "sleep": lambda _s: None,
    }
    args.update(kw)
    return rel.ensure_release(**args)


def _legacy_venv(tree: dict[str, Path]) -> Path:
    venv = tree["venv"]
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("legacy")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.12.3\n")
    tree["stamp"].parent.mkdir(parents=True, exist_ok=True)
    tree["stamp"].write_text(PIN[:12] + "\n")
    return venv


# ── pin, triggers, install commands ──────────────────────────────────────────


def test_read_pin_takes_url_extras_and_ref_from_the_requirements_line(tree) -> None:
    pin = rel.read_pin(tree["req"])
    assert pin == rel.Pin(url=URL, extras=("web", "mcp"), ref=PIN)


@pytest.mark.parametrize("ref", ["v2026.7.20", PIN[:12], "f" * 40, "0.19.0"])
def test_only_a_vetted_40_hex_commit_is_accepted(ref: str) -> None:
    with pytest.raises(rel.ReleaseError):
        rel.require_vetted(ref)


def test_lock_install_command_lines(tmp_path: Path) -> None:
    """ADR-0034 decision 1: export with hashes, sync --require-hashes, then --no-deps -e."""
    src, venv, export = tmp_path / "src", tmp_path / "venv", tmp_path / "req.lock"
    src.mkdir()
    (src / "uv.lock").write_text("")
    lines = rel.install_command_lines(
        uv="uv", src=src, venv=venv, extras=("web", "mcp"), export_file=export
    )
    py = str(venv / "bin" / "python")
    assert lines == [
        ["uv", "export", "--frozen", "--project", str(src), "--extra", "web", "--extra", "mcp",
         "--no-emit-project", "--output-file", str(export)],
        ["uv", "pip", "sync", "--python", py, "--require-hashes", str(export)],
        ["uv", "pip", "install", "--python", py, "--no-deps", "-e", str(src)],
    ]  # fmt: skip


def test_no_lock_falls_back_to_an_editable_install_with_extras(tmp_path: Path) -> None:
    src, venv = tmp_path / "src", tmp_path / "venv"
    src.mkdir()
    lines = rel.install_command_lines(
        uv="uv", src=src, venv=venv, extras=("web", "mcp"), export_file=tmp_path / "x"
    )
    assert lines == [
        ["uv", "pip", "install", "--python", str(venv / "bin" / "python"), "-e", f"{src}[web,mcp]"]
    ]


def test_triggers(tree) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    assert rel.build_trigger(layout, PIN, "3.12") == "no live release"
    _legacy_venv(tree)
    assert "pre-layout venv" in rel.build_trigger(layout, PIN, "3.12")


# ── build ────────────────────────────────────────────────────────────────────


def test_fresh_install_builds_activates_stamps_and_links(tree) -> None:
    tools = FakeTools()
    out = _ensure(tree, tools)
    assert out.status == "activated", out.error
    layout = rel.Layout.for_venv(tree["venv"])
    live = rel.live_release(layout)
    assert live is not None and live.ref == PIN and live.id == out.release_id
    assert live.id.startswith(PIN[:12] + "-")
    # venvs/hermes -> <root>/current/venv, current -> releases/<id> (relative)
    assert os.readlink(tree["venv"]) == str(layout.current / "venv")
    assert os.readlink(layout.current) == f"releases/{live.id}"
    assert not layout.previous.exists()
    data = live.data
    assert data["describe"] == "v2026.7.20"
    assert data["python_minor"] == "3.12" and data["python"] == "/usr/bin/python3.12"
    assert data["lock"] is False and data["web_dist"] == "absent" and data["legacy"] is False
    # Every activation path writes the stamp from release.json.
    assert tree["stamp"].read_text() == PIN[:12] + "\n"
    # The mirror fetched the sha WITH tags, and the worktree is detached at it.
    assert tools.find(
        lambda c: c[:2] == ["git", "-C"] and "fetch" in c and "--tags" in c and c[-1] == PIN
    )
    assert tools.find(lambda c: "worktree" in c and "--detach" in c and c[-1] == PIN)
    # Fallback install path at 3ef6bbd2 (no uv.lock).
    assert tools.find(lambda c: c[:3] == [UV, "pip", "install"] and c[-1].endswith("[web,mcp]"))


def test_the_mirror_is_a_blobless_partial_clone(tree) -> None:
    tools = FakeTools()
    assert _ensure(tree, tools).ok
    assert tools.find(lambda c: c[-2:] == ["remote.origin.promisor", "true"])
    assert tools.find(lambda c: c[-2:] == ["remote.origin.partialclonefilter", "blob:none"])
    assert tools.find(lambda c: "fetch" in c and "--filter=blob:none" in c)


def test_the_offline_safe_release_check(tree) -> None:
    """ADR-0034 d3: import check + --help + mcp probe; never ``hermes --version``."""
    tools = FakeTools()
    assert _ensure(tree, tools).ok
    assert tools.find(
        lambda c: c[-2:] == ["-c", "import hermes_cli.main, hermes_cli.web_server"] and "-I" in c
    )
    assert tools.find(lambda c: c[-1] == "--help" and c[0].endswith("/bin/hermes"))
    assert not tools.find(lambda c: "--version" in c)


def test_locked_ref_installs_from_the_lock(tree) -> None:
    tools = FakeTools(lock=True)
    out = _ensure(tree, tools)
    assert out.ok
    assert tools.find(lambda c: c[:2] == [UV, "export"] and "--frozen" in c)
    assert tools.find(lambda c: c[:3] == [UV, "pip", "sync"] and "--require-hashes" in c)
    assert tools.find(lambda c: c[:3] == [UV, "pip", "install"] and "--no-deps" in c)
    assert rel.live_release(rel.Layout.for_venv(tree["venv"])).data["lock"] is True


def test_a_checkout_at_the_wrong_commit_fails_the_build(tree) -> None:
    out = _ensure(tree, FakeTools(head="e" * 40))
    assert out.status == "build_failed"
    assert "verify-ref" in out.error
    layout = rel.Layout.for_venv(tree["venv"])
    assert list(layout.releases.iterdir()) == []  # the half-built release is gone


def test_a_failed_build_leaves_the_live_release_alone(tree) -> None:
    assert _ensure(tree, FakeTools()).ok
    layout = rel.Layout.for_venv(tree["venv"])
    before = os.readlink(layout.current)
    out = _ensure(tree, FakeTools(fail=lambda c: c[:3] == [UV, "pip", "install"]), force=True)
    assert out.status == "build_failed" and "install" in out.error
    assert os.readlink(layout.current) == before
    assert [p.name for p in layout.releases.iterdir()] == [before.split("/")[-1]]


def test_converged_host_builds_nothing(tree) -> None:
    assert _ensure(tree, FakeTools()).ok
    tools = FakeTools()
    out = _ensure(tree, tools)
    assert out.status == "converged"
    assert tools.calls == []


def test_a_repair_builds_a_fresh_release_of_the_same_ref(tree) -> None:
    first = _ensure(tree, FakeTools())
    second = _ensure(tree, FakeTools(), force=True, reason="--repair")
    assert second.status == "activated"
    assert second.release_id != first.release_id
    assert second.previous_id == first.release_id


# ── web UI build (ADR-0034 decision 4, #2837) ────────────────────────────────


def test_web_ui_is_built_into_the_release_checkout(tree) -> None:
    tools = FakeTools(web="new")
    out = _ensure(tree, tools)
    assert out.ok
    live = rel.live_release(rel.Layout.for_venv(tree["venv"]))
    assert live.data["web_dist"] == "built"
    assert (live.src / "hermes_cli" / "web_dist" / "index.html").is_file()


def test_web_build_failure_fails_a_ref_that_ships_the_build_script(tree) -> None:
    out = _ensure(tree, FakeTools(web="new", web_ok=False))
    assert out.status == "build_failed" and "web-build" in out.error


def test_web_build_failure_at_the_old_pin_records_absent(tree) -> None:
    """3ef6bbd2 has no scripts/build/web.mjs and needs a system npm: not fatal."""
    out = _ensure(tree, FakeTools(web="old", web_ok=False))
    assert out.ok
    assert rel.live_release(rel.Layout.for_venv(tree["venv"])).data["web_dist"] == "absent"


def test_web_build_runs_with_upstreams_tool_store_inside_the_release_tree(tree) -> None:
    """Node goes to hermes/tools via HERMES_RUNTIME_DIR, shared by every release."""
    tools = FakeTools(web="new")
    assert _ensure(tree, tools).ok
    layout = rel.Layout.for_venv(tree["venv"])
    [i] = [
        n
        for n, c in enumerate(tools.calls)
        if c[0].endswith("/bin/python") and "_build_web_ui" in " ".join(c)
    ]
    assert tools.envs[i]["HERMES_RUNTIME_DIR"] == str(layout.tools)
    assert layout.tools == layout.root / "tools"


@pytest.mark.parametrize("web", ["new", "old"])
def test_lazy_installs_off_fails_the_v0222_web_build_with_the_fix(tree, web: str) -> None:
    """security.allow_lazy_installs: false makes upstream's build pass
    --no-install; a ref with web.mjs fails up front and names the fix, before
    any npm work. A ref without web.mjs (0.19.0) is unaffected."""
    (tree["home"] / "config.yaml").write_text("security:\n  allow_lazy_installs: false\n")
    tools = FakeTools(web=web)
    out = _ensure(tree, tools)
    if web == "new":
        assert out.status == "build_failed"
        assert "allow_lazy_installs" in out.error and "reprovision hermes" in out.error
        assert not tools.find(lambda c: "_build_web_ui" in " ".join(c))
    else:
        assert out.ok


# ── offline (#2838) ──────────────────────────────────────────────────────────


def test_offline_refuses_a_needed_build_and_names_the_step(tree) -> None:
    tools = FakeTools()
    out = _ensure(tree, tools, offline=True)
    assert out.status == "offline"
    assert "fetch" in out.error and "needs the network" in out.error and PIN[:12] in out.error
    assert tools.calls == []
    assert not tree["venv"].exists()


def test_offline_is_fine_when_nothing_needs_building(tree) -> None:
    assert _ensure(tree, FakeTools()).ok
    assert _ensure(tree, FakeTools(), offline=True).status == "converged"


def test_offline_keeps_the_live_release_on_a_legacy_host(tree) -> None:
    venv = _legacy_venv(tree)
    out = _ensure(tree, FakeTools(), offline=True)
    assert out.status == "offline"
    assert venv.is_dir() and not venv.is_symlink()


# ── lock ─────────────────────────────────────────────────────────────────────


def test_lock_contention_is_busy_with_the_holder_record(tree) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    with rel.builder_lock(layout, caller="provision", wait=False):
        out = _ensure(tree, FakeTools(), wait=False)
        assert out.status == "busy"
        assert out.holder["pid"] == os.getpid()
        assert out.holder["caller"] == "provision"
        assert out.holder["started_at"].endswith("Z")
    # Released: the next caller builds.
    assert _ensure(tree, FakeTools(), wait=False).ok


def test_a_waiting_caller_reports_the_holder_then_proceeds(tree) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    seen: list[dict[str, Any]] = []
    entered = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with rel.builder_lock(layout, caller="converge", wait=False):
            entered.set()
            release.wait(5)

    t = threading.Thread(target=_hold)
    t.start()
    entered.wait(5)

    def _on_wait(holder: dict[str, Any]) -> None:
        seen.append(holder)
        release.set()

    with rel.builder_lock(layout, caller="provision", wait=True, on_wait=_on_wait):
        assert rel.read_lock_holder(layout)["caller"] == "provision"
    t.join(5)
    assert seen and seen[0]["caller"] == "converge"


def test_builder_lock_free_true_when_unheld(tmp_path: Path) -> None:
    layout = rel.Layout.for_venv(tmp_path / "venvs" / "hermes")
    free, holder = rel.builder_lock_free(layout)
    assert free is True and holder == {}


def test_builder_lock_free_false_while_held(tmp_path: Path) -> None:
    layout = rel.Layout.for_venv(tmp_path / "venvs" / "hermes")
    with rel.builder_lock(layout, caller="t", wait=False):
        free, holder = rel.builder_lock_free(layout)
    assert free is False and holder["caller"] == "t"


def test_builder_lock_free_never_rewrites_the_holder_record(tmp_path: Path) -> None:
    layout = rel.Layout.for_venv(tmp_path / "venvs" / "hermes")
    with rel.builder_lock(layout, caller="t", wait=False):
        before = layout.lock.read_bytes()
        rel.builder_lock_free(layout)
        assert layout.lock.read_bytes() == before
    # Free again: the probe took and dropped the lock without touching the stale record.
    free, holder = rel.builder_lock_free(layout)
    assert free is True and holder == {}
    assert layout.lock.read_bytes() == before


def test_an_incomplete_release_is_swept_under_the_lock(tree) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    dead = layout.releases / f"{PIN[:12]}-20200101T000000Z"
    (dead / "venv").mkdir(parents=True)  # no release.json: a builder died
    assert _ensure(tree, FakeTools()).ok
    assert not dead.exists()


# ── first migration (decision 11) ────────────────────────────────────────────


def test_first_migration_moves_the_legacy_venv_with_units_stopped(tree) -> None:
    venv = _legacy_venv(tree)
    units = FakeUnits(active={rel.AGENT_UNIT, rel.GATEWAY_UNIT})
    (tree["home"] / "config.yaml").write_text("model: a\n")
    out = _ensure(tree, FakeTools(), units)
    assert out.status == "activated", out.error
    assert out.migrated_legacy
    layout = rel.Layout.for_venv(venv)
    legacy = layout.releases / f"legacy-{PIN[:12]}"
    assert (legacy / "venv" / "bin" / "python").read_text() == "legacy"
    data = json.loads((legacy / "release.json").read_text())
    assert data["legacy"] is True and data["ref"] == PIN[:12]
    assert data["python_home"] == "/usr/bin" and data["python_minor"] == "3.12"
    assert os.readlink(layout.previous) == f"releases/legacy-{PIN[:12]}"
    assert venv.is_symlink() and os.readlink(venv) == str(layout.current / "venv")
    # Config copied into the outgoing (legacy) release before the switch.
    assert (legacy / f"config.yaml.before-{out.release_id}").read_text() == "model: a\n"
    # Both units stopped across the switch and started again.
    assert units.log == [
        ("stop", rel.AGENT_UNIT), ("stop", rel.GATEWAY_UNIT),
        ("start", rel.AGENT_UNIT), ("start", rel.GATEWAY_UNIT),
    ]  # fmt: skip


def test_a_failed_first_migration_flips_back_to_legacy(tree) -> None:
    venv = _legacy_venv(tree)
    (tree["home"] / "config.yaml").write_text("before\n")
    units = FakeUnits(active={rel.AGENT_UNIT})
    tools = FakeTools()
    calls = {"n": 0}

    def _probe(_py: Path) -> dict[str, Any]:
        calls["n"] += 1  # build check passes, re-verify through venvs/hermes fails
        return {"ok": calls["n"] == 1, "error": "no mcp"}

    def _migrate_then_fail(argv: list[Any], **kw: Any) -> Any:
        if [str(a) for a in argv][-2:] == ["config", "migrate"]:
            (tree["home"] / "config.yaml").write_text("migrated\n")
        return tools(argv, **kw)

    out = _ensure(tree, tools, units, probe=_probe, run=_migrate_then_fail)
    assert out.status == "activation_failed" and out.flipped_back
    assert out.remedy == "sudo hal0 agent reprovision hermes --repair"
    layout = rel.Layout.for_venv(venv)
    assert os.readlink(layout.current) == f"releases/legacy-{PIN[:12]}"
    assert not layout.previous.exists()
    assert (venv / "bin" / "python").read_text() == "legacy"  # served through the link
    assert (tree["home"] / "config.yaml").read_text() == "before\n"
    # The failed release is gone, and no stamp was written for it.
    assert [p.name for p in layout.releases.iterdir()] == [f"legacy-{PIN[:12]}"]
    assert tree["stamp"].read_text() == PIN[:12] + "\n"
    assert units.state[rel.AGENT_UNIT] == "active"
    # A legacy release left live triggers a fresh migration attempt next time.
    assert rel.build_trigger(layout, PIN, "3.12") == "the pre-layout release is live"


def test_a_failed_switch_puts_the_legacy_venv_back(tree, monkeypatch) -> None:
    """A rename that fails mid-migration leaves the host exactly as it was."""
    venv = _legacy_venv(tree)
    units = FakeUnits(active={rel.AGENT_UNIT})

    def _boom(*_a: Any) -> None:
        raise OSError("EXDEV: cross-device link")

    monkeypatch.setattr(rel.os, "rename", _boom)
    out = _ensure(tree, FakeTools(), units)
    assert out.status == "activation_failed"
    assert "switch failed" in out.error
    assert venv.is_dir() and not venv.is_symlink()
    assert (venv / "bin" / "python").read_text() == "legacy"
    layout = rel.Layout.for_venv(venv)
    assert not layout.current.exists() and not layout.previous.exists()
    assert not (layout.releases / f"legacy-{PIN[:12]}").exists()
    assert units.state[rel.AGENT_UNIT] == "active"


# ── activation (decision 7) ──────────────────────────────────────────────────


def test_only_units_that_were_active_are_stopped_and_started(tree) -> None:
    """#2823: a gateway the operator stopped or disabled is never started."""
    assert _ensure(tree, FakeTools()).ok
    units = FakeUnits(active={rel.AGENT_UNIT})
    assert _ensure(tree, FakeTools(), units, force=True).ok
    assert units.log == [("stop", rel.AGENT_UNIT), ("start", rel.AGENT_UNIT)]
    assert units.state[rel.GATEWAY_UNIT] == "inactive"


def test_flip_back_on_a_crash_looping_unit_restores_config(tree) -> None:
    first = _ensure(tree, FakeTools())
    (tree["home"] / "config.yaml").write_text("good: 1\n")
    units = FakeUnits(active={rel.AGENT_UNIT, rel.GATEWAY_UNIT}, crash_on_start={rel.GATEWAY_UNIT})
    tools = FakeTools()

    def _run(argv: list[Any], **kw: Any) -> Any:
        if [str(a) for a in argv][-2:] == ["config", "migrate"]:
            (tree["home"] / "config.yaml").write_text("migrated-by-new: 1\n")
        return tools(argv, **kw)

    out = _ensure(tree, tools, units, force=True, run=_run, sleep=units.settle)
    assert out.status == "activation_failed" and out.flipped_back
    assert "restarted" in out.error
    layout = rel.Layout.for_venv(tree["venv"])
    assert os.readlink(layout.current) == f"releases/{first.release_id}"
    assert (tree["home"] / "config.yaml").read_text() == "good: 1\n"
    assert tree["stamp"].read_text() == PIN[:12] + "\n"


def test_systemd_units_use_the_seam_as_hal0_and_systemctl_as_root() -> None:
    from hal0.system.seam import SEAM_BIN

    calls: list[list[str]] = []

    def _run(argv: list[str], **_kw: Any) -> Any:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "ActiveState=active\nNRestarts=2\n", "")

    hal0 = rel.SystemdUnits(run=_run, use_seam=True)
    assert hal0.active(rel.GATEWAY_UNIT) and hal0.restarts(rel.AGENT_UNIT) == 2
    hal0.stop(rel.AGENT_UNIT)
    hal0.start(rel.AGENT_UNIT)
    hal0.stop(rel.GATEWAY_UNIT)
    hal0.start(rel.GATEWAY_UNIT)
    root = rel.SystemdUnits(run=_run, use_seam=False)
    root.start(rel.GATEWAY_UNIT)
    assert calls[0] == [
        "systemctl",
        "show",
        "-p",
        "ActiveState",
        "-p",
        "NRestarts",
        rel.GATEWAY_UNIT,
    ]
    assert calls[2:] == [
        ["sudo", "-n", SEAM_BIN, "stop-agent", "hermes"],
        ["sudo", "-n", SEAM_BIN, "start-agent", "hermes"],
        ["sudo", "-n", SEAM_BIN, "svc-stop", "hermes-gateway"],
        ["sudo", "-n", SEAM_BIN, "svc-start", "hermes-gateway"],
        ["systemctl", "start", rel.GATEWAY_UNIT],
    ]


# ── retention (decision 10) ──────────────────────────────────────────────────


def test_retention_keeps_current_and_previous_only(tree) -> None:
    ids = [_ensure(tree, FakeTools(), force=True).release_id for _ in range(4)]
    layout = rel.Layout.for_venv(tree["venv"])
    assert sorted(p.name for p in layout.releases.iterdir()) == sorted(ids[-2:])
    assert os.readlink(layout.current) == f"releases/{ids[-1]}"
    assert os.readlink(layout.previous) == f"releases/{ids[-2]}"


def test_retention_honours_the_gateway_backstop(tree) -> None:
    first = _ensure(tree, FakeTools()).release_id
    layout = rel.Layout.for_venv(tree["venv"])
    tree["unit"].parent.mkdir(parents=True)
    tree["unit"].write_text(
        f"ExecStart={layout.releases / first}/venv/bin/python -m hermes_cli.main gateway run\n"
    )
    _ensure(tree, FakeTools(), force=True)
    out = _ensure(tree, FakeTools(), force=True)
    assert out.ok
    assert (layout.releases / first).is_dir(), "a release the gateway unit names is kept"
    assert any("sudo hal0 agent install hermes" in w and first in w for w in out.warnings)


def test_prune_runs_uv_cache_prune_and_keeps_an_in_use_interpreter(tree) -> None:
    tools = FakeTools()
    assert _ensure(tree, tools).ok
    assert tools.find(lambda c: c == [UV, "cache", "prune"])
    assert not tools.find(lambda c: c[:3] == [UV, "python", "uninstall"])


def test_retired_interpreter_is_uninstalled_once_nothing_names_it(tree) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    assert _ensure(tree, FakeTools()).ok
    (layout.python_dir / "cpython-3.11.9-linux-x86_64-gnu").mkdir(parents=True)
    tools = FakeTools()
    rel.prune_releases(
        layout, run=tools, uv=UV, gateway_unit=tree["unit"], retired_minors=("3.11",)
    )
    assert tools.find(lambda c: c == [UV, "python", "uninstall", "3.11"])


# ── ADR-0034 decision 2 amendment: uv's trees live inside the release tree ──


def test_a_hal0_build_never_needs_the_root_owned_legacy_python_dir(tree) -> None:
    """Existing hosts have /var/lib/hal0/python root:hal0 755; builds run as hal0.

    The builder points uv's interpreters and cache into the hal0-owned
    release tree, so an unwritable legacy dir cannot fail the build.
    """
    var_lib = tree["root"].parent
    legacy = var_lib / "python"
    (legacy / "cpython-3.12.15-linux-x86_64-gnu").mkdir(parents=True)
    legacy.chmod(0o555)
    stale_cache = var_lib / ".cache" / "uv"
    stale_cache.mkdir(parents=True)
    stale_cache.chmod(0o555)
    try:
        tools = FakeTools()
        out = _ensure(tree, tools)
        assert out.ok, out.error
        layout = rel.Layout.for_venv(tree["venv"])
        uv_envs = [e for c, e in zip(tools.calls, tools.envs, strict=True) if c[0] == UV]
        assert uv_envs
        for env in uv_envs:
            assert env["UV_PYTHON_INSTALL_DIR"] == str(layout.python_dir)
            assert env["UV_CACHE_DIR"] == str(layout.uv_cache)
        assert layout.python_dir.parent == tree["root"]
    finally:
        legacy.chmod(0o755)
        stale_cache.chmod(0o755)


def test_production_uv_paths_sit_inside_the_release_tree() -> None:
    from hal0.agents import hermes_layout as hl

    assert hp.UV_PYTHON_INSTALL_DIR == hl.HERMES_PYTHON_ROOT == hl.HERMES_RELEASE_ROOT / "python"
    assert hp.UV_CACHE_DIR == hl.HERMES_UV_CACHE == hl.HERMES_RELEASE_ROOT / ".cache" / "uv"
    assert rel.Layout.for_venv(hl.HERMES_VENV_PATH).python_dir == hl.HERMES_PYTHON_ROOT


def test_prune_never_touches_the_legacy_python_dir(tree) -> None:
    var_lib = tree["root"].parent
    legacy = var_lib / "python" / "cpython-3.12.15-linux-x86_64-gnu"
    legacy.mkdir(parents=True)
    tools = FakeTools()
    assert _ensure(tree, tools).ok
    assert legacy.is_dir()
    assert not tools.find(lambda c: c[:3] == [UV, "python", "uninstall"])


# ── legacy /var/lib/hal0/python removal (root paths only) ────────────────────


def _legacy_root(tree) -> Path:
    root = tree["root"].parent / "python"
    (root / "cpython-3.12.15").mkdir(parents=True)
    return root


def test_legacy_python_is_kept_while_the_legacy_venv_uses_it(tree) -> None:
    from hal0.agents import hermes_layout as hl

    legacy = _legacy_root(tree)
    venv = tree["venv"]
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(f"home = {legacy}/cpython-3.12.15/bin\nversion = 3.12.15\n")
    assert hl.legacy_python_users(venv, legacy_root=legacy) == [str(venv)]
    assert hl.remove_legacy_python_if_unused(venv, legacy_root=legacy) is False
    assert legacy.is_dir()


def test_legacy_python_is_kept_while_a_release_names_it(tree) -> None:
    from hal0.agents import hermes_layout as hl

    legacy = _legacy_root(tree)
    rdir = tree["root"] / "releases" / "legacy-3ef6bbd20126"
    rdir.mkdir(parents=True)
    (rdir / "release.json").write_text(
        json.dumps({"legacy": True, "python_home": f"{legacy}/cpython-3.12.15/bin"})
    )
    assert hl.remove_legacy_python_if_unused(tree["venv"], legacy_root=legacy) is False


def test_legacy_python_is_kept_while_hal0_hermes_python_points_into_it(tree) -> None:
    from hal0.agents import hermes_layout as hl

    legacy = _legacy_root(tree)
    configured = f"{legacy}/cpython-3.12.15/bin/python3.12"
    assert (
        hl.remove_legacy_python_if_unused(tree["venv"], legacy_root=legacy, configured=configured)
        is False
    )


def test_unused_legacy_python_is_removed(tree) -> None:
    from hal0.agents import hermes_layout as hl

    legacy = _legacy_root(tree)
    assert _ensure(tree, FakeTools()).ok  # a release on /usr/bin/python3.12
    assert hl.remove_legacy_python_if_unused(tree["venv"], legacy_root=legacy) is True
    assert not legacy.exists()


def test_a_symlinked_legacy_python_is_never_followed(tree, tmp_path) -> None:
    from hal0.agents import hermes_layout as hl

    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep").write_text("x")
    link = tree["root"].parent / "python"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    assert hl.remove_legacy_python_if_unused(tree["venv"], legacy_root=link) is False
    assert (target / "keep").exists()


# ── root callers drop to hal0 (§7.4) ─────────────────────────────────────────


def test_a_root_caller_runs_the_builder_as_hal0(tree, monkeypatch) -> None:
    seen: list[list[str]] = []

    cwds: list[Any] = []

    def _fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        seen.append(list(cmd))
        cwds.append(_kw.get("cwd"))
        payload = json.dumps({"status": "activated", "release_id": "r9", "unknown": 1})
        return subprocess.CompletedProcess(cmd, 0, "log line\n" + payload + "\n", None)

    monkeypatch.setattr(rel.os, "geteuid", lambda: 0)
    monkeypatch.setattr(rel, "_hal0_user_exists", lambda: True)
    monkeypatch.setattr(
        rel.shutil, "which", lambda name: "/usr/sbin/runuser" if name == "runuser" else None
    )
    monkeypatch.setattr(rel.subprocess, "run", _fake_run)
    out = rel.ensure_release(caller="converge", venv=tree["venv"], wait=False, units=rel.NoUnits())
    assert out.status == "activated" and out.release_id == "r9"
    [cmd] = seen
    # #2883: the child must not inherit root's /root cwd (uv reads ./uv.toml).
    assert cwds == ["/"]
    assert cmd[:4] == ["runuser", "-u", "hal0", "--"]
    assert cmd[-3:-1] == ["-m", "hal0.agents.hermes_release"]
    kw = json.loads(cmd[-1])
    assert kw["caller"] == "converge" and kw["wait"] is False and kw["no_units"] is True
    assert kw["venv"] == str(tree["venv"])


def test_the_child_entry_point_never_drops_again(tree, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_ensure(**kw: Any) -> rel.ReleaseOutcome:
        calls.append(kw)
        return rel.ReleaseOutcome(status="converged")

    monkeypatch.setattr(rel, "ensure_release", _fake_ensure)
    assert rel.main([json.dumps({"caller": "repair", "no_units": True})]) == 0
    assert calls[0]["drop_root"] is False
    assert isinstance(calls[0]["units"], rel.NoUnits)


def test_a_first_install_that_fails_reverify_is_rebuilt_next_time(tree) -> None:
    calls = {"n": 0}

    def _probe(_py: Path) -> dict[str, Any]:
        calls["n"] += 1
        return {"ok": calls["n"] == 1, "error": "no mcp"}

    out = _ensure(tree, FakeTools(), probe=_probe)
    assert out.status == "activation_failed" and not out.flipped_back
    layout = rel.Layout.for_venv(tree["venv"])
    assert "failed its last re-verify" in rel.build_trigger(layout, PIN, "3.12")
    again = _ensure(tree, FakeTools())
    assert again.status == "activated"
    assert rel.build_trigger(layout, PIN, "3.12") is None


def test_a_flip_back_whose_link_swap_fails_still_restarts_units(tree, monkeypatch) -> None:
    assert _ensure(tree, FakeTools()).ok
    units = FakeUnits(active={rel.AGENT_UNIT})
    real_swap = rel._swap_link
    state = {"n": 0}

    def _swap(link: Path, target: Any) -> None:
        if link.name == "current":
            state["n"] += 1
            if state["n"] == 2:  # the flip-back's swap
                raise OSError("ENOSPC")
        real_swap(link, target)

    monkeypatch.setattr(rel, "_swap_link", _swap)
    n = {"p": 0}

    def _probe(_py: Path) -> dict[str, Any]:
        n["p"] += 1
        return {"ok": n["p"] == 1, "error": "broken"}

    out = _ensure(tree, FakeTools(), units, force=True, probe=_probe)
    assert out.status == "activation_failed"
    assert "flip-back failed" in out.error
    assert units.state[rel.AGENT_UNIT] == "active"


def test_prune_backstop_matches_a_bare_release_dir(tree) -> None:
    first = _ensure(tree, FakeTools()).release_id
    layout = rel.Layout.for_venv(tree["venv"])
    tree["unit"].parent.mkdir(parents=True)
    tree["unit"].write_text(f"WorkingDirectory={layout.releases / first}\n")
    _ensure(tree, FakeTools(), force=True)
    _ensure(tree, FakeTools(), force=True)
    assert (layout.releases / first).is_dir()


# ── review follow-ups on #2851 ───────────────────────────────────────────────


class StopFails(FakeUnits):
    def stop(self, unit: str) -> bool:
        self.log.append(("stop", unit))
        return False


def test_failed_activations_never_leak_releases(tree) -> None:
    """Each failed activation must drop its release, or every update adds one."""
    first = _ensure(tree, FakeTools()).release_id
    layout = rel.Layout.for_venv(tree["venv"])
    for _ in range(3):
        out = _ensure(tree, FakeTools(), StopFails(active={rel.AGENT_UNIT}), force=True)
        assert out.status == "activation_failed" and "could not stop" in out.error
    n = {"p": 0}

    def _probe(_py: Path) -> dict[str, Any]:
        n["p"] += 1
        return {"ok": n["p"] % 2 == 1, "error": "broken"}  # build passes, re-verify fails

    for _ in range(3):
        out = _ensure(tree, FakeTools(), force=True, probe=_probe)
        assert out.status == "activation_failed" and out.flipped_back
    assert [p.name for p in layout.releases.iterdir()] == [first]
    assert os.readlink(layout.current) == f"releases/{first}"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_an_unmovable_pre_layout_venv_refuses_before_building(tree) -> None:
    venv = _legacy_venv(tree)
    venv.chmod(0o555)
    try:
        tools = FakeTools()
        out = _ensure(tree, tools)
        assert out.status == "build_failed"
        assert "not writable" in out.error
        assert "doctor perms --fix" in out.remedy and "reprovision hermes" in out.remedy
        assert not tools.find(lambda c: "fetch" in c), "refused before building"
        assert venv.is_dir() and not venv.is_symlink()
    finally:
        venv.chmod(0o755)


def test_a_missing_venv_link_is_relinked_without_a_rebuild(tree) -> None:
    assert _ensure(tree, FakeTools()).ok
    layout = rel.Layout.for_venv(tree["venv"])
    tree["venv"].unlink()  # a builder that died between the rename and the swap
    assert "does not point at current/venv" in rel.build_trigger(layout, PIN, "3.12")
    tools = FakeTools()
    out = _ensure(tree, tools)
    assert out.status == "converged"
    assert any("relinked" in w for w in out.warnings)
    assert os.readlink(tree["venv"]) == str(layout.venv_target)
    assert not tools.find(lambda c: "fetch" in c)


def test_a_wrong_venv_link_is_relinked(tree, tmp_path) -> None:
    assert _ensure(tree, FakeTools()).ok
    tree["venv"].unlink()
    tree["venv"].symlink_to(tmp_path)
    assert _ensure(tree, FakeTools()).status == "converged"
    assert os.readlink(tree["venv"]) == str(rel.Layout.for_venv(tree["venv"]).venv_target)


def test_units_a_dead_builder_stopped_are_started_again(tree) -> None:
    assert _ensure(tree, FakeTools()).ok
    layout = rel.Layout.for_venv(tree["venv"])
    (layout.root / rel.STOPPED_UNITS).write_text(
        json.dumps({"units": [rel.AGENT_UNIT, "evil.service"]})
    )
    units = FakeUnits()
    out = _ensure(tree, FakeTools(), units)
    assert out.status == "converged"
    assert units.log == [("start", rel.AGENT_UNIT)]  # never a unit outside HERMES_UNITS
    assert not (layout.root / rel.STOPPED_UNITS).exists()


def test_the_stopped_units_record_is_written_before_stopping_and_cleared_after(tree) -> None:
    assert _ensure(tree, FakeTools()).ok
    layout = rel.Layout.for_venv(tree["venv"])
    seen: list[Any] = []

    class Spy(FakeUnits):
        def stop(self, unit: str) -> bool:
            seen.append(json.loads((layout.root / rel.STOPPED_UNITS).read_text())["units"])
            return super().stop(unit)

    assert _ensure(tree, FakeTools(), Spy(active={rel.AGENT_UNIT}), force=True).ok
    assert seen == [[rel.AGENT_UNIT]]
    assert not (layout.root / rel.STOPPED_UNITS).exists()


def test_a_half_initialised_mirror_gets_its_origin_back(tree) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    layout.mirror.mkdir(parents=True)
    (layout.mirror / "HEAD").write_text("ref: refs/heads/main\n")  # init ran, remote add did not

    class NoOrigin(FakeTools):
        def __call__(self, argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
            argv = [str(a) for a in argv]
            if argv[-3:] == ["remote", "get-url", "origin"]:
                self.calls.append(argv)
                self.envs.append({})
                return subprocess.CompletedProcess(argv, 2, "", "error: No such remote 'origin'")
            return super().__call__(argv, **kw)

    tools = NoOrigin()
    assert _ensure(tree, tools).ok
    assert tools.find(lambda c: c[-4:] == ["remote", "add", "origin", URL])
    assert not tools.find(lambda c: "set-url" in c)
    assert tools.find(lambda c: c[-2:] == ["remote.origin.promisor", "true"])


def test_root_drop_forwards_the_wait_printer(tree, monkeypatch) -> None:
    seen: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": "converged"}), None)

    monkeypatch.setattr(rel.os, "geteuid", lambda: 0)
    monkeypatch.setattr(rel, "_hal0_user_exists", lambda: True)
    monkeypatch.setattr(rel.shutil, "which", lambda _n: None)
    monkeypatch.setattr(rel.subprocess, "run", _fake_run)
    rel.ensure_release(caller="provision", venv=tree["venv"], on_wait=lambda _h: None)
    assert json.loads(seen[0][-1])["print_wait"] is True
    assert seen[0][:5] == ["sudo", "-n", "-u", "hal0", "--"]

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        rel,
        "ensure_release",
        lambda **kw: calls.append(kw) or rel.ReleaseOutcome(status="converged"),
    )
    rel.main([json.dumps({"caller": "provision", "print_wait": True})])
    assert callable(calls[0]["on_wait"])


# ── re-review follow-ups (a)-(c) ─────────────────────────────────────────────


def _crashed_mid_migration(tree) -> rel.Layout:
    """The state a builder leaves when it dies after the rename, before ``current``."""
    layout = rel.Layout.for_venv(tree["venv"])
    rdir = layout.releases / f"legacy-{PIN[:12]}"
    (rdir / "venv" / "bin").mkdir(parents=True)
    (rdir / "venv" / "bin" / "python").write_text("legacy")
    (rdir / "release.json").write_text(
        json.dumps({"id": rdir.name, "legacy": True, "ref": PIN[:12], "python_minor": "3.12"})
    )
    (layout.root / rel.STOPPED_UNITS).write_text(json.dumps({"units": [rel.AGENT_UNIT]}))
    assert not tree["venv"].exists() and not layout.current.exists()
    return layout


def test_an_orphaned_pre_layout_release_is_adopted_and_the_migration_retried(tree) -> None:
    layout = _crashed_mid_migration(tree)
    units = FakeUnits()
    out = _ensure(tree, FakeTools(), units)
    assert out.status == "activated", out.error
    assert out.previous_id == f"legacy-{PIN[:12]}"
    assert os.readlink(layout.previous) == f"releases/legacy-{PIN[:12]}"
    assert tree["venv"].is_symlink()
    # The unit the dead builder stopped was started again.
    assert ("start", rel.AGENT_UNIT) in units.log


def test_adoption_alone_restores_the_pre_migration_host(tree) -> None:
    layout = _crashed_mid_migration(tree)
    assert rel.repair_venv_link(layout) is True
    assert os.readlink(layout.current) == f"releases/legacy-{PIN[:12]}"
    assert (tree["venv"] / "bin" / "python").read_text() == "legacy"
    assert rel.build_trigger(layout, PIN, "3.12") == "the pre-layout release is live"


def test_a_never_activated_release_is_not_adopted(tree) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    rdir = layout.releases / f"{PIN[:12]}-20261021T000000Z"
    (rdir / "venv" / "bin").mkdir(parents=True)
    (rdir / "venv" / "bin" / "python").write_text("")
    (rdir / "release.json").write_text(json.dumps({"id": rdir.name, "ref": PIN}))
    assert rel.repair_venv_link(layout) is False
    assert not layout.current.exists()


def test_the_link_is_repaired_before_stranded_units_start(tree) -> None:
    assert _ensure(tree, FakeTools()).ok
    layout = rel.Layout.for_venv(tree["venv"])
    tree["venv"].unlink()
    (layout.root / rel.STOPPED_UNITS).write_text(json.dumps({"units": [rel.AGENT_UNIT]}))
    seen: list[bool] = []

    class Spy(FakeUnits):
        def start(self, unit: str) -> bool:
            seen.append(tree["venv"].is_symlink())
            return super().start(unit)

    assert _ensure(tree, FakeTools(), Spy()).status == "converged"
    assert seen == [True]


@pytest.mark.parametrize("state", ["disabled", "masked", "masked-runtime"])
def test_a_stranded_unit_the_operator_turned_off_stays_off(tree, state: str) -> None:
    """#2823 holds for crash recovery too."""
    assert _ensure(tree, FakeTools()).ok
    layout = rel.Layout.for_venv(tree["venv"])
    (layout.root / rel.STOPPED_UNITS).write_text(
        json.dumps({"units": [rel.AGENT_UNIT, rel.GATEWAY_UNIT]})
    )

    class Off(FakeUnits):
        def enabled_state(self, unit: str) -> str:
            return state if unit == rel.GATEWAY_UNIT else "enabled"

    units = Off()
    _ensure(tree, FakeTools(), units)
    assert units.log == [("start", rel.AGENT_UNIT)]


def test_systemd_units_read_is_enabled_without_privilege() -> None:
    calls: list[list[str]] = []

    def _run(argv: list[str], **_kw: Any) -> Any:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "disabled\n", "")

    assert rel.SystemdUnits(run=_run, use_seam=True).enabled_state(rel.GATEWAY_UNIT) == "disabled"
    assert calls == [["systemctl", "is-enabled", rel.GATEWAY_UNIT]]


# ── PR 3: Python 3.14, chosen per ref (ADR-0032 d9, ADR-0034 d1/d2 amendment) ─


@pytest.fixture
def ref_admits_314(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make PIN's vetted record admit 3.14, as a pin bump to v0.22.2 would."""
    monkeypatch.setitem(hp.VETTED_HERMES_REQUIRES_PYTHON, PIN, ">=3.11,<3.15")


def test_the_target_minor_follows_the_ref(ref_admits_314) -> None:
    assert rel.target_minor(PIN) == "3.14"
    assert rel.target_minor(None) == "3.14" == hp.UV_PYTHON_FALLBACK
    assert hp.hermes_target_minor(PIN, ">=3.11,<3.14") == "3.12"
    assert hp.hermes_target_minor(PIN, ">=3.11,<3.13") == "3.12"
    with pytest.raises(ValueError):
        hp.hermes_target_minor(PIN, ">=3.15")


def test_3ef6bbd2_targets_312_its_requires_python_admits() -> None:
    assert hp.VETTED_HERMES_REQUIRES_PYTHON[PIN] == ">=3.11,<3.14"
    assert rel.target_minor(PIN) == "3.12"


def test_a_312_release_of_a_ref_capped_below_314_never_loops(tree) -> None:
    """PR 3 alone changes nothing on a 3ef6bbd2 host: 3.12 under its lock."""
    assert _ensure(tree, FakeTools(lock=True)).ok
    layout = rel.Layout.for_venv(tree["venv"])
    assert rel.live_release(layout).python_minor == "3.12"
    assert rel.build_trigger(layout, PIN, rel.target_minor(PIN)) is None
    for _ in range(3):
        tools = FakeTools(lock=True)
        assert _ensure(tree, tools).status == "converged"
        assert tools.calls == []


def test_the_minor_trigger_fires_when_the_ref_admits_314(tree, monkeypatch) -> None:
    assert _ensure(tree, FakeTools()).ok  # 3.12, under the 3ef6bbd2 record
    layout = rel.Layout.for_venv(tree["venv"])
    old = rel.live_release(layout).id
    monkeypatch.setitem(hp.VETTED_HERMES_REQUIRES_PYTHON, PIN, ">=3.14")
    assert "interpreter 3.12 differs from target 3.14" in rel.build_trigger(
        layout, PIN, rel.target_minor(PIN)
    )
    seen: list[str] = []
    out = _ensure(
        tree,
        FakeTools(minor="3.14", requires_python=">=3.14"),
        python_resolver=lambda m: seen.append(m) or f"/usr/bin/python{m}",
    )
    assert out.status == "activated" and out.previous_id == old
    assert seen == ["3.14"]
    assert rel.live_release(layout).python_minor == "3.14"
    assert os.readlink(layout.previous) == f"releases/{old}"


def test_a_checkout_that_disagrees_with_its_vetted_record_is_refused(tree) -> None:
    tools = FakeTools(requires_python=">=3.14")  # record says <3.14
    out = _ensure(tree, tools)
    assert out.status == "build_failed"
    assert "VETTED_HERMES_REQUIRES_PYTHON" in out.error
    assert not tools.find(lambda c: c[:2] == [UV, "venv"]), "refused before the venv"


class LockFor312(FakeTools):
    """A checkout whose uv.lock is 3ef6bbd2's: requires-python <3.14."""

    def __call__(self, argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
        out = super().__call__(argv, **kw)
        argv = [str(a) for a in argv]
        if argv[0] == "git" and "worktree" in argv and "add" in argv:
            (Path(argv[-2]) / "uv.lock").write_text(
                'version = 1\nrevision = 3\nrequires-python = ">=3.11, <3.14"\n'
            )
        return out


def test_3ef6bbd2s_lock_serves_its_312_venv(tree) -> None:
    tools = LockFor312()
    assert _ensure(tree, tools).ok
    assert tools.find(lambda c: c[:2] == [UV, "export"])
    data = rel.live_release(rel.Layout.for_venv(tree["venv"])).data
    assert data["install"] == "lock" and data["python_minor"] == "3.12"


def test_the_no_lock_fallback_is_pinned_to_the_commit_date(tree) -> None:
    tools = FakeTools()  # no uv.lock in the checkout
    assert _ensure(tree, tools).ok
    [install] = tools.find(lambda c: c[:3] == [UV, "pip", "install"])
    assert install[install.index("--exclude-newer") + 1] == "2026-07-20T12:00:00+00:00"
    data = rel.live_release(rel.Layout.for_venv(tree["venv"])).data
    assert data["install"] == "editable" and data["exclude_newer"] == "2026-07-20T12:00:00+00:00"


def test_a_lock_that_cannot_serve_the_minor_falls_back_pinned(tree, ref_admits_314) -> None:
    tools = LockFor312(minor="3.14")
    assert _ensure(tree, tools).ok
    assert not tools.find(lambda c: c[:2] == [UV, "export"])
    [install] = tools.find(lambda c: c[:3] == [UV, "pip", "install"])
    assert "--exclude-newer" in install and install[-1].endswith("[web,mcp]")
    data = rel.live_release(rel.Layout.for_venv(tree["venv"])).data
    assert data["install"] == "editable" and data["lock"] is False and data["lock_present"] is True


def test_a_lock_that_serves_314_records_the_lock_path(tree, ref_admits_314) -> None:
    tools = FakeTools(lock=True, minor="3.14")
    assert _ensure(tree, tools).ok
    data = rel.live_release(rel.Layout.for_venv(tree["venv"])).data
    assert data["install"] == "lock" and data["lock"] is True and data["exclude_newer"] is None
    [install] = tools.find(lambda c: c[:3] == [UV, "pip", "install"])
    assert "--exclude-newer" not in install


@pytest.mark.parametrize(
    ("spec", "serves"),
    [(">=3.11, <3.14", False), (">=3.14", True), (">=3.11", True), ("==3.12.*", False)],
)
def test_lock_serves(tmp_path: Path, spec: str, serves: bool) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(f'version = 1\nrequires-python = "{spec}"\n')
    assert rel.lock_serves(lock, "3.14") is serves


def test_lock_without_requires_python_does_not_serve(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n")
    assert rel.lock_serves(lock, "3.14") is False
    assert rel.lock_serves(tmp_path / "absent.lock", None) is False


def test_managed_312_is_uninstalled_only_once_no_kept_release_names_it(tree, monkeypatch) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    managed312 = layout.python_dir / "cpython-3.12.15-linux-x86_64-gnu"
    managed312.mkdir(parents=True)
    py312 = f"{managed312}/bin/python3.12"
    py314 = f"{layout.python_dir}/cpython-3.14.7-linux-x86_64-gnu/bin/python3.14"

    def uninstalls(tools: FakeTools) -> list[list[str]]:
        return tools.find(lambda c: c[:3] == [UV, "python", "uninstall"])

    t = FakeTools()
    assert _ensure(tree, t, python_resolver=lambda _m: py312).ok
    assert uninstalls(t) == []  # the live release uses it
    monkeypatch.setitem(hp.VETTED_HERMES_REQUIRES_PYTHON, PIN, ">=3.14")
    t = FakeTools(minor="3.14", requires_python=">=3.14")
    assert _ensure(tree, t, python_resolver=lambda _m: py314).ok
    assert uninstalls(t) == []  # still kept as previous
    t = FakeTools(minor="3.14", requires_python=">=3.14")
    assert _ensure(tree, t, python_resolver=lambda _m: py314, force=True).ok
    assert uninstalls(t) == [[UV, "python", "uninstall", "3.12"]]


def test_a_configured_314_does_not_pin_the_retired_312(tree, monkeypatch, ref_admits_314) -> None:
    layout = rel.Layout.for_venv(tree["venv"])
    (layout.python_dir / "cpython-3.12.15").mkdir(parents=True)
    monkeypatch.setenv("HAL0_HERMES_PYTHON", f"{layout.python_dir}/cpython-3.14.7/bin/python3.14")
    assert _ensure(tree, FakeTools(minor="3.14")).ok
    tools = FakeTools()
    rel.prune_releases(layout, run=tools, uv=UV, gateway_unit=tree["unit"])
    assert tools.find(lambda c: c == [UV, "python", "uninstall", "3.12"])
    monkeypatch.setenv("HAL0_HERMES_PYTHON", f"{layout.python_dir}/cpython-3.12.15/bin/python3.12")
    tools = FakeTools()
    rel.prune_releases(layout, run=tools, uv=UV, gateway_unit=tree["unit"])
    assert not tools.find(lambda c: c[:3] == [UV, "python", "uninstall"])


# ── PR 4: the pin moves to v0.22.2, which moves hosts to 3.14 ────────────────


class LockFor314(FakeTools):
    """A v0.22.2 checkout: requires-python >=3.11,<3.15 and a uv.lock serving 3.14."""

    def __init__(self, **kw: Any) -> None:
        kw.setdefault("head", NEW_PIN)
        kw.setdefault("minor", "3.14")
        kw.setdefault("requires_python", ">=3.11,<3.15")
        super().__init__(**kw)

    def __call__(self, argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
        out = super().__call__(argv, **kw)
        argv = [str(a) for a in argv]
        if argv[0] == "git" and "worktree" in argv and "add" in argv:
            (Path(argv[-2]) / "uv.lock").write_text(
                'version = 1\nrevision = 3\nrequires-python = ">=3.11, <3.15"\n'
            )
        return out


def test_the_shipped_pin_is_vetted_and_targets_314() -> None:
    assert NEW_PIN in hp.VETTED_HERMES_REFS
    assert hp.VETTED_HERMES_REQUIRES_PYTHON[NEW_PIN] == ">=3.11,<3.15"
    assert rel.target_minor(NEW_PIN) == "3.14"


def test_the_pin_bump_rebuilds_a_312_release_on_the_managed_314_from_the_lock(tree) -> None:
    """`hal0 update` on a 0.19.0 host: the requirements line moves to v0.22.2,
    the build trigger fires, and the new release runs a uv-managed 3.14
    installed from upstream's lock."""
    layout = rel.Layout.for_venv(tree["venv"])
    assert _ensure(tree, LockFor312()).ok  # the 0.19.0 release, on 3.12
    old = rel.live_release(layout)
    assert old.python_minor == "3.12"

    tree["req"].write_text(f"hermes-agent[web,mcp] @ git+{URL}@{NEW_PIN}\n", encoding="utf-8")
    trigger = rel.build_trigger(layout, NEW_PIN, rel.target_minor(NEW_PIN))
    assert trigger is not None
    managed = f"{layout.python_dir}/cpython-3.14.7-linux-x86_64-gnu/bin/python3.14"
    seen: list[str] = []
    tools = LockFor314()
    out = _ensure(tree, tools, python_resolver=lambda m: seen.append(m) or managed)

    assert out.status == "activated" and out.previous_id == old.id
    assert seen == ["3.14"]
    live = rel.live_release(layout)
    assert live.ref == NEW_PIN and live.python_minor == "3.14"
    data = live.data
    assert data["python"].startswith(f"{layout.python_dir}/cpython-3.14")
    assert data["install"] == "lock" and data["lock"] is True
    assert tools.find(lambda c: c[:2] == [UV, "export"])
    assert os.readlink(layout.previous) == f"releases/{old.id}"
    # Converged afterwards: no second rebuild on the next update.
    again = LockFor314()
    assert _ensure(tree, again, python_resolver=lambda _m: managed).status == "converged"
    assert again.calls == []


def test_a_v0222_checkout_that_disagrees_with_its_record_is_refused(tree) -> None:
    tree["req"].write_text(f"hermes-agent[web,mcp] @ git+{URL}@{NEW_PIN}\n", encoding="utf-8")
    out = _ensure(tree, LockFor314(requires_python=">=3.11,<3.14"))
    assert out.status == "build_failed"
    assert "VETTED_HERMES_REQUIRES_PYTHON" in out.error


# ── hal0 update: config keys reach an existing box (#2615 PR 4 follow-up) ───

_OLD_CONFIG = """\
_config_version: 22
agent:
  max_turns: 60
  system_prompt_prelude: You are hal0's agent.
display:
  personality: Hermes
  streaming: true
auxiliary:
  vision: {provider: main}
  web_extract: {provider: custom, model: u}
  session_search: {provider: custom, model: u}
"""


class BootMaintenanceTools(LockFor314):
    """Simulates upstream's boot maintenance: any `hermes` CLI start (here
    `--help` and `config migrate`) rewrites $HERMES_HOME/config.yaml and drops
    a `.bak-<stamp>` beside it, as post_update.step_migrate_config does."""

    def __call__(self, argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
        out = super().__call__(argv, **kw)
        argv = [str(a) for a in argv]
        if argv[0].endswith("/bin/hermes"):
            home = Path((kw.get("env") or {})["HERMES_HOME"])
            cfg = home / "config.yaml"
            if cfg.is_file():
                (home / "config.yaml.bak-20261021T174537Z").write_text(cfg.read_text())
                cfg.write_text(cfg.read_text().replace("personality: Hermes", "personality: ''"))
        return out


class ConfigSnapshotUnits(FakeUnits):
    """Records config.yaml as each unit starts."""

    def __init__(self, home: Path, **kw: Any) -> None:
        super().__init__(**kw)
        self.home = home
        self.seen: list[tuple[str, str]] = []

    def start(self, unit: str) -> bool:
        cfg = self.home / "config.yaml"
        self.seen.append((unit, cfg.read_text() if cfg.is_file() else ""))
        return super().start(unit)


@pytest.fixture
def update_box(tree, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A 0.19.0 box on 3.12 with a pre-#2839 config.yaml, about to `hal0 update`."""
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tree["home"].parent / "no-overrides.yaml")
    assert _ensure(tree, LockFor312()).ok
    (tree["home"] / "config.yaml").write_text(_OLD_CONFIG)
    tree["req"].write_text(f"hermes-agent[web,mcp] @ git+{URL}@{NEW_PIN}\n", encoding="utf-8")
    return tree


def _cfg(home: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load((home / "config.yaml").read_text())


def test_the_build_never_touches_the_live_hermes_home(update_box) -> None:
    """`hermes --help` in verify must not run boot maintenance on the live home."""
    tree = update_box
    tools = BootMaintenanceTools(web="new")
    rel.build_release(
        NEW_PIN,
        layout=rel.Layout.for_venv(tree["venv"]),
        pin=rel.Pin(url=URL, extras=("web", "mcp"), ref=NEW_PIN),
        run=tools,
        probe=OK_PROBE,
        python="/usr/bin/python3.14",
        uv=UV,
        hermes_home=tree["home"],
        minor="3.14",
    )
    assert (tree["home"] / "config.yaml").read_text() == _OLD_CONFIG
    assert not list(tree["home"].glob("config.yaml.bak-*"))
    [i] = [n for n, c in enumerate(tools.calls) if c[-1] == "--help"]
    assert tools.envs[i]["HERMES_HOME"] != str(tree["home"])


def test_update_converge_writes_the_v022_keys_before_the_units_start(update_box) -> None:
    """Regression for the CT150 lane: an update-path converge from a 0.19-shaped
    config ends with sessions.auto_prune false, the persona in
    agent.system_prompt and the retired keys gone, and both units first start
    on that file."""
    tree = update_box
    units = ConfigSnapshotUnits(tree["home"], active={rel.AGENT_UNIT, rel.GATEWAY_UNIT})
    out = _ensure(tree, BootMaintenanceTools(web="new"), units=units)
    assert out.status == "activated", out.error

    cfg = _cfg(tree["home"])
    assert cfg["sessions"]["auto_prune"] is False
    assert cfg["agent"]["system_prompt"] == "You are hal0's agent."
    assert "system_prompt_prelude" not in cfg["agent"]
    assert set(cfg["auxiliary"]) == {"vision"}
    assert cfg["display"]["personality"] == ""
    assert cfg["agent"]["max_turns"] == 60 and cfg["display"]["streaming"] is True

    # Ordering: the stop happened first, and every start saw the keys.
    assert units.log[0][0] == "stop"
    starts = [text for unit, text in units.seen]
    assert starts, "the units were restarted"
    for text in starts:
        import yaml

        started_on = yaml.safe_load(text)
        assert started_on["sessions"]["auto_prune"] is False
        assert "system_prompt_prelude" not in started_on["agent"]


def test_the_flip_back_copy_is_the_config_from_before_the_update(update_box) -> None:
    tree = update_box
    out = _ensure(tree, BootMaintenanceTools(web="new"))
    assert out.ok
    layout = rel.Layout.for_venv(tree["venv"])
    previous = rel.Layout.for_venv(tree["venv"]).previous.resolve()
    [copy] = list(previous.glob("config.yaml.before-*"))
    assert copy.read_text() == _OLD_CONFIG
    assert layout.current.resolve() != previous


def test_an_operator_override_of_auto_prune_still_wins(update_box, monkeypatch) -> None:
    tree = update_box
    overrides = tree["home"].parent / "overrides.yaml"
    overrides.write_text("sessions:\n  auto_prune: true\n")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", overrides)
    assert _ensure(tree, BootMaintenanceTools(web="new")).ok
    assert _cfg(tree["home"])["sessions"]["auto_prune"] is True


def test_an_unwritable_config_never_starts_the_new_release(update_box, monkeypatch) -> None:
    """If hal0 cannot put sessions.auto_prune: false on disk, the v0.22 release
    must not start: flip back to the 0.19.0 release and its config instead."""
    tree = update_box
    layout = rel.Layout.for_venv(tree["venv"])
    old = rel.live_release(layout)

    def boom(_home: Any, **_kw: Any) -> bool:
        raise OSError("read-only file system")

    monkeypatch.setattr(hp, "apply_release_config_invariants", boom)
    units = ConfigSnapshotUnits(tree["home"], active={rel.AGENT_UNIT, rel.GATEWAY_UNIT})
    out = _ensure(tree, BootMaintenanceTools(web="new"), units=units)
    assert out.status == "activation_failed"
    assert "read-only file system" in out.error and out.flipped_back
    assert rel.live_release(layout).id == old.id
    assert (tree["home"] / "config.yaml").read_text() == _OLD_CONFIG
    assert units.seen, "the old release's units are started again"


# ── platform extras: the gateway's messaging SDKs survive a rebuild ──────────

#: The extras v0.22.2 declares that matter here (pyproject.toml at d923a5eb).
_V0222_EXTRAS = (
    "web", "mcp", "telegram", "discord", "slack", "sms", "messaging", "matrix", "all",
    "edge-tts", "exa",
)  # fmt: skip


class PlatformLock(LockFor314):
    """A v0.22.2 checkout whose pyproject declares the platform extras."""

    def __call__(self, argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
        out = super().__call__(argv, **kw)
        argv = [str(a) for a in argv]
        if argv[0] == "git" and "worktree" in argv and "add" in argv:
            extras = "\n".join(f'{e} = ["x"]' for e in _V0222_EXTRAS)
            (Path(argv[-2]) / "pyproject.toml").write_text(
                '[project]\nname = "hermes-agent"\nrequires-python = ">=3.11,<3.15"\n'
                f"[project.optional-dependencies]\n{extras}\n"
            )
        return out


def _on_v0222(tree: dict[str, Path]) -> rel.Layout:
    tree["req"].write_text(f"hermes-agent[web,mcp] @ git+{URL}@{NEW_PIN}\n", encoding="utf-8")
    return rel.Layout.for_venv(tree["venv"])


def _vault(tree: dict[str, Path], **env: str) -> None:
    tree["vault"].parent.mkdir(parents=True, exist_ok=True)
    tree["vault"].write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")


def _site(release: rel.Release) -> Path:
    return release.venv / "lib" / "python3.14" / "site-packages"


def test_a_config_with_telegram_and_discord_installs_those_extras(tree) -> None:
    layout = _on_v0222(tree)
    _vault(tree, TELEGRAM_BOT_TOKEN="123:abc", DISCORD_BOT_TOKEN="xyz")
    tools = PlatformLock()
    out = _ensure(tree, tools)
    assert out.status == "activated", out.error
    assert tools.exported == [["web", "mcp", "discord", "sms", "telegram"]]
    live = rel.live_release(layout)
    assert live.data["extras"] == ["web", "mcp", "discord", "sms", "telegram"]
    assert live.data["platform_extras"] == ["discord", "sms", "telegram"]
    assert {p.name for p in _site(live).iterdir()} == {"telegram", "discord", "aiohttp"}


def test_a_config_yaml_platform_block_counts_and_enabled_false_beats_a_token(tree) -> None:
    _on_v0222(tree)
    (tree["home"] / "config.yaml").write_text(
        "platforms:\n  slack:\n    enabled: true\ngateway:\n  telegram:\n    enabled: false\n",
        encoding="utf-8",
    )
    (tree["home"] / ".env").write_text("TELEGRAM_BOT_TOKEN='123:abc'\nWEBHOOK_ENABLED=true\n")
    tools = PlatformLock()
    assert _ensure(tree, tools).ok
    # slack by its block, the webhook's aiohttp by the env flag, telegram disabled.
    assert tools.exported == [["web", "mcp", "slack", "sms"]]


def test_a_config_with_no_platforms_needs_no_extras() -> None:
    from hal0.agents import hermes_platforms as hpl

    assert hpl.configured_platforms({"model": {"default": "x"}}, [{}]) == []
    assert hpl.extras_for([]) == ()


def test_a_box_with_no_messaging_platforms_adds_only_the_api_servers_aiohttp(tree) -> None:
    """hal0 enables the gateway API server on every box, before the key exists
    on a fresh install, so aiohttp (`sms`) is the only extra beyond web and mcp."""
    layout = _on_v0222(tree)
    (tree["home"] / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")
    tools = PlatformLock()
    assert _ensure(tree, tools).ok
    assert tools.exported == [["web", "mcp", "sms"]]
    live = rel.live_release(layout)
    assert live.data["extras"] == ["web", "mcp", "sms"] and live.data["platform_extras"] == ["sms"]
    assert {p.name for p in _site(live).iterdir()} == {"aiohttp"}


def test_an_explicitly_disabled_api_server_adds_nothing(tree) -> None:
    _on_v0222(tree)
    (tree["home"] / "config.yaml").write_text(
        "platforms:\n  api_server:\n    enabled: false\n", encoding="utf-8"
    )
    tools = PlatformLock()
    assert _ensure(tree, tools).ok
    assert tools.exported == [["web", "mcp"]]


def test_upstream_any_of_gates_and_top_level_blocks() -> None:
    """Weixin enables on either name (gateway/config_env.py:551 at d923a5eb), and a
    top-level `<platform>:` block's `enabled` wins over the nested ones."""
    from hal0.agents import hermes_platforms as hpl

    assert hpl.configured_platforms(None, [{"WEIXIN_TOKEN": "t"}]) == ["weixin"]
    assert hpl.configured_platforms(None, [{"TEAMS_CLIENT_ID": "a"}]) == []
    config = {"telegram": {"enabled": True}, "platforms": {"discord": {"enabled": True}},
              "discord": {"enabled": False}}  # fmt: skip
    assert hpl.configured_platforms(config, [{"DISCORD_BOT_TOKEN": "x"}]) == ["telegram"]
    teams = {"TEAMS_CLIENT_ID": "a", "TEAMS_CLIENT_SECRET": "b", "TEAMS_TENANT_ID": "c"}
    assert hpl.extras_for(hpl.configured_platforms(None, [teams])) == ("teams",)
    assert hpl.extras_for(["wecom_callback"]) == ("sms", "wecom")


def test_a_hal0_caller_reads_the_vaults_key_names_through_the_seam(tmp_path) -> None:
    """hal0-api cannot read the 0600 vault; the seam returns names, never values."""
    from hal0.agents import hermes_platforms as hpl

    vault = tmp_path / "hermes.env"
    vault.write_text("TELEGRAM_BOT_TOKEN=secret\n")
    vault.chmod(0)
    calls: list[list[str]] = []

    def _seam(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "TELEGRAM_BOT_TOKEN\nAPI_SERVER_KEY\n", "")

    try:
        if os.access(vault, os.R_OK):
            pytest.skip("root reads a 0000 file")
        got = hpl.configured_platform_extras(tmp_path, vault, seam="/s/hal0-agentenv", run=_seam)
    finally:
        vault.chmod(0o600)
    assert calls == [["sudo", "-n", "/s/hal0-agentenv", "secret-keys", "hermes"]]
    assert got == ("sms", "telegram")
    # An old seam without the verb, or no vault at all, reads as no keys.
    fail = lambda argv, **_kw: subprocess.CompletedProcess(argv, 2, "", "bad cmd")  # noqa: E731
    assert hpl.read_vault(vault.with_name("absent.env"), seam="/s", run=fail) == {}


def test_a_vault_in_an_unreadable_directory_still_goes_through_the_seam(tmp_path) -> None:
    """The real layout: the vault's directory is 0700 root, so hal0 cannot stat it.

    On Python 3.12 ``Path.exists()`` raises PermissionError there; the hal0
    side must not touch the path beyond ``os.access`` and ask the seam.
    """
    from hal0.agents import hermes_platforms as hpl

    vault_dir = tmp_path / "agents"
    vault_dir.mkdir()
    vault = vault_dir / "hermes.env"
    vault.write_text("TELEGRAM_BOT_TOKEN=secret\n")
    vault_dir.chmod(0)
    seam = lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, "TELEGRAM_BOT_TOKEN\n", "")  # noqa: E731
    try:
        if os.access(vault, os.R_OK):
            pytest.skip("root can read inside a 0000 directory")
        got = hpl.read_vault(vault, seam="/s/hal0-agentenv", run=seam)
    finally:
        vault_dir.chmod(0o700)
    assert got == {"TELEGRAM_BOT_TOKEN": "1"}


def test_a_configured_platform_missing_from_the_live_release_triggers_a_rebuild(tree) -> None:
    """A box already on v0.22.2 without the SDKs (the 1.6.0 update) is rebuilt."""
    layout = _on_v0222(tree)
    managed = "/usr/bin/python3.14"
    assert _ensure(tree, PlatformLock(), python_resolver=lambda _m: managed).ok
    first = rel.live_release(layout)
    _vault(tree, TELEGRAM_BOT_TOKEN="123:abc")
    minor = rel.target_minor(NEW_PIN)
    assert rel.build_trigger(layout, NEW_PIN, minor) is None
    trigger = rel.build_trigger(layout, NEW_PIN, minor, rel.configured_extras())
    assert trigger == "configured platform extras missing from the live release: telegram"
    assert rel.configured_extras() == ("sms", "telegram")

    tools = PlatformLock()
    out = _ensure(tree, tools, python_resolver=lambda _m: managed)
    assert out.status == "activated" and out.reason == trigger
    assert out.previous_id == first.id
    assert rel.live_release(layout).data["platform_extras"] == ["sms", "telegram"]
    again = PlatformLock()
    assert _ensure(tree, again).status == "converged" and again.calls == []


def test_extras_installed_by_hand_count_as_satisfied(tree) -> None:
    """The operator's manual `uv pip install` of the messaging export is enough."""
    layout = _on_v0222(tree)
    assert _ensure(tree, PlatformLock()).ok
    _vault(tree, TELEGRAM_BOT_TOKEN="123:abc", DISCORD_BOT_TOKEN="xyz", API_SERVER_KEY="k" * 43)
    site = _site(rel.live_release(layout))
    site.mkdir(parents=True, exist_ok=True)
    for pkg in ("telegram", "discord", "slack_bolt"):
        (site / pkg).mkdir()
    extras = rel.configured_extras()
    assert extras == ("discord", "sms", "telegram")
    assert rel.build_trigger(layout, NEW_PIN, rel.target_minor(NEW_PIN), extras) is None
    tools = PlatformLock()
    assert _ensure(tree, tools).status == "converged" and tools.calls == []


def test_a_rebuild_carries_the_live_venvs_platform_sdks(tree) -> None:
    """A forced rebuild keeps the hand-installed SDKs even with no visible config
    (a hal0 caller cannot read the root-only vault)."""
    layout = _on_v0222(tree)
    assert _ensure(tree, PlatformLock()).ok
    site = _site(rel.live_release(layout))
    for pkg in ("telegram", "discord"):
        (site / pkg).mkdir()
    tools = PlatformLock()
    assert _ensure(tree, tools, force=True).ok
    assert tools.exported == [["web", "mcp", "discord", "sms", "telegram"]]


def test_the_first_migration_carries_the_pre_layout_venvs_sdks(tree) -> None:
    """1.5 to 1.6: the 0.19 venv's python-telegram-bot, discord.py and aiohttp."""
    _on_v0222(tree)
    venv = _legacy_venv(tree)
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    for pkg in ("telegram", "discord", "aiohttp"):
        (site / pkg).mkdir()
    (site / "slack_sdk").mkdir()  # slack-sdk alone is not slack_bolt: not carried
    tools = PlatformLock()
    assert _ensure(tree, tools).ok
    assert tools.exported == [["web", "mcp", "discord", "sms", "telegram"]]


def test_platform_extras_take_the_lock_and_hash_path_only(tree) -> None:
    _on_v0222(tree)
    _vault(tree, TELEGRAM_BOT_TOKEN="123:abc", SLACK_BOT_TOKEN="xoxb")
    tools = PlatformLock()
    assert _ensure(tree, tools).ok
    [export] = tools.find(lambda c: c[:2] == [UV, "export"])
    assert "--frozen" in export and "--no-emit-project" in export
    assert tools.exported == [["web", "mcp", "slack", "sms", "telegram"]]
    [sync] = tools.find(lambda c: c[:3] == [UV, "pip", "sync"])
    assert "--require-hashes" in sync and sync[-1] == export[export.index("--output-file") + 1]
    # The only `pip install` is the project itself, without dependencies.
    [install] = tools.find(lambda c: c[:3] == [UV, "pip", "install"])
    assert "--no-deps" in install and install[-2:] == ["-e", install[-1]]
    assert "[" not in install[-1]
    assert not tools.find(lambda c: any("telegram" in a and a != "telegram" for a in c))


def test_the_no_lock_fallback_never_adds_platform_extras(tree) -> None:
    """3ef6bbd2 on its editable path resolves from PyPI unhashed: pin extras only,
    and the trigger does not judge such a release, or it would fire forever."""
    _vault(tree, TELEGRAM_BOT_TOKEN="123:abc")
    tools = FakeTools()
    assert _ensure(tree, tools).ok
    [install] = tools.find(lambda c: c[:3] == [UV, "pip", "install"])
    assert install[-1].endswith("[web,mcp]")
    layout = rel.Layout.for_venv(tree["venv"])
    assert rel.live_release(layout).data["platform_extras"] == []
    assert rel.build_trigger(layout, PIN, "3.12", ("telegram",)) is None


def test_an_extra_the_checkout_does_not_declare_is_dropped(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text(
        '[project]\nname = "h"\n[project.optional-dependencies]\nweb = []\ntelegram = []\n'
    )
    extras, applied = rel.release_extras(("web", "mcp"), ["telegram", "discord", "web"], src)
    assert extras == ("web", "mcp", "telegram") and applied == ("telegram",)


def test_a_flip_back_to_a_release_without_the_extras_warns_and_still_triggers(tree) -> None:
    layout = _on_v0222(tree)
    first = _ensure(tree, PlatformLock())
    _vault(tree, DISCORD_BOT_TOKEN="xyz")
    units = FakeUnits(active={rel.GATEWAY_UNIT}, crash_on_start={rel.GATEWAY_UNIT})
    out = _ensure(tree, PlatformLock(), units, sleep=units.settle)
    assert out.status == "activation_failed" and out.flipped_back
    assert rel.live_release(layout).id == first.release_id
    assert any("lacks the configured platform extras discord" in w for w in out.warnings)
    trigger = rel.build_trigger(layout, NEW_PIN, rel.target_minor(NEW_PIN), ("discord",))
    assert trigger and "discord" in trigger


def test_a_root_caller_reads_the_vault_and_hands_the_extras_to_the_hal0_child(
    tree, monkeypatch
) -> None:
    seen: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": "converged"}), None)

    _vault(tree, TELEGRAM_BOT_TOKEN="123:abc")
    monkeypatch.setattr(rel.os, "geteuid", lambda: 0)
    monkeypatch.setattr(rel, "_hal0_user_exists", lambda: True)
    monkeypatch.setattr(rel.shutil, "which", lambda _n: None)
    monkeypatch.setattr(rel.subprocess, "run", _fake_run)
    rel.ensure_release(caller="converge", venv=tree["venv"], units=rel.NoUnits())
    assert json.loads(seen[0][-1])["platform_extras"] == ["sms", "telegram"]


def _lazy_generation(
    tree: dict[str, Path], release: rel.Release, *pkgs: str, extras: tuple[str, ...] = ("discord",)
) -> Path:
    """What upstream's pm leaves after a lazy install: a committed generation
    for the release's checkout under HERMES_HOME/installs (pm/lock.py:269-279)."""
    from hal0.agents import hermes_platforms as hpl

    state = tree["home"] / "installs" / hpl.install_key(release.src)
    gen = state / "environments" / "g1" / "venv"
    site = gen / "lib" / "python3.14" / "site-packages"
    site.mkdir(parents=True)
    (gen / "pyvenv.cfg").write_text("version_info = 3.14.0\n")
    for pkg in pkgs:
        (site / pkg).mkdir()
    (state / "facts.json").write_text(
        json.dumps(
            {"packages": {"venv": {"stamp": "s", "extras": list(extras), "environment": str(gen)}}}
        )
    )
    return gen


def test_a_lazily_installed_generation_triggers_a_rebuild_that_carries_its_sdks(tree) -> None:
    """Hermes boots the release into its own generation (hermes_bootstrap.py:613),
    which drops the pin's extras; the rebuild escapes it with a new install key."""
    from hal0.agents import hermes_platforms as hpl

    layout = _on_v0222(tree)
    assert _ensure(tree, PlatformLock()).ok
    first = rel.live_release(layout)
    gen = _lazy_generation(tree, first, "discord", "aiohttp")
    minor = rel.target_minor(NEW_PIN)
    trigger = rel.build_trigger(layout, NEW_PIN, minor, hermes_home=tree["home"])
    assert trigger and str(gen) in trigger

    tools = PlatformLock()
    out = _ensure(tree, tools)
    assert out.status == "activated" and out.reason == trigger
    assert tools.exported == [["web", "mcp", "discord", "sms"]]
    new = rel.live_release(layout)
    assert hpl.install_key(new.src) != hpl.install_key(first.src)
    assert hpl.committed_generation(tree["home"], new.src) is None
    assert rel.build_trigger(layout, NEW_PIN, minor, hermes_home=tree["home"]) is None


def test_a_generation_record_outside_its_install_is_ignored(tree) -> None:
    from hal0.agents import hermes_platforms as hpl

    layout = _on_v0222(tree)
    assert _ensure(tree, PlatformLock()).ok
    live = rel.live_release(layout)
    _lazy_generation(tree, live)
    facts = tree["home"] / "installs" / hpl.install_key(live.src) / "facts.json"
    facts.write_text(json.dumps({"packages": {"venv": {"environment": str(live.venv)}}}))
    assert hpl.committed_generation(tree["home"], live.src) is None


def test_a_lazily_installed_tts_extra_converges_after_one_rebuild(tree) -> None:
    """#2876: a generation holding a non-platform extra (a TTS engine) is rebuilt
    once, the rebuild carries the extra, and nothing triggers again, including
    on a later rebuild of the release."""
    layout = _on_v0222(tree)
    managed = "/usr/bin/python3.14"
    assert _ensure(tree, PlatformLock(), python_resolver=lambda _m: managed).ok
    first = rel.live_release(layout)
    _lazy_generation(tree, first, extras=("edge-tts",))
    minor = rel.target_minor(NEW_PIN)
    assert rel.build_trigger(layout, NEW_PIN, minor, hermes_home=tree["home"])

    tools = PlatformLock()
    assert _ensure(tree, tools, python_resolver=lambda _m: managed).status == "activated"
    assert tools.exported == [["web", "mcp", "edge-tts", "sms"]]
    assert rel.live_release(layout).data["platform_extras"] == ["edge-tts", "sms"]

    # Converged: the release carries the engine, so no lazy install, no generation.
    assert rel.build_trigger(layout, NEW_PIN, minor, ("sms",), hermes_home=tree["home"]) is None
    again = PlatformLock()
    assert _ensure(tree, again, python_resolver=lambda _m: managed).status == "converged"
    assert again.calls == []
    # A later rebuild (a repair, a pin bump) keeps it: release.json carries it on.
    forced = PlatformLock()
    assert _ensure(tree, forced, force=True, python_resolver=lambda _m: managed).ok
    assert forced.exported == [["web", "mcp", "edge-tts", "sms"]]


def test_matrix_umbrellas_and_undeclared_extras_are_never_carried(tree) -> None:
    layout = _on_v0222(tree)
    assert _ensure(tree, PlatformLock()).ok
    _lazy_generation(
        tree, rel.live_release(layout), extras=("matrix", "all", "exa", "not-declared")
    )
    tools = PlatformLock()
    assert _ensure(tree, tools).ok
    assert tools.exported == [["web", "mcp", "exa", "sms"]]


def test_run_does_not_inherit_the_callers_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2883: uv reads ``./uv.toml``, so a builder run from ``sudo``'s ``/root`` cwd
    died on ``/root/uv.toml`` even with HOME pinned. Unpinned commands must not
    start in the caller's cwd."""
    monkeypatch.chdir(tmp_path)
    proc = rel._run([sys.executable, "-c", "import os; print(os.getcwd())"])
    assert Path(proc.stdout.strip()).resolve() != tmp_path.resolve()
    # An explicit cwd (the git worktree calls) is still honoured.
    proc = rel._run([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
    assert Path(proc.stdout.strip()).resolve() == tmp_path.resolve()


# ── #2894: a gateway unit on upstream's release-local launcher shim ──────────

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hermes"
_GATEWAY_UID = 998


class BootstrapLock(PlatformLock):
    """A v0.22.2 checkout that ships ``hermes_bootstrap``, which hal0's launcher imports."""

    def __call__(self, argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
        out = super().__call__(argv, **kw)
        argv = [str(a) for a in argv]
        if argv[0] == "git" and "worktree" in argv and "add" in argv:
            (Path(argv[-2]) / "hermes_bootstrap.py").write_text("", encoding="utf-8")
        return out


class UnitFileUnits(FakeUnits):
    """systemd running the gateway from the unit file on disk.

    Upstream's shim hard-codes the release it was written in, so on a new
    release it exits on every start ("no dependency environment is committed
    for this install") and systemd keeps restarting it.
    """

    def __init__(self, unit: Path) -> None:
        super().__init__(active={rel.AGENT_UNIT, rel.GATEWAY_UNIT})
        self.unit = unit

    def settle(self, _seconds: float) -> None:
        if self.state[rel.GATEWAY_UNIT] == "active" and "/src/.hermes/bin/hermes" in (
            self.unit.read_text()
        ):
            self.restart_count[rel.GATEWAY_UNIT] += 1


class RootVerb:
    """``sudo -n hal0-update hermes-gateway``: the real root primitive, on the tmp unit."""

    def __init__(self, unit: Path, units: FakeUnits, linger_dir: Path) -> None:
        self.unit = unit
        self.units = units
        self.linger_dir = linger_dir
        self.calls = 0

    def _systemctl(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess:
        if argv[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0, "enabled\n", "")
        if argv[:2] == ["systemctl", "is-active"]:
            rc = 0 if self.units.active(rel.GATEWAY_UNIT) else 3
            return subprocess.CompletedProcess(argv, rc, "", "")
        if argv[:2] == ["loginctl", "enable-linger"]:
            (self.linger_dir / argv[2]).touch()
        self.units.log.append(("root", " ".join(argv)))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def hermes_gateway(self) -> dict[str, Any]:
        from hal0.agents import hermes_gateway_regen as regen

        self.calls += 1
        return regen.regenerate_gateway_unit(
            unit_path=self.unit,
            linger_dir=str(self.linger_dir),
            run=self._systemctl,
            uid_of=lambda _name: _GATEWAY_UID,
            which=lambda name: f"/usr/bin/{name}",
        ).as_dict()


def test_activation_regenerates_an_upstream_shim_unit_before_the_gateway_starts(
    tree, monkeypatch
) -> None:
    """#2894: the unit `hermes gateway migrate --multiplex` wrote runs upstream's
    release-local shim. Activation must put hal0's launcher in place through the
    root verb, with linger, before the new release's gateway starts, so the
    re-verify passes and nothing flips back."""
    from hal0.agents import hermes_gateway_regen as regen
    from hal0.updater import privileged

    monkeypatch.setattr(regen.os, "chown", lambda *_a: None)
    layout = _on_v0222(tree)
    assert _ensure(tree, BootstrapLock(), drop_root=False).ok
    first = rel.live_release(layout)
    # The lazy generation that keeps asking for a rebuild on that box.
    _lazy_generation(tree, first)

    unit = tree["unit"]
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text((_FIXTURES / "gateway-unit-upstream-shim.service").read_text())
    wants = unit.parent / "multi-user.target.wants"
    wants.mkdir()
    (wants / unit.name).symlink_to(unit)
    linger_dir = tree["root"] / "linger"
    linger_dir.mkdir()

    units = UnitFileUnits(unit)
    verb = RootVerb(unit, units, linger_dir)
    monkeypatch.setattr(privileged, "UpdateSeam", lambda job_id=None: verb)

    out = _ensure(tree, BootstrapLock(), units, sleep=units.settle, drop_root=False)

    assert out.status == "activated", out.error
    assert not out.flipped_back
    assert rel.live_release(layout).id == out.release_id != first.id
    assert verb.calls == 1
    assert unit.read_text() == regen.render_unit(_GATEWAY_UID)
    assert (linger_dir / "hal0").exists()
    assert rel.GATEWAY_HEALED_NOTE in out.warnings
    # Rewritten while stopped, before the first start on the new release.
    log = units.log
    rewrite = log.index(("root", "systemctl daemon-reload"))
    assert log.index(("stop", rel.GATEWAY_UNIT)) < rewrite
    assert rewrite < log.index(("start", rel.GATEWAY_UNIT))
    assert ("root", "systemctl restart hermes-gateway.service") not in log


def test_activation_leaves_a_hal0_unit_alone_without_sudo(tree, monkeypatch) -> None:
    """A unit already on hal0's launcher, linger on: the probe answers, no sudo."""
    from hal0.agents import hermes_gateway_regen as regen
    from hal0.updater import privileged

    layout = _on_v0222(tree)
    assert _ensure(tree, BootstrapLock(), drop_root=False).ok
    _lazy_generation(tree, rel.live_release(layout))
    unit = tree["unit"]
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(regen.render_unit(_GATEWAY_UID))
    units = UnitFileUnits(unit)
    calls: list[str] = []
    monkeypatch.setattr(privileged, "UpdateSeam", lambda job_id=None: calls.append("seam"))
    monkeypatch.setattr(
        "hal0.system.linger.gateway_unit_regen_reason",
        lambda **kw: regen.unit_stale_reason(Path(kw["unit"]).read_text()),
    )
    out = _ensure(tree, BootstrapLock(), units, sleep=units.settle, drop_root=False)
    assert out.status == "activated", out.error
    assert calls == []
    assert unit.read_text() == regen.render_unit(_GATEWAY_UID)


def test_prune_keeps_the_release_a_suffixed_gateway_unit_names(tree) -> None:
    """#2943: until the gateway is moved off upstream's suffixed unit, the release
    baked into it must survive the prune, or the gateway cannot restart."""
    from hal0.agents.hermes_gateway_regen import HASHED_UNIT

    first = _ensure(tree, FakeTools()).release_id
    layout = rel.Layout.for_venv(tree["venv"])
    tree["unit"].parent.mkdir(parents=True)
    (tree["unit"].parent / HASHED_UNIT).write_text(
        "[Service]\nUser=hal0\n"
        f"ExecStart={layout.releases / first}/venv/bin/python -m hermes_cli.main gateway run\n"
        'Environment="HERMES_HOME=/var/lib/hal0/.hermes"\n'
    )
    _ensure(tree, FakeTools(), force=True)
    _ensure(tree, FakeTools(), force=True)
    assert (layout.releases / first).is_dir()


def _flapping_gateway_heal(units: FakeUnits):  # type: ignore[no-untyped-def]
    """A heal that changes nothing, while the gateway flaps to active meanwhile."""

    def heal(_layout: Any) -> None:
        units.state[rel.GATEWAY_UNIT] = "active"
        return None

    return heal


def test_activation_never_adopts_a_crash_looping_gateway_on_an_upgraded_box(tree) -> None:
    """#2946 review: no suffixed unit before the heal, so a gateway that reads active
    after it (Restart=always flapping) is not taken into the settle window."""
    _ensure(tree, FakeTools())
    units = FakeUnits(active={rel.AGENT_UNIT})
    out = _ensure(tree, FakeTools(), units, force=True, heal_gateway=_flapping_gateway_heal(units))
    assert out.status != "activation_failed", out.error
    assert ("start", rel.GATEWAY_UNIT) not in units.log


def test_activation_adopts_a_gateway_the_heal_moved_off_the_suffixed_unit(
    tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.agents.hermes_gateway_regen import HASHED_UNIT

    _ensure(tree, FakeTools())
    tree["unit"].parent.mkdir(parents=True, exist_ok=True)
    suffixed = tree["unit"].parent / HASHED_UNIT
    suffixed.write_text('[Service]\nUser=hal0\nEnvironment="HERMES_HOME=/var/lib/hal0/.hermes"\n')
    units = FakeUnits(active={rel.AGENT_UNIT})

    def heal(_layout: Any) -> None:
        suffixed.unlink()
        units.state[rel.GATEWAY_UNIT] = "active"
        return None

    journal: list[list[str]] = []
    real_record = rel._record_stopped_units

    def spy(layout: Any, units_: list[str]) -> None:
        journal.append(list(units_))
        real_record(layout, units_)

    monkeypatch.setattr(rel, "_record_stopped_units", spy)
    _ensure(tree, FakeTools(), units, force=True, heal_gateway=heal)
    assert ("start", rel.GATEWAY_UNIT) in units.log
    # the crash-recovery journal is rewritten with the adopted gateway
    assert journal and rel.GATEWAY_UNIT in journal[-1]
