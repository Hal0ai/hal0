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

import os
import shutil
import subprocess
import sys
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
