"""Skill library behind the agent bridge's teach tools (ADR-0043 decision 10).

Hermes loads skills from two directories (``SKILLS_EXTERNAL_DIRS`` in
:mod:`hal0.agents.hermes_provision`): hal0's shipped mirror
(``/etc/hal0/agent-skills``) and the operator drop-in directory
(``/var/lib/hal0/skills``). The bridge reads both and writes only the second.

Both use Hermes's layout: ``<root>/<skill>/SKILL.md`` or
``<root>/<category>/<skill>/SKILL.md``. A directory is a skill if it holds a
``SKILL.md``; one level above it is its category. Hermes rescans on a 30 s
TTL, so a new directory is picked up without a restart.

Trust differs between the two roots. The shipped mirror is a farm of symlinks
into ``/usr/share/hal0/skills`` by design, and the bridge only reads it, so
its links are followed. The drop-in directory is writable by the ``hal0``
group, so nothing in it is followed: reads, writes and deletes go through
:mod:`hal0.security.nofollow` or descriptors opened relative to the parent, and
a symlinked skill is skipped when listing and refused when written.

A write is staged in ``.tmp-<name>-<uuid>`` beside the target and swapped in
with ``rename``, so Hermes never reads a half-written skill; a failure leaves
the previous skill in place and removes the staging directory.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import shutil
import stat
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from hal0.bridge.errors import BridgeError
from hal0.security import nofollow

__all__ = ["SkillLibrary"]

log = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"

#: A skill's directory name, and the ``name`` in its frontmatter.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

MAX_FILES = 16
#: Cap on ``SKILL.md`` and on each supporting file, in UTF-8 bytes.
MAX_FILE_BYTES = 262144
#: A ``data:`` URI longer than this in ``SKILL.md`` is refused: it is an image
#: or blob pasted into a file the model reads on every load.
MAX_DATA_URI_CHARS = 1024
_DATA_URI_RE = re.compile(rf"data:[^\s)\"']{{{MAX_DATA_URI_CHARS + 1},}}")

#: Reads of an existing ``SKILL.md`` stop here; an operator can drop a bigger
#: file than a bridge write may create, but not an unbounded one.
_READ_CAP = 1024 * 1024
_MAX_LISTED_FILES = 256

SkillRow = dict[str, Any]


def _invalid(message: str) -> BridgeError:
    return BridgeError("bridge.skill_invalid", message)


def _check_name(name: str) -> None:
    if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
        raise _invalid(f"skill name {name!r} must match {NAME_RE.pattern}")


# ── frontmatter ─────────────────────────────────────────────────────────────


def _frontmatter_block(text: str) -> str | None:
    """The text between the opening ``---`` line and the next ``---`` line."""
    lines = text.removeprefix("﻿").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return None


def _parse_frontmatter(text: str) -> dict[Any, Any]:
    block = _frontmatter_block(text)
    if block is None:
        raise _invalid("SKILL.md must start with a frontmatter block between --- lines")
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        reason = getattr(exc, "problem", None) or "parse error"
        raise _invalid(f"frontmatter is not valid YAML: {reason}") from exc
    if not isinstance(data, dict):
        raise _invalid("frontmatter must be a YAML mapping")
    return data


def _description_of(text: str) -> str:
    """Frontmatter ``description``, or ``""`` when it cannot be read."""
    try:
        description = _parse_frontmatter(text).get("description")
    except BridgeError:
        return ""
    return description.strip() if isinstance(description, str) else ""


# ── filesystem reads ────────────────────────────────────────────────────────


def _subdirs(path: Path, *, follow: bool) -> list[Path]:
    """Visible child directories of ``path``; symlinked ones only when ``follow``."""
    found: list[Path] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                with contextlib.suppress(OSError):
                    if entry.is_dir(follow_symlinks=follow):
                        found.append(Path(entry.path))
    except OSError:
        return []
    return sorted(found)


def _has_skill_file(directory: Path, *, follow: bool) -> bool:
    try:
        st = os.stat(directory / SKILL_FILE) if follow else os.lstat(directory / SKILL_FILE)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def _scan(root: Path, *, follow: bool) -> Iterator[tuple[str, str | None, Path]]:
    """Yield ``(name, category, directory)`` for every skill under ``root``."""
    for entry in _subdirs(root, follow=follow):
        if _has_skill_file(entry, follow=follow):
            yield entry.name, None, entry
            continue
        for inner in _subdirs(entry, follow=follow):
            if _has_skill_file(inner, follow=follow):
                yield inner.name, entry.name, inner


def _find(root: Path, name: str, *, follow: bool) -> tuple[Path, str | None] | None:
    for found, category, directory in _scan(root, follow=follow):
        if found == name:
            return directory, category
    return None


def _read_text(path: Path, *, follow: bool) -> str:
    """Contents of ``path`` as text, capped at :data:`_READ_CAP`.

    Raises ``OSError`` when it is absent, too big, not a regular file, or (not
    ``follow``) reached through a symlink.
    """
    if follow:
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise OSError(errno.EINVAL, f"{path} is not a regular file")
            if st.st_size > _READ_CAP:
                raise OSError(errno.EFBIG, f"{path} is larger than {_READ_CAP} bytes")
            data = fh.read(_READ_CAP + 1)
    else:
        if os.lstat(path).st_size > _READ_CAP:
            raise OSError(errno.EFBIG, f"{path} is larger than {_READ_CAP} bytes")
        raw = nofollow.read_bytes(path)
        if raw is None:
            raise FileNotFoundError(errno.ENOENT, "no such file", str(path))
        data = raw
    return data.decode("utf-8", errors="replace")


def _mtime(path: Path, *, follow: bool) -> float:
    return (os.stat(path) if follow else os.lstat(path)).st_mtime


def _list_files(skill_dir: Path, *, follow: bool) -> list[str]:
    """Regular files in ``skill_dir`` besides its ``SKILL.md``, as sorted relative paths."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames.sort()
        rel = os.path.relpath(dirpath, skill_dir)
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            if rel == "." and fname == SKILL_FILE:
                continue
            try:
                st = os.stat(full) if follow else os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            out.append(fname if rel == "." else f"{rel}/{fname}")
            if len(out) >= _MAX_LISTED_FILES:
                return sorted(out)
    return sorted(out)


