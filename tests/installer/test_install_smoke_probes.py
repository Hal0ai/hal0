"""Post-install smoke probes: structured output + `hal0 update --check` (#2066).

Two blind spots this release cycle proved expensive:

* The verify step probed plain chat coherence but never a ``json_object``
  request — so extraction was silently dead on every fresh install of the
  :0820 lineage while the install summary stayed green.
* It never dry-ran ``hal0 update --check`` — which is how #2052 shipped
  through three RCs: install green, update path broken, discovered at the
  NEXT release.

Probe shape is load-bearing (live-verified on ct151 during rc.9 validation):

* the structured-output probe must go via the GATEWAY (``:$HAL0_PORT/v1``),
  never direct-to-slot — direct-to-slot 200s mask gateway-path failures
  (rc.7's #2020);
* the body must be kwarg-ABSENT (model + messages + response_format only) —
  kwarg-present bodies mask template-branch differences between backends.

Both probes are fail-soft: they warn loudly and land in the summary box,
but never abort a finished install.

Functions are extracted from install.sh and driven through fakes on PATH
(the ``test_api_restart_on_upgrade.py`` shim technique) — real daemons are
not available in CI.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
_TEXT = INSTALL_SH.read_text(encoding="utf-8")


def _extract(func_name: str) -> str:
    """Pull one top-level ``name() { ... }`` body out of install.sh."""
    start = _TEXT.index(f"{func_name}()")
    end = _TEXT.index("\n}\n", start) + 3
    return _TEXT[start:end]


def _write_exec(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _chat_completion(content: str) -> str:
    """A minimal OpenAI-shaped chat-completion reply carrying ``content``."""
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]})


# ── Contract: probe shape ──────────────────────────────────────────────────


def test_structured_output_probe_exists_and_targets_the_gateway() -> None:
    """The probe must hit /v1/chat/completions on the gateway port — a
    direct-to-slot probe would 200 while the gateway path is broken (#2020)."""
    func = _extract("smoke_structured_output")
    assert "/v1/chat/completions" in func
    assert "${HAL0_PORT}" in func, "probe must go through the gateway port, not a slot port"


def test_structured_output_body_is_kwarg_absent_json_object() -> None:
    """Body carries response_format json_object and NOTHING else beyond
    model + messages — extra kwargs mask template-branch differences."""
    func = _extract("smoke_structured_output")
    m = re.search(r"body='(\{.*?\})'", func, re.DOTALL)
    assert m, "expected a single-quoted JSON body literal in smoke_structured_output"
    body = json.loads(m.group(1))
    assert body.get("response_format") == {"type": "json_object"}
    assert set(body) == {"model", "messages", "response_format"}, (
        f"probe body must be kwarg-absent; got extra keys: {set(body)}"
    )


def test_update_check_probe_exists() -> None:
    func = _extract("smoke_update_check")
    assert "update --check" in func


# ── Behavior: driven through fakes ─────────────────────────────────────────


def _drive(tmp_path: Path, script_body: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "drive.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -uo pipefail\n"
        "info() { :; }\n"
        'warn() { echo "WARN: $*" >&2; }\n'
        "HAL0_PORT=8080\nPY=python3\nHINDSIGHT_PIN=9.9.9\n"
        f"HAL0_BIN={tmp_path}/hal0\n"
        f"{_extract('smoke_structured_output')}\n"
        f"{_extract('smoke_update_check')}\n"
        f"{_extract('smoke_memory_engine_version')}\n"
        f"{_extract('run_post_install_smoke')}\n"
        "SMOKE_FAILED=()\n"
        f"{script_body}\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=30, check=False
    )


def test_probe_passes_when_gateway_returns_parseable_json(tmp_path: Path) -> None:
    reply = _chat_completion('{"ok": true}')
    _write_exec(tmp_path / "curl", f"echo '{reply}'")
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode == 0, res.stderr


def test_probe_fails_on_non_json_content(tmp_path: Path) -> None:
    """The regression this probe exists for: a coherent chat reply ('Paris')
    is NOT structured output — the probe must fail on it."""
    reply = _chat_completion("Paris")
    _write_exec(tmp_path / "curl", f"echo '{reply}'")
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode != 0


def test_probe_fails_when_gateway_is_down(tmp_path: Path) -> None:
    _write_exec(tmp_path / "curl", "exit 7")
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode != 0


def test_update_check_invokes_hal0_update_check(tmp_path: Path) -> None:
    calls = tmp_path / "hal0.calls"
    _write_exec(tmp_path / "hal0", f'echo "$@" >> "{calls}"')
    res = _drive(tmp_path, "smoke_update_check")
    assert res.returncode == 0, res.stderr
    assert "update --check" in calls.read_text(encoding="utf-8")


def test_smoke_is_fail_soft_and_records_failures(tmp_path: Path) -> None:
    """Both probes failing must not abort the driver (exit 0) — install
    completes; failures land in SMOKE_FAILED for the summary box."""
    _write_exec(tmp_path / "curl", "exit 7")
    _write_exec(tmp_path / "hal0", "exit 1")
    res = _drive(
        tmp_path,
        'run_post_install_smoke\nprintf "%s\\n" "${SMOKE_FAILED[@]}"\nexit 0',
    )
    assert res.returncode == 0, res.stderr
    assert "structured-output" in res.stdout
    assert "update-check" in res.stdout
    assert "WARN:" in res.stderr, "fail-soft still has to be LOUD"


def test_memory_engine_probe_passes_on_pin_match(tmp_path: Path) -> None:
    unit = tmp_path / "hindsight-api.service"
    unit.write_text("[Service]\n", encoding="utf-8")
    _write_exec(tmp_path / "curl", 'echo \'{"api_version": "9.9.9"}\'')
    res = _drive(tmp_path, f'HINDSIGHT_UNIT_DST="{unit}"\nsmoke_memory_engine_version')
    assert res.returncode == 0, res.stderr


def test_memory_engine_probe_fails_on_stale_engine(tmp_path: Path) -> None:
    unit = tmp_path / "hindsight-api.service"
    unit.write_text("[Service]\n", encoding="utf-8")
    _write_exec(tmp_path / "curl", 'echo \'{"api_version": "0.8.4"}\'')
    res = _drive(tmp_path, f'HINDSIGHT_UNIT_DST="{unit}"\nsmoke_memory_engine_version')
    assert res.returncode != 0


def test_memory_engine_probe_skips_when_engine_not_installed(tmp_path: Path) -> None:
    """No unit file (fresh box with HAL0_SKIP_HINDSIGHT, or engine never
    installed) — the probe must skip, not fail: curl faked dead proves it
    never even probes."""
    _write_exec(tmp_path / "curl", "exit 7")
    res = _drive(
        tmp_path, f'HINDSIGHT_UNIT_DST="{tmp_path}/missing"\nsmoke_memory_engine_version'
    )
    assert res.returncode == 0, res.stderr


def test_smoke_failures_surface_in_the_summary_box() -> None:
    """The summary builder must fold SMOKE_FAILED into SUMMARY_LINES so an
    rc-validate run catches failures without scrolling the warn stream."""
    assert "SMOKE_FAILED" in _TEXT.split("SUMMARY_LINES=(", 1)[1], (
        "SMOKE_FAILED is never referenced after the summary box starts building"
    )
