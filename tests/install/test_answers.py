import os
import textwrap
import warnings

import pytest

from hal0.cli.setup_command import build_auto_selections
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.answers import AnswersError, gen_download_requested, load_answers


def _hw(ram_gb=96, npu_present=True, npu_validated=True):
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=ram_gb * 1024,
        ram_available_mb=ram_gb * 1024,
        unified_memory_mb=ram_gb * 1024,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        # Default fixture is a present-AND-healthy NPU (#1109 auto → opt-in True).
        npu=NPUInfo(present=npu_present, validated=npu_validated if npu_present else None),
    )


def _write(tmp_path, text):
    p = tmp_path / "hal0-setup.yaml"
    p.write_text(textwrap.dedent(text))
    return str(p)


_ALL_AUTO = """
version: 1
model_store:
  path: /var/lib/hal0/models
slots:
  - capability: chat
    name: chat
    port: 8081
    model_id: auto
  - capability: coder
    name: coder
    port: 8082
    model_id: auto
npu:
  opt_in: auto
gen:
  mode: scaffold_only
  capabilities:
    txt2img: auto
    img2img: auto
    txt2video: auto
    img2video: auto
    image_upscale: auto
apps:
  openwebui: { enabled: true }
  hermes: { enabled: true }
  pi: { enabled: false }
"""


def test_all_auto_matches_build_auto_selections(tmp_path):
    hw = _hw()
    path = _write(tmp_path, _ALL_AUTO)
    sel = load_answers(path, hw)

    auto_sel = build_auto_selections(hw, storage_dir="/var/lib/hal0/models")

    assert sel.storage_dir == auto_sel.storage_dir
    assert {s.capability for s in sel.slots} == {"chat", "coder"}
    assert sel.extensions["openwebui"] is True
    assert sel.extensions["hermes"] is True
    assert sel.extensions["pi"] is False
    assert sel.extensions["comfyui"] is True
    assert sel.npu_opt_in == auto_sel.npu_opt_in
    assert sel.npu_opt_in is True
    # every model_id resolved to a real suggestion (not left as the literal "auto")
    assert all(s.model_id and s.model_id != "auto" for s in sel.slots)
    # comfyui_defaults populated for every capability requested as auto
    assert {cap_id for cap_id, _family in sel.comfyui_defaults} == {
        "txt2img",
        "img2img",
        "txt2video",
        "img2video",
        "image_upscale",
    }


def test_missing_version_raises(tmp_path):
    path = _write(
        tmp_path,
        """
        model_store: { path: /var/lib/hal0/models }
        slots: []
        """,
    )
    with pytest.raises(AnswersError):
        load_answers(path, _hw())


def test_unknown_version_raises(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 2
        model_store: { path: /var/lib/hal0/models }
        slots: []
        """,
    )
    with pytest.raises(AnswersError):
        load_answers(path, _hw())


def test_bad_slot_capability_raises_naming_value(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots:
          - { capability: embed, name: embed, port: 8083, model_id: auto }
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    with pytest.raises(AnswersError, match="embed"):
        load_answers(path, _hw())


def test_unknown_extension_id_raises_naming_value(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { bogus_app: { enabled: true } }
        """,
    )
    with pytest.raises(AnswersError, match="bogus_app"):
        load_answers(path, _hw())


def test_unknown_gen_capability_raises_naming_value(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: false }
        gen: { mode: scaffold_only, capabilities: { bogus_cap: auto } }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    with pytest.raises(AnswersError, match="bogus_cap"):
        load_answers(path, _hw())


