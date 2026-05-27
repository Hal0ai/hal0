#!/usr/bin/env bash
# tests/harness/agents-test.sh
#
# δ-harness regression tier for #346 (agents: hermes uninstall — API
# status string lies + provision.json survives).
#
# Drives the actual production AgentManager class against a tmp
# HAL0_HOME prefix — exercises real code paths (manager.py +
# api/routes/agents.py) without requiring a real hermes wheel + venv +
# Lemonade stack. The driver stubs in tests/agents/test_manager.py
# cover the same surface at the pure-unit tier; this tier covers the
# end-to-end install-corrupt-uninstall flow + the install-uninstall
# round-trip the issue's δ-harness acceptance criteria call out.
#
# Scenarios (one row each):
#
#   agents-install-corrupt-uninstall
#       AgentManager.install(hermes) → stamp a provision.json under
#       state_root → delete the seed TOML out from under it (simulates
#       the "lost seed" half-uninstall the issue traces) → uninstall →
#       assert removed=True AND all three on-disk witnesses (seed,
#       data_dir, state dir) are gone.
#
#   agents-roundtrip-no-orphans
#       install → uninstall → install → uninstall, and after each
#       uninstall every witness is gone (no orphan data_dir,
#       state dir, or seed left behind).
#
#   agents-hermes-venv-teardown (#348)
#       Drive HermesDriver.uninstall() directly against a stamped
#       provision.json that records a venv path → assert the venv
#       directory is gone.
#
#   agents-hermes-context-link-teardown (#349)
#       Drive HermesDriver.uninstall() against a provision.json whose
#       context_link phase records HERMES.md + AGENTS.md under
#       $HAL0_HOME/etc/hal0/ → assert both files are gone.
#
# Driver mocking: scenarios #346 patch the manager's _driver_for() to
# return a stub so we don't need the Hermes upstream installed on the
# harness host. The #348 + #349 scenarios DO NOT stub — they instantiate
# HermesDriver directly because the cleanup paths under test live in
# that driver, not the manager.
#
# Exit: 0 if both rows pass, non-zero (count of fails) otherwise.

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

REPORT="${SCRIPT_DIR}/reports/agents.json"
harness_init "agents" "${REPORT}"

# Pick up the dev-install venv if installer-test.sh ran; fall back to
# the system `hal0`/`python3` on PATH.
HANDOFF="${SCRIPT_DIR}/reports/.api-handoff"
if [[ -r "${HANDOFF}" ]]; then
    # shellcheck disable=SC1090
    source "${HANDOFF}"
fi
: "${HAL0_HOME:=}"
PY_BIN=""
if [[ -n "${HAL0_HOME}" && -x "${HAL0_HOME}/.venv/bin/python" ]]; then
    PY_BIN="${HAL0_HOME}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="$(command -v python3)"
fi

if [[ -z "${PY_BIN}" ]]; then
    add_row "preflight-python" "fail" "0" "no python interpreter on PATH"
    harness_write_report || true
    exit 1
fi

# Verify hal0 is importable; if not, the unit-level tests already cover
# the contract and this tier defers.
if ! "${PY_BIN}" -c 'import hal0.agents.manager' >/dev/null 2>&1; then
    add_row "agents-install-corrupt-uninstall" "deferred" "0" \
        "hal0 package not importable from ${PY_BIN}; unit tests in tests/agents/test_manager.py cover the same contract"
    add_row "agents-roundtrip-no-orphans" "deferred" "0" \
        "hal0 package not importable from ${PY_BIN}; unit tests cover the same contract"
    harness_write_report || true
    exit 0
fi

log_step "Agent uninstall regression rows (#346) — python=${PY_BIN}"

