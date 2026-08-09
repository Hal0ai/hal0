"""Tests for ``hal0 doctor all`` — the read-only evidence roll-up (§21.4).

The extra classifiers are pure (parsed JSON in, ``Check`` out). The
orchestration + exit-code roll-up is driven through the module's seams so no
live API is needed.
"""

from __future__ import annotations

import io
import json as jsonlib

import pytest
import typer
from rich.console import Console

from hal0.cli import doctor_all as da
from hal0.cli.doctor_verify import Check

# ── check_auth_posture ────────────────────────────────────────────────────────


def test_auth_unreachable_warns() -> None:
    c = da.check_auth_posture(None)
    assert c.status == "warn" and c.key == "auth"


def test_auth_open_passes() -> None:
    c = da.check_auth_posture({"auth_required": False, "has_admin_key": False})
    assert c.status == "pass"
    assert "open" in c.detail


def test_auth_required_no_key_warns() -> None:
    c = da.check_auth_posture({"auth_required": True, "has_admin_key": False})
    assert c.status == "warn"
    assert "HAL0_ADMIN_KEY" in c.detail


def test_auth_required_with_key_passes() -> None:
    c = da.check_auth_posture({"auth_required": True, "has_admin_key": True})
    assert c.status == "pass"


# ── check_model_store ─────────────────────────────────────────────────────────


def test_model_store_unreachable_warns() -> None:
    assert da.check_model_store(None).status == "warn"


def test_model_store_clean_when_all_present() -> None:
    models = {"models": [{"id": "a", "path": "/m/a.gguf"}, {"id": "b", "path": "/m/b.gguf"}]}
    c = da.check_model_store(models, exists=lambda _p: True)
    assert c.status == "pass"
    assert "2 registered" in c.detail


def test_model_store_fails_on_dangling() -> None:
    models = [{"id": "a", "path": "/m/a.gguf"}, {"id": "b", "path": "/m/gone.gguf"}]
    c = da.check_model_store(models, exists=lambda p: p == "/m/a.gguf")
    assert c.status == "fail"
    assert not c.critical  # actionable but non-blocking
    assert "gone.gguf" in c.detail or "b" in c.detail


def test_model_store_bad_payload_warns() -> None:
    assert da.check_model_store(12345).status == "warn"


# ── check_migrations ──────────────────────────────────────────────────────────


def test_migrations_planner_unavailable_passes() -> None:
    assert da.check_migrations(None).status == "pass"


def test_migrations_current_passes() -> None:
    assert da.check_migrations((0, 0)).status == "pass"


def test_migrations_pending_warns() -> None:
    c = da.check_migrations((5, 2))
    assert c.status == "warn"
    assert "5 link" in c.detail


# ── check_ui_dist ──────────────────────────────────────────────────────────────


def test_ui_dist_no_api_env_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = da.check_ui_dist(
        api_env_path=tmp_path / "missing.env", current_ui_dist=tmp_path / "current/ui/dist"
    )
    assert c.status == "pass" and c.key == "ui-dist"
    assert "no HAL0_UI_DIST override" in c.detail


def test_ui_dist_no_override_line_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / "api.env"
    env.write_text("HAL0_PORT=8080\nHAL0_LOG_LEVEL=info\n")
    c = da.check_ui_dist(api_env_path=env, current_ui_dist=tmp_path / "current/ui/dist")
    assert c.status == "pass"


def test_ui_dist_matches_current_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    current = tmp_path / "current" / "ui" / "dist"
    current.mkdir(parents=True)
    env = tmp_path / "api.env"
    env.write_text(f"HAL0_UI_DIST={current}\n")
    c = da.check_ui_dist(api_env_path=env, current_ui_dist=current)
    assert c.status == "pass"
    assert "matches the current release bundle" in c.detail


