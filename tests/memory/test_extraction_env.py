"""Unit tests for the hindsight-api extraction-slot drop-in writer (ADR-0023).

``apply_extraction_slot`` writes a systemd drop-in pinning
``HINDSIGHT_API_LLM_MODEL=hal0/<slot>`` and restarts hindsight-api so the
engine's native extraction LLM follows the operator's chosen slot. The writer is
best-effort (returns a status dict rather than raising) so an unprivileged hal0
-api surfaces a partial result instead of 500ing.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from hal0.memory.extraction_env import (
    DROP_IN_PATH,
    EXTRACTION_FLOOR_ENV,
    EXTRACTION_MIN_CONTEXT_TOKENS,
    MEMORY_ALIAS,
    ExtractionLimits,
    apply_extraction_slot,
    clamp_to_parallel,
    concurrency_clamp_warning,
    drop_in_matches,
    effective_limits,
    extraction_floor,
    extraction_model_name,
    extraction_slot_parallel,
    memory_engine_target,
    render_drop_in,
    resolve_extraction_window,
)
from hal0.system.seam import SEAM_BIN, SystemCtlSeam


def _seam_recorder(rc: int = 0, stderr: str = ""):
    """Record ``(argv, stdin-body)`` for every seam invocation."""
    calls: list[tuple[list[str], str | None]] = []

    def _run(argv, **kwargs):
        calls.append((list(argv), kwargs.get("input")))
        done = subprocess.CompletedProcess(list(argv), rc, "", stderr)
        if rc and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, list(argv), "", stderr)
        return done

    return calls, _run


def _forbidden(*_a, **_k):  # pragma: no cover — a call here is the bug
    raise AssertionError("privileged work must route through the seam, not bare subprocess")


def test_render_drop_in_pins_the_memory_alias():
    """#2919: the drop-in no longer carries the slot. hindsight-api always
    calls the one virtual id ``hal0/memory``; hal0's /v1 resolves it from
    [memory.graph] at request time, so switching the engine between a local
    slot and an upstream model never rewrites this file or restarts the
    engine."""
    out = render_drop_in()
    assert MEMORY_ALIAS == "hal0/memory"
    assert "HINDSIGHT_API_LLM_MODEL=hal0/memory" in out
    assert "hal0/utility" not in out
    assert "[Service]" in out


def test_memory_engine_target_slot_and_upstream():
    from hal0.config.schema import MemoryGraphConfig

    assert memory_engine_target(MemoryGraphConfig(extraction_slot="utility")) == (
        "slot",
        "hal0/utility",
    )
    assert memory_engine_target(MemoryGraphConfig(extraction_slot="agent")) == (
        "slot",
        "hal0/agent",
    )
    # An upstream model wins over the slot while it is set.
    assert memory_engine_target(
        MemoryGraphConfig(extraction_slot="utility", extraction_model="MiniMax-M2.5")
    ) == ("upstream", "MiniMax-M2.5")


def test_drop_in_path_is_a_systemd_override():
    # The override lives in the hindsight-api drop-in dir so it layers over the
    # installer-owned base unit without hand-editing it.
    assert DROP_IN_PATH.name == "extraction-model.conf"
    assert "hindsight-api.service.d" in str(DROP_IN_PATH)


def test_apply_writes_drop_in_and_reports_status(monkeypatch, tmp_path: Path):
    # Redirect the drop-in to a tmp dir + inject a fake runner so the test never
    # touches /etc or the real service.
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    result = apply_extraction_slot("utility", seam=seam)
    ran = [c[0] for c in calls]

    assert result["error"] is None
    assert result["written"] is True
    assert result["daemon_reloaded"] is True
    assert result["restarted"] is True
    assert result["model"] == "hal0/memory"
    assert drop_in.read_text().count("HINDSIGHT_API_LLM_MODEL=hal0/memory") == 1
    # daemon-reload then restart, in order. Off the hal0 service account the
    # seam is a passthrough, so these are the bare argv.
    assert ran[0][:2] == ["systemctl", "daemon-reload"]
    assert ran[1] == ["systemctl", "restart", "hindsight-api.service"]


def test_apply_no_restart_skips_systemctl(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    result = apply_extraction_slot("agent", restart=False, seam=seam)
    assert result["written"] is True
    assert result["restarted"] is False
    assert result["error"] is None
    assert drop_in.exists()


def test_render_drop_in_includes_llm_timeout():
    # Default mirrors MemoryGraphConfig.llm_timeout_s (300s); explicit values
    # ride the same drop-in so one file owns both hindsight LLM env overrides.
    assert "HINDSIGHT_API_LLM_TIMEOUT=300" in render_drop_in()
    assert "HINDSIGHT_API_LLM_TIMEOUT=600" in render_drop_in(timeout_s=600)


# ── #1834: extraction limits ride the same drop-in ───────────────────────────
#
# A shared inference slot with no concurrency cap, no completion-token cap and
# a requeueing retry ladder never drains its retain queue on a slow box. The
# caps are hindsight-api's own env knobs; hal0 owns their values in
# [memory.graph] and renders them into the one drop-in it already manages.


def test_extraction_limits_defaults_are_conservative():
    from hal0.memory.extraction_env import ExtractionLimits

    limits = ExtractionLimits()
    assert limits.max_concurrent == 1
    assert limits.max_tokens == 16384
    assert limits.llm_retries == 1
    assert limits.task_retries == 2
    assert limits.retry_backoff_s == 120


def test_render_drop_in_carries_the_extraction_limits():
    from hal0.memory.extraction_env import ExtractionLimits

    out = render_drop_in()
    assert "Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT=1\n" in out
    assert "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT=1" in out
    assert "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS=16384" in out
    assert "HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES=1" in out
    assert "HINDSIGHT_API_WORKER_MAX_RETRIES=2" in out
    assert "HINDSIGHT_API_WORKER_TASK_RETRY_BACKOFF_SECONDS=120" in out

    tuned = ExtractionLimits(
        max_concurrent=2, max_tokens=8192, llm_retries=0, task_retries=5, retry_backoff_s=30
    )
    out = render_drop_in(limits=tuned)
    assert "Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT=2\n" in out
    assert "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT=2" in out
    assert "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS=8192" in out
    assert "HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES=0" in out
    assert "HINDSIGHT_API_WORKER_MAX_RETRIES=5" in out
    assert "HINDSIGHT_API_WORKER_TASK_RETRY_BACKOFF_SECONDS=30" in out


def test_extraction_limits_from_config_mirrors_the_schema():
    from hal0.config.schema import MemoryGraphConfig
    from hal0.memory.extraction_env import ExtractionLimits

    cfg = MemoryGraphConfig(
        extraction_max_concurrent=3,
        extraction_max_tokens=6000,
        extraction_llm_retries=2,
        extraction_task_retries=1,
        extraction_retry_backoff_s=45,
    )
    assert ExtractionLimits.from_config(cfg) == ExtractionLimits(
        max_concurrent=3, max_tokens=6000, llm_retries=2, task_retries=1, retry_backoff_s=45
    )
    assert ExtractionLimits.from_config(MemoryGraphConfig()) == ExtractionLimits()


def test_drop_in_matches_sees_a_limits_change(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee
    from hal0.memory.extraction_env import ExtractionLimits

    path = tmp_path / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)
    path.write_text(render_drop_in(300), encoding="utf-8")
    assert drop_in_matches(300) is True
    assert drop_in_matches(300, limits=ExtractionLimits(max_concurrent=2)) is False


def test_drop_in_matches_detects_retain_only_concurrency(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)
    current = render_drop_in()
    path.write_text(
        "".join(
            line
            for line in current.splitlines(keepends=True)
            if not line.startswith("Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT=")
        ),
        encoding="utf-8",
    )
    assert drop_in_matches() is False
    path.write_text(current, encoding="utf-8")
    assert drop_in_matches() is True


def test_apply_threads_limits_into_drop_in_and_status(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee
    from hal0.memory.extraction_env import ExtractionLimits

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    limits = ExtractionLimits(max_concurrent=2, max_tokens=5000)
    result = apply_extraction_slot("agent", restart=False, limits=limits)
    assert result["limits"] == {
        "max_concurrent": 2,
        "max_tokens": 5000,
        "llm_retries": 1,
        "task_retries": 2,
        "retry_backoff_s": 120,
    }
    text = drop_in.read_text()
    assert "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT=2" in text
    assert "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS=5000" in text


def test_apply_threads_timeout_into_drop_in_and_status(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    result = apply_extraction_slot("agent", timeout_s=900, restart=False)
    assert result["timeout_s"] == 900
    text = drop_in.read_text()
    assert "HINDSIGHT_API_LLM_MODEL=hal0/memory" in text
    assert "HINDSIGHT_API_LLM_TIMEOUT=900" in text


# ── #1641: the unprivileged hal0-api path ────────────────────────────────────
#
# hal0-api runs as the unprivileged ``hal0`` service user (User=hal0), and
# /etc/systemd/system/hindsight-api.service.d is root:root. Writing the drop-in
# directly is EPERM and a bare ``systemctl restart`` escalates through polkit
# ("Interactive authentication required"), so on every standard install the
# propagation silently no-opped while hal0.toml recorded the new slot. Every
# privileged step must route through the hal0-systemctl seam instead.


def test_apply_routes_every_step_through_the_seam_as_the_hal0_user(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)
    monkeypatch.setattr(ee.subprocess, "run", _forbidden)

    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_extraction_slot("utility", timeout_s=420, seam=seam)

    assert result["error"] is None
    assert result["written"] is True
    assert result["daemon_reloaded"] is True
    assert result["restarted"] is True
    # Never written directly — the root side owns the literal path.
    assert not drop_in.exists()
    assert [c[0] for c in calls] == [
        ["sudo", "-n", SEAM_BIN, "write-hindsight-dropin"],
        ["sudo", "-n", SEAM_BIN, "daemon-reload"],
        ["sudo", "-n", SEAM_BIN, "svc-restart", "hindsight"],
    ]
    body = calls[0][1]
    assert "HINDSIGHT_API_LLM_MODEL=hal0/memory" in body
    assert "HINDSIGHT_API_LLM_TIMEOUT=420" in body


def test_apply_surfaces_a_seam_write_failure(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)
    monkeypatch.setattr(ee.subprocess, "run", _forbidden)

    _calls, run = _seam_recorder(rc=1, stderr="sudo: a password is required")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_extraction_slot("utility", seam=seam)

    assert result["written"] is False
    assert result["restarted"] is False
    assert result["error"] and "sudo" in result["error"]


def test_apply_bounds_the_privileged_write(monkeypatch, tmp_path: Path):
    """A stalled sudo must not park an API worker thread forever."""
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    seen: list[object] = []

    def _run(argv, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    seam = SystemCtlSeam(run=_run, is_hal0_user=lambda: True)
    apply_extraction_slot("utility", seam=seam)

    # write, daemon-reload, restart — every one bounded.
    assert seen == [ee._SYSTEMCTL_TIMEOUT_S] * 3


def test_apply_names_the_stale_wrapper_as_the_cause(monkeypatch, tmp_path: Path):
    """`hal0 update` never refreshes ${LIB_DIR}/bin, so new Python can meet an
    old wrapper. Exit 64 / `bad cmd` is a fixable operator condition — say so."""
    import hal0.memory.extraction_env as ee

    monkeypatch.setattr(ee, "DROP_IN_PATH", tmp_path / "extraction-model.conf")

    _calls, run = _seam_recorder(rc=64, stderr="hal0-systemctl: bad cmd: write-hindsight-dropin")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_extraction_slot("utility", seam=seam)

    assert result["written"] is False
    assert "install.sh" in result["error"]


def test_apply_does_not_blame_the_wrapper_for_other_failures(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    monkeypatch.setattr(ee, "DROP_IN_PATH", tmp_path / "extraction-model.conf")

    _calls, run = _seam_recorder(rc=1, stderr="sudo: a password is required")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    assert "install.sh" not in (apply_extraction_slot("utility", seam=seam)["error"] or "")


def test_drop_in_matches_true_when_content_is_identical(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    path.write_text(render_drop_in(300))
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)

    assert drop_in_matches(300) is True


def test_drop_in_matches_false_on_a_pre_alias_slot_drop_in(monkeypatch, tmp_path: Path):
    """A box upgraded from the slot-carrying drop-in (``hal0/utility``) still
    has that file on disk — it must read as drift so the next enabled PUT
    rewrites it to the alias and restarts the engine once."""
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    path.write_text(render_drop_in(300).replace("hal0/memory", "hal0/utility"), encoding="utf-8")
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)

    assert drop_in_matches(300) is False


def test_drop_in_matches_false_when_missing(monkeypatch, tmp_path: Path):
    """#1682 review: a host where the privileged write previously failed
    silently (pre-seam bug) has hal0.toml recording a slot that was NEVER
    actually applied — no drop-in file at all. That must read as "does not
    match", not error out, so the caller knows to (re)propagate."""
    import hal0.memory.extraction_env as ee

    monkeypatch.setattr(ee, "DROP_IN_PATH", tmp_path / "never-written.conf")

    assert drop_in_matches(300) is False