# ── the library ─────────────────────────────────────────────────────────────


class SkillLibrary:
    """List, read, validate, write and delete Hermes skills (ADR-0043).

    ``shipped_root`` is hal0's read-only mirror; ``dropin_root`` is the only
    place the bridge writes. Failures raise :class:`~hal0.bridge.errors.BridgeError`
    with ``bridge.skill_invalid``, ``bridge.skill_not_found`` or
    ``bridge.skill_readonly``.
    """

    def __init__(self, *, shipped_root: Path, dropin_root: Path) -> None:
        self._shipped_root = Path(shipped_root)
        self._dropin_root = Path(dropin_root)

    def _sources(self) -> tuple[tuple[str, Path, bool], ...]:
        """``(source, root, follow symlinks)``, drop-in first."""
        return (("dropin", self._dropin_root, False), ("shipped", self._shipped_root, True))

    def _is_shipped(self, name: str) -> bool:
        root = self._shipped_root
        if os.path.lexists(root / name):
            return True
        return any(os.path.lexists(category / name) for category in _subdirs(root, follow=True))

    def get(self, name: str) -> SkillRow:
        """One skill: the drop-in copy if there is one, else the shipped one.

        ``files`` names the supporting files (relative paths); their content
        is not returned.
        """
        _check_name(name)
        for source, root, follow in self._sources():
            found = _find(root, name, follow=follow)
            if found is None:
                continue
            directory, category = found
            try:
                text = _read_text(directory / SKILL_FILE, follow=follow)
            except OSError as exc:
                log.warning("bridge.skill_unreadable name=%s source=%s error=%s", name, source, exc)
                continue
            return {
                "name": name,
                "source": source,
                "category": category,
                "skill_md": text,
                "files": _list_files(directory, follow=follow),
            }
        raise BridgeError("bridge.skill_not_found", f"no skill named {name!r}")

    def validate(self, name: str, skill_md: str, files: dict[str, str] | None = None) -> None:
        """Raise ``bridge.skill_invalid`` with the reason when a put would be refused."""
        _check_name(name)
        if not isinstance(skill_md, str):
            raise _invalid("SKILL.md must be text")
        files = files or {}
        if len(files) > MAX_FILES:
            raise _invalid(f"at most {MAX_FILES} files are allowed, got {len(files)}")
        _check_size(SKILL_FILE, skill_md)
        for key, content in files.items():
            if (
                not isinstance(key, str)
                or not key
                or key.startswith(".")
                or ".." in key
                or any(c in key for c in "/\\\0")
            ):
                raise _invalid(
                    f"file name {key!r} is not allowed: no path separators, '..', "
                    "leading '.' or empty names"
                )
            if key == SKILL_FILE:
                raise _invalid(f"file name {key!r} collides with the skill file {SKILL_FILE}")
            if not isinstance(content, str):
                raise _invalid(f"file {key!r} content must be text")
            _check_size(key, content)
        frontmatter = _parse_frontmatter(skill_md)
        if "name" not in frontmatter:
            raise _invalid("frontmatter is missing name")
        if frontmatter["name"] != name:
            raise _invalid(
                f"frontmatter name {frontmatter['name']!r} does not match skill name {name!r}"
            )
        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip():
            raise _invalid("frontmatter description is required and must not be blank")
        if _DATA_URI_RE.search(skill_md):
            raise _invalid(
                f"SKILL.md contains a data: URI longer than {MAX_DATA_URI_CHARS} characters"
            )

    def put(self, name: str, skill_md: str, files: dict[str, str] | None = None) -> SkillRow:
        """Create or replace a drop-in skill; return it as :meth:`get` does.

        The files are written to a staging directory and renamed into place,
        so a failure leaves the previous skill (or none) and no debris. A name
        that already lives in the drop-in directory under a category is
        replaced there.
        """
        self.validate(name, skill_md, files)
        if self._is_shipped(name):
            raise BridgeError(
                "bridge.skill_readonly", f"{name!r} is a shipped skill and cannot be changed"
            )
        target = self._put_target(name)
        parent = target.parent
        try:
            dfd = nofollow.open_dir(parent, create=True)
        except nofollow.UnsafePathError as exc:
            raise _invalid(f"refusing to write: {exc.reason}") from exc
        stage = f".tmp-{name}-{uuid.uuid4().hex}"
        try:
            self._stage(parent / stage, skill_md, files or {})
            self._swap(dfd, stage, name)
        except nofollow.UnsafePathError as exc:
            shutil.rmtree(stage, ignore_errors=True, dir_fd=dfd)
            raise _invalid(f"refusing to write: {exc.reason}") from exc
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True, dir_fd=dfd)
            raise
        finally:
            os.close(dfd)
        return self.get(name)

    def delete(self, name: str) -> None:
        """Remove a drop-in skill's directory; a shipped skill is read-only."""
        _check_name(name)
        found = _find(self._dropin_root, name, follow=False)
        if found is None:
            if self._is_shipped(name):
                raise BridgeError(
                    "bridge.skill_readonly", f"{name!r} is a shipped skill and cannot be deleted"
                )
            if _is_symlink(self._dropin_root / name):
                raise _invalid("target is a symlink")
            raise BridgeError("bridge.skill_not_found", f"no drop-in skill named {name!r}")
        directory, _category = found
        try:
            dfd = nofollow.open_dir(directory.parent)
        except nofollow.UnsafePathError as exc:
            raise _invalid(f"refusing to delete: {exc.reason}") from exc
        try:
            shutil.rmtree(directory.name, dir_fd=dfd)
        finally:
            os.close(dfd)

    def list(self) -> list[SkillRow]:
        """Every skill in both roots, sorted by name then source.

        A name present in both roots is listed twice; ``source`` tells them
        apart. ``description`` is ``""`` when the frontmatter cannot be read.
        """
        rows: list[SkillRow] = []
        for source, root, follow in self._sources():
            for name, category, directory in _scan(root, follow=follow):
                skill_file = directory / SKILL_FILE
                try:
                    text = _read_text(skill_file, follow=follow)
                    mtime = _mtime(skill_file, follow=follow)
                except OSError as exc:
                    log.warning(
                        "bridge.skill_unreadable name=%s source=%s error=%s", name, source, exc
                    )
                    continue
                rows.append(
                    {
                        "name": name,
                        "source": source,
                        "category": category,
                        "description": _description_of(text),
                        "mtime": mtime,
                        "files": _list_files(directory, follow=follow),
                    }
                )
        rows.sort(key=lambda row: (row["name"], row["source"]))
        return rows

    # ── write helpers ───────────────────────────────────────────────────────

    def _put_target(self, name: str) -> Path:
        """Directory a put writes: the existing drop-in skill, else ``dropin_root/<name>``."""
        flat = self._dropin_root / name
        try:
            st = os.lstat(flat)
        except FileNotFoundError:
            existing = _find(self._dropin_root, name, follow=False)
            return existing[0] if existing else flat
        if stat.S_ISLNK(st.st_mode):
            raise _invalid("target is a symlink")
        if not stat.S_ISDIR(st.st_mode) or not _has_skill_file(flat, follow=False):
            raise _invalid(f"{flat.name!r} exists in the drop-in directory and is not a skill")
        return flat

    @staticmethod
    def _stage(stage: Path, skill_md: str, files: dict[str, str]) -> None:
        nofollow.ensure_dir(stage, mode=0o755, create=True)
        nofollow.write_file(stage / SKILL_FILE, skill_md, mode=0o644, create_dirs=False)
        for fname, content in files.items():
            nofollow.write_file(stage / fname, content, mode=0o644, create_dirs=False)

    @staticmethod
    def _swap(dfd: int, stage: str, name: str) -> None:
        """Rename ``stage`` over ``name`` in the directory ``dfd``.

        An existing skill is renamed aside first (a directory cannot be renamed
        over a non-empty one), restored if the second rename fails, and removed
        once the new one is in place.
        """
        try:
            os.lstat(name, dir_fd=dfd)
        except FileNotFoundError:
            os.replace(stage, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            return
        old = f".old-{name}-{uuid.uuid4().hex}"
        os.replace(name, old, src_dir_fd=dfd, dst_dir_fd=dfd)
        try:
            os.replace(stage, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.replace(old, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            raise
        try:
            shutil.rmtree(old, dir_fd=dfd)
        except OSError as exc:
            log.warning("bridge.skill_old_copy_left name=%s path=%s error=%s", name, old, exc)


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except OSError:
        return False


def _check_size(label: str, text: str) -> None:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise _invalid(f"{label} is not valid UTF-8 text") from exc
    if size > MAX_FILE_BYTES:
        raise _invalid(f"{label} is {size} bytes; the limit is {MAX_FILE_BYTES}")
