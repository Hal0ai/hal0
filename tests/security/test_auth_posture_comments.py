"""The in-source auth-posture commentary must not lie about the auth surface.

The memory REST surface and the MCP mount both carried a comment asserting
that "ADR-0012 removed auth and TLS platform wide" and that hal0 "does not
authenticate" the surface. Both halves of that were wrong by the time anyone
read them:

* :class:`hal0.api.auth.AuthEnforcementMiddleware` exists and is wired into
  ``create_app()``, and :mod:`hal0.security.exposure` classifies
  ``/api/memory`` as ``AuthClass.ADMIN``. There IS an auth surface.
* What is actually true is narrower and more dangerous:
  :func:`hal0.api.auth.require_auth_enabled` returns ``False`` by default
  (operator decision 2026-07-19, finding O19), so on a shipped install that
  middleware is classified-but-inert.

A reader who believes the old comment concludes "there is nothing to enable"
and never turns auth on. A reader who is told only the first half concludes
"memory is ADMIN-gated, we're fine" and also never turns auth on. The comment
has to carry BOTH halves, so this test pins both.

These assertions run against the source text on purpose: the defect was in the
prose, and no behavioural test can catch prose. The paired behavioural facts
are asserted below against the real code so the prose can never drift away
from a truth that has itself changed.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import hal0.api.auth as auth_mod
import hal0.api.mcp_mount as mcp_mount_mod
import hal0.api.routes.memory as memory_routes_mod

# Phrases that asserted hal0 has no auth surface at all. Every one of these is
# false as of the KB-1 auth landing; none may reappear.
_STALE_CLAIMS = (
    "ADR-0012 removed auth and TLS",
    "hal0 does not authenticate it",
    "Auth was removed",
    "auth surface was\n# removed",
    "removed network auth entirely",
    "Bearer auth was removed",
)

_SOURCES = {
    "hal0.api.routes.memory": memory_routes_mod,
    "hal0.api.mcp_mount": mcp_mount_mod,
    "hal0.api.auth": auth_mod,
}


@pytest.mark.parametrize("mod_name", sorted(_SOURCES))
def test_no_module_claims_auth_was_removed(mod_name: str) -> None:
    source = Path(inspect.getfile(_SOURCES[mod_name])).read_text(encoding="utf-8")
    for claim in _STALE_CLAIMS:
        assert claim not in source, (
            f"{mod_name} still claims auth does not exist ({claim!r}). "
            "AuthEnforcementMiddleware is real; what is true is that "
            "require_auth_enabled() defaults to False."
        )


def test_memory_route_comment_states_both_halves() -> None:
    """The memory surface's posture comment must name the middleware AND the
    default-off toggle. Either half alone misleads."""
    source = Path(inspect.getfile(memory_routes_mod)).read_text(encoding="utf-8")
    assert "AuthEnforcementMiddleware" in source, (
        "the posture comment must say the enforcement middleware exists"
    )
    assert "require_auth_enabled" in source, (
        "the posture comment must name the toggle that makes it inert"
    )
    assert "False by default" in source, "the posture comment must state the shipped default is OFF"
    assert "ADMIN" in source, "the posture comment must state /api/memory's exposure class"


def test_mcp_mount_comment_names_the_default_off_toggle() -> None:
    source = Path(inspect.getfile(mcp_mount_mod)).read_text(encoding="utf-8")
    assert "require_auth_enabled" in source, (
        "the MCP mount's auth commentary must name the posture toggle rather "
        "than asserting auth does not exist"
    )


# ── the behavioural facts the prose above is describing ──────────────────────


def test_auth_enforcement_middleware_actually_exists() -> None:
    """Half one: there IS an auth surface."""
    assert hasattr(auth_mod, "AuthEnforcementMiddleware")
    assert "AuthEnforcementMiddleware" in auth_mod.__all__


def test_memory_prefix_is_admin_classed() -> None:
    """Half one, continued: /api/memory is ADMIN in the exposure table."""
    from hal0.security.exposure import AuthClass, classify

    assert classify("GET", "/api/memory/status") is AuthClass.ADMIN
    assert classify("DELETE", "/api/memory/banks/abc") is AuthClass.ADMIN
    assert classify("POST", "/api/memory/delete") is AuthClass.ADMIN


def test_require_auth_defaults_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Half two: and none of that is enforced on a shipped install."""
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    auth_mod._require_auth_cache = None
    assert auth_mod.require_auth_enabled() is False