def test_drop_in_matches_false_on_undecodable_bytes(monkeypatch, tmp_path: Path):
    """#1717 review: ``Path.read_text()`` raises ``UnicodeDecodeError`` (NOT
    an ``OSError``) on malformed/tampered content. That must still read as
    "does not match" so the caller reconciles by rewriting the file, instead
    of an otherwise-idempotent graph PUT 500ing on a decode failure."""
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    path.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)

    assert drop_in_matches(300) is False


def test_drop_in_matches_reads_utf8_regardless_of_locale(monkeypatch, tmp_path: Path):
    """#1717 review: the drop-in (and the em dash in its header comment) is
    always written UTF-8 by SystemCtlSeam — the comparison must decode it as
    UTF-8 explicitly rather than via ``Path.read_text()``'s locale-dependent
    default, or a byte-for-byte correct file can misread as drift under
    e.g. ``LC_ALL=C`` and trigger a needless rewrite + restart on every
    enabled graph PUT."""
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    # The rendered template contains a real em dash (U+2014) in its header.
    content = render_drop_in(300)
    assert "—" in content
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)

    real_read_text = Path.read_text

    def _locale_sensitive_read_text(self, *args, **kwargs):
        # Fail unless the caller passed an explicit encoding — the same
        # failure mode as a real ASCII-locale default.
        if kwargs.get("encoding") != "utf-8" and (len(args) < 1 or args[0] != "utf-8"):
            raise UnicodeDecodeError("ascii", b"\xe2\x80\x94", 0, 1, "ordinal not in range(128)")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _locale_sensitive_read_text)

    assert drop_in_matches(300) is True


def test_apply_writes_directly_when_not_the_hal0_user(monkeypatch, tmp_path: Path):
    """Root / dev / CI keeps the pre-seam behaviour: a direct atomic write."""
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    result = apply_extraction_slot("agent", seam=seam)

    assert result["error"] is None
    assert drop_in.read_text().count("HINDSIGHT_API_LLM_MODEL=hal0/memory") == 1
    assert [c[0] for c in calls] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "hindsight-api.service"],
    ]


# ── #2918: the LLM the engine really runs ────────────────────────────────────
#
# systemd applies EnvironmentFile= after every Environment= line, drop-ins
# included, so PROVIDER/BASE_URL/MODEL lines an operator adds to
# /etc/hal0/hindsight-llm.env silently beat hal0's target. hal0 must report
# what the engine loads, not what it configured.


def _no_process() -> None:
    """The engine's /proc environ is not readable (engine down, other uid)."""
    return None


_UNIT_ENV = {
    "HINDSIGHT_API_LLM_PROVIDER": "openai",
    "HINDSIGHT_API_LLM_BASE_URL": "http://127.0.0.1:8080/v1",
    "HINDSIGHT_API_LLM_MODEL": "hal0/memory",
    "HINDSIGHT_API_LLM_API_KEY": "unit-placeholder",
}


def test_effective_engine_llm_key_only_file_is_not_an_override(tmp_path: Path):
    from hal0.memory.extraction_env import effective_engine_llm

    env_file = tmp_path / "hindsight-llm.env"
    env_file.write_text("HINDSIGHT_API_LLM_API_KEY=supersecret-box-key\n")
    out = effective_engine_llm(env_file=env_file, unit_env=_UNIT_ENV, read_process_env=_no_process)
    assert out == {
        "provider": "openai",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "hal0/memory",
        "overridden": False,
        "override_file": None,
        "override_keys": [],
        "source": "unit+file",
    }
    assert "supersecret" not in repr(out)


def test_effective_engine_llm_reports_a_provider_override(tmp_path: Path):
    from hal0.memory.extraction_env import effective_engine_llm

    env_file = tmp_path / "hindsight-llm.env"
    env_file.write_text(
        "HINDSIGHT_API_LLM_PROVIDER=minimax\n"
        "HINDSIGHT_API_LLM_BASE_URL=https://api.minimax.io/v1\n"
        "HINDSIGHT_API_LLM_MODEL=MiniMax-M2.5\n"
        "HINDSIGHT_API_LLM_API_KEY=supersecret-cloud-key\n"
    )
    out = effective_engine_llm(env_file=env_file, unit_env=_UNIT_ENV, read_process_env=_no_process)
    assert out["provider"] == "minimax"
    assert out["base_url"] == "https://api.minimax.io/v1"
    assert out["model"] == "MiniMax-M2.5"
    assert out["overridden"] is True
    assert out["override_file"] == str(env_file)
    assert out["override_keys"] == [
        "HINDSIGHT_API_LLM_BASE_URL",
        "HINDSIGHT_API_LLM_MODEL",
        "HINDSIGHT_API_LLM_PROVIDER",
    ]
    assert "supersecret" not in repr(out)


def test_effective_engine_llm_without_file_uses_unit_env(tmp_path: Path):
    from hal0.memory.extraction_env import effective_engine_llm

    out = effective_engine_llm(
        env_file=tmp_path / "missing.env", unit_env=_UNIT_ENV, read_process_env=_no_process
    )
    assert out["model"] == "hal0/memory"
    assert out["overridden"] is False
    assert out["override_file"] is None


def test_effective_engine_llm_tolerates_an_unreadable_unit(tmp_path: Path):
    """``systemctl show`` failing (no systemd, a dev shell) degrades to Nones,
    never an exception on the status route."""
    from hal0.memory.extraction_env import effective_engine_llm

    out = effective_engine_llm(
        env_file=tmp_path / "missing.env", show_unit_env=lambda: {}, read_process_env=_no_process
    )
    assert out == {
        "provider": None,
        "base_url": None,
        "model": None,
        "overridden": False,
        "override_file": None,
        "override_keys": [],
        "source": "unit",
    }


# ── the running engine's environ is the first source of truth (#2926 follow-up) ─
#
# On CT105 /etc/hal0/hindsight-llm.env is 0600 root:hal0 — hal0-api cannot read
# it — and the status said "not overridden" while the engine ran MiniMax. The
# engine runs as the same uid as hal0-api, so its /proc/<pid>/environ IS
# readable: that is what it really loaded, with no need to open the key file.

_SECRET = "sk-should-never-leave-the-process"


def test_effective_engine_llm_reads_the_running_process_first(tmp_path: Path):
    from hal0.memory.extraction_env import effective_engine_llm

    unreadable = tmp_path / "hindsight-llm.env"  # exists, never opened on this path
    unreadable.write_text("HINDSIGHT_API_LLM_API_KEY=x\n")
    unreadable.chmod(0)
    process = {
        "HINDSIGHT_API_LLM_PROVIDER": "minimax",
        "HINDSIGHT_API_LLM_BASE_URL": "https://api.minimax.io/v1",
        "HINDSIGHT_API_LLM_MODEL": "MiniMax-M2.5",
    }
    out = effective_engine_llm(
        env_file=unreadable, unit_env=_UNIT_ENV, read_process_env=lambda: process
    )
    assert out["source"] == "process"
    assert out["model"] == "MiniMax-M2.5"
    assert out["provider"] == "minimax"
    assert out["overridden"] is True
    assert out["override_keys"] == [
        "HINDSIGHT_API_LLM_BASE_URL",
        "HINDSIGHT_API_LLM_MODEL",
        "HINDSIGHT_API_LLM_PROVIDER",
    ]
    assert out["override_file"] == str(unreadable)


def test_effective_engine_llm_process_drift_without_an_env_file_is_not_blamed_on_it(tmp_path: Path):
    """The engine still has hal0/utility while the unit already says
    hal0/memory (a drop-in written but the restart failed, or a daemon-reload
    without a restart). No env file exists, so the difference must not be
    reported as an override of it — the "still pinned, save once" advice
    applies instead."""
    from hal0.memory.extraction_env import effective_engine_llm

    process = {
        "HINDSIGHT_API_LLM_PROVIDER": "openai",
        "HINDSIGHT_API_LLM_BASE_URL": "http://127.0.0.1:8080/v1",
        "HINDSIGHT_API_LLM_MODEL": "hal0/utility",
    }
    out = effective_engine_llm(
        env_file=tmp_path / "absent.env", unit_env=_UNIT_ENV, read_process_env=lambda: process
    )
    assert out["source"] == "process"
    assert out["model"] == "hal0/utility"
    assert out["overridden"] is False
    assert out["override_file"] is None
    assert out["override_keys"] == []


def test_effective_engine_llm_process_matching_the_unit_is_not_an_override(tmp_path: Path):
    from hal0.memory.extraction_env import effective_engine_llm

    process = {k: v for k, v in _UNIT_ENV.items() if k != "HINDSIGHT_API_LLM_API_KEY"}
    out = effective_engine_llm(
        env_file=tmp_path / "x.env", unit_env=_UNIT_ENV, read_process_env=lambda: process
    )
    assert out["source"] == "process"
    assert out["overridden"] is False
    assert out["override_keys"] == []
    assert out["override_file"] is None