def test_ui_dist_stale_override_warns(tmp_path) -> None:  # type: ignore[no-untyped-def]
    stale = tmp_path / "old-layout" / "ui" / "dist"
    stale.mkdir(parents=True)
    current = tmp_path / "current" / "ui" / "dist"
    current.mkdir(parents=True)
    env = tmp_path / "api.env"
    env.write_text(f"HAL0_UI_DIST={stale}\n")
    c = da.check_ui_dist(api_env_path=env, current_ui_dist=current)
    assert c.status == "warn"
    assert str(stale) in c.detail
    assert "restart hal0-api" in c.detail


def test_ui_dist_commented_out_line_does_not_count(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / "api.env"
    env.write_text('# HAL0_UI_DIST="/old/ui/dist"\n')
    c = da.check_ui_dist(api_env_path=env, current_ui_dist=tmp_path / "current/ui/dist")
    assert c.status == "pass"


def test_ui_dist_quoted_value_is_unquoted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    current = tmp_path / "current" / "ui" / "dist"
    current.mkdir(parents=True)
    env = tmp_path / "api.env"
    env.write_text(f'HAL0_UI_DIST="{current}"\n')
    c = da.check_ui_dist(api_env_path=env, current_ui_dist=current)
    assert c.status == "pass"


# ── check_ports ───────────────────────────────────────────────────────────────


def test_ports_unreachable_warns() -> None:
    assert da.check_ports(None).status == "warn"


def test_ports_none_bound_passes() -> None:
    c = da.check_ports([])
    assert c.status == "pass"


def test_ports_lists_bound() -> None:
    c = da.check_ports([{"port": 8081}, {"port": 8082}, {"name": "x"}])
    assert c.status == "pass"
    assert "8081" in c.detail and "8082" in c.detail


# ── #1501: the row must not cry "unreachable" at a healthy-but-slow box ───────
#
# Live repro on lxc105 (main @ 5be7f2a5, hal0-api active and serving all 18
# slots): GET /api/slots took 11.2-14.6s while `api_get`'s default budget is
# 10.0s. httpx.TimeoutException subclasses httpx.HTTPError, so _api_request
# converts it to CliApiError, _get_any swallows that to None, and check_ports
# rendered None as "slots endpoint unreachable" — a false negative on the
# operator's first-line diagnostic.


def test_ports_fetch_failure_does_not_assert_unreachability() -> None:
    """A failed fetch must not claim the endpoint is unreachable.

    None only means "we didn't get a usable answer" — a timeout against a
    perfectly healthy endpoint lands here too, so the copy has to stay honest
    about which of the two it was.
    """
    c = da.check_ports(None)
    assert c.status == "warn"
    assert "unreachable" not in c.detail.lower() or "slow" in c.detail.lower()


def test_ports_fetch_failure_names_a_followup_command() -> None:
    """Every other failing row names a drill-down; this one must too (#1501)."""
    c = da.check_ports(None)
    assert "hal0 doctor ports" in c.detail


def test_ports_unexpected_shape_is_distinct_from_a_fetch_failure() -> None:
    """A wrong-shaped body is a different fault than no body at all.

    ``check_model_store`` already keeps these apart; ``check_ports`` collapsed
    both into the same "unreachable" string, which is why a shape regression
    would have been misreported as a connectivity problem.
    """
    unreachable = da.check_ports(None)
    wrong_shape = da.check_ports({"slots": [{"port": 8081}]})
    assert wrong_shape.status == "warn"
    assert wrong_shape.detail != unreachable.detail


def test_slots_probe_gets_a_budget_larger_than_the_default() -> None:
    """The slots aggregator is the slowest read-only route — give it room.

    It merges SlotManager entries with upstream-backed ones and container-probes
    each, so on a populated box it legitimately outruns the 10s default that
    every other doctor fetch uses.
    """
    seen: dict[str, float | None] = {}

    def fake_api_get(path: str, *, base: str | None = None, timeout: float = 10.0, **kw: object):
        seen[path] = timeout
        return []

    import hal0.cli._shared as shared

    orig = shared.api_get
    shared.api_get = fake_api_get  # type: ignore[assignment]
    try:
        da._get_any("/api/slots", None, timeout=da.SLOTS_PROBE_TIMEOUT_S)
    finally:
        shared.api_get = orig  # type: ignore[assignment]

    assert seen["/api/slots"] == da.SLOTS_PROBE_TIMEOUT_S
    assert da.SLOTS_PROBE_TIMEOUT_S > 10.0


def test_doctor_ports_subcommand_exists() -> None:
    """The command the warn row points at has to actually resolve (#1501).

    Before this, `hal0 doctor ports` answered "No such command 'ports'. Did you
    mean 'perms'?", so the row named a dead end.
    """
    from hal0.cli import doctor_commands

    names = {c.name for c in typer.main.get_command(doctor_commands.app).commands.values()}
    assert "ports" in names


# ── check_hal0_target ─────────────────────────────────────────────────────────


def test_hal0_target_missing_fails() -> None:
    c = da.check_hal0_target(exists=lambda _p: False)
    assert c.status == "fail"
    assert c.key == "hal0_target"
    assert "not installed" in c.detail


def test_hal0_target_installed_but_disabled_fails() -> None:
    c = da.check_hal0_target(exists=lambda _p: True, is_enabled=lambda: False)
    assert c.status == "fail"
    assert "not enabled" in c.detail


def test_hal0_target_installed_and_enabled_passes() -> None:
    c = da.check_hal0_target(exists=lambda _p: True, is_enabled=lambda: True)
    assert c.status == "pass"


def test_hal0_target_systemctl_unavailable_warns() -> None:
    c = da.check_hal0_target(exists=lambda _p: True, is_enabled=lambda: None)
    assert c.status == "warn"


def test_hal0_target_uses_given_unit_dir(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "hal0.target").write_text("[Unit]\n")
    c = da.check_hal0_target(unit_dir=tmp_path, is_enabled=lambda: True)
    assert c.status == "pass"


def test_hal0_target_enabled_probe_no_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: None)
    assert da._hal0_target_enabled_probe() is None


