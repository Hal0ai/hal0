"""Unit tests for hal0.openwebui.env_writer.

Verifies the prewired environment file is written atomically with the
exact key set documented in PLAN.md §8.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.openwebui.env_writer import (
    default_openwebui_env,
    write_openwebui_env,
)


def _parse_env(text: str) -> dict[str, str]:
    """Parse a hal0 env file into a dict (ignoring comments / blanks)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        # Strip systemd-style outer double-quotes if present.
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out[key] = value
    return out


def test_write_openwebui_env_writes_all_prewired_keys(tmp_path: Path) -> None:
    """All seven prewired vars from PLAN.md §8 are present in the file."""
    target = tmp_path / "openwebui.env"
    written = write_openwebui_env(target)
    assert written == target
    assert target.exists()

    env = _parse_env(target.read_text())
    # OpenWebUI runs inside a Docker container — point it at the host
    # gateway, not the container's own loopback.
    assert env["OPENAI_API_BASE_URLS"] == "http://host.docker.internal:8080/v1"
    assert env["WEBUI_AUTH"] == "False"
    assert env["WEBUI_NAME"] == "hal0"
    assert env["ENABLE_OPENAI_API"] == "True"
    assert env["ENABLE_OLLAMA_API"] == "False"
    assert env["DATA_DIR"] == "/app/backend/data"
    assert env["DEFAULT_LOCALE"] == "en"


def test_write_openwebui_env_defaults_to_paths_resolver(
    tmp_hal0_home: str,
) -> None:
    """With no explicit path, write_openwebui_env honours HAL0_HOME."""
    written = write_openwebui_env()
    assert written == Path(tmp_hal0_home) / "etc" / "hal0" / "openwebui.env"
    assert written.exists()


def test_overrides_replace_defaults(tmp_path: Path) -> None:
    """An override key replaces the default value for that key only."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(
        target,
        overrides={
            "OPENAI_API_BASE_URLS": "http://10.0.0.5:8090/v1",
            "WEBUI_NAME": "halo-fork",
        },
    )
    env = _parse_env(target.read_text())
    assert env["OPENAI_API_BASE_URLS"] == "http://10.0.0.5:8090/v1"
    assert env["WEBUI_NAME"] == "halo-fork"
    # Untouched defaults remain.
    assert env["WEBUI_AUTH"] == "False"


def test_override_none_deletes_default(tmp_path: Path) -> None:
    """Setting an override to None removes the key from the output."""
    target = tmp_path / "openwebui.env"
    # type-ignore: we deliberately exercise the None branch documented in
    # the docstring.
    write_openwebui_env(target, overrides={"WEBUI_AUTH": None})  # type: ignore[dict-item]
    env = _parse_env(target.read_text())
    assert "WEBUI_AUTH" not in env
    # Other defaults still present.
    assert env["WEBUI_NAME"] == "hal0"


def test_override_non_string_raises(tmp_path: Path) -> None:
    """Non-string override values raise TypeError (via write_env_atomic)."""
    target = tmp_path / "openwebui.env"
    with pytest.raises(TypeError):
        write_openwebui_env(target, overrides={"WEBUI_AUTH": 0})  # type: ignore[dict-item]


def test_default_openwebui_env_returns_fresh_copy() -> None:
    """Mutating one returned dict must not affect subsequent calls."""
    a = default_openwebui_env()
    a["WEBUI_NAME"] = "mutated"
    b = default_openwebui_env()
    assert b["WEBUI_NAME"] == "hal0"


def test_atomic_write_no_orphan_tmp(tmp_path: Path) -> None:
    """After a successful write, no .hal0-env-*.tmp files remain."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target)
    leftover = list(tmp_path.glob(".hal0-env-*"))
    assert leftover == [], f"unexpected tmp file leftovers: {leftover}"


# ── Auth-aware defaults (Team J / v0.2 auth POC) ─────────────────────────────


def test_webui_auth_is_always_false_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenWebUI prewires with WEBUI_AUTH=False — no login screen, no
    trusted-email header. Auth is upstream's job (see ADR-0012)."""
    # Auth env vars no longer exist; ensure their absence doesn't matter.
    monkeypatch.delenv("HAL0_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target)
    env = _parse_env(target.read_text())
    assert env["WEBUI_AUTH"] == "False"
    assert "WEBUI_AUTH_TRUSTED_EMAIL_HEADER" not in env


def test_trusted_email_header_via_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operators fronting hal0 with a reverse proxy that injects a
    trusted email header opt in via the `overrides` parameter — there
    is no env-var auto-detect anymore."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(
        target,
        overrides={
            "WEBUI_AUTH": "True",
            "WEBUI_AUTH_TRUSTED_EMAIL_HEADER": "X-Forwarded-Email",
        },
    )
    env = _parse_env(target.read_text())
    assert env["WEBUI_AUTH"] == "True"
    assert env["WEBUI_AUTH_TRUSTED_EMAIL_HEADER"] == "X-Forwarded-Email"