# Shared Python driver script. Takes the scenario name as argv[1], the
# tmp prefix as argv[2]; prints "OK" on success, anything else means
# failure (the row's detail captures the diagnostic).
DRIVER="${SCRIPT_DIR}/reports/agents-driver.py"
cat >"${DRIVER}" <<'PY'
"""δ-harness driver for #346 + #348 + #349. See tests/harness/agents-test.sh."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

from hal0.agents import manager as mgr_mod
from hal0.agents.manager import AgentManager


class _StubDriver:
    """Records call counts without touching the real Hermes upstream."""

    name = "hermes"

    def __init__(self) -> None:
        self.installs = 0
        self.uninstalls = 0
        self._installed = False

    def install(self, *, bearer_token: str | None = None) -> None:
        self.installs += 1
        self._installed = True

    def uninstall(self) -> None:
        self.uninstalls += 1
        self._installed = False

    def status(self) -> str:
        return "installed" if self._installed else "broken"


def _patch_driver(stub: _StubDriver) -> None:
    def _fake(name: str) -> _StubDriver:
        if name != "hermes":
            from hal0.agents.manager import AgentNotFoundError

            raise AgentNotFoundError(name)
        return stub

    mgr_mod._driver_for = _fake  # type: ignore[assignment]


def _seed_state_dir(mgr: AgentManager, name: str) -> Path:
    """Mirror hermes_provision.py writing provision.json + logs."""
    state_dir = mgr._state_dir(name)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "provision.json").write_text('{"phases":{}}\n')
    logs = state_dir / "provision-logs"
    logs.mkdir(exist_ok=True)
    (logs / "preflight.log").write_text("ok\n")
    return state_dir


def _make_mgr(prefix: Path) -> AgentManager:
    return AgentManager(
        etc_root=prefix / "etc",
        var_root=prefix / "var",
        state_root=prefix / "state",
    )


def scenario_install_corrupt_uninstall(prefix: Path) -> None:
    """install → corrupt seed → uninstall → assert all three paths gone."""
    stub = _StubDriver()
    _patch_driver(stub)
    mgr = _make_mgr(prefix)

    rec = mgr.install("hermes")
    seed = Path(rec.config_path)
    data = Path(rec.data_dir)
    state = _seed_state_dir(mgr, "hermes")

    # Sanity: every witness present.
    assert seed.exists(), f"seed missing post-install: {seed}"
    assert data.exists(), f"data_dir missing post-install: {data}"
    assert state.exists(), f"state_dir missing post-stamp: {state}"
    # The seed should parse as TOML on a happy install.
    tomllib.loads(seed.read_text())

    # Corrupt the registry: remove the seed by hand. This is the exact
    # shape the issue traces — a prior partial uninstall lost the seed
    # but left data + state dirs in place.
    seed.unlink()
    assert not seed.exists()
    assert data.exists()
    assert state.exists()

    removed = mgr.uninstall("hermes")
    if removed is not True:
        raise AssertionError(
            f"uninstall returned {removed!r}; expected True. #346: API "
            f"reports status='not_installed' even though data+state were "
            f"on disk."
        )

    if data.exists():
        raise AssertionError(f"data dir survived uninstall: {data}")
    if state.exists():
        raise AssertionError(
            f"state dir survived uninstall: {state} — #346 acceptance "
            f"criterion #1 unmet"
        )


def scenario_roundtrip_no_orphans(prefix: Path) -> None:
    """install → uninstall → install → uninstall; no orphans after each round."""
    stub = _StubDriver()
    _patch_driver(stub)
    mgr = _make_mgr(prefix)

    for round_idx in range(2):
        rec = mgr.install("hermes")
        seed = Path(rec.config_path)
        data = Path(rec.data_dir)
        state = _seed_state_dir(mgr, "hermes")
        assert seed.exists() and data.exists() and state.exists()
        assert mgr.installed_names() == ["hermes"]

        removed = mgr.uninstall("hermes")
        if removed is not True:
            raise AssertionError(
                f"round {round_idx}: uninstall returned {removed!r}, expected True"
            )

        orphans = [p for p in (seed, data, state) if p.exists()]
        if orphans:
            raise AssertionError(
                f"round {round_idx}: orphans survived uninstall: {orphans}"
            )
        if mgr.installed_names():
            raise AssertionError(
                f"round {round_idx}: installed_names() = "
                f"{mgr.installed_names()!r} after uninstall; expected []"
            )


def _stamp_provision_json(
    state_dir: Path,
    *,
    venv: Path | None = None,
    rendered_paths: list[Path] | None = None,
) -> None:
    """Mirror what hermes_provision writes — minimal shape sufficient for the
    HermesDriver uninstall reader."""
    state_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "agent_id": "hermes-agent",
        "phases": {},
    }
    if venv is not None:
        payload["venv"] = str(venv)
    if rendered_paths is not None:
        payload["phases"]["context_link"] = {
            "status": "ok",
            "details": {
                "rendered": {
                    p.name: {"path": str(p), "sha256": "0" * 64} for p in rendered_paths
                },
                "links": [],
                "warnings": [],
            },
        }
    (state_dir / "provision.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def scenario_hermes_venv_teardown(prefix: Path) -> None:
    """#348: HermesDriver.uninstall() removes the recorded venv directory."""
    os.environ["HAL0_HOME"] = str(prefix)
    # Late import so HAL0_HOME is in effect when paths resolve.
    from hal0.agents.hermes import HermesDriver
    from hal0.config import paths as _paths

    venv = _paths.var_lib() / "venvs" / "hermes"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/usr/bin/env python\n")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    state_dir = _paths.var_lib() / "state" / "agents" / "hermes"
    _stamp_provision_json(state_dir, venv=venv)

    if not venv.exists():
        raise AssertionError(f"pre-condition: venv must exist at {venv}")

    drv = HermesDriver(prober=lambda: True)
    drv.uninstall()

    if venv.exists():
        raise AssertionError(
            f"#348 unmet: venv survived HermesDriver.uninstall() at {venv}"
        )


