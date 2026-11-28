"""Jobs chosen at install create their slots: the ``--ensure`` command line and voice.

Nothing here reaches the network: the pull is faked at ``hal0.registry.pull.run_pull``,
the same seam ``test_agent_model.py`` fakes. Everything is written under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hal0.install import static_seeds
from hal0.install.static_seeds import SEED_TEMPLATES, add_seed_tombstone, read_seed_tombstones
from hal0.install.voice import (
    VOICE_MODEL_ID,
    VOICE_SLOT_NAME,
    describe_plan,
    main,
    provision_voice,
)
from hal0.registry.curated import get_curated


@pytest.fixture(autouse=True)
def _no_gpu_lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.install.profile_derive.rocm_lane_present", lambda *a, **k: False)
    monkeypatch.setattr(
        "hal0.install.profile_derive.default_image_serves_vulkan_lane", lambda *a, **k: True
    )


@pytest.fixture()
def installer_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake installer tree; the command lines resolve their root through it."""
    src_dir = tmp_path / "installer-root" / "installer" / "etc-hal0" / "slots"
    src_dir.mkdir(parents=True)
    for name in SEED_TEMPLATES:
        (src_dir / f"{name}.toml").write_text(
            f'name = "{name}"\ndevice = "gpu-rocm"\n\n[model]\nport = 9000\n', encoding="utf-8"
        )
    root = tmp_path / "installer-root"
    monkeypatch.setattr("hal0.agents.hermes_provision.REPO_ROOT_FOR_INSTALLER", root)
    return root


# ── static_seeds --ensure ───────────────────────────────────────────────────


def test_ensure_cli_creates_named_seed(
    tmp_path: Path, installer_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "img.toml").write_text('name = "img"\n# operator edit\n', encoding="utf-8")
    before = (slots / "img.toml").read_bytes()
    rc = static_seeds._main(["--slots-dir", str(slots), "--ensure", "tts", "img"])
    assert rc == 0
    assert capsys.readouterr().out.splitlines() == ["tts created", "img exists"]
    assert (slots / "tts.toml").is_file()
    assert (slots / "img.toml").read_bytes() == before


