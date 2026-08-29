from unittest.mock import patch

import pytest

from hal0.config.schema import SEED_PROFILES, ProfileConfig
from hal0.errors import UnprocessableEntity
from hal0.providers.container import (
    ContainerProvider,
    _guard_specialty_runner,
    _resolve_context_size,
    _resolve_llama_scalars,
)
from hal0.registry.specialty import specialty_env_for
from hal0.runners import RUNNER_IMAGES


def _pf_model(companions=None):
    comps = companions if companions is not None else {
        "promptforge_ffn": "/var/lib/hal0/models/m/ffn.pfs",
        "promptforge_gdn": "/var/lib/hal0/models/m/gdn.pfs",
        "promptforge_output_k8": "/var/lib/hal0/models/m/k8.pfs",
    }
    return {
        "_model_key": "qwen-pf",
        "metadata": {"specialty": "promptforge", "companions": comps},
    }


def test_capable_runner_passes():
    assert _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["promptforge"]) is None


def test_incapable_runner_degrades_with_reason():
    reason = _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["rocmfpx"])
    assert reason["code"] == "slot.specialty_degraded"
    assert reason["specialty"] == "promptforge"
    assert reason["runner"] == "rocmfpx"


def test_missing_required_companion_degrades_even_on_capable_runner():
    model = _pf_model(companions={"promptforge_ffn": "/x/ffn.pfs"})  # gdn+k8 missing
    reason = _guard_specialty_runner(model, RUNNER_IMAGES["promptforge"])
    assert reason["code"] == "slot.specialty_degraded"
    assert "promptforge_gdn" in reason["detail"]


def test_plain_model_is_untouched():
    model = {"_model_key": "plain", "metadata": {}}
    assert _guard_specialty_runner(model, RUNNER_IMAGES["cpu"]) is None


def test_unknown_specialty_degrades_not_crashes():
    model = {"_model_key": "future", "metadata": {"specialty": "hyperdrive-v9"}}
    reason = _guard_specialty_runner(model, RUNNER_IMAGES["rocmfpx"])
    assert reason["code"] == "slot.specialty_degraded"


def test_degraded_not_ok_raises_422(monkeypatch):
    import dataclasses
    import hal0.registry.specialty as sp
    strict = dataclasses.replace(sp.SPECIALTY_KINDS["promptforge"], degraded_ok=False)
    monkeypatch.setitem(sp.SPECIALTY_KINDS, "promptforge", strict)
    with pytest.raises(UnprocessableEntity) as exc:
        _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["rocmfpx"])
    assert exc.value.code == "slot.unsupported_specialty_for_runner"


def test_preview_call_does_not_warn(caplog):
    # default log_degraded=False — the preview/poll path (for_launch=False)
    # must stay silent on every dashboard poll (#1946 fix round 1).
    with caplog.at_level("WARNING"):
        reason = _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["rocmfpx"])
    assert reason["code"] == "slot.specialty_degraded"
    assert not caplog.records


def test_launch_call_warns(caplog):
    # log_degraded=True — the real launch path — still logs once.
    with caplog.at_level("WARNING"):
        reason = _guard_specialty_runner(
            _pf_model(), RUNNER_IMAGES["rocmfpx"], log_degraded=True
        )
    assert reason["code"] == "slot.specialty_degraded"
    assert any("specialty" in r.message for r in caplog.records)


def test_unknown_specialty_silent_by_default_warns_when_launch(caplog):
    model = {"_model_key": "future", "metadata": {"specialty": "hyperdrive-v9"}}
    with caplog.at_level("WARNING"):
        reason = _guard_specialty_runner(model, RUNNER_IMAGES["rocmfpx"])
    assert reason["code"] == "slot.specialty_degraded"
    assert not caplog.records

    caplog.clear()
    with caplog.at_level("WARNING"):
        reason2 = _guard_specialty_runner(
            model, RUNNER_IMAGES["rocmfpx"], log_degraded=True
        )
    assert reason2 == reason  # parity: identical dict regardless of log_degraded
    assert any(caplog.records)