# ── overall_verdict + exit codes ──────────────────────────────────────────────


def _c(status: str, *, critical: bool = False) -> Check:
    return Check("k", "L", status, "d", critical=critical)


def test_verdict_ok_when_only_warn() -> None:
    assert da.overall_verdict([_c("pass"), _c("warn")]) == "ok"


def test_verdict_fail_on_noncritical_fail() -> None:
    assert da.overall_verdict([_c("pass"), _c("fail")]) == "fail"


def test_verdict_critical_on_critical_fail() -> None:
    assert da.overall_verdict([_c("fail", critical=True), _c("fail")]) == "critical"


def test_exit_code_mapping() -> None:
    assert da._exit_code([_c("pass")]) == 0
    assert da._exit_code([_c("fail")]) == 1
    assert da._exit_code([_c("fail", critical=True)]) == 2


# ── check_voice_stt_weights ───────────────────────────────────────────────────


class _StubProfile:
    def __init__(self, flags: str) -> None:
        self.resolved_flags = flags


class _StubCatalog:
    def __init__(self, flags: str | None) -> None:
        self._flags = flags

    def resolve(self, name: str):
        if self._flags is None:
            raise KeyError(name)
        return _StubProfile(self._flags)


def _stt_weights_with(monkeypatch: pytest.MonkeyPatch, flags: str | None) -> Check:
    import hal0.profiles as profiles_mod

    monkeypatch.setattr(profiles_mod, "ProfileCatalog", lambda: _StubCatalog(flags))
    return da.check_voice_stt_weights()


def test_stt_weights_missing_bundle_fails_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    missing = tmp_path / "moonshine"
    c = _stt_weights_with(monkeypatch, f"--model_path {missing} --model_arch small_streaming")
    assert c.status == "fail"
    assert str(missing) in c.detail


def test_stt_weights_empty_path_warns_hf_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _stt_weights_with(monkeypatch, "--model_arch small_streaming")
    assert c.status == "warn"
    assert "HuggingFace" in c.detail


def test_stt_weights_staged_bundle_passes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    leaf = tmp_path / "moonshine" / "quantized" / "small-streaming-en"
    leaf.mkdir(parents=True)
    (leaf / "encoder_model.ort").write_bytes(b"\0")
    c = _stt_weights_with(monkeypatch, f"--model_path {tmp_path / 'moonshine'}")
    assert c.status == "pass"


