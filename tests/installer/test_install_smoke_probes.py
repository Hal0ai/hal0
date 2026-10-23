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
    _gateway(tmp_path, _LOADED)
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode == 0, res.stderr


def test_probe_fails_on_non_json_content(tmp_path: Path) -> None:
    """The regression this probe exists for: a coherent chat reply ('Paris')
    is NOT structured output — the probe must fail on it."""
    _gateway(tmp_path, _LOADED, content="Paris")
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode == 1


def test_probe_fails_when_the_chat_request_fails(tmp_path: Path) -> None:
    _gateway(tmp_path, _LOADED, post_fails=True)
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode == 1


def test_update_check_invokes_hal0_update_check(tmp_path: Path) -> None:
    calls = tmp_path / "hal0.calls"
    _write_exec(tmp_path / "hal0", f'echo "$@" >> "{calls}"')
    res = _drive(tmp_path, "smoke_update_check")
    assert res.returncode == 0, res.stderr
    assert "update --check" in calls.read_text(encoding="utf-8")


def test_smoke_is_fail_soft_and_records_failures(tmp_path: Path) -> None:
    """Both probes failing must not abort the driver (exit 0) — install
    completes; failures land in SMOKE_FAILED for the summary box."""
    _gateway(tmp_path, _LOADED, post_fails=True)
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
    res = _drive(tmp_path, f'HINDSIGHT_UNIT_DST="{tmp_path}/missing"\nsmoke_memory_engine_version')
    assert res.returncode == 0, res.stderr


# ── #2131: a failed probe must name the unbound brain slot, not just fail ───
#
# The 0.9.8 → 1.0.0 upgrade ended at a bare "structured-output probe failed"
# while the cause — the default brain model downloaded but never bound — was
# one command away from a fix and mentioned nowhere. The probe's failure branch
# now asks `brain_model --check-binding`, which prints the remediation when
# (and only when) that is actually the shape on disk.


def test_probe_failure_asks_for_the_brain_binding_diagnosis() -> None:
    func = _extract("run_post_install_smoke")
    assert "hal0.install.brain_model --check-binding" in func, (
        "a failed structured-output probe must check for the #2131 unbound-brain "
        "shape instead of leaving 'check hal0 status' as the only lead"
    )


def _fake_venv(tmp_path: Path, body: str) -> Path:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    _write_exec(venv / "bin" / "python", body)
    return venv


def test_probe_failure_prints_the_brain_remediation_when_one_is_offered(
    tmp_path: Path,
) -> None:
    venv = _fake_venv(tmp_path, "echo 'bind it: hal0 slot edit brain --model lfm2.5-2.6b'")
    _gateway(tmp_path, _LOADED, post_fails=True)
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, f'VENV_DIR="{venv}"\nrun_post_install_smoke\nexit 0')
    assert res.returncode == 0, res.stderr
    assert "hal0 slot edit brain --model lfm2.5-2.6b" in res.stderr


def test_probe_failure_says_nothing_extra_when_the_slot_is_bound(tmp_path: Path) -> None:
    """`--check-binding` prints nothing when the binding is fine, so a
    fresh-install probe failure reads exactly as it did before."""
    venv = _fake_venv(tmp_path, "exit 0")
    _gateway(tmp_path, _LOADED, post_fails=True)
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, f'VENV_DIR="{venv}"\nrun_post_install_smoke\nexit 0')
    assert res.returncode == 0, res.stderr
    assert "slot edit brain" not in res.stderr


def test_probe_failure_survives_a_missing_venv(tmp_path: Path) -> None:
    """The hint runs under `set -euo pipefail` while the installer is ALREADY
    reporting a failure — an absent venv (or an unset VENV_DIR) must not turn
    a warning into an aborted install."""
    _gateway(tmp_path, _LOADED, post_fails=True)
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(
        tmp_path,
        'set -e\nrun_post_install_smoke\nprintf "%s\\n" "${SMOKE_FAILED[@]}"\nexit 0',
    )
    assert res.returncode == 0, res.stderr
    assert "structured-output" in res.stdout


