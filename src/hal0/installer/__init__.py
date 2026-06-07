"""hal0.installer — Installer utilities.

Backs the install.sh post-install setup step.

New module (no haloai equivalent).
See PLAN.md §15 Phase 4.
"""

from __future__ import annotations

from hal0.installer.template_unit import DEFAULT_DEST, install_template_unit

__all__ = [
    "DEFAULT_DEST",
    "install_template_unit",
]