def test_stt_weights_profile_absent_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _stt_weights_with(monkeypatch, None)
    assert c.status == "pass"


# ── check_mcp_mounts ────────────────────────────────────────────────────────


def test_mcp_mounts_pass_when_both_answer_with_tools() -> None:
    c = da.check_mcp_mounts(
        {
            "hal0-admin": {"ok": True, "tools": ["slot_list", "model_list"], "error": None},
            "hal0-memory": {"ok": True, "tools": ["memory_add"], "error": None},
        }
    )
    assert c.status == "pass" and c.key == "mcp_mounts"
    assert "hal0-admin=2" in c.detail and "hal0-memory=1" in c.detail


def test_mcp_mounts_401_names_the_repair_command() -> None:
    c = da.check_mcp_mounts(
        {
            "hal0-admin": {"ok": False, "tools": [], "error": "HTTP Error 401: Unauthorized"},
            "hal0-memory": {"ok": True, "tools": ["memory_add"], "error": None},
        }
    )
    assert c.status == "fail"
    assert "hal0-admin" in c.detail
    assert "hal0 agent bootstrap hermes --repair" in c.detail


def test_mcp_mounts_generic_transport_failure_surfaces_raw_error() -> None:
    c = da.check_mcp_mounts(
        {
            "hal0-admin": {"ok": False, "tools": [], "error": "connection refused"},
            "hal0-memory": {"ok": True, "tools": ["memory_add"], "error": None},
        }
    )
    assert c.status == "fail"
    assert "connection refused" in c.detail
    # Non-auth failures don't get the repair-command red herring.
    assert "--repair" not in c.detail


def test_mcp_mounts_zero_tools_is_a_failure() -> None:
    c = da.check_mcp_mounts(
        {
            "hal0-admin": {"ok": True, "tools": [], "error": None},
            "hal0-memory": {"ok": True, "tools": ["memory_add"], "error": None},
        }
    )
    assert c.status == "fail"
    assert "zero tools" in c.detail


def test_probe_builtin_mcp_mounts_hits_both_mount_roots() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def _fake_probe(url: str, **kw: object) -> dict[str, object]:
        seen.append((url, kw))
        return {"ok": True, "tools": ["t"], "error": None}

    rows = da.probe_builtin_mcp_mounts(probe=_fake_probe)
    assert set(rows) == {"hal0-admin", "hal0-memory"}
    urls = {u for u, _ in seen}
    assert urls == {"http://127.0.0.1:8080/mcp/admin", "http://127.0.0.1:8080/mcp/memory"}
    memory_kw = next(kw for u, kw in seen if u.endswith("/mcp/memory"))
    assert memory_kw["private"] is True


# ── check_hermes_mcp_config_auth ────────────────────────────────────────────