def test_smoke_failures_surface_in_the_summary_box() -> None:
    """The summary builder must fold SMOKE_FAILED into SUMMARY_LINES so an
    rc-validate run catches failures without scrolling the warn stream."""
    assert "SMOKE_FAILED" in _TEXT.split("SUMMARY_LINES=(", 1)[1], (
        "SMOKE_FAILED is never referenced after the summary box starts building"
    )


# ── Skip gate: no bound model, or a cold slot the probe would load ─────────


def _slot(name: str, model: str | None, status: str, kind: str = "local") -> dict[str, object]:
    return {"name": name, "kind": kind, "type": "llm", "model_default": model, "status": status}


_LOADED = [_slot("utility", "small-model", "ready")]


def _gateway(
    tmp_path: Path,
    slots: list[dict[str, object]] | str,
    content: str = '{"ok": true}',
    *,
    post_fails: bool = False,
    post_rc: int = 7,
) -> Path:
    """Fake curl: ``/api/slots`` answers ``slots`` (a raw string is sent as
    is); every other request is logged, so a test can assert the chat POST
    never happened, and answers ``content`` or fails like a dead gateway."""
    posts = tmp_path / "posts.log"
    raw = slots if isinstance(slots, str) else json.dumps(slots)
    (tmp_path / "slots.json").write_text(raw, encoding="utf-8")
    (tmp_path / "reply.json").write_text(_chat_completion(content), encoding="utf-8")
    _write_exec(
        tmp_path / "curl",
        'for a in "$@"; do\n'
        f'  case "$a" in */api/slots) cat "{tmp_path}/slots.json"; exit 0 ;; esac\n'
        "done\n"
        f'echo post >> "{posts}"\n'
        + (f"exit {post_rc}" if post_fails else f'cat "{tmp_path}/reply.json"'),
    )
    return posts


_SHOW = 'info() { echo "INFO: $*"; }\nrun_post_install_smoke\nprintf "FAILED:%s\\n" "${SMOKE_FAILED[@]}"\nexit 0'


def test_fresh_install_with_no_bound_model_skips_without_failing(tmp_path: Path) -> None:
    """First-run seeding binds no model by design, so the probe cannot pass
    on a fresh box; it must not count as a verify failure."""
    posts = _gateway(tmp_path, [_slot("agent", None, "offline"), _slot("utility", "", "offline")])
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "structured-output probe skipped — no model bound yet" in res.stdout
    assert "FAILED:structured-output" not in res.stdout
    assert not posts.exists(), "the chat probe must not be sent when nothing is bound"


def test_a_cold_target_slot_is_skipped_not_loaded(tmp_path: Path) -> None:
    """hal0/utility resolves to a bound utility slot even when it is cold and
    the request would load it; a warm agent does not change the target."""
    posts = _gateway(
        tmp_path,
        [_slot("agent", "big-model", "ready"), _slot("utility", "small-model", "offline")],
    )
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "structured-output probe skipped — slot 'utility' is not loaded (offline)" in res.stdout
    assert "FAILED:structured-output" not in res.stdout
    assert not posts.exists(), "the probe must not cold-load a slot"


def test_a_loaded_anchor_is_probed_when_utility_has_no_model(tmp_path: Path) -> None:
    posts = _gateway(
        tmp_path, [_slot("utility", None, "offline"), _slot("agent", "big-model", "serving")]
    )
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode == 0, res.stderr
    assert posts.read_text(encoding="utf-8").count("post") == 1


def test_a_loaded_target_that_returns_prose_still_fails(tmp_path: Path) -> None:
    _gateway(tmp_path, [_slot("utility", "small-model", "ready")], content="Paris")
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "FAILED:structured-output" in res.stdout


def test_remote_upstreams_do_not_count_as_a_bound_slot(tmp_path: Path) -> None:
    """/api/slots also lists synthetic remote upstreams; hal0/utility never
    resolves to one, so a box with only remotes has no model bound."""
    remote = {
        "name": "openrouter",
        "kind": "remote",
        "type": None,
        "model": "x",
        "status": "serving",
    }
    posts = _gateway(tmp_path, [remote, _slot("agent", None, "offline")])
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode == 2
    assert not posts.exists()


