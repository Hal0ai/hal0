"""Install-time agent-anchor provisioning — the OPT-IN half (v1.0, Stream A).

The brain pull makes steward CHAT work. This makes steward TOOL CALLS work:
the 1B brain model does not emit tool calls, so tool turns route to
``[brain_chat] tool_model`` (default ``hal0/agent``) — and ``agent.toml`` ships
model-less, so that target is empty on a fresh box.

The properties under test are the ones that would turn a 15-31 GB download into
an incident:

* it is never pulled without consent, and the consent prompt states the size;
* a headless install NEVER blocks and NEVER pulls (the prompt is bash-side, so
  the invariant tested here is that ``--plan`` is pure and side-effect-free and
  that install.sh's gate is the same ``_interactive`` one the other prompts
  use — asserted against install.sh's text in
  ``tests/installer/test_install_single_entry_point.py``);
* declining, skipping, or failing all leave the install successful;
* the size printed is the size that gets downloaded (no bash-side hardcode).

No test reaches huggingface.co — ``run_pull`` is stubbed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.install.agent_model import (
    AGENT_MODEL_ANCHOR,
    AGENT_MODEL_IDS,
    AGENT_MODEL_LEAN,
    AGENT_MODEL_QUALITY,
    AGENT_SLOT_NAME,
    SKIP_NOTICE,
    agent_model_for_hardware,
    describe_offer,
    main,
    provision_agent_model,
)
from hal0.registry.curated import get_curated


def _hw(
    *,
    platform: str = "generic",
    gpus: list[GPUInfo] | None = None,
    unified_mb: int = 0,
    ram_mb: int = 0,
) -> HardwareInfo:
    return HardwareInfo(
        platform=platform,
        gpus=gpus or [],
        unified_memory_mb=unified_mb,
        ram_mb=ram_mb,
    )


def _strix(unified_mb: int) -> HardwareInfo:
    return _hw(platform="strix-halo", unified_mb=unified_mb)


# ── who gets offered what ───────────────────────────────────────────────────


def test_no_gpu_box_is_offered_nothing() -> None:
    """Every rung is a ROCmFPX/MTP repack.

    Offering a 15 GB download to a CPU-only box that would then refuse to load
    it is strictly worse than offering nothing — same reasoning as the brain
    module's Q8-vs-F16 split, inverted: there is no portable agent build to
    fall back to, so the correct answer is "no offer".
    """
    assert agent_model_for_hardware(_hw(ram_mb=128 * 1024)) is None
    assert describe_offer(_hw(ram_mb=128 * 1024)) is None


def test_full_pool_gets_the_quality_rung() -> None:
    assert agent_model_for_hardware(_strix(128 * 1024)) == AGENT_MODEL_QUALITY


def test_mid_pool_gets_the_lean_35b_rung() -> None:
    """~24 GB fits the 19.05 GB STRIX_LEAN build but not the 31.41 GB one."""
    assert agent_model_for_hardware(_strix(24 * 1024)) == AGENT_MODEL_ANCHOR


def test_small_pool_gets_the_27b_rung() -> None:
    assert agent_model_for_hardware(_strix(16 * 1024)) == AGENT_MODEL_LEAN


def test_pool_below_the_smallest_rung_is_offered_nothing() -> None:
    """A box that cannot serve even the 27B must not be asked to download it."""
    assert agent_model_for_hardware(_strix(8 * 1024)) is None
    assert describe_offer(_strix(8 * 1024)) is None


def test_vulkan_only_gpu_counts_as_capable() -> None:
    hw = _hw(gpus=[GPUInfo(vendor="amd", vulkan_capable=True)], ram_mb=64 * 1024)
    assert agent_model_for_hardware(hw) == AGENT_MODEL_QUALITY


def test_ram_is_used_when_there_is_no_unified_pool() -> None:
    hw = _hw(platform="strix-halo", unified_mb=0, ram_mb=24 * 1024)
    assert agent_model_for_hardware(hw) == AGENT_MODEL_ANCHOR


def test_override_selects_a_known_rung_even_on_a_small_box() -> None:
    assert agent_model_for_hardware(_strix(8 * 1024), override=AGENT_MODEL_QUALITY) == (
        AGENT_MODEL_QUALITY
    )
    assert agent_model_for_hardware(_strix(8 * 1024), override=f" {AGENT_MODEL_LEAN}  ") == (
        AGENT_MODEL_LEAN
    )


def test_unknown_override_falls_back_instead_of_404ing() -> None:
    assert agent_model_for_hardware(_strix(128 * 1024), override="chadrock-9000") == (
        AGENT_MODEL_QUALITY
    )


# ── the consent prompt states a REAL size ───────────────────────────────────


@pytest.mark.parametrize("model_id", AGENT_MODEL_IDS)
def test_every_rung_is_pullable(model_id: str) -> None:
    curated = get_curated(model_id)
    assert curated is not None, f"{model_id!r} missing from CURATED_MODELS"
    assert curated.hf_repo and curated.hf_file.endswith(".gguf")
    assert curated.capability == "chat"


def test_the_rungs_are_ordered_largest_first() -> None:
    """``agent_model_for_hardware`` takes the FIRST rung that fits, so the
    ladder must descend or a big box would land on a small model."""
    mins = [get_curated(m).vram_gb_min for m in AGENT_MODEL_IDS]  # type: ignore[union-attr]
    assert mins == sorted(mins, reverse=True)
    sizes = [get_curated(m).size_gb for m in AGENT_MODEL_IDS]  # type: ignore[union-attr]
    assert sizes == sorted(sizes, reverse=True)


def test_offer_line_is_id_tab_sentence_and_carries_the_curated_size() -> None:
    """install.sh splits on the tab and prints the sentence verbatim.

    The GB figure lives on the curated row and is rendered here, so bash never
    hardcodes one — the number consented to is the number downloaded.
    """
    offer = describe_offer(_strix(128 * 1024))
    assert offer is not None
    model_id, _, sentence = offer.partition("\t")
    assert model_id == AGENT_MODEL_QUALITY
    curated = get_curated(model_id)
    assert curated is not None
    assert f"{curated.size_gb:.2f} GB" in sentence
    assert curated.display_name in sentence
    assert "\n" not in offer


def test_offer_sizes_match_the_measured_hf_blob_sizes() -> None:
    """Exact bytes/1e9 from the HF API, 2026-07-30. These are printed to the
    operator as the download size, so a drifted figure is a lie, not a nit."""
    expected = {
        AGENT_MODEL_QUALITY: 31.41,  # 31,410,670,848 B
        AGENT_MODEL_ANCHOR: 19.05,  # 19,046,929,664 B
        AGENT_MODEL_LEAN: 14.82,  # 14,817,251,520 B
    }
    for model_id, size in expected.items():
        curated = get_curated(model_id)
        assert curated is not None
        assert curated.size_gb == pytest.approx(size, abs=0.01), model_id


def test_skip_notice_names_the_setting_and_the_slot() -> None:
    """The whole point of the notice is that a blank tool call is never a
    mystery: it must say what is missing and where to fix it."""
    assert "tool_model" in SKIP_NOTICE
    assert "hal0/agent" in SKIP_NOTICE
    assert "agent" in SKIP_NOTICE


# ── provisioning ────────────────────────────────────────────────────────────


class _FakeRegistry:
    def __init__(self) -> None:
        self.ensured: list[str] = []

    def ensure(self, model_id: str) -> None:
        self.ensured.append(model_id)


class _FakeSlotManager:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def update_config(self, slot: str, updates: dict[str, Any]) -> None:
        self.updates.append((slot, updates))


def _stub_run_pull(monkeypatch: pytest.MonkeyPatch, *, state: str, exc: Exception | None = None):
    import hal0.registry.pull as pull_mod

    async def _fake(job, **kwargs):
        if exc is not None:
            raise exc
        job.state = state

    monkeypatch.setattr(pull_mod, "run_pull", _fake)


def test_successful_pull_binds_the_model_to_the_agent_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_run_pull(monkeypatch, state="completed")
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    landed = asyncio.run(
        provision_agent_model(hw=_strix(128 * 1024), slot_manager=sm, registry=reg)
    )
    assert landed == AGENT_MODEL_QUALITY
    assert reg.ensured == [AGENT_MODEL_QUALITY]
    assert sm.updates == [(AGENT_SLOT_NAME, {"model": {"default": AGENT_MODEL_QUALITY}})]


def test_failed_pull_leaves_the_agent_slot_model_less(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_run_pull(monkeypatch, state="failed")
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    with pytest.raises(RuntimeError):
        asyncio.run(provision_agent_model(hw=_strix(128 * 1024), slot_manager=sm, registry=reg))
    assert sm.updates == [(AGENT_SLOT_NAME, {"meta": {"pull_failed": True}})]
    assert all("model" not in upd for _, upd in sm.updates)


def test_provisioning_an_unfit_box_raises_before_touching_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-braces: even if install.sh ever called the pull half without
    the plan half agreeing, it must refuse rather than invent a rung."""
    _stub_run_pull(monkeypatch, state="completed")
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    with pytest.raises(RuntimeError, match="no agent anchor fits"):
        asyncio.run(provision_agent_model(hw=_hw(ram_mb=128 * 1024), slot_manager=sm, registry=reg))
    assert sm.updates == []
    assert reg.ensured == []


