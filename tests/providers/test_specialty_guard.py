from unittest.mock import patch

import pytest

from hal0.config.schema import SEED_PROFILES, ProfileConfig
from hal0.errors import UnprocessableEntity
from hal0.providers.container import (
    _CTX_SAFE_FALLBACK,
    ContainerProvider,
    _guard_specialty_runner,
    _resolve_context_size,
    _resolve_llama_scalars,
    resolve_effective_context_size,
)
from hal0.registry.specialty import specialty_env_for
from hal0.runners import RUNNER_IMAGES


def _pf_model(companions=None):
    comps = (
        companions
        if companions is not None
        else {
            "promptforge_ffn": "/var/lib/hal0/models/m/ffn.pfs",
            "promptforge_gdn": "/var/lib/hal0/models/m/gdn.pfs",
            "promptforge_output_k8": "/var/lib/hal0/models/m/k8.pfs",
        }
    )
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


def test_empty_companion_path_degrades_not_silently_accelerates():
    """M2 (fix wave): the guard's completeness check was ``role not in
    companions``, so a row whose path is ``""`` passed as ACCELERATED — while
    ``specialty_env_for`` skips non-empty-str values and silently dropped the
    env var. Guard and env synthesizer must agree on what "present" means."""
    model = _pf_model(
        companions={
            "promptforge_ffn": "",  # present-but-empty
            "promptforge_gdn": "/x/gdn.pfs",
            "promptforge_output_k8": "/x/k8.pfs",
        }
    )
    reason = _guard_specialty_runner(model, RUNNER_IMAGES["promptforge"])
    assert reason["code"] == "slot.specialty_degraded"
    assert "promptforge_ffn" in reason["detail"]
    # the env synthesizer's view, which the guard now matches
    assert "PROMPTFORGE_SIDECAR" not in specialty_env_for(model["metadata"])


def test_non_str_companion_path_degrades():
    """Same rule for a non-string value (a null/number that leaked in)."""
    model = _pf_model(
        companions={
            "promptforge_ffn": None,
            "promptforge_gdn": "/x/gdn.pfs",
            "promptforge_output_k8": "/x/k8.pfs",
        }
    )
    reason = _guard_specialty_runner(model, RUNNER_IMAGES["promptforge"])
    assert reason["code"] == "slot.specialty_degraded"
    assert "promptforge_ffn" in reason["detail"]


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
        reason = _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["rocmfpx"], log_degraded=True)
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
        reason2 = _guard_specialty_runner(model, RUNNER_IMAGES["rocmfpx"], log_degraded=True)
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

    capable = _resolve_llama_scalars(_pf_slot(binary="promptforge"), model, profile)
    assert capable["specialty_degraded"] is None
    assert capable["specialty_env"]["PROMPTFORGE_SIDECAR"] == "/var/lib/hal0/models/m/ffn.pfs"
    assert capable["specialty_env"]["PROMPTFORGE_GDN_SIDECAR"] == "/var/lib/hal0/models/m/gdn.pfs"
    assert (
        capable["specialty_env"]["PROMPTFORGE_MTP_OUTPUT_K8_PROXY"]
        == "/var/lib/hal0/models/m/k8.pfs"
    )

    incapable = _resolve_llama_scalars(_pf_slot(binary="rocmfpx"), model, profile)
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


# ── I2 fix round 1: specialty default_ctx only applies when accelerated ──────


def test_specialty_default_ctx_not_applied_when_degraded():
    """A DEGRADED specialty launch resolves context exactly like a plain
    model — never the card's default_ctx (spec 2026-08-29, #1946 fix
    round 1). No metadata.context_length either, so this lands on the safe
    8192 floor, same as any other unstamped/unmetadata'd model."""
    mi = _specialty_model_info()
    degraded_reason = {"code": "slot.specialty_degraded", "specialty": "promptforge"}
    assert _resolve_context_size(300_000, mi, specialty_degraded=degraded_reason) == (
        _CTX_SAFE_FALLBACK
    )


def test_specialty_default_ctx_end_to_end_via_resolve_llama_scalars():
    """Integration coverage for the real wiring (not just the pure
    function): _resolve_llama_scalars computes specialty_degraded from the
    ACTUAL guard against the resolved runner and threads it into
    _resolve_context_size. Capable runner -> the card's 262144; incapable
    runner -> plain-model resolution (8192 safe floor, no metadata.context_length
    on this fixture)."""
    model = _pf_model()
    profile = ProfileConfig()

    capable = _resolve_llama_scalars(_pf_slot(binary="promptforge"), model, profile)
    assert capable["specialty_degraded"] is None
    assert capable["context_size"] == 262_144

    incapable = _resolve_llama_scalars(_pf_slot(binary="rocmfpx"), model, profile)
    assert incapable["specialty_degraded"]["code"] == "slot.specialty_degraded"
    assert incapable["context_size"] == _CTX_SAFE_FALLBACK


