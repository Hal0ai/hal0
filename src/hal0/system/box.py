"""Detect hal0 running inside its rootless podman box.

The box is a systemd container started by podman. podman writes
``/run/.containerenv`` and sets ``/run/systemd/container`` to ``podman``. An LXC
guest also has ``/run/systemd/container`` (content ``lxc``), so the file's
existence alone is not proof: only a ``podman`` value counts.
"""

from __future__ import annotations

from pathlib import Path


def in_box(root: Path = Path("/")) -> bool:
    """True when ``root`` looks like the inside of a podman container."""
    try:
        if (root / "run/.containerenv").exists():
            return True
        text = (root / "run/systemd/container").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "podman" in text.strip().lower()


__all__ = ["in_box"]