def test_effective_engine_llm_unreadable_file_without_process_is_unknown(tmp_path: Path):
    """Neither the process nor the file can be read: "can't tell", never
    "not overridden"."""
    import os

    import pytest

    from hal0.memory.extraction_env import effective_engine_llm

    if os.geteuid() == 0:
        pytest.skip("root reads a 0000 file")
    env_file = tmp_path / "hindsight-llm.env"
    env_file.write_text("HINDSIGHT_API_LLM_MODEL=MiniMax-M2.5\n")
    env_file.chmod(0)
    try:
        out = effective_engine_llm(
            env_file=env_file, unit_env=_UNIT_ENV, read_process_env=_no_process
        )
    finally:
        env_file.chmod(0o600)
    assert out["overridden"] is None
    assert out["source"] == "unit"
    assert out["override_file"] == str(env_file)
    assert out["model"] == "hal0/memory"  # the unit's value, explicitly unverified


def test_engine_process_env_keeps_only_the_three_target_keys(tmp_path: Path, monkeypatch):
    """The raw environ holds the API key and everything else; only the three
    target keys may come out, and no failure may carry the buffer."""
    import hal0.memory.extraction_env as ee

    raw = (
        f"HOME=/var/lib/hal0\0HINDSIGHT_API_LLM_API_KEY={_SECRET}\0"
        "HINDSIGHT_API_LLM_PROVIDER=minimax\0HINDSIGHT_API_LLM_MODEL=MiniMax-M2.5\0"
        f"HINDSIGHT_API_RETAIN_LLM_API_KEY={_SECRET}\0garbage-without-equals\0"
    ).encode()
    environ = tmp_path / "environ"
    environ.write_bytes(raw)
    monkeypatch.setattr(ee, "_engine_main_pid", lambda: 4242)
    monkeypatch.setattr(ee, "_proc_environ_path", lambda pid: environ)
    got = ee._read_engine_process_env()
    assert got == {
        "HINDSIGHT_API_LLM_PROVIDER": "minimax",
        "HINDSIGHT_API_LLM_MODEL": "MiniMax-M2.5",
    }
    assert _SECRET not in repr(got)
    payload = ee.effective_engine_llm(
        env_file=tmp_path / "missing.env",
        unit_env=_UNIT_ENV,
        read_process_env=ee._read_engine_process_env,
    )
    assert _SECRET not in repr(payload)


def test_engine_process_env_failure_never_carries_the_buffer(tmp_path: Path, monkeypatch, caplog):
    import hal0.memory.extraction_env as ee

    monkeypatch.setattr(ee, "_engine_main_pid", lambda: 4242)

    class _Boom:
        def read_bytes(self):
            raise PermissionError(13, f"denied {_SECRET}")

    monkeypatch.setattr(ee, "_proc_environ_path", lambda pid: _Boom())
    caplog.set_level("DEBUG")
    assert ee._read_engine_process_env() is None  # unreadable → None, no raise
    assert _SECRET not in caplog.text


def test_engine_process_env_none_when_the_engine_is_not_running(monkeypatch):
    import hal0.memory.extraction_env as ee

    monkeypatch.setattr(ee, "_engine_main_pid", lambda: None)
    assert ee._read_engine_process_env() is None


def test_parse_systemctl_environment_value():
    """``systemctl show -p Environment --value`` prints one space-separated
    line of KEY=VALUE pairs (values may be quoted when they carry spaces)."""
    from hal0.memory.extraction_env import _parse_systemctl_environment

    raw = (
        "HOME=/var/lib/hal0/memory/hindsight HINDSIGHT_API_LLM_MODEL=hal0/memory "
        '"HINDSIGHT_API_LLM_EXTRA_BODY={\\"a\\": 1}" HINDSIGHT_API_LLM_TIMEOUT=300\n'
    )
    env = _parse_systemctl_environment(raw)
    assert env["HINDSIGHT_API_LLM_MODEL"] == "hal0/memory"
    assert env["HINDSIGHT_API_LLM_TIMEOUT"] == "300"
    assert env["HOME"] == "/var/lib/hal0/memory/hindsight"


# ── #1903: extraction-slot context-window preflight ─────────────────────────
#
# Hindsight's native extraction call is dispatched to ``hal0/<extraction_slot>``
# regardless of the hal0-side enable toggle (reporting-only on this engine).
# Nothing ever preflighted that dispatch's window before this — an undersized
# resolved slot used to answer /add with HTTP 200 + a document id and then
# either drop the retain (Hindsight's own "Context size has been exceeded")
# or persist the extraction prompt's own scaffolding as fact. These pin the
# resolution logic reused from #1877's `resolve_anchor_window`.


def test_extraction_model_name_matches_the_drop_in_spelling():
    assert extraction_model_name("utility") == "hal0/utility"
    assert extraction_model_name("agent") == "hal0/agent"


def test_resolve_extraction_window_below_floor_on_the_repro_shape(tmp_path: Path):
    """The reported ct151-cpu-fresh shape: extraction slot resolves to a live 4096-token
    window — clearly under the extraction prompt's own footprint."""
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)

    assert window.verdict == "below_floor"
    assert window.effective == 4096
    assert window.floor == EXTRACTION_MIN_CONTEXT_TOKENS
    assert window.slot == "utility"


def test_resolve_extraction_window_ok_when_the_slot_clears_the_floor(tmp_path: Path):
    entry = {"id": "hal0/utility", "context_length": 32768}
    catalog = [{"id": "utility", "context_length": 32768}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)

    assert window.verdict == "ok"


def test_resolve_extraction_window_unknown_when_the_catalog_has_no_evidence(tmp_path: Path):
    """No advertised context anywhere — the caller must treat this as
    "cannot prove", never as a silent pass (#1831's lesson, reused here)."""
    window = resolve_extraction_window("utility", entry=None, catalog=[], slots_dir=tmp_path)

    assert window.verdict == "unknown"


def test_resolve_extraction_window_uses_a_dedicated_floor_not_hermes(tmp_path: Path):
    """The extraction floor is the prompt's own footprint, not Hermes' much
    larger MINIMUM_CONTEXT_LENGTH — an extraction slot deliberately sized
    well below 64,000 (typical for a small local utility model) must not be
    flagged just because it would fail Hermes' anchor check."""
    entry = {"id": "hal0/utility", "context_length": 16384}
    catalog = [{"id": "utility", "context_length": 16384}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)

    assert window.verdict == "ok"
    assert window.floor < 64_000


def test_below_floor_message_names_memory_extraction_not_hermes(tmp_path: Path):
    """Finding 2 on PR #1917: the parent ``AnchorWindow.message`` is
    hard-coded for Hermes' 64,000-token anchor preflight. The 503 an operator
    sees for a refused memory write must talk about memory extraction — the
    right subsystem, the right floor semantics, the right failure scope —
    while keeping the resolver's numbers, slot, and fix command."""
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)
    msg = window.message()

    assert window.verdict == "below_floor"
    assert "Hermes" not in msg
    assert "EVERY turn" not in msg
    assert "memory" in msg
    assert "extraction" in msg
    assert "4,096" in msg
    assert f"{EXTRACTION_MIN_CONTEXT_TOKENS:,}" in msg
    assert "slot 'utility'" in msg


def test_below_floor_message_includes_the_fix_command_for_a_binding_profile_window(
    tmp_path: Path, tmp_hal0_home: str
):
    """The window is the slot profile's ``ctx_size`` (ADR-0029): the message
    names the profile and its ``ctx_size`` and the fix edits the profile."""
    profiles = Path(tmp_hal0_home) / "etc" / "hal0" / "profiles.toml"
    profiles.parent.mkdir(parents=True, exist_ok=True)
    profiles.write_text('[profile.tiny-ctx]\nflags = ""\nctx_size = 4096\n', encoding="utf-8")
    (tmp_path / "utility.toml").write_text('name = "utility"\nprofile = "tiny-ctx"\n')
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)
    msg = window.message()

    assert window.profile == "tiny-ctx"
    assert window.ceiling == 4096
    assert window.ceiling_is_binding
    assert "profile 'tiny-ctx' sets ctx_size = 4,096" in msg
    assert "raise ctx_size to 8192 on profile 'tiny-ctx'" in msg
    assert "hal0 slot restart utility" in msg
    assert "[model].context_size" not in msg
    assert "hal0 slot edit utility --ctx-size" not in msg


def test_below_floor_message_blames_the_model_when_the_profile_sets_no_ctx_size(tmp_path: Path):
    (tmp_path / "utility.toml").write_text('name = "utility"\nprofile = "chat"\n')
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)
    msg = window.message()

    assert window.profile == "chat"
    assert window.ceiling is None
    assert "profile 'chat' sets no ctx_size" in msg
    assert "only advertises 4,096" in msg
    assert "defaults.context_size" not in msg
    assert "hal0 slot edit utility --model <model-id>" in msg


def test_unknown_and_ok_messages_are_extraction_specific(tmp_path: Path):
    unknown = resolve_extraction_window("utility", entry=None, catalog=[], slots_dir=tmp_path)
    ok = resolve_extraction_window(
        "utility",
        entry={"id": "hal0/utility", "context_length": 32768},
        catalog=[{"id": "utility", "context_length": 32768}],
        slots_dir=tmp_path,
    )

    assert "Hermes" not in unknown.message()
    assert "Hermes" not in ok.message()
    assert "memory extraction" in unknown.message()
    assert "memory extraction" in ok.message()


# ── HAL0_MEMORY_EXTRACTION_FLOOR override (PR #1917 review, finding 7) ──────


def test_extraction_floor_defaults_without_the_env_var(monkeypatch):
    monkeypatch.delenv(EXTRACTION_FLOOR_ENV, raising=False)
    assert extraction_floor() == (EXTRACTION_MIN_CONTEXT_TOKENS, "hal0:extraction-prompt-floor")


def test_extraction_floor_env_override_is_honoured(monkeypatch, tmp_path: Path):
    """The floor is a documented estimate hal0 cannot measure; an operator
    whose engine's prompt is genuinely smaller must have a knob that doesn't
    require resizing a slot."""
    monkeypatch.setenv(EXTRACTION_FLOOR_ENV, "4000")

    assert extraction_floor() == (4000, f"env:{EXTRACTION_FLOOR_ENV}")

    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]
    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)

    assert window.verdict == "ok"
    assert window.floor == 4000


def test_extraction_floor_garbage_override_falls_back(monkeypatch):
    """A typo'd override must not 500 every write — it is ignored."""
    for garbage in ("8k", "-1", "0", "  "):
        monkeypatch.setenv(EXTRACTION_FLOOR_ENV, garbage)
        floor, source = extraction_floor()
        assert floor == EXTRACTION_MIN_CONTEXT_TOKENS
        assert source == "hal0:extraction-prompt-floor"