# ── the install.sh entry point ──────────────────────────────────────────────


def test_plan_prints_nothing_and_exits_zero_when_there_is_no_offer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty stdout is how install.sh learns "make no offer" — and exit 0 keeps
    it out of the ``set -e`` blast radius."""
    import hal0.install.agent_model as am

    monkeypatch.setattr(am, "_load_hardware", lambda: _hw(ram_mb=128 * 1024))
    assert main(["--plan"]) == 0
    assert capsys.readouterr().out == ""


def test_plan_prints_one_line_when_there_is_an_offer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import hal0.install.agent_model as am

    monkeypatch.setattr(am, "_load_hardware", lambda: _strix(128 * 1024))
    assert main(["--plan"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert out.split("\t")[0] == AGENT_MODEL_QUALITY


def test_plan_never_pulls(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--plan`` is the consent-gathering half. If it could download, the
    consent would be theatre."""
    import hal0.install.agent_model as am
    import hal0.registry.pull as pull_mod

    monkeypatch.setattr(am, "_load_hardware", lambda: _strix(128 * 1024))

    async def _explode(job, **kwargs):
        raise AssertionError("--plan must never reach the pull engine")

    monkeypatch.setattr(pull_mod, "run_pull", _explode)
    assert main(["--plan"]) == 0


def test_plan_exits_zero_even_when_the_probe_explodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plan that cannot be computed is no offer, not a failed install."""
    import hal0.install.agent_model as am

    def _boom() -> None:
        raise RuntimeError("hardware probe exploded")

    monkeypatch.setattr(am, "_load_hardware", _boom)
    assert main(["--plan"]) == 0


def test_main_returns_nonzero_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.install.agent_model as am

    def _boom() -> None:
        raise RuntimeError("hardware probe exploded")

    monkeypatch.setattr(am, "_load_hardware", _boom)
    assert main([]) == 1


def test_failed_main_prints_the_skip_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import hal0.install.agent_model as am

    monkeypatch.setattr(am, "_load_hardware", lambda: _hw(ram_mb=128 * 1024))
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (_FakeSlotManager(), _FakeRegistry()),
    )
    assert main([]) == 1
    assert "tool_model" in capsys.readouterr().err


def test_main_returns_zero_on_a_successful_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.install.agent_model as am

    monkeypatch.setattr(am, "_load_hardware", lambda: _strix(128 * 1024))
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (_FakeSlotManager(), _FakeRegistry()),
    )
    _stub_run_pull(monkeypatch, state="completed")
    assert main([]) == 0
