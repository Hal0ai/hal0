import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.cli import setup_command
from hal0.cli.setup_command import _api_reachable, app, build_auto_selections
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.extensions import ExtensionOutcome


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
    """`--auto` scaffolds ComfyUI but never names image models: nobody was asked."""
    assert build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models").image_models == ()


def test_auto_selections_carry_the_image_models_an_operator_chose():
    sel = build_auto_selections(
        _hw(96),
        storage_dir="/var/lib/hal0/models",
        image_models=("sdxl-lightning", "sd-1.5-pruned-emaonly"),
    )
    assert sel.image_models == ("sdxl-lightning", "sd-1.5-pruned-emaonly")


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

    def fake_provision_offline(model_ids, *, hf_token, on_line):
        calls["install"].append((tuple(model_ids), hf_token, on_line))
        return [(model_id, calls.get("state", "completed")) for model_id in model_ids]

    def fake_install_extension(ext_id):
        calls["extension"].append(ext_id)
        return ExtensionOutcome(ext_id=ext_id, installed=True)

    monkeypatch.setattr("hal0.cli.setup_command.HardwareProbe", _FakeProbe)
    monkeypatch.setattr("hal0.cli.setup_command._run_auto", fake_run_auto)
    monkeypatch.setattr("hal0.install.image_models.provision_offline", fake_provision_offline)
    monkeypatch.setattr("hal0.upstreams.huggingface.hf_token_from_env", lambda: "hf_from_env")
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
    # `provision_image_models` owns starting the img slot; the setup path never enables it.
    assert headless["extension"] == []


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


def test_setup_headless_installs_selects_and_starts_the_slot_once(monkeypatch, tmp_path):
    """The headless chain, unstubbed below `provision_offline`, in the order it must run.

    Download, then a normal apply (which writes the img unit and starts the slot); the
    operator reads what is happening and what came of it on the way. Nothing else starts or
    enables the unit: `provision_image_models` owns the start, and `systemctl enable` cannot
    enable a Quadlet-generated unit anyway (it only printed a spurious warning).
    """
    events: list[str] = []

    class Orchestrator:
        async def get_state(self):
            return {"selections": {}}

        async def apply(self, slot, child, partial, *, if_unselected=False):
            events.append(f"apply {slot}/{child} {partial['model']}")
            return {"status": "ready"}

    async def build_orchestrator():
        return Orchestrator()

    async def installed(model_ids, hf_token, say):
        events.append("install " + ",".join(model_ids))
        say("sdxl-lightning: installing")
        return [(model_id, "completed") for model_id in model_ids]

    def install_extension(ext_id):
        events.append(f"extension {ext_id}")
        return ExtensionOutcome(ext_id=ext_id, installed=True)

    async def fake_run_auto(sel, hw, *, no_pull=False):
        events.append("apply_setup")

    monkeypatch.setattr("hal0.cli.setup_command.HardwareProbe", _FakeProbe)
    monkeypatch.setattr("hal0.cli.setup_command._run_auto", fake_run_auto)
    monkeypatch.setattr("hal0.capabilities.catalog.comfyui_backends", lambda: ["gpu-vulkan"])
    monkeypatch.setattr("hal0.comfyui.install.install_each", installed)
    monkeypatch.setattr("hal0.install.image_models._build_offline_orchestrator", build_orchestrator)
    monkeypatch.setattr(
        "hal0.components.openwebui_arm.reconcile_openwebui_env", lambda: events.append("owui") or {}
    )
    monkeypatch.setattr("hal0.install.extensions.install_extension", install_extension)
    answers = _answers_file(tmp_path, "{ mode: scaffold_and_download, models: [sdxl-lightning] }")

    result = CliRunner().invoke(app, ["--answers", answers])

    assert result.exit_code == 0, result.output
    assert events == [
        "apply_setup",
        "install sdxl-lightning",
        "apply img/img sdxl-lightning",
        "owui",
    ]
    assert "could not enable" not in result.output
    lines = result.output.splitlines()
    starting = next(i for i, line in enumerate(lines) if "this starts the image engine" in line)
    assert "downloads the ComfyUI runtime" in lines[starting]
    after = [line for line in lines[starting + 1 :] if "is the default image model" in line]
    assert after[0].endswith("is the default image model; the image engine is ready")


