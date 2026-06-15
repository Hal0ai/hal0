"""CLI implementation for ``hal0 doctor``.

The default ``hal0 doctor`` invocation shells out to
``installer/lib/preflight.sh`` (the same script ``installer/install.sh``
sources for its pre-install checks) so the operator can re-run the full
preflight battery post-install without touching the installer.

Locating the script:

* ``HAL0_PREFLIGHT_SH`` env var wins, when set — useful for tests and
  for the eventual FHS install layout (``/opt/hal0/installer/lib/...``).
* Otherwise we walk up from this module's path to find a sibling
  ``installer/lib/preflight.sh``. ``install.sh`` does an editable
  ``pip install -e <repo>`` today, so ``Path(hal0.__file__).parents[2]``
  resolves to the repo root in every install.sh-produced environment.

The command preserves the script's exit code so it composes with other
shell tooling (``hal0 doctor && hal0 status``).

Sub-commands:

* ``hal0 doctor toolbox-pull`` — assert that every image pinned in
  ``manifest.json.toolbox_images`` is anonymously reachable on ghcr.io
  (issue tracker: task #25 / harness FINDINGS §8).
"""

from __future__ import annotations

import grp
import os
import pwd
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

import hal0
from hal0.config.loader import load_manifest

app = typer.Typer(
    name="doctor",
    help="Re-run the installer's pre-flight checks against the live host.",
    no_args_is_help=False,
)

console = Console()


# OCI manifest media types accepted by ghcr.io. We list the OCI image
# index + the Docker manifest list first because every toolbox image is
# multi-arch (amd64 today; arm64 once Strix Halo arm spins up). The
# single-arch fallbacks are kept so the probe still resolves single-arch
# tags users may push manually.
_OCI_MANIFEST_ACCEPT = ",".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def _locate_preflight() -> Path | None:
    """Find ``installer/lib/preflight.sh`` for the current install.

    Returns ``None`` when the script is missing — the caller surfaces a
    clear error rather than a confused subprocess failure. We check the
    explicit env-var first, then derive from the package location.
    """
    override = os.environ.get("HAL0_PREFLIGHT_SH", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None

    # In an editable install, ``hal0.__file__`` is
    # ``<repo>/src/hal0/__init__.py``; parents[2] is the repo root.
    # In a future wheel-style install the file may live under
    # ``site-packages/hal0/`` with no repo neighbours — at that point
    # the install layout will need to bundle ``installer/lib/`` and
    # set ``HAL0_PREFLIGHT_SH``.
    try:
        repo_root = Path(hal0.__file__).resolve().parents[2]
    except (AttributeError, IndexError):
        return None
    candidate = repo_root / "installer" / "lib" / "preflight.sh"
    return candidate if candidate.is_file() else None


@app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Force ASCII-only output (sets HAL0_PLAIN=1 for the child shell).",
    ),
    ports: str | None = typer.Option(
        None,
        "--ports",
        help="Space-separated TCP ports for the port collision check (default: '8080 3001').",
    ),
) -> None:
    """Re-run pre-flight checks (systemd, python, docker, disk, ports)."""
    # When a sub-command (e.g. ``toolbox-pull``) is invoked, Typer still
    # calls the callback first. Bail out without running preflight so the
    # sub-command handles the request on its own — preflight is the
    # "default" only when no sub-command is given.
    if ctx.invoked_subcommand is not None:
        return
    preflight = _locate_preflight()
    if preflight is None:
        console.print(
            "[red]✗[/red]  Could not locate installer/lib/preflight.sh.\n"
            "    Set HAL0_PREFLIGHT_SH=/path/to/preflight.sh or re-install"
            " from a repo checkout."
        )
        raise typer.Exit(2)

    bash = shutil.which("bash")
    if bash is None:
        console.print("[red]✗[/red]  bash not found on PATH — required to run preflight.sh")
        raise typer.Exit(2)

    env = os.environ.copy()
    if plain:
        env["HAL0_PLAIN"] = "1"
    if ports is not None:
        env["HAL0_DOCTOR_PORTS"] = ports

    try:
        result = subprocess.run(
            [bash, str(preflight)],
            env=env,
            check=False,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except OSError as exc:  # pragma: no cover — bash missing is caught above
        console.print(f"[red]✗[/red]  failed to exec bash: {exc}")
        raise typer.Exit(2) from exc

    # Preserve the script's exit code verbatim so chained shells see a
    # non-zero on the first failed check.
    raise typer.Exit(result.returncode)


# ── hal0 doctor toolbox-pull ──────────────────────────────────────────────────


def _parse_image_ref(tag: str) -> tuple[str, str, str] | None:
    """Split ``ghcr.io/<owner>/<image>:<tag>`` into ``(registry, repo, tag)``.

    Returns ``None`` when the ref doesn't look like a ghcr.io reference —
    callers surface that as a fail row rather than crashing. We
    deliberately only support ghcr.io for now; the toolbox contract is
    "public on ghcr.io/hal0ai/" and reaching other registries would
    require a different auth flow.
    """
    if not tag.startswith("ghcr.io/"):
        return None
    body = tag[len("ghcr.io/") :]
    # Split off the tag suffix (``:v1`` etc.). Digest refs (``@sha256:...``)
    # aren't valid here — the probe HEAD's the tag because the digest is
    # exactly what we're trying to discover.
    if ":" in body:
        repo, _, ref = body.rpartition(":")
    else:
        repo, ref = body, "latest"
    if not repo or "/" not in repo:
        return None
    return ("ghcr.io", repo, ref)


def _ghcr_anon_token(repo: str, *, client: httpx.Client) -> str:
    """Exchange anonymous credentials for a pull-scoped ghcr.io bearer.

    ghcr.io's token endpoint returns ``{"token": "..."}`` for any
    public package without authentication. We pass ``scope=
    repository:<repo>:pull`` so the token is narrowed to the one repo
    we're about to HEAD.
    """
    resp = client.get(
        "https://ghcr.io/token",
        params={"scope": f"repository:{repo}:pull"},
        timeout=10.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("token") or payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"ghcr.io token endpoint returned no token for {repo}")
    return token


def _ghcr_manifest_digest(
    repo: str,
    ref: str,
    *,
    token: str,
    client: httpx.Client,
) -> str:
    """HEAD the manifest URL and return the ``Docker-Content-Digest`` header.

    The header is the canonical content digest for the (possibly
    multi-arch) manifest the tag points at. We don't fetch the body —
    HEAD is enough to assert reachability + capture the digest, which is
    all the probe needs.
    """
    resp = client.request(
        "HEAD",
        f"https://ghcr.io/v2/{repo}/manifests/{ref}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": _OCI_MANIFEST_ACCEPT,
        },
        timeout=10.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} {resp.reason_phrase or ''}".strip())
    digest = resp.headers.get("docker-content-digest") or resp.headers.get("Docker-Content-Digest")
    if not digest:
        raise RuntimeError("manifest HEAD returned no Docker-Content-Digest header")
    return digest


