"""Run the FastFlowLM CLI inside the pinned ``flm`` runner image (#2993, ADR-0036).

``flm pull`` and the catalogue's ``flm list`` used to run the HOST
``/usr/bin/flm`` from the FastFlowLM ``.deb``. Only ``install.sh`` installs that
``.deb``, so a box that only ever ran ``hal0 update`` kept an old host flm while
the NPU slot served a newer runner image, and the old binary decided which
weights landed in the store the new container served. Both commands now run in
the image the slot serves with, against the store mounted exactly where the
slot mounts it, so only the runner image's FastFlowLM version matters.

Where each command runs:

* ``flm list`` — ``podman exec`` into the running FLM slot container when there
  is one (it answers for the exact process that serves), else a one-shot
  ``podman run --rm`` of the slot's image. Only when neither is possible (no
  seam, no podman, image not pulled) does it fall back to the host binary,
  logged as ``flm.cli_host_fallback``: its ``installed`` flags and tag list
  can then lag the runner, but the catalogue still renders.
* ``flm pull`` — always a one-shot ``podman run --rm``, even while the slot
  serves. Killing a ``podman exec`` client leaves the in-container
  ``flm pull`` running, so an exec'd pull could not be cancelled; a one-shot
  container stops and is removed on SIGTERM. FastFlowLM 1.0.10's ``list`` and
  ``pull`` need no NPU device, so the one-shot coexists with a serving slot
  (verified on Strix Halo, #2993). There is no host fallback: a host pull is
  the bug this module exists to remove, and without the image the slot cannot
  serve the weights anyway. :class:`FLMRunnerUnavailableError` says so.
* ``flm validate`` stays on the host (:func:`hal0.providers.flm.flm_validate`):
  it gates the NPU lane during setup, before any runner image is pulled, and it
  checks host facts (driver, firmware, memlock) that the container shares.

Privilege: slots and their images live in ROOT's podman store, and hal0-api
runs as the unprivileged ``hal0`` user, so the container commands go through
the ``hal0-flmctl`` seam (``installer/wrappers/hal0-flmctl``). The wrapper owns
every podman flag and runs the one-shot containers as the hal0 account's own
uid, gid and groups, so they write only what the account can already write;
this side resolves its operands the way the slot launch does (:meth:`FLMProvider.image_ref`, :meth:`FLMProvider.models_store`) and
mirrors its validators so a bad operand fails before a sudo round-trip. A root
caller (``sudo hal0 …``) runs the same wrapper without sudo.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from hal0.errors import Hal0Error

log = logging.getLogger(__name__)

#: Installed path of the seam. See installer/wrappers/hal0-flmctl and
#: packaging/sudoers/hal0-flmctl.
SEAM_BIN = "/usr/lib/hal0/bin/hal0-flmctl"

#: ``exec-list``: the slot container is not running (fall back to a one-shot).
RC_NOT_RUNNING = 69
#: ``run-*``: the image is not in root's store (nothing is pulled implicitly).
RC_IMAGE_MISSING = 70

#: Mirror of the wrapper's ``FLM_TAG_RE``.
FLM_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}:[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

Mode = Literal["exec", "run", "host"]


class FLMRunnerUnavailableError(Hal0Error):
    """No route to run ``flm`` inside the runner image on this box."""

    code = "model.flm_runner_unavailable"
    status = 503


@dataclass(frozen=True)
class FlmTarget:
    """What the serving FLM slot runs: its image, its store, its container token."""

    image: str
    store: str
    #: Instance token of the FLM anchor slot (``hal0-slot-<token>``), or
    #: ``None`` when no FLM slot is configured.
    token: str | None


@dataclass(frozen=True)
class FlmInvocation:
    """One way to run an ``flm`` subcommand: the argv and its spawn kwargs."""

    mode: Mode
    argv: list[str]
    kwargs: dict[str, Any] = field(default_factory=dict)


def is_valid_flm_tag(tag: str) -> bool:
    """Would ``hal0-flmctl`` accept ``tag``? (Mirror of its ``validate_tag``.)"""
    return bool(tag) and FLM_TAG_RE.match(tag) is not None


def _slot_configs() -> list[dict[str, Any]]:
    """Every slot TOML, flattened. Unreadable files are skipped, never raised."""
    from hal0.config import paths
    from hal0.config.loader import _flatten_slot_toml, _read_toml

    out: list[dict[str, Any]] = []
    try:
        files = sorted(paths.slots_config_dir().glob("*.toml"))
    except OSError:
        return out
    for path in files:
        try:
            raw = _read_toml(path)
            name = raw.get("name") or (raw.get("slot") or {}).get("name") or path.stem
            out.append(_flatten_slot_toml(raw, slot_name=str(name)))
        except Exception:
            continue
    return out


def _anchor_cfg() -> dict[str, Any] | None:
    """The FLM anchor slot's config (the slot whose container serves the NPU)."""
    from hal0.slots.activation import npu_anchor_config

    return npu_anchor_config(_slot_configs())


