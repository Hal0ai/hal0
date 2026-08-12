"""CLI implementation for ``hal0 doctor``.

The default ``hal0 doctor`` invocation shells out to
``installer/lib/preflight.sh`` (the same script ``installer/install.sh``
sources for its pre-install checks) so the operator can re-run the full
preflight battery post-install without touching the installer.

Locating the script:

* ``HAL0_PREFLIGHT_SH`` env var wins, when set — useful for tests and
  for install layouts this module doesn't know about.
* In an editable checkout (``pip install -e <repo>``, e.g. ``--dev``
  installs) ``hal0.__file__`` is ``<repo>/src/hal0/__init__.py``, so
  ``Path(hal0.__file__).parents[2]`` resolves to the repo root.
* ``installer/install.sh`` normally builds a real wheel into a shared
  venv (prod FHS layout), so ``hal0.__file__`` lives under
  ``site-packages/`` with no repo neighbours and the editable probe
  above always misses. We fall back to the FHS release tree instead:
  ``<HAL0_FHS_ROOT or HAL0_PREFIX>/current/installer/lib/preflight.sh``,
  defaulting to ``/usr/lib/hal0/current`` (via
  :func:`hal0.config.paths.usr_lib`, which also honours ``HAL0_HOME``
  for tests) when neither env var is set, plus a bare
  ``HAL0_PREFIX/installer/lib/preflight.sh`` for dev-prefix installs
  that have no ``current`` symlink.

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
import json as jsonlib
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
from hal0.cli.doctor_diagnosis import Diagnosis, Evidence, NextStep, render_json
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
    explicit env-var first, then the editable-checkout layout, then the
    FHS release-tree layout (see the module docstring for the full
    precedence). The first candidate that exists on disk wins.
    """
    override = os.environ.get("HAL0_PREFLIGHT_SH", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None

    candidates: list[Path] = []

    # Editable install: ``hal0.__file__`` is ``<repo>/src/hal0/__init__.py``;
    # parents[2] is the repo root.
    try:
        repo_root = Path(hal0.__file__).resolve().parents[2]
    except (AttributeError, IndexError):
        pass
    else:
        candidates.append(repo_root / "installer" / "lib" / "preflight.sh")

    # FHS install (installer/install.sh's default, non-editable wheel in a
    # shared venv): the script ships inside the versioned release tree,
    # reachable through the ``current`` symlink. Honour an explicit root
    # override first, then fall back to the compiled-in default.
    fhs_root = os.environ.get("HAL0_FHS_ROOT", "").strip()
    if fhs_root:
        candidates.append(Path(fhs_root) / "current" / "installer" / "lib" / "preflight.sh")

    # ``HAL0_PREFIX`` — dev-prefix installs (``installer/install.sh --dev``)
    # point straight at the prefix with no ``current`` symlink.
    prefix = os.environ.get("HAL0_PREFIX", "").strip()
    if prefix:
        candidates.append(Path(prefix) / "installer" / "lib" / "preflight.sh")

    if not fhs_root:
        from hal0.config import paths as cfg_paths

        candidates.append(cfg_paths.usr_lib() / "installer" / "lib" / "preflight.sh")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    verify: bool = typer.Option(
        False,
        "--verify",
        hidden=True,
        help="Deprecated — use `hal0 doctor verify`.",
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

    ``hal0 doctor verify`` instead renders the WS-K report card: a pass/warn/fail
    summary over the live health seams (API, runners, capability slots, hindsight,
    OpenWebUI, Hermes) plus the computed URLs + help links. Non-blocking; exits
    2 only on a critical (no reachable URL / zero healthy runners). The bare
    ``--verify`` flag on this callback still works as a deprecated pass-through.
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
    # This is a post-install self-check, not the pre-install gate — on a
    # healthy box the default ports are bound by hal0's own hal0-api /
    # hal0-openwebui units. Don't fail the whole run over that (see
    # preflight_ports in installer/lib/preflight.sh).
    env["HAL0_DOCTOR_PORTS_SOFT"] = "1"

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


# ── hal0 doctor verify ──────────────────────────────────────────────────────


@app.command("verify")
def doctor_verify_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit {'verdict','diagnoses'} Diagnosis JSON instead of the report card.",
    ),
) -> None:
    """Render the post-setup report card (checks + live URLs + doc links).

    First-class home for what used to be reachable only via the hidden
    ``hal0 doctor --verify`` flag — every other doctor audit (``perms``,
    ``models``, ``migrations``, ``profiles``, ``toolbox-pull``) is a
    discoverable subcommand, so this one is now too. See the module-level
    docstring for the health seams this composes.
    """
    from hal0.cli.doctor_verify import run_verify

    raise typer.Exit(run_verify(console=console, json_output=json_output))


# ── hal0 doctor logs ─────────────────────────────────────────────────────────


