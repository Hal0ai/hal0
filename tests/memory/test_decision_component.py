"""#3757 — the opt-in ``memory-decision`` component (hal0-hs-decide).

Every privileged, network and process step is faked: the runner (venv, pip,
model pull, and the hal0-dropped file operations on the root path), the seam
(unit write, systemctl), the /health probe and the engine drop-in reconcile.
Nothing here builds a venv or downloads anything.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.config.schema import MemoryDecisionConfig
from hal0.memory import decision_component as dc
from hal0.memory import decision_fs
from hal0.system.seam import SystemCtlSeam, render_decision_unit

REV = dc.MODEL_REVISION
_REAL_SET_PROVIDER = dc.set_provider


class FakeSeam:
    def __init__(self, events: list[str] | None = None, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.events = events if events is not None else []
        self.fail_on = fail_on

    def systemctl(self, *args: str, check: bool = True, timeout: float | None = None):
        self.calls.append(tuple(args[1:]))
        self.events.append(f"systemctl {args[1]}")
        if self.fail_on and args[1] == self.fail_on:
            raise subprocess.CalledProcessError(1, list(args), stderr="refused")
        return subprocess.CompletedProcess(list(args), 0, stdout="active\n", stderr="")

    def write_decision_unit(self, *, path: Path, timeout: float | None = None, **params: Any):
        self.calls.append(("write-unit", str(params["port"])))
        # The direct branch: writes the unit and keeps a hand-written one.
        SystemCtlSeam(is_hal0_user=lambda: False).write_decision_unit(**params, path=path)

    def remove_decision_unit(self, *, path: Path, timeout: float | None = None):
        self.calls.append(("remove-unit",))
        self.events.append("remove-unit")
        path.unlink(missing_ok=True)


class FakeRunner:
    """Answers the build steps the way a working box does; ``fail`` names one to fail.

    ``plant`` maps a step name to a callable run after that step (to plant
    symlinks the way a compromised hal0 account could). The root path's
    ``python -m hal0.memory.decision_fs`` children are executed for real.
    """

    def __init__(self, fail: str | None = None, *, torch_out: str = "2.15.0+cpu\n") -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []
        self.fail = fail
        self.torch_out = torch_out
        self.plant: dict[str, Any] = {}
        self.in_fs_child = False

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        self.envs.append(kw.get("env"))
        self.kwargs = getattr(self, "kwargs", [])
        self.kwargs.append(kw)
        if "hal0.memory.decision_fs" in argv:
            i = argv.index("hal0.memory.decision_fs")
            import io
            import sys

            old = sys.stdin
            sys.stdin = io.TextIOWrapper(io.BytesIO(kw.get("input") or b""))
            self.in_fs_child = True
            try:
                rc = decision_fs.main(argv[i + 1 :])
            finally:
                self.in_fs_child = False
                sys.stdin = old
            return subprocess.CompletedProcess(argv, rc, stdout=b"", stderr=b"")
        joined = " ".join(argv)
        step = (
            "venv"
            if "-m" in argv and "venv" in argv
            else "torch-version"
            if "m.version('torch')" in joined
            else "pull"
            if "snapshot_download" in joined
            else "import"
            if "hindsight_decision" in joined and "-c" in argv
            else "torch"
            if "--index-url" in argv
            else "pip"
        )
        if step == self.fail:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=f"{step} broke")
        out = ""
        if step == "venv":
            py = Path(argv[argv.index("venv") + 1]) / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")
        if step == "pull":
            (dc.snapshot_dir() / "serving").mkdir(parents=True, exist_ok=True)
            out = str(dc.snapshot_dir())
        if step == "torch-version":
            out = self.torch_out
        if step in self.plant:
            self.plant[step]()
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


@pytest.fixture
def box(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "decision": MemoryDecisionConfig(provider="local"),
        "reconciled": 0,
        "providers": [],
        "events": [],
        "unit": tmp_path / "hal0-hs-decide.service",
        "outside": tmp_path / "outside.txt",
    }
    state["outside"].write_text("untouched\n")
    monkeypatch.setattr(dc, "_decision_config", lambda: state["decision"])
    monkeypatch.setattr(dc, "_MIN_FREE_BYTES", 0)
    monkeypatch.setattr(dc, "_dropping", lambda: False)

    def _reconcile(_seam: Any, _fallback: Any = None) -> dict[str, Any]:
        state["reconciled"] += 1
        state["events"].append("reconcile")
        return {"status": "applied"}

    def _set_provider(provider: str, **_kw: Any) -> bool:
        state["providers"].append(provider)
        state["events"].append(f"provider {provider}")
        return True

    monkeypatch.setattr(dc, "_reconcile_engine", _reconcile)
    monkeypatch.setattr(dc, "set_provider", _set_provider)
    return state


def _converge(box: dict[str, Any], **kw: Any) -> dict[str, Any]:
    kw.setdefault("seam", FakeSeam(box["events"]))
    kw.setdefault("runner", FakeRunner())
    kw.setdefault("http_get", lambda _url: {"status": "ok"})
    kw.setdefault("sleep", lambda _s: None)
    return dc.converge_memory_decision(unit_path=box["unit"], **kw)


def _install(box: dict[str, Any]) -> None:
    assert _converge(box, install=True)["status"] == "installed"


def _hand_install(box: dict[str, Any], *, port: int = 9178) -> str:
    """A unit + venv laid down by hand: no hal0 build stamp."""
    text = (
        "[Service]\nUser=hal0\nEnvironment=HS_DECIDE_THREADS=8\n"
        "ExecStart=/x/.venv/bin/python /x/hs_decide_serve.py --model "
        f"/x/snapshots/{'b' * 40} --port {port} --max-tokens 8192\n"
    )
    box["unit"].write_text(text)
    py = dc.venv_dir() / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    return text


# ── pins and install state ───────────────────────────────────────────────────


def test_pins_are_exact_and_the_label_moves_with_any_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert REV == "e2c789026dfd1f7608cafb2ef6a9bb31397af802"
    assert dc.TORCH_INDEX_URL == "https://download.pytorch.org/whl/cpu"
    assert dc.TORCH_VERSION == "2.15.0"
    assert all("==" in p for p in dc.PACKAGES)
    assert "transformers==5.23.0" in dc.PACKAGES
    before = dc.pinned_label()
    monkeypatch.setattr(dc, "TORCH_VERSION", "2.15.1")
    assert dc.pinned_label() != before
    monkeypatch.undo()
    monkeypatch.setattr(dc, "PACKAGES", ("fastapi==0.1",))
    assert dc.pinned_label() != before


def test_install_state_covers_hand_installs(box: dict[str, Any]) -> None:
    assert dc.install_state(unit_path=box["unit"]) == (dc.NOT_INSTALLED, None)
    _hand_install(box)
    assert dc.install_state(unit_path=box["unit"]) == (dc.UNMANAGED, None)
    assert dc.installed_label(unit_path=box["unit"]) == "unmanaged"


def test_managed_after_install(box: dict[str, Any]) -> None:
    _install(box)
    assert dc.install_state(unit_path=box["unit"]) == (dc.MANAGED, dc.pinned_label())


# ── opt-in ───────────────────────────────────────────────────────────────────


def test_catalog_pass_never_installs(box: dict[str, Any]) -> None:
    runner, seam = FakeRunner(), FakeSeam()
    result = _converge(box, runner=runner, seam=seam)
    assert result["status"] == "skipped" and "hal0 memory decision install" in result["reason"]
    assert runner.calls == [] and seam.calls == [] and box["providers"] == []


def test_catalog_pass_leaves_a_hand_install_alone_and_says_install_adopts_it(
    box: dict[str, Any],
) -> None:
    _hand_install(box)
    runner, seam = FakeRunner(), FakeSeam()
    result = _converge(box, runner=runner, seam=seam)
    assert result["status"] == "unmanaged"
    assert "hal0 memory decision install" in result["note"]
    assert runner.calls == [] and seam.calls == []


def test_diagnose_reports_without_touching_anything(box: dict[str, Any]) -> None:
    _install(box)
    runner, seam = FakeRunner(), FakeSeam()
    assert _converge(box, apply=False, runner=runner, seam=seam)["status"] == "converged"
    assert runner.calls == [] and seam.calls == []


# ── install ──────────────────────────────────────────────────────────────────


def test_fresh_install_builds_pulls_renders_starts_and_turns_the_lane_on(
    box: dict[str, Any],
) -> None:
    box["decision"] = MemoryDecisionConfig(provider="off")
    runner, seam = FakeRunner(), FakeSeam(box["events"])
    result = _converge(box, install=True, runner=runner, seam=seam)
    assert result["status"] == "installed" and result["to"] == dc.pinned_label()

    torch = next(c for c in runner.calls if "--index-url" in c)
    assert torch[torch.index("--index-url") + 1] == dc.TORCH_INDEX_URL
    assert "torch==2.15.0" in torch
    # The CPU index hosts the torch wheel only: its dependencies are pinned to
    # versions that index does not carry, so they come from the PyPI step.
    assert "--no-deps" in torch
    pip = next(c for c in runner.calls if "transformers==5.23.0" in c)
    assert "--index-url" not in pip and "--extra-index-url" not in pip
    assert "torch==2.15.0" in pip and "--no-deps" not in pip
    pips = [c for c in runner.calls if "pip" in c]
    assert len(pips) == 3
    for call in pips:
        assert {"--isolated", "--no-input", "--disable-pip-version-check"} <= set(call)
        assert call[call.index("-c") + 1] == str(dc.CONSTRAINTS_PATH)
    assert "--no-build-isolation" in pips[-1] and pips[-1][-1].endswith("/serving")
    pull_i = next(i for i, c in enumerate(runner.calls) if "snapshot_download" in " ".join(c))
    assert runner.calls[pull_i][-2:] == [dc.MODEL_REPO, REV]
    assert runner.envs[pull_i]["HF_HOME"] == str(dc.hf_home())

    assert (dc.venv_dir() / "bin" / "python").exists()
    assert not dc.venv_dir().with_name(".venv.new").exists()
    assert not dc.venv_dir().with_name(".venv.prev").exists()
    assert dc.server_script_path().read_bytes() == dc.packaged_server_script().read_bytes()

    unit = box["unit"].read_text()
    assert f"snapshots/{REV} " in unit and "--port 9178 --threads 8 --max-tokens 4096" in unit
    assert ("enable", "hal0-hs-decide.service") in seam.calls
    # The converge turns the lane on itself, then re-points the engine.
    assert box["providers"] == ["local"]
    assert box["events"][-2:] == ["provider local", "reconcile"]


def test_a_catalog_reconverge_never_writes_the_provider(box: dict[str, Any]) -> None:
    _install(box)
    box["providers"].clear()
    box["decision"] = MemoryDecisionConfig(provider="off")
    result = _converge(box)
    assert result["status"] == "converged" and box["providers"] == []
    assert "hal0 memory decision enable" in result["note"]


def test_reconverge_with_nothing_changed_only_starts(box: dict[str, Any]) -> None:
    _install(box)
    runner, seam = FakeRunner(), FakeSeam()
    assert _converge(box, runner=runner, seam=seam)["status"] == "converged"
    assert runner.calls == []
    assert ("start", "hal0-hs-decide.service") in seam.calls
    assert not any(c[0] in ("restart", "write-unit") for c in seam.calls)


def test_a_pin_change_rebuilds(box: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _install(box)
    monkeypatch.setattr(dc, "PACKAGES", (*dc.PACKAGES, "safetensors==0.7.0"))
    result = _converge(box)
    assert result["status"] == "upgraded"
    assert dc.install_state(unit_path=box["unit"]) == (dc.MANAGED, dc.pinned_label())


def test_install_adopts_a_hand_install(box: dict[str, Any]) -> None:
    _hand_install(box)
    result = _converge(box, install=True)
    assert result["status"] == "upgraded" and result["adopted"] is True
    assert dc.install_state(unit_path=box["unit"])[0] == dc.MANAGED
    assert f"snapshots/{REV} " in box["unit"].read_text()


@pytest.mark.parametrize("out", ["2.16.0+cpu\n", "2.15.0\nimport os", "../../etc/passwd"])
def test_an_unexpected_torch_probe_aborts_the_build(box: dict[str, Any], out: str) -> None:
    result = _converge(box, install=True, runner=FakeRunner(torch_out=out))
    assert result["status"] == "build_failed" and "torch probe" in result["error"]
    assert not box["unit"].exists()


def test_the_whole_converge_is_budgeted(box: dict[str, Any]) -> None:
    result = _converge(box, install=True, budget_s=0)
    assert result["status"] == "build_failed" and "budget" in result["error"]


def test_a_second_converge_refuses_while_one_holds_the_lock(box: dict[str, Any]) -> None:
    dc.decision_dir().mkdir(parents=True)
    fd = os.open(dc.decision_dir(), os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        runner = FakeRunner()
        result = _converge(box, install=True, runner=runner)
    finally:
        os.close(fd)
    assert result["status"] == "busy" and "another" in result["error"]
    assert runner.calls == []


def test_cli_wait_covers_the_converge_budget() -> None:
    from hal0.cli import update_commands

    assert dc.CLI_WAIT_S > dc.CONVERGE_BUDGET_S
    assert update_commands._CONVERGE_POLL_TIMEOUT_S["memory-decision"] == dc.CLI_WAIT_S


# ── hal0-owned paths: no write follows a planted symlink ─────────────────────


def _plant(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(target)


def test_planted_symlinks_are_replaced_never_written_through(box: dict[str, Any]) -> None:
    new = dc.venv_dir().with_name(".venv.new")
    outside = box["outside"]
    runner = FakeRunner()
    runner.plant["import"] = lambda: _plant(new / "hal0-build.json", outside)
    _plant(dc.server_script_path(), outside)
    _plant(dc.server_script_path().with_name(".hs_decide_serve.py.tmp"), outside)

    assert _converge(box, install=True, runner=runner)["status"] == "installed"
    assert outside.read_text() == "untouched\n"
    for path in (
        dc.venv_dir() / "hal0-build.json",
        dc.server_script_path(),
    ):
        assert path.is_file() and not path.is_symlink(), path


def test_a_symlinked_parent_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(decision_fs.UnsafePath):
        decision_fs.write_bytes(tmp_path / "link" / "f", b"x")
    assert not (real / "f").exists()


def test_as_root_every_hal0_owned_write_runs_dropped_to_hal0(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    monkeypatch.setattr(dc, "_dropping", lambda: True)
    monkeypatch.setattr(dc, "as_hal0_argv", lambda argv: ["runuser", "-u", "hal0", "--", *argv])
    real_write = decision_fs.write_bytes

    def guarded(*a: Any, **k: Any) -> None:
        assert runner.in_fs_child, "root wrote under a hal0-owned directory itself"
        real_write(*a, **k)

    monkeypatch.setattr(decision_fs, "write_bytes", guarded)
    assert _converge(box, install=True, runner=runner)["status"] == "installed"
    assert runner.calls and all(c[:4] == ["runuser", "-u", "hal0", "--"] for c in runner.calls)
    fs_ops = [
        c[c.index("hal0.memory.decision_fs") + 1]
        for c in runner.calls
        if "-m" in c and "hal0.memory.decision_fs" in c
    ]
    assert {"write", "rename", "mkdir", "retarget"} <= set(fs_ops)


# ── failure is transactional ─────────────────────────────────────────────────


@pytest.mark.parametrize("step", ["venv", "torch", "pip", "pull", "import"])
def test_a_failed_fresh_build_leaves_nothing(box: dict[str, Any], step: str) -> None:
    seam = FakeSeam()
    result = _converge(box, install=True, runner=FakeRunner(fail=step), seam=seam)
    assert result["status"] == "build_failed" and result["lane"] == "off"
    assert not box["unit"].exists() and seam.calls == []
    assert not dc.venv_dir().exists()
    assert not dc.venv_dir().with_name(".venv.new").exists()


@pytest.mark.parametrize("fail_on", ["health", "enable"])
def test_a_failed_fresh_install_leaves_no_unit_and_the_lane_off(
    box: dict[str, Any], fail_on: str
) -> None:
    seam = FakeSeam(box["events"], fail_on="enable" if fail_on == "enable" else None)
    http = (lambda _u: None) if fail_on == "health" else (lambda _u: {"status": "ok"})
    result = _converge(box, install=True, seam=seam, http_get=http)
    assert result["status"] == "build_failed" and result["lane"] == "off"
    assert not box["unit"].exists() and ("remove-unit",) in seam.calls
    assert ("disable", "hal0-hs-decide.service") in seam.calls
    assert box["providers"] == ["off"]


def test_a_failed_rebuild_restores_venv_script_and_unit_and_checks_health(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(box)
    old_unit = box["unit"].read_text()
    old_script = dc.server_script_path().read_bytes()
    old_label = dc.pinned_label()
    monkeypatch.setattr(dc, "PACKAGES", (*dc.PACKAGES, "safetensors==0.7.0"))
    new_script = tmp_path / "serve.py"
    new_script.write_bytes(b"# a new server\n")
    monkeypatch.setattr(dc, "packaged_server_script", lambda: new_script)
    box["decision"] = MemoryDecisionConfig(provider="local", port=9300)

    seen: list[str] = []

    def http(url: str) -> dict[str, Any] | None:
        seen.append(url)
        return {"status": "ok"} if ":9178/" in url else None

    seam = FakeSeam()
    result = _converge(box, seam=seam, http_get=http)
    assert result["status"] == "rolled_back" and result["restored"] is True
    assert box["unit"].read_text() == old_unit
    assert dc.server_script_path().read_bytes() == old_script
    assert dc._read_build_stamp(dc.venv_dir())["label"] == old_label
    assert seen[-1] == "http://127.0.0.1:9178/health"
    assert ("restart", "hal0-hs-decide.service") in seam.calls


def test_a_health_failure_with_no_rebuild_restores_the_changed_unit(
    box: dict[str, Any],
) -> None:
    _install(box)
    old_unit = box["unit"].read_text()
    box["decision"] = MemoryDecisionConfig(provider="local", threads=4)
    calls = {"n": 0}

    def http(_url: str) -> dict[str, Any] | None:
        calls["n"] += 1
        return None if calls["n"] <= 81 else {"status": "ok"}  # new unit never answers

    result = _converge(box, http_get=http)
    assert result["status"] == "rolled_back"
    assert box["unit"].read_text() == old_unit


def test_a_rollback_that_does_not_come_back_is_reported_as_a_failure(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(box)
    monkeypatch.setattr(dc, "PACKAGES", (*dc.PACKAGES, "safetensors==0.7.0"))
    result = _converge(box, http_get=lambda _u: None)
    assert result["status"] == "build_failed" and result["restored"] is False
    assert "does not answer" in result["error"]


def test_a_failed_adoption_puts_the_hand_unit_back(box: dict[str, Any]) -> None:
    _hand_install(box, port=9200)
    result = _converge(
        box, install=True, http_get=lambda u: {"status": "ok"} if ":9200/" in u else None
    )
    assert result["status"] == "rolled_back"
    restored = box["unit"].read_text()
    assert f"snapshots/{'b' * 40} " in restored and "--port 9200 " in restored


def test_parse_unit_params_reads_both_shapes() -> None:
    ours = render_decision_unit(revision=REV, threads=4, port=9300, max_tokens=2048)
    assert dc.parse_unit_params(ours) == {
        "revision": REV, "threads": 4, "port": 9300, "max_tokens": 2048,
    }  # fmt: skip
    assert dc.parse_unit_params("[Service]\nExecStart=/bin/true\n") is None


# ── remove ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("purge", [False, True])
def test_remove_turns_the_lane_off_first_then_deletes(box: dict[str, Any], purge: bool) -> None:
    _install(box)
    box["events"].clear()
    seam = FakeSeam(box["events"])
    result = _converge(box, action="remove", purge_model=purge, seam=seam)
    assert result["status"] == "removed" and result["model_kept"] is (not purge)
    assert box["events"][:3] == ["provider off", "reconcile", "systemctl stop"]
    assert not box["unit"].exists() and not dc.venv_dir().exists()
    assert dc.snapshot_dir().exists() is (not purge)


def test_remove_still_reconciles_when_teardown_fails(box: dict[str, Any]) -> None:
    _install(box)
    box["reconciled"] = 0

    class Broken(FakeSeam):
        def remove_decision_unit(self, **_kw: Any) -> None:
            raise subprocess.CalledProcessError(64, ["x"], stderr="bad cmd")

    result = _converge(box, action="remove", seam=Broken())
    assert result["status"] == "build_failed"
    assert box["reconciled"] == 2 and box["providers"][-1] == "off"


# ── provider writes ──────────────────────────────────────────────────────────


def _toml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "hal0.toml"
    path.write_text(body)
    return path


def test_set_provider_keeps_the_rest_of_the_file(tmp_hal0_home: str, tmp_path: Path) -> None:
    toml = _toml(tmp_path, '[memory]\nbank = "x"\n[memory.decision]\nport = 9300\n')
    assert dc.set_provider("local", path=toml) is True
    data = tomllib.loads(toml.read_text())
    assert data["memory"] == {
        "bank": "x", "decision": {"port": 9300, "provider": "local", "enabled": True},
    }  # fmt: skip
    assert dc.set_provider("local", path=toml) is False


# ── the status table ─────────────────────────────────────────────────────────


def test_status_row_for_a_hand_install_is_not_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.components import registry, status

    monkeypatch.setattr(registry, "_memory_decision_installed", lambda: "unmanaged")
    snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "memory-decision")
    assert row["status"] == "unmanaged" and "adopt" in row["note"]


# ── second review: children, reads, pins, hand units ─────────────────────────


def test_children_get_an_allow_listed_environment_and_no_stdin(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    monkeypatch.setenv("PIP_INDEX_URL", "https://evil.example/simple")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("https_proxy", "http://proxy.example:3128")
    runner = FakeRunner()
    assert _converge(box, install=True, runner=runner)["status"] == "installed"
    for kw in runner.kwargs:
        if "input" not in kw:
            assert kw["stdin"] is subprocess.DEVNULL
        env = kw["env"]
        assert "HF_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
        assert not any(k.startswith("PIP_") and k != "PIP_CONFIG_FILE" for k in env)
        assert env["PIP_CONFIG_FILE"] == os.devnull
        assert env["https_proxy"] == "http://proxy.example:3128"
        assert set(env) <= {
            *dc._ENV_ALLOW,
            "HOME",
            "PIP_CONFIG_FILE",
            "HF_HUB_DISABLE_TELEMETRY",
            "HF_HOME",
        }


def test_the_constraints_set_pins_every_package_exactly() -> None:
    lines = [
        ln for ln in dc.CONSTRAINTS_PATH.read_text().splitlines() if ln and not ln.startswith("#")
    ]
    assert len(lines) > 30
    assert all("==" in ln and ";" not in ln for ln in lines)
    pinned = dict(ln.split("==", 1) for ln in lines)
    assert pinned["torch"] == "2.15.0+cpu"
    for spec in dc.PACKAGES:
        name, version = spec.split("==")
        assert pinned[name] == version


def test_pinned_label_covers_the_constraints_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = dc.pinned_label()
    other = tmp_path / "c.txt"
    other.write_text(dc.CONSTRAINTS_PATH.read_text().replace("tqdm==", "tqdm==9"))
    monkeypatch.setattr(dc, "CONSTRAINTS_PATH", other)
    assert dc.pinned_label() != before


@pytest.mark.parametrize("kind", ["fifo", "symlink", "dir-symlink", "huge"])
def test_root_side_reads_refuse_fifos_symlinks_and_oversize(tmp_path: Path, kind: str) -> None:
    secret = tmp_path / "secret"
    secret.write_text('{"label": "leaked"}')
    target = tmp_path / "d" / "hal0-build.json"
    target.parent.mkdir()
    if kind == "fifo":
        os.mkfifo(target)
    elif kind == "symlink":
        target.symlink_to(secret)
    elif kind == "dir-symlink":
        real = tmp_path / "real"
        real.mkdir()
        (real / "hal0-build.json").write_text("{}")
        target = tmp_path / "linked" / "hal0-build.json"
        (tmp_path / "linked").symlink_to(real)
    else:
        target.write_bytes(b"x" * (decision_fs.MAX_READ_BYTES + 1))
    with pytest.raises(OSError):
        decision_fs.read_bytes(target)  # returns promptly; a FIFO would hang a plain read


def test_a_fifo_build_stamp_reads_as_unmanaged_without_hanging(box: dict[str, Any]) -> None:
    _install(box)
    stamp = dc.venv_dir() / "hal0-build.json"
    stamp.unlink()
    os.mkfifo(stamp)
    assert dc.install_state(unit_path=box["unit"])[0] == dc.UNMANAGED


def test_adoption_keeps_a_copy_of_the_hand_written_unit(box: dict[str, Any]) -> None:
    text = _hand_install(box)
    result = _converge(box, install=True)
    copy = Path(result["hand_unit_copy"])
    assert copy == box["unit"].with_name("hal0-hs-decide.service.pre-hal0")
    assert copy.read_text() == text and str(copy) in result["note"]
    assert not (dc.decision_dir() / "hal0-hs-decide.service.hand-written").exists()


# ── third review: a broken hal0.toml is never overwritten ────────────────────


def _broken(tmp_path: Path, kind: str) -> Path:
    toml = tmp_path / "hal0.toml"
    if kind == "malformed":
        toml.write_text('[meta]\nschema_version = 6\n[memory\nbank = "x"\n')
    elif kind == "unreadable":
        toml.write_text("[meta]\nschema_version = 6\n")
        toml.chmod(0)
    elif kind == "fifo":
        os.mkfifo(toml)
    elif kind == "symlink":
        other = tmp_path / "root-only.toml"
        other.write_text('[secret]\nkey = "v"\n')
        toml.symlink_to(other)
    return toml


@pytest.mark.parametrize("kind", ["malformed", "unreadable", "fifo", "symlink"])
def test_set_provider_refuses_a_file_it_cannot_read_safely(tmp_path: Path, kind: str) -> None:
    if kind == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a 0000 file")
    toml = _broken(tmp_path, kind)
    before = None if kind == "fifo" else os.lstat(toml)
    data = toml.read_bytes() if kind in ("malformed", "symlink") else None
    with pytest.raises(dc.Hal0TomlUnreadable):
        dc.set_provider("off", path=toml)
    if kind == "malformed":
        assert toml.read_bytes() == data
    if kind == "symlink":
        assert toml.is_symlink() and (tmp_path / "root-only.toml").read_bytes() == data
    if before is not None:
        assert os.lstat(toml).st_mtime_ns == before.st_mtime_ns
    if kind == "unreadable":
        toml.chmod(0o600)
        assert toml.read_text() == "[meta]\nschema_version = 6\n"


def test_set_provider_starts_from_empty_only_when_the_file_is_missing(tmp_path: Path) -> None:
    toml = tmp_path / "hal0.toml"
    assert dc.set_provider("off", path=toml) is True
    assert tomllib.loads(toml.read_text()) == {"memory": {"decision": {"provider": "off"}}}


@pytest.mark.parametrize("op", ["remove", "rollback"])
def test_a_broken_hal0_toml_is_left_alone_and_said_so(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, op: str
) -> None:
    from hal0.config import paths

    toml = paths.hal0_toml()
    toml.parent.mkdir(parents=True, exist_ok=True)
    broken = b"[meta]\nschema_version = 6\n[memory\n"
    if op == "remove":
        _install(box)
    toml.write_bytes(broken)
    monkeypatch.setattr(dc, "set_provider", _REAL_SET_PROVIDER)  # not the recording fake
    seam = FakeSeam()
    if op == "remove":
        result = _converge(box, action="remove", seam=seam)
        assert result["status"] == "removed"
        detail = result["note"]
    else:
        result = _converge(box, install=True, seam=seam, http_get=lambda _u: None)
        assert result["status"] == "build_failed" and result["lane"] == "off"
        detail = " ".join(result["restore_problems"])
    assert "hal0.toml could not be read" in detail and "nothing was changed" in detail
    assert toml.read_bytes() == broken
    assert ("stop", "hal0-hs-decide.service") in seam.calls
    assert not box["unit"].exists()
