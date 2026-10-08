"""Guard: no operator-lab identifiers in tracked files (#2301).

The repository is published. The maintainer's lab subnet, ssh key name,
artefact-store mount, and personal domain leaked into fixtures, comments, and
reports once already; this test keeps them out. Fixtures should use RFC 5737
documentation addresses (``192.0.2.0/24``), a generic RFC 1918 address when
the test needs private-network semantics (``10.0.0.0/24``), and
``example.com`` names.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()

FORBIDDEN = re.compile(r"10\.0\.1\.|\.ssh/thin-mint|/mnt/mintdev|thinmint\.dev")

#: (path, matched text) pairs that may stay. CHANGELOG history is a record of
#: what shipped and is not rewritten; only the entry that fixed the leaked
#: dashboard default names the domain.
ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("CHANGELOG.md", "thinmint.dev"),
    }
)


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def test_no_operator_identifiers_in_tracked_files() -> None:
    offenders: list[str] = []
    for rel in _tracked_files():
        if rel == THIS_FILE:
            continue
        path = REPO_ROOT / rel
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary
        text = data.decode("utf-8", "replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in FORBIDDEN.finditer(line):
                if (rel, match.group(0)) in ALLOWED:
                    continue
                offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not offenders, "operator-lab identifiers in tracked files:\n" + "\n".join(offenders)
