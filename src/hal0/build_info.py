"""Identity of the source tree and release this process is running from (#1550/H7).

Single owner for "what commit is actually serving right now" — the fact
`/api/status` exposes and `scripts/deploy.sh` polls after a restart to prove
the deploy actually took, rather than trusting `git rev-parse HEAD` run
against the checkout's *files* (which says nothing about whether the running
process picked them up — the exact gap #1550 reported: an editable install's
worker only sees new code after a restart, and the old script printed
success straight from git, never from the served process).
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def build_sha() -> str | None:
    """Short git SHA of the tree this process imports ``hal0`` from, or ``None``.

    Populated only for a git-tracked checkout (an editable ``pip install -e``,
    or the maintainer-only git-tracked FHS layout — see
    :func:`hal0.updater.updater._is_editable_install`) where a ``.git``
    directory sits above this file. A normal FHS install's site-packages copy
    carries no ``.git``, so this stays ``None`` there — ``__version__`` is
    that install's identity instead.

    Cached for the life of the process: the whole point is catching a worker
    that is STILL running the old tree after a deploy's restart, so this must
    report what was true when the process STARTED, never re-read live.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            try:
                result = subprocess.run(  # nosec B603 B607 — fixed argv, no caller input
                    ["git", "-C", str(candidate), "rev-parse", "--short=12", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            return result.stdout.strip() or None
    return None


@lru_cache(maxsize=1)
def serving_release() -> str:
    """The release this process started from, nightly suffix included.

    ``hal0.__version__`` cannot tell a nightly from the stable release of the
    same number: package metadata holds only the base version, so
    ``1.8.1-nightly.<stamp>`` and ``1.8.1`` both report ``1.8.1``. The release
    tree's ``VERSION`` file carries the full string
    (:func:`hal0.updater.updater.installed_version`).

    Cached for the life of the process, like :func:`build_sha`: an update or a
    rollback moves ``current`` to another tree while this process keeps
    running the old one, and the point is to say what is still serving. Call
    it once at start-up so the cache is filled before any swap can happen.
    """
    from hal0.updater.updater import installed_version

    return installed_version()


__all__ = ["build_sha", "serving_release"]