def _probe_one(
    name: str,
    entry: dict[str, Any],
    *,
    client: httpx.Client,
) -> dict[str, Any]:
    """Probe one ``toolbox_images`` entry; never raises — returns a row dict.

    Row shape:
        {"name", "tag", "ok": bool, "digest": str | None,
         "pinned_digest": str | None, "matches_pin": bool | None,
         "error": str | None}

    Digest mismatch is surfaced via ``matches_pin``; it doesn't flip
    ``ok`` to False because reconciling drift is a separate step
    (scripts/update-toolbox-digests.sh, run before a release). The
    probe's job is just "reachable yes/no" + "here's what's actually
    there".
    """
    row: dict[str, Any] = {
        "name": name,
        "tag": entry.get("tag") or "",
        "ok": False,
        "digest": None,
        "pinned_digest": entry.get("digest") or None,
        "matches_pin": None,
        "error": None,
    }
    tag = entry.get("tag")
    if not isinstance(tag, str) or not tag:
        row["error"] = "manifest entry missing 'tag'"
        return row
    parsed = _parse_image_ref(tag)
    if parsed is None:
        row["error"] = f"unsupported registry in tag {tag!r} (only ghcr.io is probed)"
        return row
    _, repo, ref = parsed
    try:
        token = _ghcr_anon_token(repo, client=client)
        digest = _ghcr_manifest_digest(repo, ref, token=token, client=client)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    row["ok"] = True
    row["digest"] = digest
    if row["pinned_digest"]:
        row["matches_pin"] = row["pinned_digest"] == digest
    return row


