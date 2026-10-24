import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.cli.setup_command import _api_reachable, app, build_auto_selections
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.extensions import ExtensionOutcome


def test_headless_interactive_prints_stage2_command(monkeypatch):
    """Two-stage handoff (issue #1112): a piped / non-TTY `hal0 setup` (no
    --auto) must NOT launch rich prompts — it prints the command to run and
    exits 0 without probing hardware or applying anything."""
    monkeypatch.delenv("HAL0_FORCE_INTERACTIVE", raising=False)

    def boom(*a, **k):  # would blow up if the probe were reached
        raise AssertionError("hardware probe should not run on the headless guard path")

    monkeypatch.setattr("hal0.cli.setup_command.HardwareProbe", boom)
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert "hal0 setup" in result.output
    assert "--auto" in result.output


def _hw(ram_gb=96):
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=ram_gb * 1024,
        ram_available_mb=ram_gb * 1024,
        unified_memory_mb=ram_gb * 1024,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=True),
    )


def test_api_reachable_false_on_connection_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr("hal0.cli.setup_command.httpx.get", boom)
    assert _api_reachable(timeout=0.01) is False


def test_auto_selections_scaffolds_capability_slots_empty():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    names = {s.slot_name for s in sel.slots}
    # the capability slot STRUCTURE is scaffolded (device/profile/port), but no
    # model is chosen — pick-free: slots yes, models no. The chat capability's
    # slot is NAMED `agent` (ADR-0023 LLM anchor) and the steward's `brain`
    # scaffold rides along.
    # vision is retired — a model property served by any llm slot, no
    # dedicated scaffold.
    assert {"agent", "brain", "embed", "rerank", "stt", "tts"} <= names
    assert "vision" not in names
    assert all(s.model_id is None for s in sel.slots)  # every slot empty
    assert sel.extensions["openwebui"] is True
    assert sel.extensions["hermes"] is True
    # an agent extension is enabled by default → coder slot is scaffolded too
    assert "coder" in names


def test_auto_selections_brain_scaffold_is_chat_capability():
    """`brain` rides the chat-capability device/profile derivation and, like
    every scaffold, ships without a model (hal0/brain falls back to `agent`
    until the operator binds one)."""
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    brain = next(s for s in sel.slots if s.slot_name == "brain")
    assert brain.capability == "chat"
    assert brain.model_id is None
    agent = next(s for s in sel.slots if s.slot_name == "agent")
    assert agent.capability == "chat"
    assert brain.port != agent.port


def test_auto_selections_no_slots_seeds_nothing():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models", with_slots=False)
    assert sel.slots == []
    assert sel.image_models == ()


def test_auto_selections_no_extensions_disables_all_and_skips_agent_slot():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models", with_extensions=False)
    assert all(v is False for v in sel.extensions.values())
    # agent (Main) slot still seeded; coder slot NOT seeded (no agent ext on)
    assert any(s.slot_name == "agent" for s in sel.slots)
    assert not any(s.slot_name == "coder" for s in sel.slots)


def test_auto_selections_default_keeps_extensions_and_agent_slot():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    assert sel.extensions["hermes"] is True
    assert any(s.slot_name == "coder" for s in sel.slots)


def test_auto_selections_skips_existing_slots():
    sel = build_auto_selections(
        _hw(96),
        storage_dir="/var/lib/hal0/models",
        existing_slots=frozenset({"agent", "brain"}),
    )
    # agent + brain already exist on disk → not re-seeded; coder (agent
    # extension default on) still seeded
    assert not any(s.slot_name == "agent" for s in sel.slots)
    assert not any(s.slot_name == "brain" for s in sel.slots)
    assert any(s.slot_name == "coder" for s in sel.slots)


def test_auto_selections_no_existing_seeds_all_default():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    assert any(s.slot_name == "agent" for s in sel.slots)


def test_auto_selections_install_no_image_models():
    """The auto and guided paths scaffold ComfyUI but never name image models."""
    assert build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models").image_models == ()


class _FakeProbe:
    def probe(self, *, validate_npu: bool = False) -> HardwareInfo:
        return _hw(96)

    def write(self, info: HardwareInfo) -> None:
        return None


