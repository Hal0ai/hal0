"""Agent-driven OAuth passthrough for hal0-bundled skills.

Ports ODS's OAuth passthrough (`extensions/services/dashboard-api/routers/
oauth_passthrough.py`, Osmantic/ODS, Apache-2.0; permission to copy granted
by its author) so a Hermes skill that needs OAuth (Google Calendar,
Spotify, ...) can be connected from the dashboard or the CLI without the
operator ever copy-pasting an authorization code.

Modules:
  providers — the `/etc/hal0/oauth-providers.toml` registry
  pkce      — RFC 7636 verifier/challenge generation
  state     — single-use state-nonce store (CSRF/replay defense)
  store     — token + client-secret persistence through the secrets store
"""

from __future__ import annotations
