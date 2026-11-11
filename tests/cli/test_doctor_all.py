"""Tests for ``hal0 doctor all`` — the read-only evidence roll-up (§21.4).

The extra classifiers are pure (parsed JSON in, ``Check`` out). The
orchestration + exit-code roll-up is driven through the module's seams so no
live API is needed.
"""

from __future__ import annotations

import io
import json as jsonlib
import os
from pathlib import Path

import pytest
import typer
from rich.console import Console

from hal0.cli import doctor_all as da
from hal0.cli.doctor_verify import Check


def test_openwebui_task_model_warns_for_removed_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "openwebui.env"
    env.write_text("TASK_MODEL_EXTERNAL=utility\n", encoding="utf-8")
    monkeypatch.setattr(da.paths, "openwebui_env", lambda: env)
    slots = [{"name": "agent"}, {"name": "memory"}]
    row = da.check_openwebui_task_model(slots)
    assert row.status == "warn"
    assert "utility" in row.detail
    env.write_text("TASK_MODEL_EXTERNAL=hal0/agent\n", encoding="utf-8")
    assert da.check_openwebui_task_model(slots).status == "pass"


def test_openwebui_task_model_ignores_an_upstream_model_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # TASK_MODEL_EXTERNAL may legitimately hold an upstream model id, not a
    # slot name. The row only warns about a slot reference.
    env = tmp_path / "openwebui.env"
    monkeypatch.setattr(da.paths, "openwebui_env", lambda: env)
    slots = [{"name": "agent"}, {"name": "memory"}]

    env.write_text("TASK_MODEL_EXTERNAL=qwen3:30b-a3b\n", encoding="utf-8")
    row = da.check_openwebui_task_model(slots)
    assert row.status == "pass"

    env.write_text("TASK_MODEL_EXTERNAL=bge-base-en-v1.5-q4_k_m\n", encoding="utf-8")
    assert da.check_openwebui_task_model(slots).status == "pass"


def test_openwebui_task_model_warns_for_a_missing_hal0_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "openwebui.env"
    monkeypatch.setattr(da.paths, "openwebui_env", lambda: env)
    slots = [{"name": "agent"}, {"name": "memory"}]

    env.write_text("TASK_MODEL_EXTERNAL=hal0/utility\n", encoding="utf-8")
    row = da.check_openwebui_task_model(slots)
    assert row.status == "warn"
    assert "hal0/utility" in row.detail


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


def test_auth_open_loopback_passes_even_missing_lan_exposed_field() -> None:
    """An older API build with no ``lan_exposed`` field degrades to False
    (assume loopback) rather than warning on missing data."""
    c = da.check_auth_posture({"auth_required": False, "has_admin_key": False})
    assert c.status == "pass"


def test_auth_open_lan_exposed_no_key_warns() -> None:
    c = da.check_auth_posture({"auth_required": False, "has_admin_key": False, "lan_exposed": True})
    assert c.status == "warn"
    assert "no admin key" in c.detail


def test_auth_open_lan_exposed_with_key_warns() -> None:
    c = da.check_auth_posture({"auth_required": False, "has_admin_key": True, "lan_exposed": True})
    assert c.status == "warn"
    assert "reachable from your network" in c.detail


def test_auth_lan_gated_copy_matches_the_gate() -> None:
    """The LAN admin gate (hal0.api.auth._lan_admin_gate) is per route CLASS:
    it gates ADMIN reads (settings, memory, logs) as well as ADMIN changes,
    and leaves CLIENT routes such as /v1 inference open, including their
    mutations. The copy must not claim "reads stay open" or that every
    mutating route is gated."""
    c = da.check_auth_posture({"auth_required": False, "has_admin_key": True, "lan_exposed": True})
    detail = " ".join(c.detail.split())
    assert "reads and inference stay open" not in detail
    assert "mutating routes already require" not in detail
    assert "settings" in detail and "logs" in detail
    assert "/v1" in detail
    assert "hal0 auth require on" in detail


def test_auth_lan_no_key_points_at_reset_key() -> None:
    """With no admin key the gate cannot apply; minting one (reset-key prints
    it, rotate does not) is what arms it."""
    c = da.check_auth_posture({"auth_required": False, "has_admin_key": False, "lan_exposed": True})
    detail = " ".join(c.detail.split())
    assert "sudo hal0 auth reset-key" in detail
    assert "hal0 auth require on" in detail


def test_auth_required_no_key_points_at_reset_key() -> None:
    c = da.check_auth_posture({"auth_required": True, "has_admin_key": False})
    assert "sudo hal0 auth reset-key" in c.detail


def test_auth_open_loopback_bind_passes_regardless_of_key() -> None:
    c = da.check_auth_posture({"auth_required": False, "has_admin_key": True, "lan_exposed": False})
    assert c.status == "pass"
    assert "open" in c.detail


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


# ── check_leftover_registry_toml ───────────────────────────────────────────────


def test_leftover_registry_toml_absent_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert da.check_leftover_registry_toml(tmp_path / "registry.toml").status == "pass"


def test_leftover_registry_toml_warns_with_reason_and_remedy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    toml = tmp_path / "registry.toml"
    toml.write_text("[models]\n")
    c = da.check_leftover_registry_toml(toml)
    assert c.status == "warn"
    assert "leftover registry.toml could not be retired" in c.detail
    assert "remove it manually" in c.detail


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


# ── check_api_env_entries (#2942) ────────────────────────────────────────────

_PLANTED_VALUE = "/tmp/planted-value-xyz"


def test_api_env_entries_missing_file_passes(tmp_path: Path) -> None:
    c = da.check_api_env_entries(api_env_path=tmp_path / "missing.env")
    assert c.status == "pass" and c.key == "api-env-entries"


def test_api_env_entries_clean_file_passes(tmp_path: Path) -> None:
    env = tmp_path / "api.env"
    env.write_text(
        'HAL0_PORT=8080\nHAL0_UI_DIST="/x"\nHF_TOKEN="hf_x"\nOPENROUTER_API_KEY="k"\n'
        "# LD_PRELOAD=/commented/out.so\n"
    )
    c = da.check_api_env_entries(api_env_path=env)
    assert c.status == "pass", c.detail


def test_api_env_entries_unreadable_warns(tmp_path: Path) -> None:
    def _deny(_p: Path) -> str:
        raise PermissionError("denied")

    c = da.check_api_env_entries(api_env_path=tmp_path / "api.env", read_text=_deny)
    assert c.status == "warn"
    assert "sudo hal0 doctor all" in c.detail


@pytest.mark.parametrize(
    "name",
    [
        # #2936 loader and interpreter names
        "LD_PRELOAD",
        "PYTHONPATH",
        "PATH",
        "NODE_OPTIONS",
        "GIT_SSH_COMMAND",
        "HERMES_HOME",
        # #2997-#3000
        "SSLKEYLOGFILE",
        "OPENSSL_CONF",
        "OPENSSL_MODULES",
        "OPENSSL_ENGINES",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "ld_preload",
    ],
)
def test_api_env_entries_flags_process_control_names(tmp_path: Path, name: str) -> None:
    env = tmp_path / "api.env"
    env.write_text(f'HF_TOKEN="hf_x"\n{name}="{_PLANTED_VALUE}"\n')
    c = da.check_api_env_entries(api_env_path=env)
    assert c.status == "warn"
    assert name in c.detail
    assert "HF_TOKEN" not in c.detail
    assert _PLANTED_VALUE not in c.detail
    assert "Settings → Secrets" in c.detail
    assert "proxy" not in c.detail


@pytest.mark.parametrize(
    "name",
    ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"],
)
def test_api_env_entries_flags_proxy_names_separately(tmp_path: Path, name: str) -> None:
    env = tmp_path / "api.env"
    env.write_text(f"{name}=http://10.9.9.9:3128\n")
    c = da.check_api_env_entries(api_env_path=env)
    assert c.status == "warn"
    assert name in c.detail
    assert "proxy" in c.detail
    assert "set them as root" in c.detail
    assert "10.9.9.9" not in c.detail
    assert "load code" not in c.detail


def test_api_env_entries_groups_every_finding_in_one_row(tmp_path: Path) -> None:
    env = tmp_path / "api.env"
    env.write_text('PYTHONPATH="/a"\nLD_PRELOAD="/b"\nHTTPS_PROXY="http://p:1"\n')
    c = da.check_api_env_entries(api_env_path=env)
    assert c.status == "warn"
    assert "LD_PRELOAD, PYTHONPATH" in c.detail
    assert "HTTPS_PROXY" in c.detail


@pytest.fixture
def _trusted_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Treat the test user as root and ``tmp_path`` as the trusted top (as test_tls_trust_env)."""
    from hal0.api import _env_store

    root = tmp_path.resolve() / "trust"
    root.mkdir(mode=0o755)
    monkeypatch.setattr(_env_store, "_TRUSTED_UID", os.getuid())
    monkeypatch.setattr(_env_store, "_TRUST_ANCHOR", root.parent)
    return root


def test_api_env_entries_accepts_a_trusted_ca_bundle(tmp_path: Path, _trusted_root: Path) -> None:
    bundle = _trusted_root / "ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    bundle.chmod(0o644)
    env = tmp_path / "api.env"
    env.write_text(f'SSL_CERT_FILE="{bundle}"\nREQUESTS_CA_BUNDLE={bundle}\n')
    c = da.check_api_env_entries(api_env_path=env)
    assert c.status == "pass", c.detail


@pytest.mark.parametrize("name", ["SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"])
def test_api_env_entries_flags_a_writable_ca_bundle(
    tmp_path: Path, _trusted_root: Path, name: str
) -> None:
    bundle = _trusted_root / "ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    bundle.chmod(0o666)
    env = tmp_path / "api.env"
    env.write_text(f'{name}="{bundle}"\n')
    c = da.check_api_env_entries(api_env_path=env)
    assert c.status == "warn"
    assert name in c.detail
    assert "group- or world-writable" in c.detail
    assert str(bundle) not in c.detail


def test_api_env_entries_flags_a_missing_ca_bundle(tmp_path: Path, _trusted_root: Path) -> None:
    env = tmp_path / "api.env"
    env.write_text(f'CURL_CA_BUNDLE="{_trusted_root}/nope.pem"\n')
    c = da.check_api_env_entries(api_env_path=env)
    assert c.status == "warn"
    assert "CURL_CA_BUNDLE" in c.detail


def test_env_file_entries_undoes_writer_quoting() -> None:
    text = 'A="x\\"y\\\\z"\n# B=1\nC=plain\nnot a line\n'
    assert da._env_file_entries(text) == [("A", 'x"y\\z'), ("C", "plain")]


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


# ── check_hardware_freshness (#1862/H9) ───────────────────────────────────────


def test_hardware_freshness_missing_file_warns(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "hardware.json"
    monkeypatch.setattr("hal0.config.paths.hardware_json", lambda: missing)
    c = da.check_hardware_freshness()
    assert c.status == "warn"
    assert c.key == "hardware_freshness"
    assert "hal0 config hardware --refresh" in c.detail


def test_hardware_freshness_fresh_cache_passes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from hal0.config.schema import HardwareInfo
    from hal0.hardware.freshness import current_kernel_string

    path = tmp_path / "hardware.json"
    path.write_text("{}")
    kernel = current_kernel_string() or "Linux version test"
    info = HardwareInfo(kernel=kernel, probed_at="2026-09-05T12:00:00+00:00")
    monkeypatch.setattr("hal0.config.paths.hardware_json", lambda: path)
    monkeypatch.setattr("hal0.config.loader.load_hardware_info", lambda: info)
    monkeypatch.setattr("hal0.hardware.freshness.current_kernel_string", lambda: kernel)
    monkeypatch.setattr(
        "hal0.hardware.freshness._boot_time_epoch_s",
        lambda: 0.0,
    )
    c = da.check_hardware_freshness()
    assert c.status == "pass"


def test_hardware_freshness_stale_cache_warns(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from hal0.config.schema import HardwareInfo

    path = tmp_path / "hardware.json"
    path.write_text("{}")
    stale = HardwareInfo(kernel="Linux version 1.0.0-old", probed_at="2020-01-01T00:00:00+00:00")
    monkeypatch.setattr("hal0.config.paths.hardware_json", lambda: path)
    monkeypatch.setattr("hal0.config.loader.load_hardware_info", lambda: stale)
    monkeypatch.setattr(
        "hal0.hardware.freshness.current_kernel_string", lambda: "Linux version 9.9.9-new"
    )
    c = da.check_hardware_freshness()
    assert c.status == "warn"
    assert "stale" in c.detail


# ── check_seed_context_envelope (#1868) ───────────────────────────────────────


def test_seed_context_envelope_no_slots_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = da.check_seed_context_envelope(slots_dir=tmp_path)
    assert c.status == "pass"


def _profile_with_window(home: str, name: str, ctx_size: int) -> None:
    """Add a custom profile (ADR-0029: the window is the profile's) to the tmp catalog."""
    from pathlib import Path

    path = Path(home) / "etc" / "hal0" / "profiles.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"[profile.{name}]\nctx_size = {ctx_size}\n\n")


def test_seed_context_envelope_flags_an_oversized_profile_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path, tmp_hal0_home: str
) -> None:  # type: ignore[no-untyped-def]
    from hal0.config.schema import HardwareInfo

    _profile_with_window(tmp_hal0_home, "huge-window", 16_000_000)
    (tmp_path / "brain.toml").write_text('profile = "huge-window"\n', encoding="utf-8")
    cpu_only = HardwareInfo(platform="bare-metal-cpu-only", ram_mb=4096, gpus=[])
    monkeypatch.setattr(
        "hal0.hardware.freshness.resolve_fresh_hardware_info", lambda: (cpu_only, None)
    )
    c = da.check_seed_context_envelope(slots_dir=tmp_path)
    assert c.status == "warn"
    assert "brain" in c.detail
    assert "profile ctx_size" in c.detail
    assert "[model]" not in c.detail


def test_seed_context_envelope_flags_an_oversized_slot_tune_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """A ``[tune].ctx_size`` above the envelope is flagged and named as the slot's override."""
    from hal0.config.schema import HardwareInfo

    (tmp_path / "brain.toml").write_text(
        'profile = "brain"\n[tune]\nctx_size = 16000000\n', encoding="utf-8"
    )
    cpu_only = HardwareInfo(platform="bare-metal-cpu-only", ram_mb=4096, gpus=[])
    monkeypatch.setattr(
        "hal0.hardware.freshness.resolve_fresh_hardware_info", lambda: (cpu_only, None)
    )
    c = da.check_seed_context_envelope(slots_dir=tmp_path)
    assert c.status == "warn"
    assert "brain" in c.detail
    assert "[tune]" in c.detail


def test_seed_context_envelope_ignores_a_stale_slot_level_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """A leftover ``[model].context_size`` is no window any more (ADR-0029): only
    the slot profile's ``ctx_size`` is checked against the envelope."""
    from hal0.config.schema import HardwareInfo

    (tmp_path / "brain.toml").write_text(
        'profile = "brain"\n[model]\ncontext_size = 999999999\n', encoding="utf-8"
    )
    cpu_only = HardwareInfo(platform="bare-metal-cpu-only", ram_mb=4096, gpus=[])
    monkeypatch.setattr(
        "hal0.hardware.freshness.resolve_fresh_hardware_info", lambda: (cpu_only, None)
    )
    assert da.check_seed_context_envelope(slots_dir=tmp_path).status == "pass"


def test_seed_context_envelope_passes_when_within_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path, tmp_hal0_home: str
) -> None:  # type: ignore[no-untyped-def]
    from hal0.config.schema import GPUInfo, HardwareInfo

    _profile_with_window(tmp_hal0_home, "eight-k", 8192)
    (tmp_path / "brain.toml").write_text('profile = "eight-k"\n', encoding="utf-8")
    big_box = HardwareInfo(
        platform="strix-halo",
        ram_mb=131072,
        gpus=[GPUInfo(vendor="amd", vram_mb=131072, compute_capable=True, vulkan_capable=True)],
    )
    monkeypatch.setattr(
        "hal0.hardware.freshness.resolve_fresh_hardware_info", lambda: (big_box, None)
    )
    c = da.check_seed_context_envelope(slots_dir=tmp_path)
    assert c.status == "pass"


# ── check_agent_uid_isolation (ADR-0002) ──────────────────────────────────────


def test_agent_uid_shared_with_api_warns() -> None:
    """The shipped default: both units are User=hal0 → advisory warn, never fail.

    This is the row's whole reason to exist — an operator reading `hal0 doctor`
    on a stock box must be told that the bundled agent shares the API's UID and
    can therefore read its credentials out of /proc.
    """
    c = da.check_agent_uid_isolation(
        unit_user=lambda _u: "hal0",
        agent_units=lambda: ["hal0-agent@hermes.service"],
    )
    assert c.key == "agent-uid"
    assert c.status == "warn"
    assert c.critical is False
    assert "hal0-agent@hermes.service" in c.detail
    assert "SECURITY.md" in c.detail