def test_explicit_floor_argument_still_wins_over_the_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(EXTRACTION_FLOOR_ENV, "4000")
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window(
        "utility", entry=entry, catalog=catalog, floor=8192, slots_dir=tmp_path
    )

    assert window.verdict == "below_floor"
    assert window.floor == 8192


def test_invalid_floor_override_warns_once_not_per_write(monkeypatch):
    """Codex on PR #1917: extraction_floor() runs on the memory-write hot
    path, so a persistent typo'd override must not emit a journal warning on
    every auto-retain — once per distinct value is enough."""
    from hal0.memory import extraction_env as mod

    monkeypatch.setattr(mod, "_floor_override_warned", set())
    monkeypatch.setenv(EXTRACTION_FLOOR_ENV, "8k")
    warnings: list[str] = []
    monkeypatch.setattr(mod.log, "warning", lambda event, **kw: warnings.append(event))

    for _ in range(3):
        assert extraction_floor() == (EXTRACTION_MIN_CONTEXT_TOKENS, "hal0:extraction-prompt-floor")

    assert warnings == ["hal0.memory.extraction_floor_override_invalid"]


def test_restart_hindsight_background_uses_seam_restart_verb() -> None:
    """#2448: the post-rotate restart goes through ``systemctl restart
    hindsight-api.service`` (the seam routes it to ``svc-restart hindsight``)."""
    import subprocess

    from hal0.memory import extraction_env as ee

    calls: list[tuple[str, ...]] = []

    class _Seam:
        def systemctl(self, *args: str, **kw: object) -> None:
            calls.append(args)

    assert ee.restart_hindsight_background(seam=_Seam()) is True  # type: ignore[arg-type]
    assert calls == [("systemctl", "restart", "hindsight-api.service")]

    class _Failing:
        def systemctl(self, *args: str, **kw: object) -> None:
            raise subprocess.CalledProcessError(
                1, list(args), stderr="sudo: a password is required"
            )

    # Never raises -- a failed restart must not fail the caller.
    assert ee.restart_hindsight_background(seam=_Failing()) is False  # type: ignore[arg-type]


# ── #2925: the shipped unit already calls the alias ──────────────────────────


def test_shipped_hindsight_unit_defaults_to_the_memory_alias():
    """A fresh box has no drop-in until the first graph PUT; the installer-owned
    unit must already point the engine at hal0/memory, so the engine model
    picker takes effect on the next call without that one-time repair."""
    import re

    unit = Path(__file__).resolve().parents[2] / "installer" / "systemd" / "hindsight-api.service"
    models = re.findall(r"^Environment=HINDSIGHT_API_LLM_MODEL=(\S+)$", unit.read_text(), re.M)
    assert models == [MEMORY_ALIAS]
    from hal0.config.schema import MemoryGraphConfig

    default_concurrency = MemoryGraphConfig().extraction_max_concurrent
    for name in ("HINDSIGHT_API_LLM_MAX_CONCURRENT", "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT"):
        values = re.findall(rf"^Environment={name}=(\d+)$", unit.read_text(), re.M)
        assert values == [str(default_concurrency)]
    # …and it is the same line the drop-in writes, so the two never disagree.
    assert f"Environment=HINDSIGHT_API_LLM_MODEL={MEMORY_ALIAS}" in render_drop_in()
    rendered = render_drop_in()
    assert f"Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT={default_concurrency}" in rendered
    assert f"Environment=HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT={default_concurrency}" in rendered


# ── retain wall limit (#3214) ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("timeout_s", "llm_retries", "expected"),
    [
        (300, 1, 3600),  # defaults: engine's own 1 h floor
        (300, 5, 5400),  # three worst-case calls of 1800 s
        (30, 0, 3600),  # fast slot never drops below the floor
        (3600, 1, 14400),  # the #3214 box: 3 x 7200 capped at 4 h
        (3600, 5, 25200),  # per-call budget 21600 exceeds the cap: per_call + timeout
    ],
)
def test_retain_wall_timeout_formula(timeout_s: int, llm_retries: int, expected: int) -> None:
    from hal0.memory.extraction_env import retain_wall_timeout_s

    got = retain_wall_timeout_s(timeout_s, llm_retries)
    per_call = timeout_s * (llm_retries + 1)
    assert got > per_call  # a single call is never cut mid-flight
    assert got >= 3600
    assert got == expected


def test_retain_wall_timeout_is_strictly_above_one_call_everywhere() -> None:
    from hal0.memory.extraction_env import RETAIN_WALL_CEILING_S, retain_wall_timeout_s

    for timeout_s in (30, 300, 900, 1800, 3600):
        for retries in range(6):
            wall = retain_wall_timeout_s(timeout_s, retries)
            per_call = timeout_s * (retries + 1)
            assert wall > per_call
            assert wall <= max(RETAIN_WALL_CEILING_S, per_call + timeout_s)


def test_drop_in_carries_the_wall_limit_and_follows_the_timeout() -> None:
    from hal0.memory.extraction_env import ExtractionLimits, render_drop_in

    assert "HINDSIGHT_API_RETAIN_WALL_TIMEOUT=3600\n" in render_drop_in()
    out = render_drop_in(3600, limits=ExtractionLimits(llm_retries=1))
    assert "HINDSIGHT_API_LLM_TIMEOUT=3600\n" in out
    assert "HINDSIGHT_API_RETAIN_WALL_TIMEOUT=14400\n" in out


# ── reconcile_extraction_dropin (#3326) ──────────────────────────────────────


def _redirect_drop_in(monkeypatch, tmp_path: Path) -> Path:
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)
    return drop_in


def _write_graph_config(monkeypatch, tmp_path: Path, **graph) -> None:
    import tomli_w

    home = tmp_path / "home"
    cfg_dir = home / "etc" / "hal0"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "hal0.toml").write_bytes(tomli_w.dumps({"memory": {"graph": graph}}).encode())
    monkeypatch.setenv("HAL0_HOME", str(home))


def test_reconcile_skips_a_box_without_a_drop_in(monkeypatch, tmp_path: Path):
    from hal0.memory.extraction_env import reconcile_extraction_dropin

    drop_in = _redirect_drop_in(monkeypatch, tmp_path)
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    assert reconcile_extraction_dropin(seam=seam)["status"] == "skipped"
    assert not drop_in.exists()


@pytest.mark.parametrize("stale_kind", ["token_cap", "retain_only"])
def test_reconcile_rewrites_stale_limits_and_restarts(monkeypatch, tmp_path: Path, stale_kind):
    from hal0.memory.extraction_env import ExtractionLimits, reconcile_extraction_dropin

    drop_in = _redirect_drop_in(monkeypatch, tmp_path)
    _write_graph_config(monkeypatch, tmp_path, extraction_max_tokens=16384)
    drop_in.parent.mkdir(parents=True)
    stale = render_drop_in(300, limits=ExtractionLimits(max_tokens=4096))
    if stale_kind == "retain_only":
        stale = render_drop_in(300).replace("Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT=1\n", "")
    drop_in.write_text(stale)
    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    result = reconcile_extraction_dropin(seam=seam)

    assert result["status"] == "applied"
    assert "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS=16384" in drop_in.read_text()
    assert "Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT=1\n" in drop_in.read_text()
    assert ["systemctl", "restart", "hindsight-api.service"] in [c[0] for c in calls]


def test_reconcile_leaves_a_current_drop_in_alone(monkeypatch, tmp_path: Path):
    from hal0.memory.extraction_env import reconcile_extraction_dropin

    drop_in = _redirect_drop_in(monkeypatch, tmp_path)
    _write_graph_config(monkeypatch, tmp_path)
    drop_in.parent.mkdir(parents=True)
    drop_in.write_text(render_drop_in(300))
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    assert reconcile_extraction_dropin(seam=seam) == {"status": "converged"}


def test_reconcile_reports_a_failed_write_without_raising(monkeypatch, tmp_path: Path):
    from hal0.memory.extraction_env import reconcile_extraction_dropin

    drop_in = _redirect_drop_in(monkeypatch, tmp_path)
    _write_graph_config(monkeypatch, tmp_path, extraction_max_tokens=20000)
    drop_in.parent.mkdir(parents=True)
    drop_in.write_text("stale")
    _, run = _seam_recorder(rc=1, stderr="boom")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    result = reconcile_extraction_dropin(seam=seam)

    assert result["status"] == "failed"
    assert result["error"]


# ── extraction concurrency is held to the slot's --parallel (#3347) ──────────


def _graph(**kw):
    from hal0.config.schema import MemoryGraphConfig

    return MemoryGraphConfig(**kw)


def _write_slot(tmp_path: Path, name: str = "memory") -> Path:
    slots = tmp_path / "slots"
    slots.mkdir(exist_ok=True)
    (slots / f"{name}.toml").write_text('name = "memory"\nport = 8092\n', encoding="utf-8")
    return slots


def _write_id_keyed_slot(tmp_path: Path, stem: str = "7") -> Path:
    """An id-keyed slot file: the stem is the id, the display name is in the body."""
    slots = tmp_path / "slots"
    slots.mkdir(exist_ok=True)
    (slots / f"{stem}.toml").write_text('name = "memory"\nport = 8092\n', encoding="utf-8")
    return slots


@pytest.mark.parametrize(
    ("configured", "parallel", "rendered"),
    [(8, 2, 2), (2, 2, 2), (1, 2, 1), (8, None, 8), (4, 0, 4)],
)
def test_clamp_to_parallel_holds_concurrency_to_the_slot(configured, parallel, rendered):
    base = ExtractionLimits(max_concurrent=configured)
    out = clamp_to_parallel(base, parallel)
    assert out.max_concurrent == rendered
    # Nothing else about the limits moves.
    assert replace(out, max_concurrent=configured) == base


def test_effective_limits_reads_the_extraction_slots_parallel(monkeypatch, tmp_path: Path):
    slots = _write_slot(tmp_path)
    seen: list[dict] = []

    def _parallel(raw):
        seen.append(raw)
        return 2

    monkeypatch.setattr("hal0.providers.container.slot_parallel_sequences", _parallel)
    graph = _graph(extraction_slot="memory", extraction_max_concurrent=8)
    assert extraction_slot_parallel(graph, slots_dir=slots) == 2
    assert effective_limits(graph, slots_dir=slots).max_concurrent == 2
    assert seen and seen[0]["port"] == 8092
    # The recorded config is untouched: hal0.toml keeps what the operator asked for.
    assert graph.extraction_max_concurrent == 8


def test_effective_limits_reads_the_parallel_on_an_id_keyed_layout(monkeypatch, tmp_path: Path):
    """#3473: the clamp must resolve the slot through the layout authority.

    On an id-keyed box the extraction slot lives at ``slots/<id>.toml`` and its
    display name is in the file body, so a name-keyed-only read found nothing
    and the clamp silently did not apply.
    """
    slots = _write_id_keyed_slot(tmp_path)
    seen: list[dict] = []

    def _parallel(raw):
        seen.append(raw)
        return 2

    monkeypatch.setattr("hal0.providers.container.slot_parallel_sequences", _parallel)
    graph = _graph(extraction_slot="memory", extraction_max_concurrent=8)
    assert extraction_slot_parallel(graph, slots_dir=slots) == 2
    assert effective_limits(graph, slots_dir=slots).max_concurrent == 2
    assert seen and seen[0]["port"] == 8092


