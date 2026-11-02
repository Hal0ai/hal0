"""A halogen checkpoint runs only on the halogen runtime, and halogen runs only it.

``model_runtime_mismatch`` is the pure rule both seams share: the slot-config
write guard (``_guard_model_fits_slot``) and ``ContainerProvider.load_sync``.
``slot_runtime_provider`` is the read-only stand-in the write seam uses for the
provider, because a config write must not resolve (and possibly persist) a
profile the way launch dispatch does.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hal0.slots.model_fit import model_runtime_mismatch, slot_runtime_provider
from hal0.slots.state import SlotConfigError, SlotModelRuntimeMismatch

HALOGEN_MODEL = {"_model_key": "flash-next", "backends": ["halogen"], "provider": "halogen"}
GGUF_MODEL = {"_model_key": "qwen3-4b", "backends": ["rocm", "vulkan"], "provider": "llama-server"}
FLM_MODEL = {"_model_key": "qwen3-4b-FLM", "backends": ["flm"], "provider": "flm"}
HALOGEN = SimpleNamespace(name="halogen")


# ── model_runtime_mismatch ───────────────────────────────────────────────────


def test_halogen_model_on_llama_slot_names_both_sides() -> None:
    msg = model_runtime_mismatch(HALOGEN_MODEL, None, slot="agent")
    assert msg is not None
    assert "'flash-next'" in msg
    assert "halogen checkpoint" in msg
    assert "'agent'" in msg
    assert "llama-server runtime" in msg
    assert "halogen profile" in msg


def test_halogen_model_on_another_runtime_names_that_runtime() -> None:
    msg = model_runtime_mismatch(HALOGEN_MODEL, SimpleNamespace(name="flm"), slot="npu")
    assert msg is not None
    assert "flm runtime" in msg


def test_gguf_on_halogen_slot_names_both_sides() -> None:
    msg = model_runtime_mismatch(GGUF_MODEL, HALOGEN, slot="flash")
    assert msg is not None
    assert "'qwen3-4b'" in msg
    assert "not a halogen checkpoint" in msg
    assert "'flash'" in msg
    assert "halogen runtime" in msg


def test_halogen_model_on_halogen_slot_passes() -> None:
    assert model_runtime_mismatch(HALOGEN_MODEL, HALOGEN, slot="flash") is None


def test_non_halogen_model_on_non_halogen_slot_passes() -> None:
    assert model_runtime_mismatch(GGUF_MODEL, None) is None
    assert model_runtime_mismatch(GGUF_MODEL, SimpleNamespace(name="llama-server")) is None
    assert model_runtime_mismatch(FLM_MODEL, SimpleNamespace(name="flm")) is None


@pytest.mark.parametrize("provider", [None, HALOGEN, SimpleNamespace(name="flm")])
def test_empty_model_info_never_misfires(provider: object) -> None:
    """No model bound (or none the registry knows) is not this rule's refusal."""
    assert model_runtime_mismatch({}, provider) is None


def test_registry_miss_is_unknown_not_a_gguf() -> None:
    """A registry miss stamps only ``_model_key``/``flm_tag``: unknown, not wrong."""
    miss = {"_model_key": "who-knows", "flm_tag": "who-knows"}
    assert model_runtime_mismatch(miss, HALOGEN) is None
    assert model_runtime_mismatch(miss, None) is None


def test_gguf_row_with_empty_backends_is_still_not_halogen() -> None:
    """A registered GGUF often has ``backends == []`` and ``provider == "llama-server"``
    (the engine identity lives in ``provider``): it must still be refused on halogen."""
    row = {"_model_key": "qwen3-8b", "backends": [], "provider": "llama-server"}
    msg = model_runtime_mismatch(row, HALOGEN, slot="flash")
    assert msg is not None
    assert "not a halogen checkpoint" in msg
    assert model_runtime_mismatch({"backends": []}, HALOGEN) is not None
    assert model_runtime_mismatch(row, None) is None


def test_provider_alone_identifies_a_halogen_row() -> None:
    row = {"_model_key": "flash-next", "provider": "halogen"}
    assert model_runtime_mismatch(row, HALOGEN) is None
    assert model_runtime_mismatch(row, None) is not None


def test_message_survives_a_missing_slot_name_and_model_key() -> None:
    msg = model_runtime_mismatch({"backends": ["halogen"]}, None)
    assert msg is not None
    assert "halogen checkpoint" in msg


# ── slot_runtime_provider (read-only, write-seam side) ───────────────────────


def _profile(home: str, name: str, runner: str) -> None:
    etc = Path(home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "profiles.toml").write_text(f'[profile.{name}]\nrunner = "{runner}"\n', encoding="utf-8")


def test_slot_runtime_provider_halogen_profile(tmp_hal0_home: str) -> None:
    _profile(tmp_hal0_home, "hgn", "halogen")
    provider = slot_runtime_provider({"profile": "hgn", "device": "gpu-rocm"})
    assert getattr(provider, "name", None) == "halogen"
    # The nested ``[slot]`` shape resolves the same way.
    nested = slot_runtime_provider({"slot": {"profile": "hgn"}})
    assert getattr(nested, "name", None) == "halogen"


def test_slot_runtime_provider_llama_profile_is_none(tmp_hal0_home: str) -> None:
    _profile(tmp_hal0_home, "std", "standard")
    assert slot_runtime_provider({"profile": "std", "device": "gpu-rocm"}) is None
    assert slot_runtime_provider({"profile": "no-such-profile"}) is None
    assert slot_runtime_provider({}) is None


def test_slot_runtime_provider_npu_slot_is_flm() -> None:
    assert getattr(slot_runtime_provider({"device": "npu"}), "name", None) == "flm"


def test_slot_runtime_provider_does_not_write_the_profile_catalog(tmp_hal0_home: str) -> None:
    slot_runtime_provider({"profile": "chat"})
    assert not (Path(tmp_hal0_home) / "etc" / "hal0" / "profiles.toml").exists()


# ── the error class ──────────────────────────────────────────────────────────


def test_mismatch_error_is_a_400_slot_config_error() -> None:
    err = SlotModelRuntimeMismatch("nope")
    assert isinstance(err, SlotConfigError)
    assert err.code == "slot.model_runtime_mismatch"
    assert err.status == 400