def test_agent_uid_split_passes() -> None:
    """The 1.1 posture: the agent has its own account → clean row."""
    users = {"hal0-api.service": "hal0", "hal0-agent@hermes.service": "hal0-agent"}
    c = da.check_agent_uid_isolation(
        unit_user=lambda u: users[u],
        agent_units=lambda: ["hal0-agent@hermes.service"],
    )
    assert c.status == "pass"


def test_agent_uid_no_agent_unit_passes() -> None:
    """No bundled agent installed — there is no boundary to report on."""
    c = da.check_agent_uid_isolation(
        unit_user=lambda _u: "hal0",
        agent_units=lambda: [],
    )
    assert c.status == "pass"
    assert "no bundled agent" in c.detail


def test_agent_uid_unknown_api_user_warns() -> None:
    """systemctl could not answer for hal0-api — say unknown, do not claim clean."""
    c = da.check_agent_uid_isolation(
        unit_user=lambda u: None if u == "hal0-api.service" else "hal0",
        agent_units=lambda: ["hal0-agent@hermes.service"],
    )
    assert c.status == "warn"
    assert "unknown" in c.detail


def test_agent_uid_flags_only_the_sharing_instance() -> None:
    """A mixed box names the instance that shares, not the one that does not."""
    users = {
        "hal0-api.service": "hal0",
        "hal0-agent@hermes.service": "hal0",
        "hal0-agent@other.service": "hal0-agent",
    }
    c = da.check_agent_uid_isolation(
        unit_user=lambda u: users[u],
        agent_units=lambda: ["hal0-agent@hermes.service", "hal0-agent@other.service"],
    )
    assert c.status == "warn"
    assert "hal0-agent@hermes.service" in c.detail
    assert "hal0-agent@other.service" not in c.detail


def test_agent_uid_probes_degrade_without_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    """No systemctl → *unknown*, never an empty inventory (which would read clean)."""
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: None)
    assert da._unit_user("hal0-api.service") is None
    assert da._agent_units() is None


def test_unit_user_reports_root_for_an_empty_user_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """systemd renders an unset User= as the empty string; that means root."""
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: "/usr/bin/systemctl")

    class _R:
        returncode = 0
        stdout = "\n"

    monkeypatch.setattr(da.subprocess, "run", lambda *_a, **_k: _R())
    assert da._unit_user("hal0-api.service") == "root"


def test_agent_units_parses_list_units_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: "/usr/bin/systemctl")
    monkeypatch.setattr(da, "_provisioned_agent_units", lambda: [])

    class _R:
        returncode = 0
        stdout = (
            "hal0-agent@hermes.service loaded active running hal0 agent (hermes)\n"
            "hal0-agent@other.service  loaded inactive dead  hal0 agent (other)\n"
        )

    monkeypatch.setattr(da.subprocess, "run", lambda *_a, **_k: _R())
    assert da._agent_units() == ["hal0-agent@hermes.service", "hal0-agent@other.service"]


def test_agent_units_is_unknown_when_systemctl_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manager error must not be laundered into "no agent installed"."""
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: "/usr/bin/systemctl")

    class _R:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(da.subprocess, "run", lambda *_a, **_k: _R())
    assert da._agent_units() is None


def test_agent_units_finds_a_stopped_disabled_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """`list-units` only reports units in memory; the enable symlink outlives that.

    An operator who stopped the agent has not uninstalled it — the boundary is
    still there the moment it starts again, so the row must still see it.
    """
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: "/usr/bin/systemctl")

    class _R:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(da.subprocess, "run", lambda *_a, **_k: _R())
    monkeypatch.setattr(da, "_provisioned_agent_units", lambda: ["hal0-agent@hermes.service"])
    assert da._agent_units() == ["hal0-agent@hermes.service"]


def test_provisioned_agent_units_reads_wants_symlinks(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    wants = tmp_path / "multi-user.target.wants"
    wants.mkdir()
    (wants / "hal0-agent@hermes.service").symlink_to(tmp_path / "hal0-agent@.service")
    (wants / "hal0-api.service").symlink_to(tmp_path / "hal0-api.service")
    assert da._provisioned_agent_units(tmp_path) == ["hal0-agent@hermes.service"]


def test_provisioned_agent_units_ignores_the_shipped_dropin_dir(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A drop-in dir alone is not evidence of an installed agent.

    `installer/install.sh` writes `hal0-agent@hermes.service.d/10-hal0.conf`
    on every install "whether or not bootstrap has been run", so a box built
    with the documented `HAL0_SKIP_HERMES=1` has the drop-in and no agent.
    Counting it would warn about an agent the operator deliberately declined.
    """
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    dropin = tmp_path / "hal0-agent@hermes.service.d"
    dropin.mkdir()
    (dropin / "10-hal0.conf").write_text("[Service]\n")
    assert da._provisioned_agent_units(tmp_path) == []


