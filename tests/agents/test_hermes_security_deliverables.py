"""RATIFIED 2026-07-18 security/validation deliverables (2, 3, 6).

* D2 — when the terminal tool is opted into (#1863 — it is OFF by default),
  terminal.cwd points at a scratch dir under HERMES_HOME, never /etc/hal0.
* D3 — a strong random API_SERVER_KEY is generated (>=32 chars, cryptographic),
  never a placeholder / hardcoded fallback; idempotent across reruns.
* D6 — `--repair` reconciles HERMES_HOME/agents + venv ownership drift to hal0.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp

# ── D2: terminal.cwd off /etc/hal0 ──────────────────────────────────────────


def _overlay(hermes_home: Path | str) -> dict[str, object]:
    return dict(
        hp._build_config_overlay(
            primary={"model_id": "m", "backend_url": "http://x/v1", "placeholder": False},
            chat_slots=[],
            delegation=None,
            auxiliary_tasks={},
            mcp_servers=[],
            agent_id="hermes",
            live_resolve_enabled=True,
            hermes_home=hermes_home,
            # D2 is about WHERE the shell lands once the operator has opted in;
            # the default-off posture itself is covered in
            # test_hermes_terminal_optin.py.
            terminal_enabled=True,
        )
    )


def test_approvals_mode_written_explicitly_as_smart() -> None:
    # Operator choice: smart approvals, pinned by hal0 rather than inherited.
    assert _overlay("/var/lib/hal0/.hermes")["approvals.mode"] == "smart"
    assert hp.HAL0_APPROVALS_MODE == "smart"


def test_approval_judge_rides_the_main_hal0_model() -> None:
    # No auxiliary.approval.* override: hermes's "auto" resolution sends the
    # smart-approval call to the main provider (custom -> hal0 gateway), the
    # same endpoint and model the chat turn uses.
    overlay = _overlay("/var/lib/hal0/.hermes")
    assert not [k for k in overlay if k.startswith("auxiliary.approval.")]
    assert overlay["model.provider"] == "custom"
    assert overlay["model.base_url"] == "http://127.0.0.1:8080/v1"
    assert overlay["model.default"] == "hal0/agent"


def test_terminal_cwd_is_scratch_under_hermes_home() -> None:
    overlay = _overlay("/var/lib/hal0/.hermes")
    assert overlay["terminal.cwd"] == "/var/lib/hal0/.hermes/scratch"


def test_terminal_cwd_never_etc_hal0() -> None:
    overlay = _overlay("/var/lib/hal0/.hermes")
    assert overlay["terminal.cwd"] != "/etc/hal0"
    assert "/etc/hal0" not in str(overlay["terminal.cwd"])


def test_terminal_backend_is_local_only_when_opted_in() -> None:
    # `local` is still the only viable backend on this box (podman, no docker;
    # a container could not reach the host or 127.0.0.1:8080), so opting in
    # reproduces the historical config exactly. Opting out writes no backend.
    assert _overlay("/var/lib/hal0/.hermes")["terminal.backend"] == "local"
    off = dict(
        hp._build_config_overlay(
            primary={"model_id": "m", "backend_url": "http://x/v1", "placeholder": False},
            chat_slots=[],
            delegation=None,
            auxiliary_tasks={},
            mcp_servers=[],
            agent_id="hermes",
            live_resolve_enabled=True,
            hermes_home="/var/lib/hal0/.hermes",
            terminal_enabled=False,
        )
    )
    assert "terminal.backend" not in off
    assert "terminal.cwd" not in off


def test_scratch_dir_is_seeded_by_home_init() -> None:
    # home_init must create the scratch/ dir the overlay points terminal.cwd at.
    src = Path(hp.__file__).read_text(encoding="utf-8")
    assert '"scratch"' in src, "home_init must create $HERMES_HOME/scratch"


# ── D3: strong API_SERVER_KEY, never a placeholder ──────────────────────────


def test_generated_key_is_strong_and_long() -> None:
    key = hp._generate_api_server_key()
    assert len(key) >= hp.API_SERVER_KEY_MIN_LENGTH >= 32
    assert hp._is_strong_api_server_key(key)


def test_generated_keys_are_unique_not_hardcoded() -> None:
    keys = {hp._generate_api_server_key() for _ in range(5)}
    assert len(keys) == 5, "keys must be random, not a hardcoded constant"


@pytest.mark.parametrize(
    "weak",
    ["", "changeme", "placeholder", "dummy", "hal0-local", "short", "x" * 31],
)
def test_placeholder_and_weak_keys_rejected(weak: str) -> None:
    assert not hp._is_strong_api_server_key(weak)


def test_ensure_generates_when_absent() -> None:
    written: dict[str, str] = {}
    result = hp.ensure_gateway_api_server_key(
        existing={}, write=lambda updates: written.update(updates)
    )
    assert result.outcome == "generated"
    assert len(written["API_SERVER_KEY"]) >= 32
    assert hp._is_strong_api_server_key(written["API_SERVER_KEY"])


def test_ensure_is_idempotent_with_existing_strong_key() -> None:
    strong = hp._generate_api_server_key()
    written: dict[str, str] = {}

    def _fail_write(_updates: dict[str, str]) -> None:  # pragma: no cover
        raise AssertionError("must not rewrite an already-strong key")

    result = hp.ensure_gateway_api_server_key(
        existing={"API_SERVER_KEY": strong}, write=_fail_write
    )
    assert result.outcome == "present"
    assert written == {}


def test_ensure_replaces_weak_existing_key() -> None:
    written: dict[str, str] = {}
    result = hp.ensure_gateway_api_server_key(
        existing={"API_SERVER_KEY": "changeme"},
        write=lambda updates: written.update(updates),
    )
    assert result.outcome == "generated"
    assert hp._is_strong_api_server_key(written["API_SERVER_KEY"])


def test_no_hardcoded_fallback_in_source() -> None:
    # There must be no literal placeholder key handed to the gateway.
    src = Path(hp.__file__).read_text(encoding="utf-8")
    assert "token_urlsafe(32)" in src
    assert 'API_SERVER_KEY", "' not in src  # never a literal "API_SERVER_KEY"="lit"


# ── D6: --repair reconciles ownership drift ─────────────────────────────────


def test_reconcile_disabled_is_noop() -> None:
    result = hp.reconcile_ownership_on_repair(enabled=False)
    assert result.reconciled == []
    assert result.skipped_reason


def test_reconcile_fixes_hermes_home_and_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import grp as _grp
    import pwd as _pwd
    from types import SimpleNamespace

    from hal0.install import perms as _perms

    # The `hal0` service user/group don't exist in CI; fake the id lookups
    # (perms.commit + the venv chown both resolve hal0:hal0).
    monkeypatch.setattr(_pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=1500))
    monkeypatch.setattr(_grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=1500))

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "hermes").write_text("#!/bin/sh\n")

    chowned: list[tuple[str, int, int]] = []

    # Fake observe: report every declarative row as root-owned drift so commit
    # has something to reconcile.
    def _observe(path: Path) -> _perms.PermObservation:
        return _perms.PermObservation(
            path=path, exists=True, owner="root", group="root", mode=0o700
        )

    result = hp.reconcile_ownership_on_repair(
        enabled=True,
        venv=venv,
        observe_fn=_observe,
        chown=lambda p, u, g: chowned.append((p, u, g)),
        chmod=lambda p, m: None,
    )

    # The venv tree is chowned (root + bin/ + bin/hermes), and the declarative
    # HERMES_HOME/agents rows are committed.
    assert str(venv) in result.reconciled
    chowned_paths = {c[0] for c in chowned}
    assert str(venv) in chowned_paths
    assert str(venv / "bin" / "hermes") in chowned_paths


# ── #2908: a hal0 caller cannot stat the 0700 root vault directory ──────────


def _non_root_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vault = tmp_path / "secrets" / "agents" / "hermes.env"
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)
    monkeypatch.setattr(hp.os, "geteuid", lambda: 999)
    monkeypatch.setattr(hp.os, "access", lambda _p, _m: False)
    return vault


def test_ensure_non_root_keeps_a_key_the_seam_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _non_root_vault(tmp_path, monkeypatch)
    monkeypatch.setattr(hp, "_vault_key_names", lambda: {"API_SERVER_KEY", "HF_TOKEN"})

    def _fail_write(_updates: dict[str, str]) -> None:  # pragma: no cover
        raise AssertionError("must not rotate a key the vault already holds")

    result = hp.ensure_gateway_api_server_key(write=_fail_write)
    assert result.outcome == "unreadable"


def test_ensure_non_root_generates_when_the_seam_reports_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _non_root_vault(tmp_path, monkeypatch)
    monkeypatch.setattr(hp, "_vault_key_names", lambda: {"HF_TOKEN"})
    written: dict[str, str] = {}
    result = hp.ensure_gateway_api_server_key(write=lambda u: written.update(u))
    assert result.outcome == "generated"
    assert hp._is_strong_api_server_key(written["API_SERVER_KEY"])


def test_ensure_non_root_never_stats_a_0700_vault_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fresh-box repro: ``secrets/agents`` exists, 0700 root, no file yet.

    ``Path.exists()`` raises ``PermissionError`` there on Python 3.12+, which
    the phase recorded as ``api_server_key: failed`` while reporting itself ok,
    so the gateway never got a key (#2908).
    """
    # ``os.getuid``: the agents conftest fakes ``geteuid`` to 0 for every test.
    if os.getuid() == 0:
        pytest.skip("root can stat any directory")
    vault = tmp_path / "secrets" / "agents" / "hermes.env"
    vault.parent.mkdir(parents=True)
    vault.parent.chmod(0)
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)
    monkeypatch.setattr(hp.os, "geteuid", lambda: 999)
    monkeypatch.setattr(hp, "_vault_key_names", set)
    written: dict[str, str] = {}
    try:
        result = hp.ensure_gateway_api_server_key(write=lambda u: written.update(u))
    finally:
        vault.parent.chmod(0o700)
    assert result.outcome == "generated" and "API_SERVER_KEY" in written


def test_vault_key_names_asks_the_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.agents import hermes_platforms as hpl

    seen: dict[str, object] = {}

    def _fake_read_vault(path, *, seam=None, run=None):
        seen["path"], seen["seam"] = path, seam
        return {"API_SERVER_KEY": "1"}

    monkeypatch.setattr(hpl, "read_vault", _fake_read_vault)
    assert hp._vault_key_names() == {"API_SERVER_KEY"}
    assert seen == {"path": hp.HERMES_SECRETS_ENV, "seam": hp._HAL0_AGENTENV}
