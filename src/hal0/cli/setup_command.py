"""`hal0 setup` — first-run configuration TUI (spec §6).

Hybrid execution: in-process ``apply_setup`` when hal0-api is unreachable
(install time), through ``POST /api/install/apply`` when it is up (so the
running service registers the new slots without a restart — roster coherence,
spec §11)."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import typer

from hal0.cli._shared import _api_base
from hal0.config import paths
from hal0.config.schema import HardwareInfo
from hal0.hardware.probe import HardwareProbe, HardwareProbeError
from hal0.install.extensions import EXTENSIONS, get_extension
from hal0.install.orchestrate import Selections, SlotSelection
from hal0.install.profile_derive import npu_healthy

#: capability → (slot_name, port) for the slots first-run provisions. Mirrors
#: installer.py:_SLOT_META for the shared capabilities (chat/coder/embed/stt/
#: tts) and adds rerank/vision on free ports in the 8081-8099 pool. ``img`` is
#: handled via the ComfyUI ``comfyui_defaults`` sidecar, not this table.
_SETUP_SLOTS = {
    "chat": ("chat", 8081),
    "coder": ("coder", 8082),
    "embed": ("embed", 8083),
    "stt": ("stt", 8084),
    "tts": ("tts", 8085),
    "rerank": ("rerank", 8086),
    "vision": ("vision", 8087),
}

#: Capabilities scaffolded (empty, no model) by ``--auto``. ``coder`` is added
#: separately, gated on an agent extension. ``apply_setup`` derives each slot's
#: device and skips any that don't apply to the hardware (e.g. NPU-only ``stt``
#: without ``npu_opt_in``).
_SCAFFOLD_CAPS = ("chat", "embed", "rerank", "stt", "tts", "vision")


def _api_reachable(timeout: float = 0.5) -> bool:
    try:
        r = httpx.get(f"{_api_base()}/api/install/state", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def _kind(ext_id: str) -> str | None:
    e = get_extension(ext_id)
    return e.kind if e else None


def _existing_slot_names() -> frozenset[str]:
    """Return the stems of ``*.toml`` files in the slots config dir.

    Returns an empty frozenset when the directory does not yet exist
    (fresh install with no prior slot configs).
    """
    slots_dir = paths.slots_config_dir()
    if not slots_dir.exists():
        return frozenset()
    return frozenset(p.stem for p in slots_dir.glob("*.toml"))


def build_auto_selections(
    hw: HardwareInfo,
    *,
    storage_dir: str,
    with_extensions: bool = True,
    with_slots: bool = True,
    existing_slots: frozenset[str] = frozenset(),
) -> Selections:
    """Non-interactive defaults for ``--auto`` (install.sh path): scaffold the
    capability + NPU slot structure with **no model picks** (pick-free) plus the
    default extension set; NPU routing on when an NPU is present.

    Each provisioned slot is an *empty scaffold* — ``SlotSelection.model_id`` is
    ``None`` so ``apply_setup`` wires device/profile/port but leaves the model
    unset for the operator to choose later.  We never pick a model for the user.

    When *with_extensions* is ``False`` every extension is disabled (all keys
    present, all values ``False``).  The coder/agent slot is gated on an agent
    extension being enabled, so it is skipped when extensions are off.  The
    chat slot is always included.

    When *with_slots* is ``False`` NO model picks are seeded at all — the
    returned selection has an empty slot list and no ComfyUI capability
    defaults.  This is the installer's mode (Task: don't ship model picks): a
    fresh box gets the first-run sentinel + extension wiring but zero model
    selections, so the operator chooses everything later via the dashboard or
    ``hal0 setup``.  Extensions still honour *with_extensions*.

    *existing_slots* is the set of slot names whose config files already exist
    on disk.  Any slot in this set is skipped so that ``--auto`` on an existing
    install does not overwrite user-customised configs.  Pass the result of
    :func:`_existing_slot_names` at the call site to keep this function pure
    and unit-testable.
    """
    if with_extensions:
        ext = {e.id: e.default_enabled for e in EXTENSIONS}
    else:
        ext = {e.id: False for e in EXTENSIONS}
    slots: list[SlotSelection] = []
    comfyui_defaults: tuple[tuple[str, str], ...] = ()
    if with_slots:
        # Scaffold the capability + NPU slot STRUCTURE with no model picks
        # (pick-free): device/profile/port are wired but ``model.default`` is
        # left unset for the operator to fill later. apply_setup derives each
        # slot's device and skips any not applicable to the hardware (e.g.
        # NPU-only ``stt`` without an NPU / opt-in). Existing configs are never
        # overwritten.
        for cap in _SCAFFOLD_CAPS:
            name, port = _SETUP_SLOTS[cap]
            if name not in existing_slots:
                slots.append(SlotSelection(cap, name, port, None))
        # Coder slot only when an agent extension is enabled (and not present).
        if (
            any(_kind(eid) == "agent" and on for eid, on in ext.items())
            and "coder" not in existing_slots
        ):
            name, port = _SETUP_SLOTS["coder"]
            slots.append(SlotSelection("coder", name, port, None))
        # Record ComfyUI default image capability picks as (capability_id,
        # family) pairs. No pull at install — the operator triggers downloads
        # later via POST /api/comfyui/models/fetch.
        from hal0.comfyui.capabilities import CAPABILITIES as _CAPS

        comfyui_defaults = tuple(
            (cap_id, cap.alternatives[0].family) for cap_id, cap in _CAPS.items()
        )
    return Selections(
        storage_dir=storage_dir,
        slots=slots,
        extensions=ext,
        # NPU routing on ONLY when present AND healthy (#1109): a present-but-
        # broken NPU (npu.validated False/None) must not auto-advertise a lane
        # apply_setup would skip. Same single npu_opt_in the picker + apply use.
        npu_opt_in=npu_healthy(hw),
        comfyui_defaults=comfyui_defaults,
    )


app = typer.Typer(help="First-run setup")


@app.callback(invoke_without_command=True)
def setup(
    auto: bool = typer.Option(False, "--auto", help="Non-interactive; recommended defaults."),
    storage_dir: str = typer.Option("/var/lib/hal0/models", "--storage-dir"),
    no_pull: bool = typer.Option(
        False,
        "--no-pull",
        help="Seed slots + sentinel without downloading models.",
    ),
    no_extensions: bool = typer.Option(
        False,
        "--no-extensions",
        help="Skip extension install/wiring in --auto mode.",
    ),
    no_slots: bool = typer.Option(
        False,
        "--no-slots",
        help="Seed the sentinel + extensions but NO model-slot picks (installer default; operator chooses models later).",
    ),
    answers: str | None = typer.Option(
        None,
        "--answers",
        help="Path to a hal0-setup.yaml answer file for a fully non-interactive run.",
    ),
    emit_answers: str | None = typer.Option(
        None,
        "--emit-answers",
        help="Write the resolved choices to a hal0-setup.yaml and exit.",
    ),
    plan: bool = typer.Option(
        False,
        "--plan",
        "--dry-run",
        help="Resolve + print the plan; write nothing (no slots, sentinel, pulls, or extensions).",
    ),
) -> None:
    # Two-stage handoff (issue #1112): the guided Stage-2 flow is interactive
    # (rich prompts on a real TTY). When it is requested (no --auto / --answers /
    # --plan / --emit-answers) but stdin is NOT a terminal — a piped/headless
    # `curl … | hal0 setup` or CI run — DON'T launch rich prompts (they would
    # EOF immediately). Print the exact command to run instead, mirroring what
    # install.sh's Stage-1 tail prints on a headless install. HAL0_FORCE_INTERACTIVE
    # bypasses the guard so tests can drive the flow over a pipe.
    interactive = not (auto or plan or answers is not None or emit_answers is not None)
    if interactive and not sys.stdin.isatty() and not os.environ.get("HAL0_FORCE_INTERACTIVE"):
        typer.echo("hal0 setup is interactive — run it from a terminal:")
        typer.echo("  hal0 setup")
        typer.echo("or run non-interactively with recommended defaults:")
        typer.echo("  hal0 setup --auto")
        return

    probe = HardwareProbe()
    # --plan / --emit-answers are side-effect-free preview paths: a light probe
    # (no NPU validation, no hardware.json write) so they honour their
    # "writes nothing" contract.
    if plan:
        from hal0.cli.setup_plan import run_plan

        hw = probe.probe()
        raise typer.Exit(
            code=run_plan(
                hw,
                answers=answers,
                storage_dir=storage_dir,
                no_extensions=no_extensions,
                no_slots=no_slots,
            )
        )
    if emit_answers is not None:
        from hal0.install.answers import load_answers, write_answers

        hw = probe.probe()
        sel = (
            load_answers(answers, hw)
            if answers is not None
            else build_auto_selections(
                hw,
                storage_dir=storage_dir,
                with_extensions=not no_extensions,
                with_slots=not no_slots,
                existing_slots=_existing_slot_names(),
            )
        )
        write_answers(sel, emit_answers)
        typer.echo(f"Wrote resolved setup answers to {emit_answers}")
        return
    # Real-apply paths only: persist an authoritative hardware.json so slots and
    # the FLM container spec read REAL device facts (not the Strix-Halo constant
    # fallbacks). validate_npu=True records the functional `flm validate` result
    # (npu.validated) before any slot launches.
    hw = probe.probe(validate_npu=True)
    try:
        probe.write(hw)
    except HardwareProbeError as exc:
        # Non-fatal: a non-root `hal0 setup` can't write /etc/hal0. The probe
        # result still drives this run; the daemon persists it later.
        typer.echo(f"warning: could not persist hardware.json ({exc})", err=True)
    if answers is not None:
        from hal0.install.answers import gen_download_requested, load_answers

        sel = load_answers(answers, hw)
        asyncio.run(_run_auto(sel, hw, no_pull=no_pull))
        # WS-G (#1113): gen.mode: scaffold_and_download opts into the ComfyUI
        # per-variant fetch. The loader records the picks in comfyui_defaults;
        # perform the working download here (skipped under --no-pull, matching
        # the LLM-slot pull deferral). The img slot activates on the first land.
        if not no_pull and gen_download_requested(answers) and sel.comfyui_defaults:
            from hal0.comfyui.provision import provision_comfyui_downloads

            provision_comfyui_downloads(sel.comfyui_defaults)
        return
    if auto:
        sel = build_auto_selections(
            hw,
            storage_dir=storage_dir,
            with_extensions=not no_extensions,
            with_slots=not no_slots,
            existing_slots=_existing_slot_names(),
        )
        asyncio.run(_run_auto(sel, hw, no_pull=no_pull))
        return
    from hal0.cli.setup_ui import run_interactive  # Task 3.x

    run_interactive(hw, storage_dir=storage_dir)


async def _run_auto(sel: Selections, hw: HardwareInfo, *, no_pull: bool = False) -> None:
    """Apply the auto-selected config. Routes hybrid (in-process at install
    time when the API is down; via the API when it is up, so a post-install
    `hal0 setup --auto` on a live service doesn't drift the roster)."""
    from hal0.cli.setup_install import run_install

    await run_install(sel, hw, no_pull=no_pull)


def _build_offline_deps():
    """Construct a SlotManager + model registry WITHOUT a running API, mirroring
    how src/hal0/api/__init__.py builds app.state.slot_manager / app.state.model_registry
    in the lifespan function.

    From app.py lifespan (lines ~701-795):
        model_registry = ModelRegistry()          # bare constructor, no args
        event_bus = EventBus(sink=None)           # no audit sink offline
        slot_manager = SlotManager(event_bus=event_bus, upstreams_registry=None)
                                                  # upstreams_registry=None: skip
                                                  # container upstream wiring

    ModelRegistry() with no args resolves its directory from
    hal0.config.paths.registry_dir() at call time (honours HAL0_HOME).
    SlotManager with event_bus=None and upstreams_registry=None is the
    CLI / unit-test construction path.
    """
    from hal0.events import EventBus
    from hal0.registry.store import ModelRegistry
    from hal0.slots.manager import SlotManager

    model_registry = ModelRegistry()
    event_bus = EventBus(sink=None)
    slot_manager = SlotManager(event_bus=event_bus, upstreams_registry=None)
    return slot_manager, model_registry
