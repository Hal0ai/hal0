"""Tests for ``hal0 setup --emit-answers`` (issue #1117): the ``dump_answers``/
``write_answers`` serializer half of the answer-file round trip, and the CLI
wiring that resolves + writes + exits before any apply.

Spec: ``handoffs/hal0-setup-answers-spec-2026-07-05.md`` §2 (CLI surface),
§3 (schema), §8 (security — never inline a token).
"""

from __future__ import annotations

import textwrap

import pytest
import yaml
from typer.testing import CliRunner

from hal0.cli import setup_command
from hal0.cli.setup_command import build_auto_selections
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.answers import dump_answers, load_answers, write_answers
from hal0.install.orchestrate import Selections, SlotSelection

runner = CliRunner()


def _hw(ram_gb=96, npu_present=True):
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=ram_gb * 1024,
        ram_available_mb=ram_gb * 1024,
        unified_memory_mb=ram_gb * 1024,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=npu_present),
    )


def _manual_selections() -> Selections:
    return Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[
            SlotSelection(
                "chat", "chat", 8081, "some/chat-model-gguf", device="vulkan", profile="default"
            ),
            SlotSelection("coder", "coder", 8082, None),  # pick-free empty scaffold
        ],
        extensions={"openwebui": True, "comfyui": True, "hermes": True, "pi": False},
        npu_opt_in=True,
        comfyui_defaults=(("txt2img", "qwen-image"), ("img2img", "qwen-image-edit")),
    )


# ── dump_answers / write_answers round trip ─────────────────────────────────


def test_dump_answers_has_version_1():
    doc = dump_answers(_manual_selections())
    assert doc["version"] == 1


def test_dump_answers_never_inlines_a_token():
    doc = dump_answers(_manual_selections())
    assert doc["huggingface"] == {"token_env": "HF_TOKEN"}
    text = yaml.safe_dump(doc)
    # only the env-var-name key appears; no bare `token:`/`token_file:` with a value.
    assert "token_env: HF_TOKEN" in text
    assert "token_file" not in text
    assert "\ntoken:" not in text and not text.startswith("token:")


def test_round_trip_manual_selections_is_equivalent(tmp_path):
    sel = _manual_selections()
    hw = _hw()

    doc = dump_answers(sel)
    path = tmp_path / "hal0-setup.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    loaded = load_answers(str(path), hw)

    assert loaded.storage_dir == sel.storage_dir
    assert loaded.npu_opt_in == sel.npu_opt_in
    assert loaded.extensions == sel.extensions
    assert loaded.comfyui_defaults == sel.comfyui_defaults
    assert {
        (s.capability, s.slot_name, s.port, s.model_id, s.device, s.profile) for s in loaded.slots
    } == {(s.capability, s.slot_name, s.port, s.model_id, s.device, s.profile) for s in sel.slots}


def test_round_trip_from_build_auto_selections(tmp_path):
    hw = _hw()
    sel = build_auto_selections(hw, storage_dir="/mnt/hal0-models")

    # build_auto_selections also scaffolds embed/rerank/stt/tts/vision slots;
    # the answer-file slots schema only supports chat/coder today (spec §3/§5)
    # so dump_answers must warn-and-skip those rather than emit an invalid file.
    with pytest.warns(UserWarning, match="does not yet support"):
        doc = dump_answers(sel)
    assert {s["capability"] for s in doc["slots"]} == {"chat", "coder"}

    path = tmp_path / "hal0-setup.yaml"
    write_answers(sel, str(path))
    loaded = load_answers(str(path), hw)

    assert loaded.storage_dir == sel.storage_dir
    assert loaded.npu_opt_in == sel.npu_opt_in
    assert loaded.extensions == sel.extensions
    assert loaded.comfyui_defaults == sel.comfyui_defaults
    assert {s.capability for s in loaded.slots} == {"chat", "coder"}
    orig_by_cap = {s.capability: s for s in sel.slots if s.capability in ("chat", "coder")}
    for s in loaded.slots:
        assert s.model_id == orig_by_cap[s.capability].model_id
        assert s.port == orig_by_cap[s.capability].port


