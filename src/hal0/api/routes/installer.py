"""First-run wizard backend endpoints (mounted under /api/install).

These are the *server* contract that the FirstRun wizard UI (Team B/E)
consumes; the wizard itself is a separate Vue view.

Endpoints:
    GET  /api/install/state    — surface first-run + has-models +
                                 has-default-slot + openwebui-running
                                 flags so the dashboard can decide
                                 whether to route to FirstRun.
    POST /api/install/probe    — re-run the hardware probe and write
                                 ``/etc/hal0/hardware.json`` atomically.
    POST /api/install/complete — marker call after the wizard finishes;
                                 writes ``/var/lib/hal0/.first_run_done``
                                 so subsequent boots skip the wizard.

The curated-models picker feeds into POST /apply and POST /apply-selections
for multi-slot orchestrated install (design D3/D6).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.bundles import tiers as bundle_tiers
from hal0.config import paths
from hal0.hardware.probe import HardwareProbe
from hal0.install.orchestrate import Selections, SlotSelection, apply_setup
from hal0.registry.curated import CURATED_MODELS
from hal0.registry.pull import run_pull

# Auth was removed in ADR-0012. All endpoints are open on the local
# network; the first-run wizard runs without any credential.

router = APIRouter()


class PickDefaultError(Hal0Error):
    """Validation errors for install orchestration endpoints."""

    code = "install.pick_default_failed"
    status = 400


class CuratedModelNotFound(PickDefaultError):
    """404 — caller asked for a curated id that's not in the catalogue."""

    code = "install.curated_not_found"
    status = 404


_DEFAULT_SLOT = "chat"


def _first_run_sentinel() -> Path:
    """Path to the marker file written after the FirstRun wizard finishes."""
    return paths.var_lib() / ".first_run_done"


def _models_dir_populated() -> bool:
    """True if ``/var/lib/hal0/models/`` exists and contains at least one file."""
    d = paths.models_dir()
    if not d.exists():
        return False
    try:
        for entry in d.iterdir():
            # A nested directory or a model file both count as "populated"
            # — fresh installs have the directory missing or empty.
            if entry.name.startswith("."):
                continue
            return True
    except OSError:
        return False
    return False


def _has_default_slot() -> bool:
    """True if the chat slot TOML exists (chat.toml or legacy primary.toml)."""
    slots_dir = paths.slots_config_dir()
    return (slots_dir / "chat.toml").exists() or (slots_dir / "primary.toml").exists()


async def _openwebui_running() -> bool:
    """Best-effort: ask systemd whether hal0-openwebui.service is active.

    Returns False on hosts without systemctl (CI, mac dev boxes) and on
    any subprocess failure — this flag drives a UI hint, not a hard gate,
    so we tolerate missing infra.
    """
    if shutil.which("systemctl") is None:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "is-active",
            "hal0-openwebui.service",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except (TimeoutError, OSError):
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()  # type: ignore[has-type]
        return False
    return stdout.decode("utf-8", errors="replace").strip() == "active"


@router.get("/state")
async def install_state(request: Request) -> dict[str, Any]:
    """Return the dashboard's first-run gating state.

    Shape::

        {
            "first_run": true,
            "has_models": false,
            "has_default_slot": false,
            "openwebui_running": false,
            "sentinel_path": "/var/lib/hal0/.first_run_done"
        }

    ``first_run`` is true when ``/var/lib/hal0/models/`` is empty AND the
    sentinel hasn't been written. Either condition flipping false hides
    the FirstRun wizard.
    """
    has_models = _models_dir_populated()
    sentinel = _first_run_sentinel()
    sentinel_present = sentinel.exists()
    has_default = _has_default_slot()
    openwebui = await _openwebui_running()
    first_run = (not has_models) and (not sentinel_present)
    return {
        "first_run": first_run,
        "has_models": has_models,
        "has_default_slot": has_default,
        "openwebui_running": openwebui,
        "sentinel_path": str(sentinel),
    }


