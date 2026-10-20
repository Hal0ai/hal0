"""A re-run must not re-enable units the operator disabled (#2807).

Extracts the helper and the enable sites from install.sh and runs them
against a stub ``systemctl`` that records every call.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / "installer" / "install.sh").read_text(encoding="utf-8")


def _slice(start: str, end: str) -> str:
    s = TEXT.index(start)
    return TEXT[s : TEXT.index(end, s)]


HELPER = _slice("unit_operator_disabled() {", "DEV_MODE=0")
BENCH_CAPTURE = _slice("        _bench_worker_off=0;", "        for _bench_unit in")
BENCH_ENABLE = _slice(
    '        if [[ "${DEV_MODE}" -eq 0 ]]; then\n            systemctl daemon-reload',
    '        info "wrote ${ETC_DIR}/bench',
)
GATEWAY_CAPTURE = _slice("        _gateway_off=0", "        # m1 fix")
GATEWAY_ENABLE = _slice(
    '            if [[ "${_gateway_off}" -eq 1 ]]',
    '        else\n            warn "hermes-gateway unit not',
)

BENCH_BODY = BENCH_CAPTURE + BENCH_ENABLE
GATEWAY_BODY = GATEWAY_CAPTURE + "systemctl daemon-reload\n" + GATEWAY_ENABLE


def _run(tmp_path: Path, body: str, states: dict[str, str]) -> list[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    cases = "\n".join(f'    "{u}") echo "{s}" ;;' for u, s in states.items())
    stub = bindir / "systemctl"
    stub.write_text(
        f'#!/bin/bash\necho "$*" >> "{log}"\n'
        f'if [[ "$1" == "is-enabled" ]]; then case "$2" in\n{cases}\n'
        "    *) exit 1 ;; esac; fi\nexit 0\n"
    )
    stub.chmod(0o755)
    script = (
        'info() { echo "INFO: $*"; }\nwarn() { echo "WARN: $*"; }\n'
        "wait_active() { return 0; }\nDEV_MODE=0\nETC_DIR=/etc/hal0\n"
        "install() { :; }\nUNIT_DIR=/nonexistent\n" + HELPER + body
    )
    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"},
        check=True,
        capture_output=True,
        text=True,
    )
    return log.read_text().splitlines() if log.exists() else []


def test_bench_disabled_units_stay_disabled(tmp_path: Path) -> None:
    calls = _run(
        tmp_path,
        BENCH_BODY,
        {"hal0-bench-worker.service": "disabled", "hal0-bench.timer": "masked"},
    )
    assert not [c for c in calls if c.startswith("enable")]


def test_bench_fresh_install_still_enables(tmp_path: Path) -> None:
    calls = _run(tmp_path, BENCH_BODY, {})
    assert "enable --now hal0-bench.timer" in calls
    assert "enable --now hal0-bench-worker.service" in calls


def test_bench_only_disabled_one_is_respected(tmp_path: Path) -> None:
    calls = _run(tmp_path, BENCH_BODY, {"hal0-bench.timer": "disabled"})
    assert "enable --now hal0-bench.timer" not in calls
    assert "enable --now hal0-bench-worker.service" in calls


def test_gateway_disabled_is_not_reenabled(tmp_path: Path) -> None:
    calls = _run(tmp_path, GATEWAY_BODY, {"hermes-gateway.service": "disabled"})
    assert "enable --now hermes-gateway.service" not in calls
    assert "disable --now hermes-gateway.service" in calls


def test_gateway_fresh_install_enables(tmp_path: Path) -> None:
    calls = _run(tmp_path, GATEWAY_BODY, {})
    assert "enable --now hermes-gateway.service" in calls