def test_setup_headless_with_a_selection_in_place_starts_nothing(monkeypatch, tmp_path):
    """A kept selection means nothing is started: not by the auto-select, not by the setup path."""
    events: list[str] = []

    class Orchestrator:
        async def get_state(self):
            selection = {"enabled": True, "model": "sdxl-turbo"}
            return {"selections": {"img": {"img": selection}}}

        async def apply(self, slot, child, partial, *, if_unselected=False):
            events.append(f"apply {slot}/{child}")
            return {"status": "ready"}

    async def build_orchestrator():
        return Orchestrator()

    async def installed(model_ids, hf_token, say):
        return [(model_id, "completed") for model_id in model_ids]

    async def fake_run_auto(sel, hw, *, no_pull=False):
        events.append("apply_setup")

    monkeypatch.setattr("hal0.cli.setup_command.HardwareProbe", _FakeProbe)
    monkeypatch.setattr("hal0.cli.setup_command._run_auto", fake_run_auto)
    monkeypatch.setattr("hal0.capabilities.catalog.comfyui_backends", lambda: ["gpu-vulkan"])
    monkeypatch.setattr("hal0.comfyui.install.install_each", installed)
    monkeypatch.setattr("hal0.install.image_models._build_offline_orchestrator", build_orchestrator)
    monkeypatch.setattr("hal0.components.openwebui_arm.reconcile_openwebui_env", lambda: {})
    monkeypatch.setattr(
        "hal0.install.extensions.install_extension",
        lambda ext_id: events.append(f"extension {ext_id}") or ExtensionOutcome(ext_id=ext_id),
    )
    answers = _answers_file(tmp_path, "{ mode: scaffold_and_download, models: [sdxl-lightning] }")

    result = CliRunner().invoke(app, ["--answers", answers])

    assert result.exit_code == 0, result.output
    assert events == ["apply_setup"]
    assert "keeping sdxl-turbo as the default image model" in result.output


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
    assert "Download (GB)" in out
    sdxl = next(line for line in out.splitlines() if "sdxl-lightning" in line)
    assert "7.1" in sdxl  # the catalog's 7.6e9 bytes, shown as 7.1 GB
    assert "7.6" not in sdxl
    esrgan = next(line for line in out.splitlines() if "esrgan-4x" in line)
    assert "0.1" in esrgan


def test_plan_without_image_models_prints_no_image_table(capsys):
    from hal0.cli.setup_plan import _render_plan_table
    from hal0.install.orchestrate import Selections

    _render_plan_table(
        Selections(storage_dir="/var/lib/hal0/models", slots=[], extensions={}), strict=False
    )

    assert "Image models" not in capsys.readouterr().out


# ── `hal0 setup` with no flags asks nothing ──────────────────────────────────

_POINTER = (
    "hal0 setup asks nothing itself. To change what was chosen at install, "
    "run the installer again with --reconfigure."
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_no_flags_prints_the_pointer_and_exits_zero(runner):
    r = runner.invoke(app, [])
    assert r.exit_code == 0
    assert r.stdout.strip() == _POINTER


def test_no_flags_reads_nothing_from_the_terminal(runner, monkeypatch):
    def asked(*args):
        raise AssertionError("asked")

    monkeypatch.setattr("builtins.input", asked)
    assert runner.invoke(app, []).exit_code == 0


def test_force_interactive_variable_no_longer_does_anything(runner, monkeypatch):
    monkeypatch.setenv("HAL0_FORCE_INTERACTIVE", "1")
    r = runner.invoke(app, [])
    assert r.exit_code == 0
    assert "asks nothing itself" in r.stdout


def test_the_module_has_no_prompt_code():
    text = Path(setup_command.__file__).read_text()
    assert "rich.prompt" not in text
    assert "run_interactive" not in text
    assert "input(" not in text
