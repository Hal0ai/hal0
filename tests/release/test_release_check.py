"""Functional contracts for the non-publishing release preflight."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release-check.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _make_tree(
    tmp_path: Path, report: dict[str, object] | None = None
) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / "release-check.sh")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "hal0ai"\nversion = "1.0.0-alpha.1"\n', encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps({"toolbox_images": {"test": {"digest": "sha256:abc"}}}),
        encoding="utf-8",
    )
    (root / "uv.lock").write_bytes(b"fixture lock bytes\n")
    if report is not None:
        (root / "tests" / "release-gate-report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv.log"
    _write_executable(
        fake_bin / "uv",
        """#!/usr/bin/env bash
printf "%s|%s|%s\\n" "$PYTHONPATH" "$HAL0_HOME" "$*" >> "$UV_LOG"
if [[ "$*" != "run --locked "* ]]; then
    printf 'unexpected lock rewrite\\n' > uv.lock
fi
""",
    )
    _write_executable(fake_bin / "shellcheck", "#!/usr/bin/env bash\nexit 0\n")

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release-test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["UV_LOG"] = str(uv_log)
    env["HAL0_HOME"] = str(tmp_path / "must-not-be-used")
    return root, env


def _run(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / "release-check.sh"), *args],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _fresh_report(**summary_overrides: int) -> dict[str, object]:
    summary = {"total": 1, "pass": 1, "fail": 0, "skip": 0, "deferred": 0}
    summary.update(summary_overrides)
    return {"generated": int(time.time()), "summary": summary}


def test_help_and_unknown_argument_contract(tmp_path: Path) -> None:
    root, env = _make_tree(tmp_path)

    help_result = _run(root, env, "--help")
    bad_result = _run(root, env, "--unknown")

    assert help_result.returncode == 0
    assert "--local" in help_result.stdout
    assert "non-publishing" in help_result.stdout.lower()
    assert bad_result.returncode == 2
    assert "Unknown argument" in bad_result.stderr


def test_local_uses_isolated_repository_uv_and_skips_remote_report(tmp_path: Path) -> None:
    root, env = _make_tree(tmp_path)

    original_lock = (root / "uv.lock").read_bytes()

    result = _run(root, env, "--local")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "uv.lock").read_bytes() == original_lock
    output = result.stdout + result.stderr
    assert "LOCAL rehearsal" in output
    assert "skipping remote tier-" in output
    assert "release-gate report" in output
    assert "insufficient for release authorization" in output
    uv_calls = Path(env["UV_LOG"]).read_text(encoding="utf-8").splitlines()
    assert len(uv_calls) == 2
    homes = set()
    for call in uv_calls:
        pythonpath, hal0_home, command = call.split("|", 2)
        assert pythonpath == str(root / "src")
        assert hal0_home != env["HAL0_HOME"]
        assert "hal0-release-check." in hal0_home
        homes.add(hal0_home)
        assert command.startswith("run --locked ")
    assert len(homes) == 1
    assert not Path(next(iter(homes))).exists()
    assert any("run --locked pytest" in call for call in uv_calls)
    assert any("run --locked ruff check" in call for call in uv_calls)


@pytest.mark.parametrize(
    ("report", "expected_success"),
    [
        (None, False),
        ({**_fresh_report(), "generated": int(time.time()) - 25 * 3600}, False),
        (_fresh_report(**{"fail": 1, "pass": 0}), False),
        (_fresh_report(), True),
    ],
)
def test_nonlocal_requires_fresh_coherent_report(
    tmp_path: Path, report: dict[str, object] | None, expected_success: bool
) -> None:
    root, env = _make_tree(tmp_path, report)

    result = _run(root, env, "--dry-run")

    assert (result.returncode == 0) is expected_success
    output = result.stdout + result.stderr
    if expected_success:
        assert "release-gate report accepted" in output
    else:
        assert "release-gate report" in output.lower()
        assert "FAILED" in output


@pytest.mark.parametrize(
    ("summary", "rows", "expected_success"),
    [
        ({"total": 0, "pass": 0, "fail": 0, "skip": 0, "deferred": 0}, None, False),
        ({"total": 1, "pass": 0, "fail": 0, "skip": 1, "deferred": 0}, None, False),
        ({"total": 1, "pass": 0, "fail": 0, "skip": 0, "deferred": 1}, None, False),
        ({"total": 2, "pass": 1, "fail": 0, "skip": 0, "deferred": 0}, None, False),
        ({"total": 1, "pass": "1", "fail": 0, "skip": 0, "deferred": 0}, None, False),
        (
            {"total": 2, "pass": 1, "fail": 0, "skip": 1, "deferred": 0},
            [{"status": "pass"}, {"status": "skip"}],
            True,
        ),
        (
            {"total": 2, "pass": 1, "fail": 1, "skip": 0, "deferred": 0},
            [{"status": "pass"}, {"status": "fail"}],
            False,
        ),
        (
            {"total": 2, "pass": 1, "fail": 0, "skip": 1, "deferred": 0},
            [{"status": "pass"}, {"status": "deferred"}],
            False,
        ),
    ],
    ids=[
        "empty-report",
        "all-skipped",
        "all-deferred",
        "inconsistent-counts",
        "malformed-count",
        "pass-with-skip",
        "failed-row",
        "rows-summary-mismatch",
    ],
)
def test_tier_gamma_report_policy(
    tmp_path: Path,
    summary: dict[str, object],
    rows: list[dict[str, str]] | None,
    expected_success: bool,
) -> None:
    report: dict[str, object] = {"generated": int(time.time()), "summary": summary}
    if rows is not None:
        report["rows"] = rows
    root, env = _make_tree(tmp_path, report)

    result = _run(root, env, "--dry-run")
    output = result.stdout + result.stderr

    assert (result.returncode == 0) is expected_success, output
    if expected_success:
        assert "1 pass, 1 skip, 0 deferred; no failed rows" in output
        assert "all-pass" not in output.lower()
    else:
        assert "Release check FAILED" in output


def test_git_cleanliness_ignores_only_generated_paths(tmp_path: Path) -> None:
    root, env = _make_tree(tmp_path)
    generated = [
        root / ".pi" / "shepherd" / "index.json",
        root / ".pi-subagents" / "worker.log",
        root / "graphify-out" / "graph.json",
    ]
    for path in generated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated\n", encoding="utf-8")

    generated_result = _run(root, env, "--local")
    assert generated_result.returncode == 0, generated_result.stdout + generated_result.stderr
    assert "working tree clean (excluding generated state)" in generated_result.stdout

    source_dirt = root / "src" / "unexpected.py"
    source_dirt.write_text("dirty = True\n", encoding="utf-8")
    source_result = _run(root, env, "--local")

    assert source_result.returncode == 1
    assert "working tree is dirty" in source_result.stderr