def test_provisioned_agent_units_reads_per_agent_config(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    agents = tmp_path / "home" / "etc" / "hal0" / "agents"
    agents.mkdir(parents=True)
    (agents / "hermes.toml").write_text("")
    (agents / "other.env").write_text("")
    (agents / "README").write_text("")
    assert da._provisioned_agent_units(tmp_path) == [
        "hal0-agent@hermes.service",
        "hal0-agent@other.service",
    ]


def test_provisioned_agent_units_missing_unit_dir_is_empty(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    assert da._provisioned_agent_units(tmp_path / "nope") == []


def test_agent_uid_unknown_inventory_warns() -> None:
    """systemd could not be asked → warn, not a clean row."""
    c = da.check_agent_uid_isolation(
        unit_user=lambda _u: "hal0",
        agent_units=lambda: None,
    )
    assert c.status == "warn"
    assert "could not enumerate" in c.detail


def test_agent_uid_unknown_agent_user_warns_instead_of_passing() -> None:
    """An unreadable agent User= must not fall through to "runs as a different user"."""
    c = da.check_agent_uid_isolation(
        unit_user=lambda u: "hal0" if u == "hal0-api.service" else None,
        agent_units=lambda: ["hal0-agent@hermes.service"],
    )
    assert c.status == "warn"
    assert "hal0-agent@hermes.service" in c.detail
    assert "could not read User=" in c.detail


def test_agent_uid_compares_resolved_uids_not_strings() -> None:
    """A drop-in spelling one side numerically is still the same UID to the kernel."""
    users = {"hal0-api.service": "hal0", "hal0-agent@hermes.service": "991"}
    c = da.check_agent_uid_isolation(
        unit_user=lambda u: users[u],
        agent_units=lambda: ["hal0-agent@hermes.service"],
        resolve_uid=lambda user: 991 if user in ("hal0", "991") else None,
    )
    assert c.status == "warn", "numeric-vs-name spelling must not read as a UID split"
    assert "uid 991" in c.detail


def test_agent_uid_distinct_uids_pass_despite_a_shared_prefix() -> None:
    users = {"hal0-api.service": "hal0", "hal0-agent@hermes.service": "hal0-agent"}
    c = da.check_agent_uid_isolation(
        unit_user=lambda u: users[u],
        agent_units=lambda: ["hal0-agent@hermes.service"],
        resolve_uid=lambda user: {"hal0": 991, "hal0-agent": 992}[user],
    )
    assert c.status == "pass"


def test_resolve_uid_accepts_a_numeric_literal_and_unknown_names() -> None:
    assert da._resolve_uid("4242") == 4242
    assert da._resolve_uid("definitely-not-a-real-account-x9") is None


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
    monkeypatch.setattr(da, "_moonshine_slots", lambda: ({"stt": "moonshine"}, []))
    return da.check_voice_stt_weights()


def test_stt_weights_skipped_when_no_slot_uses_moonshine(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """#2437: seeded profile + missing dir must not FAIL if nothing uses it."""
    import hal0.profiles as profiles_mod

    missing = tmp_path / "nope"
    monkeypatch.setattr(
        profiles_mod, "ProfileCatalog", lambda: _StubCatalog(f"--model_path {missing}")
    )
    monkeypatch.setattr(da, "_moonshine_slots", lambda: ({}, []))
    c = da.check_voice_stt_weights()
    assert c.status == "pass"
    assert "no slot uses" in c.detail


def _slot_cfgs(monkeypatch: pytest.MonkeyPatch, cfgs: dict, families: dict | None = None) -> None:
    """Install slot configs; ``families`` maps profile name -> runtime_family.

    Dispatch itself (``_spec_provider_for``) is NOT stubbed: only the profile
    catalog lookup it performs is, so the test exercises the real authority.
    """
    import types

    import hal0.config.loader as loader
    import hal0.providers.container as container
    from hal0.config.schema import SlotConfig

    built = {n: SlotConfig.model_validate({"name": n, "port": 8100, **c}) for n, c in cfgs.items()}
    monkeypatch.setattr(loader, "list_slots", lambda: list(built))
    monkeypatch.setattr(loader, "load_slot_config", lambda n: built[n])

    class _Cat:
        def resolve(self, name: str):
            if name not in (families or {}):
                raise KeyError(name)
            return types.SimpleNamespace(runtime_family=families[name])

    monkeypatch.setattr(container, "ProfileCatalog", lambda: _Cat())


def test_moonshine_slots_profileless_transcription_on_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _slot_cfgs(monkeypatch, {"stt": {"type": "transcription", "device": "cpu"}})
    users, unreadable = da._moonshine_slots()
    assert users == {"stt": "moonshine"}  # provider default profile
    assert unreadable == []


def test_moonshine_slots_custom_profile_with_moonshine_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _slot_cfgs(
        monkeypatch,
        {
            "stt": {"type": "transcription", "device": "cpu", "profile": "my-stt"},
            "chat": {"device": "gpu-rocm", "profile": "rocmfpx-rocm"},
        },
        families={"my-stt": "moonshine", "rocmfpx-rocm": "llama-server"},
    )
    users, _ = da._moonshine_slots()
    assert users == {"stt": "my-stt"}


def test_moonshine_slots_ignores_stale_provider_label_on_npu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``provider`` is a deprecated label dispatch ignores; npu routes to FLM."""
    _slot_cfgs(
        monkeypatch,
        {"stt": {"type": "transcription", "device": "npu", "provider": "moonshine"}},
    )
    users, _ = da._moonshine_slots()
    assert users == {}


def test_moonshine_slots_reports_unreadable_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.config.loader as loader

    monkeypatch.setattr(loader, "list_slots", lambda: ["bad"])

    def _boom(_n: str):
        raise OSError("permission denied")

    monkeypatch.setattr(loader, "load_slot_config", _boom)
    users, unreadable = da._moonshine_slots()
    assert users == {}
    assert unreadable == ["bad"]


def test_stt_weights_fresh_install_passes_without_checking(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Fresh install: seeded profile, missing weights dir, no slot uses it."""
    import hal0.profiles as profiles_mod

    _slot_cfgs(monkeypatch, {"chat": {"device": "gpu-rocm"}})
    monkeypatch.setattr(
        profiles_mod, "ProfileCatalog", lambda: _StubCatalog(f"--model_path {tmp_path / 'nope'}")
    )
    c = da.check_voice_stt_weights()
    assert c.status == "pass"


def test_stt_weights_unreadable_slot_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(da, "_moonshine_slots", lambda: ({}, ["bad"]))
    c = da.check_voice_stt_weights()
    assert c.status == "warn"
    assert "bad" in c.detail


def test_stt_weights_checks_the_profile_the_slot_uses(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import hal0.profiles as profiles_mod

    seen: list[str] = []

    class _Cat:
        def resolve(self, name: str):
            seen.append(name)
            return _StubProfile(f"--model_path {tmp_path / name}")

    monkeypatch.setattr(profiles_mod, "ProfileCatalog", lambda: _Cat())
    monkeypatch.setattr(da, "_moonshine_slots", lambda: ({"stt": "my-stt"}, []))
    c = da.check_voice_stt_weights()
    assert seen == ["my-stt"]
    assert c.status == "fail"
    assert "my-stt" in c.detail


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


def test_stt_weights_profile_absent_warns_when_slot_uses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _stt_weights_with(monkeypatch, None)
    assert c.status == "warn"


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
    assert "hal0 agent reprovision hermes --repair" in c.detail


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
    assert "hal0 agent reprovision hermes --repair" in c.detail


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


# ── check_hermes_anchor_window ────────────────────────────────────────────────
#
# #1867's window preflight has to be reachable on the path the defect lives on.
# ``installer/install.sh`` only calls ``install_hermes`` when the hermes venv is
# ABSENT ("covers the upgrade path where no provision ran"), so on an in-place
# upgrade — the one shape where a stale ``[model].context_size`` from an older
# release survives — the smoke probes never run at all. ``hal0 doctor`` is what
# an operator runs when the agent misbehaves, so the row lives here too.
#
# The payloads below are verbatim captures from ct152 (rc.5), a box
# whose agent slot is offline: ``hal0/agent`` resolves through the routing
# fallback chain to the brain slot's 32,768-token window (under Hermes' 64,000
# floor, i.e. it cannot chat) while ``/v1/models`` lists no ``agent`` row at all.

_CT152_BRAIN_ROW = {
    "id": "brain",
    "object": "model",
    "created": 1786589347,
    "owned_by": "hal0",
    "name": "brain · hal0-brain-sft-f16",
    "downloaded": True,
    "context_length": 32768,
    "max_context_window": 32768,
    "labels": ["chat"],
    "recipe": "chat",
}
_CT152_CATALOG = [
    _CT152_BRAIN_ROW,
    {
        "id": "utility",
        "object": "model",
        "created": 1786589347,
        "owned_by": "hal0",
        "name": "utility · Qwen3.5-0.8B",
        "downloaded": True,
        "context_length": 32768,
        "max_context_window": 32768,
        "labels": ["chat"],
        "checkpoint": "Q4_K_XL",
    },
]
_CT152_BY_ID = {**_CT152_BRAIN_ROW, "id": "hal0/agent"}


def _hermes_config(tmp_path, anchor: str = "hal0/agent"):  # type: ignore[no-untyped-def]
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"model:\n  default: {anchor}\n  base_url: http://127.0.0.1:8080/v1\n")
    return cfg


def test_anchor_window_passes_when_hermes_is_not_provisioned(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = da.check_hermes_anchor_window(config_path=tmp_path / "nope.yaml")
    assert c.status == "pass"
    assert "not provisioned" in c.detail


def test_anchor_window_fails_on_the_ct152_shape(tmp_path, tmp_hal0_home: str) -> None:  # type: ignore[no-untyped-def]
    """The demonstrated false negative, as an operator would meet it on an
    upgraded box: `hal0 doctor` must name the real serving slot, its profile
    and the fix."""
    _profile_with_window(tmp_hal0_home, "brain-window", 32768)
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "brain.toml").write_text('profile = "brain-window"\n')
    (slots / "agent.toml").write_text('profile = "agent"\n')
    c = da.check_hermes_anchor_window(
        config_path=_hermes_config(tmp_path),
        fetch=lambda *_a: (_CT152_BY_ID, _CT152_CATALOG),
        slots_dir=slots,
        venv_python=tmp_path / "no-venv-python",
    )
    assert c.status == "fail"
    assert "32,768" in c.detail and "64,000" in c.detail
    assert "raise ctx_size to 65536 on profile 'brain-window'" in c.detail
    assert "hal0 slot restart brain" in c.detail
    assert "agent.toml" not in c.detail


def test_anchor_window_warns_rather_than_passing_when_it_cannot_see_the_window(  # type: ignore[no-untyped-def]
    tmp_path,
) -> None:
    """The gateway is unreachable / advertises nothing: no evidence is not a
    pass. Passing on no evidence is exactly how the broken box read green."""
    c = da.check_hermes_anchor_window(
        config_path=_hermes_config(tmp_path),
        fetch=lambda *_a: (None, []),
        slots_dir=tmp_path / "slots",
        venv_python=tmp_path / "no-venv-python",
    )
    assert c.status == "warn"
    assert "NOT a pass" in c.detail


def test_anchor_window_passes_when_the_window_clears_the_floor(tmp_path) -> None:  # type: ignore[no-untyped-def]
    row = {"id": "agent", "owned_by": "hal0", "context_length": 65536}
    c = da.check_hermes_anchor_window(
        config_path=_hermes_config(tmp_path),
        fetch=lambda *_a: ({**row, "id": "hal0/agent"}, [row]),
        slots_dir=tmp_path / "slots",
        venv_python=tmp_path / "no-venv-python",
    )
    assert c.status == "pass"
    assert "65,536" in c.detail


def test_anchor_window_warns_when_the_fetch_explodes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def _boom(*_a):  # type: ignore[no-untyped-def]
        raise RuntimeError("connection refused")

    c = da.check_hermes_anchor_window(
        config_path=_hermes_config(tmp_path),
        fetch=_boom,
        venv_python=tmp_path / "no-venv-python",
    )
    assert c.status == "warn"
    assert "connection refused" in c.detail


# ── check_hermes_model_choice (#3325) ─────────────────────────────────────────


def _model_cfg(tmp_path, body: str):  # type: ignore[no-untyped-def]
    cfg = tmp_path / "config.yaml"
    cfg.write_text(body)
    return cfg


_PICKED = "model:\n  default: anthropic/claude-x\n  provider: openrouter\n"
_PICKED_LOCAL = (
    "model:\n  default: anthropic/claude-x\n  provider: openai\n"
    "  base_url: http://127.0.0.1:9999/v1\n"
)


def test_model_choice_passes_when_not_provisioned(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = da.check_hermes_model_choice(config_path=tmp_path / "nope.yaml")
    assert c.status == "pass" and "not provisioned" in c.detail


def test_model_choice_passes_quietly_when_hal0_owns_the_model(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = _model_cfg(
        tmp_path,
        "model:\n  default: hal0/agent\n  provider: custom\n  base_url: http://127.0.0.1:8080/v1\n",
    )
    c = da.check_hermes_model_choice(config_path=cfg)
    assert c.status == "pass" and "kept your" not in c.detail


def test_model_choice_names_kept_pick_provider_and_way_back(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from hal0.agents.hermes_provision import kept_model_notice

    c = da.check_hermes_model_choice(
        config_path=_model_cfg(tmp_path, _PICKED), route_ready=lambda *_a: None
    )
    assert c.status == "pass"
    assert kept_model_notice("anthropic/claude-x") in c.detail
    assert "openrouter" in c.detail


def test_model_choice_warns_when_loopback_route_is_dead_and_is_read_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = _model_cfg(tmp_path, _PICKED_LOCAL)
    before = cfg.read_text()
    c = da.check_hermes_model_choice(config_path=cfg, route_ready=lambda *_a: False)
    assert c.status == "warn"
    assert "not answering" in c.detail and "hermes config set model.default hal0/agent" in c.detail
    assert cfg.read_text() == before


def test_model_choice_never_probes_a_third_party_provider(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def _boom(*_a):  # type: ignore[no-untyped-def]
        raise AssertionError("probed a non-loopback provider")

    cfg = _model_cfg(tmp_path, _PICKED + "  base_url: https://openrouter.ai/api/v1\n")
    assert da.check_hermes_model_choice(config_path=cfg, route_ready=_boom).status == "pass"


# ── build_all_checks orchestration ────────────────────────────────────────────


def test_build_all_checks_includes_hermes_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Composition test: also pins the ``hermes-api`` row and its payload wiring."""
    hermes_api_kwargs: dict = {}
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
    # ui-dist (#1589) reads real api.env otherwise; neutralise it so this
    # composition test asserts the row list, not the host's api.env content.
    monkeypatch.setattr(
        da, "check_ui_dist", lambda: Check("ui-dist", "Dashboard bundle", "pass", "stubbed")
    )
    monkeypatch.setattr(
        da,
        "check_api_env_entries",
        lambda: Check("api-env-entries", "api.env entries", "pass", "stubbed"),
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
        "check_hermes_profiles_mcp",
        lambda **_kw: Check("hermes_profiles_mcp", "Hermes profile MCP wiring", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_default_memory",
        lambda **_kw: Check(
            "hermes_default_memory", "Hermes default profile memory", "pass", "stubbed"
        ),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_brain_memory",
        lambda **_kw: Check(
            "hermes_brain_memory", "Hermes hal0-brain profile memory", "pass", "stubbed"
        ),
    )
    monkeypatch.setattr(
        da,
        "check_bridge_store",
        lambda **_kw: Check("bridge-store", "Agent bridge store", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_openwebui_mcp",
        lambda **_kw: Check("openwebui_mcp", "Open WebUI MCP tools", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_generation_extras",
        lambda **_kw: Check("hermes_generation", "Hermes pm generation", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_command_approvals",
        lambda **_kw: Check(
            "hermes_command_approvals", "Hermes command approvals", "pass", "stubbed"
        ),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_api_upstreams",
        lambda **kw: (
            hermes_api_kwargs.update(kw)
            or Check("hermes-api", "Hermes API upstreams", "pass", "stubbed")
        ),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_gateway_unit",
        lambda **_kw: Check("hermes-gateway-unit", "Hermes gateway unit", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_delegation_slot",
        lambda *_a, **_kw: Check(
            "hermes_delegation_slot", "Hermes delegation slot", "pass", "stubbed"
        ),
    )
    monkeypatch.setattr(
        da,
        "check_hindsight_llm_auth",
        lambda **_kw: Check("hindsight_llm_auth", "Memory engine LLM auth", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_anchor_window",
        lambda **_kw: Check(
            "hermes_anchor_window", "Hermes anchor context window", "pass", "stubbed"
        ),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_model_choice",
        lambda **_kw: Check("hermes_model_choice", "Hermes model choice", "pass", "stubbed"),
    )

    # Board rows read the real token file / api.env / dashboard otherwise.
    monkeypatch.setattr(
        da,
        "check_stale_hermes_session_token",
        lambda **_kw: Check("hermes-session-token-pin", "Stale pin", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_dashboard_token",
        lambda **_kw: Check("hermes-dashboard-token", "Token", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_hermes_kanban_reachable",
        lambda **_kw: Check("hermes-kanban", "Kanban", "pass", "stubbed"),
    )

    # Linger row (ADR-0034) shells out to loginctl otherwise.
    monkeypatch.setattr(
        da,
        "check_hal0_linger",
        lambda **_kw: Check("hal0_linger", "hal0 user linger", "pass", "stubbed"),
    )

    # Stale-DNAT + netns rows (#1814) shell out to nft/podman otherwise;
    # neutralise both so this composition test asserts the row list.
    monkeypatch.setattr(
        da, "check_port_dnat", lambda _s: Check("port_dnat", "Port DNAT rules", "pass", "stubbed")
    )
    monkeypatch.setattr(
        da,
        "check_netns_durability",
        lambda *_a, **_k: Check("netns", "Container netns", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_box_boundary",
        lambda *_a, **_k: Check("box", "Box boundary", "pass", "stubbed"),
    )

    # Agent-UID row (ADR-0002) shells out to systemctl otherwise; neutralise it
    # so this composition test asserts the row list, not the host's units.
    monkeypatch.setattr(
        da,
        "check_agent_uid_isolation",
        lambda **_kw: Check("agent-uid", "Agent UID split", "pass", "stubbed"),
    )
    # Journal-read row (#2435) reads /proc + systemctl otherwise.
    monkeypatch.setattr(
        da,
        "check_api_journal_access",
        lambda **_kw: Check("api-journal", "API journal read", "pass", "stubbed"),
    )
    # GTT-leak row reads the host's amdgpu counters + KFD table otherwise.
    gtt_leak_slots: list = []
    monkeypatch.setattr(
        da,
        "check_gtt_leak",
        lambda slots=None, **_kw: (
            gtt_leak_slots.append(slots),
            Check("gtt_leak", "GPU memory leak", "pass", "stubbed"),
        )[1],
    )

    monkeypatch.setattr(
        da,
        "check_fabric_clock",
        lambda slots=None, **_kw: Check(
            "fabric_clock", "NPU/GPU fabric-clock guard", "pass", "stubbed"
        ),
    )

    monkeypatch.setattr(
        da,
        "check_img_custom_nodes",
        lambda: Check("img-custom-nodes", "Image custom nodes", "pass", "stubbed"),
    )

    monkeypatch.setattr(
        da,
        "check_api_unit_current",
        lambda **_kw: Check("api-unit", "API unit current", "pass", "stubbed"),
    )

    monkeypatch.setattr(
        da,
        "check_migration_015",
        lambda **_kw: Check("migration-015", "Model-tuning migration (015)", "pass", "stubbed"),
    )
    monkeypatch.setattr(
        da,
        "check_memory_decision_lane",
        lambda **_kw: Check("memory_decision_lane", "Memory decision lane", "pass", "stubbed"),
    )

    # Engine-env row (#3520) reads hal0.toml + systemctl show otherwise.
    monkeypatch.setattr(
        da,
        "check_memory_engine_env",
        lambda **_kw: Check("memory_engine_env", "Memory engine env", "pass", "stubbed"),
    )

    # Embedder row (#3518) reads hal0.toml, the record and the engine home otherwise.
    monkeypatch.setattr(
        da,
        "check_memory_embedder",
        lambda **_kw: Check("memory_embedder", "Memory embedder", "pass", "stubbed"),
    )

    # Projects row (ADR-0058 4a) reads hal0.toml and lists the bank's tags otherwise.
    projects_kwargs: dict = {}
    monkeypatch.setattr(
        da,
        "check_memory_projects",
        lambda **kw: (
            projects_kwargs.update(kw),
            Check("memory_projects", "Memory projects", "pass", "stubbed"),
        )[1],
    )

    checks = da.build_all_checks()
    keys = [c.key for c in checks]
    assert gtt_leak_slots == [[{"port": 8081}]]
    assert set(projects_kwargs) == {"memory", "base"}
    # Verify rows plus the extra evidence rows (gtt-leak, api-unit).
    assert keys[-50:] == [
        "auth",
        "models",
        "registry-toml",
        "ui-dist",
        "hermes-session-token-pin",
        "api-env-entries",
        "ports",
        "port_dnat",
        "netns",
        "box",
        "hal0_target",
        "secret-modes",
        "hardware_freshness",
        "gtt_leak",
        "fabric_clock",
        "seed_context_envelope",
        "agent-uid",
        "api-journal",
        "img-custom-nodes",
        "api-unit",
        "migration-015",
        "stt-weights",
        "seams",
        "mcp_mounts",
        "hermes-api",
        "hermes_mcp_auth",
        "hermes_profiles_mcp",
        "hermes_default_memory",
        "hermes_brain_memory",
        "bridge-store",
        "agent-bridge",
        "openwebui_mcp",
        "openwebui-task-model",
        "hermes_generation",
        "hermes_command_approvals",
        "hermes_delegation_slot",
        "hermes-gateway-unit",
        "hermes-dashboard-token",
        "hermes-kanban",
        "hermes_anchor_window",
        "hermes_model_choice",
        "hal0_linger",
        "hindsight_llm_auth",
        "memory_engine_version",
        "memory_engine_env",
        "memory_embedder",
        "memory_extraction_throughput",
        "memory_retain_chunk",
        "memory_decision_lane",
        "memory_projects",
    ]
    assert "api" in keys and "runners" in keys
    assert da.overall_verdict(checks) == "ok"
    assert hermes_api_kwargs["slots_payload"] == [{"port": 8081}]
    assert "upstreams_payload" in hermes_api_kwargs


# ── hal0 user linger (ADR-0034 decision 6) ───────────────────────────────────


@pytest.mark.parametrize(
    ("gateway", "enabled", "status"),
    [
        (True, True, "pass"),
        (True, False, "warn"),
        (False, True, "warn"),
        (False, False, "pass"),
        (True, None, "warn"),
    ],
)
def test_hal0_linger_row(tmp_path, gateway: bool, enabled, status: str) -> None:  # type: ignore[no-untyped-def]
    from hal0.system.linger import LingerState

    unit = tmp_path / "hermes-gateway.service"
    if gateway:
        unit.write_text("[Unit]\nAfter=user@999.service\nWants=user@999.service\n")
    check = da.check_hal0_linger(
        probe=lambda: LingerState("hal0", enabled, "test", "why"),
        gateway_unit=unit,
        gateway_in_use=lambda: True,
    )
    assert check.key == "hal0_linger"
    assert check.status == status
    if status == "warn" and gateway and enabled is False:
        assert "sudo hal0 agent install hermes" in check.detail
    if status == "warn" and not gateway:
        assert "disable-linger" in check.detail


def test_hal0_linger_row_flags_a_gateway_unit_from_before_0_22(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The 0.19.0 generator wrote `python -m hermes_cli.main ... gateway run`
    # with no user@ ordering; it still starts v0.22.2 but should be regenerated.
    from hal0.system.linger import LingerState

    unit = tmp_path / "hermes-gateway.service"
    unit.write_text("[Service]\nExecStart=/v/bin/python -m hermes_cli.main gateway run\n")
    check = da.check_hal0_linger(
        probe=lambda: LingerState("hal0", True, "test"),
        gateway_unit=unit,
        gateway_in_use=lambda: True,
    )
    assert check.status == "warn"
    assert "predates Hermes 0.22" in check.detail
    assert "sudo hal0 agent install hermes" in check.detail


@pytest.mark.parametrize("enabled", [True, False])
def test_hal0_linger_row_is_info_for_a_disabled_gateway(tmp_path, enabled: bool) -> None:  # type: ignore[no-untyped-def]
    # A gateway the operator disabled (the test boxes) never runs: an old
    # unit shape or linger off must not warn.
    from hal0.system.linger import LingerState

    unit = tmp_path / "hermes-gateway.service"
    unit.write_text("[Service]\nExecStart=/v/bin/python -m hermes_cli.main gateway run\n")
    check = da.check_hal0_linger(
        probe=lambda: LingerState("hal0", enabled, "test"),
        gateway_unit=unit,
        gateway_in_use=lambda: False,
    )
    assert check.status == "pass"
    assert "disabled" in check.detail


def test_hal0_linger_row_never_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def boom():  # type: ignore[no-untyped-def]
        raise RuntimeError("no dbus")

    check = da.check_hal0_linger(probe=boom, gateway_unit=tmp_path / "x")
    assert check.status == "warn"
    assert "no dbus" in check.detail


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


# ── memory engine version (staleness signal, never a FAIL) ───────────────────


def test_memory_engine_version_passes_on_pin_match() -> None:
    from hal0.memory.engine_upgrade import HINDSIGHT_API_PIN

    check = da.check_memory_engine_version(
        memory={
            "enabled": True,
            "engine": "hindsight",
            "reachable": True,
            "version": HINDSIGHT_API_PIN,
        }
    )
    assert check.status == "pass"


def test_memory_engine_version_warns_on_stale_engine_with_remedy() -> None:
    check = da.check_memory_engine_version(
        memory={"enabled": True, "engine": "hindsight", "reachable": True, "version": "0.8.4"}
    )
    assert check.status == "warn"  # advisory: behavior gates on features, not versions
    assert "hal0 update" in check.detail
    assert "0.8.4" in check.detail


def test_memory_engine_version_passes_when_memory_disabled_or_unreachable() -> None:
    assert da.check_memory_engine_version(memory={"enabled": True, "engine": None}).status == "pass"
    # Reachability is the memory row's job — no duplicate noise here.
    check = da.check_memory_engine_version(
        memory={"enabled": True, "engine": "hindsight", "reachable": False, "version": None}
    )
    assert check.status == "pass"
    assert da.check_memory_engine_version(memory=None).status == "warn"


# ── memory extraction speed (#2353, advisory) ───────────────────────────────

_LIVE_MEMORY = {"enabled": True, "engine": "hindsight", "reachable": True}


def test_extraction_throughput_warns_on_a_slow_slot() -> None:
    from hal0.memory.extraction_env import ExtractionThroughput

    slow = ExtractionThroughput(
        slot="utility", timeout_s=300, max_tokens=4096, prompt_tps=12.0, decode_tps=4.0
    ).as_dict()
    check = da.check_memory_extraction_throughput(memory=_LIVE_MEMORY, measure=lambda _b: slow)
    assert check.status == "warn"  # warn, never fail: the enable path never refuses either
    assert "12.0 prompt tok/s" in check.detail
    assert "llm_timeout_s = 300 s" in check.detail


def test_extraction_throughput_passes_on_a_fast_slot() -> None:
    from hal0.memory.extraction_env import ExtractionThroughput

    fast = ExtractionThroughput(
        slot="utility", timeout_s=300, max_tokens=4096, prompt_tps=800.0, decode_tps=45.0
    ).as_dict()
    check = da.check_memory_extraction_throughput(memory=_LIVE_MEMORY, measure=lambda _b: fast)
    assert check.status == "pass"


def test_extraction_throughput_unmeasurable_is_a_warn_not_a_pass() -> None:
    check = da.check_memory_extraction_throughput(memory=_LIVE_MEMORY, measure=lambda _b: None)
    assert check.status == "warn"


def test_extraction_throughput_skips_the_probe_without_a_live_engine() -> None:
    def _never(_b: object) -> None:  # pragma: no cover — a call here is the bug
        raise AssertionError("no probe without a reachable memory engine")

    for memory in (
        {"enabled": True, "engine": None},
        {"enabled": False, "engine": "hindsight"},
        {"enabled": True, "engine": "hindsight", "reachable": False},
    ):
        assert da.check_memory_extraction_throughput(memory=memory, measure=_never).status == "pass"


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


# ── hal0-api journal read (#2435) ─────────────────────────────────────────────


def _journal(
    *,
    ids: tuple[int, set[int]] | None = (999, {999, 105}),
    gid: int | None = 105,
) -> Check:
    return da.check_api_journal_access(
        process_ids=lambda: ids,
        journal_gid=lambda: gid,
    )


def test_api_journal_passes_when_process_holds_the_group() -> None:
    check = _journal()
    assert check.key == "api-journal"
    assert check.status == "pass"


def test_api_journal_warns_when_process_lacks_the_group() -> None:
    check = _journal(ids=(999, {999, 44}))
    assert check.status == "warn"
    # Fix text names the seam verb and the installer, never a plain `hal0 update`,
    # which stops before activation when no newer release exists.
    assert "refresh-api-unit" in check.detail
    assert "install.sh" in check.detail
    assert "/api/logs" in check.detail
    assert "systemd-journal" in check.detail


def test_api_journal_warns_for_an_unexpected_root_api() -> None:
    check = _journal(ids=(0, {0}))
    assert check.status == "warn"
    assert "root" in check.detail
    assert "hal0-api" in check.detail


def test_api_journal_warns_when_host_has_no_journal_group_for_hal0_api() -> None:
    check = _journal(gid=None)
    assert check.status == "warn"
    assert "systemd-journal" in check.detail


def test_api_journal_warns_when_host_has_no_journal_group() -> None:
    check = _journal(gid=None)
    assert check.status == "warn"
    assert "systemd-journal" in check.detail


def test_api_journal_warns_when_process_unknown() -> None:
    check = _journal(ids=None)
    assert check.status == "warn"
    assert "hal0-api" in check.detail


def test_api_journal_process_probe_reads_main_pid_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """The real probe: MainPID from systemctl, Uid/Groups from /proc."""
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: "/usr/bin/systemctl")

    class _R:
        returncode = 0
        stdout = "4242\n"

    monkeypatch.setattr(da.subprocess, "run", lambda *_a, **_k: _R())
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "status").write_text(
        "Name:\thal0\nUid:\t999\t999\t999\t999\nGroups:\t44 105 999 \n",
        encoding="utf-8",
    )
    assert da._api_process_ids(proc_root=proc) == (999, {44, 105, 999})


def test_api_journal_process_probe_unknown_when_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: "/usr/bin/systemctl")

    class _R:
        returncode = 0
        stdout = "0\n"

    monkeypatch.setattr(da.subprocess, "run", lambda *_a, **_k: _R())
    assert da._api_process_ids() is None


def test_api_journal_process_probe_unknown_without_systemctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(da.shutil, "which", lambda _cmd: None)
    assert da._api_process_ids() is None


# ── check_hermes_command_approvals (advisory drift row) ─────────────────────


def _approvals_status(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "mode": "smart",
        "effective": "smart",
        "source": "setting",
        "default": "smart",
        "invalid_value": None,
        "seed_unreadable": False,
        "seed_path": "/etc/hal0/agents/hermes.toml",
        "rendered": "smart",
        "in_sync": True,
    }
    base.update(over)
    return base


def _approvals_check(tmp_path, **over: object) -> Check:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("approvals:\n  mode: smart\n")
    return da.check_hermes_command_approvals(
        status_fn=lambda: _approvals_status(**over), config_path=cfg
    )


def test_command_approvals_not_provisioned_passes(tmp_path) -> None:
    c = da.check_hermes_command_approvals(config_path=tmp_path / "missing.yaml")
    assert c.status == "pass" and "not provisioned" in c.detail


def test_command_approvals_in_sync_passes(tmp_path) -> None:
    c = _approvals_check(tmp_path)
    assert c.status == "pass" and c.detail == "smart (setting)"


def test_command_approvals_drift_warns_with_the_setting_fix(tmp_path) -> None:
    c = _approvals_check(tmp_path, mode="manual", effective="manual", rendered="off", in_sync=False)
    assert c.status == "warn"
    assert "'off'" in c.detail and "terminal-enabled agent" in c.detail
    assert "hal0 agent command-approvals manual" in c.detail


def test_command_approvals_drift_under_override_advises_reprovision_only(tmp_path) -> None:
    c = _approvals_check(tmp_path, source="overrides", rendered="manual", in_sync=False)
    assert c.status == "warn"
    assert "hal0 agent reprovision hermes" in c.detail
    assert "command-approvals" not in c.detail


def test_command_approvals_unreadable_seed_warns(tmp_path) -> None:
    c = _approvals_check(tmp_path, seed_unreadable=True, source="default")
    assert c.status == "warn" and "could not be read" in c.detail


def test_command_approvals_invalid_value_warns(tmp_path) -> None:
    c = _approvals_check(tmp_path, invalid_value="bogus", source="default")
    assert c.status == "warn" and "'bogus'" in c.detail


def test_command_approvals_unreadable_config_is_not_a_finding(tmp_path) -> None:
    c = _approvals_check(tmp_path, rendered=None, in_sync=None)
    assert c.status == "pass" and "run as root" in c.detail


def test_delegation_slot_default_passes() -> None:
    c = da.check_hermes_delegation_slot([], slot_name="agent")
    assert c.status == "pass"


def test_delegation_slot_offline_warns() -> None:
    slots = [{"name": "utility", "type": "llm", "state": "warming", "model_id": "m"}]
    c = da.check_hermes_delegation_slot(slots, slot_name="utility")
    assert c.status == "warn" and "utility" in c.detail
    assert "last-rendered" in c.detail and "reprovision" in c.detail


def test_delegation_slot_ready_passes() -> None:
    slots = [{"name": "utility", "type": "llm", "state": "ready", "model_id": "m"}]
    assert da.check_hermes_delegation_slot(slots, slot_name="utility").status == "pass"


# ── Hermes API upstreams ──────────────────────────────────────────────────────

_KEY = "k" * 32
_API_TAIL = " (API path only; MCP tools are not checked, see #2891)"


def _hermes_home(tmp_path, *, default_key=True, alpha_key=True):
    (tmp_path / "config.yaml").write_text("model: x\n")
    if default_key:
        (tmp_path / ".env").write_text(f"API_SERVER_KEY={_KEY}\n")
    prof = tmp_path / "profiles" / "alpha"
    prof.mkdir(parents=True)
    if alpha_key:
        (prof / ".env").write_text(f"API_SERVER_KEY={_KEY}\n")
    return tmp_path


def _api(home, *, status=200, upstreams=None, slots=None, calls=None):
    def http_get(url, headers):
        if calls is not None:
            calls.append((url, headers))
        return status, {"data": [{"id": "x"}]}

    if upstreams is None:
        upstreams = [{"name": "hermes"}, {"name": "hermes.alpha"}]
    return da.check_hermes_api_upstreams(
        home=home,
        probe=lambda url: 200,
        http_get=http_get,
        upstreams_payload=upstreams,
        slots_payload=slots,
        gateway_expected=lambda: True,
    )


def test_check_hermes_api_upstreams_gateway_disabled_warns(tmp_path) -> None:
    """Disabled AND stopped (transport failure, unit not expected): WARN, no per-profile request."""
    home = _hermes_home(tmp_path)
    calls: list = []

    def http_get(url, headers):
        calls.append(url)
        return 200, {}

    c = da.check_hermes_api_upstreams(
        home=home,
        probe=lambda url: 0,
        http_get=http_get,
        upstreams_payload=[],
        gateway_expected=lambda: False,
    )
    assert c.status == "warn" and c.critical is False
    assert "disabled and not running" in c.detail and "/health" in c.detail
    assert "enable --now hermes-gateway" in c.detail and c.detail.endswith(_API_TAIL)
    assert calls == []


@pytest.mark.parametrize(
    ("state", "remedy"),
    [
        ("disabled", "sudo systemctl enable --now hermes-gateway.service"),
        (
            "masked",
            "sudo systemctl unmask hermes-gateway.service && sudo hal0 agent install hermes",
        ),
        (
            "masked-runtime",
            "sudo systemctl unmask --runtime hermes-gateway.service && sudo hal0 agent install hermes",
        ),
    ],
)
def test_check_hermes_api_upstreams_off_remedy_follows_unit_state(tmp_path, state, remedy) -> None:
    """#3030: a masked gateway has no unit file to enable; doctor prints the same
    remedy as `hal0 agent install hermes`."""
    c = da.check_hermes_api_upstreams(
        home=_hermes_home(tmp_path),
        probe=lambda url: 0,
        upstreams_payload=[],
        gateway_expected=lambda: False,
        gateway_state=lambda: state,
    )
    assert c.status == "warn" and f"to run it: {remedy}" in c.detail
    if state != "disabled":
        assert "enable --now" not in c.detail


def test_check_hermes_api_upstreams_enabled_but_unreachable_fails(tmp_path) -> None:
    """An enabled gateway that does not answer is an outage, not an off switch."""
    home = _hermes_home(tmp_path)
    c = da.check_hermes_api_upstreams(
        home=home,
        probe=lambda url: 0,
        http_get=lambda url, headers: (0, None),
        upstreams_payload=[],
        gateway_expected=lambda: True,
    )
    assert c.status == "fail"
    assert "profile default: GET /v1/models answered 0" in c.detail


def test_check_hermes_api_upstreams_unhealthy_running_gateway_fails(tmp_path) -> None:
    """A 503 on /health is a running, unhealthy gateway: never the WARN path."""
    home = _hermes_home(tmp_path)
    c = da.check_hermes_api_upstreams(
        home=home,
        probe=lambda url: 503,
        http_get=lambda url, headers: (503, None),
        upstreams_payload=[],
        gateway_expected=lambda: False,
    )
    assert c.status == "fail"
    assert "answered 503" in c.detail


def test_check_hermes_api_upstreams_absent_passes(tmp_path) -> None:
    c = da.check_hermes_api_upstreams(home=tmp_path, upstreams_payload=[])
    assert (c.key, c.label, c.status, c.critical) == (
        "hermes-api",
        "Hermes API upstreams",
        "pass",
        False,
    )
    assert "no Hermes agent installed" in c.detail


def test_check_hermes_api_upstreams_unreadable_home_warns(tmp_path, monkeypatch) -> None:
    """Ruling 2: a 0700 Hermes home seen by a non-hal0 user is a WARN, never PASS."""
    home = _hermes_home(tmp_path)
    real_access = da.os.access

    def fake_access(path, mode, *a, **kw):
        if Path(path) == home:
            return False
        return real_access(path, mode, *a, **kw)

    monkeypatch.setattr(da.os, "access", fake_access)
    c = da.check_hermes_api_upstreams(home=home, upstreams_payload=[])
    assert c.status == "warn"
    assert c.detail == f"cannot read {home}; run hal0 doctor as hal0 or root"


def test_check_hermes_api_upstreams_config_permission_error_warns(tmp_path, monkeypatch) -> None:
    home = _hermes_home(tmp_path)
    real_stat = Path.stat

    def fake_stat(self, *a, **kw):
        if self.name == "config.yaml":
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", fake_stat)
    c = da.check_hermes_api_upstreams(home=home, upstreams_payload=[])
    assert c.status == "warn"
    assert c.detail == f"cannot read {home}; run hal0 doctor as hal0 or root"


def test_check_hermes_api_upstreams_missing_home_passes(tmp_path) -> None:
    c = da.check_hermes_api_upstreams(home=tmp_path / "nowhere", upstreams_payload=[])
    assert c.status == "pass" and "no Hermes agent installed" in c.detail


def test_check_hermes_api_upstreams_pass_lists_ids(tmp_path) -> None:
    calls: list = []
    c = _api(
        _hermes_home(tmp_path),
        calls=calls,
        upstreams={"upstreams": [{"name": "hermes"}, {"name": "hermes.alpha"}]},
    )
    assert c.status == "pass"
    assert "default→hermes-agent" in c.detail and "alpha→alpha" in c.detail
    assert c.detail.endswith(_API_TAIL)
    urls = {u for u, _ in calls}
    assert "http://127.0.0.1:8642/v1/models" in urls
    assert "http://127.0.0.1:8642/p/alpha/v1/models" in urls
    assert all(h == {"Authorization": f"Bearer {_KEY}"} for _, h in calls)


def test_check_hermes_api_upstreams_warns_without_key(tmp_path) -> None:
    c = _api(_hermes_home(tmp_path, alpha_key=False))
    assert c.status == "warn"
    assert "alpha" in c.detail and "sudo hal0 agent reprovision hermes --repair" in c.detail
    assert "vault" not in c.detail
    assert c.detail.endswith(_API_TAIL)


def test_check_hermes_api_upstreams_default_without_key_names_the_vault_mirror(tmp_path) -> None:
    """Codex P1: on a box provisioned before the mirror, the default key sits in
    the root-only vault; the fix is a root --repair, and the row says so."""
    c = _api(_hermes_home(tmp_path, default_key=False))
    assert c.status == "warn"
    assert "profile default has no usable API key" in c.detail
    assert "sudo hal0 agent reprovision hermes --repair" in c.detail
    assert "mirrors the gateway's vault key into $HERMES_HOME/.env" in c.detail


def test_check_hermes_api_upstreams_fails_on_401(tmp_path) -> None:
    c = _api(_hermes_home(tmp_path), status=401)
    assert c.status == "fail" and c.critical is False
    assert "401" in c.detail and c.detail.endswith(_API_TAIL)


def test_check_hermes_api_upstreams_warns_when_not_reconciled(tmp_path) -> None:
    c = _api(_hermes_home(tmp_path), upstreams=[{"name": "hermes"}])
    assert c.status == "warn"
    assert "hermes.alpha" in c.detail
    assert "POST /api/agents/hermes/reconcile-upstreams" in c.detail


def test_check_hermes_api_upstreams_ignores_remote_rows_in_the_slots_payload(tmp_path) -> None:
    """/api/slots lists the Hermes upstreams themselves as kind "remote" rows; they
    are not local slots and must not be reported as colliding with their own ids."""
    slots = [
        {
            "name": "hermes",
            "kind": "remote",
            "provider": "remote-upstream",
            "model": "hermes-agent",
        },
        {"name": "hermes.alpha", "kind": "remote", "model": "alpha"},
    ]
    c = _api(_hermes_home(tmp_path), slots=slots)
    assert c.status == "pass" and "wins" not in c.detail


def test_check_hermes_api_upstreams_warns_on_collision(tmp_path) -> None:
    c = _api(_hermes_home(tmp_path), slots=[{"name": "agent", "model_id": "alpha"}])
    assert c.status == "warn"
    assert "slot agent advertises alpha, which Hermes profile alpha owns" in c.detail
    assert "the Hermes upstream wins" in c.detail


def test_check_hermes_api_upstreams_warns_on_unreadable_env(tmp_path) -> None:
    home = _hermes_home(tmp_path)
    env = home / "profiles" / "alpha" / ".env"
    orig = da.Path.read_text

    def _deny(self, *a, **k):
        if self == env:
            raise PermissionError("denied")
        return orig(self, *a, **k)

    import pytest as _pt

    with _pt.MonkeyPatch.context() as mp:
        mp.setattr(da.Path, "read_text", _deny)
        c = _api(home)
    assert c.status == "warn"
    assert "cannot read" in c.detail and "run hal0 doctor as hal0 or root" in c.detail


def test_check_hermes_api_upstreams_warns_when_api_unreachable(tmp_path) -> None:
    c = da.check_hermes_api_upstreams(
        home=_hermes_home(tmp_path),
        probe=lambda url: 200,
        http_get=lambda url, headers: (200, {}),
        upstreams_payload=None,
    )
    assert c.status == "warn" and "hal0-api unreachable" in c.detail


@pytest.mark.parametrize(
    ("url", "trust_env"),
    [("http://127.0.0.1:8642/v1/models", False), ("http://hermes.example:8642/v1/models", True)],
)
def test_hermes_http_get_keeps_loopback_requests_off_the_proxy(monkeypatch, url, trust_env):
    """Codex P1: the authenticated doctor probe carries a profile key; a
    loopback request must never be routed through HTTP_PROXY."""
    import httpx

    seen: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": []}

    def fake_get(u, **kw):
        seen.update(url=u, **kw)
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    assert da._hermes_http_get(url, {"Authorization": "Bearer k"}) == (200, {"data": []})
    assert seen["trust_env"] is trust_env


# ── #2891: profile MCP wiring + pm generation drift ──────────────────────────


def _profiles_home(tmp_path, **profiles):
    """``profiles=<name>: yaml text | None`` (None = directory without config)."""
    home = tmp_path / "hermes"
    for name, text in profiles.items():
        d = home / "profiles" / name
        d.mkdir(parents=True)
        if text is not None:
            (d / "config.yaml").write_text(text)
    return home


def _wired_yaml() -> str:
    from hal0.agents import hermes_provision as hp

    admin_t = hp._HAL0_ADMIN_MCP_TIMEOUT_S
    mem_t = hp._memory_mcp_timeout()
    return (
        "memory:\n  provider: hal0-memory\n"
        "mcp_servers:\n"
        "  hal0-admin:\n    type: http\n    url: http://127.0.0.1:8080/mcp/admin/mcp\n"
        "    headers:\n      X-hal0-Agent: hermes__coder\n      X-hal0-Private: '0'\n"
        "      Authorization: Bearer k\n"
        f"    timeout: {admin_t}\n"
        "  hal0-memory:\n    type: http\n    url: http://127.0.0.1:8080/mcp/memory/mcp\n"
        "    headers:\n      X-hal0-Agent: hermes__coder\n      X-hal0-Private: '0'\n"
        "      Authorization: Bearer k\n"
        f"    timeout: {mem_t}\n"
    )


_WIRED = _wired_yaml()
_WIRED_FULL = _WIRED
_UNAUTHED = _WIRED.replace("      Authorization: Bearer k\n", "")


def test_profiles_mcp_passes_with_no_named_profiles(tmp_path) -> None:
    home = _profiles_home(tmp_path)
    (home / "profiles" / "hal0-brain").mkdir(parents=True)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert (c.key, c.status, c.critical) == ("hermes_profiles_mcp", "pass", False)


def test_profiles_mcp_warns_when_profiles_dir_unreadable(tmp_path, monkeypatch) -> None:
    home = _profiles_home(tmp_path, coder=_WIRED)
    real_access = da.os.access

    def fake_access(path, mode, *a, **kw):
        if Path(path) == home / "profiles":
            return False
        return real_access(path, mode, *a, **kw)

    monkeypatch.setattr(da.os, "access", fake_access)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn" and "run hal0 doctor as hal0 or root" in c.detail


def test_profiles_mcp_warns_on_unwired_profile(tmp_path) -> None:
    home = _profiles_home(tmp_path, coder=_WIRED, homelab="model:\n  default: x\n", bare=None)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn"
    assert "profile(s) homelab not wired" in c.detail and "reconcile-upstreams" in c.detail
    # CodeRabbit: a profile without config.yaml is not wired by the sweep; say so instead.
    assert "bare have no config.yaml" in c.detail and "once Hermes creates its config" in c.detail


def test_profiles_mcp_no_config_alone_is_informational(tmp_path) -> None:
    home = _profiles_home(tmp_path, coder=_WIRED, bare=None)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert (
        c.status == "pass" and "wired: coder" in c.detail and "bare have no config.yaml" in c.detail
    )


def test_profiles_mcp_fails_on_a_malformed_or_stale_bearer(tmp_path, monkeypatch) -> None:
    """Codex P2: a truthy but unusable Authorization, or one that is not the box's current key."""
    bad_shape = _WIRED.replace("Authorization: Bearer k", "Authorization: invalid")
    home = _profiles_home(tmp_path, coder=bad_shape)
    assert da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home).status == "fail"
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "current-key")
    home2 = _profiles_home(tmp_path / "b", coder=_WIRED)  # "Bearer k" is stale now
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home2)
    assert c.status == "fail" and "stale" in c.detail
    home3 = _profiles_home(tmp_path / "c", coder=_WIRED.replace("Bearer k", "Bearer current-key"))
    assert da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home3).status == "pass"


def test_profiles_mcp_fails_without_bearer_when_auth_required(tmp_path) -> None:
    home = _profiles_home(tmp_path, coder=_UNAUTHED)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "fail"
    assert "no usable or a stale Authorization" in c.detail and "coder" in c.detail
    # Auth-open box: a tokenless entry is the expected shape.
    assert da.check_hermes_profiles_mcp(auth={"auth_required": False}, home=home).status == "pass"


def test_profiles_mcp_reports_opt_out(tmp_path) -> None:
    home = _profiles_home(tmp_path, coder=_WIRED, lab="hal0:\n  wire_mcp: false\n")
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "pass"
    assert "wired: coder" in c.detail and "opted out: lab" in c.detail
    # Codex: an opt-out added after wiring leaves the entries; the row says so.
    home2 = _profiles_home(tmp_path / "b", lab="hal0:\n  wire_mcp: false\n" + _WIRED)
    c2 = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home2)
    assert "opted out: lab (still carries hal0-admin, hal0-memory)" in c2.detail


def test_profiles_mcp_warns_when_enumeration_fails(tmp_path, monkeypatch) -> None:
    home = _profiles_home(tmp_path, coder=_WIRED)
    real = Path.iterdir

    def boom(self):
        if self == home / "profiles":
            raise OSError("io error")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", boom)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn" and "cannot list" in c.detail


def test_generation_extras_warns_on_unreadable_release_metadata(tmp_path, monkeypatch) -> None:
    from hal0.agents import hermes_release as rel

    home, venv, root = _release_layout(tmp_path, ["web", "mcp"])
    (root / "releases" / "abc-1" / "release.json").write_text("{not json")
    monkeypatch.setattr(rel, "release_root_for", lambda _v: root)
    c = da.check_hermes_generation_extras(home=home, venv=venv)
    assert c.status == "warn" and "release.json cannot be read" in c.detail


def _release_layout(tmp_path, extras, *, generation_extras=None):
    """A live release under ``tmp_path/hermes`` and, optionally, a committed pm generation."""
    import json

    from hal0.agents import hermes_platforms as hpl

    root = tmp_path / "hermes"
    rid = "abc-1"
    rel = root / "releases" / rid
    (rel / "src").mkdir(parents=True)
    (rel / "venv").mkdir()
    (rel / "release.json").write_text(json.dumps({"ref": "x", "extras": extras}))
    (root / "current").symlink_to(rel, target_is_directory=True)
    venv = tmp_path / "venvs" / "hermes"
    venv.parent.mkdir()
    venv.symlink_to(rel / "venv", target_is_directory=True)
    home = tmp_path / "home"
    home.mkdir()
    if generation_extras is not None:
        state = home / "installs" / hpl.install_key(rel / "src")
        env = state / "environments" / "gen1"
        env.mkdir(parents=True)
        (env / "pyvenv.cfg").write_text("home = x\n")
        (state / "facts.json").write_text(
            json.dumps(
                {"packages": {"venv": {"environment": str(env), "extras": generation_extras}}}
            )
        )
    return home, venv, root


def test_generation_extras_passes_without_a_release(tmp_path) -> None:
    c = da.check_hermes_generation_extras(home=tmp_path, venv=tmp_path / "nope")
    assert (c.key, c.status) == ("hermes_generation", "pass")


def test_generation_extras_passes_when_no_generation_committed(tmp_path, monkeypatch) -> None:
    from hal0.agents import hermes_release as rel

    home, venv, root = _release_layout(tmp_path, ["web", "mcp"])
    monkeypatch.setattr(rel, "release_root_for", lambda _v: root)
    c = da.check_hermes_generation_extras(home=home, venv=venv)
    assert c.status == "pass" and "release venv" in c.detail


def test_generation_extras_warns_on_drift(tmp_path, monkeypatch) -> None:
    from hal0.agents import hermes_release as rel

    home, venv, root = _release_layout(
        tmp_path, ["web", "mcp", "telegram", "matrix"], generation_extras=["telegram"]
    )
    monkeypatch.setattr(rel, "release_root_for", lambda _v: root)
    c = da.check_hermes_generation_extras(home=home, venv=venv)
    assert c.status == "warn"
    assert "lacks extra(s) mcp, web" in c.detail  # matrix is never carried
    assert "reprovision hermes --repair" in c.detail


def test_generation_extras_passes_when_generation_matches(tmp_path, monkeypatch) -> None:
    from hal0.agents import hermes_release as rel

    home, venv, root = _release_layout(tmp_path, ["web", "mcp"], generation_extras=["mcp", "web"])
    monkeypatch.setattr(rel, "release_root_for", lambda _v: root)
    c = da.check_hermes_generation_extras(home=home, venv=venv)
    assert c.status == "pass" and "gen1" in c.detail


def test_profiles_mcp_treats_non_mapping_headers_as_missing_auth(tmp_path) -> None:
    broken = _WIRED.replace(
        "    headers:\n      X-hal0-Agent: hermes__coder\n      X-hal0-Private: '0'\n"
        "      Authorization: Bearer k\n",
        "    headers: invalid\n",
        1,
    )
    home = _profiles_home(tmp_path, coder=broken)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "fail" and "coder" in c.detail


def test_profiles_mcp_warns_when_home_is_not_traversable(tmp_path, monkeypatch) -> None:
    """A 0700 Hermes home seen by another user: ``is_dir()`` is False, not an error."""
    home = _profiles_home(tmp_path, coder=_WIRED)
    real_access = da.os.access

    def fake_access(path, mode, *a, **kw):
        if Path(path) == home:
            return False
        return real_access(path, mode, *a, **kw)

    monkeypatch.setattr(da.os, "access", fake_access)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn" and "run hal0 doctor as hal0 or root" in c.detail


def test_profiles_mcp_warns_on_drifted_identity_or_provider(tmp_path) -> None:
    """Codex P2: a copied X-hal0-Agent or a missing X-hal0-Private is not "wired"."""
    copied = _WIRED.replace("hermes__coder", "hermes__hal0-brain")
    no_private = _WIRED.replace("      X-hal0-Private: '0'\n", "")
    home = _profiles_home(tmp_path, coder=copied, lab=no_private)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn"
    assert "coder, lab" in c.detail and "differ from hal0's wiring" in c.detail
    # Only a different profile name than the fixture's identity drifts: ``coder`` as itself passes.
    home2 = _profiles_home(tmp_path / "b", coder=_WIRED)
    assert da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home2).status == "pass"


def test_profiles_mcp_private_header_follows_the_profile_opt_in(tmp_path) -> None:
    """A "1" is a stale header on a shared profile and correct on a memory_private one."""
    stale = _WIRED.replace("X-hal0-Private: '0'", "X-hal0-Private: '1'")
    c = da.check_hermes_profiles_mcp(
        auth={"auth_required": True}, home=_profiles_home(tmp_path, coder=stale)
    )
    assert c.status == "warn"
    assert "stale private header" in c.detail and "converge will remove it" in c.detail
    opted = stale + "hal0:\n  memory_private: true\n"
    home2 = _profiles_home(tmp_path / "b", coder=opted)
    c2 = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home2)
    assert c2.status == "pass" and "memory: private (opt-in)" in c2.detail
    home3 = _profiles_home(tmp_path / "c", coder=_WIRED)
    c3 = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home3)
    assert c3.status == "pass" and "private" not in c3.detail