class TestSpecialtyEnv:
    def test_env_synthesized_from_companions(self):
        meta = _pf_model()["metadata"]
        env = specialty_env_for(meta)
        assert env["PROMPTFORGE_SIDECAR"] == "/var/lib/hal0/models/m/ffn.pfs"
        assert env["PROMPTFORGE_GDN_SIDECAR"] == "/var/lib/hal0/models/m/gdn.pfs"
        assert env["PROMPTFORGE_MTP_OUTPUT_K8_PROXY"] == "/var/lib/hal0/models/m/k8.pfs"

    def test_plain_metadata_empty(self):
        assert specialty_env_for({}) == {}
        assert specialty_env_for({"specialty": "promptforge"}) == {}  # no companions


def _pf_slot(**overrides):
    base = {
        "name": "s",
        "port": 8099,
        "device": "cpu",
        "model": {"default": "qwen-pf"},
    }
    base.update(overrides)
    return base


def test_scalars_carry_specialty_env_only_when_accelerated():
    """_resolve_llama_scalars: accelerated (capable runner) vs degraded
    (incapable runner) — specialty_env is synthesized ONLY on the
    accelerated path; a degraded launch gets NO specialty env."""
    model = _pf_model()
    profile = ProfileConfig()

    capable = _resolve_llama_scalars(
        _pf_slot(binary="promptforge"), model, profile
    )
    assert capable["specialty_degraded"] is None
    assert capable["specialty_env"]["PROMPTFORGE_SIDECAR"] == "/var/lib/hal0/models/m/ffn.pfs"
    assert capable["specialty_env"]["PROMPTFORGE_GDN_SIDECAR"] == "/var/lib/hal0/models/m/gdn.pfs"
    assert (
        capable["specialty_env"]["PROMPTFORGE_MTP_OUTPUT_K8_PROXY"]
        == "/var/lib/hal0/models/m/k8.pfs"
    )

    incapable = _resolve_llama_scalars(
        _pf_slot(binary="rocmfpx"), model, profile
    )
    assert incapable["specialty_env"] == {}
    assert incapable["specialty_degraded"]["code"] == "slot.specialty_degraded"


def test_operator_server_env_wins():
    """container_spec: an explicit [server].env key on the SAME name the
    specialty env synthesizes still wins in the merged launch plan — the
    three-way merge is vis_env < specialty_env < server_env (operator
    last, operator wins)."""
    cfg = _pf_slot(
        profile="promptforge",
        binary="promptforge",
        server={"env": {"PROMPTFORGE_SIDECAR": "/operator/override.pfs"}},
    )
    model = _pf_model()
    provider = ContainerProvider()
    profile = ProfileConfig()

    with patch("hal0.providers.container._resolve_profile", return_value=profile):
        plan = provider.container_spec(cfg, model)

    assert plan.env["PROMPTFORGE_SIDECAR"] == "/operator/override.pfs"
    # non-overridden companion env still rides through from the specialty path.
    assert plan.env["PROMPTFORGE_GDN_SIDECAR"] == "/var/lib/hal0/models/m/gdn.pfs"


# ── promptforge seed profile (spec 2026-08-29, #1946) ────────────────────────


def test_promptforge_seed_profile_exists():
    prof = SEED_PROFILES["promptforge"]
    assert "--no-cache-prompt" in prof["flags"]
    assert "-fa on" in prof["flags"]
    assert prof["mtp"] is True


# ── specialty default_ctx in the context precedence chain ────────────────────


def _specialty_model_info(**overrides):
    base = {
        "path": "/mnt/ai-models/qwen-pf.gguf",
        "_model_key": "qwen-pf",
        "metadata": {"specialty": "promptforge"},
    }
    base.update(overrides)
    return base


def test_specialty_default_ctx_used_when_model_has_none():
    """metadata.specialty=promptforge, no defaults.context_size, no
    metadata.context_length, generous slot ceiling -> the card's 262144."""
    mi = _specialty_model_info()
    assert _resolve_context_size(300_000, mi) == 262_144


def test_specialty_default_ctx_still_clamped_by_slot_ceiling():
    """The specialty default is authoritative but not exempt from the slot's
    hardware-ceiling clamp — same clamp path every other source uses."""
    mi = _specialty_model_info()
    assert _resolve_context_size(65_536, mi) == 65_536


def test_model_defaults_context_size_still_wins():
    """defaults.context_size=8192 -> 8192, specialty ignored: an explicit
    model choice outranks the card's default_ctx."""
    mi = _specialty_model_info(defaults={"context_size": 8192})
    assert _resolve_context_size(None, mi) == 8192