@app.command("logs")
def doctor_logs(
    unit: str = typer.Option(
        "hal0-api",
        "--unit",
        help="systemd unit to tail (default: hal0-api — hal0's own daemon).",
    ),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs (SSE tail)."),
    lines: int = typer.Option(
        200, "--lines", "-n", min=1, max=5000, help="Trailing line count (ignored with --follow)."
    ),
    level: str | None = typer.Option(
        None, "--level", help="Filter to this journald priority and higher (e.g. 'warning')."
    ),
    since: str | None = typer.Option(
        None, "--since", help="journalctl --since value (ISO timestamp or '5min ago')."
    ),
) -> None:
    """Print or follow hal0-api's own systemd journal.

    ``hal0 slot logs`` covers per-slot logs and ``hal0 agent log`` covers
    per-agent provisioning logs, but nothing surfaced hal0-api's *own*
    log — operators were pushed to ``journalctl -u hal0-api`` by hand,
    defeating the point of ``doctor`` as the diagnostics entry point.
    Thin client over ``GET /api/logs`` (and ``/api/logs/stream`` with
    ``--follow``), the same endpoints the dashboard's log panel uses.
    """
    from hal0.cli._shared import (
        CliApiError,
        _api_base,
        _api_unreachable,
        api_get,
        die,
        follow_sse_logs,
    )

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    if not follow:
        params: dict[str, object] = {"unit": unit, "n": lines}
        if level is not None:
            params["level"] = level
        if since is not None:
            params["since"] = since
        try:
            data = api_get("/api/logs", params=params)
        except CliApiError as exc:
            die(str(exc))
            return
        log_lines = data.get("lines") or [] if isinstance(data, dict) else []
        if not log_lines:
            hint = data.get("hint") if isinstance(data, dict) else None
            console.print(f"[dim]no logs{f' ({hint})' if hint else ''}.[/dim]")
            return
        for line in log_lines:
            console.print(line)
        return

    # Stream SSE — line-buffered passthrough (mirrors `hal0 slot logs --follow`).
    stream_params: dict[str, object] = {"unit": unit}
    if level is not None:
        stream_params["level"] = level
    if since is not None:
        stream_params["since"] = since
    follow_sse_logs("/api/logs/stream", console=console, params=stream_params)


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
        # Emit raw, plain JSON — `console.print_json` re-highlights with ANSI
        # escapes (rich reports is_terminal=True even when stdout is a pipe), so
        # `toolbox-pull --json | jq` would choke on colour codes. The --json
        # contract must be deterministic parseable JSON regardless of tty.
        print(jsonlib.dumps(rows, indent=2))
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


def _diagnose_audit_rows(
    rows: list[dict[str, str]],
    *,
    diagnosis_id: str,
    ok_summary: str,
    next_steps: list[NextStep] | None = None,
) -> list[Diagnosis]:
    """Convert ``ok``/``drift``/``absent`` audit rows into ``Diagnosis`` rows.

    :func:`check_hermes_ownership`, :func:`check_tree_group_share`, and
    ``hal0.install.perms.audit_rows`` all share this exact row vocabulary
    (``{path,label,status,detail}``), so one adapter covers every
    ``doctor perms`` sub-check (§2.1). ``absent`` and ``symlink`` rows carry
    no finding (a ``symlink`` row is one perms deliberately left alone, #1739).
    ``drift`` rows each become one ``fail`` Diagnosis; when nothing drifted,
    a single ``HAL0-DOCTOR-OK`` info row is emitted instead of an empty list
    so a clean ``--json`` run still has something to show.
    """
    drift = [r for r in rows if r["status"] == "drift"]
    if not drift:
        return [
            Diagnosis(id="HAL0-DOCTOR-OK", severity="info", confidence="high", summary=ok_summary)
        ]
    return [
        Diagnosis(
            id=diagnosis_id,
            severity="fail",
            confidence="high",
            summary=f"{r['label']}: {r['detail']}",
            detail=r["detail"],
            evidence=[
                Evidence(
                    kind="file", summary=r["detail"], data={"path": r["path"], "label": r["label"]}
                )
            ],
            next_steps=next_steps or [],
        )
        for r in drift
    ]


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
        # #1739: perms deliberately never follows a symlink, so its target
        # (outside the declared tree) is never chowned.
        "symlink": "[dim]symlink[/dim]",
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
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip the confirmation prompt before applying --fix.",
    ),
    table_root: bool = typer.Option(
        False,
        "--table-root",
        help=(
            "Audit against the OLD root-era table (service_user='root') instead "
            "of the P3-perms hal0-owned default — the emergency rollback check "
            "if a box needs to verify/restore the pre-flip layout."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit stable Diagnosis JSON (HAL0-PERMS-*) instead of the human tables. "
            "Implies audit-only — --fix is ignored under --json."
        ),
    ),
) -> None:
    """Audit ownership for the root-clobber regression (#843) + the path table.

    Covers three surfaces: Hermes runtime state (/var/lib/hal0/.hermes), the
    editable code checkout's group-share, and the canonical path-ownership table
    (:mod:`hal0.install.perms` — P3-perms, the single ownership authority). The
    audit tables above print the concrete before/after (path, current
    owner:group:mode, wanted owner:group:mode) before anything is touched.
    ``--fix`` then repairs the group-share in place AND applies the ownership
    table (both need root, and both prompt for confirmation unless
    ``--force``/``-f`` is also passed); Hermes drift is still reconciled via
    ``sudo hal0 agent bootstrap hermes --repair``. This command is audit-only by
    default — nothing is ever written without ``--fix``. ``--json`` prints the
    §21.4 ``Diagnosis`` rows (``HAL0-PERMS-HERMES-DRIFT`` /
    ``HAL0-PERMS-TREE-NOT-SHARED`` / ``HAL0-PERMS-PATH-OWNERSHIP-DRIFT``)
    instead and never applies ``--fix``, even if it was also passed.
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
    if not json_output:
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
    if not json_output:
        _render_audit("Editable checkout group-share (#843)", tree_rows)
    tree_drift = has_ownership_drift(tree_rows)

    # 3) Canonical path-ownership table (read-only audit; --fix applies it).
    # Default table is hal0-owned (P3-perms) — a fresh, born-owned install
    # shows no drift here; an un-migrated pre-P3-perms box genuinely drifts,
    # which is the intended one-shot `--fix` migration. `--table-root` audits
    # against the OLD root-era table instead (emergency rollback check).
    from hal0.install import perms as perms_mod

    own_table = perms_mod.ownership_table(service_user="root") if table_root else None
    own_plan = perms_mod.plan(own_table)
    own_rows = perms_mod.audit_rows(own_plan)
    _table_title = (
        "Path ownership table (P3-perms, --table-root)"
        if table_root
        else "Path ownership table (P3-perms)"
    )
    if not json_output:
        _render_audit(_table_title, own_rows)
    own_drift = has_ownership_drift(own_rows)

    if json_output:
        diagnoses = (
            _diagnose_audit_rows(
                hermes_rows,
                diagnosis_id="HAL0-PERMS-HERMES-DRIFT",
                ok_summary="Hermes ownership clean",
                next_steps=[
                    NextStep(
                        kind="command",
                        label="sudo hal0 agent bootstrap hermes --repair",
                        target="hal0 agent bootstrap hermes --repair",
                    )
                ],
            )
            + _diagnose_audit_rows(
                tree_rows,
                diagnosis_id="HAL0-PERMS-TREE-NOT-SHARED",
                ok_summary="editable checkout group-share clean",
                next_steps=[
                    NextStep(
                        kind="command",
                        label="sudo hal0 doctor perms --fix",
                        target="hal0 doctor perms --fix",
                    )
                ],
            )
            + _diagnose_audit_rows(
                own_rows,
                diagnosis_id="HAL0-PERMS-PATH-OWNERSHIP-DRIFT",
                ok_summary="path-ownership table clean",
                next_steps=[
                    NextStep(
                        kind="command",
                        label="sudo hal0 doctor perms --fix",
                        target="hal0 doctor perms --fix",
                    )
                ],
            )
        )
        console.print_json(render_json(diagnoses))
        raise typer.Exit(1 if (has_ownership_drift(hermes_rows) or tree_drift or own_drift) else 0)

    if fix:
        if root is None:
            console.print("[dim]nothing to fix — not an editable checkout.[/dim]")
        elif not tree_drift:
            console.print("[green]✓[/green]  group-share already clean — nothing to fix.")
        elif os.geteuid() != 0:
            console.print("[red]✗[/red]  --fix needs root — re-run `sudo hal0 doctor perms --fix`.")
            raise typer.Exit(1)
        elif not force and not typer.confirm(
            f"Apply group-share repair to {root}? (chgrp -R {_SHARED_GROUP}, chmod g+rwX, "
            "setgid on every dir — see the drift rows above)",
            default=False,
        ):
            console.print("[dim]group-share repair skipped (not confirmed).[/dim]")
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
            elif not force and not typer.confirm(
                f"Apply the ownership table to {len(own_plan.drifted)} drifted path(s)? "
                "(see the 'is X, want Y' rows above)",
                default=False,
            ):
                console.print("[dim]ownership repair skipped (not confirmed).[/dim]")
            else:
                try:
                    changed = perms_mod.commit(own_plan)
                except (OSError, KeyError) as exc:
                    console.print(f"[red]✗[/red]  ownership repair failed: {exc}")
                    raise typer.Exit(1) from exc
                console.print(
                    f"[green]✓[/green]  ownership table applied "
                    f"({len(changed)} path(s) reconciled)."
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


def _dangling_registry_entries(local: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Registry entries (``{id,path,...}``) whose file no longer exists on disk."""
    return [m for m in local if not Path(str(m["path"])).exists()]


