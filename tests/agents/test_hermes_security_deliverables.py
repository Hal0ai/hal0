"""RATIFIED 2026-07-18 security/validation deliverables (2, 3, 6).

* D2 — terminal.cwd moves off /etc/hal0 to a scratch dir under HERMES_HOME
  (terminal.backend=local stays).
* D3 — a strong random API_SERVER_KEY is generated (>=32 chars, cryptographic),
  never a placeholder / hardcoded fallback; idempotent across reruns.
* D6 — `--repair` reconciles HERMES_HOME/agents + venv ownership drift to hal0.
"""

from __future__ import annotations

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
            system_prompt="",
            personality_name="",
            live_resolve_enabled=True,
            hermes_home=hermes_home,
        )
    )


def test_terminal_cwd_is_scratch_under_hermes_home() -> None:
    overlay = _overlay("/var/lib/hal0/.hermes")
    assert overlay["terminal.cwd"] == "/var/lib/hal0/.hermes/scratch"


def test_terminal_cwd_never_etc_hal0() -> None:
    overlay = _overlay("/var/lib/hal0/.hermes")
    assert overlay["terminal.cwd"] != "/etc/hal0"
    assert "/etc/hal0" not in str(overlay["terminal.cwd"])


def test_terminal_backend_stays_local() -> None:
    assert _overlay("/var/lib/hal0/.hermes")["terminal.backend"] == "local"


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
