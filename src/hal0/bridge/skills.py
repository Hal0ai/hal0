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
second copy. ``.archive`` here is the bridge's own: at the same Hermes ref the
curator's archive is ``~/.hermes/skills/.archive`` only (``_archive_dir``,
``tools/skill_usage.py:55-56``) and a skill under ``skills.external_dirs``, as
the drop-in root is, is read-only to the curator (``tools/skill_usage.py:274-275``),
so nothing of Hermes's moves into or out of this directory.

Replacing an existing skill takes two renames: the old copy is parked, then the
stage moved in. Before the first, a marker ``stage-<name>-<uuid>.json`` naming
both is written to the scratch directory. A failure between them puts the old
copy back at once; a crash between them leaves the skill parked, so Hermes does
not see it until the next ``put`` or ``delete``, whose cleanup puts it back.
That cleanup runs first in every ``put`` and ``delete``, with the write lock
held, so no entry in ``.archive/.bridge/`` belongs to a live write: it finishes
or undoes each swap a marker names (the old copy goes back if the skill is
missing, else it is dropped), puts back any other parked ``old-<name>-<uuid>``
whose skill is missing from the drop-in root, and only then reaps the rest.

``put`` and ``delete`` hold one lock per drop-in root for their whole run
(hal0-api is one process; the CLI never writes skills), so same-name writers
never interleave their renames and the trailing ``get`` of a ``put`` cannot land
in another writer's swap window.
"""

from __future__ import annotations

import contextlib
import errno
import json
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
    lines = text.removeprefix("\ufeff").replace("\r\n", "\n").split("\n")
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
    except (ValueError, RecursionError) as exc:
        # safe_load also raises these: a timestamp that is no date (``created:
        # 2024-13-45``) is a ValueError, and deep nesting a RecursionError.
        reason = (
            "nested too deeply" if isinstance(exc, RecursionError) else "a value it cannot read"
        )
        raise _invalid(f"frontmatter is not valid YAML: {reason}") from exc
    if not isinstance(data, dict):
        raise _invalid("frontmatter must be a YAML mapping")
    return data


def _description_of(text: str) -> tuple[str, bool]:
    """Frontmatter ``description`` (``""`` when absent), and whether the frontmatter is broken."""
    try:
        description = _parse_frontmatter(text).get("description")
    except BridgeError:
        return "", True
    return (description.strip() if isinstance(description, str) else ""), False


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

    Called with the write lock held, after :meth:`SkillLibrary._recover`, so none
    is a live stage or a parked copy that still has to go back: each is left over
    from a crash, a failed removal or a lost race.
    """
    for entry in os.listdir(scratch_fd):
        _remove(scratch_fd, entry)


_MARKER_SUFFIX = ".json"
_PARKED_RE = re.compile(r"^old-(?P<name>[a-z0-9][a-z0-9-]{0,63})-[0-9a-f]{32}$")
_STAGE_RE = re.compile(r"^stage-[a-z0-9][a-z0-9-]{0,63}-[0-9a-f]{32}$")


def _exists(dir_fd: int, name: str) -> bool:
    try:
        os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return False
    return True


