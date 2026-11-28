"""Shell lint ratchet (scripts/lint-shell.sh).

Real bash under subprocess. The script is copied into a throwaway repository
laid out like the real one (it finds the repository root from its own
location), a fake ``shellcheck`` on PATH prints canned gcc-format lines, and
the baseline lives in that throwaway tree. Only the baseline-content test reads
the real, tracked baseline file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "lint-shell.sh"
REAL_BASELINE = REPO / "scripts" / "shellcheck-baseline.txt"

FINDING = "installer/install.sh:{line}:5: warning: Quote this. [SC2086]"


def make_repo(tmp_path: Path, canned: list[str], baseline: list[str]) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "installer").mkdir()
    (root / "installer" / "install.sh").write_text("#!/bin/sh\n")
    shutil.copy(SCRIPT, root / "scripts" / "lint-shell.sh")
    (root / "scripts" / "shellcheck-baseline.txt").write_text(
        "# shellcheck 0.0.0\n" + "".join(b + "\n" for b in baseline)
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "canned.txt"
    out.write_text("".join(c + "\n" for c in canned))
    fake = bin_dir / "shellcheck"
    fake.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "ShellCheck - shell script analysis tool"; '
        'echo "version: 0.0.0"; exit 0; fi\n'
        f'cat "{out}"\n[ -s "{out}" ] && exit 1\nexit 0\n'
    )
    fake.chmod(0o755)
    return root


def run(root: Path, tmp_path: Path, *args: str, path: str | None = None, cwd: Path | None = None):
    env = dict(os.environ)
    env["PATH"] = path if path is not None else f"{tmp_path / 'bin'}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(root / "scripts" / "lint-shell.sh"), *args],
        cwd=cwd or root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_passes_at_baseline(tmp_path: Path):
    canned = [FINDING.format(line=3), FINDING.format(line=9)]
    root = make_repo(tmp_path, canned, ["installer/install.sh:SC2086:2"])
    r = run(root, tmp_path, cwd=tmp_path)  # from outside the repo root
    assert r.returncode == 0, r.stdout + r.stderr


def test_fails_on_new_finding(tmp_path: Path):
    canned = [FINDING.format(line=3), FINDING.format(line=9), FINDING.format(line=12)]
    root = make_repo(tmp_path, canned, ["installer/install.sh:SC2086:2"])
    r = run(root, tmp_path)
    assert r.returncode == 1
    assert "installer/install.sh:SC2086" in r.stdout + r.stderr
    assert "installer/install.sh:12:5" in r.stdout + r.stderr  # printed in full


def test_reports_when_count_fell(tmp_path: Path):
    root = make_repo(tmp_path, [FINDING.format(line=3)], ["installer/install.sh:SC2086:2"])
    r = run(root, tmp_path)
    assert r.returncode == 0
    assert "--update-baseline" in r.stdout + r.stderr


def test_install_matrix_files_have_no_baseline_entries():
    lines = [x for x in REAL_BASELINE.read_text().splitlines() if x and not x.startswith("#")]
    assert lines, "baseline is empty; generate it with scripts/lint-shell.sh --update-baseline"
    assert not [x for x in lines if x.startswith("scripts/install-matrix/")]
    assert not [x for x in lines if x.startswith("/")]
    assert lines == sorted(lines)


def test_missing_shellcheck_exits_3(tmp_path: Path):
    root = make_repo(tmp_path, [], [])
    bash_dir = tmp_path / "nosc"
    bash_dir.mkdir()
    for tool in (
        "bash",
        "sort",
        "find",
        "awk",
        "sed",
        "grep",
        "cat",
        "dirname",
        "mktemp",
        "rm",
        "diff",
        "comm",
        "head",
    ):
        found = shutil.which(tool)
        if found:
            (bash_dir / tool).symlink_to(found)
    r = run(root, tmp_path, path=str(bash_dir))
    assert r.returncode == 3
    assert "shellcheck" in r.stdout + r.stderr
