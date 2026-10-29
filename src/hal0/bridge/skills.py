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
into ``/usr/share/hal0/skills`` by design, so the *skill directory* link
(``<shipped>/<name>``, or a category directory) is followed, resolved once with
:func:`os.path.realpath`. Nothing below it is: ``SKILL.md`` and every
supporting file are read with ``lstat``/``O_NOFOLLOW`` as in the drop-in root,
so a file-level link inside a shipped skill makes that skill absent instead of
exposing whatever it names. The drop-in directory is writable by the ``hal0``
group, so nothing in it is followed at all: reads, writes and deletes go
through :mod:`hal0.security.nofollow` or descriptors opened relative to the
parent, and a symlinked skill is skipped when listing and refused when written.

Hermes walks the drop-in root with ``os.walk(followlinks=True)`` and prunes only
a fixed set of names (``EXCLUDED_SKILL_DIRS``, Hermes 4d043038
``agent/skill_utils.py:24``; ``iter_skill_index_files``, ``:859``). A staging
directory beside the skills would therefore be read as a second, ambiguous copy
of the skill. A write is staged, and the previous copy parked, under
``<dropin>/.archive/.bridge/`` (``stage-<name>-<uuid>``, ``old-<name>-<uuid>``):
``.archive`` is in that pruned set and is on the same filesystem, so the final
``rename`` is still atomic and Hermes never reads a half-written skill or a
second copy. A failure leaves the previous skill in place and removes the
stage. Every entry in ``.archive/.bridge/`` is debris by construction once the
write lock is held, so each ``put`` and ``delete`` reaps them all first.

``put`` and ``delete`` hold one lock per drop-in root for their whole run
(hal0-api is one process; the CLI never writes skills), so same-name writers
never interleave their renames and the trailing ``get`` of a ``put`` cannot land
in another writer's swap window.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import shutil
import stat
import threading
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
#: Cap on a supporting file's name, in UTF-8 bytes. A name is also written as
#: ``.<name>.<16 hex>`` by :func:`hal0.security.nofollow.write_file`, so a
#: filesystem's 255-byte limit would otherwise surface as a raw ``OSError``.
MAX_FILE_NAME_BYTES = 200
#: A ``data:`` URI longer than this in ``SKILL.md`` is refused (the whole URI,
#: scheme included): it is an image or blob pasted into a file the model reads
#: on every load. The URI ends at whitespace, ``)``, ``"`` or ``'``.
MAX_DATA_URI_CHARS = 1024
_DATA_SCHEME = "data:"
_DATA_URI_RE = re.compile(
    rf"{_DATA_SCHEME}[^\s)\"']{{{MAX_DATA_URI_CHARS - len(_DATA_SCHEME) + 1},}}", re.IGNORECASE
)

#: Where a write is staged and the previous copy parked: below the drop-in root,
#: in a directory Hermes does not walk (see the module docstring).
ARCHIVE_DIR = ".archive"
SCRATCH_DIR = ".bridge"

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


def _resolve_dir(entry: Path, *, follow_dirs: bool) -> Path | None:
    """The directory to read for ``entry``, or ``None`` when it is not usable.

    The one place a link is followed: a shipped skill (or category) directory
    is resolved once, so everything read below it can be refused a link.
    """
    if not follow_dirs:
        return entry
    try:
        resolved = os.path.realpath(entry, strict=True)
    except OSError:
        return None
    return Path(resolved) if os.path.isdir(resolved) else None


def _has_skill_file(directory: Path) -> bool:
    """True when ``directory/SKILL.md`` is a regular file (a link to one is not)."""
    try:
        return stat.S_ISREG(os.lstat(directory / SKILL_FILE).st_mode)
    except OSError:
        return False


def _scan(root: Path, *, follow_dirs: bool) -> Iterator[tuple[str, str | None, Path]]:
    """Yield ``(name, category, directory)`` for every skill under ``root``.

    With ``follow_dirs`` a symlinked skill or category directory is followed
    (the shipped mirror); ``directory`` is then its resolved target. A link
    below the skill directory never is.
    """
    for entry in _subdirs(root, follow=follow_dirs):
        directory = _resolve_dir(entry, follow_dirs=follow_dirs)
        if directory is None:
            continue
        if _has_skill_file(directory):
            yield entry.name, None, directory
            continue
        for inner in _subdirs(directory, follow=follow_dirs):
            inner_dir = _resolve_dir(inner, follow_dirs=follow_dirs)
            if inner_dir is not None and _has_skill_file(inner_dir):
                yield inner.name, entry.name, inner_dir


def _find(root: Path, name: str, *, follow_dirs: bool) -> tuple[Path, str | None] | None:
    for found, category, directory in _scan(root, follow_dirs=follow_dirs):
        if found == name:
            return directory, category
    return None


