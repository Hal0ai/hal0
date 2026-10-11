"""Bundled agent skills hal0 used to ship and no longer does.

``install.sh`` copies ``installer/agent-skills/*`` to
``/usr/share/hal0/skills`` with ``cp -rf`` and the Hermes provisioner links
each entry into ``/etc/hal0/agent-skills``. Neither step removes a skill that
a later release stopped shipping, so it would stay on the box and keep being
offered to the agent. :data:`RETIRED_BUNDLED_SKILLS` names every such skill
explicitly; :func:`prune_retired_skills` removes them from those two
hal0-owned locations only. The operator's drop-in directory
(``/var/lib/hal0/skills``) and any skill name hal0 never shipped are never
touched.

Called by ``install.sh`` (as root, after copying the shipped set) and by
:func:`hal0.updater.updater.run_post_activation_migrations` (best-effort, as
the hal0 user: it unlinks the mirror links and leaves the root-owned copies
to the next ``install.sh``). The provisioner never re-links a retired name.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

#: Skills a hal0 release shipped and a later one removed. Add a name here in
#: the same change that deletes it from ``installer/agent-skills/``.
RETIRED_BUNDLED_SKILLS: frozenset[str] = frozenset(
    {
        # ROCmFPX quantization toolchain; the quants it produced are no longer
        # loadable by any hal0 runner (ADR-0028).
        "hal0-quantize",
    }
)

SHIP_ROOT = Path("/usr/share/hal0/skills")


def mirror_root() -> Path:
    """The provisioner's skill mirror (``/etc/hal0/agent-skills``, HAL0_HOME-aware)."""
    from hal0.config import paths

    return paths.etc() / "agent-skills"


def prune_retired_skills(
    *,
    ship_root: Path | None = SHIP_ROOT,
    mirror: Path | None = None,
    shipped: Iterable[str] = (),
) -> list[str]:
    """Remove retired bundled skills; return the paths removed.

    A name still in ``shipped`` (the set this build ships) is never removed,
    so re-adding a skill only needs it taken off the retired list. In the
    mirror only symlinks are removed (the provisioner creates nothing else
    there); in the ship root only directories. ``ship_root=None`` leaves the
    copies alone (they are root-owned; callers running as the hal0 user only
    unlink the mirror). A path that cannot be removed is logged and skipped.
    """
    keep = set(shipped)
    links_root = mirror if mirror is not None else mirror_root()
    removed: list[str] = []
    for name in sorted(RETIRED_BUNDLED_SKILLS - keep):
        link = links_root / name
        if link.is_symlink():
            try:
                link.unlink()
                removed.append(str(link))
            except OSError as exc:
                log.warning("skills.retired_unlink_failed path=%s error=%s", link, exc)
        if ship_root is None:
            continue
        copy = ship_root / name
        if copy.is_dir() and not copy.is_symlink():
            try:
                shutil.rmtree(copy)
                removed.append(str(copy))
            except OSError as exc:
                log.warning("skills.retired_remove_failed path=%s error=%s", copy, exc)
    for path in removed:
        log.info("skills.retired_removed path=%s", path)
    return removed


def main(argv: list[str] | None = None) -> int:
    """``python -m hal0.agents.bundled_skills`` — install.sh's prune step."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ship-root", type=Path, default=SHIP_ROOT)
    parser.add_argument("--mirror-root", type=Path, default=None)
    parser.add_argument(
        "--shipped-from",
        type=Path,
        default=None,
        help="directory holding the skills this build ships (installer/agent-skills)",
    )
    args = parser.parse_args(argv)
    shipped: list[str] = []
    if args.shipped_from is not None and args.shipped_from.is_dir():
        shipped = [p.name for p in args.shipped_from.iterdir() if p.is_dir()]
    for path in prune_retired_skills(
        ship_root=args.ship_root, mirror=args.mirror_root, shipped=shipped
    ):
        print(f"removed retired skill {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["RETIRED_BUNDLED_SKILLS", "SHIP_ROOT", "main", "mirror_root", "prune_retired_skills"]
