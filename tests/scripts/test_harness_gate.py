"""Contract tests for the δ harness exit gate (scripts/harness-gate.py, #2349).

``make harness`` installs with ``--dev``, where ``hal0 slot load`` cannot
start a systemd slot unit: ``tests/harness/runtime-test.sh`` records
``runtime-slot-load`` as ``deferred`` and skips ``runtime-chat-roundtrip``.
``scripts/harness.sh`` used to count only ``fail`` rows, so such a run ended
with a bare ``harness OK`` although no slot was ever loaded.

The gate now treats a deferred slot-lifecycle group as a failure unless the
run opts in (``HAL0_HARNESS_ALLOW_DEFERRED=1`` / ``--allow-deferred``), and an
allowed run names the unverified rows instead of printing a bare OK.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE = _REPO_ROOT / "scripts" / "harness-gate.py"
_HARNESS = _REPO_ROOT / "scripts" / "harness.sh"


def _row(name: str, status: str, tier: str = "runtime") -> dict[str, object]:
    return {"name": name, "status": status, "duration_ms": 1, "detail": "", "tier": tier}


def _report(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    statuses = [r["status"] for r in rows]
    report = {
        "_schema": "hal0.harness-report.v1",
        "generated": 1,
        "tiers": [],
        "summary": {
            "total": len(rows),
            **{s: statuses.count(s) for s in ("pass", "fail", "skip", "deferred")},
        },
        "rows": rows,
    }
    path = tmp_path / "harness.json"
    path.write_text(json.dumps(report))
    return path


def _gate(
    report: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    run_env = {k: v for k, v in os.environ.items() if k != "HAL0_HARNESS_ALLOW_DEFERRED"}
    run_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(_GATE), str(report), *args],
        capture_output=True,
        text=True,
        env=run_env,
        check=False,
    )


_DEV_ROWS = [
    _row("cli-version", "pass", tier="cli"),
    _row("runtime-slot-create", "pass"),
    _row("runtime-slot-load", "deferred"),
    _row("runtime-chat-roundtrip", "skip"),
    # Not in the slot-lifecycle group: stays tolerated as before.
    _row("cleanup-uninstall", "deferred", tier="cleanup"),
]


def test_clean_run_prints_bare_ok(tmp_path: Path) -> None:
    rows = [
        _row("runtime-slot-load", "pass"),
        _row("runtime-chat-roundtrip", "pass"),
        _row("cleanup-uninstall", "deferred", tier="cleanup"),
    ]
    res = _gate(_report(tmp_path, rows))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "harness OK"


def test_deferred_slot_lifecycle_fails_by_default(tmp_path: Path) -> None:
    res = _gate(_report(tmp_path, _DEV_ROWS))
    assert res.returncode == 1
    out = res.stdout + res.stderr
    assert "harness FAILED" in out
    assert "harness OK" not in out
    assert "runtime-slot-load (deferred)" in out
    assert "runtime-chat-roundtrip (skip)" in out
    # Tells the operator how to opt in and where real coverage lives.
    assert "HAL0_HARNESS_ALLOW_DEFERRED=1" in out
    assert "--allow-deferred" in out
    assert "make release-test" in out
    assert "#2377" in out
    # Deferred rows outside the slot-lifecycle group are not named.
    assert "cleanup-uninstall" not in out


@pytest.mark.parametrize(
    ("args", "env"),
    [
        (("--allow-deferred",), {}),
        ((), {"HAL0_HARNESS_ALLOW_DEFERRED": "1"}),
    ],
    ids=["flag", "env"],
)
def test_allowed_deferred_run_names_rows(
    tmp_path: Path, args: tuple[str, ...], env: dict[str, str]
) -> None:
    res = _gate(_report(tmp_path, _DEV_ROWS), *args, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout.strip()
    assert out.startswith("harness OK")
    assert out != "harness OK"
    assert "runtime-slot-load (deferred)" in out
    assert "runtime-chat-roundtrip (skip)" in out
    assert "not verified" in out


@pytest.mark.parametrize("value", ["0", "", "no"])
def test_env_opt_in_requires_exactly_one(tmp_path: Path, value: str) -> None:
    res = _gate(_report(tmp_path, _DEV_ROWS), env={"HAL0_HARNESS_ALLOW_DEFERRED": value})
    assert res.returncode == 1


def test_fail_rows_still_fail_even_when_deferred_allowed(tmp_path: Path) -> None:
    rows = [*_DEV_ROWS, _row("cli-config-validate", "fail", tier="cli")]
    res = _gate(_report(tmp_path, rows), "--allow-deferred")
    assert res.returncode == 1
    out = res.stdout + res.stderr
    assert "harness FAILED" in out
    assert "1 row(s) failed" in out


def test_chat_skip_without_deferred_load_is_not_gated(tmp_path: Path) -> None:
    # slot-load failed outright: the fail row already fails the run; the
    # skipped chat row is its consequence, not a deferral to opt into.
    rows = [_row("runtime-slot-load", "fail"), _row("runtime-chat-roundtrip", "skip")]
    res = _gate(_report(tmp_path, rows), "--allow-deferred")
    assert res.returncode == 1
    assert "not verified" not in res.stdout + res.stderr


def test_unknown_gate_argument_is_usage_error(tmp_path: Path) -> None:
    res = _gate(_report(tmp_path, _DEV_ROWS), "--bogus")
    assert res.returncode == 2


def _sandbox(tmp_path: Path, runtime_rows: list[dict[str, object]]) -> Path:
    """Copy the real harness.sh + its Python helpers into a hermetic tree.

    harness.sh derives every path from its own location, so stub tier
    scripts under ``<tmp>/tests/harness/`` stand in for the real installer,
    CLI, runtime, agents and cleanup tiers. Nothing is installed.
    """
    root = tmp_path / "repo"
    scripts = root / "scripts"
    harness_dir = root / "tests" / "harness"
    reports = harness_dir / "reports"
    scripts.mkdir(parents=True)
    reports.mkdir(parents=True)
    for name in ("harness.sh", "harness-report.py", "harness-gate.py"):
        (scripts / name).write_text((_REPO_ROOT / "scripts" / name).read_text())
    tier_rows: dict[str, list[dict[str, object]]] = {
        "installer": [_row("dev-install", "pass", tier="installer")],
        "cli": [_row("cli-version", "pass", tier="cli")],
        "runtime": runtime_rows,
        "agents": [],
        "cleanup": [_row("stop-api", "pass", tier="cleanup")],
    }
    scripts_by_tier = {
        "installer": "installer-test.sh",
        "cli": "cli-test.sh",
        "runtime": "runtime-test.sh",
        "agents": "agents-test.sh",
        "cleanup": "harness-cleanup.sh",
    }
    for tier, script in scripts_by_tier.items():
        payload = json.dumps({"summary": {}, "rows": tier_rows[tier]})
        (harness_dir / script).write_text(
            "#!/usr/bin/env bash\n"
            f"echo 'stub tier {tier} ran'\n"
            f'cat > "$(dirname "$0")/reports/{tier}.json" <<\'JSON\'\n{payload}\nJSON\n'
        )
    return scripts / "harness.sh"


def _harness(
    script: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    run_env = {k: v for k, v in os.environ.items() if k != "HAL0_HARNESS_ALLOW_DEFERRED"}
    run_env.update(env or {})
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=run_env,
        timeout=60,
        check=False,
    )


_DEV_RUNTIME = [
    _row("runtime-slot-create", "pass"),
    _row("runtime-slot-load", "deferred"),
    _row("runtime-chat-roundtrip", "skip"),
]


def test_harness_sh_fails_a_dev_run_with_deferred_slot_load(tmp_path: Path) -> None:
    res = _harness(_sandbox(tmp_path, _DEV_RUNTIME))
    assert "stub tier runtime ran" in res.stdout
    assert res.returncode == 1
    out = res.stdout + res.stderr
    assert "harness FAILED" in out
    assert "harness OK" not in out
    assert "runtime-slot-load (deferred)" in out
    # Report schema and row names are unchanged by the gate.
    report = json.loads(
        (tmp_path / "repo" / "tests" / "harness" / "reports" / "harness.json").read_text()
    )
    assert report["_schema"] == "hal0.harness-report.v1"
    assert report["summary"]["fail"] == 0
    assert report["summary"]["deferred"] == 1
    statuses = {r["name"]: r["status"] for r in report["rows"]}
    assert statuses["runtime-slot-load"] == "deferred"
    assert statuses["runtime-chat-roundtrip"] == "skip"


@pytest.mark.parametrize(
    ("args", "env"),
    [
        (("--allow-deferred",), {}),
        ((), {"HAL0_HARNESS_ALLOW_DEFERRED": "1"}),
    ],
    ids=["flag", "env"],
)
def test_harness_sh_allowed_run_is_qualified_ok(
    tmp_path: Path, args: tuple[str, ...], env: dict[str, str]
) -> None:
    res = _harness(_sandbox(tmp_path, _DEV_RUNTIME), *args, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    last = res.stdout.strip().splitlines()[-1]
    assert last.startswith("harness OK")
    assert last != "harness OK"
    assert "runtime-slot-load (deferred)" in last
    assert "runtime-chat-roundtrip (skip)" in last


def test_harness_sh_clean_run_is_bare_ok(tmp_path: Path) -> None:
    rows = [_row("runtime-slot-load", "pass"), _row("runtime-chat-roundtrip", "pass")]
    res = _harness(_sandbox(tmp_path, rows))
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.strip().splitlines()[-1] == "harness OK"


def test_harness_sh_rejects_unknown_flag_before_running_tiers(tmp_path: Path) -> None:
    res = _harness(_sandbox(tmp_path, _DEV_RUNTIME), "--bogus")
    assert res.returncode == 2
    assert "--allow-deferred" in res.stderr
    assert "stub tier" not in res.stdout
