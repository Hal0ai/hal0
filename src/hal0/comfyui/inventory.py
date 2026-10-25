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

:func:`delete_file` removes one file the catalog does not own, addressed by ``(subdir,
relpath)`` as the listing reports them. It deletes only what the listing could show: a
regular file with a weight-file extension (``_WEIGHT_EXTS``) inside a model subdirectory,
reached without a symlink, with no hidden part in its path. A ``.yaml``, a ``.json`` or ComfyUI's
``put_checkpoints_here`` placeholder is never listed, so it is never deleted.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from hal0.comfyui import paths
from hal0.comfyui.arch import sniff_checkpoint_arch
from hal0.comfyui.catalog import CATALOG, ComponentKind, component_path
from hal0.errors import Hal0Error

# Extensions of files worth listing; configs, indexes and partial downloads are not weights.
_WEIGHT_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin", ".sft")


def _is_weight_name(name: str) -> bool:
    """True for a file name the listing shows: not hidden, with a weight-file extension.

    The one rule :func:`list_files` scans by and :func:`delete_file` enforces, so what the
    listing can show and what a delete can reach never drift apart.
    """
    return not name.startswith(".") and name.lower().endswith(_WEIGHT_EXTS)


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


class FilePathInvalid(Hal0Error):
    """The ``(subdir, relpath)`` does not name a file inside a model subdirectory."""

    code = "comfyui.file_path_invalid"
    status = 400


class FileIsSymlink(Hal0Error):
    """The path is a symlink; delete its target, or the link by hand."""

    code = "comfyui.file_symlink"
    status = 400


class FileMissing(Hal0Error):
    """There is no regular file at the path."""

    code = "comfyui.file_not_found"
    status = 404


class FileCatalogOwned(Hal0Error):
    """A catalog model loads this file; ``details["models"]`` names them."""

    code = "comfyui.file_catalog_owned"
    status = 409


class FileDeleteDenied(Hal0Error):
    """The hal0 user may not delete the file, or search the directory it is in."""

    code = "comfyui.file_permission"
    status = 403


def owners(root: Path) -> dict[Path, list[str]]:
    """Catalog model ids by the file each of their components lands at under ``root``.

    The one ownership map: the listing's ``models`` and :func:`delete_file`'s refusal both
    read it, so a file the listing shows as owned is a file the delete refuses.
    """
    by_path: dict[Path, list[str]] = {}
    for model in CATALOG.values():
        for component in model.components:
            ids = by_path.setdefault(component_path(component, root), [])
            if model.id not in ids:
                ids.append(model.id)
    return by_path


def ckpt_api_id(relpath: str) -> str:
    """The ``/v1/images/generations`` model id of the checkpoint at ``relpath``.

    ``ckpt/`` plus ``relpath`` without its suffix: ``a.safetensors`` is ``ckpt/a`` and
    ``sub/a.safetensors`` is ``ckpt/sub/a``. The one place the id is built, for the listing
    and for whoever needs to know which id a file carried.
    """
    return f"ckpt/{PurePosixPath(relpath).with_suffix('')}"


def list_files(*, sniff: bool = True) -> list[dict[str, Any]]:
    """Every weight file under the models tree, sorted by subdirectory then path.

    Each entry is ``{kind, subdir, relpath, size_bytes, models, symlink}``: ``relpath`` is
    relative to ``subdir`` and uses ``/``, ``models`` lists the catalog ids that load
    this file (empty for a file nobody in the catalog owns) and ``symlink`` is true when the
    listed path is a link, which :func:`delete_file` refuses. A checkpoint nobody owns whose
    family is known also has ``arch`` (``sdxl`` or ``sd15``) and ``api_id``: ``ckpt/`` plus
    its ``relpath`` without the ``.safetensors`` suffix (:func:`ckpt_api_id`). Distinct
    relpaths give distinct ids; the one exception, two files that differ only in the case
    of the suffix, leaves the second without an ``api_id`` rather than naming two files alike.

    ``sniff=False`` skips the header reads that find ``arch`` and ``api_id``, for callers
    that only count files (``/status`` polls this). A subdirectory that is
    itself a symlink is skipped, a symlinked directory inside one is not entered, and a
    weight file that is a symlink is listed by its target's size unless the target is gone.
    An absent tree, or one that cannot be read, yields an empty list.
    """
    root = paths.models_root()
    owned_by = owners(root)
    found: list[dict[str, Any]] = []
    api_ids: set[str] = set()
    for kind, subdir in _SCAN:
        base = root / subdir
        if os.path.islink(base) or not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if not _is_weight_name(name):
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
                    "models": list(owned_by.get(path, ())),
                    "symlink": path.is_symlink(),
                }
                if sniff and kind == ComponentKind.checkpoint.value and not entry["models"]:
                    arch = sniff_checkpoint_arch(path)
                    api_id = ckpt_api_id(entry["relpath"])
                    if arch is not None and api_id not in api_ids:
                        api_ids.add(api_id)
                        entry["arch"] = arch
                        entry["api_id"] = api_id
                found.append(entry)
    found.sort(key=lambda f: (f["subdir"], f["relpath"]))
    return found


