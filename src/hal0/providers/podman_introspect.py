"""podman_introspect — read-only podman introspection through the ROOTFUL
context slots actually use (O12, halo143/halo150 deploy finding).

Slots run under ROOTFUL podman: Quadlet units under ``/etc/containers/systemd/``
(see :mod:`hal0.providers.container` + the ``hal0-systemctl`` write-quadlet
seam), populating root's podman image/container store. Post-P3-perms,
``hal0-api`` itself runs as the unprivileged ``hal0`` service user, so a bare
``podman images``/``podman ps`` call issued FROM hal0-api hits hal0's own
ROOTLESS store — a completely different store from the one slots populate.
Confirmed on deployed boxes: backends report ``"installable"`` even though
the image is present (in root's store), and any probe keyed off hal0's
rootless ``HOME`` is fragile. (9e07c0d3 tried to paper over the symptom by
chown'ing hal0's rootless ``HOME`` dirs so rootless podman would at least run
without erroring; O12 fixes the actual cause by asking root's store directly
instead — see the two ``PermRow`` removals in :mod:`hal0.install.perms`.)

This module is the ONE place call sites route rootful podman reads through,
so nobody hand-rolls the ``sudo -n`` argv::

    from hal0.providers import podman_introspect

    result = podman_introspect.images()
    if result is not None:
        repos, context = result.repos, result.context

Privileged seam: ``sudo -n /usr/lib/hal0/bin/hal0-podman-ro <verb>`` — a
tiny, argument-free wrapper (``installer/wrappers/hal0-podman-ro``) that
hardcodes each read-only podman invocation; see
``packaging/sudoers/hal0-podman-ro`` for the grant. Mirrors the gating
:class:`hal0.system.seam.SystemCtlSeam` already uses: the seam is only
ATTEMPTED when this process is literally the ``hal0`` service account
(:func:`hal0.system.seam.is_hal0_service_user`) — a dev shell, CI runner, or
unit test is almost always non-root too, but none of those have the seam
installed, so a bare "attempt sudo" would make every such process try (and
noisily fail) a grant that doesn't exist there.

Degrades honestly beyond that gate too: if ``sudo -n`` itself is denied (the
sudoers grant not yet installed, mid-upgrade race) the seam attempt fails
cleanly and this falls back to a direct (rootless) ``podman`` call, marking
the result ``context="rootless"`` so callers/UI can tell the read may have
come from a different store than the one slots actually use. Returns
``None`` only when NEITHER context produced usable output (podman absent
entirely, or both invocations failed) — the pre-existing graceful-degrade
contract (§21.4 HARD REQUIREMENT #5) callers already rely on.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from hal0.system.seam import is_hal0_service_user

#: Installed path of the read-only podman introspection seam (O12). See
#: installer/wrappers/hal0-podman-ro + packaging/sudoers/hal0-podman-ro.
SEAM_BIN = "/usr/lib/hal0/bin/hal0-podman-ro"

#: Which store a result actually came from: "rootful" is the store slots
#: populate (via the sudo -n seam); "rootless" is hal0-api's own store,
#: reached only as a fallback when the seam isn't usable.
PodmanContext = Literal["rootful", "rootless"]

_RunFn = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class PodmanImagesResult:
    """``podman images`` output + which store it actually came from."""

    repos: set[str]
    context: PodmanContext


def _seam_argv(*verb: str) -> list[str]:
    return ["sudo", "-n", SEAM_BIN, *verb]


def _run(
    run: _RunFn, argv: list[str], *, timeout: float
) -> subprocess.CompletedProcess[str] | None:
    try:
        return run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _parse_repos(stdout: str) -> set[str]:
    return {line.strip() for line in stdout.splitlines() if line.strip() and line != "<none>"}


def images(
    *,
    run: _RunFn = subprocess.run,
    which: Callable[[str], str | None] | None = None,
    is_hal0_user: Callable[[], bool] = is_hal0_service_user,
    timeout: float = 10.0,
) -> PodmanImagesResult | None:
    """The local ``registry/repo`` set, from root's store when reachable.

    Only ATTEMPTS the ``sudo -n hal0-podman-ro images`` seam when this
    process is the ``hal0`` service account (mirrors
    :class:`hal0.system.seam.SystemCtlSeam`'s gate — never touches ``sudo``
    on a dev/CI/test box that has no grant installed). When the seam is
    attempted but denied or otherwise fails, falls back to a direct
    (rootless) ``podman images`` call and marks the result
    ``context="rootless"``. Returns ``None`` when neither produced a
    successful read (podman entirely absent, or both invocations failed) —
    every backend then degrades to ``"unavailable"``, same as before O12.
    """
    if is_hal0_user():
        proc = _run(run, _seam_argv("images"), timeout=timeout)
        if proc is not None and proc.returncode == 0:
            return PodmanImagesResult(repos=_parse_repos(proc.stdout), context="rootful")
        # sudo -n denied / seam missing / any failure — degrade honestly below.

    which_fn = which or shutil.which
    podman = which_fn("podman")
    if podman is None:
        return None
    proc = _run(run, [podman, "images", "--format", "{{.Repository}}"], timeout=timeout)
    if proc is None or proc.returncode != 0:
        return None
    return PodmanImagesResult(repos=_parse_repos(proc.stdout), context="rootless")


__all__ = ["SEAM_BIN", "PodmanContext", "PodmanImagesResult", "images"]
