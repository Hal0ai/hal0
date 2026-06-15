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


def build_auto_selections(hw: HardwareInfo, *, storage_dir: str) -> Selections:
    """Non-interactive defaults for ``--auto`` (install.sh path): recommended
    model per slot, default extension set, NPU trio on if present."""
    ext = {e.id: e.default_enabled for e in EXTENSIONS}
    slots: list[SlotSelection] = []
    # Main (chat) is always provisioned in --auto (OWUI + Hermes default on).
    chat = suggest_models("chat", hw, limit=1)
    if chat:
        name, port = _SETUP_SLOTS["chat"]
        slots.append(SlotSelection("chat", name, port, chat[0].model_id))
    # Agent slot only if an agent extension is enabled.
    if any(_kind(eid) == "agent" and on for eid, on in ext.items()):
        coder = suggest_models("coder", hw, limit=1, prefer_coder=True)
        if coder:
            name, port = _SETUP_SLOTS["coder"]
            slots.append(SlotSelection("coder", name, port, coder[0].model_id))
    return Selections(
        storage_dir=storage_dir, slots=slots, extensions=ext, npu_opt_in=bool(hw.npu.present)
    )


app = typer.Typer(help="First-run setup")


@app.callback(invoke_without_command=True)
def setup(
    auto: bool = typer.Option(False, "--auto", help="Non-interactive; recommended defaults."),
    storage_dir: str = typer.Option("/var/lib/hal0/models", "--storage-dir"),
) -> None:
    hw = HardwareProbe().probe()
    if auto:
        sel = build_auto_selections(hw, storage_dir=storage_dir)
        asyncio.run(_run_auto(sel, hw))
        return
    from hal0.cli.setup_ui import run_interactive  # Task 3.x

    run_interactive(hw, storage_dir=storage_dir)


async def _run_auto(sel: Selections, hw: HardwareInfo) -> None:
    """Apply the auto-selected config. Routes hybrid (in-process at install
    time when the API is down; via the API when it is up, so a post-install
    `hal0 setup --auto` on a live service doesn't drift the roster)."""
    from hal0.cli.setup_install import run_install

    await run_install(sel, hw)


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