def test_write_answers_creates_parent_dirs_and_header(tmp_path):
    sel = _manual_selections()
    path = tmp_path / "nested" / "dir" / "hal0-setup.yaml"
    write_answers(sel, str(path))

    text = path.read_text()
    assert text.startswith("#")
    assert "hardware detected" in text
    doc = yaml.safe_load(text)
    assert doc["version"] == 1
    assert doc["model_store"]["path"] == sel.storage_dir


def test_apps_omits_comfyui_key():
    doc = dump_answers(_manual_selections())
    assert set(doc["apps"].keys()) == {"openwebui", "hermes", "pi"}
    assert doc["apps"]["openwebui"] == {"enabled": True}
    assert doc["apps"]["pi"] == {"enabled": False}


def test_gen_mode_derived_from_comfyui_extension():
    sel = _manual_selections()
    doc = dump_answers(sel)
    assert doc["gen"]["mode"] == "scaffold_only"
    assert doc["gen"]["capabilities"] == {"txt2img": "qwen-image", "img2img": "qwen-image-edit"}

    off_sel = Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[],
        extensions={"openwebui": True, "comfyui": False, "hermes": False, "pi": False},
        npu_opt_in=False,
        comfyui_defaults=(),
    )
    off_doc = dump_answers(off_sel)
    assert off_doc["gen"]["mode"] == "off"
    assert off_doc["gen"]["capabilities"] == {}


def test_slot_entry_omits_device_and_profile_when_none():
    sel = _manual_selections()
    doc = dump_answers(sel)
    coder_entry = next(s for s in doc["slots"] if s["capability"] == "coder")
    assert "device" not in coder_entry
    assert "profile" not in coder_entry
    assert coder_entry["model_id"] is None

    chat_entry = next(s for s in doc["slots"] if s["capability"] == "chat")
    assert chat_entry["device"] == "vulkan"
    assert chat_entry["profile"] == "default"


# ── CLI wiring: --emit-answers writes and exits before any apply ────────────


@pytest.fixture(autouse=True)
def _no_real_hardware_probe(monkeypatch):
    """Every CLI test below stubs HardwareProbe so it never touches the host."""
    monkeypatch.setattr(setup_command, "HardwareProbe", lambda: _StubProbe())


class _StubProbe:
    def probe(self):
        return _hw()


@pytest.fixture
def _forbid_apply(monkeypatch):
    """Make run_install/apply_setup explode if called — --emit-answers must
    return before either is invoked."""

    def boom_run_install(*a, **k):
        raise AssertionError("run_install must not be called for --emit-answers")

    def boom_apply_setup(*a, **k):
        raise AssertionError("apply_setup must not be called for --emit-answers")

    monkeypatch.setattr("hal0.cli.setup_install.run_install", boom_run_install)
    monkeypatch.setattr("hal0.install.orchestrate.apply_setup", boom_apply_setup)


def test_cli_emit_answers_auto_writes_file_and_returns(tmp_path, _forbid_apply):
    out = tmp_path / "hal0-setup.yaml"
    result = runner.invoke(
        setup_command.app,
        ["--auto", "--emit-answers", str(out), "--storage-dir", "/mnt/hal0-models"],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    doc = yaml.safe_load(out.read_text())
    assert doc["version"] == 1
    assert doc["model_store"]["path"] == "/mnt/hal0-models"


def test_cli_emit_answers_default_no_auto_flag_also_writes(tmp_path, _forbid_apply):
    """--emit-answers alone (no --auto, no --answers) resolves via
    build_auto_selections defaults rather than dropping into the interactive
    TUI — it must never block on a TTY."""
    out = tmp_path / "hal0-setup.yaml"
    result = runner.invoke(setup_command.app, ["--emit-answers", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_cli_emit_answers_from_answers_file(tmp_path, _forbid_apply):
    answers_path = tmp_path / "in.yaml"
    answers_path.write_text(
        textwrap.dedent(
            """
            version: 1
            model_store: { path: /var/lib/hal0/models }
            slots:
              - { capability: chat, name: chat, port: 8081, model_id: auto }
            npu: { opt_in: false }
            gen: { mode: "off" }
            apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
            """
        )
    )
    out = tmp_path / "hal0-setup.yaml"
    result = runner.invoke(
        setup_command.app,
        ["--answers", str(answers_path), "--emit-answers", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    doc = yaml.safe_load(out.read_text())
    assert doc["npu"]["opt_in"] is False
    assert {s["capability"] for s in doc["slots"]} == {"chat"}