def test_profiles_mcp_ignores_dirs_hermes_would_not_serve(tmp_path) -> None:
    home = _profiles_home(tmp_path, **{"Not A Profile": "a: 1\n"})
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "pass" and "no named Hermes profiles" in c.detail


def test_generation_extras_counts_platform_extras(tmp_path, monkeypatch) -> None:
    """Codex P2: a generation without the gateway platforms' SDK is drift too."""
    import json

    from hal0.agents import hermes_release as rel

    home, venv, root = _release_layout(tmp_path, ["web", "mcp"], generation_extras=["mcp", "web"])
    rj = root / "releases" / "abc-1" / "release.json"
    rj.write_text(
        json.dumps({"ref": "x", "extras": ["web", "mcp"], "platform_extras": ["telegram"]})
    )
    monkeypatch.setattr(rel, "release_root_for", lambda _v: root)
    c = da.check_hermes_generation_extras(home=home, venv=venv)
    assert c.status == "warn" and "lacks extra(s) telegram" in c.detail


def test_profiles_mcp_warns_on_a_stale_memory_timeout(tmp_path) -> None:
    """Codex P2: the 30 s budget the brain phase used cancels memory_reflect."""
    import re

    stale = re.sub(r"    timeout: \d+\n$", "    timeout: 30\n", _WIRED_FULL)
    assert stale != _WIRED_FULL
    home = _profiles_home(tmp_path, coder=stale)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn" and "coder" in c.detail