@app.command("toolbox-pull")
def toolbox_pull(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a JSON list of probe rows instead of the human-readable table.",
    ),
    manifest_path: Path | None = typer.Option(
        None,
        "--manifest",
        help="Override manifest.json location (defaults to the loader's FHS-aware resolver).",
    ),
) -> None:
    """Verify each pinned toolbox image is anonymously pullable from ghcr.io.

    Walks ``manifest.json.toolbox_images`` and exercises the anonymous
    OCI v2 token-exchange + HEAD-manifest flow per image. Reports each
    image's reachable status and the actual ghcr.io digest seen
    alongside the pinned digest from the manifest.

    Exit codes:
      0 — every entry was reachable (digest drift is reported but does
          NOT fail; that's the manifest job's problem).
      1 — at least one image could not be reached.
      2 — manifest.json is empty or has no toolbox_images entries.
    """
    import json as jsonlib

    manifest = load_manifest(manifest_path) if manifest_path else load_manifest()
    images = manifest.get("toolbox_images") or {}
    if not isinstance(images, dict) or not images:
        console.print("[yellow]![/yellow]  manifest.json has no toolbox_images entries to probe.")
        raise typer.Exit(2)

    rows: list[dict[str, Any]] = []
    with httpx.Client(follow_redirects=True) as client:
        for name in sorted(images.keys()):
            entry = images[name]
            if not isinstance(entry, dict):
                rows.append(
                    {
                        "name": name,
                        "tag": "",
                        "ok": False,
                        "digest": None,
                        "pinned_digest": None,
                        "matches_pin": None,
                        "error": "manifest entry is not a dict",
                    }
                )
                continue
            rows.append(_probe_one(name, entry, client=client))

    if json_output:
        console.print_json(jsonlib.dumps(rows))
    else:
        table = Table(title="ghcr.io toolbox-image pull probe (anonymous)")
        table.add_column("Image", style="bold")
        table.add_column("Status")
        table.add_column("Digest (actual)")
        table.add_column("Pin")
        for row in rows:
            status = "[green]ok[/green]" if row["ok"] else f"[red]FAIL[/red] {row['error']}"
            digest = row["digest"] or "—"
            if row["matches_pin"] is True:
                pin = "[green]match[/green]"
            elif row["matches_pin"] is False:
                pin = "[yellow]drift[/yellow]"
            else:
                pin = "[dim]unpinned[/dim]"
            table.add_row(row["name"], status, digest, pin)
        console.print(table)

    failures = [r for r in rows if not r["ok"]]
    raise typer.Exit(1 if failures else 0)


# ── hal0 doctor perms — Hermes ownership drift (#843) ─────────────────────────
#
# Read-only audit for the root-clobber regression: when root runs Hermes it
# writes a split-brain /root/.hermes tree and/or leaves root:root files the
# User=hal0 unit can't read (so Hermes silently falls back to the default
# provider). This surfaces that loudly. It NEVER repairs — reconciliation is the
# explicit `sudo hal0 agent bootstrap hermes --repair` path.

_HERMES_HOME = Path("/var/lib/hal0/.hermes")
_HERMES_VENV = Path("/var/lib/hal0/venvs/hermes")
_STRAY_ROOT_HOME = Path("/root/.hermes")
_EXPECTED_OWNER = "hal0"


def check_hermes_ownership(
    *,
    expected_user: str = _EXPECTED_OWNER,
    hermes_home: Path = _HERMES_HOME,
    venv: Path = _HERMES_VENV,
    stray_home: Path = _STRAY_ROOT_HOME,
    owner_of: Callable[[Path], str | None],
    exists: Callable[[Path], bool],
) -> list[dict[str, str]]:
    """Audit Hermes runtime ownership; return rows ``{path,label,status,detail}``.

    ``status`` is ``ok`` (owned by ``expected_user``), ``drift`` (wrong owner, or
    a stray /root/.hermes), or ``absent`` (not present — not a problem).
    """
    rows: list[dict[str, str]] = []
    checks = (
        (hermes_home, "HERMES_HOME tree"),
        (hermes_home / "config.yaml", "config.yaml"),
        (hermes_home / "runtime.json", "runtime.json (embed token)"),
        (venv, "hermes venv"),
    )
    for path, label in checks:
        if not exists(path):
            rows.append(
                {"path": str(path), "label": label, "status": "absent", "detail": "not present"}
            )
            continue
        owner = owner_of(path)
        if owner == expected_user:
            rows.append(
                {"path": str(path), "label": label, "status": "ok", "detail": f"owned by {owner}"}
            )
        else:
            rows.append(
                {
                    "path": str(path),
                    "label": label,
                    "status": "drift",
                    "detail": f"owned by {owner or '?'} (expected {expected_user})",
                }
            )
    if exists(stray_home):
        rows.append(
            {
                "path": str(stray_home),
                "label": "split-brain /root/.hermes",
                "status": "drift",
                "detail": "root ran Hermes; remove after reconciling",
            }
        )
    return rows