def test_effective_limits_leaves_concurrency_alone_when_the_slot_is_unknown(tmp_path: Path):
    graph = _graph(extraction_slot="memory", extraction_max_concurrent=8)
    # No slot TOML on disk.
    assert extraction_slot_parallel(graph, slots_dir=tmp_path) is None
    assert effective_limits(graph, slots_dir=tmp_path).max_concurrent == 8


def test_an_upstream_target_is_never_clamped(monkeypatch, tmp_path: Path):
    slots = _write_slot(tmp_path)
    monkeypatch.setattr("hal0.providers.container.slot_parallel_sequences", lambda raw: 1)
    graph = _graph(
        extraction_slot="memory", extraction_model="MiniMax-M2.5", extraction_max_concurrent=8
    )
    assert extraction_slot_parallel(graph, slots_dir=slots) is None
    assert effective_limits(graph, slots_dir=slots).max_concurrent == 8


def test_unresolvable_slot_never_raises(monkeypatch, tmp_path: Path):
    slots = _write_slot(tmp_path)

    def _boom(_raw):
        raise RuntimeError("profile exploded")

    monkeypatch.setattr("hal0.providers.container.slot_parallel_sequences", _boom)
    assert extraction_slot_parallel(_graph(extraction_slot="memory"), slots_dir=slots) is None


def test_concurrency_warning_names_the_numbers_only_when_it_clamps():
    graph = _graph(extraction_slot="memory", extraction_max_concurrent=8)
    msg = concurrency_clamp_warning(graph, 2)
    assert msg is not None
    assert "extraction_max_concurrent = 8" in msg
    assert "'memory'" in msg and "--parallel 2" in msg
    assert concurrency_clamp_warning(graph, 8) is None
    assert concurrency_clamp_warning(graph, 16) is None
    assert concurrency_clamp_warning(graph, None) is None


def test_clamped_limits_render_into_the_drop_in():
    out = render_drop_in(300, limits=clamp_to_parallel(ExtractionLimits(max_concurrent=8), 2))
    assert "Environment=HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT=2\n" in out


def test_reconcile_compares_against_the_clamped_limits(monkeypatch, tmp_path: Path):
    """The update reconcile must not rewrite a clamped drop-in back to the raw
    ``extraction_max_concurrent`` (and restart the engine) on every update."""
    from hal0.memory import extraction_env as ee

    graph = _graph(extraction_slot="memory", extraction_max_concurrent=8)
    clamped = clamp_to_parallel(ExtractionLimits.from_config(graph), 2)
    drop_in = tmp_path / "extraction-model.conf"
    drop_in.write_text(render_drop_in(graph.llm_timeout_s, limits=clamped), encoding="utf-8")

    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)
    monkeypatch.setattr(ee, "extraction_slot_parallel", lambda g, **_k: 2)
    monkeypatch.setattr(
        "hal0.config.loader.load_hal0_config",
        lambda *a, **k: type("C", (), {"memory": type("M", (), {"graph": graph})()})(),
    )
    assert ee.reconcile_extraction_dropin()["status"] == "converged"


# ── showcase drop-in apply / reconcile (#3492) ───────────────────────────────


def _redirect_showcase(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    import hal0.memory.extraction_env as ee

    extraction = _redirect_drop_in(monkeypatch, tmp_path)
    showcase = extraction.parent / "showcase-lanes.conf"
    monkeypatch.setattr(ee, "SHOWCASE_DROP_IN_PATH", showcase)
    monkeypatch.setattr(
        ee, "LEGACY_LLM_CAP_DROP_IN_PATH", extraction.parent / "zz-h0-98-llm-global-cap.conf"
    )
    return extraction, showcase


def _showcase_cfg():
    from hal0.config.schema import MemoryDecisionConfig, MemoryLanesConfig, MemoryModelsConfig

    return {
        "models": MemoryModelsConfig(),
        "decision": MemoryDecisionConfig(provider="local"),
        "lanes": MemoryLanesConfig(),
    }


def test_apply_showcase_writes_through_the_seam_verb_then_restarts():
    """The unprivileged path: body on stdin to the dedicated verb (never a
    path argument), then daemon-reload + restart — in that order."""
    from hal0.memory.extraction_env import apply_showcase_dropin, render_showcase_env

    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_showcase_dropin(**_showcase_cfg(), seam=seam)

    assert result["error"] is None
    assert result["written"] and result["daemon_reloaded"] and result["restarted"]
    argvs = [c[0] for c in calls]
    assert argvs[0][-1] == "write-hindsight-showcase-dropin"
    assert SEAM_BIN in argvs[0]
    assert calls[0][1] == render_showcase_env(**_showcase_cfg())
    assert argvs[1][-1] == "daemon-reload"
    assert argvs[2][-2:] == ["svc-restart", "hindsight"]


def test_apply_showcase_renders_the_embedder_a_switch_hands_it(tmp_path, monkeypatch):
    """#3518: `hal0 memory embedder switch` is the one caller that moves a
    running engine to another embedder. It passes the pair; the drop-in
    carries exactly that pair, and the record stays the caller's to write."""
    from hal0.memory import embedder_record
    from hal0.memory.embedder_record import Embedder
    from hal0.memory.extraction_env import apply_showcase_dropin

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))

    def _no_resolve(*_a, **_k):  # pragma: no cover — a call here is the bug
        raise AssertionError("an explicit embedder must not be resolved from the engine")

    monkeypatch.setattr("hal0.memory.extraction_env.ensure_embedder_record", _no_resolve)
    monkeypatch.setattr("hal0.memory.extraction_env.resolve_embedder", _no_resolve)
    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_showcase_dropin(
        **_showcase_cfg(), seam=seam, embedder=Embedder("local", "example-org/next-embedder")
    )

    assert result["error"] is None and result["restarted"]
    body = calls[0][1]
    assert "Environment=HINDSIGHT_API_EMBEDDINGS_PROVIDER=local\n" in body
    assert "Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=example-org/next-embedder\n" in body
    assert [c[0][-1] for c in calls[:2]] == ["write-hindsight-showcase-dropin", "daemon-reload"]
    assert not embedder_record.embedder_record_path().exists()


def test_apply_showcase_reports_a_refused_write_and_skips_the_restart():
    from hal0.memory.extraction_env import apply_showcase_dropin

    calls, run = _seam_recorder(rc=64, stderr="drop-in rejected: nope")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_showcase_dropin(**_showcase_cfg(), seam=seam)

    assert result["written"] is False and result["restarted"] is False
    assert "drop-in rejected" in result["error"]
    assert len(calls) == 1


def test_apply_showcase_without_restart_only_writes(monkeypatch, tmp_path: Path):
    from hal0.memory.extraction_env import apply_showcase_dropin

    _, showcase = _redirect_showcase(monkeypatch, tmp_path)
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    result = apply_showcase_dropin(**_showcase_cfg(), restart=False, seam=seam)

    assert result["written"] and not result["restarted"]
    assert "\n[Service]\n" in showcase.read_text()


def test_showcase_reconcile_skips_a_box_without_the_engine(monkeypatch, tmp_path: Path):
    from hal0.memory.extraction_env import reconcile_showcase_dropin

    _, showcase = _redirect_showcase(monkeypatch, tmp_path)
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    assert reconcile_showcase_dropin(seam=seam)["status"] == "skipped"
    assert not showcase.exists()


def test_showcase_reconcile_creates_then_converges(monkeypatch, tmp_path: Path):
    """An updated box with the engine but no showcase drop-in gets one (and a
    restart); the next pass is a no-op that touches nothing privileged."""
    from hal0.memory.extraction_env import reconcile_showcase_dropin

    extraction, showcase = _redirect_showcase(monkeypatch, tmp_path)
    _write_graph_config(monkeypatch, tmp_path)
    extraction.parent.mkdir(parents=True)
    extraction.write_text(render_drop_in(300))
    calls, run = _seam_recorder()

    result = reconcile_showcase_dropin(seam=SystemCtlSeam(run=run, is_hal0_user=lambda: False))

    assert result["status"] == "applied"
    assert "HINDSIGHT_API_EMBEDDINGS_" in showcase.read_text()
    assert ["systemctl", "restart", "hindsight-api.service"] in [c[0] for c in calls]
    quiet = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)
    assert reconcile_showcase_dropin(seam=quiet) == {"status": "converged"}


def test_showcase_caps_are_held_to_the_extraction_ceiling():
    """#3492 review: showcase-lanes.conf sorts after extraction-model.conf, so
    its caps are the ones the engine runs with — they must not undo the slot
    --parallel clamp. Ops routed off the memory slot keep their own cap."""
    from hal0.config.schema import (
        MemoryDecisionConfig,
        MemoryLaneOpConfig,
        MemoryLanesConfig,
        MemoryModelsConfig,
    )
    from hal0.memory.extraction_env import render_showcase_env

    lanes = MemoryLanesConfig(
        llm_max_concurrent=4,
        retain=MemoryLaneOpConfig(max_concurrent=6),
        reflect=MemoryLaneOpConfig(max_concurrent=5, virtual_model="hal0/primary"),
    )
    body = render_showcase_env(
        models=MemoryModelsConfig(), decision=MemoryDecisionConfig(), lanes=lanes, ceiling=1
    )
    env = dict(
        line.removeprefix("Environment=").split("=", 1)
        for line in body.splitlines()
        if line.startswith("Environment=")
    )
    assert env["HINDSIGHT_API_LLM_MAX_CONCURRENT"] == "1"
    assert env["HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT"] == "1"
    assert env["HINDSIGHT_API_CONSOLIDATION_LLM_MAX_CONCURRENT"] == "1"
    assert env["HINDSIGHT_API_REFLECT_LLM_MAX_CONCURRENT"] == "5"


def test_effective_concurrency_across_both_dropins_keeps_the_slot_clamp():
    """Merge the two drop-ins the way systemd does (lexical order, later
    assignment wins) and check every shared name lands at or under the clamp."""
    from hal0.config.schema import (
        MemoryDecisionConfig,
        MemoryLaneOpConfig,
        MemoryLanesConfig,
        MemoryModelsConfig,
    )
    from hal0.memory.extraction_env import ExtractionLimits, render_showcase_env

    clamped = ExtractionLimits(max_concurrent=1)
    extraction = render_drop_in(300, limits=clamped)
    showcase = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(),
        lanes=MemoryLanesConfig(llm_max_concurrent=8, retain=MemoryLaneOpConfig(max_concurrent=8)),
        ceiling=clamped.max_concurrent,
    )

    def _env(body: str) -> dict[str, str]:
        return dict(
            line.removeprefix("Environment=").split("=", 1)
            for line in body.splitlines()
            if line.startswith("Environment=")
        )

    first, last = _env(extraction), _env(showcase)
    effective = {**first, **last}  # extraction-model.conf < showcase-lanes.conf
    shared = set(first) & set(last)
    assert shared, "the two drop-ins are expected to share the concurrency names"
    for name in shared:
        if name.endswith("_MAX_CONCURRENT"):
            assert int(effective[name]) <= clamped.max_concurrent, name