def resolve_target(anchor: dict[str, Any] | None = None) -> FlmTarget:
    """Resolve the image, store and container token the serving slot uses.

    Same resolution as :meth:`FLMProvider.container_spec`: the image honours the
    slot's ``image_pin``, ``[slots].default_images["flm"]`` and the runner pin
    table (:meth:`FLMProvider.image_ref`), and the store is the slot's mount
    source (:meth:`FLMProvider.models_store`). So a pull lands in the directory
    the slot mounts, and progress polling reads that same directory.
    """
    from hal0.providers.flm import FLMProvider
    from hal0.slots.naming import slot_instance_token

    cfg = anchor if anchor is not None else _anchor_cfg()
    provider = FLMProvider()
    slot_cfg = cfg or {}
    token = slot_instance_token(cfg) if cfg else ""
    return FlmTarget(
        image=provider.image_ref(slot_cfg),
        store=provider.models_store(slot_cfg),
        token=token or None,
    )


def _launcher(
    *,
    is_hal0_user: Callable[[], bool] | None = None,
    euid: Callable[[], int] = os.geteuid,
    seam_present: Callable[[], bool] | None = None,
) -> list[str] | None:
    """argv prefix that reaches ``hal0-flmctl``, or ``None`` when nothing can.

    The service user goes through ``sudo -n``; root runs the wrapper directly.
    Any other user (a dev shell, CI) has no route to root's podman store.
    """
    if is_hal0_user is None:
        from hal0.system.seam import is_hal0_service_user

        is_hal0_user = is_hal0_service_user
    present = seam_present() if seam_present is not None else os.access(SEAM_BIN, os.X_OK)
    if not present:
        return None
    if is_hal0_user():
        return ["sudo", "-n", SEAM_BIN]
    if euid() == 0:
        return [SEAM_BIN]
    return None


def _host_invocation(*args: str) -> FlmInvocation:
    """The host-binary fallback. A missing binary surfaces as ``OSError`` at spawn."""
    from hal0.providers import flm as flm_mod

    return FlmInvocation("host", [flm_mod._HOST_FLM_BIN, *args], flm_mod.flm_host_spawn_kwargs())


def list_invocations(
    target: FlmTarget, *, launcher: list[str] | None, host: bool = True
) -> list[FlmInvocation]:
    """Ordered ways to run ``flm list -j``: exec, then one-shot run, then host."""
    out: list[FlmInvocation] = []
    if launcher is not None:
        if target.token:
            out.append(FlmInvocation("exec", [*launcher, "exec-list", target.token]))
        out.append(FlmInvocation("run", [*launcher, "run-list", target.image, target.store]))
    if host:
        out.append(_host_invocation("list", "-j"))
    return out


