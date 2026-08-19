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

Privileged seam: ``sudo -n /usr/lib/hal0/bin/hal0-podman-ro <verb> [arg]`` —
a tiny wrapper (``installer/wrappers/hal0-podman-ro``) that hardcodes each
read-only podman subcommand, flag and ``--format`` string; see
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

#1889 — argument-taking verbs
============================

O12 shipped one argument-free verb (``images``), which left the sibling call
sites in :mod:`hal0.providers.container` (``image_present`` /
``running_image`` / ``running_argv``) on a bare rootless ``podman`` call
against the wrong store. Consequence: ``GET /api/slots`` reported
``image_status="missing"`` for every running, healthy slot, ``actual_image``
was always ``null``, and the #663 image-drift detector could never fire.

Fixing that needs a *specific* image ref / container name to reach podman, so
the wrapper now takes one validated positional operand for three read verbs.
The validation is authoritative on the ROOT side (see the wrapper header);
:data:`IMAGE_REF_RE` and :data:`SLOT_TOKEN_RE` below are mirrors of the
wrapper's regexes so the unprivileged side fails fast with a readable error
instead of burning a sudo round-trip — exactly the role
:func:`hal0.system.seam.agent_unit_name` plays for ``hal0-systemctl``. Keep
the two in lock-step; ``tests/installer/test_podman_ro_validation.py``
asserts they agree by running the real wrapper.

The three read helpers (:func:`image_exists`, :func:`container_image`,
:func:`container_argv`) are TRI-STATE and deliberately do NOT fall back to a
rootless call themselves: they return ``None`` for "the rootful seam did not
answer" and let the caller decide, because a rootless answer for a *named*
image or container is not merely stale, it is an answer about a different
object. ``images()`` keeps its original fall-back behaviour (a repo *set* is
still useful, and it self-labels via ``context``).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from hal0.system.seam import is_hal0_service_user

#: Installed path of the read-only podman introspection seam (O12). See
#: installer/wrappers/hal0-podman-ro + packaging/sudoers/hal0-podman-ro.
SEAM_BIN = "/usr/lib/hal0/bin/hal0-podman-ro"

#: Mirror of the wrapper's ``SLOT_TOKEN_RE`` (#1889). Same charset as
#: ``hal0-systemctl``'s ``validate_slot_id`` / :data:`hal0.system.seam._AGENT_ID_RE`:
#: no ``.``, ``/``, ``:``, ``@`` or whitespace, so a validated token can never
#: carry a path traversal, a second argv word or an option-looking value.
SLOT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Mirror of the wrapper's ``IMAGE_REF_RE`` (#1889) —
#: ``[host[:port]/]path[/path...][:tag][@sha256:<64 hex>]``. Structure-strict
#: (no whitespace, no shell metacharacter, no leading ``-``, no ``..``, no
#: second word) and case-permissive; see the wrapper header for why case is
#: not folded here.
# Separator set straight from the distribution-reference grammar:
# ``"." | "_" | "__" | "-"+``. ``__`` is listed FIRST because Python's ``re``
# is leftmost-FIRST (unlike POSIX ERE's leftmost-longest), so ``_|__`` would
# match only the first underscore of ``model__gpu`` and then fail.
_REF_SEP = r"(__|[._]|-+)"
#: A registry host is a dotted/dashed name OR a bracketed IPv6 literal
#: (``[2001:db8::1]:5000/…``), which the reference grammar permits.
_REF_IPV6 = r"\[[0-9A-Fa-f:]{2,45}\]"
_REF_HOST = rf"([A-Za-z0-9]+(([.]|-+)[A-Za-z0-9]+)*|{_REF_IPV6})(:[0-9]{{1,5}})?"
_REF_PATH = rf"[A-Za-z0-9]+({_REF_SEP}[A-Za-z0-9]+)*(/[A-Za-z0-9]+({_REF_SEP}[A-Za-z0-9]+)*)*"
_REF_TAG = r"(:[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
_REF_DIGEST = r"(@sha256:[0-9a-f]{64})?"
IMAGE_REF_RE = re.compile(rf"^({_REF_HOST}/)?{_REF_PATH}{_REF_TAG}{_REF_DIGEST}$")

#: Longest image ref the wrapper will look at before it even reaches the
#: regex engine. Mirrors the wrapper's cap.
IMAGE_REF_MAX_LEN = 512

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


# ── #1889: argument-taking read verbs ──────────────────────────────────────


