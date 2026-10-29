"""Caller: who is asking the agent bridge for a run (ADR-0043 decision 9).

A module of its own so the ``/api/bridge`` routes and the ``hal0-agents`` MCP
server can name the caller without importing :mod:`hal0.bridge.service`, which
pulls in the Hermes client and :mod:`hal0.agents.hermes_api`. hal0's core
(``hal0.api`` and the routes it mounts) must import no Hermes module, so a box
without Hermes still boots (golden path #15); the service is imported only
where the bridge boot phase builds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from hal0.bridge.errors import BridgeError

if TYPE_CHECKING:
    from hal0.api.auth import AuthPrincipal

__all__ = ["Caller"]


@dataclass(frozen=True)
class Caller:
    """Who is asking: a bridge client by name, or the admin key (``name="admin"``)."""

    tier: Literal["bridge", "admin"]
    name: str

    @classmethod
    def from_principal(cls, principal: AuthPrincipal) -> Caller:
        """The caller an authenticated principal acts as; other tiers get 403 ``auth.forbidden``."""
        if principal.tier == "admin":
            return cls("admin", "admin")
        if principal.tier == "bridge" and principal.name:
            return cls("bridge", principal.name)
        raise BridgeError(
            "auth.forbidden", "the agent bridge needs a bridge key or the admin key", status=403
        )