@pytest.fixture
def headless(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Everything `hal0 setup --answers` touches stubbed; returns what the image step did."""
    calls: dict[str, list] = {"install": [], "extension": []}

    async def fake_run_auto(sel, hw, *, no_pull=False):
        calls["applied"] = [sel.image_models, no_pull]

    def fake_install_models_blocking(model_ids, *, hf_token, on_line=None):
        calls["install"].append((tuple(model_ids), hf_token, on_line))
        return [(model_id, calls.get("state", "completed")) for model_id in model_ids]

    def fake_install_extension(ext_id):
        calls["extension"].append(ext_id)
        return ExtensionOutcome(ext_id=ext_id, installed=True)

    monkeypatch.setattr("hal0.cli.setup_command.HardwareProbe", _FakeProbe)
    monkeypatch.setattr("hal0.cli.setup_command._run_auto", fake_run_auto)
    monkeypatch.setattr(
        "hal0.comfyui.install.install_models_blocking", fake_install_models_blocking
    )
    monkeypatch.setattr("hal0.comfyui.install.hf_token_from_env", lambda: "hf_from_env")
    monkeypatch.setattr("hal0.install.extensions.install_extension", fake_install_extension)
    return calls


def _answers_file(tmp_path: Path, gen: str) -> str:
    path = tmp_path / "hal0-setup.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            version: 1
            model_store: {{ path: {tmp_path}/models }}
            slots: []
            npu: {{ opt_in: false }}
            gen: {gen}
            apps: {{ openwebui: {{ enabled: false }}, hermes: {{ enabled: false }} }}
            """
        )
    )
    return str(path)


def test_setup_headless_installs_image_models(headless, tmp_path):
    answers = _answers_file(
        tmp_path, "{ mode: scaffold_and_download, models: [sdxl-lightning, esrgan-4x] }"
    )

    result = CliRunner().invoke(app, ["--answers", answers])

    assert result.exit_code == 0, result.output
    (call,) = headless["install"]
    model_ids, hf_token, on_line = call
    assert model_ids == ("sdxl-lightning", "esrgan-4x")
    assert hf_token == "hf_from_env"
    assert callable(on_line)
    assert headless["extension"] == ["comfyui"]  # something landed: bring the img slot up


def test_setup_headless_skips_image_models_under_no_pull(headless, tmp_path):
    answers = _answers_file(tmp_path, "{ mode: scaffold_and_download, models: [sdxl-lightning] }")

    result = CliRunner().invoke(app, ["--answers", answers, "--no-pull"])

    assert result.exit_code == 0, result.output
    assert headless["install"] == []
    assert headless["extension"] == []


@pytest.mark.parametrize("mode", ["scaffold_only", "off"])
def test_setup_headless_installs_nothing_unless_mode_downloads(headless, tmp_path, mode):
    answers = _answers_file(tmp_path, f"{{ mode: {mode}, models: [sdxl-lightning] }}")

    result = CliRunner().invoke(app, ["--answers", answers])

    assert result.exit_code == 0, result.output
    assert headless["install"] == []


def test_setup_headless_leaves_img_slot_alone_when_nothing_landed(headless, tmp_path):
    headless["state"] = "failed"
    answers = _answers_file(tmp_path, "{ mode: scaffold_and_download, models: [sdxl-lightning] }")

    result = CliRunner().invoke(app, ["--answers", answers])

    assert result.exit_code == 0, result.output
    assert len(headless["install"]) == 1
    assert headless["extension"] == []
    assert "sdxl-lightning" in result.output  # the failure is named, not swallowed


def test_setup_headless_without_models_skips_the_installer(headless, tmp_path):
    answers = _answers_file(tmp_path, "{ mode: scaffold_and_download }")

    result = CliRunner().invoke(app, ["--answers", answers])

    assert result.exit_code == 0, result.output
    assert headless["install"] == []


def test_plan_lists_image_models_with_their_download_size(capsys):
    from hal0.cli.setup_plan import _render_plan_table
    from hal0.install.orchestrate import Selections

    sel = Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[],
        extensions={"comfyui": True},
        image_models=("sdxl-lightning", "esrgan-4x"),
    )

    _render_plan_table(sel, strict=False)

    out = capsys.readouterr().out
    assert "Image models" in out
    sdxl = next(line for line in out.splitlines() if "sdxl-lightning" in line)
    assert "7.6" in sdxl
    esrgan = next(line for line in out.splitlines() if "esrgan-4x" in line)
    assert "0.1" in esrgan


def test_plan_without_image_models_prints_no_image_table(capsys):
    from hal0.cli.setup_plan import _render_plan_table
    from hal0.install.orchestrate import Selections

    _render_plan_table(
        Selections(storage_dir="/var/lib/hal0/models", slots=[], extensions={}), strict=False
    )

    assert "Image models" not in capsys.readouterr().out