def scenario_hermes_context_link_teardown(prefix: Path) -> None:
    """#349: HermesDriver.uninstall() removes /etc/hal0/AGENTS.md + HERMES.md."""
    os.environ["HAL0_HOME"] = str(prefix)
    from hal0.agents.hermes import HermesDriver
    from hal0.config import paths as _paths

    etc_hal0 = _paths.etc()
    etc_hal0.mkdir(parents=True, exist_ok=True)
    agents_md = etc_hal0 / "AGENTS.md"
    hermes_md = etc_hal0 / "HERMES.md"
    agents_md.write_text("# Agents\n", encoding="utf-8")
    hermes_md.write_text("# Hermes\n", encoding="utf-8")
    state_dir = _paths.var_lib() / "state" / "agents" / "hermes"
    _stamp_provision_json(state_dir, rendered_paths=[agents_md, hermes_md])

    if not (agents_md.exists() and hermes_md.exists()):
        raise AssertionError("pre-condition: AGENTS.md + HERMES.md must exist")

    drv = HermesDriver(prober=lambda: True)
    drv.uninstall()

    survivors = [p for p in (agents_md, hermes_md) if p.exists()]
    if survivors:
        raise AssertionError(
            f"#349 unmet: context_link docs survived uninstall: {survivors}"
        )


def main(argv: list[str]) -> int:
    scenario_name = argv[1]
    prefix = Path(argv[2])
    if prefix.exists():
        shutil.rmtree(prefix)
    prefix.mkdir(parents=True)

    try:
        if scenario_name == "install-corrupt-uninstall":
            scenario_install_corrupt_uninstall(prefix)
        elif scenario_name == "roundtrip-no-orphans":
            scenario_roundtrip_no_orphans(prefix)
        elif scenario_name == "hermes-venv-teardown":
            scenario_hermes_venv_teardown(prefix)
        elif scenario_name == "hermes-context-link-teardown":
            scenario_hermes_context_link_teardown(prefix)
        else:
            print(f"unknown scenario: {scenario_name}", file=sys.stderr)
            return 2
    finally:
        shutil.rmtree(prefix, ignore_errors=True)

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
PY

run_scenario() {
    local row_name="$1" scenario="$2"
    local log="${SCRIPT_DIR}/reports/agents-${row_name}.log"
    local tmp_prefix="${SCRIPT_DIR}/reports/.tmp-agents-${scenario}-$$"
    local start; start=$(start_ms)
    set +e
    "${PY_BIN}" "${DRIVER}" "${scenario}" "${tmp_prefix}" >"${log}" 2>&1
    local rc=$?
    set -e
    if [[ "${rc}" -eq 0 ]] && grep -q '^OK$' "${log}"; then
        add_row "${row_name}" "pass" "$(since_ms "${start}")" "${scenario} ok"
    else
        local detail
        detail="$(tail -n1 "${log}" 2>/dev/null | tr -d '\n')"
        add_row "${row_name}" "fail" "$(since_ms "${start}")" "exit=${rc}: ${detail:-(no stderr)}"
    fi
}

run_scenario "agents-install-corrupt-uninstall"   "install-corrupt-uninstall"
run_scenario "agents-roundtrip-no-orphans"        "roundtrip-no-orphans"
run_scenario "agents-hermes-venv-teardown"        "hermes-venv-teardown"
run_scenario "agents-hermes-context-link-teardown" "hermes-context-link-teardown"

log_step "Write report"
harness_write_report || true
log_info "report: ${REPORT}"
exit 0