def _refuse_path(subdir: str, relpath: str, why: str) -> FilePathInvalid:
    return FilePathInvalid(
        f"cannot delete {subdir}/{relpath}: {why}", details={"subdir": subdir, "relpath": relpath}
    )


def _denied(subdir: str, relpath: str, exc: PermissionError) -> FileDeleteDenied:
    return FileDeleteDenied(
        f"cannot delete {subdir}/{relpath}: {exc.strerror or exc}; make the directory writable "
        "by the hal0 user or group (e.g. chgrp <hal0 group> + chmod 2775)",
        details={"subdir": subdir, "relpath": relpath},
    )


def delete_file(subdir: str, relpath: str) -> None:
    """Delete the file at ``models_root()/subdir/relpath``, if no catalog model loads it.

    ``subdir`` is a directory name from :data:`~hal0.comfyui.paths.SUBDIRS`; ``relpath`` is
    the path the listing reports, ``/``-separated. Refuses, changing nothing:

    * :class:`FilePathInvalid` (400): ``subdir`` is not a model directory; ``relpath`` is
      empty, absolute, or has an empty, ``.``, ``..`` or hidden part (a part starting with
      ``.``, which is also where an install's ``.hf_stage_*`` partials live); the file name
      has no weight-file extension, so the listing would not show it; the path resolves
      outside ``subdir``; or it goes through a symlinked directory, which the listing never
      enters.
    * :class:`FileIsSymlink` (400): the last component is a link. Checked with ``lstat``
      before anything is resolved, so a link is never judged by where it points.
    * :class:`FileMissing` (404): no regular file is there.
    * :class:`FileCatalogOwned` (409): :func:`owners` names a catalog model for it.
    * :class:`FileDeleteDenied` (403): the OS refused with ``PermissionError``.

    A deleted file's now-empty parent directory stays.
    """
    if subdir not in paths.SUBDIRS.values():
        raise _refuse_path(subdir, relpath, f"{subdir!r} is not a ComfyUI model directory")
    parts = relpath.split("/")
    if "\0" in relpath or any(not part or part.startswith(".") for part in parts):
        raise _refuse_path(
            subdir, relpath, "the path must be relative, with no empty, '.', '..' or hidden part"
        )
    if not _is_weight_name(parts[-1]):
        raise _refuse_path(
            subdir,
            relpath,
            f"only weight files ({', '.join(_WEIGHT_EXTS)}) are listed, so only they can be deleted",
        )
    base = paths.models_root() / subdir
    target = base.joinpath(*parts)
    try:
        mode = os.lstat(target).st_mode
    except (FileNotFoundError, NotADirectoryError):
        raise FileMissing(
            f"no file {subdir}/{relpath}", details={"subdir": subdir, "relpath": relpath}
        ) from None
    except PermissionError as exc:
        raise _denied(subdir, relpath, exc) from exc
    except OSError as exc:  # a symlink loop, a name too long
        raise _refuse_path(subdir, relpath, exc.strerror or str(exc)) from exc
    if stat.S_ISLNK(mode):
        raise FileIsSymlink(
            f"{subdir}/{relpath} is a symlink; delete its target instead",
            details={"subdir": subdir, "relpath": relpath},
        )
    if not stat.S_ISREG(mode):
        raise FileMissing(
            f"{subdir}/{relpath} is not a file", details={"subdir": subdir, "relpath": relpath}
        )
    if not paths.is_under_subdir(subdir, target):
        raise _refuse_path(subdir, relpath, f"it resolves outside {subdir}")
    if target.resolve() != paths.models_root().resolve().joinpath(subdir, *parts):
        raise _refuse_path(subdir, relpath, "it goes through a symlink")
    models = owners(paths.models_root()).get(target)
    if models:
        raise FileCatalogOwned(
            f"{subdir}/{relpath} is loaded by {', '.join(models)}; remove the model instead",
            details={"models": list(models)},
        )
    try:
        target.unlink()
    except FileNotFoundError:
        raise FileMissing(
            f"no file {subdir}/{relpath}", details={"subdir": subdir, "relpath": relpath}
        ) from None
    except PermissionError as exc:
        raise _denied(subdir, relpath, exc) from exc


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


__all__ = [
    "FileCatalogOwned",
    "FileDeleteDenied",
    "FileIsSymlink",
    "FileMissing",
    "FilePathInvalid",
    "ckpt_api_id",
    "delete_file",
    "list_files",
    "owners",
    "status_counts",
]
