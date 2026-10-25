import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.cli import setup_command
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


# ── the guided flow's image-model prompt ─────────────────────────────────────


@pytest.fixture
def guided(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """`run_interactive` with every side effect stubbed; returns what it asked and did.

    `seen["answers"]` is the list of answers handed to the image-model prompt in order;
    once it is empty the prompt gets its own default, as an operator pressing Enter does.
    """
    seen: dict = {"asked": [], "defaults": {}, "events": [], "installed": [], "answers": []}

    def fake_prompt(question, default, *, console):
        seen["asked"].append(question)
        seen["defaults"][question] = default
        if question.startswith("Image models") and seen["answers"]:
            return seen["answers"].pop(0)
        return default

    def fake_confirm(question, default, *, console):
        return False if "verification" in question else default

    async def fake_run_auto(sel, hw, *, no_pull=False):
        seen["events"].append("apply")
        seen["selection"] = sel

    def fake_install_image_models(model_ids):
        seen["events"].append("install")
        seen["installed"].append(tuple(model_ids))

    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))
    monkeypatch.setattr("hal0.capabilities.catalog.comfyui_backends", lambda: ["gpu-vulkan"])
    monkeypatch.setattr(setup_command, "_prompt", fake_prompt)
    monkeypatch.setattr(setup_command, "_confirm", fake_confirm)
    monkeypatch.setattr(setup_command, "_run_auto", fake_run_auto)
    monkeypatch.setattr(setup_command, "_existing_slot_names", lambda: frozenset())
    monkeypatch.setattr(setup_command, "_install_image_models", fake_install_image_models)
    return seen


def _guided(tmp_path: Path, pool_gb: int = 96) -> None:
    setup_command.run_interactive(_hw(pool_gb), storage_dir=str(tmp_path))


def test_run_interactive_prompts_for_image_models(guided, tmp_path, capsys):
    guided["answers"] = ["recommended"]

    _guided(tmp_path)

    image_prompts = [q for q in guided["asked"] if q.startswith("Image models")]
    assert len(image_prompts) == 1
    out = capsys.readouterr().out
    assert "qwen-image-lightning" in out  # the 96 GB pick, listed in the table
    assert "recommended" in out
    assert guided["installed"] == [("qwen-image-lightning",)]
    assert guided["selection"].image_models == ("qwen-image-lightning",)
    assert guided["events"] == ["apply", "install"]  # downloads follow the slot apply


def test_run_interactive_asks_after_the_scaffold_question(monkeypatch, guided, tmp_path):
    order: list[str] = []
    real_confirm = setup_command._confirm

    def tracking_confirm(question, default, *, console):
        order.append(question)
        return real_confirm(question, default, console=console)

    def tracking_prompt(question, default, *, console):
        order.append(question)
        return default

    monkeypatch.setattr(setup_command, "_confirm", tracking_confirm)
    monkeypatch.setattr(setup_command, "_prompt", tracking_prompt)

    _guided(tmp_path)

    kinds = [
        "scaffold" if "Scaffold" in q else "images" if q.startswith("Image models") else "other"
        for q in order
    ]
    assert kinds.index("images") == kinds.index("scaffold") + 1
    assert "verification" in order[-1]  # the report question stays last


def test_run_interactive_image_prompt_defaults_to_skip(guided, tmp_path):
    _guided(tmp_path)  # Enter at every prompt

    question = next(q for q in guided["asked"] if q.startswith("Image models"))
    assert guided["defaults"][question] == "none"
    assert guided["installed"] == []
    assert guided["selection"].image_models == ()


def test_run_interactive_reprompts_on_an_invalid_image_answer(guided, tmp_path, capsys):
    guided["answers"] = ["bogus", "1"]

    _guided(tmp_path)

    assert len([q for q in guided["asked"] if q.startswith("Image models")]) == 2
    assert "bogus" in capsys.readouterr().out  # said why, before asking again
    assert guided["installed"] == [("sdxl-lightning",)]  # offer 1, catalog order


def test_run_interactive_accepts_several_models(guided, tmp_path):
    guided["answers"] = ["1,3"]

    _guided(tmp_path)

    assert guided["installed"] == [("sdxl-lightning", "sd-1.5-pruned-emaonly")]


def test_run_interactive_offers_no_image_models_without_a_rocm_lane(
    guided, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("hal0.capabilities.catalog.comfyui_backends", lambda: [])

    _guided(tmp_path)

    assert not any(q.startswith("Image models") for q in guided["asked"])
    assert "no ROCm lane on this host" in capsys.readouterr().out
    assert guided["installed"] == []