def has_ownership_drift(rows: list[dict[str, str]]) -> bool:
    """True iff any row is in the ``drift`` state."""
    return any(r["status"] == "drift" for r in rows)


# ── editable-checkout group-share — the #843 root-clobber *fix* surface ───────
#
# Distinct from the Hermes-home audit above. When the install is an editable git
# checkout (e.g. /opt/hal0 on CT 105), every root-run deploy — `git reset --hard`
# + `npm build` — recreates each touched file as root:root 644, locking out the
# unprivileged `hal0` user that Hermes and the in-runtime agents execute as. A
# one-shot `chown` doesn't hold: the next deploy re-roots exactly the files it
# changed ("creep"). The durable cure is to make the tree group-shared
# (group=hal0, setgid dirs, g+w, core.sharedRepository=group) AND have writers
# use umask 002 (scripts/deploy.sh + the hal0-api unit). This block audits that
# model and, with --fix, repairs it in place — the easy path for an existing
# install that's already drifted.

_SHARED_GROUP = "hal0"
# Values git accepts for core.sharedRepository that grant group write.
_GIT_SHARED_OK = {"group", "true", "1", "all", "world", "everybody", "2"}


def detect_editable_root(start: Path) -> Path | None:
    """Nearest ancestor of ``start`` that is a git checkout (contains ``.git``),
    or ``None`` for an immutable FHS install (no ``.git`` — nothing to share)."""
    for p in (start, *start.parents):
        if (p / ".git").exists():
            return p
    return None


def _share_row(label: str, ok: bool, detail: str, path: str = "") -> dict[str, str]:
    return {"path": path, "label": label, "status": "ok" if ok else "drift", "detail": detail}


def check_tree_group_share(
    root: Path | None,
    *,
    group: str = _SHARED_GROUP,
    group_of: Callable[[Path], str | None],
    mode_of: Callable[[Path], int],
    git_shared_of: Callable[[Path], str | None],
) -> list[dict[str, str]]:
    """Audit whether an editable checkout is group-shared with ``group``.

    Returns rows ``{path,label,status,detail}`` using the same vocabulary as
    :func:`check_hermes_ownership` (``ok`` / ``drift`` / ``absent``). When
    ``root`` is ``None`` the single row is ``absent`` — an immutable FHS install
    has no editable tree to share, which is correct, not a problem. The stat and
    git lookups are injected seams so the logic is testable without a real tree.
    """
    if root is None:
        return [
            {
                "path": "",
                "label": "editable checkout",
                "status": "absent",
                "detail": "no .git (immutable FHS install) — nothing to share",
            }
        ]
    p = str(root)
    grp_name = group_of(root)
    mode = mode_of(root)
    shared = git_shared_of(root)
    return [
        _share_row(
            f"tree group == {group}",
            grp_name == group,
            f"group is {grp_name or '?'}",
            p,
        ),
        _share_row(
            "tree group-writable",
            bool(mode & stat.S_IWGRP),
            "g+w set" if mode & stat.S_IWGRP else "missing g+w (root-run deploy locks out hal0)",
            p,
        ),
        _share_row(
            "dirs setgid (new files inherit group)",
            bool(mode & stat.S_ISGID),
            "setgid set" if mode & stat.S_ISGID else "missing setgid",
            p,
        ),
        _share_row(
            "git core.sharedRepository",
            (shared or "").lower() in _GIT_SHARED_OK,
            f"= {shared or 'unset'}",
            p,
        ),
    ]


