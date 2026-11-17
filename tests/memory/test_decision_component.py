"""#3757 — the opt-in ``memory-decision`` component (hal0-hs-decide).

Every privileged, network and process step is faked: the runner (venv, pip,
model pull), the seam (unit write, systemctl), the /health probe and the
engine drop-in reconcile. Nothing here builds a venv or downloads anything.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.config.schema import MemoryDecisionConfig
from hal0.memory import decision_component as dc
from hal0.system.seam import render_decision_unit


class FakeSeam:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_on = fail_on

    def systemctl(self, *args: str, check: bool = True, timeout: float | None = None):
        self.calls.append(tuple(args[1:]))
        if self.fail_on and args[1] == self.fail_on:
            raise subprocess.CalledProcessError(1, list(args), stderr="refused")
        return subprocess.CompletedProcess(list(args), 0, stdout="active\n", stderr="")

    def write_decision_unit(self, *, path: Path, timeout: float | None = None, **params: Any):
        self.calls.append(("write-unit", str(params["port"])))
        path.write_text(render_decision_unit(**params), encoding="utf-8")

    def remove_decision_unit(self, *, path: Path, timeout: float | None = None):
        self.calls.append(("remove-unit",))
        path.unlink(missing_ok=True)


class FakeRunner:
    """Answers the build steps the way a working box does; ``fail`` names one to fail."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []
        self.fail = fail

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        self.envs.append(kw.get("env"))
        joined = " ".join(argv)
        step = (
            "venv"
            if argv[1:3] == ["-m", "venv"]
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
        if step == "venv":
            py = Path(argv[3]) / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")
        if step == "pull":
            (dc.snapshot_dir() / "serving").mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(argv, 0, stdout=str(dc.snapshot_dir()), stderr="")
        if step == "torch-version":
            return subprocess.CompletedProcess(argv, 0, stdout="2.15.0+cpu\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@pytest.fixture
def box(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "decision": MemoryDecisionConfig(provider="local"),
        "reconciled": 0,
        "unit": tmp_path / "hal0-hs-decide.service",
    }
    monkeypatch.setattr(dc, "_decision_config", lambda: state["decision"])
    monkeypatch.setattr(dc, "_MIN_FREE_BYTES", 0)
    monkeypatch.setattr(dc, "_as_hal0", lambda argv: argv)
    monkeypatch.setattr(dc, "_own", lambda _p: None)
    monkeypatch.setattr(dc, "_retarget_scripts", lambda _a, _b: None)

    def _reconcile(_seam: Any) -> dict[str, Any]:
        state["reconciled"] += 1
        return {"status": "applied"}

    monkeypatch.setattr(dc, "_reconcile_engine", _reconcile)
    return state


def _converge(box: dict[str, Any], **kw: Any) -> dict[str, Any]:
    kw.setdefault("seam", FakeSeam())
    kw.setdefault("runner", FakeRunner())
    kw.setdefault("http_get", lambda _url: {"status": "ok"})
    kw.setdefault("sleep", lambda _s: None)
    return dc.converge_memory_decision(unit_path=box["unit"], **kw)


def _install(box: dict[str, Any]) -> None:
    assert _converge(box, install=True)["status"] == "installed"


# ── pins and the installed probe ─────────────────────────────────────────────


def test_pins_are_the_measured_revision_and_cpu_index() -> None:
    assert dc.MODEL_REVISION == "e2c789026dfd1f7608cafb2ef6a9bb31397af802"
    assert dc.TORCH_INDEX_URL == "https://download.pytorch.org/whl/cpu"
    assert "transformers>=5.16.1" in dc.PACKAGES and "huggingface-hub>=1.0" in dc.PACKAGES
    assert dc.pinned_label().startswith(dc.MODEL_REVISION[:12])


def test_installed_needs_unit_venv_and_build_stamp(box: dict[str, Any]) -> None:
    unit = box["unit"]
    assert dc.installed_label(unit_path=unit) is None
    _install(box)
    assert dc.installed_label(unit_path=unit) == dc.pinned_label()
    # A hand-built venv (no stamp) is not adopted until install runs.
    (dc.venv_dir() / "hal0-build.json").unlink()
    assert dc.installed_label(unit_path=unit) is None


# ── opt-in ───────────────────────────────────────────────────────────────────


def test_catalog_pass_never_installs(box: dict[str, Any]) -> None:
    runner, seam = FakeRunner(), FakeSeam()
    result = _converge(box, runner=runner, seam=seam)
    assert result["status"] == "skipped"
    assert "hal0 memory decision install" in result["reason"]
    assert runner.calls == [] and seam.calls == []


def test_diagnose_reports_without_touching_anything(box: dict[str, Any]) -> None:
    assert _converge(box, apply=False, install=True)["status"] == "skipped"
    _install(box)
    runner, seam = FakeRunner(), FakeSeam()
    assert _converge(box, apply=False, runner=runner, seam=seam)["status"] == "converged"
    assert runner.calls == [] and seam.calls == []


# ── install ──────────────────────────────────────────────────────────────────


def test_fresh_install_builds_pulls_renders_starts_and_reconciles(box: dict[str, Any]) -> None:
    runner, seam = FakeRunner(), FakeSeam()
    result = _converge(box, install=True, runner=runner, seam=seam)
    assert result["status"] == "installed" and result["to"] == dc.pinned_label()

    torch = next(c for c in runner.calls if "--index-url" in c)
    assert torch[torch.index("--index-url") + 1] == dc.TORCH_INDEX_URL
    pip = next(c for c in runner.calls if "transformers>=5.16.1" in c)
    assert "-c" in pip and "--index-url" not in pip and "--extra-index-url" not in pip
    assert Path(pip[pip.index("-c") + 1]).name == "hal0-constraints.txt"
    constraints = dc.venv_dir() / "hal0-constraints.txt"
    assert constraints.read_text() == "torch==2.15.0+cpu\n"
    pull_i = next(i for i, c in enumerate(runner.calls) if "snapshot_download" in " ".join(c))
    assert runner.calls[pull_i][-2:] == [dc.MODEL_REPO, dc.MODEL_REVISION]
    assert runner.envs[pull_i]["HF_HOME"] == str(dc.hf_home())
    assert any(c[-1] == str(dc.snapshot_dir() / "serving") for c in runner.calls)

    # Built aside, swapped in, nothing half-built left behind.
    assert (dc.venv_dir() / "bin" / "python").exists()
    assert not dc.venv_dir().with_name(".venv.new").exists()
    assert not dc.venv_dir().with_name(".venv.prev").exists()
    assert dc.server_script_path().read_bytes() == dc.packaged_server_script().read_bytes()

    unit = box["unit"].read_text()
    assert f"snapshots/{dc.MODEL_REVISION} " in unit
    assert "--host 127.0.0.1 --port 9178 --threads 8 --max-tokens 4096" in unit
    assert "User=hal0" in unit
    assert ("daemon-reload",) in seam.calls
    assert ("enable", "hal0-hs-decide.service") in seam.calls
    assert ("restart", "hal0-hs-decide.service") in seam.calls
    assert box["reconciled"] == 1
    assert "note" not in result


def test_unit_carries_decision_threads_port_and_max_tokens(box: dict[str, Any]) -> None:
    box["decision"] = MemoryDecisionConfig(
        provider="local", threads=4, port=9300, max_request_tokens=8192
    )
    seen: list[str] = []
    _converge(box, install=True, http_get=lambda url: seen.append(url) or {"status": "ok"})
    assert "--port 9300 --threads 4 --max-tokens 8192" in box["unit"].read_text()
    assert seen == ["http://127.0.0.1:9300/health"]


def test_install_with_the_lane_off_says_how_to_turn_it_on(box: dict[str, Any]) -> None:
    box["decision"] = MemoryDecisionConfig(provider="off")
    result = _converge(box, install=True)
    assert result["status"] == "installed"
    assert "hal0 memory decision enable" in result["note"]


def test_reconverge_with_nothing_changed_only_starts(box: dict[str, Any]) -> None:
    _install(box)
    runner, seam = FakeRunner(), FakeSeam()
    result = _converge(box, runner=runner, seam=seam)
    assert result["status"] == "converged"
    assert runner.calls == []
    assert ("start", "hal0-hs-decide.service") in seam.calls
    assert not any(c[0] in ("restart", "write-unit") for c in seam.calls)


def test_a_pin_change_rebuilds_an_installed_server(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(box)
    monkeypatch.setattr(dc, "PACKAGES", (*dc.PACKAGES, "safetensors"))
    runner = FakeRunner()
    result = _converge(box, runner=runner)
    assert result["status"] == "upgraded"
    assert any("safetensors" in c for c in runner.calls)
    assert dc.installed_label(unit_path=box["unit"]) == dc.pinned_label()


# ── failure leaves the lane off, or on the previous venv ────────────────────


@pytest.mark.parametrize("step", ["venv", "torch", "pip", "pull", "import"])
def test_a_failed_build_step_leaves_nothing_installed(box: dict[str, Any], step: str) -> None:
    seam = FakeSeam()
    result = _converge(box, install=True, runner=FakeRunner(fail=step), seam=seam)
    assert result["status"] == "build_failed"
    assert result["error"] and result["remedy"]
    assert result["lane"] == "off"
    assert not box["unit"].exists() and seam.calls == []
    assert not dc.venv_dir().exists()
    assert not dc.venv_dir().with_name(".venv.new").exists()


def test_a_failed_rebuild_keeps_the_live_venv_serving(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(box)
    monkeypatch.setattr(dc, "PACKAGES", (*dc.PACKAGES, "safetensors"))
    seam = FakeSeam()
    result = _converge(box, runner=FakeRunner(fail="pip"), seam=seam)
    assert result["status"] == "build_failed" and result["lane"] == "unchanged"
    assert seam.calls == []
    assert dc._read_build_stamp(dc.venv_dir())["label"] != dc.pinned_label()


def test_no_health_on_a_fresh_install_tears_the_unit_down(box: dict[str, Any]) -> None:
    seam = FakeSeam()
    result = _converge(box, install=True, seam=seam, http_get=lambda _u: None)
    assert result["status"] == "build_failed" and result["lane"] == "off"
    assert "did not answer" in result["error"]
    assert not box["unit"].exists()
    assert ("stop", "hal0-hs-decide.service") in seam.calls
    assert ("disable", "hal0-hs-decide.service") in seam.calls
    assert box["reconciled"] == 1


def test_no_health_after_a_rebuild_rolls_back_to_the_previous_venv(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(box)
    old_label = dc.pinned_label()
    monkeypatch.setattr(dc, "PACKAGES", (*dc.PACKAGES, "safetensors"))
    result = _converge(box, http_get=lambda _u: None)
    assert result["status"] == "rolled_back"
    assert dc._read_build_stamp(dc.venv_dir())["label"] == old_label
    assert box["unit"].exists()


def test_a_refused_seam_call_is_a_status_not_a_raise(box: dict[str, Any]) -> None:
    result = _converge(box, install=True, seam=FakeSeam(fail_on="enable"))
    assert result["status"] == "build_failed"
    assert "hal0-hs-decide.service" in result["error"]


def test_too_little_disk_refuses_before_building(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dc, "_MIN_FREE_BYTES", 1 << 62)
    runner = FakeRunner()
    result = _converge(box, install=True, runner=runner)
    assert result["status"] == "build_failed" and "free" in result["error"]
    assert runner.calls == []


# ── remove ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("purge", [False, True])
def test_remove_deletes_unit_and_venv_and_keeps_the_model_unless_purged(
    box: dict[str, Any], purge: bool
) -> None:
    _install(box)
    seam = FakeSeam()
    result = _converge(box, action="remove", purge_model=purge, seam=seam)
    assert result["status"] == "removed" and result["model_kept"] is (not purge)
    assert seam.calls[:2] == [
        ("stop", "hal0-hs-decide.service"),
        ("disable", "hal0-hs-decide.service"),
    ]
    assert not box["unit"].exists()
    assert not dc.venv_dir().exists() and not dc.server_script_path().exists()
    assert dc.snapshot_dir().exists() is (not purge)
    assert dc.installed_label(unit_path=box["unit"]) is None