def test_profiles_mcp_reports_a_falsy_document_as_unreadable(tmp_path) -> None:
    home = _profiles_home(tmp_path, coder="[]\n", lab="")
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn"
    assert "coder is unreadable or not a mapping" in c.detail and "fix it by hand" in c.detail
    assert "lab not wired" in c.detail.replace("profile(s) ", "")


def test_generation_extras_warns_when_the_home_is_unreadable(tmp_path, monkeypatch) -> None:
    from hal0.agents import hermes_release as rel

    home, venv, root = _release_layout(tmp_path, ["web", "mcp"], generation_extras=["telegram"])
    monkeypatch.setattr(rel, "release_root_for", lambda _v: root)
    real_access = da.os.access
    monkeypatch.setattr(
        da.os,
        "access",
        lambda p, m, *a, **k: False if Path(p) == home else real_access(p, m, *a, **k),
    )
    c = da.check_hermes_generation_extras(home=home, venv=venv)
    assert c.status == "warn" and "run hal0 doctor as hal0 or root" in c.detail


def test_profiles_mcp_rejects_a_fifo_config_without_reading_it(tmp_path) -> None:
    """Codex P2: a FIFO config.yaml would block hal0 doctor forever."""
    import os

    home = _profiles_home(tmp_path, pipe=None, coder=_WIRED)
    os.mkfifo(home / "profiles" / "pipe" / "config.yaml")
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn" and "pipe is unreadable or not a mapping" in c.detail


def test_profiles_mcp_warns_on_a_symlinked_home(tmp_path) -> None:
    real_home = _profiles_home(tmp_path / "real", coder=_WIRED)
    link = tmp_path / ".hermes"
    link.symlink_to(real_home, target_is_directory=True)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=link)
    assert c.status == "warn" and "is a symlink" in c.detail


def test_profiles_mcp_reports_a_symlinked_profile_as_unmanaged(tmp_path) -> None:
    """Codex P2: the writer never follows a linked profile, so doctor must not call it wired."""
    home = _profiles_home(tmp_path, coder=_WIRED)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "config.yaml").write_text(_WIRED.replace("hermes__coder", "hermes__linked"))
    (home / "profiles" / "linked").symlink_to(real, target_is_directory=True)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn" and "linked are symlinks" in c.detail and "wired" not in c.detail


def test_profiles_mcp_reads_configs_through_one_descriptor(tmp_path, monkeypatch) -> None:
    """Codex P2: no second pathname lookup between the type check and the read."""
    home = _profiles_home(tmp_path, coder=_WIRED)

    def no_path_read(self, *a, **k):
        raise AssertionError("read_text must not be used for profile configs")

    monkeypatch.setattr(Path, "read_text", no_path_read)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "pass" and "wired: coder" in c.detail


# ── Hermes gateway unit (ADR-0034 amendment, #2874) ──────────────────────────


def _proc(tmp_path, procs):
    proc = tmp_path / "proc"
    for pid, argv, cgroup in procs:
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "cgroup").write_text(f"0::{cgroup}\n")
    (proc / "self").mkdir(parents=True, exist_ok=True)
    return proc


def test_gateway_unit_no_hermes_passes(tmp_path) -> None:
    c = da.check_hermes_gateway_unit(
        home=tmp_path / "none", unit_path=tmp_path / "u.service", proc=_proc(tmp_path, [])
    )
    assert c.status == "pass" and "no Hermes agent" in c.detail


def test_gateway_unit_installed_passes(tmp_path) -> None:
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: x\n")
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text("[Unit]\n")
    managed = [
        "/var/lib/hal0/hermes/current/.venv/bin/python",
        "-m",
        "hermes_cli.main",
        "gateway",
        "run",
        "--quiet",
    ]
    c = da.check_hermes_gateway_unit(
        home=home,
        unit_path=unit,
        proc=_proc(tmp_path, [(10, managed, "/system.slice/hermes-gateway.service")]),
    )
    assert c.status == "pass" and "hermes-gateway.service installed" in c.detail


def test_gateway_unit_masked_counts_as_installed(tmp_path) -> None:
    """``systemctl mask`` leaves a symlink to /dev/null; a mask blocks the spawn too."""
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: x\n")
    unit = tmp_path / "hermes-gateway.service"
    unit.symlink_to("/dev/null")
    c = da.check_hermes_gateway_unit(home=home, unit_path=unit, proc=_proc(tmp_path, []))
    assert c.status == "pass" and "installed (masked)" in c.detail


def test_gateway_unit_standalone_profile_without_its_unit_fails(tmp_path) -> None:
    """A gateway.standalone profile owns hermes-gateway-<profile>.service; absent, it spawns."""
    home = tmp_path / "hermes"
    (home / "profiles" / "lab").mkdir(parents=True)
    (home / "profiles" / "shared").mkdir(parents=True)
    (home / "config.yaml").write_text("model: x\n")
    (home / "profiles" / "lab" / "config.yaml").write_text("gateway:\n  standalone: true\n")
    (home / "profiles" / "shared" / "config.yaml").write_text("gateway:\n  standalone: false\n")
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text("[Unit]\n")
    c = da.check_hermes_gateway_unit(home=home, unit_path=unit, proc=_proc(tmp_path, []))
    assert c.status == "fail" and c.critical is False
    assert "lab (hermes-gateway-lab.service)" in c.detail and "shared" not in c.detail
    (tmp_path / "hermes-gateway-lab.service").write_text("[Unit]\n")
    c = da.check_hermes_gateway_unit(home=home, unit_path=unit, proc=_proc(tmp_path, []))
    assert c.status == "pass"


def test_gateway_unit_missing_fails(tmp_path) -> None:
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: x\n")
    c = da.check_hermes_gateway_unit(
        home=home, unit_path=tmp_path / "hermes-gateway.service", proc=_proc(tmp_path, [])
    )
    assert c.status == "fail" and c.critical is False
    assert "is missing" in c.detail and "hal0 agent install hermes" in c.detail
    assert "disable --now hermes-gateway" in c.detail


def test_gateway_unit_unmanaged_gateway_fails(tmp_path) -> None:
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: x\n")
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text("[Unit]\n")
    rogue = [
        "/var/lib/hal0/hermes/current/.venv/bin/python",
        "-m",
        "hermes_cli.main",
        "--profile",
        "default",
        "gateway",
        "run",
        "--quiet",
        "--idle-exit",
    ]
    unrelated = ["grep", "gateway", "run", "--idle-exit"]
    procs = [
        (77, rogue, "/system.slice/system-hal0\\x2dagent.slice/hal0-agent@hermes.service"),
        (78, unrelated, "/user.slice/user-0.slice/session-1.scope"),
    ]
    c = da.check_hermes_gateway_unit(home=home, unit_path=unit, proc=_proc(tmp_path, procs))
    assert c.status == "fail" and c.critical is False
    assert "pid 77" in c.detail and "hal0-agent@hermes.service" in c.detail
    assert "pid 78" in c.detail  # argv is the rule; a wrapper that carries it is reported too
    assert "Stop it" in c.detail and "hal0 agent install hermes" in c.detail


