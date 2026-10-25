"""Install-time OPT-IN starter image models (ADR-0037's catalog, offered at install).

Why this exists
---------------
A fresh box can run image generation the moment a model is on disk and selected, but nothing
downloads one: the catalog models are 4 to 51 GB, so ``install.sh`` and ``hal0 setup`` ask
instead of assuming. This module is the bridge, modelled on
:mod:`hal0.install.agent_model`, and the shape of it is the whole point:

**Offered, never assumed.** The offer is the catalog models the API can render, in catalog
order, with the one that suits this box's memory pool marked ``recommended``
(:func:`hal0.comfyui.picker.recommended_for`). Any of them can be picked instead, or
none. The default answer is none.

**Sizes come from here.** ``--plan`` renders each line off the catalog and the models tree,
so the number an operator consents to is the number that downloads and ``install.sh`` never
hardcodes one. A model already on disk says so instead of quoting a download.

**A headless install never blocks and never pulls.** The prompt sits behind install.sh's
``_interactive`` gate. ``HAL0_IMAGE_MODELS`` is the explicit unattended opt-in.

**Never fatal.** :func:`main` never raises (install.sh runs under ``set -euo pipefail``). A
failed model is reported and the install carries on.

The first model that lands and that the API can render becomes the ``img`` capability's
selection, once (:func:`hal0.comfyui.autoselect.auto_select_first_image_model`), which
starts the image engine; Open WebUI's environment is re-rendered to match (a running
Open WebUI restarts, a stopped or skipped one stays down).
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hal0.capabilities import catalog as capability_catalog
from hal0.comfyui import install as comfyui_install
from hal0.comfyui import paths as comfyui_paths
from hal0.comfyui.autoselect import (
    AutoSelectStatus,
    auto_select_first_image_model,
    enabled_img_model,
)
from hal0.comfyui.catalog import (
    CATALOG,
    ImageModel,
    get_model,
    install_state,
    missing_gib,
    weights_gib,
)
from hal0.comfyui.picker import recommended_for
from hal0.config.schema import HardwareInfo
from hal0.upstreams.huggingface import hf_token_from_env

if TYPE_CHECKING:
    from hal0.capabilities.orchestrator import CapabilityOrchestrator

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageOffer:
    """One model on offer. ``missing_gb`` is what is still to download (0 once it is all on disk).

    Both sizes are GiB, as in the image-model API rows, so the menu and
    ``hal0 comfyui models list`` quote one figure for one download.
    """

    id: str
    label: str
    approx_gb: float
    missing_gb: float
    est_seconds: int
    recommended: bool
    license: str


def image_offers(hw: HardwareInfo) -> list[ImageOffer]:
    """The models to offer on this box: the API-renderable catalog, in catalog order.

    Empty when the host has no ComfyUI lane
    (:func:`~hal0.capabilities.catalog.comfyui_backends`): nothing installed here could
    be served, so nothing is offered. Exactly one offer is ``recommended`` otherwise.
    """
    if not capability_catalog.comfyui_backends():
        return []
    recommended = recommended_for(hw)
    root = comfyui_paths.models_root()
    return [
        ImageOffer(
            id=model.id,
            label=model.label,
            approx_gb=weights_gib(model),
            missing_gb=missing_gib(install_state(model, root)),
            est_seconds=model.est_seconds,
            recommended=model.id == recommended,
            license=model.license,
        )
        for model in CATALOG.values()
        if model.api_renderable
    ]


def no_offer_reason(hw: HardwareInfo) -> str | None:
    """Why nothing is offered on this box, or ``None`` when something is."""
    return None if image_offers(hw) else capability_catalog.COMFYUI_NO_ENGINE_REASON


def offer_text(offer: ImageOffer) -> str:
    """The one-line description of ``offer`` that ``--plan`` prints and the menu shows."""
    done = " (already downloaded)" if offer.missing_gb == 0 else ""
    return f"{offer.label} — {offer.missing_gb:.1f} GiB download, ~{offer.est_seconds}s per image{done}"


_SEPARATORS = re.compile(r"[,\s]+")
_NUMBER = re.compile(r"[0-9]+")


def resolve_selection(answer: str, offers: list[ImageOffer]) -> list[str]:
    """Turn an operator's ``answer`` into catalog ids.

    ``""`` and ``none`` choose nothing; ``recommended`` chooses the recommended model;
    otherwise the answer is comma or space separated 1-based numbers into ``offers`` or
    catalog ids, de-duplicated and kept in the order given. Anything else raises
    :class:`ValueError` naming what could not be resolved and listing what is valid.
    """
    text = answer.strip()
    if text.lower() in ("", "none"):
        return []
    if text.lower() == "recommended":
        picked = [o.id for o in offers if o.recommended]
        if not picked:
            raise ValueError(f"no model is recommended here. {_valid_inputs(offers)}")
        return picked
    ids = [o.id for o in offers]
    chosen: list[str] = []
    for token in _SEPARATORS.split(text):
        if not token:
            continue
        if _NUMBER.fullmatch(token):
            number = int(token)
            if not 1 <= number <= len(ids):
                raise ValueError(f"{token!r} is not a listed number. {_valid_inputs(offers)}")
            model_id = ids[number - 1]
        elif token.lower() in ids:
            model_id = token.lower()
        else:
            raise ValueError(f"{token!r} is not a listed model. {_valid_inputs(offers)}")
        if model_id not in chosen:
            chosen.append(model_id)
    return chosen


def _valid_inputs(offers: list[ImageOffer]) -> str:
    if not offers:
        return "This host offers no image models: press Enter or answer 'none'."
    return (
        "Answer 'none' (or press Enter) to skip, 'recommended', "
        f"numbers 1-{len(offers)}, or ids ({', '.join(o.id for o in offers)}), "
        "separated by commas or spaces."
    )


async def provision_image_models(
    model_ids: Sequence[str],
    *,
    orchestrator: CapabilityOrchestrator | None,
    hf_token: str | None,
    on_line: Callable[[str], None],
) -> list[tuple[str, str]]:
    """Install ``model_ids`` one after another and make the first usable one the image model.

    Returns ``(model_id, final_state)`` for each id and never raises for one model's
    failure: an unknown id, a full disk or a failed download comes back ``failed`` and the
    rest still run. Progress goes through ``on_line`` (percentage and GB, at most one line
    every 5 s per model). Asking for a model clears any earlier removal of it.

    Afterwards the first ``completed`` model the API can render is offered to
    :func:`~hal0.comfyui.autoselect.auto_select_first_image_model` (once; an existing
    selection is left alone), and Open WebUI's environment is re-rendered so it learns of
    the new image model (a running Open WebUI restarts; a stopped or skipped one stays
    down). The selection is a normal apply, so it writes the img slot's unit and starts the
    slot: that can wait on ComfyUI's runtime download and on its readiness, which is why a
    line says so first. A second line says what came of it; a failure in either follow-up is
    one such line, never a traceback, and changes no result.

    This runs outside the API process, so it bypasses the API's install-job registry and
    its tree lock. ``install.sh`` runs it before the installer starts the API, so on a fresh
    install nothing runs beside it. A guided ``hal0 setup`` run by hand on a box whose API
    is already up does the same: a second-process install, slot load and
    ``capabilities.toml`` write beside the live API, and a dashboard install of the same
    model running at the same time is not deduplicated against it.
    """
    ids = list(model_ids)
    log.info("install.image_models models=%s", ids)
    results = await comfyui_install.install_each(ids, hf_token, on_line)

    completed = (get_model(model_id) for model_id, state in results if state == "completed")
    renderable = [model for model in completed if model is not None and model.api_renderable]
    if renderable:
        model = renderable[0]
        on_line(
            f"making {model.label} the default image model: this starts the image engine, "
            "and its first start downloads the ComfyUI runtime, which can take several minutes"
        )
        on_line(await _select_and_start(model, orchestrator))
        await asyncio.to_thread(_reconcile_open_webui)
    return results


async def _select_and_start(model: ImageModel, orchestrator: CapabilityOrchestrator | None) -> str:
    """Make ``model`` the ``img`` selection, starting the slot, and say in one line what came of it."""
    try:
        outcome = await auto_select_first_image_model(model, orchestrator=orchestrator)
    except Exception as exc:
        log.debug(
            "install.image_models_auto_select_error",
            extra={"model_id": model.id},
            exc_info=True,
        )
        return await _could_not(model, f"{type(exc).__name__}: {exc}", orchestrator)
    match outcome.status:
        case AutoSelectStatus.selected:
            state = f"is {outcome.detail}" if outcome.detail else "has been started"
            return f"{model.label} is the default image model; the image engine {state}"
        case AutoSelectStatus.kept:
            return (
                f"keeping {outcome.detail} as the default image model; "
                f"{model.label} is installed and can be picked in the dashboard"
            )
        case AutoSelectStatus.skipped:
            return (
                f"{model.label} was not made the default image model "
                f"({outcome.detail}); pick it in the dashboard"
            )
        case _:
            return await _could_not(model, outcome.detail, orchestrator)


async def _could_not(
    model: ImageModel, why: str, orchestrator: CapabilityOrchestrator | None
) -> str:
    """The failure line, with the one recovery step that fits how far the apply got.

    A refusal before the selection was saved leaves nothing to load, so the pick is still to
    make; a failure while starting the slot comes after the save, so loading it is enough.
    """
    try:
        saved = orchestrator is not None and await enabled_img_model(orchestrator) == model.id
    except Exception:
        log.debug("install.image_models_selection_unreadable", exc_info=True)
        saved = False
    hint = "load it later with: hal0 slot load img" if saved else "pick it in the dashboard"
    return f"could not make {model.label} the default image model ({why}); {hint}"


def _reconcile_open_webui() -> None:
    """Re-render Open WebUI's image settings; best-effort, a failure is logged and dropped.

    The synchronous reconcile, because this runs in a short-lived process. It is already a
    no-op when the Open WebUI unit is not installed or nothing changed.
    """
    try:
        from hal0.components import openwebui_arm

        result = openwebui_arm.reconcile_openwebui_env()
    except Exception:
        log.warning("install.image_models_owui_reconcile_failed", exc_info=True)
        return
    log.info("install.image_models_owui_reconciled status=%s", result.get("status"))


async def _build_offline_orchestrator() -> CapabilityOrchestrator:
    """A capability orchestrator over the offline slot manager, built as the API builds its own.

    Mirrors ``_boot_capabilities`` in :mod:`hal0.api`: construct, then seed
    ``capabilities.toml`` from the slot configs when it does not exist yet, so the first
    apply here cannot leave a file the API's own seeding would then skip.
    """
    from hal0.capabilities.orchestrator import CapabilityOrchestrator
    from hal0.cli.setup_command import _build_offline_deps

    slot_manager, registry = _build_offline_deps()
    orchestrator = CapabilityOrchestrator(slot_manager=slot_manager, registry=registry)
    try:
        await orchestrator.initialize_if_missing()
    except Exception as exc:
        log.warning("install.image_models_capabilities_init_failed err=%s", exc)
    return orchestrator


def provision_offline(
    model_ids: Sequence[str],
    *,
    hf_token: str | None,
    on_line: Callable[[str], None],
) -> list[tuple[str, str]]:
    """:func:`provision_image_models` for a caller with no running API and no event loop.

    Owns its event loop (``asyncio.run``), so it cannot be called from async code. If the
    orchestrator cannot be built the models still download; they just are not selected, and
    a line says so.
    """

    async def run() -> list[tuple[str, str]]:
        orchestrator: CapabilityOrchestrator | None
        try:
            orchestrator = await _build_offline_orchestrator()
        except Exception as exc:
            log.debug("install.image_models_orchestrator_unavailable", exc_info=True)
            on_line(
                f"could not build the capability orchestrator ({type(exc).__name__}: {exc}); "
                "models will download but not be selected"
            )
            orchestrator = None
        return await provision_image_models(
            model_ids, orchestrator=orchestrator, hf_token=hf_token, on_line=on_line
        )

    return asyncio.run(run())


def _load_hardware() -> HardwareInfo:
    """Same hardware.json-first strategy as the brain and agent pulls."""
    from hal0.install.brain_model import _load_hardware as _brain_load_hardware

    return _brain_load_hardware()


def _plan() -> int:
    try:
        hw = _load_hardware()
        offers = image_offers(hw)
        if not offers:
            print(f"none\t{no_offer_reason(hw)}")
            return 0
        for offer in offers:
            print(f"{offer.id}\t{offer_text(offer)}\t{int(offer.recommended)}")
    except Exception as exc:
        # A plan that cannot be computed is no offer; the operator is never prompted.
        log.warning("install.image_models_plan_failed err=%s", exc)
        print(f"none\tcould not plan image models ({type(exc).__name__}: {exc})")
    return 0


def _resolve(answer: str) -> int:
    try:
        ids = resolve_selection(answer, image_offers(_load_hardware()))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"could not resolve {answer!r} ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    print(" ".join(ids))
    return 0


class _OneLineFormatter(logging.Formatter):
    """The installer's log format: an exception is its type and message, never a traceback."""

    def formatException(self, ei: Any) -> str:
        return f"{ei[0].__name__}: {ei[1]}"