@router.post("/probe")
async def install_probe(request: Request) -> dict[str, Any]:
    """Re-run the hardware probe and rewrite ``/etc/hal0/hardware.json``.

    Returns the freshly-probed HardwareInfo as JSON. Uses the existing
    ``HardwareProbe.write()`` which already does atomic tempfile+replace.
    """
    probe: HardwareProbe = getattr(request.app.state, "hardware_probe", None) or HardwareProbe()
    info = await probe.probe_async()
    # ``write()`` raises HardwareProbeError on disk failures, which is a
    # Hal0Error subclass — let the envelope middleware surface it.
    target = probe.write(info)
    # Cache on app.state so subsequent /api/hardware reads can skip the
    # re-probe round-trip.
    request.app.state.hardware_info = info
    return {
        "hardware": info.model_dump(mode="json"),
        "path": str(target),
    }


@router.post("/complete")
async def install_complete(request: Request) -> dict[str, Any]:
    """Mark the FirstRun wizard as complete by writing the sentinel.

    Atomic: tempfile + os.replace in the parent directory so a partial
    write can't leave a half-written marker. Idempotent — re-calling
    after the sentinel already exists is a no-op.

    Also consumes the ``.first-run.lock`` file. The lockfile already
    disappears on a successful POST /api/auth/password, but operators
    who chose "Skip — leave open" never hit that path. Without this
    cleanup the claim window would stay open indefinitely — the
    sentinel hides the wizard route in the UI, but the anonymous
    pass-through on wizard writer routes would survive. Consuming
    here closes that window the moment the wizard signals completion.
    """
    sentinel = _first_run_sentinel()
    sentinel.parent.mkdir(parents=True, exist_ok=True)

    payload = "first_run_done\n"
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{sentinel.name}.",
            suffix=".tmp",
            dir=sentinel.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, sentinel)
        tmp_path = None
    except OSError as exc:
        raise Hal0Error(
            f"could not write first-run sentinel: {exc}",
            details={"path": str(sentinel), "error": str(exc)},
        ) from exc
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    return {"first_run": False, "sentinel_path": str(sentinel)}


@router.get("/curated-models")
async def curated_models() -> dict[str, Any]:
    """Return the curated chat-model catalogue the FirstRun wizard renders.

    Shape::

        {
            "models": [{...CuratedModel...}, ...],
            "custom_allowed": true
        }

    Filtered to ``recommended_slot == "chat"`` — image models and any
    future non-chat picks live in the same source list but have their own
    placement in the wizard (step 4 capability pickers). Leaving them in
    the chat picker would let an operator install Flux as their "chat
    model", which is meaningless and previously confused users. Sourcing
    capability picks happens through ``/api/capabilities``.
    """
    chat_picks = [
        m for m in CURATED_MODELS if m.recommended_slot in ("chat", "primary") and not m.bundle_only
    ]
    return {
        "models": [m.model_dump(mode="json") for m in chat_picks],
        "custom_allowed": True,
    }


# ── FirstRun v2: orchestrated multi-slot install (design D3) ────────────────


#: Map a manifest ``ModelEntry.slot`` → (capability, slot_name, port). The
#: capability drives both the on-disk store group (design D2) and the
#: device/profile derivation (design D4).
_SLOT_META: dict[str, tuple[str, str, int]] = {
    "chat.primary": ("chat", "chat", 8081),
    "chat.coder": ("coder", "coder", 8082),
    "embed": ("embed", "embed", 8083),
    "stt": ("stt", "stt", 8084),
    "tts": ("tts", "tts", 8085),
    "img": ("img", "img", 8186),
}


def _resolve_tier(name: str) -> str:
    """Map a tier name (any case) to its canonical key, or raise 404.

    Accepts both the canonical name (``hal0-Pro``) and the bare display
    suffix the FirstRun picker sends (``Pro``) — ``firstrun.jsx`` POSTs
    ``tierObj.name`` (e.g. ``"Pro"``), not the ``hal0-`` prefixed slug.
    Matching is case-insensitive on both forms.
    """
    if name in bundle_tiers.BUNDLES:
        return name
    lower = name.lower()
    for canonical in bundle_tiers.BUNDLES:
        clower = canonical.lower()
        if lower in (clower, clower.removeprefix("hal0-")):
            return canonical
    raise CuratedModelNotFound(
        f"unknown tier {name!r}",
        details={"tier": name, "valid": list(bundle_tiers.BUNDLES)},
    )


