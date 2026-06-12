"""``hal0 registry`` subcommands — v0.1.x backup recovery.

This module hosts operator tooling that touches
``/var/lib/hal0/registry/registry.toml`` directly. Today there is
exactly one such command — ``import`` — which restores a registry
extracted from a ``hal0-v0.1-backup-YYYY-MM-DD.tar.gz`` tarball
produced by following the v0.1.x backup instructions in install.sh
(see the v0.2 adoption plan §9).

Design contract (from plan §11 PR-21):

* **Registry only.** v0.1.x → v0.2 is a clean break. Slot selections,
  capabilities, and per-slot TOML files are NOT migrated. The operator
  redoes slot selection via the bundle picker. Plan §9 is explicit
  about this: "Slot selections must be redone — alpha social contract".
* **Refuse to overwrite.** A pre-existing ``registry.toml`` at the
  canonical path is left alone unless ``--force`` is passed. The
  freshly-installed registry on a v0.2 box may already have curated
  picks from the bundle picker; we don't silently clobber them.
* **Atomic.** The copy goes through a sibling tempfile + ``os.replace``
  so a crash mid-copy never leaves a half-written ``registry.toml``.
* **Tar safety.** The tarball is extracted into a freshly-created
  ``tempfile.mkdtemp`` and we refuse any member with an absolute path
  or a ``..`` component (defence against a hand-crafted backup).

What this script does NOT do:

* It does NOT rewrite slot or capability state — the registry import
  is one step of a larger recovery flow, and the operator may want to
  inspect the imported file first. registry.toml is the sole catalog;
  downstream consumers pick it up on the next read.
* It does NOT extract ``/etc/hal0/`` from the backup — v0.2's
  ``capabilities.toml`` schema is incompatible with v0.1.x's
  per-slot TOML files.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

import typer
from rich.console import Console

console = Console()

app = typer.Typer(
    name="registry",
    help="Inspect + repair the hal0 model registry.",
    no_args_is_help=True,
)


@app.callback()
def _registry_callback() -> None:
    """Inspect + repair the hal0 model registry.

    Currently exposes ``import`` (v0.1.x backup recovery). Future
    subcommands will live here; the callback keeps Typer from
    auto-collapsing the single-command group, which would make a
    second subcommand a breaking change at the CLI surface.
    """


# Default v0.2 canonical path for registry.toml. Override via --dest for
# tests + dev installs. Source: the v0.2 adoption plan §6.1 + §9.
DEFAULT_REGISTRY_PATH = Path("/var/lib/hal0/registry/registry.toml")

# Relative path inside the v0.1.x backup tarball. The backup instructions
# in install.sh archive ``/var/lib/hal0/registry``, which extracts as
# ``var/lib/hal0/registry/registry.toml`` (tar strips the leading slash).
# We accept either layout to be robust against operators who manually
# repacked the backup.
_TARBALL_REGISTRY_CANDIDATES: tuple[str, ...] = (
    "var/lib/hal0/registry/registry.toml",
    "registry/registry.toml",
    "registry.toml",
)


def _is_within(base: Path, target: Path) -> bool:
    """Return True if ``target`` resolves inside ``base``.

    Used to defend against tar members with absolute paths or ``..``
    components — a hand-crafted backup could otherwise write anywhere
    on disk that the running user has permission for.
    """
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract ``tar`` into ``dest`` with path-traversal protection."""
    for member in tar.getmembers():
        member_path = dest / member.name
        if not _is_within(dest, member_path):
            raise typer.BadParameter(
                f"refusing to extract {member.name!r}: escapes the tempdir "
                f"(absolute path or .. component)",
                param_hint="path",
            )
        # Filter out symlinks/hardlinks/devices — only regular files +
        # directories are expected in a hal0 backup; anything else is
        # almost certainly hostile.
        if member.issym() or member.islnk() or member.isdev():
            raise typer.BadParameter(
                f"refusing to extract {member.name!r}: link/device member "
                f"not allowed in hal0 backup",
                param_hint="path",
            )
    # `filter="data"` is the safest standard filter in Python 3.12+; it
    # strips ownership/mode metadata and rejects unsafe member types.
    # The pre-loop scan above adds belt-and-braces refusal for absolute
    # paths so we don't depend solely on tarfile's behaviour matrix.
    tar.extractall(path=dest, filter="data")


