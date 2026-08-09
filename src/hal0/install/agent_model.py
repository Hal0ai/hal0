"""Install-time OPT-IN provisioning of the `agent` anchor model (v1.0).

Why this exists — the fallback gap it closes
--------------------------------------------
:mod:`hal0.install.brain_model` makes the steward work out of the box: as of
runner ade07ba (``DEFAULT_ROCMFPX_IMAGE``) the brain models are NATIVE
tool-callers, so the steward chats AND executes its own calls on the brain
slot (``installer/etc-hal0/slots/brain.toml``, verified live on the SFT
quants and the MiniCPM5 base). The ``[brain_chat] tool_model`` reroute
(:class:`~hal0.config.schema.BrainChatConfig`, default ``"hal0/agent"``)
remains as the FALLBACK: it catches artefact rounds and covers older runner
images or off-dialect models — and a bound agent model is also simply a much
bigger tool-caller than the ~1B brain.

But ``installer/etc-hal0/slots/agent.toml`` ships WITHOUT a ``[model].default``
— deliberately, per spec-p3-brain §5b/5c: model-presence is the activation
signal (#1369) and a surprise 20 GB download during a platform install is not
acceptable. So on a fresh box the fallback resolves to a slot with no model:
the steward still tool-calls, but without a net and without the quality
upgrade, until an operator picks something.

This module is the bridge, and the shape of it is the whole point:

**Opt-in, size disclosed, default SKIP.** Unlike the brain pull (which is
unconditional and ~1-2 GB), the agent anchor is 15-31 GB. install.sh asks
once, on a terminal only, printing the exact size first, and Enter means no.

**A headless install must never block and never pull.** The prompt sits behind
the same ``_interactive`` gate as the model-store and HF-token questions, so
``curl | bash``, ``ssh host 'bash install.sh'`` and ``HAL0_NONINTERACTIVE=1``
all take the skip path without asking. ``HAL0_PULL_AGENT_MODEL=1`` is the
explicit unattended opt-in for automation that genuinely wants the bytes.

**Never fatal.** Same posture as the brain pull (ruling 7): a declined,
skipped or failed agent pull leaves the slot model-less, prints what that
means for tool calls, and the install still succeeds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from hal0.config.schema import HardwareInfo
from hal0.install.brain_model import already_pulled, rocmfpx_capable

log = logging.getLogger(__name__)

#: The slot this binds to. ADR-0023's always-on LLM anchor, and the model
#: ``[brain_chat] tool_model`` resolves to by default (``hal0/agent``).
AGENT_SLOT_NAME = "agent"

#: The anchor ladder, LARGEST FIRST. Every entry is a chadrock/Ace-Saber
#: ROCmFPX+MTP build — the family ``installer/etc-hal0/slots/agent.toml``'s
#: seeded ``profile = "chadrock-moe"`` recipe is tuned for, and the family
#: ``brain.toml`` records as "confirmed clean native tool-callers on this
#: runtime". Sizes and thresholds live on the curated rows
#: (:mod:`hal0.registry.curated`), not here, so the consent prompt and the
#: catalogue can never disagree.
AGENT_MODEL_QUALITY = "chadrock-35b-ace-saber-moequality-7bpw"
AGENT_MODEL_ANCHOR = "qwen3-6-35b-a3b-nsc-ace-saber-mtp-f16-to-rocmfp4-strix-lean"
AGENT_MODEL_LEAN = "chadrock3-6-27b-pi-agent-mtp-rocmfp4-strix-lean"

#: Ladder order. ``agent_model_for_hardware`` walks it largest-first and takes
#: the first rung whose ``vram_gb_min`` fits the detected pool.
AGENT_MODEL_IDS = (AGENT_MODEL_QUALITY, AGENT_MODEL_ANCHOR, AGENT_MODEL_LEAN)

#: Printed verbatim by install.sh whenever the anchor does NOT land — declined,
#: skipped, headless, no fitting rung, or a failed pull. The one thing an
#: operator must not have to discover from a silent empty reply.
SKIP_NOTICE = (
    "the brain steward chats and calls tools on its own; the [brain_chat] "
    "tool_model fallback (default hal0/agent) has no live target until an "
    "agent model is bound to the agent slot. Bind one from the dashboard, or "
    "run 'hal0 model pull <id> && hal0 slot load agent --model <id>', "
    "whenever you like."
)


def _pool_gb(hw: HardwareInfo) -> float:
    """Detected memory pool in GB — unified first, else system RAM.

    Identical to :func:`hal0.install.suggest._ram_gb` on purpose: the
    install-time offer and the dashboard's fit badges must rank the same
    catalogue the same way, or the prompt recommends something the Models
    view then greys out.
    """
    return (hw.unified_memory_mb or hw.ram_mb) / 1024


def agent_model_for_hardware(hw: HardwareInfo, *, override: str | None = None) -> str | None:
    """The agent-anchor id to OFFER on this box, or ``None`` to offer nothing.

    ``None`` — never an error — is returned when:

    * the box has no ROCm/Vulkan device. Every rung is a ROCmFPX/MTP repack;
      on a stock-llama.cpp CPU lane they are the wrong build entirely, and
      offering a 15 GB download that then refuses to load is worse than
      offering nothing.
    * nothing fits. The smallest rung wants ~15 GB; a 16 GB box that just
      spent some of it on the brain has no business being offered one.

    *override* (``HAL0_AGENT_MODEL``) wins when it names a known rung, so an
    operator can force the quality build on a box the ladder would have put on
    a lower rung. An unrecognised override is ignored with a warning rather
    than honoured — a typo must not become a 404 pull.
    """
    from hal0.registry.curated import get_curated

    if override:
        override = override.strip()
        if override in AGENT_MODEL_IDS:
            return override
        log.warning(
            "install.agent_model_override_unknown id=%s known=%s", override, AGENT_MODEL_IDS
        )
    if not rocmfpx_capable(hw):
        return None
    pool = _pool_gb(hw)
    for model_id in AGENT_MODEL_IDS:  # largest first
        curated = get_curated(model_id)
        if curated is not None and curated.vram_gb_min <= pool + 0.01:
            return model_id
    return None


def describe_offer(hw: HardwareInfo, *, override: str | None = None) -> str | None:
    """One tab-separated ``id\\tsentence`` line for install.sh, or ``None``.

    ``--plan`` prints this and nothing else; empty stdout means "make no
    offer". Keeping the size string on the Python side of the boundary is what
    guarantees the number the operator consents to is the number
    :data:`~hal0.registry.curated.CURATED_MODELS` will actually download —
    bash never hardcodes a GB figure.
    """
    from hal0.registry.curated import get_curated

    model_id = agent_model_for_hardware(hw, override=override)
    if model_id is None:
        return None
    curated = get_curated(model_id)
    if curated is None:  # pragma: no cover — guarded by test_agent_model.py
        return None
    if already_pulled(model_id) is not None:
        # Re-run of install.sh on a box that already has these bytes. Quoting
        # a multi-gigabyte "download" for a file that is sitting on disk would
        # scare an operator out of a free, instant bind.
        return (
            f"{model_id}\t{curated.display_name} — already downloaded; "
            "binding it to the agent slot costs nothing"
        )
    return (
        f"{model_id}\t{curated.display_name} — {curated.size_gb:.2f} GB download "
        f"(~{curated.vram_gb_min:.0f} GB memory to serve)"
    )


async def provision_agent_model(
    *,
    hw: HardwareInfo,
    slot_manager,
    registry,
    hf_token: str | None = None,
    override: str | None = None,
) -> str:
    """Pull the agent anchor and bind it to the ``agent`` slot.

    Returns the curated id that landed. Raises on failure — :func:`main` turns
    that into a warning + non-zero exit so install.sh's ``|| warn`` keeps the
    install alive. Mirrors :func:`hal0.install.brain_model.provision_brain_model`
    exactly, including the ``run_pull_and_activate`` guarantee that
    ``[model].default`` is stamped only after the bytes land.
    """
    from hal0.install.orchestrate import PullPlan, run_pull_and_activate
    from hal0.registry.curated import get_curated
    from hal0.registry.pull import make_job

    model_id = agent_model_for_hardware(hw, override=override)
    if model_id is None:
        raise RuntimeError(
            "no agent anchor fits this box (needs a ROCm/Vulkan device and "
            f">= {_smallest_rung_gb():.0f} GB of pool) — nothing to pull"
        )
    curated = get_curated(model_id)
    if curated is None:  # pragma: no cover — guarded by test_agent_model.py
        raise RuntimeError(
            f"agent model {model_id!r} is not in the curated catalogue "
            "(registry/curated.py) — nothing to pull from"
        )

    if hasattr(registry, "ensure"):
        registry.ensure(model_id)

    # Already on disk? Bind and stop — see brain_model.already_pulled. This
    # matters more here than for the brain: re-downloading 15-31 GB because an
    # operator re-ran install.sh and said yes twice is not a small mistake.
    existing = already_pulled(model_id)
    if existing is not None:
        from hal0.install.orchestrate import _activate_slot_model

        log.info("install.agent_model_already_present id=%s path=%s", model_id, existing)
        await _activate_slot_model(slot_manager, AGENT_SLOT_NAME, model_id, failed=False)
        return model_id

    plan = PullPlan(
        model_id=model_id,
        job=make_job(model_id),
        kwargs=dict(
            hf_repo=curated.hf_repo,
            hf_file=curated.hf_file,
            registry=registry,
            hf_token=hf_token or None,
            capability="chat",
        ),
        slot_names=[AGENT_SLOT_NAME],
    )
    await run_pull_and_activate(plan, slot_manager=slot_manager)
    if getattr(plan.job, "state", None) != "completed":
        raise RuntimeError(
            f"agent model pull ended in state {getattr(plan.job, 'state', '?')!r}: "
            f"{getattr(plan.job, 'error', None)}"
        )
    return model_id


def _smallest_rung_gb() -> float:
    from hal0.registry.curated import get_curated

    rungs = [get_curated(m) for m in AGENT_MODEL_IDS]
    return min((c.vram_gb_min for c in rungs if c is not None), default=0.0)


def _load_hardware() -> HardwareInfo:
    """Same hardware.json-first strategy as the brain pull (see that module)."""
    from hal0.install.brain_model import _load_hardware as _brain_load_hardware

    return _brain_load_hardware()


def main(argv: list[str] | None = None) -> int:
    """``python -m hal0.install.agent_model [--plan|--floor]`` — install.sh entry point.

    ``--plan`` is the *offer* half: it prints one ``id\\tsentence`` line (or
    nothing) and exits 0 without touching the network. install.sh turns that
    into the consent prompt. ``--floor`` prints the smallest curated rung's
    ``vram_gb_min`` as a bare integer (no hardware probe — catalogue only), so
    the "no agent model fits this box" notice can quote the real floor instead
    of a bash-side literal that drifts from the ladder. No arguments is the
    *pull* half, run only after the operator said yes.

    NEVER raises: install.sh runs under ``set -euo pipefail`` and a traceback
    escaping here would abort a whole install over an optional model.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    override = (os.environ.get("HAL0_AGENT_MODEL") or "").strip() or None
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or None

    if "--floor" in argv:
        print(f"{_smallest_rung_gb():.0f}")
        return 0

    if "--plan" in argv:
        try:
            offer = describe_offer(_load_hardware(), override=override)
        except BaseException as exc:
            # A plan that cannot be computed is simply no offer — the operator
            # is never prompted, and the skip notice explains the state.
            log.warning("install.agent_model_plan_failed err=%s", exc)
            return 0
        if offer:
            print(offer)
        return 0

    try:
        from hal0.cli.setup_command import _build_offline_deps

        hw = _load_hardware()
        chosen = agent_model_for_hardware(hw, override=override)
        print(f"  agent model: {chosen}")
        if chosen and already_pulled(chosen) is not None:
            print("  already on disk from an earlier run — binding it, no download")
        slot_manager, registry = _build_offline_deps()
        landed = asyncio.run(
            provision_agent_model(
                hw=hw,
                slot_manager=slot_manager,
                registry=registry,
                hf_token=hf_token,
                override=override,
            )
        )
    except BaseException as exc:
        print(f"  agent model pull failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        print(f"  {SKIP_NOTICE}", file=sys.stderr)
        return 1
    print(f"  agent model ready: {landed} bound to the '{AGENT_SLOT_NAME}' slot")
    print("  brain tool calls now route here ([brain_chat] tool_model = hal0/agent)")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised by install.sh
    raise SystemExit(main())
