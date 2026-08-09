"""Hermes WebUI (hermex) companion provisioning.

Third-party web app for the Hermes agent (github.com/nesquena/hermes-webui).
hal0 manages it as a pinned git tree under /var/lib/hal0/hermes-webui running
from the hermes venv, with a seeded-but-operator-owned ``.env``.

Deliberately import-free of :mod:`hal0.agents.hermes_provision` (which lazily
imports this module for its pipeline step) to avoid a cycle.
"""

from __future__ import annotations

import secrets
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