def test_gen_mode_off_disables_comfyui(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    sel = load_answers(path, _hw())
    assert sel.extensions["comfyui"] is False
    assert sel.comfyui_defaults == ()


def test_gen_download_requested_only_for_scaffold_and_download(tmp_path):
    base = """
        version: 1
        model_store: {{ path: /var/lib/hal0/models }}
        slots: []
        npu: {{ opt_in: false }}
        gen: {{ mode: {mode}, capabilities: {{ txt2img: auto }} }}
        apps: {{ openwebui: {{ enabled: true }}, hermes: {{ enabled: false }}, pi: {{ enabled: false }} }}
    """
    off = _write(tmp_path, base.format(mode="off"))
    assert gen_download_requested(off) is False
    scaffold = _write(tmp_path, base.format(mode="scaffold_only"))
    assert gen_download_requested(scaffold) is False
    download = _write(tmp_path, base.format(mode="scaffold_and_download"))
    assert gen_download_requested(download) is True
    # A missing / unreadable file degrades to False (never raises here).
    assert gen_download_requested(str(tmp_path / "nope.yaml")) is False


def test_scaffold_and_download_no_longer_warns_and_records_defaults(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: false }
        gen: { mode: scaffold_and_download, capabilities: { txt2img: sdxl } }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning → failure
        sel = load_answers(path, _hw())
    assert sel.extensions["comfyui"] is True
    assert sel.comfyui_defaults == (("txt2img", "sdxl"),)
    assert gen_download_requested(path) is True


def test_gen_mode_scaffold_only_enables_comfyui_and_populates_defaults(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: false }
        gen: { mode: scaffold_only, capabilities: { txt2img: auto } }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    sel = load_answers(path, _hw())
    assert sel.extensions["comfyui"] is True
    assert sel.comfyui_defaults == (("txt2img", "qwen-image"),)


def test_network_block_applied_huggingface_still_warns(tmp_path, monkeypatch):
    """WS-C (#1099): the network block is now APPLIED (bind_host → the coherent
    HAL0_BIND_HOST/HAL0_HOSTNAME/HAL0_ALLOWED_ORIGINS triple), no longer a
    warn-ignore; huggingface stays a warn-ignore until its workstream lands."""
    for var in ("HAL0_BIND_HOST", "HAL0_HOSTNAME", "HAL0_ALLOWED_ORIGINS", "HAL0_LAN_IPS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HAL0_LAN_IPS", "192.168.1.42")  # deterministic derivation
    path = _write(
        tmp_path,
        """
        version: 1
        network:
          bind_host: 0.0.0.0
          hostname: hal0
        model_store: { path: /var/lib/hal0/models }
        huggingface:
          token_env: HF_TOKEN
        slots: []
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sel = load_answers(path, _hw())
    assert sel.storage_dir == "/var/lib/hal0/models"
    messages = [str(w.message) for w in caught]
    # network is applied, so it must NOT emit a "not yet applied" warning.
    assert not any("network is not yet applied" in m for m in messages)
    assert any("huggingface" in m for m in messages)
    # The bind choice was resolved into the coherent env triple.
    assert os.environ["HAL0_BIND_HOST"] == "0.0.0.0"
    assert os.environ["HAL0_HOSTNAME"] == "hal0"
    origins = os.environ["HAL0_ALLOWED_ORIGINS"].split(",")
    assert "http://192.168.1.42:8080" in origins  # advertised URL passes WS gate
    assert "http://hal0:8080" in origins


def test_network_block_bad_type_raises(tmp_path):
    """A malformed network value is rejected, not silently ignored."""
    path = _write(
        tmp_path,
        """
        version: 1
        network:
          bind_host: [not, a, string]
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: false }
        gen: { mode: off }
        apps: {}
        """,
    )
    with pytest.raises(AnswersError, match=r"network\.bind_host"):
        load_answers(path, _hw())


def test_unknown_top_level_key_warns_by_default(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        totally_unknown_key: 42
        """,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_answers(path, _hw())
    assert any("totally_unknown_key" in str(w.message) for w in caught)


def test_unknown_top_level_key_raises_when_strict(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        strict: true
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        totally_unknown_key: 42
        """,
    )
    with pytest.raises(AnswersError, match="totally_unknown_key"):
        load_answers(path, _hw())


def test_relative_storage_dir_raises(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: relative/path }
        slots: []
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    with pytest.raises(AnswersError):
        load_answers(path, _hw())


def test_npu_opt_in_explicit_bool_passes_through(tmp_path):
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots: []
        npu: { opt_in: true }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    # even on a box without an NPU, explicit true passes through unchanged.
    sel = load_answers(path, _hw(npu_present=False))
    assert sel.npu_opt_in is True


_AUTO_NPU = """
version: 1
model_store: { path: /var/lib/hal0/models }
slots: []
npu: { opt_in: auto }
gen: { mode: off }
apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
"""


def test_npu_opt_in_auto_resolves_present_and_healthy(tmp_path):
    # `auto` on a present + validated NPU → opt-in True (#1109).
    path = _write(tmp_path, _AUTO_NPU)
    sel = load_answers(path, _hw(npu_present=True, npu_validated=True))
    assert sel.npu_opt_in is True


def test_npu_opt_in_auto_off_when_present_but_broken(tmp_path):
    # `auto` on a present-but-BROKEN NPU (validated False) → opt-in False: never
    # auto-advertise a lane apply_setup would skip (#1109).
    path = _write(tmp_path, _AUTO_NPU)
    sel = load_answers(path, _hw(npu_present=True, npu_validated=False))
    assert sel.npu_opt_in is False


def test_npu_opt_in_auto_off_when_absent(tmp_path):
    path = _write(tmp_path, _AUTO_NPU)
    sel = load_answers(path, _hw(npu_present=False))
    assert sel.npu_opt_in is False


# ── slots[].device / profile passthrough (WS-E, #1107) ───────────────────────


def test_slot_device_and_profile_pass_through(tmp_path):
    """Explicit slots[].device / profile on an answer-file entry ride onto the
    resulting SlotSelection verbatim (clean-seed derivation override, #1107).

    apply_setup honours these via ``s.device or derive_device(...)`` /
    ``s.profile or derive_profile(...)``, so an operator can pin a lane the
    preflight would otherwise derive differently."""
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots:
          - capability: chat
            name: chat
            port: 8081
            model_id: null
            device: gpu-vulkan
            profile: vulkan
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    sel = load_answers(path, _hw())
    (slot,) = sel.slots
    assert slot.slot_name == "chat"
    assert slot.device == "gpu-vulkan"
    assert slot.profile == "vulkan"
    # model_id: null → a grey scaffold slot (no pick, no pull).
    assert slot.model_id is None


def test_slot_device_profile_omitted_and_auto_derive(tmp_path):
    """Omitted or ``auto`` device/profile leave the SlotSelection fields None so
    apply_setup derives them from the preflight (clean-seed default path)."""
    path = _write(
        tmp_path,
        """
        version: 1
        model_store: { path: /var/lib/hal0/models }
        slots:
          - { capability: chat, name: chat, port: 8081, model_id: null }
          - { capability: coder, name: coder, port: 8082, model_id: null, device: auto, profile: auto }
        npu: { opt_in: false }
        gen: { mode: off }
        apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
        """,
    )
    sel = load_answers(path, _hw())
    for slot in sel.slots:
        assert slot.device is None
        assert slot.profile is None
