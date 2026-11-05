"""Prepare the writable Python environment ComfyUI and its custom nodes run in (#3132).

The img container runs as the hal0 account (#3066), and the image's ``/opt/venv``
is root-owned. A custom node that runs ``[sys.executable, "-m", "pip", "install", ...]``
at startup therefore needs an interpreter whose prefix it can write. That is an
ordinary venv in a mounted directory under the ComfyUI data root, which survives the
container being recreated and leaves the image untouched.

The other ways to point pip somewhere else do not work on this image:

* ``PIP_USER``: ``/opt/venv`` has ``include-system-site-packages = false``, so pip
  refuses ``--user`` and Python does not read a user site.
* ``PIP_TARGET``: ``--target`` ignores what is already installed, so a node that
  needs ``accelerate`` pulls a second torch from PyPI, and the ``PYTHONPATH`` entry
  that makes the target importable puts it ahead of the image's ROCm build.
* ``PIP_PREFIX``: pip first uninstalls an older copy of a package it is upgrading,
  which fails with ``Permission denied`` when that copy is in the image.

In a venv whose prefix is not the image's, pip leaves the image's copies alone
("Not uninstalling ... outside environment"), counts them as installed, and installs
only what is missing. A ``.pth`` file puts the image's site-packages behind the
venv's own. A ``pip.conf`` pins the image's GPU stack: a second torch would persist
across restarts and end GPU renders, so that install is refused and only the node
that asked for it fails.

Deployment: this file ships as a hal0 package asset (``hal0.comfyui.assets``). The
ComfyUI provider copies it into the mounted directory, and the container's start
command runs it under the image's Python before launching ComfyUI. A failure is one
line on stderr, which ``hal0 doctor all`` reads from the journal, and never stops the
start: the shell carries on and ComfyUI runs on the image's Python.

Standard library only. It runs inside the image, not inside hal0.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import os
import site
import subprocess  # nosec B404
import sys
import venv
from pathlib import Path

#: The packages built for the image's GPU runtime, which PyPI's builds cannot replace.
_GPU_STACK = ("torch", "torchvision", "torchaudio")


def _gpu_stack_pins() -> str:
    """One ``name==version`` line per GPU-stack package the running Python has."""
    pins = []
    for name in _GPU_STACK:
        with contextlib.suppress(importlib.metadata.PackageNotFoundError):
            pins.append(f"{name}=={importlib.metadata.version(name)}\n")
    return "".join(pins)


def _write_if_changed(path: Path, text: str) -> None:
    """Replace ``path`` atomically, and only when ``text`` differs.

    A full disk then costs nothing on a start with nothing to change, and a failed
    write leaves the old file whole. A truncated ``.pth`` under a live ``current``
    would leave ComfyUI without the image's packages, torch among them.
    """
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return
    except FileNotFoundError:
        pass
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        staged.write_bytes(data)
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _point_current_at(root: Path, version: str) -> None:
    link = root / "current"
    try:
        if os.readlink(link) == version:
            return
    except OSError:
        pass
    staged = root / f".current.{os.getpid()}"
    staged.unlink(missing_ok=True)
    staged.symlink_to(version)
    os.replace(staged, link)


def prepare(root: Path) -> None:
    """Create or refresh the environment for the running Python under ``root``.

    The directory is named for the Python's minor version, since what a node
    installed is built for it. An image that moves to a new minor gets a new
    environment and nodes install again. The files that describe the image follow
    an image update, and what nodes installed is kept. ``current`` is touched last,
    once the environment has been seen to reach the image's packages.
    """
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    env = root / version
    image_site = site.getsitepackages()[0]
    if not (env / "bin" / "pip").exists():
        # Link the interpreter, never copy it. The image's is a small stub that needs
        # libpython beside it, so a copy outlives the image, stays first on PATH and
        # dies in the loader, where a dangling link falls through to the image's.
        venv.EnvBuilder(with_pip=True, symlinks=True).create(env)
    env_site = env / "lib" / f"python{version}" / "site-packages"
    # addsitedir, not a bare path line: the image's own .pth files must run too.
    _write_if_changed(
        env_site / "hal0_image_packages.pth", f"import site; site.addsitedir({image_site!r})\n"
    )
    constraints = env / "hal0-constraints.txt"
    _write_if_changed(constraints, _gpu_stack_pins())
    _write_if_changed(env / "pip.conf", f"[install]\nconstraint = {constraints}\n")
    subprocess.run(
        [
            env / "bin" / "python",
            "-c",
            "import sys; sys.exit(sys.argv[1] not in sys.path)",
            image_site,
        ],
        check=True,
        timeout=60,
    )
    _point_current_at(root, version)


def main() -> int:
    root = Path(__file__).resolve().parent
    try:
        prepare(root)
    except Exception as exc:  # ComfyUI must start whatever went wrong here.
        print(
            f"comfyui.python_env_failed root={root} err={type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        # Nothing activates, so ComfyUI really does start on the image's Python.
        with contextlib.suppress(OSError):
            (root / "current").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
