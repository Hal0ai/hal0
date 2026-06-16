"""`hal0 setup` — first-run configuration TUI (spec §6).

Hybrid execution: in-process ``apply_setup`` when hal0-api is unreachable
(install time), through ``POST /api/install/apply`` when it is up (so the
running service registers the new slots without a restart — roster coherence,
spec §11)."""

from __future__ import annotations

import asyncio

import httpx
import typer

from hal0.cli._shared import _api_base
from hal0.config import paths
from hal0.config.schema import HardwareInfo
from hal0.hardware.probe import HardwareProbe
from hal0.install.extensions import EXTENSIONS, get_extension
from hal0.install.orchestrate import Selections, SlotSelection
from hal0.install.suggest import suggest_models

#: capability → (slot_name, port). Mirrors installer.py:_SLOT_META for the
#: two slots first-run provisions.
_SETUP_SLOTS = {"chat": ("chat", 8081), "coder": ("coder", 8082)}


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
    existing_slots: frozenset[str] = frozenset(),
) -> Selections:
    """Non-interactive defaults for ``--auto`` (install.sh path): recommended
    model per slot, default extension set, NPU trio on if present.

    When *with_extensions* is ``False`` every extension is disabled (all keys
    present, all values ``False``).  The coder/agent slot is gated on an agent
    extension being enabled, so it is skipped when extensions are off.  The
    chat slot is always included.

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
    # Main (chat) is always provisioned in --auto (OWUI + Hermes default on)
    # — unless it was already configured by a prior install.
    if "chat" not in existing_slots:
        chat = suggest_models("chat", hw, limit=1)
        if chat:
            name, port = _SETUP_SLOTS["chat"]
            slots.append(SlotSelection("chat", name, port, chat[0].model_id))
    # Agent slot only if an agent extension is enabled and not already present.
    if (
        any(_kind(eid) == "agent" and on for eid, on in ext.items())
        and "coder" not in existing_slots
    ):
        coder = suggest_models("coder", hw, limit=1, prefer_coder=True)
        if coder:
            name, port = _SETUP_SLOTS["coder"]
            slots.append(SlotSelection("coder", name, port, coder[0].model_id))
    # Record ComfyUI default capability picks as (capability_id, family) pairs.
    # No model pull at install — operator triggers downloads later via
    # POST /api/comfyui/models/fetch.
    from hal0.comfyui.capabilities import CAPABILITIES as _CAPS

    comfyui_defaults = tuple((cap_id, cap.alternatives[0].family) for cap_id, cap in _CAPS.items())
    return Selections(
        storage_dir=storage_dir,
        slots=slots,
        extensions=ext,
        npu_opt_in=bool(hw.npu.present),
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
) -> None:
    hw = HardwareProbe().probe()
    if auto:
        sel = build_auto_selections(
            hw,
            storage_dir=storage_dir,
            with_extensions=not no_extensions,
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
