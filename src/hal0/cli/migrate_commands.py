"""``hal0 migrate`` subcommands — one-shot v0.2 layout reorganisation.

This module hosts disk-layout reshapes that v0.2 needs the operator to
run once. Today there is exactly one such command: ``model-layout``,
which reorganises the v0.1.x ad-hoc model store at ``/mnt/ai-models/``
into the canonical ``<recipe>/<capability>/`` tree rooted at
``/var/lib/hal0/models/`` (see lemonade-adoption-plan §6.1 + ADR-0008
§7).

Design contract (from plan §11 PR-7):

* **Default is dry-run.** Mutations only happen on explicit ``--apply``.
* **Idempotent.** A second ``--apply`` after the first is a no-op.
* **Classification by registry.** ``registry.toml`` is the source of
  truth for capability + recipe selection. Models on disk that aren't
  in the registry are classified by the directory of origin (the v0.1.x
  layout described in the ``hal0_model_store_layout`` memory) and
  reported as "imported, classify manually" warnings.
* **Symlinks only.** Never copies or moves the files themselves — the
  v0.1.x slots may still be reading them, and the files can be huge.
  The canonical tree consists entirely of per-leaf symlinks pointing at
  the real on-disk location.
* **Atomic.** Each symlink is written through a sibling tempfile +
  ``os.replace`` so a crash mid-run never leaves a half-built link.
* **Safe by default.** Refuses to overwrite a canonical symlink that
  already points somewhere else unless ``--force`` is given. Refuses to
  run when the canonical root is itself a bind mount or symlink (would
  reflect the mutation into an unexpected place).

What the script does NOT do:

* It does not touch the v0.1.x layout under ``/mnt/ai-models/`` — that
  stays exactly where it is. Slots running off it keep working.
* It does not download, copy, move, or hash any model file.
* It does not write to ``/mnt/ai-models/`` at all.
* It does not migrate ``/mnt/ai-models/huggingface/`` (HF cache) —
  Lemonade reads HF cache directly, and clobbering it would re-trigger
  multi-GB redownloads.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import typer
from rich.console import Console
from rich.table import Table

console = Console()

app = typer.Typer(
    name="migrate",
    help="One-shot v0.2 layout migrations (disk reshape, no data move).",
    no_args_is_help=True,
)


@app.callback()
def _migrate_callback() -> None:
    """One-shot v0.2 layout migrations (disk reshape, no data move).

    The callback exists so Typer keeps ``model-layout`` as a named
    subcommand even though it's currently the only command — otherwise
    Typer auto-collapses single-command groups and the operator ends up
    typing ``hal0 migrate`` instead of ``hal0 migrate model-layout``,
    which would break the moment we add a second migration.
    """


# ── Canonical tree shape ───────────────────────────────────────────────────────

# (recipe, capability) directories the script mkdir -ps under the canonical
# root. Anything outside this set is rejected at classification time so a typo
# in the registry can't seed a stray directory.
CANONICAL_LEAVES: tuple[tuple[str, str], ...] = (
    ("llamacpp", "chat"),
    ("llamacpp", "embed"),
    ("llamacpp", "rerank"),
    ("flm", "chat"),
    ("flm", "embed"),
    ("whispercpp", "stt"),
    ("whispercpp", "moonshine"),
    ("kokoro", "tts"),
    ("sd-cpp", "img"),
    ("collections", "omni"),
)

# Default paths. Override via CLI flags in tests + dev installs.
DEFAULT_REGISTRY_PATH = Path("/var/lib/hal0/registry/registry.toml")
DEFAULT_MOUNT_ROOT = Path("/mnt/ai-models")
DEFAULT_CANONICAL_ROOT = Path("/var/lib/hal0/models")

# v0.1.x directories under DEFAULT_MOUNT_ROOT that we glob for non-registry
# models. Maps to (recipe, capability) for the canonical placement.
#
# Source: ``hal0_model_store_layout`` memory + the v0.1 launchers
# (providers/flm.py, providers/moonshine.py, providers/kokoro.py).
V01_DIR_TO_LEAF: dict[str, tuple[str, str]] = {
    "flm-ubuntu": ("flm", "chat"),
    "moonshine_voice": ("whispercpp", "moonshine"),
    "voices": ("kokoro", "tts"),
    "comfyui": ("sd-cpp", "img"),
}

# Directories under DEFAULT_MOUNT_ROOT that the script must NOT touch.
# - ``huggingface``: shared HF cache; Lemonade reads it directly.
# - any leaf already in canonical layout: those are the migration target.
PROTECTED_DIRS: frozenset[str] = frozenset({"huggingface"})


# ── Registry-driven capability + recipe inference ──────────────────────────────

# hal0 capability vocab → canonical capability directory.
# Mirrors the rules in lemonade-adoption-plan-2026-05-22 §6.1.
_CAPABILITY_TO_LEAF_CAP: dict[str, str] = {
    "chat": "chat",
    "embed": "embed",
    "embedding": "embed",
    "embeddings": "embed",
    "rerank": "rerank",
    "reranking": "rerank",
    "asr": "stt",
    "transcription": "stt",
    "stt": "stt",
    "tts": "tts",
    "image": "img",
    "img": "img",
}

# Strength order — strongest non-chat capability wins when a model
# advertises multiple. Same idea as
# ``hal0.lemonade.server_models_gen._CAPABILITY_STRENGTH`` but the leaf
# vocab is different.
_CAP_STRENGTH: tuple[str, ...] = (
    "rerank",
    "reranking",
    "embed",
    "embedding",
    "embeddings",
    "asr",
    "transcription",
    "stt",
    "tts",
    "image",
    "img",
    "chat",
)

# hal0 backend name → canonical recipe directory.
_BACKEND_TO_RECIPE: dict[str, str] = {
    "vulkan": "llamacpp",
    "rocm": "llamacpp",
    "cuda": "llamacpp",
    "cpu": "llamacpp",
    "llamacpp": "llamacpp",
    "moonshine": "whispercpp",
    "whispercpp": "whispercpp",
    "whisper": "whispercpp",
    "kokoro": "kokoro",
    "sdcpp": "sd-cpp",
    "sd-cpp": "sd-cpp",
    "stable-diffusion": "sd-cpp",
    "comfyui": "sd-cpp",
    "flm": "flm",
}

# Fallback recipe per leaf-capability when the registry doesn't advertise any
# recognised backend. Mirrors ``server_models_gen._DEFAULT_RECIPE_BY_LABEL``
# but keyed by our leaf-cap vocab.
_DEFAULT_RECIPE_BY_LEAF_CAP: dict[str, str] = {
    "chat": "llamacpp",
    "embed": "llamacpp",
    "rerank": "llamacpp",
    "stt": "whispercpp",
    "tts": "kokoro",
    "img": "sd-cpp",
}


# ── Plan dataclasses ───────────────────────────────────────────────────────────


ActionKind = Literal["create", "skip-exists", "would-overwrite", "overwrite", "unclassified"]


@dataclass(frozen=True)
class SymlinkAction:
    """One planned symlink (or one skip / one warning)."""

    kind: ActionKind
    link_path: Path
    target_path: Path | None
    source: str  # "registry:<model_id>" | "imported:<dir>"
    reason: str = ""


@dataclass
class MigrationReport:
    """Aggregate report returned from ``plan_migration`` + ``execute``."""

    actions: list[SymlinkAction] = field(default_factory=list)
    unclassified: list[Path] = field(default_factory=list)

    def by_leaf(self) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for a in self.actions:
            if a.kind in ("create", "overwrite"):
                # link_path = <root>/<recipe>/<capability>/<name>
                parts = a.link_path.parts
                if len(parts) >= 3:
                    leaf = (parts[-3], parts[-2])
                    out[leaf] = out.get(leaf, 0) + 1
        return out


# ── Registry reading ───────────────────────────────────────────────────────────


def _read_registry(path: Path) -> dict[str, dict[str, Any]]:
    """Parse registry.toml into ``{model_id: entry}`` or return ``{}``.

    Empty / missing / malformed registry yields ``{}`` so a fresh-install
    box (registry not yet populated) still runs cleanly — every on-disk
    model just falls into the "imported, classify manually" bucket.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return {}

    raw = data.get("models", {}) if isinstance(data, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for mid, entry in raw.items():
            if isinstance(entry, dict):
                out[str(mid)] = entry
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                mid = entry.get("id")
                if isinstance(mid, str) and mid:
                    out[mid] = entry
    return out


def _pick_capability(caps: Iterable[Any]) -> str | None:
    """Pick the strongest registered capability, or ``None`` if none usable."""
    seen = {str(c).lower() for c in caps if isinstance(c, str)}
    for candidate in _CAP_STRENGTH:
        if candidate in seen:
            return candidate
    return None


def _classify_registry_entry(entry: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(recipe, capability)`` for a registry entry, or ``None``.

    Resolution order matches the production path the dispatcher takes:

    1. Pick the strongest capability (rerank > embed > asr > tts > img >
       chat). ``None`` if no recognised capability.
    2. Map capability → canonical leaf-capability directory.
    3. Map the registry's first recognised backend → recipe. ``flm``
       in the backends list always wins (NPU is exclusive). Otherwise
       fall back to the per-leaf-capability default recipe.
    """
    cap = _pick_capability(list(entry.get("capabilities") or []))
    if cap is None:
        return None
    leaf_cap = _CAPABILITY_TO_LEAF_CAP.get(cap)
    if leaf_cap is None:
        return None

    backends = [str(b).lower() for b in (entry.get("backends") or []) if isinstance(b, str)]
    recipe: str | None = None
    if "flm" in backends:
        recipe = "flm"
    else:
        for b in backends:
            r = _BACKEND_TO_RECIPE.get(b)
            if r:
                recipe = r
                break
    if recipe is None:
        recipe = _DEFAULT_RECIPE_BY_LEAF_CAP.get(leaf_cap, "llamacpp")

    if (recipe, leaf_cap) not in CANONICAL_LEAVES:
        # Defensive — registry shouldn't combine these, but if it does the
        # script should report rather than create stray directories.
        return None
    return recipe, leaf_cap


def _entry_disk_path(entry: dict[str, Any]) -> Path | None:
    """Best-effort absolute path to the entry's on-disk file."""
    p = entry.get("path") or ""
    if isinstance(p, str) and p:
        return Path(p)
    return None


# ── On-disk scan (v0.1.x layout) ───────────────────────────────────────────────


def _scan_v01_layout(mount_root: Path) -> list[tuple[Path, tuple[str, str] | None]]:
    """Walk the v0.1.x directories under ``mount_root``.

    Returns ``[(disk_path, (recipe, capability) | None), ...]``. Files
    under directories we know the layout of get a classification; files
    under ``local/`` (which historically holds a mix of GGUFs across
    capabilities) get ``None`` — those need registry coverage or manual
    classification.
    """
    out: list[tuple[Path, tuple[str, str] | None]] = []
    if not mount_root.exists():
        return out

    # Known dirs (v0.1.x or already-canonical layout).
    for child in sorted(mount_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name in PROTECTED_DIRS:
            continue
        # Already-canonical: <mount_root>/<recipe>/<capability>/...
        if (name,) in {(r,) for r, _ in CANONICAL_LEAVES}:
            for cap_dir in sorted(child.iterdir()):
                if not cap_dir.is_dir():
                    continue
                leaf = (name, cap_dir.name)
                if leaf not in CANONICAL_LEAVES:
                    continue
                for f in sorted(_iter_files(cap_dir)):
                    out.append((f, leaf))
            continue
        # v0.1.x well-known dir.
        leaf = V01_DIR_TO_LEAF.get(name)
        if leaf is not None:
            for f in sorted(_iter_files(child)):
                out.append((f, leaf))
            continue
        # ``local/`` (or anything else) — leave unclassified for registry
        # lookup or warning.
        for f in sorted(_iter_files(child)):
            out.append((f, None))
    return out


def _iter_files(root: Path) -> Iterable[Path]:
    """Yield every regular file beneath ``root`` (depth-first, deterministic).

    Walks into subdirectories so the script also catches the directory-as-
    model layouts (FLM model dirs, moonshine model dirs). Each "leaf"
    becomes one symlink at the canonical layer.

    Heuristic: when a subdirectory contains a ``model.gguf`` /
    ``config.json`` / ``preprocessor_config.json``, treat the *directory*
    as one model (yields the directory, not its files). Otherwise yield
    each regular file.
    """
    if not root.exists():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            sentinels = {p.name for p in entry.iterdir() if p.is_file()}
            if sentinels & {"config.json", "preprocessor_config.json", "model.gguf"}:
                yield entry
                continue
            yield from _iter_files(entry)
        elif entry.is_file():
            yield entry


# ── Planner ────────────────────────────────────────────────────────────────────


def plan_migration(
    *,
    registry_path: Path,
    mount_root: Path,
    canonical_root: Path,
    force: bool,
) -> MigrationReport:
    """Compute the migration plan without touching the filesystem.

    Returns a ``MigrationReport`` whose ``actions`` list every symlink the
    apply step would write (or skip, or refuse), plus an ``unclassified``
    list of disk paths that couldn't be placed.
    """
    report = MigrationReport()

    # Index disk files by their absolute resolved path so the registry walk
    # can dedupe against on-disk reality.
    disk_by_path: dict[Path, tuple[str, str] | None] = {}
    for disk_path, leaf in _scan_v01_layout(mount_root):
        try:
            resolved = disk_path.resolve()
        except OSError:
            resolved = disk_path
        disk_by_path[resolved] = leaf

    # Track every link we plan to create so we don't double-plan when a
    # registry entry and an on-disk scan name the same target.
    planned_links: set[Path] = set()

    # ── Pass 1: registry-driven ──────────────────────────────────────────
    registry = _read_registry(registry_path)
    for mid, entry in registry.items():
        disk = _entry_disk_path(entry)
        if disk is None:
            continue
        leaf = _classify_registry_entry(entry)
        if leaf is None:
            report.unclassified.append(disk)
            report.actions.append(
                SymlinkAction(
                    kind="unclassified",
                    link_path=canonical_root,
                    target_path=disk,
                    source=f"registry:{mid}",
                    reason="no recognised capability in registry entry",
                )
            )
            continue
        recipe, leaf_cap = leaf
        link_path = canonical_root / recipe / leaf_cap / disk.name
        action = _plan_one_link(
            link_path=link_path,
            target=disk,
            source=f"registry:{mid}",
            force=force,
        )
        if action.link_path not in planned_links:
            report.actions.append(action)
            planned_links.add(action.link_path)

    # ── Pass 2: on-disk fallback (v0.1.x directories) ────────────────────
    registry_paths = {p for p in (_entry_disk_path(e) for e in registry.values()) if p is not None}
    for disk_path, leaf in disk_by_path.items():
        if disk_path in registry_paths:
            continue  # already handled in pass 1
        if leaf is None:
            # Unknown dir (e.g. ``local/`` mixed bag) — surface for manual
            # classification.
            report.unclassified.append(disk_path)
            report.actions.append(
                SymlinkAction(
                    kind="unclassified",
                    link_path=canonical_root,
                    target_path=disk_path,
                    source=f"imported:{_origin_dir(disk_path, mount_root)}",
                    reason="not in registry; source dir is not a known v0.1.x layout",
                )
            )
            continue
        recipe, leaf_cap = leaf
        link_path = canonical_root / recipe / leaf_cap / disk_path.name
        if link_path in planned_links:
            continue
        action = _plan_one_link(
            link_path=link_path,
            target=disk_path,
            source=f"imported:{_origin_dir(disk_path, mount_root)}",
            force=force,
        )
        report.actions.append(action)
        planned_links.add(link_path)

    return report


def _origin_dir(disk_path: Path, mount_root: Path) -> str:
    """Return the top-level subdirectory under ``mount_root`` for reporting."""
    try:
        rel = disk_path.relative_to(mount_root)
    except ValueError:
        return str(disk_path)
    parts = rel.parts
    return parts[0] if parts else str(disk_path)


def _plan_one_link(
    *,
    link_path: Path,
    target: Path,
    source: str,
    force: bool,
) -> SymlinkAction:
    """Inspect the filesystem and decide what action ``link_path`` needs."""
    if link_path.is_symlink():
        try:
            current = Path(os.readlink(link_path))
        except OSError as exc:
            return SymlinkAction(
                kind="would-overwrite",
                link_path=link_path,
                target_path=target,
                source=source,
                reason=f"readlink failed: {exc}",
            )
        if current == target:
            return SymlinkAction(
                kind="skip-exists",
                link_path=link_path,
                target_path=target,
                source=source,
                reason="symlink already points at the canonical target",
            )
        if force:
            return SymlinkAction(
                kind="overwrite",
                link_path=link_path,
                target_path=target,
                source=source,
                reason=f"existing symlink → {current}; --force overwrite",
            )
        return SymlinkAction(
            kind="would-overwrite",
            link_path=link_path,
            target_path=target,
            source=source,
            reason=f"refusing to overwrite existing symlink → {current}; pass --force",
        )
    if link_path.exists():
        # A real file or directory sits where we'd plant the symlink.
        # Refuse regardless of --force — that's not a symlink we own.
        return SymlinkAction(
            kind="would-overwrite",
            link_path=link_path,
            target_path=target,
            source=source,
            reason="a non-symlink file/dir already occupies this path; refusing",
        )
    return SymlinkAction(
        kind="create",
        link_path=link_path,
        target_path=target,
        source=source,
    )


# ── Apply step ─────────────────────────────────────────────────────────────────


def _safe_canonical_root(canonical_root: Path) -> None:
    """Refuse to operate on a bind mount / symlinked canonical root.

    Touching a symlinked or bind-mounted target would scatter the
    migration into an unexpected place. The script is opinionated: the
    canonical root must be a plain directory (or absent — we'll mkdir).
    """
    if canonical_root.is_symlink():
        raise typer.BadParameter(
            f"{canonical_root} is a symlink; refusing to migrate. "
            f"Move or recreate it as a plain directory first.",
            param_hint="--canonical-root",
        )
    if not canonical_root.exists():
        return
    # Best-effort bind-mount check: a bind mount has a different st_dev
    # from its parent. Skip the check on systems where the parent is on
    # the same mount as us — that's the common case.
    try:
        own = canonical_root.stat().st_dev
        parent = canonical_root.parent.stat().st_dev
    except OSError:
        return
    if own != parent:
        raise typer.BadParameter(
            f"{canonical_root} appears to be a bind mount (different st_dev "
            f"from its parent). Refusing to migrate. Unmount or pick a "
            f"different canonical root.",
            param_hint="--canonical-root",
        )


def _ensure_canonical_dirs(canonical_root: Path) -> None:
    """``mkdir -p`` every canonical leaf so the symlink writes have a target."""
    for recipe, capability in CANONICAL_LEAVES:
        (canonical_root / recipe / capability).mkdir(parents=True, exist_ok=True)


def _atomic_symlink(link_path: Path, target: Path) -> None:
    """Write ``link_path → target`` via a tempfile + ``os.replace``.

    Mirrors the atomic-write pattern in
    ``hal0.lemonade.server_models_gen.write_server_models``: a partial /
    crashed run never leaves a half-formed symlink at the canonical
    location.
    """
    link_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{link_path.name}.",
        suffix=".lnk.tmp",
        dir=link_path.parent,
    )
    # We don't need the fd — the tempfile is just for a unique name.
    os.close(fd)
    tmp_path = Path(tmp_str)
    try:
        # ``mkstemp`` actually created a file at the path; remove it so
        # ``os.symlink`` can take the name.
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        os.symlink(target, tmp_path)
        os.replace(tmp_path, link_path)
        tmp_path = None  # type: ignore[assignment]
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def execute_plan(report: MigrationReport) -> list[SymlinkAction]:
    """Realise the planned actions on disk. Returns the applied actions.

    Skips ``skip-exists`` (already correct) and ``would-overwrite`` /
    ``unclassified`` (the planner already decided not to touch those).
    """
    applied: list[SymlinkAction] = []
    for action in report.actions:
        if action.kind not in ("create", "overwrite"):
            continue
        assert action.target_path is not None, "create/overwrite require a target"
        _atomic_symlink(action.link_path, action.target_path)
        applied.append(action)
    return applied


# ── CLI command ────────────────────────────────────────────────────────────────


@app.command("model-layout")
def model_layout(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the migration. Without this, the command is a dry-run.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite canonical symlinks that already point elsewhere.",
    ),
    registry_path: Path = typer.Option(
        DEFAULT_REGISTRY_PATH,
        "--registry",
        help="Path to hal0 registry.toml.",
    ),
    mount_root: Path = typer.Option(
        DEFAULT_MOUNT_ROOT,
        "--mount-root",
        help="Disk-backed model store root (the v0.1.x layout source).",
    ),
    canonical_root: Path = typer.Option(
        DEFAULT_CANONICAL_ROOT,
        "--canonical-root",
        help="Canonical <recipe>/<capability>/ tree root (the symlink target).",
    ),
) -> None:
    """Reorganise the model store into the v0.2 canonical layout.

    Reads the v0.1.x layout under ``--mount-root`` and produces a
    per-leaf symlink farm under ``--canonical-root`` pointing at it.
    Classification is driven by ``--registry`` first; on-disk entries
    not in the registry are placed by source-directory convention (the
    v0.1.x layout described in the ``hal0_model_store_layout`` memory)
    and reported as warnings for manual review.

    The actual model files are NEVER moved or copied — v0.1.x slots
    keep working off the disk paths they already know.

    Default is dry-run: pass ``--apply`` to write. Idempotent on
    repeat: re-running ``--apply`` after a successful run is a no-op.
    """
    _safe_canonical_root(canonical_root)

    report = plan_migration(
        registry_path=registry_path,
        mount_root=mount_root,
        canonical_root=canonical_root,
        force=force,
    )

    # Summary table.
    table = Table(title=f"model-layout migration ({'apply' if apply else 'dry-run'})")
    table.add_column("action", style="bold")
    table.add_column("link", overflow="fold")
    table.add_column("target", overflow="fold")
    table.add_column("source")
    table.add_column("reason", overflow="fold")

    by_kind: dict[str, int] = {}
    for action in report.actions:
        by_kind[action.kind] = by_kind.get(action.kind, 0) + 1
        table.add_row(
            action.kind,
            str(action.link_path),
            str(action.target_path) if action.target_path else "—",
            action.source,
            action.reason,
        )

    if report.actions:
        console.print(table)
    else:
        console.print(f"[green]nothing to do[/green] — no models found under {mount_root}.")

    # Per-leaf rollup.
    leaf_counts = report.by_leaf()
    if leaf_counts:
        leaf_table = Table(title="planned by leaf")
        leaf_table.add_column("recipe")
        leaf_table.add_column("capability")
        leaf_table.add_column("count", justify="right")
        for (recipe, cap), count in sorted(leaf_counts.items()):
            leaf_table.add_row(recipe, cap, str(count))
        console.print(leaf_table)

    # Refusal handling — if any would-overwrite remained, exit non-zero so
    # CI / scripts notice. Unclassifieds are warnings, not errors.
    blocked = by_kind.get("would-overwrite", 0)
    if blocked:
        console.print(
            f"\n[yellow]warning[/yellow]: {blocked} action(s) blocked; "
            f"pass --force to overwrite differing symlinks (will NOT clobber "
            f"non-symlink files)."
        )

    if not apply:
        creates = by_kind.get("create", 0)
        console.print(
            f"\n[yellow]--dry-run[/yellow] — would create {creates} symlink(s); "
            f"pass --apply to write."
        )
        # Dry-run is informational only; do not exit non-zero on blocked.
        raise typer.Exit(0)

    # ── Apply ────────────────────────────────────────────────────────────
    _ensure_canonical_dirs(canonical_root)
    applied = execute_plan(report)
    console.print(f"\n[green]applied[/green] {len(applied)} symlink(s) under {canonical_root}.")

    if report.unclassified:
        console.print(
            f"[yellow]warning[/yellow]: {len(report.unclassified)} on-disk model(s) "
            f"could not be classified automatically - see the table above for paths. "
            f"Register them with [bold]hal0 model register <id> --path <p>[/bold] "
            f"and re-run."
        )

    if blocked:
        # In --apply mode a blocked action is a real problem; surface it.
        raise typer.Exit(1)