def test_ensure_cli_unknown_name_exits_2(
    tmp_path: Path, installer_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    slots = tmp_path / "slots"
    rc = static_seeds._main(["--slots-dir", str(slots), "--ensure", "tts", "nonesuch"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "nonesuch" in captured.err
    assert captured.out == ""
    assert not (slots / "tts.toml").exists()  # checked before anything was created


def test_ensure_cli_clears_tombstone(tmp_path: Path, installer_root: Path) -> None:
    slots = tmp_path / "slots"
    slots.mkdir()
    add_seed_tombstone("img", slots_dir=slots)
    assert "img" in read_seed_tombstones(slots_dir=slots)
    rc = static_seeds._main(["--slots-dir", str(slots), "--ensure", "img"])
    assert rc == 0
    assert (slots / "img.toml").is_file()
    assert "img" not in read_seed_tombstones(slots_dir=slots)


def test_derive_mode_without_ensure_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(static_seeds, "_resolve_hardware_info", lambda: None)
    assert static_seeds._main(["--slots-dir", str(tmp_path), "agent"]) == 1
    assert "hardware unresolvable" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        static_seeds._main(["--slots-dir", str(tmp_path)])
    assert exc.value.code == 2


# ── voice ───────────────────────────────────────────────────────────────────


class _FakeRegistry:
    def __init__(self) -> None:
        self.ensured: list[str] = []

    def ensure(self, model_id: str) -> None:
        self.ensured.append(model_id)


class _FakeSlotManager:
    def __init__(self, bound: str = "") -> None:
        self.bound = bound
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def get_config(self, slot: str) -> dict[str, Any]:
        return {"model": {"default": self.bound}} if self.bound else {"model": {}}

    async def update_config(self, slot: str, updates: dict[str, Any]) -> None:
        self.updates.append((slot, updates))
        self.bound = updates["model"]["default"]


def _stub_run_pull(monkeypatch: pytest.MonkeyPatch, *, state: str = "completed") -> list[Any]:
    import hal0.registry.pull as pull_mod

    calls: list[Any] = []

    async def _fake(job, **kwargs):
        calls.append((job.model_id, kwargs))
        job.state = state

    monkeypatch.setattr(pull_mod, "run_pull", _fake)
    return calls


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import hal0.config.store as store_mod

    monkeypatch.setattr(store_mod, "store_root", lambda: tmp_path / "store")
    return tmp_path / "store"


def _place_kokoro() -> None:
    from hal0.registry.pull import _final_path_for_entry

    curated = get_curated(VOICE_MODEL_ID)
    assert curated is not None
    dest = _final_path_for_entry(VOICE_MODEL_ID, curated.hf_file, "tts")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"onnx")
    (dest.parent / "meta.json").write_text(
        json.dumps(
            {
                "curated_id": VOICE_MODEL_ID,
                "hf_repo": curated.hf_repo,
                "hf_file": curated.hf_file,
                "size_bytes": len(b"onnx"),
                "capability": "tts",
            }
        )
    )


def test_voice_model_is_the_curated_kokoro_row() -> None:
    curated = get_curated(VOICE_MODEL_ID)
    assert curated is not None
    assert (VOICE_MODEL_ID, VOICE_SLOT_NAME) == ("kokoro-v1", "tts")
    assert curated.backend == "kokoro" and curated.recommended_slot == "tts"


def test_voice_plan_prints_id_tab_sentence_with_size_and_changes_nothing(
    tmp_path: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _stub_run_pull(monkeypatch)
    slots = tmp_path / "slots"
    assert main(["--plan", "--slots-dir", str(slots)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    model_id, _, sentence = lines[0].partition("\t")
    assert model_id == "kokoro-v1"
    assert "0.35 GB" in sentence
    assert lines[0] == describe_plan()
    assert calls == []
    assert not slots.exists()


def test_voice_creates_slot_and_pulls_kokoro_into_it(
    tmp_path: Path, installer_root: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_pull(monkeypatch)
    slots = tmp_path / "slots"
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    outcome = asyncio.run(provision_voice(slot_manager=sm, registry=reg, slots_dir=slots))
    assert outcome == "bound"
    assert (slots / "tts.toml").is_file()
    assert [c[0] for c in calls] == ["kokoro-v1"]
    assert calls[0][1]["capability"] == "tts"
    assert reg.ensured == ["kokoro-v1"]
    assert sm.updates == [("tts", {"model": {"default": "kokoro-v1"}})]


def test_voice_failed_pull_is_nonzero_with_slot_kept_and_no_model(
    tmp_path: Path,
    installer_root: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_run_pull(monkeypatch, state="failed")
    sm = _FakeSlotManager()
    monkeypatch.setattr("hal0.cli.setup_command._build_offline_deps", lambda: (sm, _FakeRegistry()))
    slots = tmp_path / "slots"
    assert main(["--slots-dir", str(slots)]) == 1
    err = capsys.readouterr().err
    assert len([ln for ln in err.splitlines() if ln.strip()]) == 1
    assert "voice" in err
    assert (slots / "tts.toml").is_file()
    assert sm.updates == []
    assert sm.bound == ""


def test_voice_pull_exception_is_nonzero_not_a_traceback(
    tmp_path: Path,
    installer_root: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hal0.registry.pull as pull_mod

    async def _boom(job, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(pull_mod, "run_pull", _boom)
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (_FakeSlotManager(), _FakeRegistry()),
    )
    slots = tmp_path / "slots"
    assert main(["--slots-dir", str(slots)]) == 1
    err = capsys.readouterr().err
    assert "network down" in err and "Traceback" not in err
    assert (slots / "tts.toml").is_file()


def test_voice_second_run_with_model_bound_does_nothing(
    tmp_path: Path,
    installer_root: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _stub_run_pull(monkeypatch)
    _place_kokoro()
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "tts.toml").write_text('name = "tts"\n# operator edit\n', encoding="utf-8")
    before = (slots / "tts.toml").read_bytes()
    sm = _FakeSlotManager(bound="kokoro-v1")
    monkeypatch.setattr("hal0.cli.setup_command._build_offline_deps", lambda: (sm, _FakeRegistry()))
    assert main(["--slots-dir", str(slots)]) == 0
    assert "already" in capsys.readouterr().out
    assert calls == []
    assert sm.updates == []
    assert (slots / "tts.toml").read_bytes() == before


def test_voice_never_replaces_another_model_bound_to_tts(
    tmp_path: Path, installer_root: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_pull(monkeypatch)
    sm = _FakeSlotManager(bound="some-other-voice")
    outcome = asyncio.run(
        provision_voice(slot_manager=sm, registry=_FakeRegistry(), slots_dir=tmp_path / "slots")
    )
    assert outcome == "other"
    assert calls == [] and sm.updates == []
    assert sm.bound == "some-other-voice"


def test_voice_binds_existing_bytes_without_downloading(
    tmp_path: Path, installer_root: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_pull(monkeypatch)
    _place_kokoro()
    sm = _FakeSlotManager()
    outcome = asyncio.run(
        provision_voice(slot_manager=sm, registry=_FakeRegistry(), slots_dir=tmp_path / "slots")
    )
    assert outcome == "bound"
    assert calls == []
    assert sm.updates == [("tts", {"model": {"default": "kokoro-v1"}})]


# ── fix round 1 ─────────────────────────────────────────────────────────────


class _UnreadableSlotManager(_FakeSlotManager):
    async def get_config(self, slot: str) -> dict[str, Any]:
        raise OSError("unreadable toml")


class _StickyLessSlotManager(_FakeSlotManager):
    """update_config 'succeeds' but the binding never lands."""

    async def update_config(self, slot: str, updates: dict[str, Any]) -> None:
        self.updates.append((slot, updates))


def test_voice_unreadable_binding_binds_nothing_and_exits_1(
    tmp_path: Path,
    installer_root: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _stub_run_pull(monkeypatch)
    sm = _UnreadableSlotManager()
    monkeypatch.setattr("hal0.cli.setup_command._build_offline_deps", lambda: (sm, _FakeRegistry()))
    assert main(["--slots-dir", str(tmp_path / "slots")]) == 1
    captured = capsys.readouterr()
    lines = [ln for ln in captured.err.splitlines() if ln.strip()]
    assert len(lines) == 1 and "could not be read" in lines[0]
    assert calls == [] and sm.updates == []
    assert "ready" not in captured.out


def test_voice_other_model_bound_exits_0_with_one_line(
    tmp_path: Path,
    installer_root: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _stub_run_pull(monkeypatch)
    sm = _FakeSlotManager(bound="some-other-voice")
    monkeypatch.setattr("hal0.cli.setup_command._build_offline_deps", lambda: (sm, _FakeRegistry()))
    assert main(["--slots-dir", str(tmp_path / "slots")]) == 0
    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(out) == 1 and "already uses another model" in out[0]
    assert calls == [] and sm.updates == [] and sm.bound == "some-other-voice"


def test_voice_activation_that_does_not_stick_exits_1(
    tmp_path: Path,
    installer_root: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_run_pull(monkeypatch)
    sm = _StickyLessSlotManager()
    monkeypatch.setattr("hal0.cli.setup_command._build_offline_deps", lambda: (sm, _FakeRegistry()))
    assert main(["--slots-dir", str(tmp_path / "slots")]) == 1
    captured = capsys.readouterr()
    lines = [ln for ln in captured.err.splitlines() if ln.strip()]
    assert len(lines) == 1 and "kokoro-v1" in lines[0]
    assert "ready" not in captured.out


def test_ensure_cli_one_failing_name_does_not_stop_the_rest(
    tmp_path: Path,
    installer_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real = static_seeds.ensure_seed_slot

    def _flaky(name: str, **kwargs: Any):
        if name == "img":
            raise RuntimeError("lock\ntimed out")
        return real(name, **kwargs)

    monkeypatch.setattr(static_seeds, "ensure_seed_slot", _flaky)
    slots = tmp_path / "slots"
    rc = static_seeds._main(["--slots-dir", str(slots), "--ensure", "tts", "img", "rerank"])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["tts created", "rerank created"]
    assert "img failed: " in captured.err and "lock timed out" in captured.err
    assert (slots / "tts.toml").is_file() and (slots / "rerank.toml").is_file()


def test_ensure_cli_positional_names_with_ensure_exit_2(
    tmp_path: Path, installer_root: Path
) -> None:
    slots = tmp_path / "slots"
    with pytest.raises(SystemExit) as exc:
        static_seeds._main(["--slots-dir", str(slots), "--ensure", "tts", "--", "img"])
    assert exc.value.code == 2
    assert not (slots / "tts.toml").exists() and not (slots / "img.toml").exists()
