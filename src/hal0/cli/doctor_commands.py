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

import contextlib
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
    verify: bool = typer.Option(
        False,
        "--verify",
        help="Render the post-setup report card (checks + live URLs + doc links) against the live API.",
    ),
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
    """Re-run pre-flight checks (systemd, python, docker, disk, ports).

    ``--verify`` instead renders the WS-K report card: a pass/warn/fail summary
    over the live health seams (API, runners, capability slots, hindsight,
    OpenWebUI, Hermes) plus the computed URLs + help links. Non-blocking; exits
    2 only on a critical (no reachable URL / zero healthy runners).
    """
    # When a sub-command (e.g. ``toolbox-pull``) is invoked, Typer still
    # calls the callback first. Bail out without running preflight so the
    # sub-command handles the request on its own — preflight is the
    # "default" only when no sub-command is given.
    if ctx.invoked_subcommand is not None:
        return
    if verify:
        from hal0.cli.doctor_verify import run_verify

        raise typer.Exit(run_verify(console=console))
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
    """Audit ownership for the root-clobber regression (#843) + the path table.

    Covers three surfaces: Hermes runtime state (/var/lib/hal0/.hermes), the
    editable code checkout's group-share, and the canonical path-ownership table
    (:mod:`hal0.install.perms`, overhaul plan §5). ``--fix`` repairs the
    group-share in place AND applies the ownership table (both need root); Hermes
    drift is still reconciled via ``sudo hal0 agent bootstrap hermes --repair``.
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

    # 3) Canonical path-ownership table (read-only audit; --fix applies it).
    # Phase 0: the table encodes current root-era values, so a freshly-installed
    # box shows no drift here. Honest drift surfaces an actual ownership skew.
    from hal0.install import perms as perms_mod

    own_plan = perms_mod.plan()
    own_rows = perms_mod.audit_rows(own_plan)
    _render_audit("Path ownership table (overhaul plan §5)", own_rows)
    own_drift = has_ownership_drift(own_rows)

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

        # Apply the ownership table (root-gated; atomic with rollback).
        if own_drift:
            if os.geteuid() != 0:
                console.print(
                    "[red]✗[/red]  --fix needs root for ownership repair — "
                    "re-run `sudo hal0 doctor perms --fix`."
                )
                raise typer.Exit(1)
            try:
                changed = perms_mod.commit(own_plan)
            except (OSError, KeyError) as exc:
                console.print(f"[red]✗[/red]  ownership repair failed: {exc}")
                raise typer.Exit(1) from exc
            console.print(
                f"[green]✓[/green]  ownership table applied ({len(changed)} path(s) reconciled)."
            )
            own_drift = False

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
    if own_drift and not fix:
        console.print(
            "[yellow]![/yellow]  path-ownership drift — run "
            "`sudo hal0 doctor perms --fix` to reconcile against the table."
        )
    if hermes_drift or tree_drift or own_drift:
        raise typer.Exit(1)
    console.print("[green]✓[/green]  ownership clean.")
    raise typer.Exit(0)


# ── hal0 doctor models — FLM (NPU) store pure helpers ─────────────────────────
#
# The FLM store is the single most reboot-fragile surface on the box: the NPU
# slot bind-mounts it, and a missing / non-writable / not-yet-mounted source
# makes podman exit 125 ("statfs ... no such file or directory") — a silent,
# post-reboot slot death. These three pure classifiers cover the incident modes
# the old inline check missed, and feed a root-gated ``--fix``.

# uid the FLM toolbox container runs as — fixed by the image (mirror of
# hal0.providers.flm._FLM_CONTAINER_UID; kept local to avoid importing the
# provider, which pulls in podman spec machinery, into the CLI path).
_FLM_CONTAINER_UID = 1000
# Path prefixes that almost always denote an external mount (an off-root
# filesystem). A store here that isn't backed by a live mount is the classic
# "NPU slot exits 125 after reboot because /mnt wasn't up yet" trap.
_EXTERNAL_MOUNT_PREFIXES = ("/mnt/", "/srv/", "/media/")


def flm_store_divergence(env_val: str | None, toml_val: str | None) -> dict[str, str] | None:
    """Warn when the env var and the TOML field name *different* FLM stores.

    ``HAL0_FLM_MODELS_DIR`` wins over ``[models].flm_store`` (see
    :func:`hal0.config.paths.flm_models_dir`), so an operator who relocates the
    store by editing hal0.toml while a stale env var is still exported gets a
    silently-ignored edit — pulls land in one dir, the slot mounts the other,
    and every model reports installed=False. Returns a row only on genuine
    disagreement; equal values or either side unset is fine.
    """
    env = (env_val or "").strip().rstrip("/")
    toml = (toml_val or "").strip().rstrip("/")
    if env and toml and env != toml:
        return {
            "status": "warn",
            "detail": (
                f"HAL0_FLM_MODELS_DIR={env} overrides [models].flm_store={toml} — "
                f"the env var wins; unset it or align the TOML to avoid a split store."
            ),
        }
    return None


def _deepest_mountpoint(path: Path, *, ismount: Callable[[str], bool]) -> str:
    """Return the deepest ancestor of ``path`` (incl. itself) that is a mountpoint.

    Falls back to ``/`` which is always a mountpoint — so a store sitting on the
    root filesystem resolves here to ``/``.
    """
    for cand in (path, *path.parents):
        if ismount(str(cand)):
            return str(cand)
    return "/"


def flm_mount_guard(
    store: Path,
    *,
    ismount: Callable[[str], bool] = os.path.ismount,
) -> dict[str, str] | None:
    """Warn when the store lives under an external mount prefix that isn't mounted.

    Matches the documented reboot failure: the store is configured under
    ``/mnt/...`` (an off-root disk) but nothing along that path is a live
    mountpoint, so the directory is either absent or an empty stub on the root
    filesystem. At the next NPU slot start podman bind-mounts a phantom source
    and dies with exit 125. Advisory (warn), because a deliberate on-root
    ``/mnt`` dir is legal, just unusual.
    """
    s = str(store).rstrip("/")
    if not any(s.startswith(p) for p in _EXTERNAL_MOUNT_PREFIXES):
        return None
    if _deepest_mountpoint(store, ismount=ismount) != "/":
        return None  # a real mount backs the path — all good
    return {
        "status": "warn",
        "detail": (
            f"{s} is under an external mount path that is not mounted — the NPU slot "
            f"will exit 125 at boot until it is up. Order the slot after the mount "
            f"(systemd RequiresMountsFor=) or move the store onto the root fs."
        ),
    }


def flm_store_writability(
    store: Path,
    *,
    stat_of: Callable[[Path], os.stat_result],
) -> dict[str, object] | None:
    """Classify FLM-store writability for the container uid; ``None`` when fine.

    The container runs as uid 1000 (image-fixed, not the host hal0 uid). The
    store is writable for it when owned by 1000 *or* group/other-writable. When
    neither holds, returns a row carrying the repair target so ``--fix`` can
    apply ``chown 1000:hal0`` + ``chmod 2775`` without re-deriving it.
    """
    st = stat_of(store)
    world_or_group_w = bool(st.st_mode & 0o020) or bool(st.st_mode & 0o002)
    if st.st_uid == _FLM_CONTAINER_UID or world_or_group_w:
        return None
    return {
        "status": "fail",
        "uid": st.st_uid,
        "mode": st.st_mode & 0o7777,
        "detail": (
            f"not writable by the FLM container uid ({_FLM_CONTAINER_UID}): "
            f"owner uid {st.st_uid}, mode {oct(st.st_mode & 0o7777)}"
        ),
    }


def repair_flm_store(
    store: Path,
    *,
    group: str = "hal0",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, str]:
    """Apply ``chown {uid}:{group}`` + ``chmod 2775`` to the FLM store (needs root).

    Idempotent and matches the guidance the audit prints. Returns ``(ok, msg)``;
    the first failing step short-circuits with its stderr.
    """
    steps = (
        (["chown", f"{_FLM_CONTAINER_UID}:{group}", str(store)], "chown"),
        (["chmod", "2775", str(store)], "chmod 2775"),
    )
    for argv, label in steps:
        proc = run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            return False, f"{label} failed: {detail}"
    return True, f"FLM store {store} → uid {_FLM_CONTAINER_UID}:{group} mode 2775"


# ── hal0 doctor models ────────────────────────────────────────────────────────


@app.command("models")
def doctor_models(
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Repair FLM store ownership/mode drift in place (needs root).",
    ),
) -> None:
    """Audit the model pipeline: registry paths, store/roots agreement, FLM dir.

    Catches the classic install failures in one pass:

      * registry entries whose file no longer exists (slot dies with
        "gguf_init_from_file: ... No such file or directory")
      * model files on disk in the store that were never registered
        (pull_root/store not scanned on older installs)
      * an FLM (NPU) store dir that is missing or not writable by the
        container uid (podman exit 125 statfs after a reboot)
      * an FLM store split between HAL0_FLM_MODELS_DIR and [models].flm_store
        (the env var silently wins — pulls and the slot mount diverge)
      * an FLM store under an external mount (/mnt/...) that isn't mounted
        (the classic post-reboot exit-125 before the disk is up)

    ``--fix`` repairs FLM store ownership/mode drift in place (chown 1000:hal0,
    chmod 2775 — needs root). Exits non-zero when anything actionable is found.
    """
    from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get
    from hal0.config import paths as cfg_paths
    from hal0.config.loader import load_hal0_config

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    problems = 0

    # 1. Registry entries → file existence.
    try:
        data = api_get("/api/models")
    except CliApiError as exc:
        console.print(f"[red]✗[/red]  cannot list models: {exc}")
        raise typer.Exit(2) from exc
    rows = data.get("models", data) if isinstance(data, dict) else data
    local = [m for m in rows if isinstance(m, dict) and m.get("path")]
    dangling = [m for m in local if not Path(str(m["path"])).exists()]
    if dangling:
        problems += len(dangling)
        console.print(f"[red]✗[/red]  {len(dangling)} registry entr(y/ies) point at missing files:")
        for m in dangling:
            console.print(f"      {m.get('id')} → {m.get('path')}")
        console.print(
            "      Fix: hal0 model rm <id> && hal0 model scan   (re-register from disk)\n"
            "      or point the store at the real location: hal0 model store <dir>"
        )
    else:
        console.print(f"[green]✓[/green]  all {len(local)} registered model file(s) exist on disk.")

    # 2. Store/roots agreement + unregistered files in the store.
    try:
        cfg = load_hal0_config()
        scan_roots = cfg.models.scan_roots()
        effective = cfg.models.effective_store()
        exts = {e.lower() for e in cfg.models.file_extensions}
    except Exception as exc:  # config unreadable — report, keep going
        console.print(f"[yellow]![/yellow]  could not read hal0.toml: {exc}")
        scan_roots, effective, exts = [], "", {".gguf", ".safetensors"}
    if effective:
        registered_paths = {str(m.get("path")) for m in local}
        store_dir = Path(effective)
        unregistered: list[str] = []
        if store_dir.is_dir():
            for f in store_dir.rglob("*"):
                if (
                    f.is_file()
                    and f.suffix.lower() in exts
                    and not f.name.startswith(".")
                    and str(f) not in registered_paths
                ):
                    unregistered.append(str(f))
        else:
            problems += 1
            console.print(f"[red]✗[/red]  effective store {effective} does not exist.")
        if unregistered:
            problems += 1
            console.print(
                f"[yellow]![/yellow]  {len(unregistered)} model file(s) in the store are "
                f"not registered — run: hal0 model scan"
            )
            for f in unregistered[:10]:
                console.print(f"      {f}")
        console.print(f"[dim]store: {effective}  ·  scan roots: {', '.join(scan_roots)}[/dim]")

    # 3a. FLM store divergence: env var silently overriding the TOML field.
    env_flm = os.environ.get("HAL0_FLM_MODELS_DIR")
    toml_flm = None
    with contextlib.suppress(Exception):  # already surfaced under step 2's hal0.toml read
        toml_flm = load_hal0_config().models.flm_store
    divergence = flm_store_divergence(env_flm, toml_flm)
    if divergence:
        problems += 1
        console.print(f"[yellow]![/yellow]  {divergence['detail']}")

    # 3b. FLM (NPU) store: mount-backed, exists, writable by the container uid.
    flm_dir = Path(cfg_paths.flm_models_dir())
    mount_warn = flm_mount_guard(flm_dir)
    if mount_warn:
        problems += 1
        console.print(f"[yellow]![/yellow]  {mount_warn['detail']}")

    if flm_dir.exists():
        writ = flm_store_writability(flm_dir, stat_of=lambda p: p.stat())
        if writ is None:
            console.print(f"[green]✓[/green]  FLM store {flm_dir} present.")
        elif fix:
            if os.geteuid() != 0:
                problems += 1
                console.print(
                    f"[red]✗[/red]  FLM store {flm_dir}: {writ['detail']}\n"
                    "      --fix needs root — re-run `sudo hal0 doctor models --fix`."
                )
            else:
                ok, msg = repair_flm_store(flm_dir)
                if ok:
                    console.print(f"[green]✓[/green]  repaired {msg}")
                else:
                    problems += 1
                    console.print(f"[red]✗[/red]  repair failed: {msg}")
        else:
            problems += 1
            console.print(
                f"[yellow]![/yellow]  FLM store {flm_dir} {writ['detail']}.\n"
                "      Fix: sudo hal0 doctor models --fix   "
                f"(chown {_FLM_CONTAINER_UID}:hal0 + chmod 2775)"
            )
    elif not mount_warn:
        console.print(
            f"[dim]FLM store {flm_dir} absent (fine unless you use the NPU slot — "
            f"it is created on the next NPU slot start).[/dim]"
        )

    raise typer.Exit(1 if problems else 0)


# ── hal0 doctor migrations ────────────────────────────────────────────────────


def pending_layout_migration() -> tuple[int, int] | None:
    """Dry-run the v0.1→v0.2 model-layout migration; return ``(create, overwrite)``.

    Returns the count of symlinks the migration *would* write (``create`` +
    ``would-overwrite`` kinds), or ``None`` when the migration machinery can't be
    consulted at all (e.g. running from a build with no migrate module). A box
    that is already on the canonical layout — or a fresh install with no v0.1.x
    store — yields ``(0, 0)``: nothing pending. Never raises; a missing store or
    registry degrades to an empty plan inside ``plan_migration``.
    """
    try:
        from hal0.cli.migrate_commands import (
            DEFAULT_CANONICAL_ROOT,
            DEFAULT_MOUNT_ROOT,
            DEFAULT_REGISTRY_PATH,
            plan_migration,
        )
    except ImportError:
        return None
    try:
        report = plan_migration(
            registry_path=DEFAULT_REGISTRY_PATH,
            mount_root=DEFAULT_MOUNT_ROOT,
            canonical_root=DEFAULT_CANONICAL_ROOT,
            force=False,
        )
    except Exception:
        return None
    create = sum(1 for a in report.actions if a.kind == "create")
    overwrite = sum(1 for a in report.actions if a.kind == "would-overwrite")
    return (create, overwrite)


@app.command("migrations")
def doctor_migrations() -> None:
    """Surface a pending v0.1→v0.2 model-layout migration (read-only).

    The canonical ``<recipe>/<capability>/`` symlink farm is populated by
    ``hal0 migrate model-layout --apply``, but nothing tells an upgrading
    operator it's outstanding — so slots that expect the canonical paths quietly
    find nothing. This dry-runs the same planner and reports how many links are
    missing, pointing at the apply command. It never writes.

    Exit codes:
      0 — up to date (or nothing to migrate).
      1 — links are pending (advisory; run the apply command to reconcile).
    """
    pending = pending_layout_migration()
    if pending is None:
        console.print("[dim]model-layout migration: planner unavailable — skipped.[/dim]")
        raise typer.Exit(0)
    create, overwrite = pending
    if not create and not overwrite:
        console.print("[green]✓[/green]  model layout is current — no migration pending.")
        raise typer.Exit(0)
    detail = f"{create} link(s) to create"
    if overwrite:
        detail += f", {overwrite} to overwrite (needs --force)"
    console.print(
        f"[yellow]![/yellow]  model-layout migration pending: {detail}.\n"
        "      Preview: hal0 migrate model-layout        (dry-run)\n"
        "      Apply:   hal0 migrate model-layout --apply"
    )
    raise typer.Exit(1)


# ── hal0 doctor profiles — the slot↔profile referential + fitness layer ────────
#
# Profiles sit between a slot and the runtime image/backend. Nothing validates
# that layer until a slot actually *starts* (resolve_slot_profile raises
# KeyError late, on the start path), so a dangling reference or an un-pulled
# image is invisible to an operator staring at a "degraded" slot. These pure
# classifiers surface it up front. Check 1 (broken refs) is the hard failure;
# check 2 (image-present) is advisory.


def check_slot_profile_refs(
    slot_profiles: list[tuple[str, str | None]],
    valid_names: set[str],
) -> list[dict[str, str]]:
    """Flag slots whose ``profile = "..."`` names a profile not in the catalog.

    ``resolve_slot_profile`` raises ``KeyError`` for a missing name only when the
    slot starts — so a renamed/deleted profile is a latent slot-start failure.
    A slot with ``profile = None`` (base-image resolution) is legal and skipped.
    Returns one row per slot: ``ok`` when the reference resolves, ``drift`` when
    it dangles.
    """
    rows: list[dict[str, str]] = []
    for slot, profile in slot_profiles:
        if not profile:
            continue  # base-image slot — no profile to resolve
        if profile in valid_names:
            rows.append(
                {"label": slot, "status": "ok", "detail": f"→ {profile}"},
            )
        else:
            rows.append(
                {
                    "label": slot,
                    "status": "drift",
                    "detail": (
                        f"references missing profile {profile!r} — the slot will fail "
                        f"to start (KeyError). Repoint it: hal0 slot edit {slot} "
                        f"--profile <name>, or recreate {profile!r}."
                    ),
                },
            )
    return rows


def _image_repo(ref: str) -> str:
    """Strip the tag/digest → the bare ``registry/repo`` of an image ref."""
    body = ref.split("@", 1)[0]  # drop @sha256:... digest
    # A ':' after the last '/' is a tag; a ':' inside the host part is a port.
    head, _, tail = body.rpartition("/")
    tail = tail.split(":", 1)[0]
    return f"{head}/{tail}" if head else tail


def check_profile_images_present(
    profiles: list[Any],
    local_repos: set[str] | None,
) -> list[dict[str, str]]:
    """Warn when an *in-use* profile's image repo isn't present locally.

    ``local_repos`` is the set of ``registry/repo`` strings from ``podman
    images`` (tag-insensitive to avoid false alarms on a re-pinned tag), or
    ``None`` when podman couldn't be queried — in which case the whole check is
    skipped (no rows). Only profiles referenced by a slot are checked: an unused
    profile whose image was never pulled is not a live problem.
    """
    if local_repos is None:
        return []
    rows: list[dict[str, str]] = []
    for p in profiles:
        if not getattr(p, "used_by", ()) or not getattr(p, "image", ""):
            continue
        repo = _image_repo(p.image)
        if repo in local_repos:
            rows.append({"label": p.name, "status": "ok", "detail": f"image {repo} present"})
        else:
            rows.append(
                {
                    "label": p.name,
                    "status": "warn",
                    "detail": (
                        f"image repo {repo} not pulled (used by "
                        f"{', '.join(p.used_by)}) — first slot start will pull it, "
                        f"or pre-pull: podman pull {p.image}"
                    ),
                },
            )
    return rows


def _local_image_repos() -> set[str] | None:
    """Query ``podman images`` for the set of local ``registry/repo`` strings.

    Returns ``None`` (→ image check skipped) when podman is absent or errors, so
    a box without podman never gets spurious "not pulled" rows.
    """
    podman = shutil.which("podman")
    if podman is None:
        return None
    try:
        proc = subprocess.run(
            [podman, "images", "--format", "{{.Repository}}"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return {line.strip() for line in proc.stdout.splitlines() if line.strip() and line != "<none>"}


def _render_profiles(title: str, rows: list[dict[str, str]]) -> None:
    """Print profile audit rows with an ok/warn/drift badge, drift/warn detailed."""
    badge = {
        "ok": "[green]ok[/green]",
        "warn": "[yellow]warn[/yellow]",
        "drift": "[red]DRIFT[/red]",
    }
    console.print(f"\n[bold]{title}[/bold]")
    if not rows:
        console.print("  [dim](nothing to check)[/dim]")
        return
    for r in rows:
        # ``ok`` rows stay terse; warn/drift carry the actionable detail.
        if r["status"] == "ok":
            console.print(f"  {badge['ok']}  {r['label']}  [dim]{r['detail']}[/dim]")
        else:
            console.print(f"  {badge[r['status']]}  {r['label']} — {r['detail']}")


@app.command("profiles")
def doctor_profiles() -> None:
    """Audit the slot↔profile layer: dangling references + un-pulled images.

    Two checks, surfaced before a slot has to fail on start:

      * broken refs — a slot's ``profile = "..."`` names a profile that no longer
        exists (the slot dies with KeyError at start). This is the only failing
        check.
      * image present — an in-use profile's toolbox image isn't pulled locally
        (advisory; the first slot start pulls it).

    Exit codes:
      0 — no broken references (advisory warnings may still be printed).
      1 — at least one slot references a missing profile.
    """
    try:
        from hal0.config.loader import list_slots, load_slot_config
        from hal0.profiles import ProfileCatalog
    except ImportError as exc:  # pragma: no cover — profile layer always present
        console.print(f"[red]✗[/red]  profile layer unavailable: {exc}")
        raise typer.Exit(2) from exc

    catalog = ProfileCatalog()
    try:
        profiles = catalog.list()
    except Exception as exc:
        console.print(f"[red]✗[/red]  could not read the profile catalog: {exc}")
        raise typer.Exit(2) from exc
    valid_names = {p.name for p in profiles}

    # Scan slots → (slot, profile_name) here (not via the catalog's private
    # helper) so a malformed slot is surfaced, not silently skipped.
    slot_profiles: list[tuple[str, str | None]] = []
    for slot_name in list_slots():
        try:
            cfg = load_slot_config(slot_name)
        except Exception as exc:
            console.print(
                f"[yellow]![/yellow]  slot {slot_name}: unreadable TOML ({exc}) — skipped."
            )
            continue
        slot_profiles.append((slot_name, cfg.profile))

    ref_rows = check_slot_profile_refs(slot_profiles, valid_names)
    img_rows = check_profile_images_present(profiles, _local_image_repos())

    _render_profiles("Slot → profile references", ref_rows)
    _render_profiles("Profile images (in-use)", img_rows)

    broken = [r for r in ref_rows if r["status"] == "drift"]
    if broken:
        console.print(
            f"\n[red]✗[/red]  {len(broken)} slot(s) reference a missing profile — "
            "fix before those slots can start."
        )
        raise typer.Exit(1)
    console.print("\n[green]✓[/green]  every slot resolves to a real profile.")
    raise typer.Exit(0)