# ── I1 fix round 1: unstamped-model template injection (#1787) ───────────────


def test_promptforge_template_flags_not_injected_when_degraded():
    """The unstamped-model profile-TEMPLATE injection path (#1787,
    container.py's ``slot_profile_template_flags`` block) must not leak the
    card's forced-kernel argv (``-fa on`` et al) onto a DEGRADED launch
    (spec 2026-08-29, #1946 fix round 1): that runner never runs the
    accelerated kernel path the card's flags assume, so a degraded launch
    must resolve flags exactly like a plain, unstamped model — none of the
    slot's named profile flags injected as a template."""
    profile = ProfileConfig.model_validate(SEED_PROFILES["promptforge"])
    model = _pf_model()  # no stamped defaults/provenance -> hits the #1787 path

    capable = _resolve_llama_scalars(
        _pf_slot(profile="promptforge", binary="promptforge"), model, profile
    )
    assert "--no-cache-prompt" in capable["slot_profile_template_flags"]

    incapable = _resolve_llama_scalars(
        _pf_slot(profile="promptforge", binary="rocmfpx"), model, profile
    )
    assert incapable["slot_profile_template_flags"] == ""


# ── I2 (fix wave): the read-only ctx surface agrees with the launch ──────────


class _FakeRegistry:
    """Minimal ModelRegistry stand-in for the read-only wrapper."""

    def __init__(self, dump):
        self._dump = dump

    def get(self, model_id):
        from types import SimpleNamespace

        return SimpleNamespace(model_dump=lambda: dict(self._dump))


def test_degraded_slot_reports_the_ctx_it_launches_with():
    """``resolve_effective_context_size`` — the read-only wrapper backing
    ``ctx_max`` on the slots list, the detail route and ``/v1/models`` — used
    to take ``_resolve_context_size``'s ``specialty_degraded=None`` default
    ("accelerated") unconditionally, so a PromptForge model on a ``rocmfpx``
    slot LAUNCHED at 8192 while the drawer's "ctx used / max" pane advertised
    262144. Given the slot's own cfg it now reaches the same guard verdict the
    launch path does, through the same ``_resolve_llama_scalars`` choke point.
    """
    model = _pf_model()
    registry = _FakeRegistry(model)
    profile = ProfileConfig()

    accelerated_cfg = _pf_slot(profile="promptforge", binary="promptforge")
    degraded_cfg = _pf_slot(profile="promptforge", binary="rocmfpx")

    with patch("hal0.providers.container._best_effort_model_info", return_value=model):
        advertised_accel = resolve_effective_context_size(
            300_000, registry, "qwen-pf", slot_name="s", slot_cfg=accelerated_cfg
        )
        advertised_degraded = resolve_effective_context_size(
            300_000, registry, "qwen-pf", slot_name="s", slot_cfg=degraded_cfg
        )

    # parity with what the launch path actually resolves for the same slots
    launched_accel = _resolve_llama_scalars(accelerated_cfg, model, profile)["context_size"]
    launched_degraded = _resolve_llama_scalars(degraded_cfg, model, profile)["context_size"]

    assert launched_accel == 262_144
    assert launched_degraded == _CTX_SAFE_FALLBACK
    assert advertised_accel == launched_accel
    assert advertised_degraded == launched_degraded  # was 262144 pre-fix


def test_ctx_wrapper_without_slot_cfg_is_unchanged():
    """Back-compat pin: a caller with no slot cfg in hand (and therefore no way
    to know the guard's verdict) keeps the pre-fix accelerated assumption
    rather than degrading every specialty model on the wire."""
    model = _pf_model()
    assert (
        resolve_effective_context_size(300_000, _FakeRegistry(model), "qwen-pf", slot_name="s")
        == 262_144
    )


def test_ctx_wrapper_plain_model_never_touches_the_preview_bundle():
    """The specialty gate is what keeps the ~2s slot poll cheap: a plain model
    must not pay for a preview-bundle resolution just because a slot cfg was
    passed."""
    plain = {"_model_key": "plain", "metadata": {}}
    calls = []

    def _boom(cfg, model_path=None):
        calls.append(cfg)
        raise AssertionError("preview bundle must not be resolved for a plain model")

    with patch("hal0.providers.container._resolve_preview_bundle", _boom):
        ctx = resolve_effective_context_size(
            65_536,
            _FakeRegistry(plain),
            "plain",
            slot_name="s",
            slot_cfg=_pf_slot(profile="promptforge", binary="rocmfpx"),
        )
    assert ctx == _CTX_SAFE_FALLBACK
    assert calls == []