def test_reload_and_restart_reloads_first_and_reports_a_seam_timeout():
    from hal0.memory.extraction_env import reload_and_restart_hindsight
    from hal0.system.seam import SeamTimeout

    calls, run = _seam_recorder()
    ok = reload_and_restart_hindsight(seam=SystemCtlSeam(run=run, is_hal0_user=lambda: False))
    assert ok == {"daemon_reloaded": True, "restarted": True, "error": None}
    assert [c[0] for c in calls] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "hindsight-api.service"],
    ]

    class _Seam:
        def systemctl(self, *args, **kwargs):
            if "restart" in args:
                raise SeamTimeout(list(args), 30, "activating")

    out = reload_and_restart_hindsight(seam=_Seam())  # type: ignore[arg-type]
    assert out["daemon_reloaded"] is True and out["restarted"] is False
    assert "did not return within 30s" in out["error"]


@pytest.mark.parametrize(
    ("section", "body"),
    [
        ("lanes", {"reasoning_effort": "x-high"}),
        ("lanes", {"retain": {"virtual_model": "gpt-4o"}}),
        ("lanes", {"retain": {"reasoning_effort": "High"}}),
        ("models", {"embeddings_model": "../../etc/passwd"}),
        ("models", {"reranker_provider": "Local Thing"}),
        ("decision", {"model": "a b"}),
    ],
)
def test_schema_refuses_values_the_showcase_wrapper_would(section, body):
    from pydantic import ValidationError

    from hal0.config.schema import MemoryConfig

    with pytest.raises(ValidationError):
        MemoryConfig.model_validate({section: body})


# ── legacy H0-98 drop-in retirement + effective unit env (#3520 / #3388) ─────


def test_showcase_direct_write_removes_the_legacy_cap_drop_in(monkeypatch, tmp_path: Path):
    """#3520: installing the showcase drop-in deletes the hand-maintained
    zz-h0-98 file beside it (it sorts later and would win) — and nothing else."""
    from hal0.memory.extraction_env import apply_showcase_dropin

    _, showcase = _redirect_showcase(monkeypatch, tmp_path)
    showcase.parent.mkdir(parents=True)
    legacy = showcase.parent / "zz-h0-98-llm-global-cap.conf"
    legacy.write_text("[Service]\nEnvironment=HINDSIGHT_API_LLM_MAX_CONCURRENT=2\n")
    other = showcase.parent / "zz-operator.conf"
    other.write_text("[Service]\n")
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    result = apply_showcase_dropin(**_showcase_cfg(), restart=False, seam=seam)

    assert result["written"] and result["legacy_drop_in_present"] is False
    assert not legacy.exists()
    assert other.exists()


def test_showcase_reconcile_applies_while_the_legacy_drop_in_exists(monkeypatch, tmp_path: Path):
    """A byte-identical showcase file is NOT converged while the legacy file
    still overrides it: only the privileged write removes it."""
    from hal0.memory import extraction_env as ee

    extraction, _showcase = _redirect_showcase(monkeypatch, tmp_path)
    _write_graph_config(monkeypatch, tmp_path)
    extraction.parent.mkdir(parents=True)
    extraction.write_text(render_drop_in(300))
    quiet = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)
    assert ee.reconcile_showcase_dropin(restart=False, seam=quiet)["status"] == "applied"
    assert ee.reconcile_showcase_dropin(restart=False, seam=quiet) == {"status": "converged"}

    legacy = ee.LEGACY_LLM_CAP_DROP_IN_PATH
    legacy.write_text("[Service]\nEnvironment=HINDSIGHT_API_LLM_MAX_CONCURRENT=2\n")
    result = ee.reconcile_showcase_dropin(restart=False, seam=quiet)

    assert result["status"] == "applied"
    assert not legacy.exists()
    assert ee.reconcile_showcase_dropin(restart=False, seam=quiet) == {"status": "converged"}


def test_drop_in_matches_compares_the_unit_env_when_given(monkeypatch, tmp_path: Path):
    """#3388: a later-sorting drop-in overriding a managed key is drift even
    though hal0's own file is byte-for-byte right."""
    from hal0.memory import extraction_env as ee

    drop_in = _redirect_drop_in(monkeypatch, tmp_path)
    monkeypatch.setattr(ee, "SHOWCASE_DROP_IN_PATH", drop_in.parent / "showcase-lanes.conf")
    drop_in.parent.mkdir(parents=True)
    body = render_drop_in(300)
    drop_in.write_text(body)
    unit = ee._env_from_body(body)

    assert ee.drop_in_matches(300)  # file only, as before
    assert ee.drop_in_matches(300, unit_env=unit)
    assert ee.drop_in_matches(300, unit_env={})  # systemd absent: cannot refute
    assert not ee.drop_in_matches(300, unit_env={**unit, "HINDSIGHT_API_LLM_TIMEOUT": "3600"})


def test_drop_in_matches_ignores_keys_the_showcase_drop_in_owns(monkeypatch, tmp_path: Path):
    """The showcase drop-in legitimately overrides the concurrency caps; that
    is not extraction drift."""
    from hal0.memory import extraction_env as ee

    drop_in, showcase = _redirect_showcase(monkeypatch, tmp_path)
    drop_in.parent.mkdir(parents=True)
    body = render_drop_in(300)
    drop_in.write_text(body)
    showcase.write_text(ee.render_showcase_env(**_showcase_cfg(), ceiling=1))
    unit = {**ee._env_from_body(body), **ee._env_from_body(showcase.read_text())}
    unit["HINDSIGHT_API_LLM_MAX_CONCURRENT"] = "7"

    assert ee.drop_in_matches(300, unit_env=unit)


def test_showcase_drop_in_matches_compares_the_unit_env(monkeypatch, tmp_path: Path):
    from hal0.memory import extraction_env as ee

    _, showcase = _redirect_showcase(monkeypatch, tmp_path)
    showcase.parent.mkdir(parents=True)
    body = ee.render_showcase_env(**_showcase_cfg())
    showcase.write_text(body)
    unit = ee._env_from_body(body)

    assert ee.showcase_drop_in_matches(**_showcase_cfg(), unit_env=unit)
    assert not ee.showcase_drop_in_matches(
        **_showcase_cfg(), unit_env={**unit, "HINDSIGHT_API_LLM_MAX_CONCURRENT": "2"}
    )


def _memory_cfg():
    from hal0.config.schema import MemoryConfig

    return MemoryConfig()


def test_expected_engine_env_merges_in_systemd_order(tmp_path: Path):
    """showcase-lanes.conf sorts after extraction-model.conf, so a key both set
    carries the showcase value."""
    from hal0.memory import extraction_env as ee

    mem = _memory_cfg()
    mem.graph.extraction_max_concurrent = 4
    mem.lanes.llm_max_concurrent = 1
    env = ee.expected_engine_env(mem, slots_dir=tmp_path)

    assert env["HINDSIGHT_API_LLM_TIMEOUT"] == str(mem.graph.llm_timeout_s)
    assert env["HINDSIGHT_API_LLM_MAX_CONCURRENT"] == "1"
    only_extraction = ee.expected_engine_env(mem, include_showcase=False, slots_dir=tmp_path)
    assert only_extraction["HINDSIGHT_API_LLM_MAX_CONCURRENT"] == "4"
    assert "HINDSIGHT_API_EMBEDDINGS_PROVIDER" not in only_extraction


def _report_paths(tmp_path: Path) -> dict[str, Path]:
    d = tmp_path / "hindsight-api.service.d"
    d.mkdir(parents=True, exist_ok=True)
    return {
        "drop_in_path": d / "extraction-model.conf",
        "showcase_path": d / "showcase-lanes.conf",
        "legacy_path": d / "zz-h0-98-llm-global-cap.conf",
    }


def test_engine_env_report_not_installed(tmp_path: Path):
    from hal0.memory import extraction_env as ee

    report = ee.engine_env_report(
        _memory_cfg(), show_unit_env=_forbidden, slots_dir=tmp_path, **_report_paths(tmp_path)
    )
    assert report["installed"] is False and report["checked"] is False
    assert report["drift"] == []


def test_engine_env_report_flags_the_h0_98_override(tmp_path: Path):
    """The live #3520 shape: hal0 renders a global cap of 1, the leftover
    zz-h0-98 drop-in makes the unit carry 2 — reported as drift, with the
    legacy file named."""
    from hal0.memory import extraction_env as ee

    paths = _report_paths(tmp_path)
    mem = _memory_cfg()
    paths["drop_in_path"].write_text("x")
    paths["showcase_path"].write_text("x")
    paths["legacy_path"].write_text("x")
    expected = ee.expected_engine_env(mem, slots_dir=tmp_path)
    unit = {**expected, "HINDSIGHT_API_LLM_MAX_CONCURRENT": "2", "PATH": "/usr/bin"}

    report = ee.engine_env_report(mem, show_unit_env=lambda: unit, slots_dir=tmp_path, **paths)

    assert report["checked"] is True
    assert report["drift"] == [
        {
            "key": "HINDSIGHT_API_LLM_MAX_CONCURRENT",
            "expected": expected["HINDSIGHT_API_LLM_MAX_CONCURRENT"],
            "effective": "2",
        }
    ]
    assert report["legacy_drop_in"] == str(paths["legacy_path"])
    assert "PATH" not in report["effective"]  # only managed keys leave the function
    warning = ee.engine_env_warning(report)
    assert warning is not None
    assert "zz-h0-98-llm-global-cap.conf" in warning
    assert "HINDSIGHT_API_LLM_MAX_CONCURRENT=2" in warning


def test_engine_env_report_clean_and_unreadable(tmp_path: Path):
    from hal0.memory import extraction_env as ee

    paths = _report_paths(tmp_path)
    mem = _memory_cfg()
    paths["drop_in_path"].write_text("x")
    expected = ee.expected_engine_env(mem, include_showcase=False, slots_dir=tmp_path)

    clean = ee.engine_env_report(mem, unit_env=expected, slots_dir=tmp_path, **paths)
    assert clean["checked"] and clean["drift"] == []
    assert ee.engine_env_warning(clean) is None

    unreadable = ee.engine_env_report(mem, unit_env={}, slots_dir=tmp_path, **paths)
    assert unreadable["checked"] is False and unreadable["drift"] == []


def test_engine_env_report_never_raises(tmp_path: Path):
    from hal0.memory import extraction_env as ee

    paths = _report_paths(tmp_path)
    paths["drop_in_path"].write_text("x")

    def _boom():
        raise RuntimeError("systemd exploded")

    report = ee.engine_env_report(_memory_cfg(), show_unit_env=_boom, slots_dir=tmp_path, **paths)
    assert report["installed"] is True and report["checked"] is False


