"""Hermes WebUI (hermex) companion provisioning.

Third-party web app for the Hermes agent (github.com/nesquena/hermes-webui).
hal0 manages it as a pinned git tree under /var/lib/hal0/hermes-webui running
from the hermes venv, with a seeded-but-operator-owned ``.env``.

Deliberately import-free of :mod:`hal0.agents.hermes_provision` (which lazily
imports this module for its pipeline step) to avoid a cycle.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

WEBUI_REPO_URL = "https://github.com/nesquena/hermes-webui.git"
# Vetting mirrors VETTED_HERMES_REFS: full commit SHAs that have been reviewed
# and run in production. deac1384… is the CT105 hand-install HEAD (2026-08-09),
# so adopting that box is a no-op.
WEBUI_PINNED_REF = "deac1384fed96c07134130b5c8df45b431d0b8c3"
VETTED_HERMES_WEBUI_REFS: frozenset[str] = frozenset({WEBUI_PINNED_REF})

WEBUI_TREE_DEFAULT = Path("/var/lib/hal0/hermes-webui")
WEBUI_VENV_DEFAULT = Path("/var/lib/hal0/venvs/hermes")
WEBUI_DEFAULT_PORT = 8787


@dataclass(frozen=True)
class StepOutcome:
    ok: bool
    changed: bool
    detail: str


def _env_defaults(venv: Path) -> dict[str, str]:
    return {
        "HERMES_WEBUI_HOST": "127.0.0.1",
        "HERMES_WEBUI_PORT": str(WEBUI_DEFAULT_PORT),
        "HERMES_WEBUI_PYTHON": str(venv / "bin" / "python3"),
    }


def ensure_env(tree: Path, venv: Path) -> StepOutcome:
    """Seed missing keys into ``<tree>/.env``; never rewrite existing values.

    The file is operator-owned after first write: re-provisioning only appends
    keys that are absent (the CT105 adoption case keeps its 0.0.0.0 bind and
    hand-set password). A password is generated only when the key is missing.
    """
    path = tree / ".env"
    existing = ""
    present: set[str] = set()
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                present.add(stripped.split("=", 1)[0].strip())

    wanted = _env_defaults(venv)
    missing = [(k, v) for k, v in wanted.items() if k not in present]
    if "HERMES_WEBUI_PASSWORD" not in present:
        missing.append(("HERMES_WEBUI_PASSWORD", secrets.token_urlsafe(24)))

    if not missing:
        # converge the mode even when content is current
        path.chmod(0o600)
        return StepOutcome(ok=True, changed=False, detail=".env current")

    body = existing
    if body and not body.endswith("\n"):
        body += "\n"
    if not body:
        body = "# hermes-webui server config (seeded by hal0; edits preserved)\n"
    body += "".join(f"{k}={v}\n" for k, v in missing)

    tmp = path.with_name(".env.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    return StepOutcome(
        ok=True, changed=True,
        detail=f"seeded {', '.join(k for k, _ in missing)}",
    )


_GIT_TIMEOUT = 120


def ensure_tree(
    tree: Path,
    *,
    ref: str = WEBUI_PINNED_REF,
    repo_url: str = WEBUI_REPO_URL,
    run=subprocess.run,
) -> StepOutcome:
    """Converge ``tree`` to the vetted pinned ref.

    Adoption-safe: an existing checkout already at the pin is untouched; a
    dirty checkout or a non-git directory is refused (ok=False) rather than
    clobbered — the caller downgrades that to a SKIP with this detail.
    """
    if ref not in VETTED_HERMES_WEBUI_REFS and not os.environ.get(
        "HAL0_ALLOW_UNVETTED_HERMES_WEBUI", ""
    ).strip():
        return StepOutcome(ok=False, changed=False, detail=f"unvetted ref {ref[:12]}")

    def _git(*args: str, cwd: Path | None = None):
        argv = ["git", *(("-C", str(cwd)) if cwd else ()), *args]
        return run(argv, check=False, capture_output=True, text=True, timeout=_GIT_TIMEOUT)

    if (tree / ".git").exists():
        head = _git("rev-parse", "HEAD", cwd=tree)
        if head.returncode == 0 and head.stdout.strip() == ref:
            return StepOutcome(ok=True, changed=False, detail=f"tree at pinned ref {ref[:12]}")
        dirty = _git("status", "--porcelain", cwd=tree)
        if dirty.returncode != 0 or dirty.stdout.strip():
            return StepOutcome(ok=False, changed=False,
                               detail="tree dirty or unreadable; refusing to move ref")
        fetch = _git("fetch", "origin", ref, cwd=tree)
        if fetch.returncode != 0:
            return StepOutcome(ok=False, changed=False,
                               detail=f"git fetch failed: {(fetch.stderr or '').strip()[-200:]}")
        co = _git("checkout", "--detach", ref, cwd=tree)
        if co.returncode != 0:
            return StepOutcome(ok=False, changed=False,
                               detail=f"git checkout failed: {(co.stderr or '').strip()[-200:]}")
        return StepOutcome(ok=True, changed=True, detail=f"checked out pinned ref {ref[:12]}")

    if tree.exists() and any(tree.iterdir()):
        return StepOutcome(
            ok=False, changed=False,
            detail="unmanaged non-git tree present; move it aside or set HAL0_SKIP_HERMES_WEBUI=1",
        )

    clone = run(["git", "clone", "--filter=blob:none", repo_url, str(tree)],
                check=False, capture_output=True, text=True, timeout=600)
    if clone.returncode != 0:
        return StepOutcome(ok=False, changed=False,
                           detail=f"git clone failed: {(clone.stderr or '').strip()[-200:]}")
    co = _git("checkout", "--detach", ref, cwd=tree)
    if co.returncode != 0:
        return StepOutcome(ok=False, changed=False,
                           detail=f"git checkout failed: {(co.stderr or '').strip()[-200:]}")
    return StepOutcome(ok=True, changed=True, detail=f"cloned at pinned ref {ref[:12]}")