def test_gateway_unit_unreadable_home_warns(tmp_path, monkeypatch) -> None:
    home = tmp_path / "hermes"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text("model: x\n")
    real_stat = Path.stat

    def deny(self, *a, **k):
        if self == cfg:
            raise PermissionError(13, "denied", str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", deny)
    c = da.check_hermes_gateway_unit(home=home, unit_path=tmp_path / "u", proc=_proc(tmp_path, []))
    assert c.status == "warn" and "run hal0 doctor as hal0 or root" in c.detail
    # The shared check names the state `unreadable`; the report card keeps it a warning.
    from hal0.agents import hermes_doctor

    row = hermes_doctor.check_hermes_gateway_unit(
        home=home, unit_path=tmp_path / "u", proc=_proc(tmp_path, [])
    )
    assert row["status"] == "unreadable"


def test_gateway_unit_names_upstreams_hashed_unit(tmp_path) -> None:
    """#2943: a fresh 1.6.0 nightly install got hermes-gateway-<hash>.service."""
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: x\n")
    units = tmp_path / "units"
    units.mkdir()
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "hermes"
        / "gateway-unit-hashed-v0.22.4.service"
    )
    (units / "hermes-gateway-1975ce4b.service").write_text(fixture.read_text())
    c = da.check_hermes_gateway_unit(
        home=home, unit_path=units / "hermes-gateway.service", proc=_proc(tmp_path, [])
    )
    assert c.status == "fail" and c.critical is False
    assert "hermes-gateway-1975ce4b.service" in c.detail and "sudo hal0 update" in c.detail


def test_gateway_unit_flags_both_units_installed(tmp_path) -> None:
    """An interrupted move leaves both files; the live gateway may be the suffixed one."""
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: x\n")
    units = tmp_path / "units"
    units.mkdir()
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "hermes"
        / "gateway-unit-hashed-v0.22.4.service"
    )
    (units / "hermes-gateway-1975ce4b.service").write_text(fixture.read_text())
    (units / "hermes-gateway.service").write_text("[Unit]\n")
    c = da.check_hermes_gateway_unit(
        home=home, unit_path=units / "hermes-gateway.service", proc=_proc(tmp_path, [])
    )
    assert c.status == "fail" and "both hermes-gateway.service" in c.detail


# ── default profile memory privacy (ADR-0035 d9) ─────────────────────────────


def _root_cfg(tmp_path, admin: str, memory: str, *, opt_in: bool = False):
    home = tmp_path / "hermes"
    home.mkdir()
    hal0 = "hal0:\n  memory_private: true\n" if opt_in else ""
    (home / "config.yaml").write_text(
        hal0
        + "mcp_servers:\n"
        + f"  hal0-admin:\n    headers:\n      X-hal0-Private: '{admin}'\n"
        + f"  hal0-memory:\n    headers:\n      X-hal0-Private: '{memory}'\n"
    )
    return home


def test_default_memory_passes_when_not_provisioned(tmp_path) -> None:
    c = da.check_hermes_default_memory(home=tmp_path / "nope")
    assert (c.key, c.status, c.critical) == ("hermes_default_memory", "pass", False)


def test_default_memory_passes_when_shared_and_no_opt_in(tmp_path) -> None:
    c = da.check_hermes_default_memory(home=_root_cfg(tmp_path, "0", "0"))
    assert c.status == "pass" and "shared" in c.detail


def test_default_memory_warns_on_a_stale_private_header(tmp_path) -> None:
    c = da.check_hermes_default_memory(home=_root_cfg(tmp_path, "0", "1"))
    assert c.status == "warn"
    assert "hal0-memory" in c.detail and "hal0 update" in c.detail


def test_default_memory_passes_for_an_opt_in_with_private_headers(tmp_path) -> None:
    c = da.check_hermes_default_memory(home=_root_cfg(tmp_path, "1", "1", opt_in=True))
    assert c.status == "pass" and "private" in c.detail


def test_default_memory_warns_when_an_opt_in_is_not_applied(tmp_path) -> None:
    c = da.check_hermes_default_memory(home=_root_cfg(tmp_path, "0", "0", opt_in=True))
    assert c.status == "warn" and "hal0-admin" in c.detail and "hal0-memory" in c.detail


def _root_text(tmp_path, text):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(text)
    return home


_SERVERS_1 = (
    "mcp_servers:\n"
    "  hal0-admin:\n    headers:\n      X-hal0-Private: '1'\n"
    "  hal0-memory:\n    headers:\n      X-hal0-Private: '1'\n"
)


def test_default_memory_leaves_a_wire_mcp_opt_out_alone(tmp_path) -> None:
    home = _root_text(tmp_path, "hal0:\n  wire_mcp: false\n" + _SERVERS_1)
    c = da.check_hermes_default_memory(home=home)
    assert c.status == "pass" and "wire_mcp" in c.detail


def test_default_memory_warns_on_a_non_boolean_opt_in(tmp_path) -> None:
    home = _root_text(tmp_path, "hal0:\n  memory_private: 'true'\n" + _SERVERS_1)
    c = da.check_hermes_default_memory(home=home)
    assert c.status == "warn" and "memory_private" in c.detail and "not a boolean" in c.detail


def test_default_memory_treats_an_off_word_as_shared(tmp_path) -> None:
    home = _root_text(
        tmp_path, "hal0:\n  memory_private: 'off'\n" + _SERVERS_1.replace("'1'", "'0'")
    )
    c = da.check_hermes_default_memory(home=home)
    assert c.status == "warn" and "not a boolean" in c.detail
    assert "shared" in c.detail and "private" not in c.detail.replace("memory_private", "")


def test_default_memory_unapplied_opt_in_names_the_repair(tmp_path) -> None:
    c = da.check_hermes_default_memory(home=_root_cfg(tmp_path, "0", "0", opt_in=True))
    assert c.status == "warn" and "reprovision hermes --repair" in c.detail


def test_profiles_mcp_warns_on_a_non_boolean_opt_in(tmp_path) -> None:
    text = "hal0:\n  memory_private: 'yes'\n" + _wired_yaml().replace(
        "X-hal0-Private: '0'", "X-hal0-Private: '1'"
    )
    home = _profiles_home(tmp_path, coder=text)
    c = da.check_hermes_profiles_mcp(auth={"auth_required": True}, home=home)
    assert c.status == "warn" and "memory_private" in c.detail and "coder" in c.detail


def _brain_home(tmp_path, text):
    home = tmp_path / "hermes"
    (home / "profiles" / "hal0-brain").mkdir(parents=True)
    if text is not None:
        (home / "profiles" / "hal0-brain" / "config.yaml").write_text(text)
    return home


def test_brain_memory_passes_without_the_profile(tmp_path) -> None:
    c = da.check_hermes_brain_memory(home=_brain_home(tmp_path, None))
    assert (c.key, c.status, c.critical) == ("hermes_brain_memory", "pass", False)


def test_brain_memory_flags_an_opt_in_its_headers_do_not_carry(tmp_path) -> None:
    home = _brain_home(
        tmp_path,
        "hal0:\n  memory_private: true\n"
        "mcp_servers:\n  hal0-memory:\n    headers:\n      X-hal0-Private: '0'\n",
    )
    c = da.check_hermes_brain_memory(home=home)
    assert c.status == "warn" and "hal0-brain profile" in c.detail and "hal0-memory" in c.detail


def test_brain_memory_passes_when_headers_follow_the_opt_in(tmp_path) -> None:
    home = _brain_home(
        tmp_path,
        "hal0:\n  memory_private: true\n"
        "mcp_servers:\n  hal0-memory:\n    headers:\n      X-hal0-Private: '1'\n",
    )
    c = da.check_hermes_brain_memory(home=home)
    assert c.status == "pass" and "private" in c.detail


# ── memory retain chunk size (#3215, advisory) ──────────────────────────────


def test_retain_chunk_warns_when_a_bank_is_not_cleared() -> None:
    audit = {"verdict": "warn", "message": "bank x (12000) is not cleared", "max_tokens": 4096}
    check = da.check_memory_retain_chunk(memory=_LIVE_MEMORY, fetch=lambda _b: audit)
    assert check.status == "warn" and "12000" in check.detail


def test_retain_chunk_passes_when_every_bank_is_cleared() -> None:
    audit = {"verdict": "ok", "max_tokens": 16384, "checked": 3}
    check = da.check_memory_retain_chunk(memory=_LIVE_MEMORY, fetch=lambda _b: audit)
    assert check.status == "pass" and "3 bank(s)" in check.detail


def test_retain_chunk_unreadable_is_a_warn_not_a_pass() -> None:
    assert da.check_memory_retain_chunk(memory=_LIVE_MEMORY, fetch=lambda _b: None).status == "warn"
    unknown = {"verdict": "unknown"}
    assert (
        da.check_memory_retain_chunk(memory=_LIVE_MEMORY, fetch=lambda _b: unknown).status == "warn"
    )


def test_retain_chunk_skips_without_a_live_engine() -> None:
    def _never(_b: object) -> None:  # pragma: no cover — a call here is the bug
        raise AssertionError("no fetch without a reachable memory engine")

    for memory in (
        {"enabled": True, "engine": None},
        {"enabled": False, "engine": "hindsight"},
        {"enabled": True, "engine": "hindsight", "reachable": False},
    ):
        assert da.check_memory_retain_chunk(memory=memory, fetch=_never).status == "pass"


# ── memory projects (ADR-0058 decision 4a, advisory) ────────────────────────


def _projects_row(rows, *, aliases=None, registered=(), bank="shared", read_ok=True):
    from hal0.memory.projects import ProjectRegistry

    registry = ProjectRegistry(aliases=aliases or {}, registered=frozenset(registered))
    seen: list[tuple[object, str]] = []

    def fetch(base, bank_id):
        seen.append((base, bank_id))
        return rows

    check = da.check_memory_projects(
        memory=_LIVE_MEMORY, base="http://api.test", fetch=fetch, config=(bank, registry, read_ok)
    )
    assert check.key == "memory_projects" and check.label == "Memory projects"
    assert seen == [("http://api.test", bank)]
    return check


def _tag(tag: str, count: int) -> dict:
    return {"tag": tag, "count": count}


def test_projects_row_reports_the_registered_share() -> None:
    check = _projects_row(
        [_tag("project:atlas", 60), _tag("project:atlas-mono", 20), _tag("project:cirrus", 20)],
        aliases={"atlas-mono": "atlas"},
        registered=["atlas"],
    )
    assert check.status == "pass"
    assert "80 of 100 project-tagged facts (80%)" in check.detail
    assert "project:unresolved: 0" in check.detail
    assert "not in the registry (1): cirrus" in check.detail


def test_projects_row_counts_an_alias_target_as_registered() -> None:
    check = _projects_row([_tag("project:atlas", 5)], aliases={"atlas-mono": "atlas"})
    assert check.status == "pass" and "5 of 5" in check.detail and "100%" in check.detail
    assert "not in the registry" not in check.detail


def test_projects_row_warns_on_unresolved_writes() -> None:
    check = _projects_row(
        [_tag("project:atlas", 9), _tag("project:unresolved", 1)], registered=["atlas"]
    )
    assert check.status == "warn"
    assert "9 of 10 project-tagged facts (90%)" in check.detail
    assert "project:unresolved: 1" in check.detail
    assert "a coding client is writing without a project" in check.detail
    # The marker is never listed as an unregistered project.
    assert "not in the registry" not in check.detail


def test_projects_row_passes_with_a_handful_of_unregistered_slugs() -> None:
    assert da.PROJECTS_UNREGISTERED_WARN_ABOVE == 5
    rows = [_tag(f"project:repo-{n}", 10 - n) for n in range(5)]
    check = _projects_row(rows)
    assert check.status == "pass"
    assert "0 of 40 project-tagged facts (0%)" in check.detail
    # Most-used first.
    assert "not in the registry (5): repo-0, repo-1, repo-2, repo-3, repo-4" in check.detail


def test_projects_row_warns_past_a_handful_and_lists_ten() -> None:
    assert da.PROJECTS_UNREGISTERED_LISTED == 10
    rows = [_tag(f"project:repo-{n:02d}", 100 - n) for n in range(13)]
    check = _projects_row([*rows, _tag("project:atlas", 1)], registered=["atlas"])
    assert check.status == "warn"
    listed = ", ".join(f"repo-{n:02d}" for n in range(10))
    assert f"not in the registry (13): {listed} and 3 more" in check.detail
    assert "repo-10" not in check.detail
    assert "[memory.projects].registered" in check.detail
    six = _projects_row([_tag(f"project:repo-{n}", 1) for n in range(6)])
    assert six.status == "warn"


def test_projects_row_ignores_rows_that_are_not_project_tags() -> None:
    check = _projects_row(
        [
            _tag("project:atlas", 3),
            _tag("Project:Other", 50),  # the engine's wildcard is case-insensitive
            _tag("projects:x", 50),
            _tag("project:", 50),
            {"tag": "project:bad-count", "count": "many"},
            {"tag": "project:zero", "count": 0},
            {"tag": None, "count": 4},
            {"count": 4},
        ],
        registered=["atlas"],
    )
    assert check.status == "pass" and "3 of 3" in check.detail


def test_projects_row_with_no_project_tagged_facts() -> None:
    for rows in ([], [_tag("project:atlas", 0)]):
        check = _projects_row(rows, bank="platform-bank")
        assert check.status == "pass"
        assert check.detail == "no project-tagged facts in bank platform-bank yet"


def test_projects_row_says_when_hal0_toml_could_not_be_read() -> None:
    check = _projects_row([_tag("project:atlas", 2)], read_ok=False)
    assert check.status == "pass"
    assert "hal0.toml unreadable" in check.detail and "empty registry" in check.detail


def test_projects_row_passes_with_a_note_when_there_is_nothing_to_count() -> None:
    def _never(_base: object, _bank: str) -> None:  # pragma: no cover — a call here is the bug
        raise AssertionError("no tags listing without a reachable memory engine")

    for memory, note in (
        (None, "status unreachable"),
        ({"enabled": True, "engine": None}, "memory disabled"),
        ({"enabled": False, "engine": "hindsight"}, "memory disabled"),
        ({"enabled": True, "engine": "hindsight", "reachable": False}, "engine unreachable"),
    ):
        check = da.check_memory_projects(memory=memory, fetch=_never, config=("shared", None, True))
        assert check.status == "pass" and note in check.detail

    unlisted = _projects_row(None)
    assert unlisted.status == "pass"
    assert unlisted.detail == "could not list the tags of bank shared — nothing counted"


def test_projects_row_lists_tags_with_the_engines_wildcard_and_pages(monkeypatch) -> None:
    from hal0.cli import _shared

    calls: list[dict] = []
    full_page = [_tag(f"project:repo-{n}", 1) for n in range(da._PROJECT_TAGS_PAGE)]
    pages = [
        {"items": full_page, "total": 501},
        {"items": [_tag("project:last", 1)], "total": 501},
    ]

    def fake_get(path, *, base=None, timeout=10.0, **kwargs):
        calls.append({"path": path, "base": base, "timeout": timeout, **kwargs})
        return pages[len(calls) - 1]

    monkeypatch.setattr(_shared, "api_get", fake_get)
    rows = da._fetch_project_tags("http://api.test", "platform-bank")
    assert rows is not None and len(rows) == 501
    assert [c["path"] for c in calls] == ["/api/memory/banks/platform-bank/tags"] * 2
    assert [c["params"] for c in calls] == [
        {"q": "project:*", "limit": 500, "offset": 0},
        {"q": "project:*", "limit": 500, "offset": 500},
    ]
    assert {c["timeout"] for c in calls} == {da.PROJECT_TAGS_TIMEOUT_S}
    assert {c["base"] for c in calls} == {"http://api.test"}


def test_projects_row_tags_listing_fails_soft(monkeypatch) -> None:
    from hal0.cli import _shared

    def refuse(path, **_kw):
        raise _shared.CliApiError("GET failed: 404")

    monkeypatch.setattr(_shared, "api_get", refuse)
    assert da._fetch_project_tags(None, "shared") is None
    monkeypatch.setattr(_shared, "api_get", lambda path, **_kw: {"unexpected": True})
    assert da._fetch_project_tags(None, "shared") is None