def pull_invocation(target: FlmTarget, tag: str, *, launcher: list[str] | None) -> FlmInvocation:
    """The one-shot ``flm pull <tag>`` in the slot's image. Never the host binary."""
    if not is_valid_flm_tag(tag):
        raise FLMRunnerUnavailableError(
            f"{tag!r} is not an FLM model tag (family:variant).",
            details={"tag": tag},
        )
    if launcher is None:
        raise FLMRunnerUnavailableError(
            "FLM models are pulled inside the flm runner image, and this process "
            f"cannot reach it: {SEAM_BIN} is missing or this user has no grant for it. "
            "Run `sudo hal0 doctor` to check the privileged seams.",
            details={"tag": tag, "seam": SEAM_BIN},
        )
    return FlmInvocation("run", [*launcher, "run-pull", target.image, target.store, tag])


def flm_list_output[T](
    *,
    parse: Callable[[str], T | None],
    target: FlmTarget | None = None,
    launcher: list[str] | None | Literal["auto"] = "auto",
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    timeout: float = 30.0,
) -> T | None:
    """Run ``flm list -j`` by the first route that answers; return ``parse(stdout)``.

    A route answers when it exits 0 and ``parse`` returns something other than
    ``None``. Returns ``None`` when no route answered.
    """
    try:
        tgt = target if target is not None else resolve_target()
    except Exception as exc:
        log.warning("flm.cli_target_unresolved", extra={"error": str(exc)})
        tgt = None
    prefix = _launcher() if launcher == "auto" else launcher
    invocations = (
        list_invocations(tgt, launcher=prefix)
        if tgt is not None
        else [_host_invocation("list", "-j")]
    )
    for inv in invocations:
        try:
            proc = (run or subprocess.run)(
                inv.argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                **inv.kwargs,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("flm.cli_list_failed", extra={"mode": inv.mode, "error": str(exc)})
            continue
        if proc.returncode != 0:
            if not (inv.mode == "exec" and proc.returncode == RC_NOT_RUNNING):
                log.debug(
                    "flm.cli_list_failed",
                    extra={"mode": inv.mode, "rc": proc.returncode},
                )
            continue
        parsed = parse(proc.stdout.decode("utf-8", errors="replace"))
        if parsed is None:
            continue
        if inv.mode == "host":
            log.warning(
                "flm.cli_host_fallback",
                extra={
                    "command": "list",
                    "reason": "no route into the flm runner image answered",
                },
            )
        return parsed
    return None


def stop_pull_invocation(tag: str, *, launcher: list[str] | None) -> FlmInvocation | None:
    """``stop-pull <tag>``: remove the tag's pull container, if one exists.

    ``None`` when there is no route to the seam or the tag is not one the seam
    would accept (then no pull container of that name can exist).
    """
    if launcher is None or not is_valid_flm_tag(tag):
        return None
    return FlmInvocation("run", [*launcher, "stop-pull", tag])


def flm_pull_stop_argv(tag: str) -> list[str] | None:
    """argv of :func:`stop_pull_invocation` with this process's launcher."""
    inv = stop_pull_invocation(tag, launcher=_launcher())
    return inv.argv if inv is not None else None


def flm_pull_invocation(tag: str, *, target: FlmTarget | None = None) -> tuple[FlmInvocation, str]:
    """``(invocation, store)`` for pulling ``tag`` into the serving slot's store."""
    tgt = target if target is not None else resolve_target()
    return pull_invocation(tgt, tag, launcher=_launcher()), tgt.store


__all__ = [
    "FLM_TAG_RE",
    "RC_IMAGE_MISSING",
    "RC_NOT_RUNNING",
    "SEAM_BIN",
    "FLMRunnerUnavailableError",
    "FlmInvocation",
    "FlmTarget",
    "flm_list_output",
    "flm_pull_invocation",
    "flm_pull_stop_argv",
    "is_valid_flm_tag",
    "list_invocations",
    "pull_invocation",
    "resolve_target",
    "stop_pull_invocation",
]
