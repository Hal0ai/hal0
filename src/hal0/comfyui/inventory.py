"""The weight files on disk under the ComfyUI models tree, and who owns them (ADR-0037).

:func:`list_files` is the one scan of ``models_root()``. Everything that reports on
the tree is a projection of its result: the ``/api/comfyui/files`` listing returns it
as is, and the ``/api/comfyui/status`` ``inventory`` block is :func:`status_counts`
over it. A file belongs to a catalog model when it sits exactly where one of that
model's components lands (:attr:`~hal0.comfyui.catalog.Component.dest`); anything
else, such as an operator's own checkpoint, is listed with no owner. An owner-less
checkpoint whose header sniffs as SDXL or SD 1.5
(:func:`~hal0.comfyui.arch.sniff_checkpoint_arch`) also carries its ``arch`` and the
``api_id`` (``ckpt/<path under checkpoints/ without the .safetensors suffix>``) that
``/v1/images/generations`` accepts as a model.

The scan reads only the model subdirectories ComfyUI itself is pointed at
(:data:`hal0.comfyui.paths.SUBDIRS`), never follows a symlink into a directory, and
skips dotfiles and dot-directories, which is where Hugging Face's ``.cache`` and
leftover ``.hf_stage_*`` staging dirs live.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any

from hal0.comfyui import paths
from hal0.comfyui.arch import sniff_checkpoint_arch
from hal0.comfyui.catalog import CATALOG, ComponentKind, component_path

# Extensions of files worth listing; configs, indexes and partial downloads are not weights.
_WEIGHT_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin", ".sft")

# (kind, subdir) for every directory scanned. A catalog kind reports as its ComponentKind
# value; a directory operators fill by hand (the rest of ``paths.SUBDIRS``) reports as its
# own name.
_CATALOG_SCAN = tuple((kind.value, kind.subdir) for kind in ComponentKind)
_KNOWN_SUBDIRS = {subdir for _, subdir in _CATALOG_SCAN}
_SCAN: tuple[tuple[str, str], ...] = _CATALOG_SCAN + tuple(
    (subdir, subdir) for subdir in paths.SUBDIRS.values() if subdir not in _KNOWN_SUBDIRS
)

# ``/status`` ``inventory`` key -> the subdirectories its count adds up. ``diffusion`` also
# takes the standalone ``unet`` directory and ``text_encoders`` the older ``clip`` one.
_S = paths.SUBDIRS
_COUNT_GROUPS: dict[str, tuple[str, ...]] = {
    "checkpoints": (_S["checkpoints"],),
    "diffusion": (_S["diffusion_models"], _S["unet"]),
    "loras": (_S["loras"],),
    "vae": (_S["vae"],),
    "controlnet": (_S["controlnet"],),
    "upscale": (_S["upscale_models"],),
    "text_encoders": (_S["text_encoders"], _S["clip"]),
}


def _owners(root: Path) -> dict[Path, list[str]]:
    """Catalog model ids by the file each of their components lands at under ``root``."""
    owners: dict[Path, list[str]] = {}
    for model in CATALOG.values():
        for component in model.components:
            ids = owners.setdefault(component_path(component, root), [])
            if model.id not in ids:
                ids.append(model.id)
    return owners


def list_files(*, sniff: bool = True) -> list[dict[str, Any]]:
    """Every weight file under the models tree, sorted by subdirectory then path.

    Each entry is ``{kind, subdir, relpath, size_bytes, models}``: ``relpath`` is
    relative to ``subdir`` and uses ``/``, and ``models`` lists the catalog ids that load
    this file (empty for a file nobody in the catalog owns). A checkpoint nobody owns whose
    family is known also has ``arch`` (``sdxl`` or ``sd15``) and ``api_id``: ``ckpt/`` plus
    its ``relpath`` without the ``.safetensors`` suffix, so ``a.safetensors`` is ``ckpt/a`` and
    ``sub/a.safetensors`` is ``ckpt/sub/a``. Distinct relpaths give distinct ids; the one
    exception, two files that differ only in the case of the suffix, leaves the second
    without an ``api_id`` rather than naming two files alike.

    ``sniff=False`` skips the header reads that find ``arch`` and ``api_id``, for callers
    that only count files (``/status`` polls this). A subdirectory that is
    itself a symlink is skipped, a symlinked directory inside one is not entered, and a
    weight file that is a symlink is listed by its target's size unless the target is gone.
    An absent tree, or one that cannot be read, yields an empty list.
    """
    root = paths.models_root()
    owners = _owners(root)
    found: list[dict[str, Any]] = []
    api_ids: set[str] = set()
    for kind, subdir in _SCAN:
        base = root / subdir
        if os.path.islink(base) or not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith(".") or not name.lower().endswith(_WEIGHT_EXTS):
                    continue
                path = Path(dirpath) / name
                try:
                    size = path.stat().st_size
                except OSError:
                    continue  # a dangling symlink, or a file that vanished mid-scan
                entry: dict[str, Any] = {
                    "kind": kind,
                    "subdir": subdir,
                    "relpath": path.relative_to(base).as_posix(),
                    "size_bytes": size,
                    "models": list(owners.get(path, ())),
                }
                if sniff and kind == ComponentKind.checkpoint.value and not entry["models"]:
                    arch = sniff_checkpoint_arch(path)
                    api_id = f"ckpt/{PurePosixPath(entry['relpath']).with_suffix('')}"
                    if arch is not None and api_id not in api_ids:
                        api_ids.add(api_id)
                        entry["arch"] = arch
                        entry["api_id"] = api_id
                found.append(entry)
    found.sort(key=lambda f: (f["subdir"], f["relpath"]))
    return found


def status_counts(files: list[dict[str, Any]]) -> dict[str, int] | None:
    """The ``/status`` ``inventory`` block: weight files per category in ``files``.

    ``None`` when the models tree does not exist (a dev box or a fresh install), so the
    pane hides the counts instead of showing zeros it cannot vouch for.
    """
    if not os.path.isdir(paths.models_root()):
        return None
    per_subdir: dict[str, int] = {}
    for entry in files:
        per_subdir[entry["subdir"]] = per_subdir.get(entry["subdir"], 0) + 1
    return {
        key: sum(per_subdir.get(subdir, 0) for subdir in subdirs)
        for key, subdirs in _COUNT_GROUPS.items()
    }


__all__ = ["list_files", "status_counts"]