def test_projects_row_reads_the_bank_and_registry_from_hal0_toml(monkeypatch) -> None:
    from types import SimpleNamespace

    from hal0.config import loader
    from hal0.config.schema import MemoryConfig

    memory = MemoryConfig(
        bank="platform-bank",
        projects={"aliases": {"Atlas Mono": "atlas"}, "registered": ["Borealis"]},
    )
    monkeypatch.setattr(loader, "load_hal0_config", lambda: SimpleNamespace(memory=memory))
    bank, registry, read_ok = da._projects_config()
    assert (bank, read_ok) == ("platform-bank", True)
    assert registry.is_known("atlas-mono") and registry.is_known("atlas")
    assert registry.is_known("borealis") and not registry.is_known("cirrus")

    def unreadable():
        raise PermissionError("hal0.toml")

    monkeypatch.setattr(loader, "load_hal0_config", unreadable)
    bank, registry, read_ok = da._projects_config()
    assert (bank, read_ok) == ("shared", False)
    assert not registry.aliases and not registry.registered


# ── memory decision lane (#3519, advisory) ──────────────────────────────────


def _no_probe(_url: str) -> bool:  # pragma: no cover — a call here is the bug
    raise AssertionError("no /health probe expected")


def test_decision_lane_warns_when_local_unit_is_not_installed() -> None:
    from hal0.config.schema import MemoryDecisionConfig

    check = da.check_memory_decision_lane(
        memory=_LIVE_MEMORY,
        decision=MemoryDecisionConfig(),
        unit_installed=False,
        probe=_no_probe,
    )
    assert check.key == "memory_decision_lane"
    assert check.status == "warn"
    assert "hal0-hs-decide" in check.detail and 'provider = "off"' in check.detail


def test_decision_lane_passes_when_the_local_server_answers() -> None:
    from hal0.config.schema import MemoryDecisionConfig

    probed: list[str] = []

    def _probe(url: str) -> bool:
        probed.append(url)
        return True

    check = da.check_memory_decision_lane(
        memory=_LIVE_MEMORY, decision=MemoryDecisionConfig(), unit_installed=True, probe=_probe
    )
    assert check.status == "pass"
    assert probed == ["http://127.0.0.1:9178"]


def test_decision_lane_warns_when_the_installed_server_is_down() -> None:
    from hal0.config.schema import MemoryDecisionConfig

    check = da.check_memory_decision_lane(
        memory=_LIVE_MEMORY,
        decision=MemoryDecisionConfig(),
        unit_installed=True,
        probe=lambda _u: False,
    )
    assert check.status == "warn"
    assert "did not answer /health" in check.detail
    assert "systemctl status hal0-hs-decide" in check.detail


def test_decision_lane_probes_an_explicit_base_url_without_the_unit() -> None:
    from hal0.config.schema import MemoryDecisionConfig

    probed: list[str] = []

    def _probe(url: str) -> bool:
        probed.append(url)
        return False

    check = da.check_memory_decision_lane(
        memory=_LIVE_MEMORY,
        decision=MemoryDecisionConfig(base_url="http://127.0.0.1:9999/"),
        unit_installed=False,
        probe=_probe,
    )
    assert probed == ["http://127.0.0.1:9999"]
    assert check.status == "warn" and "base_url" in check.detail


def test_decision_lane_warns_on_a_base_url_the_drop_in_cannot_carry() -> None:
    from hal0.config.schema import MemoryDecisionConfig

    check = da.check_memory_decision_lane(
        memory=_LIVE_MEMORY,
        decision=MemoryDecisionConfig(base_url="http://decide.example:9178"),
        unit_installed=True,
        probe=_no_probe,
    )
    assert check.status == "warn" and "loopback" in check.detail


@pytest.mark.parametrize(
    "cfg",
    [{"enabled": False}, {"provider": "off"}],
    ids=["disabled", "provider-off"],
)
def test_decision_lane_off_passes_without_probing(cfg) -> None:  # type: ignore[no-untyped-def]
    from hal0.config.schema import MemoryDecisionConfig

    check = da.check_memory_decision_lane(
        memory=_LIVE_MEMORY,
        decision=MemoryDecisionConfig(**cfg),
        unit_installed=False,
        probe=_no_probe,
    )
    assert check.status == "pass" and "off" in check.detail


def test_decision_lane_cloud_is_not_probed() -> None:
    from hal0.config.schema import MemoryDecisionConfig

    check = da.check_memory_decision_lane(
        memory=_LIVE_MEMORY,
        decision=MemoryDecisionConfig(provider="hindsight-cloud"),
        unit_installed=False,
        probe=_no_probe,
    )
    assert check.status == "pass" and "not probed" in check.detail


def test_decision_lane_skips_when_memory_is_disabled() -> None:
    check = da.check_memory_decision_lane(
        memory={"enabled": False, "engine": "hindsight"},
        unit_installed=False,
        probe=_no_probe,
    )
    assert check.status == "pass" and "memory disabled" in check.detail


# ── check_stale_hermes_session_token ───────────────────────────────────────────


def test_stale_session_token_pin_warns_without_leaking_value(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / "api.env"
    env.write_text("OTHER=1\nHERMES_SESSION_TOKEN=s3cr3t-value\n")
    c = da.check_stale_hermes_session_token(env)
    assert c.status == "warn"
    assert c.detail == (
        f"HERMES_SESSION_TOKEN in {env} is ignored since 1.8.0; remove it, then restart hal0-api"
    )
    assert "s3cr3t" not in c.detail


def test_stale_session_token_pin_absent_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / "api.env"
    env.write_text("OTHER=1\n# HERMES_SESSION_TOKEN=commented\n")
    assert da.check_stale_hermes_session_token(env).status == "pass"


def test_stale_session_token_pin_missing_file_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert da.check_stale_hermes_session_token(tmp_path / "nope.env").status == "pass"


def test_hermes_board_wrappers_map_status(tmp_path) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "tok"
    assert da.check_hermes_dashboard_token(path=f).status == "warn"
    f.write_text("x")
    assert da.check_hermes_dashboard_token(path=f).status == "pass"


def test_hermes_kanban_row_reports_unreadable_install_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from hal0.board import auth

    def boom() -> bool:
        raise PermissionError("denied")

    monkeypatch.setattr(auth, "hermes_installed", boom)
    c = da.check_hermes_kanban_reachable()
    assert c.key == "hermes-kanban" and c.status == "warn"
    assert "could not run this check as this user" in c.detail


def test_hermes_dashboard_token_unreadable_is_not_reported_missing(  # type: ignore[no-untyped-def]
    tmp_path, monkeypatch
) -> None:
    from pathlib import Path

    f = tmp_path / "tok"
    f.write_text("x\n")

    def deny(self, *a, **k):  # type: ignore[no-untyped-def]
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "read_text", deny)
    c = da.check_hermes_dashboard_token(path=f)
    assert c.status == "warn"
    assert c.detail.startswith("could not run this check as this user")
    assert "missing" not in c.detail


# ── check_gtt_leak ────────────────────────────────────────────────────────────

_GIB = 1024.0


def _gtt_sample(gtt_used_mb: float | None):  # type: ignore[no-untyped-def]
    from hal0.hardware.gpu_view import GPUMemorySample

    return GPUMemorySample(
        vendor="amd",
        is_uma=True,
        vram_total_mb=512.0,
        gtt_total_mb=98304.0 if gtt_used_mb is not None else None,
        total_mb=98304.0,
        vram_used_mb=100.0,
        gtt_used_mb=gtt_used_mb,
        used_mb=gtt_used_mb,
        gpu_busy=0.0,
        util_is_forced_high=False,
    )


def _kfd_sysfs(root: Path, *, procs: int | None) -> str:
    """A fake ``/sys`` whose KFD proc table holds ``procs`` processes (None = no KFD)."""
    if procs is not None:
        proc = root / "class" / "kfd" / "kfd" / "proc"
        proc.mkdir(parents=True)
        for pid in range(procs):
            (proc / str(1000 + pid)).mkdir()
    return str(root)


def test_gtt_leak_warns_with_the_guards_message(tmp_path: Path) -> None:
    from hal0.slots.gtt_guard import gtt_leak_message

    c = da.check_gtt_leak(
        [],
        sample=_gtt_sample(40 * _GIB),
        sys_root=_kfd_sysfs(tmp_path, procs=0),
    )
    assert c.key == "gtt_leak" and c.status == "warn"
    expected = gtt_leak_message(
        gtt_used_mb=40 * _GIB, resident_gpu_mb=0.0, resident_gpu_slots=0, kfd_procs=0
    )
    assert expected is not None and c.detail == expected
    assert "halogen-flash-server issue #79" in c.detail
    assert "another guest on this host" in c.detail
    assert "a host reboot frees it" in c.detail


def test_gtt_leak_passes_on_a_healthy_vulkan_only_box(tmp_path: Path) -> None:
    """Review Focus 3: two Vulkan residents hold the GTT, KFD proc table empty."""
    slots = [
        {"name": "brain", "state": "ready", "backend": "vulkan"},
        {"name": "coder", "state": "serving", "backend": "vulkan"},
    ]
    c = da.check_gtt_leak(
        slots,
        sample=_gtt_sample(38 * _GIB),
        sys_root=_kfd_sysfs(tmp_path, procs=0),
    )
    assert c.status == "pass"


def test_gtt_leak_an_npu_resident_does_not_hide_a_leak(tmp_path: Path) -> None:
    slots = [{"name": "flm", "state": "ready", "backend": "flm"}]
    c = da.check_gtt_leak(
        slots,
        sample=_gtt_sample(40 * _GIB),
        sys_root=_kfd_sysfs(tmp_path, procs=0),
    )
    assert c.status == "warn"


def test_gtt_leak_passes_when_a_kfd_process_holds_the_gpu(tmp_path: Path) -> None:
    c = da.check_gtt_leak(
        [],
        sample=_gtt_sample(40 * _GIB),
        sys_root=_kfd_sysfs(tmp_path, procs=1),
    )
    assert c.status == "pass"


def test_gtt_leak_passes_when_the_kfd_table_is_unreadable(tmp_path: Path) -> None:
    c = da.check_gtt_leak(
        [],
        sample=_gtt_sample(40 * _GIB),
        sys_root=_kfd_sysfs(tmp_path, procs=None),
    )
    assert c.status == "pass"
    assert "kfd" in c.detail.lower()


def test_gtt_leak_passes_when_the_gpu_memory_is_small(tmp_path: Path) -> None:
    c = da.check_gtt_leak(
        [],
        sample=_gtt_sample(2 * _GIB),
        sys_root=_kfd_sysfs(tmp_path, procs=0),
    )
    assert c.status == "pass" and "2.0 GiB" in c.detail


def test_gtt_leak_without_amdgpu_counters_passes() -> None:
    c = da.check_gtt_leak([], sample=_gtt_sample(None))
    assert c.status == "pass" and c.detail == "no amdgpu counters"


def test_gtt_leak_a_failing_sample_reads_as_no_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise OSError("no sysfs")

    monkeypatch.setattr("hal0.hardware.gpu_view.sample", boom)
    c = da.check_gtt_leak([])
    assert c.status == "pass" and c.detail == "no amdgpu counters"


def test_gtt_leak_skips_when_the_slot_list_is_unavailable(tmp_path: Path) -> None:
    """Without the slot list a leak cannot be told from slot memory (a Vulkan-only
    box looks identical), so the row says it skipped instead of crying leak."""
    root = _kfd_sysfs(tmp_path, procs=0)
    for payload in (None, {"error": "x"}):
        c = da.check_gtt_leak(payload, sample=_gtt_sample(40 * _GIB), sys_root=root)
        assert c.status == "pass" and "slot list" in c.detail


def test_gtt_leak_with_no_arguments_reads_the_live_host() -> None:
    c = da.check_gtt_leak()
    assert c.key == "gtt_leak" and c.status in {"pass", "warn"}


# ── migration 015 pending (#3297) ────────────────────────────────────────────


def _db_with_versions(path, versions):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    conn.executemany("INSERT INTO schema_migrations VALUES (?, 'x')", [(v,) for v in versions])
    conn.commit()
    conn.close()
    return path


def test_migration_015_applied_passes(tmp_path) -> None:
    from hal0.cli import doctor_all as da

    db = _db_with_versions(tmp_path / "h.db", [14, 15])
    assert da.check_migration_015(db_path=db).status == "pass"


def test_migration_015_pending_with_failed_backup_fails(tmp_path) -> None:
    from hal0.cli import doctor_all as da

    db = _tuned_db(tmp_path / "h.db")
    check = da.check_migration_015(db_path=db, backup_problem=lambda: "disk full")
    assert check.status == "fail"
    assert "db.migration_backup_failed" in check.detail
    assert "doctor perms --fix" in check.detail


def test_migration_015_pending_otherwise_warns(tmp_path) -> None:
    from hal0.cli import doctor_all as da

    db = _db_with_versions(tmp_path / "h.db", [14])
    assert da.check_migration_015(db_path=db, backup_problem=lambda: None).status == "warn"


def _tuned_db(path):
    import sqlite3

    _db_with_versions(path, [14])
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE model (id TEXT, extra TEXT)")
    conn.execute("ALTER TABLE model ADD COLUMN context_size INTEGER")
    conn.execute("INSERT INTO model VALUES ('m', NULL, 4096)")
    conn.commit()
    conn.close()
    return path


def test_migration_015_unreadable_var_lib_is_a_warn_row(tmp_path, monkeypatch) -> None:
    from hal0.cli import doctor_all as da

    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr(da.Path, "exists", boom)
    check = da.check_migration_015(db_path=tmp_path / "h.db")
    assert check.status == "warn" and "sudo hal0 doctor all" in check.detail


def test_migration_015_no_probe_without_retired_tuning(tmp_path) -> None:
    from hal0.cli import doctor_all as da

    db = _db_with_versions(tmp_path / "h.db", [14])

    def probe():
        raise AssertionError("probe must not run")

    assert da.check_migration_015(db_path=db, backup_problem=probe).status == "warn"


def test_migration_015_probe_runs_with_tuning(tmp_path) -> None:
    from hal0.cli import doctor_all as da

    db = _tuned_db(tmp_path / "h.db")
    check = da.check_migration_015(db_path=db, backup_problem=lambda: "full")
    assert check.status == "fail"


