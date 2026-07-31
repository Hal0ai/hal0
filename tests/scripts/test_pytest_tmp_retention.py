"""#1490: pin the tmp_path retention settings that keep /tmp bounded.

pytest's default policy keeps the last 3 generations of every `tmp_path`-family
fixture dir under `<tmpdir>/pytest-of-<user>/`, and this suite's HAL0_HOME /
model-registry fixtures write multi-GB payloads into them. On this project's
dev boxes `/tmp` is routinely a small tmpfs, so a handful of runs is enough to
ENOSPC the whole host — including, ironically, the test runner's own output
capture. `tmp_path_retention_count = 1` + `tmp_path_retention_policy = "failed"`
(pytest >=7.3) means a passed test's fixture tree is gone the moment the run
that produced it ends; only genuinely-failed runs' trees survive, which is
also the only case anyone actually wants to inspect one.

This test only pins the config values are present and correctly typed in
pyproject.toml — it does not exercise pytest's own retention machinery
(that's pytest's problem, not this repo's), matching how
tests/installer/test_preflight_python_floor.py pins the `requires-python`
floor rather than re-testing CPython's own version parsing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _pytest_ini_options() -> dict[str, object]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["tool"]["pytest"]["ini_options"]


def test_tmp_path_retention_count_bounds_generations() -> None:
    opts = _pytest_ini_options()
    assert opts.get("tmp_path_retention_count") == 1, (
        "tmp_path_retention_count must stay at 1 (or lower) — see #1490. "
        "pytest's default of 3 is what let /tmp fill up in the first place."
    )


def test_tmp_path_retention_policy_keeps_only_failed() -> None:
    opts = _pytest_ini_options()
    assert opts.get("tmp_path_retention_policy") == "failed", (
        "tmp_path_retention_policy must stay 'failed' — see #1490. A passed "
        "test's fixture tree is disposable the instant the run ends; only a "
        "failure is worth the disk to go inspect."
    )