def _bundle_to_selections(bundle, overrides, npu_opt_in, *, storage_dir):
    slots = []
    for entry in [e for e in (bundle.primary, bundle.coder, *bundle.aux) if e]:
        cap, slot_name, port = _SLOT_META.get(entry.slot, (entry.slot, entry.slot, 8090))
        ov = overrides.get(slot_name) if isinstance(overrides.get(slot_name), dict) else {}
        slots.append(
            SlotSelection(
                capability=cap,
                slot_name=slot_name,
                port=port,
                model_id=ov.get("model_id") or entry.model_name,
                device=ov.get("device"),
                profile=ov.get("profile"),
            )
        )
    return Selections(
        storage_dir=storage_dir,
        slots=slots,
        extensions={},
        npu_opt_in=npu_opt_in,
    )


@router.post("/apply")
async def install_apply(request: Request, background: BackgroundTasks) -> dict[str, Any]:
    """Orchestrated FirstRun install (design D3).

    Body::

        { "tier": "hal0-Default", "storage_dir": "/srv/models",
          "npu_opt_in": false, "overrides": { "<slot>": {model_id, profile, ...} } }

    Thin wrapper: builds a :class:`~hal0.install.orchestrate.Selections` from the
    tier manifest and delegates to :func:`~hal0.install.orchestrate.apply_setup`.
    Best-effort, non-aborting per row (ADR-0010). The UI reattaches per model via
    the existing ``/api/models/{id}/pull/stream`` SSE.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise PickDefaultError(f"body must be valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise PickDefaultError("body must be a JSON object")
    tier = body.get("tier")
    if not isinstance(tier, str) or not tier.strip():
        raise PickDefaultError("body.tier is required (non-empty string)")
    npu_opt_in = bool(body.get("npu_opt_in", False))
    overrides = body.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise PickDefaultError("body.overrides must be an object")

    canonical = _resolve_tier(tier.strip())
    bundle = bundle_tiers.load_bundle(canonical).bundle

    selections = _bundle_to_selections(
        bundle,
        overrides,
        npu_opt_in,
        storage_dir=body.get("storage_dir") or "",
    )
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    result = await apply_setup(
        selections,
        hardware=request.app.state.hardware_probe.probe(),
        slot_manager=request.app.state.slot_manager,
        registry=request.app.state.model_registry,
        jobs=request.app.state.model_pull_jobs,
        hf_token=hf_token,
        write_sentinel=False,  # the dashboard still POSTs /complete explicitly
    )
    for plan in result.pulls:
        background.add_task(run_pull, plan.job, **plan.kwargs)

    return {
        "tier": canonical,
        "model_ids": result.model_ids,
        "slots": [vars(s) for s in result.slots],
        "next": "reattach /api/models/{id}/pull/stream per model_id",
    }


@router.post("/apply-selections")
async def install_apply_selections(request: Request, background: BackgroundTasks) -> dict[str, Any]:
    """Tier-less orchestrated install: accepts a Selections JSON directly and
    provisions exactly the chosen slots (no tier manifest expansion). Used by
    the `hal0 setup` TUI's API-up branch (roster coherence — the running
    service registers the slots itself)."""
    try:
        body = await request.json()
    except Exception as exc:
        raise PickDefaultError(f"body must be valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise PickDefaultError("body must be a JSON object")
    raw_slots = body.get("slots") or []
    if not isinstance(raw_slots, list):
        raise PickDefaultError("body.slots must be a list")
    selections = Selections(
        storage_dir=body.get("storage_dir") or "",
        slots=[
            SlotSelection(
                capability=s["capability"],
                slot_name=s["slot_name"],
                port=int(s["port"]),
                model_id=s["model_id"],
                device=s.get("device"),
                profile=s.get("profile"),
            )
            for s in raw_slots
        ],
        extensions=body.get("extensions") or {},
        npu_opt_in=bool(body.get("npu_opt_in", False)),
    )
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    result = await apply_setup(
        selections,
        hardware=request.app.state.hardware_probe.probe(),
        slot_manager=request.app.state.slot_manager,
        registry=request.app.state.model_registry,
        jobs=request.app.state.model_pull_jobs,
        hf_token=hf_token,
        write_sentinel=False,
    )
    for plan in result.pulls:
        background.add_task(run_pull, plan.job, **plan.kwargs)
    return {"model_ids": result.model_ids, "slots": [vars(s) for s in result.slots]}


# ── FirstRun v2: services step — verify + one-click repair (design D5) ───────

#: Units the repair button is allowed to restart. Kept to a known allowlist so
#: a crafted ``{unit}`` can't restart arbitrary system services.
_REPAIRABLE_UNITS = {
    "hal0-openwebui.service",
    "hal0-api.service",
    "hindsight-api.service",
    "hal0-agent@hermes.service",
    "hal0-slot@img.service",
}
_COMFYUI_SLOT_UNIT = "hal0-slot@img.service"
_SYSTEMCTL = "/usr/bin/systemctl"


def _privileged_systemctl_argv(*args: str) -> list[str]:
    argv = [_SYSTEMCTL, *args]
    if os.geteuid() == 0:
        return argv
    return ["sudo", "-n", *argv]


def _unit_active(unit: str) -> bool:
    """True when ``systemctl is-active <unit>`` reports ``active``."""
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def _container_active() -> bool:
    """True when the ComfyUI container is running under podman or docker."""
    if _unit_active(_COMFYUI_SLOT_UNIT):
        return True
    for runtime in ("podman", "docker"):
        exe = shutil.which(runtime)
        if exe is None:
            continue
        try:
            out = subprocess.run(
                [exe, "inspect", "--format", "{{.State.Running}}", "comfyui"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip().lower() == "true":
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


@router.get("/services")
async def install_services() -> dict[str, Any]:
    """Verify post-install services for the FirstRun services step (design D5).

    Reports Hermes + OpenWebUI + ComfyUI health so the wizard can show honest
    dots and offer the one-click repair below when a unit is down.
    """
    owui = "hal0-openwebui.service"
    hermes_active = bool(os.environ.get("HAL0_HERMES_PUBLIC_URL")) or _unit_active(
        "hal0-agent@hermes.service"
    )
    services = [
        {
            "unit": owui,
            "label": "OpenWebUI",
            "active": _unit_active(owui),
            "repairable": owui in _REPAIRABLE_UNITS,
        },
        {
            "unit": "hal0-agent@hermes.service",
            "label": "Hermes agent",
            "active": hermes_active,
            "repairable": True,
        },
        # ComfyUI is slot-managed by hal0-slot@img.service; the /opt/comfyui
        # scripts are manual-operator tools only.
        {
            "unit": "comfyui",
            "label": "ComfyUI",
            "active": _container_active(),
            "repairable": True,
        },
    ]
    return {"services": services}


@router.post("/services/{unit}/repair")
async def service_repair(unit: str) -> dict[str, Any]:
    """Restart a known unit (design D5 one-click repair).

    Restricted to :data:`_REPAIRABLE_UNITS` (systemd) or the special-cased
    ``comfyui`` service id so the ``{unit}`` path segment can't be used to
    restart arbitrary system services.

    NOTE: ComfyUI is exposed to the UI as ``comfyui`` but the single lifecycle
    owner is the seeded img slot unit. The legacy ``/opt/comfyui`` scripts stay
    available for manual operator use only.
    """
    # Special case: public service id maps to the slot unit that owns :8188.
    if unit == "comfyui":
        try:
            subprocess.run(
                _privileged_systemctl_argv("restart", _COMFYUI_SLOT_UNIT),
                check=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PickDefaultError(
                f"comfyui restart failed: {exc}", details={"unit": unit}
            ) from exc
        return {"unit": unit, "active": _container_active()}

    if unit not in _REPAIRABLE_UNITS:
        raise BadRequest(
            f"unit {unit!r} is not repairable",
            details={"unit": unit, "allowed": sorted(_REPAIRABLE_UNITS)},
            code="install.unit_not_repairable",
        )
    try:
        subprocess.run(["systemctl", "restart", unit], check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PickDefaultError(f"restart failed: {exc}", details={"unit": unit}) from exc
    return {"unit": unit, "active": _unit_active(unit)}