def _models_outside_mount_roots(
    local: list[dict[str, Any]], mount_roots: list[str]
) -> list[dict[str, Any]]:
    """Registry entries whose file path is NOT under any mounted model root.

    O25 guard: the slot container bind-mounts ``model_mount_roots()`` (the
    effective ``[models].store`` PLUS ``[models].pull_root``). A registered
    model whose absolute path lives outside *every* mounted root is unreachable
    in-container — llama-server exits ~90ms after start and the slot flaps
    ``error``↔``warming``, never ``ready`` (a config sets ``store`` off to the
    side while models live under ``pull_root``). Pure — mirrors the renderer's
    mount set, no I/O. Empty ``mount_roots`` (config unreadable) → no findings.
    """
    if not mount_roots:
        return []
    norm_roots = [os.path.normpath(r) for r in mount_roots if r]
    out: list[dict[str, Any]] = []
    for m in local:
        p = str(m.get("path") or "")
        if not p:
            continue
        pn = os.path.normpath(p)
        covered = any(pn == r or pn.startswith(r.rstrip("/") + "/") for r in norm_roots)
        if not covered:
            out.append(m)
    return out


def _unregistered_store_files(
    store_dir: Path, exts: set[str], registered_paths: set[str]
) -> list[str]:
    """Model files physically in ``store_dir`` that no registry entry names."""
    if not store_dir.is_dir():
        return []
    return [
        str(f)
        for f in store_dir.rglob("*")
        if f.is_file()
        and f.suffix.lower() in exts
        and not f.name.startswith(".")
        and str(f) not in registered_paths
    ]