def repair_tree_group_share(
    root: Path,
    group: str = _SHARED_GROUP,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, str]:
    """Apply the group-shared model to ``root`` in place (needs privilege to
    chgrp). Idempotent: group→``group``, g+w, setgid on every dir, and
    ``core.sharedRepository=group`` so git preserves the share across future
    resets. Returns ``(ok, message)``; the first failing step short-circuits."""
    steps = (
        (["chgrp", "-R", group, str(root)], "chgrp"),
        # g+rwX: exec only on dirs / already-exec files, so group members can
        # traverse without flagging every source file executable.
        (["chmod", "-R", "g+rwX", str(root)], "chmod g+rwX"),
        (["find", str(root), "-type", "d", "-exec", "chmod", "g+s", "{}", "+"], "setgid dirs"),
        (
            ["git", "-C", str(root), "config", "core.sharedRepository", "group"],
            "git core.sharedRepository",
        ),
    )
    for argv, label in steps:
        proc = run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            return False, f"{label} failed: {detail}"
    return True, f"group-shared perms applied to {root} (group={group}, setgid, g+w)"


def _render_audit(title: str, rows: list[dict[str, str]]) -> None:
    badge = {
        "ok": "[green]ok[/green]",
        "drift": "[red]DRIFT[/red]",
        "absent": "[dim]absent[/dim]",
    }
    table = Table(title=title)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail")
    for r in rows:
        table.add_row(r["label"], badge[r["status"]], r["detail"])
    console.print(table)


@app.command("perms")
def perms(
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Repair editable-checkout group-share drift in place (needs root).",
    ),
) -> None:
    """Audit ownership for the root-clobber regression (#843).

    Covers two surfaces: Hermes runtime state (/var/lib/hal0/.hermes) and the
    editable code checkout's group-share. ``--fix`` repairs the latter in place
    (the easy path for an already-drifted install); Hermes drift is still
    reconciled via ``sudo hal0 agent bootstrap hermes --repair``.
    """

    def _owner(p: Path) -> str | None:
        try:
            return pwd.getpwuid(p.stat().st_uid).pw_name
        except (OSError, KeyError):
            return None

    def _group(p: Path) -> str | None:
        try:
            return grp.getgrgid(p.stat().st_gid).gr_name
        except (OSError, KeyError):
            return None

    def _mode(p: Path) -> int:
        try:
            return p.stat().st_mode
        except OSError:
            return 0

    def _git_shared(p: Path) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(p), "config", "--get", "core.sharedRepository"],
                capture_output=True,
                text=True,
            )
        except OSError:
            return None
        return (proc.stdout.strip() or None) if proc.returncode == 0 else None

    # 1) Hermes runtime ownership (read-only; repair via bootstrap --repair).
    hermes_rows = check_hermes_ownership(owner_of=_owner, exists=lambda p: p.exists())
    _render_audit("Hermes ownership audit (#843)", hermes_rows)

    # 2) Editable-checkout group-share (read-only audit; --fix repairs).
    root = detect_editable_root(Path(hal0.__file__).resolve())
    tree_rows = check_tree_group_share(
        root,
        group=_SHARED_GROUP,
        group_of=_group,
        mode_of=_mode,
        git_shared_of=_git_shared,
    )
    _render_audit("Editable checkout group-share (#843)", tree_rows)
    tree_drift = has_ownership_drift(tree_rows)

    if fix:
        if root is None:
            console.print("[dim]nothing to fix — not an editable checkout.[/dim]")
        elif not tree_drift:
            console.print("[green]✓[/green]  group-share already clean — nothing to fix.")
        elif os.geteuid() != 0:
            console.print("[red]✗[/red]  --fix needs root — re-run `sudo hal0 doctor perms --fix`.")
            raise typer.Exit(1)
        else:
            ok, msg = repair_tree_group_share(root, _SHARED_GROUP)
            if not ok:
                console.print(f"[red]✗[/red]  repair failed: {msg}")
                raise typer.Exit(1)
            console.print(f"[green]✓[/green]  {msg}")
            tree_drift = False

    hermes_drift = has_ownership_drift(hermes_rows)
    if hermes_drift:
        console.print(
            "[red]✗[/red]  Hermes ownership drift — run "
            "`sudo hal0 agent bootstrap hermes --repair` to reconcile."
        )
    if tree_drift and not fix:
        console.print(
            "[yellow]![/yellow]  editable-checkout group-share drift — run "
            "`sudo hal0 doctor perms --fix` to repair."
        )
    if hermes_drift or tree_drift:
        raise typer.Exit(1)
    console.print("[green]✓[/green]  ownership clean.")
    raise typer.Exit(0)