def test_concurrency_warning_names_both_rendered_keys():
    """#3472: hal0 renders the clamp into both concurrency keys."""
    msg = concurrency_clamp_warning(
        _graph(extraction_slot="memory", extraction_max_concurrent=8), 2
    )
    assert msg is not None
    assert "HINDSIGHT_API_LLM_MAX_CONCURRENT and HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT" in msg


def test_engine_env_report_never_carries_a_credential(tmp_path: Path, monkeypatch):
    """An operator drop-in with a real decision-model key must not leak into
    the report (it feeds status routes and doctor)."""
    from hal0.config.schema import MemoryDecisionConfig
    from hal0.memory import extraction_env as ee

    # A local decision lane renders only when hal0-hs-decide is installed (#3519).
    unit = tmp_path / "hal0-hs-decide.service"
    unit.write_text("[Unit]\n")
    monkeypatch.setattr(ee, "DECISION_UNIT_PATH", unit)
    paths = _report_paths(tmp_path)
    mem = _memory_cfg()
    mem.decision = MemoryDecisionConfig(enabled=True, provider="local")
    paths["drop_in_path"].write_text("x")
    paths["showcase_path"].write_text("x")
    expected = ee.expected_engine_env(mem, slots_dir=tmp_path)
    assert "HINDSIGHT_API_DECISION_MODEL_API_KEY" in expected
    unit = {**expected, "HINDSIGHT_API_DECISION_MODEL_API_KEY": "sk-real-secret"}

    report = ee.engine_env_report(mem, unit_env=unit, slots_dir=tmp_path, **paths)

    assert "sk-real-secret" not in repr(report)
    assert report["drift"] == []


# ── the showcase env carries the store's embedder, not the config's (#3518) ──

_BGE = "BAAI/bge-small-en-v1.5"
_HS = "hindsight-ai/hs-embed-1"
_PROVIDER_LINE = "Environment=HINDSIGHT_API_EMBEDDINGS_PROVIDER="
_EMBED_LINE = "Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL="
_PROVIDER_KEY = "HINDSIGHT_API_EMBEDDINGS_PROVIDER"
_MODEL_KEY = "HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL"


def _engine_store(monkeypatch, tmp_path: Path) -> Path:
    """An engine that has already started: its pg0 cluster under the engine home."""
    from hal0.memory.engine_upgrade import hindsight_dir

    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    data = hindsight_dir() / ".pg0" / "instances" / "hindsight" / "data"
    data.mkdir(parents=True)
    (data / "PG_VERSION").write_text("18\n")
    return data


def _recorded() -> dict:
    import json

    from hal0.memory.embedder_record import embedder_record_path

    return json.loads(embedder_record_path().read_text())


def _hs():
    from hal0.memory.embedder_record import Embedder

    return Embedder("local", _HS)


def _configured():
    from hal0.memory.embedder_record import configured_embedder

    return configured_embedder(_showcase_cfg()["models"])


def test_render_takes_the_embedder_from_the_record_not_the_config(monkeypatch, tmp_path: Path):
    from hal0.memory import extraction_env as ee
    from hal0.memory.embedder_record import ensure_embedder_record

    _redirect_showcase(monkeypatch, tmp_path)
    _engine_store(monkeypatch, tmp_path)
    cfg = _showcase_cfg()
    assert cfg["models"].embeddings_model == _HS
    ensure_embedder_record(_configured())  # existing store, nothing names an embedder

    body = ee.render_showcase_env(**cfg)

    assert _EMBED_LINE + _BGE + "\n" in body
    assert _PROVIDER_LINE + "local\n" in body
    assert _HS not in body
    # Every other key renders exactly as it does for a store built with the configured model.
    assert body.replace(_EMBED_LINE + _BGE, _EMBED_LINE + _HS) == ee.render_showcase_env(
        **cfg, embedder=_hs()
    )
    assert ee.expected_engine_env(_memory_cfg(), slots_dir=tmp_path)[_MODEL_KEY] == _BGE


def test_render_takes_the_provider_from_the_record_too(monkeypatch, tmp_path: Path):
    """Provider and model are one fact: a configured remote provider is not rendered."""
    from hal0.config.schema import MemoryModelsConfig
    from hal0.memory import extraction_env as ee
    from hal0.memory.embedder_record import ensure_embedder_record

    _redirect_showcase(monkeypatch, tmp_path)
    _engine_store(monkeypatch, tmp_path)
    ensure_embedder_record(_configured())
    cfg = _showcase_cfg()
    cfg["models"] = MemoryModelsConfig(embeddings_provider="openai", embeddings_model=_BGE)

    body = ee.render_showcase_env(**cfg)

    assert _PROVIDER_LINE + "local\n" in body
    assert "openai" not in body


def test_an_upgraded_box_with_a_store_and_no_showcase_dropin_renders_bge_small(
    monkeypatch, tmp_path: Path
):
    """#3518 regression: the first showcase render on an upgraded box must keep
    the embedder its store was built with, not move it to the config default."""
    from hal0.memory import extraction_env as ee

    extraction, showcase = _redirect_showcase(monkeypatch, tmp_path)
    _write_graph_config(monkeypatch, tmp_path)  # [memory.models] left at its default
    _engine_store(monkeypatch, tmp_path)
    extraction.parent.mkdir(parents=True)
    extraction.write_text(render_drop_in(300))
    quiet = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    result = ee.reconcile_showcase_dropin(restart=False, seam=quiet)

    assert result["status"] == "applied"
    written = showcase.read_text()
    assert _EMBED_LINE + _BGE + "\n" in written
    assert "hs-embed-1" not in written
    assert "HINDSIGHT_API_RERANKER_LOCAL_MODEL=hindsight-ai/hs-rerank-1" in written
    record = _recorded()
    assert (record["provider"], record["model"], record["mixed"]) == ("local", _BGE, False)
    assert record["source"] == "engine-default"
    # Stable: the next update is a no-op and the record is not rewritten.
    assert ee.reconcile_showcase_dropin(restart=False, seam=quiet) == {"status": "converged"}
    assert _recorded() == record


def test_a_box_already_moved_to_hs_embed_stays_there_and_is_recorded_mixed(
    monkeypatch, tmp_path: Path
):
    """A box that ran a build between #3486 and this fix: the drop-in names
    hs-embed-1 over a store built with bge-small. Moving it back would make
    things worse, so the drop-in is left as it is and the store is flagged."""
    from hal0.memory import extraction_env as ee
    from hal0.memory.embedder_record import record_store_embedder

    extraction, showcase = _redirect_showcase(monkeypatch, tmp_path)
    _write_graph_config(monkeypatch, tmp_path)
    _engine_store(monkeypatch, tmp_path)
    extraction.parent.mkdir(parents=True)
    extraction.write_text(render_drop_in(300))
    showcase.write_text(ee.render_showcase_env(**_showcase_cfg(), ceiling=1, embedder=_hs()))
    before = showcase.read_bytes()
    quiet = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    record_store_embedder()  # what the update runs first
    result = ee.reconcile_showcase_dropin(restart=False, seam=quiet)

    assert result == {"status": "converged"}
    assert showcase.read_bytes() == before
    record = _recorded()
    assert (record["model"], record["source"], record["mixed"]) == (_HS, "showcase-drop-in", True)


def test_apply_on_a_box_with_no_store_records_the_configured_pair_with_the_dropin(
    monkeypatch, tmp_path: Path
):
    from hal0.memory.embedder_record import embedder_record_path
    from hal0.memory.extraction_env import apply_showcase_dropin

    _, showcase = _redirect_showcase(monkeypatch, tmp_path)
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    assert not embedder_record_path().exists()
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    result = apply_showcase_dropin(**_showcase_cfg(), restart=False, seam=seam)

    assert result["written"]
    assert _EMBED_LINE + _HS + "\n" in showcase.read_text()
    record = _recorded()
    assert (record["provider"], record["model"]) == ("local", _HS)
    assert (record["source"], record["mixed"]) == ("configured", False)


def test_a_refused_write_on_a_box_with_no_store_records_nothing(monkeypatch, tmp_path: Path):
    """No drop-in landed, so nothing makes the engine use the configured model:
    writing it down would let a store be built under a record that is wrong."""
    from hal0.memory.embedder_record import embedder_record_path
    from hal0.memory.extraction_env import apply_showcase_dropin

    _redirect_showcase(monkeypatch, tmp_path)
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    _calls, run = _seam_recorder(rc=64, stderr="drop-in rejected: nope")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_showcase_dropin(**_showcase_cfg(), seam=seam)

    assert result["written"] is False
    assert not embedder_record_path().exists()


def test_changing_the_configured_embedder_does_not_change_the_rendered_one(
    monkeypatch, tmp_path: Path
):
    """Config differs from record, model or provider: the drop-in keeps the recorded pair."""
    from hal0.config.schema import MemoryModelsConfig
    from hal0.memory.extraction_env import apply_showcase_dropin

    _, showcase = _redirect_showcase(monkeypatch, tmp_path)
    _engine_store(monkeypatch, tmp_path)
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)
    cfg = _showcase_cfg()
    apply_showcase_dropin(**cfg, restart=False, seam=seam)
    first, record = showcase.read_bytes(), _recorded()
    assert record["model"] == _BGE

    for models in (
        MemoryModelsConfig(embeddings_model="example-org/another-embedder"),
        MemoryModelsConfig(embeddings_provider="openai"),
    ):
        cfg["models"] = models
        apply_showcase_dropin(**cfg, restart=False, seam=seam)
        assert showcase.read_bytes() == first
        assert _recorded() == record


def test_apply_leaves_an_unreadable_record_alone_and_renders_what_the_engine_runs(
    monkeypatch, tmp_path: Path
):
    from hal0.memory import embedder_record as er
    from hal0.memory.extraction_env import apply_showcase_dropin

    _, showcase = _redirect_showcase(monkeypatch, tmp_path)
    _engine_store(monkeypatch, tmp_path)
    er.embedder_record_path().parent.mkdir(parents=True)
    er.embedder_record_path().write_text("{garbage")
    monkeypatch.setattr(
        er,
        "engine_evidence",
        lambda **_kw: {
            "live": {"provider": None, "model": _HS},
            "on_disk": {"provider": None, "model": _HS},
            "on_disk_source": "unit",
        },
    )
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    result = apply_showcase_dropin(**_showcase_cfg(), restart=False, seam=seam)

    assert result["written"]
    assert _EMBED_LINE + _HS + "\n" in showcase.read_text()
    assert er.embedder_record_path().read_text() == "{garbage"


