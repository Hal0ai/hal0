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
real directory (the pre-layout 3.12 venv built from the ``git+`` line) until then.
Code that deletes or audits it must handle both, which is what
:func:`remove_hermes_install` is for.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

#: The path the agent unit, the wrappers and the gateway generator use. A fixed
#: symlink through ``current`` after the first migration.
HERMES_VENV_PATH = Path("/var/lib/hal0/venvs/hermes")

#: The hal0-owned tree holding the mirror, the releases and the two links.
HERMES_RELEASE_ROOT = Path("/var/lib/hal0/hermes")

#: Where the builder's uv puts managed interpreters (``UV_PYTHON_INSTALL_DIR``).
#: Inside the hal0-owned release tree on purpose: the older
#: ``/var/lib/hal0/python`` is root-owned on existing hosts (an earlier
#: root-run uv fetch), and builds run as hal0 (ADR-0034 decision 2 amendment).
HERMES_PYTHON_ROOT = HERMES_RELEASE_ROOT / "python"

#: uv's cache for builds, also inside the hal0-owned tree: an earlier root-run
#: uv could leave root-owned entries under ``/var/lib/hal0/.cache/uv``.
HERMES_UV_CACHE = HERMES_RELEASE_ROOT / ".cache" / "uv"

#: The pre-release-layout interpreter dir. Root-owned on existing hosts and
#: still the home of the interpreter a pre-layout venv (and a persisted
#: ``HAL0_HERMES_PYTHON``) may use. Builds never write it; only root paths
#: remove it (``installer/uninstall.sh``, and the root prelude once nothing
#: names it).
LEGACY_PYTHON_ROOT = Path("/var/lib/hal0/python")

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
    whole. A pre-layout real-directory venv is removed as a directory. A stray
    file at the path is left for the operator. Every step is best-effort and
    idempotent: a missing path is a no-op.
    """
    venv = Path(venv)
    root = release_root_for(venv)
    # The release tree is removed only when this venv path proves it owns it:
    # it is a link into the tree, or it is the canonical venvs/hermes path. A
    # recorded override such as venvs/hermes-canary shares the same parent
    # and must never take the live tree with it.
    owns_root = venv.name == HERMES_VENV_PATH.name
    removed: list[str] = []
    if venv.is_symlink():
        try:
            target = Path(os.readlink(venv))
        except OSError:
            target = Path()
        if not target.is_absolute():
            target = venv.parent / target
        owns_root = owns_root or root in target.parents
        with contextlib.suppress(OSError):
            venv.unlink()
            removed.append(str(venv))
    elif venv.is_dir():
        try:
            shutil.rmtree(venv)
            removed.append(str(venv))
        except OSError:
            pass
    if owns_root and root.is_dir() and not root.is_symlink():
        try:
            shutil.rmtree(root)
            removed.append(str(root))
        except OSError:
            pass
    return removed


def _pyvenv_home(venv: Path) -> str:
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "home":
            return value.strip()
    return ""


def legacy_python_users(
    venv: Path = HERMES_VENV_PATH,
    *,
    legacy_root: Path = LEGACY_PYTHON_ROOT,
    configured: str = "",
) -> list[str]:
    """What still uses an interpreter under ``legacy_root``; empty when nothing does.

    Checked: the venv at ``venv`` when it is still a real (pre-layout) directory,
    every release's ``release.json`` interpreter and its venv's
    ``pyvenv.cfg``, and the configured ``HAL0_HERMES_PYTHON`` value.
    """
    needle = f"{legacy_root}/"
    users: list[str] = []
    venv = Path(venv)
    if venv.is_dir() and not venv.is_symlink() and _pyvenv_home(venv).startswith(needle):
        users.append(str(venv))
    if configured.startswith(needle):
        users.append("HAL0_HERMES_PYTHON")
    releases = release_root_for(venv) / "releases"
    if releases.is_dir():
        import json

        for rdir in sorted(releases.iterdir()):
            try:
                data = json.loads((rdir / "release.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            named = (
                [str(data.get(k) or "") for k in ("python", "python_home")]
                if isinstance(data, dict)
                else []
            )
            named.append(_pyvenv_home(rdir / "venv"))
            if any(n.startswith(needle) for n in named):
                users.append(rdir.name)
    return users


def remove_legacy_python_if_unused(
    venv: Path = HERMES_VENV_PATH,
    *,
    legacy_root: Path = LEGACY_PYTHON_ROOT,
    configured: str = "",
) -> bool:
    """Root path only: delete ``/var/lib/hal0/python`` once nothing names it.

    Returns ``True`` when it was removed. A symlink is never followed.
    """
    legacy_root = Path(legacy_root)
    if legacy_root.is_symlink() or not legacy_root.is_dir():
        return False
    if legacy_python_users(venv, legacy_root=legacy_root, configured=configured):
        return False
    try:
        shutil.rmtree(legacy_root)
    except OSError:
        return False
    return True