def is_valid_image_ref(image: str) -> bool:
    """Would the wrapper accept ``image`` as an image reference?

    Unprivileged mirror of the wrapper's ``validate_image_ref``. The ROOT-side
    check is the authoritative one; this exists so a bad ref fails fast and
    readably here instead of costing a sudo round-trip and coming back as an
    opaque rc 64.
    """
    return bool(image) and len(image) <= IMAGE_REF_MAX_LEN and IMAGE_REF_RE.match(image) is not None


def is_valid_slot_token(token: str) -> bool:
    """Would the wrapper accept ``token`` as a slot instance token?

    Unprivileged mirror of the wrapper's ``validate_slot_token``. Note the
    caller passes the bare TOKEN, never a container name: the wrapper builds
    ``hal0-slot-<token>`` itself on the root side, so this seam can only ever
    name a hal0 slot container.
    """
    return bool(token) and SLOT_TOKEN_RE.match(token) is not None


def _seam_read(
    verb: str,
    arg: str,
    *,
    run: _RunFn,
    is_hal0_user: Callable[[], bool],
    timeout: float,
) -> str | None:
    """Run one argument-taking read verb; ``None`` when the seam didn't answer.

    "Didn't answer" covers every non-``rc 0`` outcome: the gate said this
    process is not the ``hal0`` service account, ``sudo -n`` was denied (grant
    not installed / mid-upgrade race), the wrapper rejected the argument
    (rc 64), podman is absent on the box (rc 65), or the call raised. Only
    ``rc 0`` is an answer — and on ``rc 0`` an EMPTY stdout is a real negative
    answer (no such container), not a failure, which is why this returns
    ``""`` rather than ``None`` in that case.
    """
    if not is_hal0_user():
        return None
    proc = _run(run, _seam_argv(verb, arg), timeout=timeout)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip()


def image_exists(
    image: str,
    *,
    run: _RunFn = subprocess.run,
    is_hal0_user: Callable[[], bool] = is_hal0_service_user,
    timeout: float = 10.0,
) -> bool | None:
    """Is ``image`` present in ROOT's image store? ``None`` if unanswerable.

    ``True``/``False`` mean the rootful seam actually answered. ``None`` means
    it did not (not the hal0 service user, no grant, podman absent, or the ref
    was rejected) — the caller must then decide, and deliberately is NOT given
    a silent rootless fallback here: hal0-api's own rootless store is a
    different store, so a "missing" from it about a *named* image is not a
    stale answer but an answer about a different object. That conflation is
    exactly #1889.
    """
    if not is_valid_image_ref(image):
        return None
    answer = _seam_read("image-exists", image, run=run, is_hal0_user=is_hal0_user, timeout=timeout)
    if answer == "present":
        return True
    if answer == "missing":
        return False
    return None


def container_image(
    token: str,
    *,
    run: _RunFn = subprocess.run,
    is_hal0_user: Callable[[], bool] = is_hal0_service_user,
    timeout: float = 10.0,
) -> str | None:
    """The running image ref of ``hal0-slot-<token>`` in ROOT's store (#663).

    Returns the ref when the seam answered and a container is running, and
    ``None`` both when the seam did not answer AND when it answered "no such
    container" — the two are indistinguishable to every caller of this value
    (both mean "no actual_image to report"), so they are collapsed here rather
    than pushing a tri-state nobody branches on into the status hot path.
    """
    if not is_valid_slot_token(token):
        return None
    answer = _seam_read(
        "container-image", token, run=run, is_hal0_user=is_hal0_user, timeout=timeout
    )
    return answer or None


def container_argv(
    token: str,
    *,
    run: _RunFn = subprocess.run,
    is_hal0_user: Callable[[], bool] = is_hal0_service_user,
    timeout: float = 10.0,
) -> str | None:
    """Raw ``{{json .Config.Cmd}}`` for ``hal0-slot-<token>`` from ROOT's store.

    Returns the undecoded JSON text (the caller owns parsing, as it already
    did), or ``None`` when the seam did not answer / no such container.
    """
    if not is_valid_slot_token(token):
        return None
    answer = _seam_read(
        "container-argv", token, run=run, is_hal0_user=is_hal0_user, timeout=timeout
    )
    return answer or None


__all__ = [
    "IMAGE_REF_MAX_LEN",
    "IMAGE_REF_RE",
    "SEAM_BIN",
    "SLOT_TOKEN_RE",
    "PodmanContext",
    "PodmanImagesResult",
    "container_argv",
    "container_image",
    "image_exists",
    "images",
    "is_valid_image_ref",
    "is_valid_slot_token",
]
