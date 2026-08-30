"""podman_mutate — the WRITE-side twin of :mod:`hal0.providers.podman_introspect`
(runner-images v3, D1(a)/D2).

``podman_introspect`` routes reads through the ``hal0-podman-ro`` seam so
hal0-api (running as the unprivileged ``hal0`` service user) can see the
ROOTFUL podman store slots actually launch from. This module is the mirror
for WRITES against that same store, through the separate, narrower
``hal0-podman-rw`` seam (``installer/wrappers/hal0-podman-rw`` +
``packaging/sudoers/hal0-podman-rw``) — two verbs only, independently
revocable from the read grant:

    from hal0.providers import podman_mutate

    async for event in podman_mutate.pull_image_stream_rootful(image):
        ...

    outcome, reason = podman_mutate.remove_image(image)

Same gating idiom as ``podman_introspect``/``hal0.system.seam.SystemCtlSeam``:
the seam is only ever ATTEMPTED when this process is literally the ``hal0``
service account (:func:`hal0.system.seam.is_hal0_service_user`) — a dev
shell, CI runner or unit test is almost always non-root too, but none of
those have the seam installed, so a bare "attempt sudo" would make every such
process try (and noisily fail) a grant that doesn't exist there.

Both entry points validate the image reference on THIS side first
(:func:`hal0.providers.podman_introspect.is_valid_image_ref`, byte-identical
to the wrapper's own validator) so a bad ref fails fast and readably instead
of burning a sudo round-trip for an rc 64 the wrapper would reject anyway —
and, for :func:`remove_image`, so a caller can never even reach ``sudo`` with
an unvalidated operand.

``remove_image``'s exit-code mapping extends ``podman_introspect._RC_REASON``
with rc 67 (see the wrapper's EXIT-CODE CONTRACT): unlike every other
non-zero rc, 67 is a DEFINITIVE answer ("refused: in use by a container, or
has child images"), not an unanswered question, so it maps to the
``"in-use"`` outcome rather than into the ``SeamUnanswered`` reason
vocabulary.

``remove_image`` also has a root fallback: a caller that is not the
``hal0`` service account but IS ``os.geteuid() == 0`` skips the seam
(there is nothing to gain from ``sudo``ing to a user you already are) and
runs ``podman rmi`` directly against root's own store — the same
rootful/rootless fallback philosophy ``podman_introspect.images`` already
uses on the read side.

``pull_image_stream_rootful`` shares its line-progress heuristic with
:meth:`hal0.providers.container.ContainerProvider.pull_image_stream` (the
rootless pull path) via :class:`PullLineParser` below, so both pull paths
report byte-identical event shapes to callers regardless of which store they
targeted. The parser lives here rather than in ``container.py`` because
``container.py`` already imports from this package's ``podman_introspect``
sibling with no cycle back the other way, so ``container.py`` importing this
module too costs nothing new; keeping the parser out of ``container.py``
(a large, heavy module) also means a caller who only wants the pure
line→event logic — this module, or a future rootful test double — never
pulls in podman-run/quadlet/GPU machinery to get it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal

from hal0.providers.podman_introspect import SeamUnanswered, is_valid_image_ref
from hal0.system.seam import is_hal0_service_user

#: Installed path of the write seam (D1(a)/D2). See
#: installer/wrappers/hal0-podman-rw + packaging/sudoers/hal0-podman-rw.
RW_SEAM_BIN = "/usr/lib/hal0/bin/hal0-podman-rw"

#: Wrapper/sudo exit code -> :data:`SeamUnanswered`, for the codes that are
#: genuinely "the question went unanswered" (as opposed to rc 67, a real
#: answer). Byte-identical to ``podman_introspect._RC_REASON`` — kept as its
#: own copy rather than imported so this module's exit-code contract stands
#: on its own the same way the wrapper documents standing alone as its own
#: privileged binary; ``tests/providers/test_podman_mutate.py`` and
#: ``tests/providers/test_podman_introspect.py`` both pin their respective
#: wrapper's documented contract, so a drift between the two wrappers would
#: surface as a test failure here, not a silent divergence.
_RC_REASON: dict[int, SeamUnanswered] = {
    1: "grant-denied",
    64: "invalid-argument",
    65: "podman-absent",
    66: "podman-failed",
}

#: Seconds :func:`pull_image_stream_rootful`'s abnormal-teardown path waits
#: after SIGTERM before escalating to SIGKILL. Module-level (rather than a
#: literal at the call site) so a test can shrink it instead of sitting out
#: the real grace period.
TERMINATE_GRACE_SECONDS = 10.0

#: Outcome of a guarded ``image-rm``. ``"unknown"`` pairs with a
#: :data:`SeamUnanswered` reason (see :func:`remove_image`); the other three
#: are definitive answers from the seam and carry no reason.
RemoveOutcome = Literal["removed", "missing", "in-use", "unknown"]

_RunFn = Callable[..., "subprocess.CompletedProcess[str]"]


class PullLineParser:
    """Turns raw ``podman pull`` progress lines into ``"pulling"`` events.

    Shared by :meth:`hal0.providers.container.ContainerProvider.pull_image_stream`
    (rootless pull) and :func:`pull_image_stream_rootful` (rootful pull, via
    the ``hal0-podman-rw`` seam) so both report identical progress shapes.

    Layer-counting heuristic, unchanged from the pre-extraction logic
    (docker/podman non-TTY pull output):
      - Each ``Pulling fs layer`` / ``Waiting`` / ``Verifying Checksum`` /
        ``Already exists`` line discovers a new layer (``total_layers`` +=1).
      - Each ``Pull complete`` / ``Download complete`` / ``Already exists``
        line finishes a layer (``done_layers`` +=1, capped at
        ``max(total_layers, 1)``).

    Callers are expected to skip blank lines themselves before calling
    :meth:`feed` — a blank line carries no signal and both pull paths already
    filter it out of the raw stream before this ever sees it.
    """

    def __init__(self) -> None:
        self.total_layers = 0
        self.done_layers = 0

    def feed(self, line: str) -> dict[str, Any]:
        """Update layer counters from one non-empty output line, return its event."""
        if any(
            kw in line
            for kw in (
                "Pulling fs layer",
                "Waiting",
                "Verifying Checksum",
                "Already exists",
            )
        ):
            self.total_layers += 1
        if "Pull complete" in line or "Download complete" in line or "Already exists" in line:
            self.done_layers = min(self.done_layers + 1, max(self.total_layers, 1))
        return {
            "state": "pulling",
            "layer": self.done_layers,
            "total_layers": self.total_layers,
            "line": line,
        }


def rw_seam_available(*, is_hal0_user: Callable[[], bool] = is_hal0_service_user) -> bool:
    """Is the write seam usable from here, cheaply?

    Service-account gate (same idiom as every other seam in this package)
    AND the binary actually exists on disk. Deliberately does NOT probe
    ``sudo -n`` itself — that costs a subprocess round-trip and the grant can
    still be denied even when the binary is present (mid-upgrade race,
    sudoers not yet installed); this is a cheap upfront check for callers
    that want to skip offering a rootful action at all, not a guarantee the
    seam will actually answer.
    """
    return is_hal0_user() and Path(RW_SEAM_BIN).is_file()


async def pull_image_stream_rootful(image: str) -> AsyncIterator[dict[str, Any]]:
    """Async generator: ``hal0-podman-rw image-pull <image>``, streamed.

    Mirrors :meth:`hal0.providers.container.ContainerProvider.pull_image_stream`
    exactly (same event shapes, via the shared :class:`PullLineParser`) but
    execs the write seam instead of a bare ``podman pull``, so progress lands
    in ROOT's store — the one slots actually launch from — rather than
    hal0-api's own rootless store.

    Yields::

        {"state": "pulling",   "layer": N, "total_layers": M, "line": "<raw line>"}
        {"state": "completed", "layer": N, "total_layers": M}
        {"state": "failed",    "error": "<message>"}

    A bad image reference is rejected HERE, before any subprocess is
    spawned — no ``sudo`` round-trip is spent on a ref the wrapper would
    reject anyway.

    A bare nonzero exit code is a poor error message on its own — rc 1 is
    what ``sudo -n`` itself returns for a denied/misconfigured grant
    (missing NOPASSWD entry, sudoers not yet installed, mid-upgrade race),
    the single most actionable failure this seam can hit. When the process
    exits 1 AND nothing that looked like real podman progress ever came
    through (either no output line at all, or every line matched sudo's own
    diagnostic shapes — ``sudo:``-prefixed, or containing "a password is
    required") the ``"failed"`` event's ``error`` names the seam and the
    grant file (``/etc/sudoers.d/hal0-podman-rw``) instead of just the exit
    code. Any other nonzero exit — or an rc 1 that followed real pull
    output — keeps the bare ``pull exited with code N`` message: that
    failure happened well past the sudo gate, and blaming the grant would
    be dishonest.
    """
    if not is_valid_image_ref(image):
        yield {"state": "failed", "error": f"invalid image reference: {image}"}
        return

    proc = await asyncio.create_subprocess_exec(
        "sudo",
        "-n",
        RW_SEAM_BIN,
        "image-pull",
        image,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    parser = PullLineParser()
    clean_eof = False
    saw_any_line = False
    sudo_shaped_lines = True

    try:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            saw_any_line = True
            if not (line.startswith("sudo:") or "a password is required" in line):
                sudo_shaped_lines = False
            yield parser.feed(line)
        clean_eof = True
    except Exception as exc:
        yield {"state": "failed", "error": str(exc)}
        return
    finally:
        # Only signal the child on an ABNORMAL exit from the loop above
        # (the consumer stopped iterating early / the generator was
        # cancelled) — never on a clean EOF, where the pull already
        # finished and `proc.wait()` below just reaps the exit code.
        #
        # SIGTERM first, not SIGKILL: `proc` here is `sudo`, not `podman`
        # itself (see the `create_subprocess_exec` call above). sudo relays
        # catchable signals like SIGTERM to the child it launched, so
        # podman gets a chance to unwind (or at least exit) cleanly.
        # SIGKILL cannot be caught or relayed by sudo, so leading with
        # `.kill()` would leave the root-owned `podman pull` orphaned,
        # running to completion (or failure) with nobody watching — and, on
        # the clean-EOF path specifically, killing sudo in the gap between
        # the stdout pipe closing and `proc.wait()` running turns a fully
        # successful pull into a reported `{"state": "failed", "error":
        # "pull exited with code -9"}`.
        #
        # But SIGTERM is a request, not a guarantee: a wedged sudo/podman
        # that never reacts would otherwise linger indefinitely with nobody
        # watching it. So after TERMINATE_GRACE_SECONDS without an exit,
        # escalate to SIGKILL — at that point the orphaned-rootful-child
        # risk above is the lesser evil versus never reclaiming the
        # process at all.
        if not clean_eof:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), TERMINATE_GRACE_SECONDS)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()

    exit_code = await proc.wait()
    if exit_code == 0:
        yield {
            "state": "completed",
            "layer": parser.done_layers,
            "total_layers": parser.total_layers,
        }
    elif exit_code == 1 and (not saw_any_line or sudo_shaped_lines):
        # rc 1 with either no output at all, or output that only ever looked
        # like sudo's own diagnostics (never a real podman progress line) —
        # the write seam's grant is the far likelier culprit than podman
        # itself, so name it instead of leaving an operator to guess at a
        # bare exit code (see packaging/sudoers/hal0-podman-rw).
        yield {
            "state": "failed",
            "error": (
                "write seam grant denied or misconfigured — check "
                f"/etc/sudoers.d/hal0-podman-rw (pull exited with code {exit_code})"
            ),
        }
    else:
        yield {"state": "failed", "error": f"pull exited with code {exit_code}"}


def remove_image(
    image: str,
    *,
    run: _RunFn = subprocess.run,
    is_hal0_user: Callable[[], bool] = is_hal0_service_user,
    which: Callable[[str], str | None] | None = None,
    timeout: float = 60.0,
) -> tuple[RemoveOutcome, str | None]:
    """Guarded ``hal0-podman-rw image-rm <image>`` — never force-removes.

    Returns ``(outcome, reason)``; ``reason`` is set if and only if
    ``outcome == "unknown"`` (same tri-state discipline as
    :class:`hal0.providers.podman_introspect.ImageProbe`):

      * ``("removed", None)``   — rc 0, wrapper printed ``removed``.
      * ``("missing", None)``   — rc 0, wrapper printed ``missing`` (no such
        image — a real negative answer, not a failure).
      * ``("in-use", None)``    — rc 67: podman refused because the image is
        in use by a container, or has child images (podman's rc 2 covers
        both; the wrapper does not distinguish them, so neither do we).
      * ``("unknown", reason)`` — every other case: a bad ref rejected before
        any subprocess ran (``"invalid-argument"``), this process is not the
        ``hal0`` service account and not root (``"not-service-user"``),
        ``sudo -n`` denied (``"grant-denied"``), the wrapper's rc 64/65/66
        (``"invalid-argument"``/``"podman-absent"``/``"podman-failed"``), or
        the call raising / an rc or stdout the contract does not define
        (``"seam-error"``; an undefined rc keeps that rc in the reason,
        e.g. ``"seam-error (image-rm exited rc=99)"``).

    Root fallback: when ``is_hal0_user()`` is False but this process is
    already root (``os.geteuid() == 0`` — an admin at a root prompt, or
    ``sudo -u hal0 hal0 runner-images rm ...`` run as ``sudo -i`` instead),
    the seam is skipped but root's OWN podman IS the rootful store, so no
    ``sudo`` round-trip is needed to reach it — mirrors the read-side
    rootful/rootless fallback philosophy in
    :func:`hal0.providers.podman_introspect.images` (root's own podman
    answers just as authoritatively as the seam would). In that case this
    runs ``podman rmi -- <ref>`` directly, no ``sudo``, no ``-f``:

      * rc 0 -> ``("removed", None)``
      * rc 1 -> ``("missing", None)``
      * rc 2 -> ``("in-use", None)``
      * any other rc -> ``("unknown", "podman-failed (podman rmi exited
        rc=N)")`` — the collapsed bucket keeps the actual rc in the reason
      * a raise -> ``("unknown", "podman-failed")``
      * ``podman`` absent from ``PATH`` -> ``("unknown", "podman-absent")``

    A non-root, non-service-user caller still gets exactly
    ``("unknown", "not-service-user")`` — the fallback is root-only, never a
    way for an arbitrary unprivileged caller to bypass the seam.
    """
    if not is_valid_image_ref(image):
        return ("unknown", "invalid-argument")
    if not is_hal0_user():
        if os.geteuid() != 0:
            return ("unknown", "not-service-user")
        which_fn = which or shutil.which
        podman = which_fn("podman")
        if podman is None:
            return ("unknown", "podman-absent")
        try:
            proc = run(
                [podman, "rmi", "--", image],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return ("unknown", "podman-failed")
        if proc.returncode == 0:
            return ("removed", None)
        if proc.returncode == 1:
            return ("missing", None)
        if proc.returncode == 2:
            return ("in-use", None)
        # Collapsed bucket for every rc outside the documented {0, 1, 2} —
        # keep the actual rc in the reason so it is not lost to the caller.
        return ("unknown", f"podman-failed (podman rmi exited rc={proc.returncode})")
    try:
        proc = run(
            ["sudo", "-n", RW_SEAM_BIN, "image-rm", image],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ("unknown", "seam-error")
    if proc.returncode == 0:
        stdout = proc.stdout.strip()
        if stdout == "removed":
            return ("removed", None)
        if stdout == "missing":
            return ("missing", None)
        # rc 0 with a word the contract does not define: the wrapper and
        # this mirror have drifted.
        return ("unknown", "seam-error")
    if proc.returncode == 67:
        return ("in-use", None)
    reason = _RC_REASON.get(proc.returncode)
    if reason is None:
        # An rc the wrapper's EXIT-CODE CONTRACT does not define — collapsed
        # to "seam-error", but keep the actual rc in the reason so it is not
        # lost to the caller.
        return ("unknown", f"seam-error (image-rm exited rc={proc.returncode})")
    return ("unknown", reason)


__all__ = [
    "RW_SEAM_BIN",
    "TERMINATE_GRACE_SECONDS",
    "PullLineParser",
    "RemoveOutcome",
    "pull_image_stream_rootful",
    "remove_image",
    "rw_seam_available",
]