def _say(line: str) -> None:
    print(f"  {line.strip()}", flush=True)


def main(argv: list[str] | None = None) -> int:
    """``python -m hal0.install.image_models [--plan | --resolve ANSWER | ID...]``.

    ``--plan`` prints one ``id<TAB>description<TAB>1|0 recommended`` line per offer, or a
    single ``none<TAB>reason`` line when nothing is offered, and touches nothing.
    ``--resolve ANSWER`` prints the ids ``ANSWER`` stands for, space separated, or exits 2
    with the reason on stderr. Positional ids install them; the exit code is 1 when any
    ended ``failed``.

    NEVER raises: install.sh runs under ``set -euo pipefail``, and a traceback escaping
    here would abort a whole install over an optional download.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    handler = logging.StreamHandler()
    handler.setFormatter(_OneLineFormatter("  %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler])

    if "--plan" in argv:
        return _plan()
    if "--resolve" in argv:
        position = argv.index("--resolve") + 1
        return _resolve(argv[position] if position < len(argv) else "")

    ids = [arg for arg in argv if arg]
    if not ids:
        return 0
    try:
        results = provision_offline(ids, hf_token=hf_token_from_env(), on_line=_say)
    except Exception as exc:
        print(f"  image model install failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    return 1 if any(state == "failed" for _, state in results) else 0


if __name__ == "__main__":  # pragma: no cover — exercised by install.sh
    raise SystemExit(main())