def _diagnose_models(
    *,
    dangling: list[dict[str, Any]],
    unregistered: list[str],
    store_missing: bool,
    unmounted: list[dict[str, Any]] | None = None,
    mount_roots: list[str] | None = None,
    effective: str,
    divergence: dict[str, str] | None,
    mount_warn: dict[str, str] | None,
    flm_dir: Path,
    writ: dict[str, object] | None,
) -> list[Diagnosis]:
    """Assemble the §21.4 ``HAL0-MODEL-*`` Diagnosis rows from the evidence
    ``doctor_models`` already gathered (§2.2). Pure — no I/O."""
    diagnoses: list[Diagnosis] = []
    for m in dangling:
        diagnoses.append(
            Diagnosis(
                id="HAL0-MODEL-FILE-MISSING",
                severity="fail",
                confidence="high",
                summary=f"registry entry {m.get('id')} points at a missing file",
                detail=str(m.get("path")),
                evidence=[
                    Evidence(
                        kind="file",
                        summary=str(m.get("path")),
                        data={"model_id": m.get("id"), "path": m.get("path")},
                    )
                ],
                next_steps=[
                    NextStep(
                        kind="command",
                        label="hal0 model rm <id> && hal0 model scan",
                        target=f"hal0 model rm {m.get('id')} && hal0 model scan",
                    )
                ],
            )
        )
    if store_missing:
        diagnoses.append(
            Diagnosis(
                id="HAL0-MODEL-STORE-MISSING",
                severity="fail",
                confidence="high",
                summary=f"effective store {effective} does not exist",
                detail=effective,
                evidence=[Evidence(kind="file", summary=effective, data={"store_path": effective})],
            )
        )
    roots_str = ", ".join(mount_roots) if mount_roots else "(none)"
    for m in unmounted or []:
        diagnoses.append(
            Diagnosis(
                id="HAL0-MODEL-STORE-UNMOUNTED",
                severity="fail",
                confidence="high",
                summary=f"registry entry {m.get('id')} lives outside every mounted model root",
                detail=(
                    f"{m.get('path')} is under no mounted root ({roots_str}) — the slot "
                    "container can't see the file; llama exits ~90ms after start (O25)"
                ),
                evidence=[
                    Evidence(
                        kind="file",
                        summary=str(m.get("path")),
                        data={
                            "model_id": m.get("id"),
                            "path": m.get("path"),
                            "mount_roots": list(mount_roots or []),
                        },
                    )
                ],
                next_steps=[
                    NextStep(
                        kind="manual",
                        label="mount the model's tree",
                        target=(
                            "set [models].store / [models].pull_root to cover the model path "
                            "(or move the model under a mounted root)"
                        ),
                    )
                ],
            )
        )
    for f in unregistered:
        diagnoses.append(
            Diagnosis(
                id="HAL0-MODEL-UNREGISTERED",
                severity="warn",
                confidence="high",
                summary="model file present but not registered",
                detail=f,
                evidence=[Evidence(kind="file", summary=f, data={"file_path": f})],
                next_steps=[
                    NextStep(kind="command", label="hal0 model scan", target="hal0 model scan")
                ],
            )
        )
    if divergence:
        diagnoses.append(
            Diagnosis(
                id="HAL0-MODEL-FLM-STORE-DIVERGED",
                severity="warn",
                confidence="high",
                summary="FLM store env var diverges from hal0.toml",
                detail=divergence["detail"],
                evidence=[Evidence(kind="config", summary=divergence["detail"])],
                fixable=False,
            )
        )
    if mount_warn:
        diagnoses.append(
            Diagnosis(
                id="HAL0-MODEL-FLM-STORE-UNMOUNTED",
                severity="warn",
                confidence="high",
                summary=f"FLM store {flm_dir} not backed by a live mount",
                detail=mount_warn["detail"],
                evidence=[
                    Evidence(
                        kind="file",
                        summary=mount_warn["detail"],
                        data={"store_path": str(flm_dir)},
                    )
                ],
                next_steps=[
                    NextStep(
                        kind="manual",
                        label="order the slot after the mount",
                        target=(
                            "add RequiresMountsFor= to the systemd unit, or move the "
                            "store onto the root fs"
                        ),
                    )
                ],
            )
        )
    if writ is not None:
        diagnoses.append(
            Diagnosis(
                id="HAL0-MODEL-FLM-STORE-NOT-WRITABLE",
                severity="fail",
                confidence="high",
                summary=f"FLM store {flm_dir} not writable by the container uid",
                detail=str(writ["detail"]),
                evidence=[
                    Evidence(
                        kind="file",
                        summary=str(writ["detail"]),
                        data={"uid": writ.get("uid"), "mode": writ.get("mode")},
                    )
                ],
                next_steps=[
                    NextStep(
                        kind="command",
                        label="sudo hal0 doctor models --fix",
                        target="hal0 doctor models --fix",
                    )
                ],
                fixable=True,
            )
        )
    if not diagnoses:
        return [
            Diagnosis(
                id="HAL0-DOCTOR-OK",
                severity="info",
                confidence="high",
                summary="model pipeline clean",
            )
        ]
    return diagnoses


