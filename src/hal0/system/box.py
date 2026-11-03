"""Detect hal0 running inside its rootless podman box.

The box is a systemd container started by podman. podman writes
``/run/.containerenv`` and sets ``/run/systemd/container`` to ``podman``, but so
does every podman container (toolbox, distrobox, CI), so those alone are not
proof. The box image also carries ``/etc/hal0-box-release``
(``installer/box/Containerfile.in``), and both must be present. An LXC guest
also has ``/run/systemd/container`` (content ``lxc``): only a ``podman`` value
counts.
"""

from __future__ import annotations

from pathlib import Path

#: Baked into the box image by ``installer/box/Containerfile.in``.
BOX_RELEASE_FILE = "etc/hal0-box-release"


def _in_podman(root: Path) -> bool:
    try:
        if (root / "run/.containerenv").exists():
            return True
        text = (root / "run/systemd/container").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "podman" in text.strip().lower()


def in_box(root: Path = Path("/")) -> bool:
    """True when ``root`` is the inside of the hal0 box: a podman container with the box marker."""
    try:
        marked = (root / BOX_RELEASE_FILE).is_file()
    except OSError:
        return False
    return marked and _in_podman(root)


__all__ = ["BOX_RELEASE_FILE", "in_box"]