def test_a_target_slot_in_error_fails_without_sending_the_request(tmp_path: Path) -> None:
    """A crash-looping slot is exactly what the probe exists to catch, so it
    is a failure, not a skip; sending the request would only retry the load."""
    posts = _gateway(tmp_path, [_slot("utility", "small-model", "error")])
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "FAILED:structured-output" in res.stdout
    assert "slot 'utility' is in error" in res.stderr
    assert not posts.exists()


def test_a_stale_runtime_model_id_does_not_count_as_bound(tmp_path: Path) -> None:
    """A row's model_id outlives a cleared binding; the resolver reads only the
    configured model, so hal0/utility goes to the warm agent and is probed."""
    stale = {**_slot("utility", None, "offline"), "model_id": "old-model"}
    posts = _gateway(tmp_path, [stale, _slot("agent", "big-model", "ready")])
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode == 0, res.stderr
    assert posts.read_text(encoding="utf-8").count("post") == 1


def test_an_unknown_state_skips_as_cold(tmp_path: Path) -> None:
    """Fail closed: only DISPATCHABLE_STATES (ready, serving, idle) are probed.
    A state this list does not know might mean the request would load it."""
    posts = _gateway(tmp_path, [_slot("utility", "small-model", "rebooting")])
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "slot 'utility' is not loaded (rebooting)" in res.stdout
    assert "FAILED:structured-output" not in res.stdout
    assert not posts.exists()


def test_an_unreachable_slot_list_skips_without_a_chat_post(tmp_path: Path) -> None:
    """Fail closed: when the slot list cannot be read the probe cannot tell
    whether it would load a slot, so it skips. API reachability is covered by
    the earlier verify steps, so this is not a failure."""
    calls = tmp_path / "curl.calls"
    _write_exec(tmp_path / "curl", f'echo "$@" >> "{calls}"\nexit 22')
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "structured-output probe skipped — could not read the slot list" in res.stdout
    assert "FAILED:structured-output" not in res.stdout
    assert "/v1/chat/completions" not in calls.read_text(encoding="utf-8")


def test_a_non_json_slot_list_skips_without_a_chat_post(tmp_path: Path) -> None:
    posts = _gateway(tmp_path, "<html>401 Unauthorized</html>")
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "structured-output probe skipped — could not read the slot list" in res.stdout
    assert "FAILED:structured-output" not in res.stdout
    assert not posts.exists()


# ── #2952: a curl timeout is advisory, not a Verify FAILED ─────────────────


def test_probe_timeout_returns_3(tmp_path: Path) -> None:
    _gateway(tmp_path, _LOADED, post_fails=True, post_rc=28)
    res = _drive(tmp_path, "smoke_structured_output")
    assert res.returncode == 3


def test_probe_timeout_warns_without_recording_a_failure(tmp_path: Path) -> None:
    _gateway(tmp_path, _LOADED, post_fails=True, post_rc=28)
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "structured-output probe timed out" in res.stderr
    assert "FAILED" not in res.stderr
    assert "FAILED:structured-output" not in res.stdout
    assert "FAILED:" not in res.stdout.replace("FAILED:\n", "")


def test_probe_http_error_still_fails(tmp_path: Path) -> None:
    _gateway(tmp_path, _LOADED, post_fails=True, post_rc=22)
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "structured-output probe FAILED" in res.stderr
    assert "FAILED:structured-output" in res.stdout


def test_probe_bad_json_still_fails_in_the_run(tmp_path: Path) -> None:
    _gateway(tmp_path, _LOADED, content="Paris")
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "structured-output probe FAILED" in res.stderr
    assert "FAILED:structured-output" in res.stdout


def test_probe_success_in_the_run_is_clean(tmp_path: Path) -> None:
    _gateway(tmp_path, _LOADED)
    _write_exec(tmp_path / "hal0", "exit 0")
    res = _drive(tmp_path, _SHOW)
    assert "structured-output probe ok" in res.stdout
    assert "FAILED:structured-output" not in res.stdout
    assert "structured-output" not in res.stderr