@app.command("models")
def doctor_models(
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Repair FLM store ownership/mode drift in place (needs root).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip the confirmation prompt before applying --fix.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit stable Diagnosis JSON (HAL0-MODEL-*) instead of the human report. "
            "Implies audit-only — --fix is ignored under --json."
        ),
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
    chmod 2775 — needs root, and prompts for confirmation unless ``--force``/
    ``-f`` is also passed). Exits non-zero when anything actionable is found.
    ``--json`` prints the §21.4 ``Diagnosis`` rows instead and never applies
    ``--fix``, even if it was also passed.
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
    dangling = _dangling_registry_entries(local)
    if dangling:
        problems += len(dangling)
        if not json_output:
            console.print(
                f"[red]✗[/red]  {len(dangling)} registry entr(y/ies) point at missing files:"
            )
            for m in dangling:
                console.print(f"      {m.get('id')} → {m.get('path')}")
            console.print(
                "      Fix: hal0 model rm <id> && hal0 model scan   (re-register from disk)\n"
                "      or point the store at the real location: hal0 model store <dir>"
            )
    elif not json_output:
        console.print(f"[green]✓[/green]  all {len(local)} registered model file(s) exist on disk.")

    # 1b. Mount reachability (O25): a model file present on disk but outside
    # every root the slot container bind-mounts (store + pull_root) is
    # unreachable in-container — llama exits ~90ms after start. Report the
    # present-but-unmounted entries (dangling ones are already covered above).
    try:
        mount_roots = cfg_paths.model_mount_roots()
    except Exception:
        mount_roots = []
    dangling_paths = {str(m.get("path")) for m in dangling}
    unmounted = [
        m
        for m in _models_outside_mount_roots(local, mount_roots)
        if str(m.get("path")) not in dangling_paths
    ]
    if unmounted:
        problems += len(unmounted)
        if not json_output:
            console.print(
                f"[red]✗[/red]  {len(unmounted)} model file(s) live outside every mounted root "
                f"({', '.join(mount_roots) or '(none)'}):"
            )
            for m in unmounted:
                console.print(f"      {m.get('id')} → {m.get('path')}")
            console.print(
                "      Fix: set [models].store / [models].pull_root to cover the model path "
                "(the slot container mounts those roots)."
            )

    # 2. Store/roots agreement + unregistered files in the store.
    try:
        cfg = load_hal0_config()
        scan_roots = cfg.models.scan_roots()
        effective = cfg.models.effective_store()
        exts = {e.lower() for e in cfg.models.file_extensions}
    except Exception as exc:  # config unreadable — report, keep going
        if not json_output:
            console.print(f"[yellow]![/yellow]  could not read hal0.toml: {exc}")
        scan_roots, effective, exts = [], "", {".gguf", ".safetensors"}
    unregistered: list[str] = []
    store_missing = False
    if effective:
        registered_paths = {str(m.get("path")) for m in local}
        store_dir = Path(effective)
        if not store_dir.is_dir():
            store_missing = True
            problems += 1
            if not json_output:
                console.print(f"[red]✗[/red]  effective store {effective} does not exist.")
        else:
            unregistered = _unregistered_store_files(store_dir, exts, registered_paths)
        if unregistered:
            problems += 1
            if not json_output:
                console.print(
                    f"[yellow]![/yellow]  {len(unregistered)} model file(s) in the store are "
                    f"not registered — run: hal0 model scan"
                )
                for f in unregistered[:10]:
                    console.print(f"      {f}")
        if not json_output:
            console.print(f"[dim]store: {effective}  ·  scan roots: {', '.join(scan_roots)}[/dim]")

    # 3a. FLM store divergence: env var silently overriding the TOML field.
    env_flm = os.environ.get("HAL0_FLM_MODELS_DIR")
    toml_flm = None
    with contextlib.suppress(Exception):  # already surfaced under step 2's hal0.toml read
        toml_flm = load_hal0_config().models.flm_store
    divergence = flm_store_divergence(env_flm, toml_flm)
    if divergence:
        problems += 1
        if not json_output:
            console.print(f"[yellow]![/yellow]  {divergence['detail']}")

    # 3b. FLM (NPU) store: mount-backed, exists, writable by the container uid.
    flm_dir = Path(cfg_paths.flm_models_dir())
    mount_warn = flm_mount_guard(flm_dir)
    if mount_warn:
        problems += 1
        if not json_output:
            console.print(f"[yellow]![/yellow]  {mount_warn['detail']}")

    writ: dict[str, object] | None = None
    if flm_dir.exists():
        writ = flm_store_writability(flm_dir, stat_of=lambda p: p.stat())
        if writ is not None:
            problems += 1

    if json_output:
        diagnoses = _diagnose_models(
            dangling=dangling,
            unregistered=unregistered,
            store_missing=store_missing,
            unmounted=unmounted,
            mount_roots=mount_roots,
            effective=effective,
            divergence=divergence,
            mount_warn=mount_warn,
            flm_dir=flm_dir,
            writ=writ,
        )
        console.print_json(render_json(diagnoses))
        raise typer.Exit(1 if problems else 0)

    if writ is None:
        if flm_dir.exists():
            console.print(f"[green]✓[/green]  FLM store {flm_dir} present.")
    elif fix:
        if os.geteuid() != 0:
            console.print(
                f"[red]✗[/red]  FLM store {flm_dir}: {writ['detail']}\n"
                "      --fix needs root — re-run `sudo hal0 doctor models --fix`."
            )
        elif not force and not typer.confirm(
            f"Apply chown {_FLM_CONTAINER_UID}:hal0 + chmod 2775 to {flm_dir}? "
            f"(currently: {writ['detail']})",
            default=False,
        ):
            console.print(f"[dim]FLM store repair skipped (not confirmed): {flm_dir}[/dim]")
        else:
            ok, msg = repair_flm_store(flm_dir)
            if ok:
                console.print(f"[green]✓[/green]  repaired {msg}")
                problems -= 1
            else:
                console.print(f"[red]✗[/red]  repair failed: {msg}")
    else:
        console.print(
            f"[yellow]![/yellow]  FLM store {flm_dir} {writ['detail']}.\n"
            "      Fix: sudo hal0 doctor models --fix   "
            f"(chown {_FLM_CONTAINER_UID}:hal0 + chmod 2775)"
        )
    if writ is None and not flm_dir.exists() and not mount_warn:
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


def _diagnose_migration(pending: tuple[int, int] | None) -> list[Diagnosis]:
    """One ``HAL0-MIGRATION-PENDING`` row (always ``warn`` — operator-initiated),
    or ``HAL0-DOCTOR-OK``/``HAL0-DOCTOR-SKIPPED`` when there's nothing to do."""
    if pending is None:
        return [
            Diagnosis(
                id="HAL0-DOCTOR-SKIPPED",
                severity="info",
                confidence="high",
                summary="model-layout migration planner unavailable — skipped",
            )
        ]
    create, overwrite = pending
    if not create and not overwrite:
        return [
            Diagnosis(
                id="HAL0-DOCTOR-OK",
                severity="info",
                confidence="high",
                summary="model layout is current — no migration pending",
            )
        ]
    detail = f"{create} link(s) to create"
    if overwrite:
        detail += f", {overwrite} to overwrite (needs --force)"
    return [
        Diagnosis(
            id="HAL0-MIGRATION-PENDING",
            severity="warn",
            confidence="high",
            summary="v0.1→v0.2 model-layout migration pending",
            detail=detail,
            evidence=[
                Evidence(
                    kind="command",
                    summary=detail,
                    data={"create_count": create, "overwrite_count": overwrite},
                )
            ],
            next_steps=[
                NextStep(
                    kind="command",
                    label="hal0 migrate model-layout --apply",
                    target="hal0 migrate model-layout --apply",
                )
            ],
        )
    ]