def test_hermes_mcp_auth_passes_when_not_provisioned(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = da.check_hermes_mcp_config_auth(
        auth={"auth_required": True}, config_path=tmp_path / "config.yaml"
    )
    assert c.status == "pass"
    assert "not provisioned" in c.detail


def test_hermes_mcp_auth_passes_when_auth_not_required(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mcp_servers:\n  hal0-admin:\n    headers: {}\n")
    c = da.check_hermes_mcp_config_auth(auth={"auth_required": False}, config_path=cfg)
    assert c.status == "pass"
    assert "not required" in c.detail


def test_hermes_mcp_auth_fails_when_bearer_missing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp_servers:\n"
        "  hal0-admin:\n"
        "    headers:\n"
        "      X-hal0-Agent: hermes\n"
        "  hal0-memory:\n"
        "    headers:\n"
        "      X-hal0-Agent: hermes\n"
    )
    c = da.check_hermes_mcp_config_auth(auth={"auth_required": True}, config_path=cfg)
    assert c.status == "fail"
    assert "hal0-admin" in c.detail and "hal0-memory" in c.detail
    assert "hal0 agent bootstrap hermes --repair" in c.detail


def test_hermes_mcp_auth_passes_when_bearer_present(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp_servers:\n"
        "  hal0-admin:\n"
        "    headers:\n"
        "      Authorization: Bearer some-key\n"
        "  hal0-memory:\n"
        "    headers:\n"
        "      Authorization: Bearer some-key\n"
    )
    c = da.check_hermes_mcp_config_auth(auth={"auth_required": True}, config_path=cfg)
    assert c.status == "pass"


def test_hermes_mcp_auth_partial_bearer_is_named(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Only hal0-memory drifted (e.g. added by hand after a stale repair) —
    the missing-list must name exactly the offender, not both."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp_servers:\n"
        "  hal0-admin:\n"
        "    headers:\n"
        "      Authorization: Bearer some-key\n"
        "  hal0-memory:\n"
        "    headers:\n"
        "      X-hal0-Agent: hermes\n"
    )
    c = da.check_hermes_mcp_config_auth(auth={"auth_required": True}, config_path=cfg)
    assert c.status == "fail"
    assert "hal0-memory" in c.detail
    assert "hal0-admin" not in c.detail


def test_hermes_mcp_auth_warns_on_unreadable_config(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mcp_servers: {}\n")

    def _boom(_p):
        raise OSError("permission denied")

    c = da.check_hermes_mcp_config_auth(
        auth={"auth_required": True}, config_path=cfg, read_text=_boom
    )
    assert c.status == "warn"


# ── build_all_checks orchestration ────────────────────────────────────────────


def test_build_all_checks_composes_verify_plus_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        da,
        "gather_payloads",
        lambda base=None: {
            "health": {"version": "1"},
            "urls": {"api": "http://halo.local:8080"},
            "system": {"checks": {"slot_manager": {"slots": 1, "errored": []}}},
            "capabilities": {"selections": {"embed": "x"}},
            "memory": {"engine": None},
            "services": {"services": []},
        },
    )

    # `timeout` is keyword-only on the real seam (#1501 gave /api/slots its own
    # budget); accept and ignore it so this stub tracks the signature.
    def _fake_get(path: str, base=None, *, timeout: float = 10.0):
        return {
            "/api/auth/status": {"auth_required": False, "has_admin_key": False},
            "/api/models": {"models": [{"id": "a", "path": "/m/a"}]},
            "/api/slots": [{"port": 8081}],
        }.get(path)

    monkeypatch.setattr(da, "_get_any", _fake_get)
    monkeypatch.setattr("hal0.cli.doctor_commands.pending_layout_migration", lambda: (0, 0))
    # ui-dist (#1589) reads real api.env otherwise; neutralise it so this
    # composition test asserts the row list, not the host's api.env content.
    monkeypatch.setattr(
        da, "check_ui_dist", lambda: Check("ui-dist", "Dashboard bundle", "pass", "stubbed")
    )
    # model file existence + hal0.target unit file: pretend present so
    # neither spuriously fails.
    monkeypatch.setattr(da.Path, "exists", lambda self: True)
    monkeypatch.setattr(da, "_hal0_target_enabled_probe", lambda: True)
    # The secret-mode row (#1466) stats real paths; pin it to nothing so this
    # composition test asserts the row list, not the host's /etc/hal0 modes.
    monkeypatch.setattr(da, "_SECRET_FILES", ())
    # Privileged seams (#1465): probed against the real filesystem otherwise,
    # which is neither present nor relevant in CI — pretend all installed.
    monkeypatch.setattr(da, "probe_seams", lambda: [])
    # Moonshine weights row (stt-weights) preflights a real filesystem path
    # via the provider helper; neutralise it so this composition test asserts
    # the row list, not the host's staged-weights state.
    monkeypatch.setattr("hal0.providers.moonshine.check_moonshine_weights", lambda _p: None)
    # MCP mount rows would otherwise dial the real loopback API / read the
    # real host config.yaml — neutralise both so this composition test
    # asserts the row list, not live network or host state.
    monkeypatch.setattr(da, "probe_builtin_mcp_mounts", lambda probe=None: {})
    monkeypatch.setattr(
        da,
        "check_hermes_mcp_config_auth",
        lambda **_kw: Check("hermes_mcp_auth", "Hermes MCP config auth", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_hindsight_llm_auth",
        lambda **_kw: Check("hindsight_llm_auth", "Memory engine LLM auth", "pass", "stubbed"),
    )

    checks = da.build_all_checks()
    keys = [c.key for c in checks]
    # 7 verify rows + 12 extras.
    assert keys[-12:] == [
        "auth",
        "models",
        "migrations",
        "ui-dist",
        "ports",
        "hal0_target",
        "secret-modes",
        "stt-weights",
        "seams",
        "mcp_mounts",
        "hermes_mcp_auth",
        "hindsight_llm_auth",
    ]
    assert "api" in keys and "runners" in keys
    assert da.overall_verdict(checks) == "ok"


# ── hindsight LLM auth (engine-side silent-401 guard) ────────────────────────


def test_hindsight_llm_auth_passes_when_engine_not_installed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    check = da.check_hindsight_llm_auth(
        auth={"auth_required": True},
        unit_path=tmp_path / "missing.service",
        env_path=tmp_path / "hindsight-llm.env",
    )
    assert check.status == "pass"


def test_hindsight_llm_auth_passes_when_auth_off(tmp_path) -> None:  # type: ignore[no-untyped-def]
    unit = tmp_path / "hindsight-api.service"
    unit.write_text("[Service]\n")
    check = da.check_hindsight_llm_auth(
        auth={"auth_required": False}, unit_path=unit, env_path=tmp_path / "hindsight-llm.env"
    )
    assert check.status == "pass"


def test_hindsight_llm_auth_fails_on_missing_or_placeholder_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    unit = tmp_path / "hindsight-api.service"
    unit.write_text("[Service]\n")
    env = tmp_path / "hindsight-llm.env"
    # Missing file.
    check = da.check_hindsight_llm_auth(auth={"auth_required": True}, unit_path=unit, env_path=env)
    assert check.status == "fail"
    assert "restart hindsight-api" in check.detail
    # Placeholder value.
    env.write_text("HINDSIGHT_API_LLM_API_KEY=hal0-local-noauth\n")
    check = da.check_hindsight_llm_auth(auth={"auth_required": True}, unit_path=unit, env_path=env)
    assert check.status == "fail"


def test_hindsight_llm_auth_passes_on_current_key_fails_on_stale(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    unit = tmp_path / "hindsight-api.service"
    unit.write_text("[Service]\n")
    env = tmp_path / "hindsight-llm.env"
    env.write_text("HINDSIGHT_API_LLM_API_KEY=live-key-123\n")
    monkeypatch.setattr(
        "hal0.service_identity.keys_from_api_env", lambda: {"HAL0_CLIENT_KEY": "live-key-123"}
    )
    check = da.check_hindsight_llm_auth(auth={"auth_required": True}, unit_path=unit, env_path=env)
    assert check.status == "pass"

    monkeypatch.setattr(
        "hal0.service_identity.keys_from_api_env", lambda: {"HAL0_CLIENT_KEY": "rotated-away"}
    )
    check = da.check_hindsight_llm_auth(auth={"auth_required": True}, unit_path=unit, env_path=env)
    assert check.status == "fail"
    assert "stale" in check.detail


# ── command ───────────────────────────────────────────────────────────────────


def test_command_json_emits_rows_and_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(da, "build_all_checks", lambda: [_c("pass"), _c("fail")])
    buf = io.StringIO()
    monkeypatch.setattr(da, "console", Console(file=buf))
    with pytest.raises(typer.Exit) as exc:
        da.doctor_all_cmd(json_output=True)
    assert exc.value.exit_code == 1
    rows = jsonlib.loads(buf.getvalue())
    assert len(rows) == 2
    assert rows[0]["status"] == "pass"


def test_command_human_exit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(da, "build_all_checks", lambda: [_c("pass"), _c("warn")])
    monkeypatch.setattr(da, "console", Console(file=io.StringIO()))
    with pytest.raises(typer.Exit) as exc:
        da.doctor_all_cmd(json_output=False)
    assert exc.value.exit_code == 0
