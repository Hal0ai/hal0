"""RATIFIED 2026-07-18 (deliverable 1) — assert the SHIPPED hal0-agent@ unit
carries the ratified security hardening posture.

This is a CI guardrail on the unit TEXT the installer copies verbatim
(installer/systemd/hal0-agent@.service): a regression that weakens the sandbox,
widens ReadWritePaths, or exposes a hal0 secret path fails loudly here rather
than on a live box. Parses directives as text (no systemd-analyze) because the
template uses %i and CI runs on non-systemd platforms.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_UNIT = _REPO_ROOT / "installer" / "systemd" / "hal0-agent@.service"

# The exact ReadWritePaths the agent legitimately needs — nothing more. A
# widening (esp. to a secrets tree) must update this set deliberately.
_ALLOWED_RW_PATHS = {"/etc/hal0", "/var/lib/hal0", "/var/log/hal0", "/run/hal0"}

# Paths that MUST NEVER appear (even nested) in ReadWritePaths — hal0's own
# secret stores. The service runs User=hal0; secrets stay root:root 0600 and are
# never made service-writable.
_FORBIDDEN_RW_SUBSTRINGS = ("/secrets", "/etc/hal0/secrets", "/var/lib/hal0/secrets")


@pytest.fixture(scope="module")
def unit_text() -> str:
    assert _UNIT.exists(), f"missing shipped agent unit at {_UNIT}"
    return _UNIT.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "directive",
    [
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "RestrictSUIDSGID=yes",
    ],
)
def test_shipped_unit_asserts_hardening_directives(unit_text: str, directive: str) -> None:
    assert directive in unit_text, f"shipped hal0-agent@ unit lost hardening: {directive}"


def test_readwritepaths_is_minimal_and_exact(unit_text: str) -> None:
    m = re.search(r"^ReadWritePaths=(.+)$", unit_text, re.MULTILINE)
    assert m, "ReadWritePaths missing — a ProtectSystem=strict agent can't write state"
    got = set(m.group(1).split())
    assert got == _ALLOWED_RW_PATHS, (
        f"ReadWritePaths drifted from the minimal set: got {sorted(got)}, "
        f"want {sorted(_ALLOWED_RW_PATHS)}"
    )


def test_readwritepaths_exposes_no_secret_path(unit_text: str) -> None:
    m = re.search(r"^ReadWritePaths=(.+)$", unit_text, re.MULTILINE)
    assert m
    rw = m.group(1)
    for forbidden in _FORBIDDEN_RW_SUBSTRINGS:
        assert forbidden not in rw, (
            f"ReadWritePaths exposes a hal0 secret path ({forbidden}); "
            "secrets must stay root:root 0600 and never be service-writable"
        )


def test_unit_runs_as_hal0_not_root(unit_text: str) -> None:
    # The whole hardening story rests on User=hal0 (an unprivileged service).
    assert "User=hal0" in unit_text
    assert "Group=hal0" in unit_text