@app.command("migrations")
def doctor_migrations(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit stable Diagnosis JSON (HAL0-MIGRATION-PENDING) instead of the human line.",
    ),
) -> None:
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
    if json_output:
        console.print_json(render_json(_diagnose_migration(pending)))
        raise typer.Exit(0 if pending is None or pending == (0, 0) else 1)
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


#: Slot types where a MISSING profile is a proven silent failure, mapped to the
#: fallback repair profile (#1830). ``tts`` / ``transcription`` / ``image`` are
#: deliberately absent: their providers carry their own profile-less defaults,
#: so an empty profile there is not (yet) a demonstrated 501.
#:
#: The fallback is only used when the create-time rule
#: (:func:`hal0.slots.profile_adopt.type_implied_profile`) cannot answer for
#: this slot's (type, device) — that rule is the authority, so doctor's repair
#: names the SAME profile a freshly created slot would get (an ``npu``
#: embedding slot wants ``flm``, not llama-server's ``embedding``).
_PROFILELESS_CAPABILITY_REPAIR: dict[str, str] = {
    "embedding": "embedding",
    "reranking": "reranking",
}


def _profileless_repair_for(slot_type: str, device: str) -> str | None:
    """The profile that repairs a profile-less capability slot, or ``None``."""
    if slot_type not in _PROFILELESS_CAPABILITY_REPAIR:
        return None
    try:
        from hal0.slots.profile_adopt import type_implied_profile

        inferred = type_implied_profile({"type": slot_type, "device": device})
    except Exception:  # unreadable catalog — fall back to the static answer
        inferred = None
    return inferred or _PROFILELESS_CAPABILITY_REPAIR[slot_type]