def _write_marker(scratch_fd: int, marker: str, body: dict[str, str]) -> None:
    """Write the swap marker ``marker`` in the scratch directory, durably, never through a link."""
    fd = os.open(
        marker,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o644,
        dir_fd=scratch_fd,
    )
    try:
        os.write(fd, json.dumps(body).encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_marker(scratch_fd: int, marker: str) -> dict[str, str] | None:
    """A swap marker's ``{name, category, stage, old}``, or None when it is not one we wrote.

    Only hal0 writes the scratch directory, but it is on disk: an entry named like a
    marker that is a directory, a FIFO or a link, or that cannot be read, is no
    marker (logged ``bridge.skill_marker_unreadable``), so :meth:`SkillLibrary._recover`
    carries on. ``O_NONBLOCK`` keeps a FIFO from blocking the open under the write
    lock, and ``fstat`` refuses anything but a regular file before it is read.
    """
    try:
        fd = os.open(
            marker,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=scratch_fd,
        )
    except OSError as exc:
        log.warning("bridge.skill_marker_unreadable marker=%s error=%s", marker, exc)
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            log.warning("bridge.skill_marker_unreadable marker=%s error=not a regular file", marker)
            return None
        raw = os.read(fd, 4096)
    except OSError as exc:
        log.warning("bridge.skill_marker_unreadable marker=%s error=%s", marker, exc)
        return None
    finally:
        os.close(fd)
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    name, category = body.get("name"), body.get("category")
    stage, old = body.get("stage"), body.get("old")
    if not (isinstance(name, str) and NAME_RE.fullmatch(name)):
        return None
    if not (isinstance(stage, str) and _STAGE_RE.fullmatch(stage)):
        return None
    if not (isinstance(old, str) and (m := _PARKED_RE.fullmatch(old)) and m["name"] == name):
        return None
    if not isinstance(category, str) or (
        category and (category.startswith(".") or any(c in category for c in "/\\\0"))
    ):
        return None
    return {"name": name, "category": category, "stage": stage, "old": old}


def _swap(scratch_fd: int, stage: str, parent_fd: int, name: str, *, category: str) -> None:
    """Rename ``stage`` (in the scratch directory) over ``name`` in ``parent_fd``.

    An existing skill is parked in the scratch directory first (a directory
    cannot be renamed over a non-empty one), put back if the second rename
    fails, and removed once the new one is in place. A marker naming the
    stage and the parked copy is written before the first rename and removed
    after the last step, so a crash in between is finished or undone by the
    next cleanup (module docstring). ``category`` is the parent's name below the
    drop-in root, ``""`` for the root itself.
    """
    if not _exists(parent_fd, name):
        os.replace(stage, name, src_dir_fd=scratch_fd, dst_dir_fd=parent_fd)
        return
    old = f"old-{name}-{uuid.uuid4().hex}"
    marker = stage + _MARKER_SUFFIX
    _write_marker(
        scratch_fd, marker, {"name": name, "category": category, "stage": stage, "old": old}
    )
    os.replace(name, old, src_dir_fd=parent_fd, dst_dir_fd=scratch_fd)
    try:
        os.replace(stage, name, src_dir_fd=scratch_fd, dst_dir_fd=parent_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.replace(old, name, src_dir_fd=scratch_fd, dst_dir_fd=parent_fd)
        raise
    _remove(scratch_fd, old)
    _remove(scratch_fd, marker)


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

    def is_shipped(self, name: str) -> bool:
        """True when hal0's shipped mirror holds ``name``: such a skill cannot be put or deleted.

        The same test :meth:`put` applies, so a caller can refuse a doomed write
        up front. Raises ``bridge.skill_invalid`` for a name that is not a valid
        skill name.
        """
        _check_name(name)
        return self._is_shipped(name)

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
            # Before the target is chosen: a copy an earlier crash left parked goes back
            # to where it was, so this write replaces it there instead of beside it.
            self._reap_scratch()
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
        apart. A skill whose frontmatter cannot be read (not YAML, a value YAML
        cannot load, nested too deeply) is still listed, with ``broken: true``
        and ``description: ""``, and a warning is logged: one bad drop-in file
        never hides the others. Every other row has ``broken: false``.
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
                description, broken = _description_of(text)
                if broken:
                    log.warning(
                        "bridge.skill_broken name=%s source=%s: its frontmatter cannot be read",
                        name,
                        source,
                    )
                rows.append(
                    {
                        "name": name,
                        "source": source,
                        "category": category,
                        "description": description,
                        "mtime": mtime,
                        "files": _list_files(directory),
                        "broken": broken,
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
                self._recover(root_fd, scratch_fd)
                _reap(scratch_fd)
            finally:
                os.close(scratch_fd)
        finally:
            os.close(root_fd)

    def _recover(self, root_fd: int, scratch_fd: int) -> None:
        """Finish or undo interrupted swaps, then put back parked copies whose skill is gone.

        Runs with the write lock held, before :func:`_reap` (module docstring). Every
        step is best effort: a failure is logged and the entry is left to the reap,
        except a parked copy, which is never reaped while its skill is missing.
        """
        entries = sorted(os.listdir(scratch_fd))
        for marker in (e for e in entries if e.endswith(_MARKER_SUFFIX)):
            info = _read_marker(scratch_fd, marker)
            if info is not None:
                try:
                    self._settle_swap(root_fd, scratch_fd, info)
                except OSError as exc:
                    log.warning("bridge.skill_swap_unsettled marker=%s error=%s", marker, exc)
            _remove(scratch_fd, marker)
        for entry in sorted(os.listdir(scratch_fd)):
            parked = _PARKED_RE.fullmatch(entry)
            if parked is None or self._present(parked["name"]):
                continue
            try:
                os.replace(entry, parked["name"], src_dir_fd=scratch_fd, dst_dir_fd=root_fd)
                log.warning("bridge.skill_restored name=%s from=%s", parked["name"], entry)
            except OSError as exc:
                log.warning("bridge.skill_restore_failed name=%s error=%s", parked["name"], exc)

    def _settle_swap(self, root_fd: int, scratch_fd: int, info: dict[str, str]) -> None:
        """Bring one interrupted swap to a whole skill: the old copy back, or the new one in."""
        name, category = info["name"], info["category"]
        parent_fd = root_fd
        if category:
            try:
                parent_fd = _open_child_dir(root_fd, category, create=False)
            except FileNotFoundError:
                parent_fd = root_fd  # the category is gone: the skill goes back at the root
        try:
            if _exists(parent_fd, name):
                return  # the swap finished (or never started): what is left is debris
            for source in (info["old"], info["stage"]):
                if _exists(scratch_fd, source):
                    os.replace(source, name, src_dir_fd=scratch_fd, dst_dir_fd=parent_fd)
                    log.warning("bridge.skill_swap_settled name=%s from=%s", name, source)
                    return
        finally:
            if parent_fd != root_fd:
                os.close(parent_fd)

    def _present(self, name: str) -> bool:
        """Whether the drop-in root holds ``name``, as a skill or as anything else."""
        if os.path.lexists(self._dropin_root / name):
            return True
        return _find(self._dropin_root, name, follow_dirs=False) is not None

    def _write(self, name: str, parent: Path, skill_md: str, files: dict[str, str]) -> None:
        """Stage the skill under ``.archive/.bridge`` and swap it into ``parent``."""
        root_fd = nofollow.open_dir(self._dropin_root, create=True)
        parent_fd = root_fd
        scratch_fd = -1
        try:
            if parent != self._dropin_root:
                parent_fd = nofollow.open_dir(parent)
            scratch_fd = _open_scratch(root_fd, create=True)
            self._recover(root_fd, scratch_fd)
            _reap(scratch_fd)
            stage = f"stage-{name}-{uuid.uuid4().hex}"
            category = "" if parent == self._dropin_root else parent.name
            try:
                self._stage(self._scratch_path / stage, skill_md, files)
                _swap(scratch_fd, stage, parent_fd, name, category=category)
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
