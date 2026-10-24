"""install.sh's root-run mutations under ${VAR_DIR} go through the descriptor helper (#2975).

``${VAR_DIR}`` is hal0-owned and not sticky. A ``[[ -L ]]`` check followed by
a pathname ``chmod``/``install -d`` leaves a window in which the hal0 account
swaps the entry for a symlink (``/etc/shadow`` made world-readable) or renames
another directory into its name. These tests pin that every such site calls
the ``nofollow`` shell function, and run that function exactly as the
installer defines it.
"""

from __future__ import annotations

import re
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "installer" / "install.sh"


def _text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def _code(block: str) -> str:
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def _function() -> str:
    m = re.search(r"^nofollow\(\) \{\n.*?\n\}\n", _text(), re.MULTILINE | re.DOTALL)
    assert m is not None, "nofollow() not found in install.sh"
    return m.group(0)


def test_helper_runs_the_module_isolated() -> None:
    fn = _function()
    assert '"${PY}" -I "${REPO_ROOT}/src/hal0/security/nofollow.py"' in fn


def test_state_dirs_are_adopted_through_the_helper() -> None:
    text = _text()
    block = text[
        text.index("# O13: the runtime state trees") : text.index("# mDNS addon advertisements")
    ]
    code = _code(block)
    assert 'nofollow dir --owner hal0:hal0 --mode 2775 --expect adopt "${_state_dir}"' in code
    for unsafe in ("chmod ", "chown ", "[[ -L"):
        assert unsafe not in code, unsafe


def test_secrets_seed_goes_through_the_helper() -> None:
    text = _text()
    start = text.index('mkdir -p "${ETC_DIR}/agents"')
    block = text[start : text.index("# ── Bundle picker manifests", start)]
    code = _code(block)
    assert "nofollow dir --create --owner root:root --mode 0700 --expect root" in code
    assert '"${VAR_DIR}/secrets" "${VAR_DIR}/secrets/agents"' in code
    for unsafe in ("install -d", "chmod ", "chown ", "[[ -L"):
        assert unsafe not in code, unsafe


def _run_helper(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    script = (
        f'set -euo pipefail\nPY={sys.executable}\nREPO_ROOT={REPO}\n{_function()}nofollow "$@"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "bash", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )


def test_helper_refuses_a_swapped_in_secrets_link(tmp_path: Path) -> None:
    var = tmp_path / "var-lib-hal0"
    var.mkdir()
    var.chmod(0o2775)
    victim = tmp_path / "etc"
    victim.mkdir()
    victim.chmod(0o755)
    (var / "secrets").symlink_to(victim)
    # A planted module in the caller's cwd must not load: -I.
    (tmp_path / "secrets.py").write_text("raise SystemExit('planted secrets.py ran')\n")
    proc = _run_helper(
        tmp_path,
        "dir",
        "--create",
        "--mode",
        "0700",
        "--expect",
        "root",
        str(var / "secrets"),
        str(var / "secrets" / "agents"),
    )
    assert proc.returncode == 3, proc.stderr
    assert "planted" not in proc.stderr
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755
    assert list(victim.iterdir()) == []


def test_helper_seeds_real_secrets_dirs(tmp_path: Path) -> None:
    var = tmp_path / "var-lib-hal0"
    var.mkdir()
    var.chmod(0o2775)
    proc = _run_helper(
        tmp_path,
        "dir",
        "--create",
        "--mode",
        "0700",
        "--expect",
        "root",
        str(var / "secrets"),
        str(var / "secrets" / "agents"),
    )
    assert proc.returncode == 0, proc.stderr
    for d in (var / "secrets", var / "secrets" / "agents"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