def test_apply_renders_the_live_embedder_when_the_record_disagrees(monkeypatch, tmp_path: Path):
    """A record that says bge-small over an engine running hs-embed-1 (a
    downgrade and re-upgrade): the write must not swap the running engine."""
    from hal0.memory import embedder_record as er
    from hal0.memory.extraction_env import apply_showcase_dropin

    _, showcase = _redirect_showcase(monkeypatch, tmp_path)
    _engine_store(monkeypatch, tmp_path)
    er.save_embedder_record(
        er.EmbedderRecord(provider="local", model=_BGE, source="engine-default")
    )
    record = _recorded()
    monkeypatch.setattr(
        er,
        "engine_evidence",
        lambda **_kw: {
            "live": {"provider": "local", "model": _HS},
            "on_disk": {"provider": "local", "model": _HS},
            "on_disk_source": "unit",
        },
    )
    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    apply_showcase_dropin(**_showcase_cfg(), restart=False, seam=seam)

    assert _EMBED_LINE + _HS + "\n" in showcase.read_text()
    assert _recorded() == record


# ── the engine's effective embedder: one evidence source (#3518) ─────────────


def _evidence(tmp_path: Path, **kw):
    from hal0.memory.extraction_env import effective_engine_embedder

    kw.setdefault("env_file", tmp_path / "no-hindsight-llm.env")
    kw.setdefault("read_process_env", lambda _keys: None)
    kw.setdefault("showcase_path", tmp_path / "no-showcase.conf")
    return effective_engine_embedder(**kw)


def test_evidence_reads_the_running_engine_first(tmp_path: Path):
    asked: list[tuple[str, ...]] = []

    def process(keys):
        asked.append(tuple(keys))
        return {_MODEL_KEY: _HS}

    out = _evidence(tmp_path, read_process_env=process, unit_env={"HOME": "/x", _MODEL_KEY: _BGE})

    assert asked == [(_PROVIDER_KEY, _MODEL_KEY)]  # nothing else leaves the process reader
    assert out["live"] == {"provider": None, "model": _HS}
    assert out["on_disk"] == {"provider": None, "model": _BGE}
    assert out["on_disk_source"] == "unit"


def test_evidence_running_engine_with_no_embedder_environment(tmp_path: Path):
    """Running, readable, neither key set: the engine is on its default."""
    out = _evidence(tmp_path, read_process_env=lambda _k: {}, unit_env={"HOME": "/x"})
    assert out["live"] == {"provider": None, "model": None}
    assert out["on_disk"] == {"provider": None, "model": None}


def test_evidence_sees_a_later_sorting_dropin_through_the_units_merged_environment(
    tmp_path: Path,
):
    """systemd merges every drop-in in order; a hand-written zz-*.conf naming
    hs-embed-1 after hal0's bge-small is what the unit's Environment= shows."""
    merged = {"HOME": "/x", _PROVIDER_KEY: "local", _MODEL_KEY: _HS}
    out = _evidence(tmp_path, show_unit_env=lambda: merged)
    assert out["live"] is None
    assert out["on_disk"] == {"provider": "local", "model": _HS}
    assert out["on_disk_source"] == "unit"


def test_evidence_puts_the_environment_file_on_top_of_the_unit(tmp_path: Path):
    """EnvironmentFile= is applied after every Environment= line, drop-ins included."""
    env_file = tmp_path / "hindsight-llm.env"
    env_file.write_text(f"HINDSIGHT_API_LLM_API_KEY=sk-secret\n{_MODEL_KEY}={_HS}\n")

    out = _evidence(
        tmp_path, env_file=env_file, unit_env={_PROVIDER_KEY: "local", _MODEL_KEY: _BGE}
    )

    assert out["on_disk"] == {"provider": "local", "model": _HS}
    assert out["on_disk_source"] == "unit+file"
    assert "sk-secret" not in repr(out)


def test_evidence_falls_back_to_hal0s_dropin_file_when_there_is_no_unit(tmp_path: Path):
    from hal0.memory import extraction_env as ee

    showcase = tmp_path / "showcase-lanes.conf"
    assert _evidence(tmp_path, unit_env={}, showcase_path=showcase) == {
        "live": None,
        "on_disk": None,
        "on_disk_source": None,
    }
    showcase.write_text(ee.render_showcase_env(**_showcase_cfg(), embedder=_hs()))

    out = _evidence(tmp_path, unit_env={}, showcase_path=showcase)

    assert out["on_disk"] == {"provider": "local", "model": _HS}
    assert out["on_disk_source"] == "drop-in"


def test_evidence_never_raises(tmp_path: Path):
    def boom(_keys):
        raise RuntimeError("proc exploded")

    assert _evidence(tmp_path, read_process_env=boom)["live"] is None


def test_the_process_reader_keeps_only_the_keys_it_is_asked_for(monkeypatch, tmp_path: Path):
    from hal0.memory import extraction_env as ee

    environ = tmp_path / "environ"
    environ.write_bytes(
        b"HINDSIGHT_API_LLM_API_KEY=sk-secret\0"
        + f"{_MODEL_KEY}={_HS}".encode()
        + b"\0HINDSIGHT_API_LLM_MODEL=hal0/memory\0"
    )
    monkeypatch.setattr(ee, "_engine_main_pid", lambda: 4242)
    monkeypatch.setattr(ee, "_proc_environ_path", lambda _pid: environ)

    assert ee._read_engine_process_env((_PROVIDER_KEY, _MODEL_KEY)) == {_MODEL_KEY: _HS}
    assert ee._read_engine_process_env() == {"HINDSIGHT_API_LLM_MODEL": "hal0/memory"}


# ── memory_embedder and memory_engine_env cannot disagree (#3518) ────────────


def _two_rows(monkeypatch, tmp_path: Path, *, unit: dict, live=None, env_file: Path | None = None):
    """Both doctor rows, fed from one engine: ``(embedder status, env report, checks)``."""
    from hal0.cli import doctor_all as da
    from hal0.memory import embedder_record as er
    from hal0.memory import extraction_env as ee

    def evidence(**_kw):
        return ee.effective_engine_embedder(
            unit_env=unit,
            read_process_env=lambda _keys: live,
            env_file=env_file if env_file is not None else tmp_path / "no-hindsight-llm.env",
        )

    monkeypatch.setattr(er, "engine_evidence", evidence)
    mem = _memory_cfg()
    paths = _report_paths(tmp_path)
    paths["drop_in_path"].write_text("x")
    paths["showcase_path"].write_text("x")
    status = er.embedder_status(er.configured_embedder(mem.models))
    report = ee.engine_env_report(mem, unit_env=unit, slots_dir=tmp_path, **paths)
    return (
        status,
        report,
        da.check_memory_embedder(status=status),
        da.check_memory_engine_env(report=report),
    )


def _unit_as_hal0_renders_it(tmp_path: Path, model: str) -> dict:
    """The unit's merged Environment= with hal0's two drop-ins on ``model``."""
    from hal0.memory import extraction_env as ee
    from hal0.memory.embedder_record import Embedder

    return {
        "HOME": "/var/lib/hal0/memory/hindsight",
        **ee.expected_engine_env(
            _memory_cfg(), slots_dir=tmp_path, embedder=Embedder("local", model)
        ),
    }


@pytest.mark.parametrize("running", [True, False])
def test_a_later_sorting_dropin_naming_hs_embed_is_what_both_rows_see(
    monkeypatch, tmp_path: Path, running: bool
):
    """hal0's drop-in says bge-small, a zz-*.conf after it says hs-embed-1.
    The record follows the engine, the store is mixed, and neither row claims
    the engine is on bge-small."""
    _engine_store(monkeypatch, tmp_path)
    unit = {**_unit_as_hal0_renders_it(tmp_path, _BGE), _MODEL_KEY: _HS}  # zz-*.conf wins
    live = {_PROVIDER_KEY: "local", _MODEL_KEY: _HS} if running else None

    status, report, embedder_row, env_row = _two_rows(monkeypatch, tmp_path, unit=unit, live=live)

    assert (status["recorded"], status["mixed"]) == (_HS, True)
    assert status["source"] == ("engine-process" if running else "unit-files")
    assert status["conflict"] is None
    assert report["effective"][_MODEL_KEY] == _HS == status["effective"]["model"]
    assert [d for d in report["drift"] if "EMBEDDINGS" in d["key"]] == []
    assert embedder_row.status == "warn" and _HS in embedder_row.detail  # mixed
    assert "EMBEDDINGS" not in env_row.detail


def test_an_environment_file_naming_hs_embed_is_what_both_rows_see(monkeypatch, tmp_path: Path):
    """The unit's Environment= says bge-small; the EnvironmentFile, applied
    last, says hs-embed-1. `systemctl show` alone would miss it."""
    _engine_store(monkeypatch, tmp_path)
    env_file = tmp_path / "hindsight-llm.env"
    env_file.write_text(f"{_MODEL_KEY}={_HS}\n")
    unit = _unit_as_hal0_renders_it(tmp_path, _BGE)

    status, report, embedder_row, env_row = _two_rows(
        monkeypatch, tmp_path, unit=unit, env_file=env_file
    )

    assert (status["recorded"], status["mixed"], status["source"]) == (_HS, True, "unit-files")
    assert status["effective"] == {"provider": "local", "model": _HS, "source": "unit+file"}
    assert report["effective"][_MODEL_KEY] == _HS
    assert [d for d in report["drift"] if "EMBEDDINGS" in d["key"]] == []
    assert embedder_row.status == "warn"
    assert "EMBEDDINGS" not in env_row.detail


def test_a_record_the_environment_file_overrides_makes_both_rows_warn(monkeypatch, tmp_path: Path):
    """Record bge-small, engine down, EnvironmentFile would start it on
    hs-embed-1: the embedder row names the conflict and the env row the drift."""
    from hal0.memory import embedder_record as er

    _engine_store(monkeypatch, tmp_path)
    er.save_embedder_record(
        er.EmbedderRecord(provider="local", model=_BGE, source="engine-default")
    )
    env_file = tmp_path / "hindsight-llm.env"
    env_file.write_text(f"{_MODEL_KEY}={_HS}\n")
    unit = _unit_as_hal0_renders_it(tmp_path, _BGE)

    status, report, embedder_row, env_row = _two_rows(
        monkeypatch, tmp_path, unit=unit, env_file=env_file
    )

    assert status["recorded"] == _BGE
    assert status["conflict"] == {"on_disk": {"provider": "local", "model": _HS}}
    assert {"key": _MODEL_KEY, "expected": _BGE, "effective": _HS} in report["drift"]
    assert embedder_row.status == "warn"
    assert _BGE in embedder_row.detail and _HS in embedder_row.detail
    assert env_row.status == "warn" and f"{_MODEL_KEY}={_HS}" in env_row.detail


def test_a_running_engine_with_no_embedder_environment_is_not_drift(monkeypatch, tmp_path: Path):
    """hal0 rendered bge-small and the engine has not restarted onto the
    drop-in yet: it runs its default, which is the same embedder."""
    _engine_store(monkeypatch, tmp_path)
    unit = _unit_as_hal0_renders_it(tmp_path, _BGE)

    status, report, embedder_row, _env_row = _two_rows(monkeypatch, tmp_path, unit=unit, live={})

    assert (status["recorded"], status["mixed"], status["conflict"]) == (_BGE, False, None)
    assert [d for d in report["drift"] if "EMBEDDINGS" in d["key"]] == []
    assert embedder_row.status == "pass"
