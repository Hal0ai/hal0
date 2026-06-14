"""hal0 chat-template library.

Bundled templates live under :data:`bundled_templates_dir` (the ``chat/``
sub-package shipped with the package wheel).  At startup the API calls
:func:`seed_chat_templates` to copy absent templates into the operator's
model-store directory (``<model_store_root>/chat-templates/``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from hal0.config.paths import model_store_root

__all__ = ["bundled_templates_dir", "seed_chat_templates"]


def bundled_templates_dir() -> Path:
    """Return the directory containing the package-bundled ``.jinja`` templates."""
    return Path(__file__).parent / "chat"


def seed_chat_templates() -> None:
    """Copy bundled chat templates into the operator model-store (absent-only).

    Skips silently if the store directory is read-only or otherwise
    inaccessible — a missing seed is non-fatal; the catalog will just
    omit the bundled entries until the store is writable.
    """
    try:
        dst = Path(model_store_root()) / "chat-templates"
        dst.mkdir(parents=True, exist_ok=True)
        for src in bundled_templates_dir().glob("*.jinja"):
            target = dst / src.name
            if not target.exists():
                target.write_text(src.read_text())
    except OSError as e:  # read-only store, etc. — non-fatal
        logging.getLogger(__name__).warning("chat-template seed skipped: %s", e)