def check_slot_profile_refs(
    slot_profiles: list[tuple[str, str | None]],
    valid_names: set[str],
    slot_types: dict[str, str] | None = None,
    slot_devices: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Flag slots whose ``profile = "..."`` names a profile not in the catalog.

    ``resolve_slot_profile`` raises ``KeyError`` for a missing name only when the
    slot starts — so a renamed/deleted profile is a latent slot-start failure.
    Returns one row per slot: ``ok`` when the reference resolves, ``drift`` when
    it dangles.

    A profile-less slot is legal for ``llm`` (base-image resolution) and skipped.
    For a CAPABILITY slot it is drift (#1830): the profile carries the mode flag
    (``--embedding`` / ``--reranking``) or selects the engine, so without one the
    slot loads to ``state=ready`` and returns 501 from its own endpoint —
    silent, and nothing else in hal0 warns about it. New slots infer the profile
    at create time, but no seed loop back-fills an existing TOML, so an upgraded
    box keeps whatever profile-less slots its operator created historically.
    ``slot_types`` maps slot name → slot type; callers that cannot supply it get
    the old skip-everything-profile-less behaviour. ``slot_devices`` maps slot
    name → device and only sharpens the suggested repair (the create-time rule
    is device-keyed: ``npu`` embeddings run on FLM, not llama-server).
    """
    types = slot_types or {}
    devices = slot_devices or {}
    rows: list[dict[str, str]] = []
    for slot, profile in slot_profiles:
        if not profile:
            implied = _profileless_repair_for(
                str(types.get(slot) or ""), str(devices.get(slot) or "")
            )
            if implied is None:
                continue  # llm / unknown type — no profile to resolve
            rows.append(
                {
                    "label": slot,
                    "status": "drift",
                    "detail": (
                        f"{types[slot]} slot with NO profile — it will load "
                        f"'ready' and answer 501 on its own endpoint (the profile "
                        f"carries the mode flag). Repair: hal0 slot edit {slot} "
                        f"--profile {implied} && hal0 slot restart {slot}."
                    ),
                },
            )
            continue
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
    """Warn when an *in-use* slot's effective image isn't present locally.

    ``local_repos`` is the set of ``registry/repo`` strings from ``podman
    images`` (tag-insensitive to avoid false alarms on a re-pinned tag), or
    ``None`` when podman couldn't be queried — in which case the whole check is
    skipped (no rows). Only profiles referenced by a slot are checked: an unused
    profile whose image was never pulled is not a live problem.
    """
    if local_repos is None:
        return []
    rows: list[dict[str, str]] = []
    from hal0.config.loader import load_slot_config
    from hal0.profiles import ProfileCatalog
    from hal0.providers.container import _resolve_image_ref

    catalog = ProfileCatalog()
    for p in profiles:
        used_by = tuple(getattr(p, "used_by", ()))
        if not used_by:
            continue
        try:
            profile = catalog.resolve(p.name)
        except Exception:
            continue
        for slot_name in used_by:
            try:
                slot_cfg = load_slot_config(slot_name).model_dump(mode="python")
                image = _resolve_image_ref(slot_cfg, profile)
            except Exception:
                continue
            repo = _image_repo(image)
            if repo in local_repos:
                rows.append({"label": slot_name, "status": "ok", "detail": f"image {repo} present"})
            else:
                rows.append(
                    {
                        "label": slot_name,
                        "status": "warn",
                        "detail": (
                            f"image repo {repo} not pulled (profile {p.name}) — first slot "
                            f"start will pull it, or pre-pull: podman pull {image}"
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


def _diagnose_profiles(
    ref_rows: list[dict[str, str]], img_rows: list[dict[str, str]]
) -> list[Diagnosis]:
    """§2.4: dangling slot→profile refs (fail) + un-pulled images (warn)."""
    diagnoses: list[Diagnosis] = [
        Diagnosis(
            id="HAL0-PROFILE-REF-DANGLES",
            severity="fail",
            confidence="high",
            summary=f"slot {r['label']} references a missing profile",
            detail=r["detail"],
            evidence=[Evidence(kind="table_row", summary=r["detail"], data=r)],
            next_steps=[
                NextStep(
                    kind="command",
                    label=f"hal0 slot edit {r['label']} --profile <name>",
                    target=f"hal0 slot edit {r['label']} --profile <name>",
                )
            ],
        )
        for r in ref_rows
        if r["status"] == "drift"
    ]
    diagnoses += [
        Diagnosis(
            id="HAL0-PROFILE-IMAGE-MISSING",
            severity="warn",
            confidence="medium",
            summary=f"profile {r['label']} image not pulled locally",
            detail=r["detail"],
            evidence=[Evidence(kind="table_row", summary=r["detail"], data=r)],
            next_steps=[
                NextStep(kind="command", label="podman pull <image>", target="podman pull <image>")
            ],
        )
        for r in img_rows
        if r["status"] == "warn"
    ]
    if not diagnoses:
        return [
            Diagnosis(
                id="HAL0-DOCTOR-OK",
                severity="info",
                confidence="high",
                summary="every slot resolves to a real profile",
            )
        ]
    return diagnoses


@app.command("profiles")
def doctor_profiles(
    json_output: bool = typer.Option(
        False,
        "--json",
        help=("Emit stable Diagnosis JSON (HAL0-PROFILE-*) instead of the human tables."),
    ),
) -> None:
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
    #
    # id-aware (P3-runtime-db inc4): list_slots() enumerates on-disk stems,
    # which on an id-keyed box are digit ids, not display names. load_slot_config
    # still needs the raw stem to find the file, but the reported identity is
    # always cfg.name — the real display name a bilingual TOML embeds
    # regardless of which stem it lives under.
    slot_profiles: list[tuple[str, str | None]] = []
    slot_types: dict[str, str] = {}
    slot_devices: dict[str, str] = {}
    for slot_name in list_slots():
        try:
            cfg = load_slot_config(slot_name)
        except Exception as exc:
            console.print(
                f"[yellow]![/yellow]  slot {slot_name}: unreadable TOML ({exc}) — skipped."
            )
            continue
        slot_profiles.append((cfg.name, cfg.profile))
        # Type + device feed the profile-less capability check (#1830): the
        # type decides whether it is drift, the device which profile repairs it.
        slot_types[cfg.name] = str(getattr(cfg, "type", "") or "")
        slot_devices[cfg.name] = str(getattr(cfg, "device", "") or "")

    ref_rows = check_slot_profile_refs(slot_profiles, valid_names, slot_types, slot_devices)
    img_rows = check_profile_images_present(profiles, _local_image_repos())

    broken = [r for r in ref_rows if r["status"] == "drift"]

    if json_output:
        console.print_json(render_json(_diagnose_profiles(ref_rows, img_rows)))
        raise typer.Exit(1 if broken else 0)

    _render_profiles("Slot → profile references", ref_rows)
    _render_profiles("Profile images (in-use)", img_rows)

    if broken:
        console.print(
            f"\n[red]✗[/red]  {len(broken)} slot(s) have a broken profile reference "
            "or none at all — a dangling reference stops the slot from starting; "
            "a missing one lets it start and answer 501. See each row for the repair."
        )
        raise typer.Exit(1)
    console.print("\n[green]✓[/green]  every slot resolves to a real profile.")
    raise typer.Exit(0)


# ── hal0 doctor ports — the drill-down the roll-up's Slot ports row names ─────


def _render_dnat(verdicts: list[Any]) -> None:
    """Print the per-port netavark DNAT table (#1814)."""
    table = Table(title="Netavark DNAT rules (published slot ports)", box=None, pad_edge=False)
    table.add_column("port", justify="right")
    table.add_column("rules", justify="right")
    table.add_column("first match")
    table.add_column("verdict")

    for v in verdicts:
        first = v.first_match
        target = f"{first.target_ip}:{first.target_port}" if first else "—"
        if not v.rules:
            verdict = "[dim]no rule (slot not running)[/dim]"
        elif v.corrupt:
            verdict = f"[red]{v.reason()}[/red]"
        else:
            verdict = "[green]ok[/green]"
        table.add_row(
            f"[red]{v.port}[/red]" if v.corrupt else str(v.port),
            str(len(v.rules)),
            target,
            verdict,
        )
    console.print(table)


def _repair_dnat(verdicts: list[Any]) -> tuple[int, int]:
    """Delete every stale DNAT handle behind a corrupt port. Returns (ok, failed).

    Deletes highest-handle-first *within* a port so an earlier delete can never
    renumber a handle we are about to use — nft handles are stable, but the
    ordering costs nothing and removes the question. Live-targeted rules are
    never candidates (see :attr:`PortVerdict.stale`), so a working slot cannot
    be cut by a repair run.
    """
    from hal0.system.seam import SystemCtlSeam

    seam = SystemCtlSeam()
    ok = failed = 0
    for verdict in verdicts:
        if not verdict.corrupt:
            continue
        for rule in sorted(verdict.stale, key=lambda r: r.handle, reverse=True):
            try:
                seam.prune_dnat(verdict.port, rule.handle)
            except Exception as exc:
                failed += 1
                detail = getattr(exc, "stderr", None) or str(exc)
                console.print(
                    f"[red]✗[/red]  port {verdict.port}: could not prune handle "
                    f"{rule.handle}: {str(detail).strip()}"
                )
            else:
                ok += 1
                console.print(f"[green]✓[/green]  port {verdict.port}: pruned {rule.render()}")
    return ok, failed


@app.command("ports")
def doctor_ports(
    fix: bool = typer.Option(
        False,
        "--fix",
        help=(
            "Delete the stale netavark DNAT rules found by the audit "
            "(privileged, routed through the hal0-systemctl seam)."
        ),
    ),
) -> None:
    """Show which port each slot has bound, flag collisions and DNAT corruption.

    The drill-down for the ``Slot ports`` row in ``hal0 doctor all`` (#1501).
    Every other failing row in that table names a follow-up command; this one
    had none, so an operator who saw it warn had nowhere to go.

    Reads the same ``GET /api/slots`` the roll-up does, with the same enlarged
    budget — that route container-probes every slot, so on a populated box it
    legitimately takes longer than a normal API call. A timeout is reported as
    a timeout here, naming the budget it exceeded, rather than as the endpoint
    being down.

    It then audits the nftables DNAT rules netavark publishes those ports with
    (#1814). A container that dies without netavark's teardown running leaves
    its DNAT rule behind, and because nftables is first-match, that dead rule
    permanently shadows the correct one — the port answers nothing while the
    container inside is perfectly healthy. Two signals, both unambiguous and
    heuristic-free: more than one DNAT rule for a port, or a first-match rule
    targeting an IP no running container holds.

    ``--fix`` is the explicit repair. It is never a side effect of the audit:
    deleting nftables rules is privileged and destructive, so it happens only
    when an operator asks. Only rules whose target is already dead are deleted,
    so a healthy port cannot be cut.

    Exit codes:
      0 — no collisions and no DNAT corruption (or every finding was repaired).
      1 — a port collision, or DNAT corruption still present.
    """
    from hal0.cli._shared import CliApiError, api_get
    from hal0.cli.doctor_all import SLOTS_PROBE_TIMEOUT_S

    try:
        slots = api_get("/api/slots", timeout=SLOTS_PROBE_TIMEOUT_S)
    except CliApiError as exc:
        console.print(
            f"[red]✗[/red]  could not read /api/slots within "
            f"{SLOTS_PROBE_TIMEOUT_S:.0f}s: {exc}\n"
            "    The API may be down, or the slots aggregator may be slower than the\n"
            "    budget — check `systemctl status hal0-api` and `hal0 doctor all`."
        )
        raise typer.Exit(1) from exc

    if not isinstance(slots, list):
        console.print(
            f"[red]✗[/red]  unexpected /api/slots payload: {type(slots).__name__}, expected a list."
        )
        raise typer.Exit(1)

    rows = [s for s in slots if isinstance(s, dict)]
    bound = [(str(s.get("name") or "?"), int(s["port"])) for s in rows if s.get("port")]

    if not bound:
        console.print(
            f"[green]✓[/green]  no slot ports bound yet ({len(rows)} slot(s) configured)."
        )
        raise typer.Exit(0)

    table = Table(title="Slot ports", box=None, pad_edge=False)
    table.add_column("slot")
    table.add_column("port", justify="right")

    by_port: dict[int, list[str]] = {}
    for name, port in bound:
        by_port.setdefault(port, []).append(name)

    for name, port in sorted(bound, key=lambda r: r[1]):
        clash = len(by_port[port]) > 1
        table.add_row(
            f"[red]{name}[/red]" if clash else name,
            f"[red]{port}[/red]" if clash else str(port),
        )
    console.print(table)

    collisions = {p: names for p, names in by_port.items() if len(names) > 1}
    for port, names in sorted(collisions.items()):
        console.print(f"\n[red]✗[/red]  port {port} claimed by {len(names)}: {', '.join(names)}")
    if not collisions:
        console.print(f"\n[green]✓[/green]  {len(bound)} port(s) bound, no collisions.")

    # ── netavark DNAT audit (#1814) ──────────────────────────────────────────
    from hal0.system.netavark import (
        NetavarkUnavailable,
        audit_ports,
        read_dnat_rules,
        read_live_container_ips,
    )

    try:
        rules = read_dnat_rules()
        live_ips = read_live_container_ips()
    except (NetavarkUnavailable, OSError, subprocess.SubprocessError) as exc:
        # No netavark table / no podman / no nft is not a finding — a box that
        # publishes nothing through netavark has nothing to audit. Say so and
        # keep the collision verdict as the exit code.
        console.print(f"\n[dim]·[/dim]  netavark DNAT audit skipped: {exc}")
        raise typer.Exit(1 if collisions else 0) from None

    verdicts = audit_ports(sorted(by_port), rules, live_ips)
    console.print()
    _render_dnat(verdicts)

    corrupt = [v for v in verdicts if v.corrupt]
    if not corrupt:
        console.print("\n[green]✓[/green]  netavark DNAT rules are clean on every bound port.")
        raise typer.Exit(1 if collisions else 0)

    stale_total = sum(len(v.stale) for v in corrupt)
    console.print(
        f"\n[red]✗[/red]  {len(corrupt)} port(s) have stale netavark DNAT rules "
        f"({stale_total} dead rule(s)). nftables is first-match, so a leaked rule "
        "black-holes the port even when the container is healthy."
    )
    if not fix:
        console.print(
            "    Repair with `hal0 doctor ports --fix` (privileged: routed through "
            "the hal0-systemctl seam)."
        )
        raise typer.Exit(1)

    console.print()
    ok, failed = _repair_dnat(corrupt)
    console.print(f"\n[bold]pruned {ok} stale rule(s), {failed} failure(s).[/bold]")
    if failed or not ok:
        raise typer.Exit(1)
    raise typer.Exit(1 if collisions else 0)


# ── hal0 doctor all — read-only evidence roll-up (§21.4) ──────────────────────
#
# Registered here (not decorated in doctor_all.py) so the aggregate module stays
# import-cycle-free: it pulls `pending_layout_migration` from this module lazily,
# inside its orchestration function, rather than at import time.
from hal0.cli.doctor_all import doctor_all_cmd as _doctor_all_cmd  # noqa: E402

app.command("all")(_doctor_all_cmd)

# ── hal0 doctor bundle — the support-bundle generator (§21.4 §3) ──────────────
#
# Same import-cycle-free pattern as `all` above: doctor_bundle.py pulls the
# `_diagnose_*` / audit helpers from THIS module lazily (inside
# _write_diagnostics_section), so importing it here at module scope is safe.
from hal0.cli.doctor_bundle import doctor_bundle_cmd as _doctor_bundle_cmd  # noqa: E402

app.command("bundle")(_doctor_bundle_cmd)
