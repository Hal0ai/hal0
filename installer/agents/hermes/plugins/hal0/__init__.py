"""hal0 model-provider profile (Hermes plugin, issue #241).

Hardcodes ``base_url`` at the local hal0 daemon's OpenAI-compatible
surface and emits a vendor ``User-Agent`` so upstream telemetry +
``hermes doctor`` can tell hal0-routed traffic apart from generic
custom-profile traffic.

This file is **copied** into ``$HERMES_HOME/plugins/model-providers/hal0/``
at bootstrap install time by ``_phase_install`` (see
``src/hal0/agents/hermes_provision.py`` and #240). It is NOT imported
by hal0 itself — the imports below resolve against the upstream
``hermes-agent`` venv where the plugin runs, NOT the hal0 wheel.

Extension reserved for #245 / v0.4: Lemonade keep-alive injection
(per ``hal0_lemonade_gotchas``) can hook ``prepare_messages`` so the
serialised-loading evict-all behaviour doesn't drop the slot mid-turn.
"""

from __future__ import annotations

from providers.base import ProviderProfile  # type: ignore[import-not-found]

# Imports resolve in the hermes-agent venv. They will fail at
# bootstrap-copy-time if Hermes isn't installed, but that's fine —
# `_phase_install` copies the file verbatim and Hermes loads it lazily
# when the user picks `provider: hal0`.
from providers import register_provider  # type: ignore[import-not-found]


class Hal0Profile(ProviderProfile):
    """hal0 LAN-inference provider profile.

    Subclass exists so future overrides (model-list filtering,
    request-shape tweaks for Lemonade) have a class hook to land on.
    """


hal0 = Hal0Profile(
    name="hal0",
    aliases=("hal0-local",),
    display_name="hal0 (local)",
    description="hal0 Lemonade-backed slots on the LAN",
    signup_url="https://hal0.dev",
    env_vars=(),  # No outbound API key — hal0 daemon trusts LAN clients.
    base_url="http://127.0.0.1:8000/api/v1",
    default_aux_model="",
    default_headers={"User-Agent": "hermes-on-hal0/1.0"},
)
register_provider(hal0)
