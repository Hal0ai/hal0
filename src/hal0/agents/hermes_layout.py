"""Fixed paths of the Hermes release layout (ADR-0032 decision 4).

Import-light on purpose: the updater, the driver, ``hal0 doctor`` and the CLI
read these without pulling in the provisioning module. Every place that used to
carry its own copy of ``/var/lib/hal0/venvs/hermes`` imports it from here.

::

    /var/lib/hal0/hermes/
      .lock                       flock for every builder operation
      mirror.git/                 bare mirror of the upstream repo
      releases/<sha12>-<stamp>/   src/ (worktree), venv/, release.json
      current  -> releases/<id>
      previous -> releases/<id>
    /var/lib/hal0/venvs/hermes -> /var/lib/hal0/hermes/current/venv

``/var/lib/hal0/venvs/hermes`` is a symlink once a host has migrated, and a
real directory (the legacy 3.12 venv built from the ``git+`` line) until then.
Code that deletes or audits it must handle both, which is what
:func:`remove_hermes_install` is for.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

#: The path the agent unit, the wrappers and the gateway generator use. A fixed
#: symlink through ``current`` after the first migration.
HERMES_VENV_PATH = Path("/var/lib/hal0/venvs/hermes")

#: The hal0-owned tree holding the mirror, the releases and the two links.
HERMES_RELEASE_ROOT = Path("/var/lib/hal0/hermes")

#: Where uv puts managed interpreters (``UV_PYTHON_INSTALL_DIR``).
HERMES_PYTHON_ROOT = Path("/var/lib/hal0/python")

#: The unit upstream's ``hermes gateway install --system`` writes.
GATEWAY_UNIT_PATH = Path("/etc/systemd/system/hermes-gateway.service")


def release_root_for(venv: Path) -> Path:
    """The release tree that belongs to a venv link at ``venv``.

    ``/var/lib/hal0/venvs/hermes`` maps to ``/var/lib/hal0/hermes``. Deriving it
    from the venv path keeps a sandboxed venv (tests, ``HAL0_HOME``) next to its
    own release tree instead of the real one.
    """
    return Path(venv).parent.parent / "hermes"


def venv_link_target(venv: Path) -> Path:
    """What the venv link points at: ``<release root>/current/venv``."""
    return release_root_for(venv) / "current" / "venv"


def remove_hermes_install(venv: Path) -> list[str]:
    """Remove the Hermes venv and its release tree; return what was removed.

    Symlink-aware (ADR-0032 decision 13). A venv link is unlinked, never
    followed by ``rmtree``, and the release tree it belongs to is removed
    whole. A legacy real-directory venv is removed as a directory. A stray
    file at the path is left for the operator. Every step is best-effort and
    idempotent: a missing path is a no-op.
    """
    venv = Path(venv)
    removed: list[str] = []
    if venv.is_symlink():
        with contextlib.suppress(OSError):
            venv.unlink()
            removed.append(str(venv))
    elif venv.is_dir():
        try:
            shutil.rmtree(venv)
            removed.append(str(venv))
        except OSError:
            pass
    root = release_root_for(venv)
    if root.is_dir() and not root.is_symlink():
        try:
            shutil.rmtree(root)
            removed.append(str(root))
        except OSError:
            pass
    return removed