def _read_text(path: Path) -> str:
    """Contents of ``path`` as text, capped at :data:`_READ_CAP`.

    Never follows a link: raises ``OSError`` when it is absent, too big, not a
    regular file, or a symlink.
    """
    if os.lstat(path).st_size > _READ_CAP:
        raise OSError(errno.EFBIG, f"{path} is larger than {_READ_CAP} bytes")
    raw = nofollow.read_bytes(path)
    if raw is None:
        raise FileNotFoundError(errno.ENOENT, "no such file", str(path))
    return raw.decode("utf-8", errors="replace")


def _list_files(skill_dir: Path) -> list[str]:
    """Regular files in ``skill_dir`` besides its ``SKILL.md``, as sorted relative paths.

    A symlinked file, and a symlinked directory (not descended), are not listed.
    """
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames.sort()
        rel = os.path.relpath(dirpath, skill_dir)
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            if rel == "." and fname == SKILL_FILE:
                continue
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            out.append(fname if rel == "." else f"{rel}/{fname}")
            if len(out) >= _MAX_LISTED_FILES:
                return sorted(out)
    return sorted(out)


# ── the scratch directory ───────────────────────────────────────────────────

_OPEN_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_child_dir(dir_fd: int, name: str, *, create: bool) -> int:
    """Descriptor of the real directory ``name`` in ``dir_fd``; a link is refused.

    ``create`` makes it, mode 0755, when absent. Raises ``FileNotFoundError``
    when it is absent and not ``create``, and ``bridge.skill_invalid`` when it is
    a symlink or not a directory.
    """
    created = False
    try:
        try:
            fd = os.open(name, _OPEN_DIR_FLAGS, dir_fd=dir_fd)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(name, 0o755, dir_fd=dir_fd)
                created = True
            except FileExistsError:
                pass
            fd = os.open(name, _OPEN_DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _invalid(
                f"{name!r} in the drop-in directory is a symlink or not a directory"
            ) from exc
        raise
    if created:
        os.fchmod(fd, 0o755)
    return fd


def _open_scratch(root_fd: int, *, create: bool) -> int:
    """Descriptor of ``.archive/.bridge`` below the drop-in root ``root_fd``.

    Raises ``FileNotFoundError`` when it is absent and not ``create``, and
    ``bridge.skill_invalid`` when ``.archive`` or ``.bridge`` is a symlink.
    """
    archive_fd = _open_child_dir(root_fd, ARCHIVE_DIR, create=create)
    try:
        return _open_child_dir(archive_fd, SCRATCH_DIR, create=create)
    finally:
        os.close(archive_fd)


def _remove(dir_fd: int, name: str) -> None:
    """Remove ``name`` from ``dir_fd`` without following it; log, never raise."""
    try:
        if stat.S_ISDIR(os.lstat(name, dir_fd=dir_fd).st_mode):
            shutil.rmtree(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("bridge.skill_scratch_left name=%s error=%s", name, exc)


def _reap(scratch_fd: int) -> None:
    """Remove every entry of the scratch directory.

    Called with the write lock held, so none is a live stage: each is left over
    from a crash, a failed removal or a lost race.
    """
    for entry in os.listdir(scratch_fd):
        _remove(scratch_fd, entry)


def _swap(scratch_fd: int, stage: str, parent_fd: int, name: str) -> None:
    """Rename ``stage`` (in the scratch directory) over ``name`` in ``parent_fd``.

    An existing skill is parked in the scratch directory first (a directory
    cannot be renamed over a non-empty one), put back if the second rename
    fails, and removed once the new one is in place.
    """
    try:
        os.lstat(name, dir_fd=parent_fd)
    except FileNotFoundError:
        os.replace(stage, name, src_dir_fd=scratch_fd, dst_dir_fd=parent_fd)
        return
    old = f"old-{name}-{uuid.uuid4().hex}"
    os.replace(name, old, src_dir_fd=parent_fd, dst_dir_fd=scratch_fd)
    try:
        os.replace(stage, name, src_dir_fd=scratch_fd, dst_dir_fd=parent_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.replace(old, name, src_dir_fd=scratch_fd, dst_dir_fd=parent_fd)
        raise
    _remove(scratch_fd, old)


# ── the library ─────────────────────────────────────────────────────────────

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _write_lock(dropin_root: Path) -> threading.Lock:
    """The one lock for writers of ``dropin_root``, shared by every library on it."""
    key = os.path.abspath(dropin_root)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


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
        self._lock = _write_lock(self._dropin_root)

    def _sources(self) -> tuple[tuple[str, Path, bool], ...]:
        """``(source, root, follow skill-directory links)``, drop-in first."""
        return (("dropin", self._dropin_root, False), ("shipped", self._shipped_root, True))

    def _is_shipped(self, name: str) -> bool:
        root = self._shipped_root
        if os.path.lexists(root / name):
            return True
        return any(os.path.lexists(category / name) for category in _subdirs(root, follow=True))

    @property
    def _scratch_path(self) -> Path:
        return self._dropin_root / ARCHIVE_DIR / SCRATCH_DIR

    def get(self, name: str) -> SkillRow:
        """One skill: the drop-in copy if there is one, else the shipped one.

        ``files`` names the supporting files (relative paths); their content
        is not returned.
        """
        _check_name(name)
        for source, root, follow_dirs in self._sources():
            found = _find(root, name, follow_dirs=follow_dirs)
            if found is None:
                continue
            directory, category = found
            try:
                text = _read_text(directory / SKILL_FILE)
            except OSError as exc:
                log.warning("bridge.skill_unreadable name=%s source=%s error=%s", name, source, exc)
                continue
            return {
                "name": name,
                "source": source,
                "category": category,
                "skill_md": text,
                "files": _list_files(directory),
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
            try:
                key_bytes = len(key.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise _invalid(f"file name {key!r} is not valid UTF-8 text") from exc
            if key_bytes > MAX_FILE_NAME_BYTES:
                raise _invalid(
                    f"file name {key[:40]!r}... is {key_bytes} bytes; "
                    f"the limit is {MAX_FILE_NAME_BYTES}"
                )
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

        The files are written to a staging directory under ``.archive/.bridge``
        and renamed into place, so a failure leaves the previous skill (or
        none) and no debris. A name that already lives in the drop-in directory
        under a category is replaced there. Writers of one drop-in root run
        one at a time, the returned :meth:`get` included.
        """
        self.validate(name, skill_md, files)
        with self._lock:
            if self._is_shipped(name):
                raise BridgeError(
                    "bridge.skill_readonly", f"{name!r} is a shipped skill and cannot be changed"
                )
            target = self._put_target(name)
            try:
                self._write(name, target.parent, skill_md, files or {})
            except nofollow.UnsafePathError as exc:
                raise _invalid(f"refusing to write: {exc.reason}") from exc
            return self.get(name)

    def delete(self, name: str) -> None:
        """Remove a drop-in skill's directory; a shipped skill is read-only."""
        _check_name(name)
        with self._lock:
            self._reap_scratch()
            found = _find(self._dropin_root, name, follow_dirs=False)
            if found is None:
                if self._is_shipped(name):
                    raise BridgeError(
                        "bridge.skill_readonly",
                        f"{name!r} is a shipped skill and cannot be deleted",
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
        for source, root, follow_dirs in self._sources():
            for name, category, directory in _scan(root, follow_dirs=follow_dirs):
                skill_file = directory / SKILL_FILE
                try:
                    text = _read_text(skill_file)
                    mtime = os.lstat(skill_file).st_mtime
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
                        "files": _list_files(directory),
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
            existing = _find(self._dropin_root, name, follow_dirs=False)
            return existing[0] if existing else flat
        if stat.S_ISLNK(st.st_mode):
            raise _invalid("target is a symlink")
        if not stat.S_ISDIR(st.st_mode) or not _has_skill_file(flat):
            raise _invalid(f"{flat.name!r} exists in the drop-in directory and is not a skill")
        return flat

    def _reap_scratch(self) -> None:
        """Remove stale scratch entries; nothing to do when there is no scratch directory."""
        try:
            root_fd = nofollow.open_dir(self._dropin_root)
        except FileNotFoundError:
            return
        except nofollow.UnsafePathError as exc:
            raise _invalid(f"refusing to write: {exc.reason}") from exc
        try:
            try:
                scratch_fd = _open_scratch(root_fd, create=False)
            except FileNotFoundError:
                return
            try:
                _reap(scratch_fd)
            finally:
                os.close(scratch_fd)
        finally:
            os.close(root_fd)

    def _write(self, name: str, parent: Path, skill_md: str, files: dict[str, str]) -> None:
        """Stage the skill under ``.archive/.bridge`` and swap it into ``parent``."""
        root_fd = nofollow.open_dir(self._dropin_root, create=True)
        parent_fd = root_fd
        scratch_fd = -1
        try:
            if parent != self._dropin_root:
                parent_fd = nofollow.open_dir(parent)
            scratch_fd = _open_scratch(root_fd, create=True)
            _reap(scratch_fd)
            stage = f"stage-{name}-{uuid.uuid4().hex}"
            try:
                self._stage(self._scratch_path / stage, skill_md, files)
                _swap(scratch_fd, stage, parent_fd, name)
            except BaseException:
                shutil.rmtree(stage, ignore_errors=True, dir_fd=scratch_fd)
                raise
        finally:
            for fd in {root_fd, parent_fd, scratch_fd}:
                if fd >= 0:
                    os.close(fd)

    @staticmethod
    def _stage(stage: Path, skill_md: str, files: dict[str, str]) -> None:
        nofollow.ensure_dir(stage, mode=0o755, create=True)
        nofollow.write_file(stage / SKILL_FILE, skill_md, mode=0o644, create_dirs=False)
        for fname, content in files.items():
            nofollow.write_file(stage / fname, content, mode=0o644, create_dirs=False)


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