def _find_registry_in_dir(root: Path) -> Path | None:
    """Locate ``registry.toml`` under ``root`` using the candidate list."""
    for rel in _TARBALL_REGISTRY_CANDIDATES:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def _atomic_copy(source: Path, dest: Path) -> None:
    """Copy ``source`` to ``dest`` via a sibling tempfile + ``os.replace``.

    Atomic-write pattern (tempfile + ``os.replace``) so a crash
    mid-copy never leaves a partial ``registry.toml`` at the canonical
    path — which would brick every registry consumer until the
    operator manually rolls back.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".tmp",
        dir=dest.parent,
    )
    tmp_path = Path(tmp_str)
    try:
        try:
            with (
                open(source, "rb") as src,
                os.fdopen(fd, "wb") as dst,
            ):
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, dest)
        tmp_path = None  # type: ignore[assignment]
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


@app.command("import")
def import_backup(
    path: Path = typer.Argument(
        ...,
        help="Path to hal0-v0.1-backup-YYYY-MM-DD.tar.gz produced by the "
        "v0.1.x backup instructions in install.sh.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite an existing registry.toml at the destination. "
        "Without this, the command refuses to clobber a registry that "
        "may already hold v0.2 selections.",
    ),
    dest: Path = typer.Option(
        DEFAULT_REGISTRY_PATH,
        "--dest",
        help="Destination registry.toml path. Test/dev escape hatch — "
        "production always uses /var/lib/hal0/registry/registry.toml.",
    ),
) -> None:
    """Restore ``registry.toml`` from a v0.1.x backup tarball.

    Reads the tarball at ``PATH``, extracts it into a tempdir, locates
    ``registry.toml`` inside (canonical layout is
    ``var/lib/hal0/registry/registry.toml``; bare ``registry/`` and
    flat layouts also accepted for hand-repacked backups), and atomically
    copies it to ``--dest`` (default: ``/var/lib/hal0/registry/registry.toml``).

    Refuses to overwrite an existing destination unless ``--force`` is
    passed — a freshly-installed v0.2 box may already have curated picks
    from the bundle picker.

    Slot selections, ``capabilities.toml``, per-slot TOML files, and all
    other v0.1.x state are NOT restored. Plan §9 is explicit: v0.1.x →
    v0.2 is a clean break. After import, redo slot selection via the
    bundle picker or ``hal0 slot create``.
    """
    # ── 1. Validate the source tarball ────────────────────────────────
    if not path.exists():
        console.print(f"[red]error[/red]: backup not found: {path}")
        raise typer.Exit(2)
    if not path.is_file():
        console.print(f"[red]error[/red]: not a regular file: {path}")
        raise typer.Exit(2)

    # ── 2. Refuse to clobber an existing registry unless --force ──────
    if dest.exists() and not force:
        console.print(
            f"[red]error[/red]: destination already exists: {dest}\n"
            f"        pass [bold]--force[/bold] to overwrite, or move the "
            f"existing file aside first."
        )
        raise typer.Exit(1)

    # ── 3. Extract into a tempdir ─────────────────────────────────────
    tmpdir = Path(tempfile.mkdtemp(prefix="hal0-registry-import-"))
    try:
        try:
            with tarfile.open(path, "r:*") as tar:
                _safe_extract(tar, tmpdir)
        except tarfile.ReadError as exc:
            console.print(
                f"[red]error[/red]: cannot read {path} as a tar archive: {exc}\n"
                f"        is this really a hal0-v0.1-backup-*.tar.gz?"
            )
            raise typer.Exit(2) from None
        except tarfile.TarError as exc:
            console.print(f"[red]error[/red]: malformed tarball: {exc}")
            raise typer.Exit(2) from None

        # ── 4. Locate registry.toml ───────────────────────────────────
        source_registry = _find_registry_in_dir(tmpdir)
        if source_registry is None:
            tried = ", ".join(_TARBALL_REGISTRY_CANDIDATES)
            console.print(
                f"[red]error[/red]: registry.toml not found in {path}\n"
                f"        looked under: {tried}\n"
                f"        is the backup missing /var/lib/hal0/registry/?"
            )
            raise typer.Exit(2)

        # ── 5. Atomically copy into place ─────────────────────────────
        try:
            _atomic_copy(source_registry, dest)
        except PermissionError as exc:
            console.print(
                f"[red]error[/red]: cannot write {dest}: {exc}\n"
                f"        re-run with sudo, or pass --dest to a writable path."
            )
            raise typer.Exit(1) from None
        except OSError as exc:
            console.print(f"[red]error[/red]: copy failed: {exc}")
            raise typer.Exit(1) from None
    finally:
        # Always nuke the tempdir, even on success. Backup tarballs can
        # be large and we don't want them lingering in /tmp.
        with contextlib.suppress(OSError):
            shutil.rmtree(tmpdir)

    # ── 6. Success guidance ───────────────────────────────────────────
    console.print(
        f"[green]registry imported[/green] from {path} → {dest}\n"
        f"\n"
        f"Slot selections from v0.1.x are NOT migrated. Use the bundle\n"
        f"picker or [bold]hal0 slot create[/bold] to declare slots."
    )
