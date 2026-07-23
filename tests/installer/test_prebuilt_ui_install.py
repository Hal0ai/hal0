"""Behavioral tests for the installer's dashboard build decision.

Release artifacts intentionally contain ``ui/dist`` but not the npm project used
to build it.  Exercise the real Node/UI shell block with fake toolchain commands
so a distribution-only tree can never regress into an impossible npm rebuild.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


def _node_and_ui_block() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(
        r'ui_step "Node\.js toolchain"\n(.*?)\nui_step "Configuration"',
        text,
        re.DOTALL,
    )
    assert match is not None, "Node.js/Dashboard UI block not found in install.sh"
    return match.group(1)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_ui_block(
    tmp_path: Path,
    *,
    package_json: bool,
    index_html: str | None,
    current_git_dist: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    tree = tmp_path / "tree"
    ui = tree / "ui"
    dist = ui / "dist"
    dist.mkdir(parents=True)
    if package_json:
        (ui / "package.json").write_text('{"scripts":{"build":"vite build"}}\n', encoding="utf-8")
    if index_html is not None:
        (dist / "index.html").write_text(index_html, encoding="utf-8")
    if current_git_dist:
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=tree, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tree, check=True)
        subprocess.run(["git", "add", "ui"], cwd=tree, check=True)
        subprocess.run(["git", "commit", "-qm", "ui"], cwd=tree, check=True)
        tree_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD:ui"], cwd=tree, text=True
        ).strip()
        (dist / ".hal0-build-stamp").write_text(f"{tree_hash}\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm_log = tmp_path / "npm.log"
    resolve_log = tmp_path / "resolve-node.log"
    _write_executable(
        fake_bin / "npm",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${NPM_LOG}"
if [[ "${1:-}" == "run" && "${2:-}" == "build" ]]; then
    mkdir -p "${REPO_ROOT}/ui/dist"
    printf '<!doctype html>built\n' > "${REPO_ROOT}/ui/dist/index.html"
fi
""",
    )
    _write_executable(fake_bin / "node", "#!/usr/bin/env bash\nprintf 'v22.0.0\\n'\n")

    harness = f"""set -euo pipefail
info() {{ printf 'INFO: %s\\n' "$*"; }}
warn() {{ printf 'WARN: %s\\n' "$*"; }}
ui_step() {{ :; }}
ui_spinner_run() {{ shift; "$@"; }}
resolve_node() {{ printf 'called\\n' >> "${{RESOLVE_LOG}}"; return 0; }}
export REPO_ROOT={tree!s}
export NPM_LOG={npm_log!s}
export RESOLVE_LOG={resolve_log!s}
DEV_MODE=0
HAL0_PORT=8080
NODE_MIN_MAJOR=20
{_node_and_ui_block()}
"""
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    proc = subprocess.run(
        ["bash", "-c", harness],
        cwd=tree,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    npm_calls = npm_log.read_text(encoding="utf-8").splitlines() if npm_log.exists() else []
    resolve_calls = (
        resolve_log.read_text(encoding="utf-8").splitlines() if resolve_log.exists() else []
    )
    return proc, npm_calls, resolve_calls


def test_release_tree_reuses_valid_prebuilt_dist_without_node_or_npm(tmp_path: Path) -> None:
    proc, npm_calls, resolve_calls = _run_ui_block(
        tmp_path,
        package_json=False,
        index_html="<!doctype html><html>signed release</html>\n",
    )

    assert proc.returncode == 0, proc.stderr
    assert npm_calls == []
    assert resolve_calls == []
    assert "using release's prebuilt ui/dist" in proc.stdout
    assert "will return 404" not in proc.stdout


def test_source_tree_retains_dashboard_build_behavior(tmp_path: Path) -> None:
    proc, npm_calls, resolve_calls = _run_ui_block(
        tmp_path,
        package_json=True,
        index_html=None,
    )

    assert proc.returncode == 0, proc.stderr
    assert resolve_calls == ["called"]
    assert npm_calls == ["install --no-audit --no-fund", "run build"]
    assert (tmp_path / "tree" / "ui" / "dist" / "index.html").is_file()


def test_current_git_dist_retains_freshness_skip(tmp_path: Path) -> None:
    proc, npm_calls, resolve_calls = _run_ui_block(
        tmp_path,
        package_json=True,
        index_html="<!doctype html><html>current source build</html>\n",
        current_git_dist=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert resolve_calls == ["called"]
    assert npm_calls == []
    assert "already built for this ui/ tree" in proc.stdout


def test_distribution_tree_with_invalid_dist_does_not_attempt_impossible_rebuild(
    tmp_path: Path,
) -> None:
    proc, npm_calls, _ = _run_ui_block(
        tmp_path,
        package_json=False,
        index_html="",
    )

    assert proc.returncode == 0, proc.stderr
    assert npm_calls == []
    assert "no valid prebuilt ui/dist/index.html" in proc.stdout
    assert "cannot rebuild without ui/package.json" in proc.stdout