def test_backup_probe_non_member_non_root_warns(tmp_path, monkeypatch) -> None:
    from hal0.cli import doctor_all as da

    monkeypatch.setattr(da, "_hal0_ids", lambda: (4242, 4242))
    monkeypatch.setattr(da.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(da.os, "getgroups", lambda: [1000])
    assert isinstance(da._backup_probe(tmp_path / "h.db", tmp_path / "b"), da._NeedsSudo)
    db = _tuned_db(tmp_path / "h.db")
    check = da.check_migration_015(db_path=db, backup_problem=lambda: da._NeedsSudo("re-run"))
    assert check.status == "warn"


def test_backup_probe_root_judges_by_mode_bits(tmp_path, monkeypatch) -> None:
    from hal0.cli import doctor_all as da

    backups = tmp_path / "backups"
    backups.mkdir()
    st = backups.stat()
    monkeypatch.setattr(da.os, "geteuid", lambda: 0)
    monkeypatch.setattr(da, "_hal0_ids", lambda: (st.st_uid, st.st_gid))
    backups.chmod(0o500)
    assert "not writable by hal0" in da._backup_probe(tmp_path / "h.db", backups)
    backups.chmod(0o700)
    assert da._backup_probe(tmp_path / "h.db", backups) is None
    monkeypatch.setattr(da, "_hal0_ids", lambda: (st.st_uid + 1, st.st_gid))
    backups.chmod(0o770)
    assert da._backup_probe(tmp_path / "h.db", backups) is None
    backups.chmod(0o750)
    assert da._backup_probe(tmp_path / "h.db", backups) is not None
    monkeypatch.setattr(da, "_hal0_ids", lambda: (st.st_uid + 1, st.st_gid + 1))
    backups.chmod(0o707)
    assert da._backup_probe(tmp_path / "h.db", backups) is None


def test_backup_probe_unwritable_and_low_space(tmp_path, monkeypatch) -> None:
    from hal0.cli import doctor_all as da

    backups = tmp_path / "backups"
    backups.mkdir()
    db = tmp_path / "h.db"
    db.write_bytes(b"x" * 4096)
    monkeypatch.setattr(da, "_hal0_ids", lambda: None)
    monkeypatch.setattr(da.os, "access", lambda *a, **k: False)
    assert "not writable" in da._backup_probe(db, backups)
    monkeypatch.setattr(da.os, "access", lambda *a, **k: True)
    monkeypatch.setattr(da.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    assert "free" in da._backup_probe(db, backups)


def test_backup_probe_non_directory_backups_fails_row(tmp_path) -> None:
    from hal0.cli import doctor_all as da

    backups = tmp_path / "backups"
    backups.write_text("x")
    assert "not a directory" in da._backup_probe(tmp_path / "h.db", backups)


# -- Box boundary row ----------------------------------------------------------


def test_box_row_passes_off_box() -> None:
    c = da.check_box_boundary(in_box=lambda: False)
    assert (c.key, c.label, c.status) == ("box", "Box boundary", "pass")


def test_box_row_passes_in_box_when_nothing_stranded() -> None:
    c = da.check_box_boundary(
        in_box=lambda: True, publish_hosts=lambda: {"utility": "127.0.0.1"}, api_port=lambda: 8080
    )
    assert c.status == "pass" and "8080, 3001, 8188" in c.detail


def test_box_row_warns_on_unreachable_publish() -> None:
    c = da.check_box_boundary(
        in_box=lambda: True, publish_hosts=lambda: {"utility": "0.0.0.0"}, api_port=lambda: 8080
    )
    assert c.status == "warn" and "utility" in c.detail and "8080" in c.detail
    assert "3001" in c.detail and "8188" in c.detail


def test_box_row_warns_on_changed_api_port() -> None:
    c = da.check_box_boundary(in_box=lambda: True, publish_hosts=lambda: {}, api_port=lambda: 9000)
    assert c.status == "warn" and "9000" in c.detail


def test_box_default_providers_read_real_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HAL0_PORT", "9001")
    c = da.check_box_boundary(in_box=lambda: True, publish_hosts=lambda: {})
    assert c.status == "warn" and "9001" in c.detail


# ── check_fabric_clock ───────────────────────────────────────────────────────


def _fclk_sysfs(root: Path, *, perf: str, marked_index: int | None) -> str:
    device = root / "class" / "drm" / "card1" / "device"
    device.mkdir(parents=True)
    levels = [
        "0: 400Mhz",
        "1: 800Mhz",
        "2: 1200Mhz",
        "3: 1600Mhz",
        "4: 1800Mhz",
        "5: 1900Mhz",
        "6: 1950Mhz",
        "7: 2000Mhz",
    ]
    if marked_index is not None:
        levels[marked_index] += " *"
    (device / "pp_dpm_fclk").write_text("\n".join(levels) + "\n")
    (device / "power_dpm_force_performance_level").write_text(perf)
    return str(root)


def test_fabric_clock_passes_without_slot_list() -> None:
    c = da.check_fabric_clock(None)
    assert c.status == "pass" and "unavailable" in c.detail


def test_fabric_clock_passes_with_no_npu_gpu_pairing() -> None:
    slots = [{"name": "chat", "state": "ready", "backend": "vulkan"}]
    c = da.check_fabric_clock(slots)
    assert c.status == "pass" and "no NPU+GPU" in c.detail


def test_fabric_clock_passes_when_held(tmp_path: Path) -> None:
    slots = [
        {"name": "flm", "state": "ready", "backend": "flm"},
        {"name": "chat", "state": "ready", "backend": "vulkan"},
    ]
    c = da.check_fabric_clock(slots, sys_root=_fclk_sysfs(tmp_path, perf="manual", marked_index=7))
    assert c.status == "pass" and "held" in c.detail


def test_fabric_clock_warns_when_npu_and_gpu_configured_and_unheld(tmp_path: Path) -> None:
    slots = [
        {"name": "flm", "state": "ready", "backend": "flm"},
        {"name": "chat", "state": "idle", "backend": "vulkan"},
    ]
    c = da.check_fabric_clock(slots, sys_root=_fclk_sysfs(tmp_path, perf="auto", marked_index=0))
    assert c.status == "warn"
    assert "flm" in c.detail and "chat" in c.detail
    assert "hal0-fabric-clock hold" in c.detail


def test_fabric_clock_passes_unreadable_when_sysfs_absent(tmp_path: Path) -> None:
    slots = [
        {"name": "flm", "state": "ready", "backend": "flm"},
        {"name": "chat", "state": "ready", "backend": "vulkan"},
    ]
    c = da.check_fabric_clock(slots, sys_root=str(tmp_path / "no-such-sys"))
    assert c.status == "pass"
    assert "unreadable" in c.detail


def test_fabric_clock_ignores_synthetic_hal0_composite(tmp_path: Path) -> None:
    """review P2-5: the API always folds in a synthetic composite row named
    ``hal0`` (backend='vulkan' from slot_view synthesis). Counting it as a
    real, individually-configured GPU slot turned every box with an NPU
    slot and NO real GPU slot configured into a false WARN naming ``hal0``
    — exclude ``_synthetic`` rows so only genuine per-slot configs pair.
    """
    slots = [
        {"name": "flm", "state": "ready", "backend": "flm"},
        {
            "name": "hal0",
            "state": "serving",
            "backend": "vulkan",
            "_synthetic": True,
        },
    ]
    c = da.check_fabric_clock(slots, sys_root=_fclk_sysfs(tmp_path, perf="auto", marked_index=0))
    assert c.status == "pass", c.detail
    assert "hal0" not in c.detail


# ── memory engine env (#3520 / #3388) ────────────────────────────────────────


def test_memory_engine_env_passes_when_engine_not_installed() -> None:
    check = da.check_memory_engine_env(report={"installed": False})
    assert check.status == "pass"


def test_memory_engine_env_passes_when_unit_matches() -> None:
    report = {"installed": True, "checked": True, "drift": [], "legacy_drop_in": None}
    assert da.check_memory_engine_env(report=report).status == "pass"


def test_memory_engine_env_warns_on_drift_with_the_numbers() -> None:
    report = {
        "installed": True,
        "checked": True,
        "drift": [
            {"key": "HINDSIGHT_API_LLM_MAX_CONCURRENT", "expected": "1", "effective": "2"},
        ],
        "legacy_drop_in": None,
    }
    check = da.check_memory_engine_env(report=report)
    assert check.status == "warn"
    assert "HINDSIGHT_API_LLM_MAX_CONCURRENT=2" in check.detail
    assert "hal0 renders 1" in check.detail


def test_memory_engine_env_warns_on_leftover_legacy_drop_in() -> None:
    report = {
        "installed": True,
        "checked": True,
        "drift": [],
        "legacy_drop_in": "/x/zz-h0-98-llm-global-cap.conf",
    }
    check = da.check_memory_engine_env(report=report)
    assert check.status == "warn"
    assert "zz-h0-98-llm-global-cap.conf" in check.detail


def test_memory_engine_env_warns_when_unit_env_unreadable() -> None:
    report = {"installed": True, "checked": False, "drift": [], "legacy_drop_in": None}
    assert da.check_memory_engine_env(report=report).status == "warn"


# ── memory embedder: recorded against configured (#3518) ─────────────────────

_BGE = "BAAI/bge-small-en-v1.5"
_HS = "hindsight-ai/hs-embed-1"


def _embedder(recorded: str, configured: str, *, mixed: bool = False, **extra: object) -> dict:
    status: dict = {
        "recorded": recorded,
        "recorded_provider": "local",
        "configured": configured,
        "configured_provider": "local",
        "mixed": mixed,
        "source": "engine-default",
        "recorded_at": "2026-11-10T21:00:00+00:00",
        "effective": None,
        "conflict": None,
        "record_error": None,
        "store_unavailable": None,
        **extra,
    }
    status["needs_switch"] = mixed or (status["recorded_provider"], recorded) != (
        status["configured_provider"],
        configured,
    )
    return status


def test_memory_embedder_passes_when_the_store_matches_the_config() -> None:
    check = da.check_memory_embedder(status=_embedder(_BGE, _BGE))
    assert (check.key, check.status) == ("memory_embedder", "pass")
    assert _BGE in check.detail


def test_memory_embedder_passes_with_a_note_on_an_upgraded_box_at_the_default() -> None:
    """Store on bge-small, config still at its shipped default: every upgraded
    box. Not a warning (nothing the operator did), but it names the way to
    move the store."""
    check = da.check_memory_embedder(status=_embedder(_BGE, _HS))
    assert check.status == "pass"
    assert f"built with {_BGE} and stays on it" in check.detail
    assert f"the configured model ({_HS})" in check.detail
    assert f"only through `hal0 memory embedder switch {_HS}`" in check.detail
    assert "re-embeds the store" in check.detail


def test_memory_embedder_warns_when_the_operator_set_a_model_that_differs() -> None:
    check = da.check_memory_embedder(status=_embedder(_BGE, "example-org/another-embedder"))
    assert check.status == "warn"
    assert f"built with {_BGE}" in check.detail
    assert "[memory.models] asks for example-org/another-embedder" in check.detail
    assert "no effect" in check.detail
    # Names the way out, with the model it would move the store to.
    assert "`hal0 memory embedder switch example-org/another-embedder`" in check.detail
    assert "a dry run; add --apply" in check.detail


def test_memory_embedder_names_the_provider_in_the_switch_command() -> None:
    check = da.check_memory_embedder(status=_embedder(_BGE, _BGE, configured_provider="openai"))
    assert f"`hal0 memory embedder switch {_BGE} --provider openai`" in check.detail


def test_memory_embedder_offers_the_switch_as_the_repair_for_a_mixed_store() -> None:
    check = da.check_memory_embedder(status=_embedder(_HS, _HS, mixed=True))
    assert check.status == "warn"
    assert f"`hal0 memory embedder switch {_HS}`" in check.detail


def test_memory_embedder_reports_an_interrupted_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A switch that stopped half-way leaves memory writes paused: say so first."""
    from hal0.memory import embedder_switch as es

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    es.journal_path().parent.mkdir(parents=True)
    es.journal_path().write_text(
        jsonlib.dumps(
            {
                "version": 1,
                "run_id": "3f9a1c2e",
                "started_at": "2026-11-11T02:00:00+00:00",
                "pid": 1,
                "phase": "swapping",
                "target": {"provider": "local", "model": _HS},
                "previous": {"provider": "local", "model": _BGE},
                "archive_dir": "/nowhere",
                "error": "hindsight-admin rename-bank exited 1",
            }
        )
    )

    # The journal alone, its pause gone: writes may be reaching half-replaced banks.
    check = da.check_memory_embedder()

    assert (check.key, check.status) == ("memory_embedder", "warn")
    assert "did not finish (hindsight-admin rename-bank exited 1)" in check.detail
    assert "run 3f9a1c2e, archives in /nowhere" in check.detail
    assert "its write pause is gone" in check.detail
    assert "`hal0 memory embedder switch --status`" in check.detail
    assert "`hal0 memory embedder switch --resume`" in check.detail


def test_memory_embedder_is_informational_after_a_switch_away_from_the_config() -> None:
    """The operator moved the store on purpose; the config naming another
    model is then a fact to state, with the setting that makes them agree."""
    status = _embedder(_BGE, "example-org/another-embedder", source="embedder-switch")
    check = da.check_memory_embedder(status=status)
    assert check.status == "pass"
    assert f"moved to {_BGE} with `hal0 memory embedder switch`" in check.detail
    assert "which is fine and has no effect" in check.detail
    assert "embeddings_model under [memory.models]" in check.detail

    # The same difference without a switch behind it is still the operator's to settle.
    plain = _embedder(_BGE, "example-org/another-embedder")
    assert da.check_memory_embedder(status=plain).status == "warn"


def test_memory_embedder_says_when_the_engine_is_private_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.memory import embedder_switch as es
    from hal0.memory import write_gate

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    write_gate.engage_gate(run_id="3f9a1c2e", archive_dir="/backups/x")
    monkeypatch.setattr(es, "isolated_port", lambda: 45123)

    check = da.check_memory_embedder()

    assert check.status == "warn"
    assert "the engine is isolated on a private port (45123)" in check.detail
    assert "recall is unavailable too" in check.detail
    assert "`hal0 memory embedder switch --resume`" in check.detail


def test_memory_embedder_says_when_the_engine_is_back_before_the_gate_lifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finish whose last restart failed: drop-in gone, gate and journal still there."""
    from hal0.memory import embedder_switch as es
    from hal0.memory import write_gate

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    write_gate.engage_gate(run_id="3f9a1c2e", archive_dir="/backups/x")
    monkeypatch.setattr(es, "isolated_port", lambda: None)
    monkeypatch.setattr(
        es,
        "interrupted_switch",
        lambda: {"run_id": "3f9a1c2e", "phase": "swapping", "isolated": True, "error": "boom"},
    )

    check = da.check_memory_embedder()

    assert check.status == "warn"
    assert "back at its normal address" in check.detail
    assert "recall works" in check.detail
    assert "`--resume` isolates it again" in check.detail
    assert "`hal0 memory embedder switch --resume`" in check.detail


def test_memory_embedder_reports_a_write_gate_that_no_switch_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A killed switch leaves the gate set on purpose; this row is its liveness story."""
    from hal0.memory import write_gate

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    write_gate.engage_gate(run_id="3f9a1c2e", archive_dir="/backups/memory-embedder-switch-x")

    check = da.check_memory_embedder()

    assert check.status == "warn"
    assert "run 3f9a1c2e, archives in /backups/memory-embedder-switch-x" in check.detail
    assert "no switch is running, so every memory write is refused" in check.detail
    assert "`hal0 memory embedder switch --status`" in check.detail
    assert "`hal0 memory embedder switch --resume`" in check.detail


def test_memory_embedder_passes_while_a_switch_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    from hal0.memory import embedder_switch as es
    from hal0.memory import write_gate

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    write_gate.engage_gate(run_id="3f9a1c2e", archive_dir="/x")
    fd = os.open(es.lock_path(), os.O_RDONLY | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        check = da.check_memory_embedder()
    finally:
        os.close(fd)

    assert check.status == "pass" and "an embedder switch is running" in check.detail


def test_memory_embedder_warns_when_the_operator_set_a_provider_that_differs() -> None:
    """Same model, a remote provider: an explicit setting that is not applied."""
    check = da.check_memory_embedder(status=_embedder(_BGE, _BGE, configured_provider="openai"))
    assert check.status == "warn"
    assert "the openai provider" in check.detail and "no effect" in check.detail


def test_memory_embedder_names_a_recorded_remote_provider_when_it_passes() -> None:
    status = _embedder(_BGE, _BGE, recorded_provider="openai", configured_provider="openai")
    check = da.check_memory_embedder(status=status)
    assert check.status == "pass"
    assert "the openai provider" in check.detail


def test_memory_embedder_warns_on_a_mixed_store_even_when_the_models_match() -> None:
    check = da.check_memory_embedder(status=_embedder(_HS, _HS, mixed=True))
    assert check.status == "warn"
    assert "two embedders" in check.detail
    assert f"`hal0 memory embedder switch {_HS}`" in check.detail
    assert "has no effect" not in check.detail


def test_memory_embedder_warns_when_a_store_on_the_default_model_is_configured_away() -> None:
    """Recorded hs-embed-1, operator asks for bge-small: an explicit setting that is not applied."""
    check = da.check_memory_embedder(status=_embedder(_HS, _BGE))
    assert check.status == "warn"
    assert f"[memory.models] asks for {_BGE}" in check.detail


def test_memory_embedder_warns_on_a_mixed_store_at_the_default_config() -> None:
    """Mixed outranks the upgraded-box note."""
    check = da.check_memory_embedder(status=_embedder(_BGE, _HS, mixed=True))
    assert check.status == "warn"
    assert "two embedders" in check.detail


def test_memory_embedder_warns_naming_both_when_the_engine_disagrees_with_the_record() -> None:
    conflict = {
        "live": {"provider": "local", "model": _HS},
        "on_disk": {"provider": "local", "model": _HS},
    }
    check = da.check_memory_embedder(status=_embedder(_BGE, _BGE, conflict=conflict))
    assert check.status == "warn"
    assert f"record says {_BGE}" in check.detail
    assert f"the running engine is on {_HS}" in check.detail
    assert f"would start it on {_HS}" in check.detail
    assert "changed neither" in check.detail


def test_memory_embedder_warns_with_the_path_when_the_record_cannot_be_read() -> None:
    status = _embedder(_BGE, _BGE, record_error="/var/lib/hal0/state/memory-embedder.json")
    check = da.check_memory_embedder(status=status)
    assert check.status == "warn"
    assert "/var/lib/hal0/state/memory-embedder.json cannot be read" in check.detail
    assert "left the file as it is" in check.detail


def test_memory_embedder_warns_when_the_store_location_is_unavailable() -> None:
    status = _embedder(
        _BGE,
        _HS,
        source="store-unavailable",
        recorded_at=None,
        store_unavailable="/var/lib/hal0/memory/hindsight",
    )
    check = da.check_memory_embedder(status=status)
    assert check.status == "warn"
    assert "/var/lib/hal0/memory/hindsight, is not available" in check.detail
    assert "nothing is recorded" in check.detail


def test_memory_embedder_passes_on_a_box_with_no_store() -> None:
    status = _embedder(_HS, _HS, source="configured", recorded_at=None)
    check = da.check_memory_embedder(status=status)
    assert check.status == "pass"
    assert "no memory store" in check.detail


def test_memory_embedder_reads_this_boxs_store_by_default(tmp_path, monkeypatch) -> None:
    """Unstubbed: an existing store and the default config is the upgraded-box note."""
    from hal0.memory import extraction_env as ee
    from hal0.memory.engine_upgrade import hindsight_dir

    monkeypatch.setattr(ee, "SHOWCASE_DROP_IN_PATH", tmp_path / "absent" / "showcase-lanes.conf")
    assert da.check_memory_embedder().status == "pass"  # no store yet

    (hindsight_dir() / ".pg0" / "instances" / "hindsight" / "data").mkdir(parents=True)
    check = da.check_memory_embedder()

    assert check.status == "pass"
    assert f"built with {_BGE} and stays on it" in check.detail and _HS in check.detail


def test_memory_embedder_skips_when_the_config_is_unreadable(monkeypatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr("hal0.config.loader.load_hal0_config", boom)
    check = da.check_memory_embedder()
    assert check.status == "warn"
    assert "unreadable" in check.detail
