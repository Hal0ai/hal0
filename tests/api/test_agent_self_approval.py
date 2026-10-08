"""ADR-0024: an agent can approve its own gated tool call today.

Agents are provisioned with ``service_key(prefer="admin")``
(``hal0.agents.hermes_provision``, ``hal0.agents.pi_coder.driver``), i.e.
the box admin key. ``POST /api/agent/approvals/{id}/approve`` is an
ordinary ADMIN route (``hal0.security.exposure``), and the auth layer
grants ADMIN to any Bearer holder of that key. So the credential an agent
holds is sufficient to approve the approval queued for that same agent.

The first test pins the fact. The second asserts the behaviour ADR-0024
item 2 requires ("approve and deny need a person: they refuse a bare
Bearer key of any tier") and is ``xfail(strict=True)``: it fails today by
design, and the strict marker turns into a failure the day the fix lands
so the marker (and this docstring) get removed with it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from hal0 import service_identity
from hal0.api import auth as auth_mod
from hal0.security.exposure import AuthClass, classify

APPROVE_PATH = "/api/agent/approvals/abc123/approve"


@pytest.fixture(autouse=True)
def _isolated_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    monkeypatch.setenv("HAL0_ADMIN_KEY", "box-admin-key")
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    auth_mod._require_auth_cache = None
    yield


def _agent_scope(key: str) -> dict[str, object]:
    return {
        "type": "http",
        "method": "POST",
        "path": APPROVE_PATH,
        "headers": [(b"authorization", f"Bearer {key}".encode())],
        "query_string": b"",
    }


def test_agent_credential_is_the_admin_key_and_approve_is_a_plain_admin_route() -> None:
    """The fact behind ADR-0024: what an agent holds, and what approve checks."""
    agent_key = service_identity.service_key(prefer="admin")
    assert agent_key == "box-admin-key"

    assert classify("POST", APPROVE_PATH) is AuthClass.ADMIN
    assert classify("POST", APPROVE_PATH.replace("/approve", "/deny")) is AuthClass.ADMIN

    principal = auth_mod.resolve_principal(_agent_scope(agent_key))
    assert (principal.tier, principal.source) == ("admin", "bearer")
    allowed, _, _ = auth_mod._decide(AuthClass.ADMIN, principal)
    assert allowed is True  # self-approval: the agent's key clears the route's gate


@pytest.mark.xfail(
    strict=True,
    reason="ADR-0024 item 2 (v1.5): approve/deny must refuse a bare Bearer key of any tier",
)
def test_bearer_key_cannot_approve() -> None:
    agent_key = service_identity.service_key(prefer="admin")
    assert agent_key is not None
    principal = auth_mod.resolve_principal(_agent_scope(agent_key))
    allowed, status, _ = auth_mod._decide(classify("POST", APPROVE_PATH), principal)
    assert allowed is False
    assert status == 403
