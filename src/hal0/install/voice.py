"""Install-time provisioning of the voice job: the ``tts`` slot and its model.

A fresh install creates only the ``agent``, ``brain`` and ``memory`` slots
(:data:`hal0.install.static_seeds.FRESH_INSTALL_SEEDS`). When the person
installing chooses voice, ``install.sh`` calls ``python -I -m
hal0.install.voice``: it makes sure the ``tts`` slot exists, pulls the curated
``kokoro-v1`` bundle (0.354 GB: the ONNX model plus its voices pack) and binds
it to that slot through the same pull-and-activate path the starter model uses
(:func:`hal0.install.agent_model.provision_agent_model`).

Same posture as the starter model: **never fatal**. A failed download returns a
non-zero status and one line on stderr, leaves the slot in place without a
model (model-presence is the activation signal, #1369), and never raises.
An existing binding on the slot is operator config and is never replaced.
Transcription is not part of voice (the NPU serves it).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import sys
from pathlib import Path

from hal0.install.brain_model import already_pulled
from hal0.upstreams.huggingface import hf_token_from_env

log = logging.getLogger(__name__)

#: The curated catalogue row this pulls (``registry/curated.py``).
VOICE_MODEL_ID = "kokoro-v1"

#: The slot it binds to, and the capability the pull lands under.
VOICE_SLOT_NAME = "tts"
VOICE_CAPABILITY = "tts"


def describe_plan() -> str:
    """One tab-separated ``id\\tsentence`` line, the form ``agent_model --plan`` prints.

    The size comes from the curated row, so the number quoted is the number
    downloaded. Pure: no network, no slot or store write.
    """
    from hal0.registry.curated import get_curated

    curated = get_curated(VOICE_MODEL_ID)
    if curated is None:  # pragma: no cover — guarded by the curated catalogue tests
        raise RuntimeError(f"{VOICE_MODEL_ID!r} is not in the curated catalogue")
    if already_pulled(VOICE_MODEL_ID, capability=VOICE_CAPABILITY) is not None:
        return (
            f"{VOICE_MODEL_ID}\t{curated.display_name} — already downloaded; "
            f"binding it to the {VOICE_SLOT_NAME} slot costs nothing"
        )
    return f"{VOICE_MODEL_ID}\t{curated.display_name} — {curated.size_gb:.2f} GB download"


async def _current_binding(slot_manager) -> str | None:
    """``[model].default`` of the tts slot; ``""`` unbound; ``None`` unreadable."""
    getter = getattr(slot_manager, "get_config", None)
    if getter is None:
        return None
    try:
        cfg = await getter(VOICE_SLOT_NAME)
    except Exception as exc:
        log.info("install.voice_binding_unreadable err=%s", exc)
        return None
    model = cfg.get("model") if isinstance(cfg, dict) else None
    if isinstance(model, dict):
        return str(model.get("default") or "")
    return ""


async def provision_voice(
    *, slot_manager, registry, hf_token: str | None = None, slots_dir: Path | None = None
) -> str:
    """Make sure the ``tts`` slot exists, then pull and bind ``kokoro-v1``.

    Returns ``"present"`` (already bound and on disk, nothing done), ``"other"``
    (another model is bound, left alone), ``"unreadable"`` (the slot's binding
    could not be read, so nothing was bound) or ``"bound"`` (landed now and
    read back). Raises on a failed pull or a binding that did not stick; :func:`main` turns that into one stderr line and exit 1.
    """
    from hal0.install.orchestrate import PullPlan, _activate_slot_model, run_pull_and_activate
    from hal0.install.static_seeds import ensure_seed_slot
    from hal0.registry.curated import get_curated
    from hal0.registry.pull import make_job

    # An explicit ask: a tombstone left by an earlier delete is cleared.
    # structlog prints its "seed applied" line to stdout; keep stdout to the
    # one outcome line install.sh shows.
    with contextlib.redirect_stdout(io.StringIO()):
        ensure_seed_slot(VOICE_SLOT_NAME, slots_dir=slots_dir)

    curated = get_curated(VOICE_MODEL_ID)
    if curated is None:  # pragma: no cover — guarded by the curated catalogue tests
        raise RuntimeError(f"{VOICE_MODEL_ID!r} is not in the curated catalogue")

    on_disk = already_pulled(VOICE_MODEL_ID, capability=VOICE_CAPABILITY) is not None
    bound = await _current_binding(slot_manager)
    if bound is None:
        return "unreadable"  # "no binding" is "", not None: never guess
    if bound and bound != VOICE_MODEL_ID:
        return "other"  # operator config wins, never replaced
    if bound == VOICE_MODEL_ID and on_disk:
        return "present"

    if hasattr(registry, "ensure"):
        registry.ensure(VOICE_MODEL_ID)
    if on_disk:
        await _activate_slot_model(slot_manager, VOICE_SLOT_NAME, VOICE_MODEL_ID)
        await _verify_bound(slot_manager)
        return "bound"

    plan = PullPlan(
        model_id=VOICE_MODEL_ID,
        job=make_job(VOICE_MODEL_ID),
        kwargs=dict(
            hf_repo=curated.hf_repo,
            hf_file=curated.hf_file,
            registry=registry,
            hf_token=hf_token or None,
            capability=VOICE_CAPABILITY,
        ),
        slot_names=[VOICE_SLOT_NAME],
    )
    await run_pull_and_activate(plan, slot_manager=slot_manager)
    if getattr(plan.job, "state", None) != "completed":
        raise RuntimeError(
            f"voice model pull ended in state {getattr(plan.job, 'state', '?')!r}: "
            f"{getattr(plan.job, 'error', None)}"
        )
    await _verify_bound(slot_manager)
    return "bound"


async def _verify_bound(slot_manager) -> None:
    """Read the binding back: ``_activate_slot_model`` swallows write errors."""
    now = await _current_binding(slot_manager)
    if now != VOICE_MODEL_ID:
        shown = "unreadable" if now is None else (now or "none")
        raise RuntimeError(
            f"the '{VOICE_SLOT_NAME}' slot binding is {shown!r} after activation, "
            f"not {VOICE_MODEL_ID!r}"
        )


def _one_line(text: str) -> str:
    return " ".join(text.split())


def main(argv: list[str] | None = None) -> int:
    """``python -m hal0.install.voice [--plan] [--slots-dir DIR]`` — install.sh entry point.

    ``--plan`` prints the ``id\\tsentence`` line and changes nothing. Without
    it: create the slot, pull, bind. Exit 0 on success (including "already
    there"), 1 on any failure with one line on stderr. NEVER raises.
    """
    import argparse

    # ERROR, not WARNING: the pull driver warns "slot left model-less" on a failed
    # pull, which would put a second line beside the one reason printed below.
    logging.basicConfig(level=logging.ERROR, format="  %(message)s")
    for noisy in ("hal0.install.orchestrate", __name__):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    try:
        parser = argparse.ArgumentParser(prog="python -m hal0.install.voice")
        parser.add_argument("--plan", action="store_true")
        parser.add_argument("--slots-dir", type=Path, default=None)
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)

        if args.plan:
            print(describe_plan())
            return 0

        from hal0.cli.setup_command import _build_offline_deps

        slot_manager, registry = _build_offline_deps()
        outcome = asyncio.run(
            provision_voice(
                slot_manager=slot_manager,
                registry=registry,
                hf_token=hf_token_from_env(),
                slots_dir=args.slots_dir,
            )
        )
    except SystemExit as exc:  # argparse
        return int(exc.code) if isinstance(exc.code, int) else 2
    except BaseException as exc:
        reason = _one_line(f"{type(exc).__name__}: {exc}")
        print(
            f"  voice model not installed ({reason}); the '{VOICE_SLOT_NAME}' slot is "
            "left without a model, bind one from the dashboard",
            file=sys.stderr,
        )
        return 1
    if outcome == "unreadable":
        print(
            f"  voice model not bound: the '{VOICE_SLOT_NAME}' slot's configuration "
            "could not be read",
            file=sys.stderr,
        )
        return 1
    if outcome == "present":
        print(
            f"  voice model {VOICE_MODEL_ID} already bound to the '{VOICE_SLOT_NAME}' slot — nothing to do"
        )
    elif outcome == "other":
        print(f"  voice already uses another model on the '{VOICE_SLOT_NAME}' slot — left as it is")
    else:
        print(f"  voice model ready: {VOICE_MODEL_ID} bound to the '{VOICE_SLOT_NAME}' slot")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised by install.sh
    raise SystemExit(main())
