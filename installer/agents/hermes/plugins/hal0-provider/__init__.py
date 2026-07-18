"""hal0-provider — Hermes ``ProviderProfile`` plugin (canonical, shipped source).

Advertises the local hal0-api as an OpenAI-compatible inference provider
(``api_mode: chat_completions``) with a live ``/v1/models`` inventory and
restart-free role aliases (``hal0/agent`` resolves per-request server-side).
See ``profile.py`` for the field/behaviour rationale.

This directory is the **canonical source**; the installer ships a
byte-identical **seed** at ``installer/agents/hermes/plugins/hal0-provider/``,
copied verbatim into ``$HERMES_HOME/plugins/model-providers/hal0/`` at
provision time by ``hal0.agents.hermes_provision._phase_install``. Parity of
the two copies is locked by ``tests/agents/hermes/plugins/
test_hal0_provider_parity.py``. At runtime the seed resolves against the
Hermes venv, where the real ``providers.base.ProviderProfile`` /
``providers.register_provider`` live; ``profile.py`` falls back to a vendored
frozen copy so it stays importable in hal0's own venv for unit tests.

Registration contract (Hermes pin ``9de9c25f``): provider profiles register
through the **module-level** seam ``providers.register_provider(profile)`` —
the general ``PluginContext`` has no ``register_provider_profile`` method (see
``tests/fixtures/hermes/contracts/{provider_profile,plugin_context}.py``). We
ship every discovery path so any loader variant finds us:

  * Re-export ``Hal0ProviderProfile`` for the "find a subclass" fallback.
  * Expose a ready ``PROFILE`` instance.
  * Provide ``register(ctx)`` — routes to ``ctx.register_provider_profile`` /
    ``ctx.register_provider`` if a context ever grows one, else the
    module-level ``register_provider`` seam.
  * Best-effort module-level ``register_provider(PROFILE)`` at import time
    (no-op in hal0's venv, where the Hermes ``providers`` package is absent).
"""

from __future__ import annotations

import contextlib

from .profile import Hal0ProviderProfile

try:  # pragma: no cover — only resolves inside the Hermes venv
    from providers import register_provider  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — covered by the hal0-venv fallback path
    try:
        from agent.provider_profile import register_provider  # type: ignore[import-not-found]
    except ImportError:
        register_provider = None  # type: ignore[assignment]

__all__ = ["PROFILE", "Hal0ProviderProfile", "register"]

# The singleton profile the loader registers under ``name = "hal0"``.
PROFILE = Hal0ProviderProfile()


def register(ctx: object | None = None) -> None:  # type: ignore[no-untyped-def]
    """Register the hal0 provider profile.

    Prefers a ``PluginContext`` seam if one is passed and exposes a provider
    registrar; otherwise falls back to the frozen module-level
    ``providers.register_provider`` seam. A no-op when neither is available
    (e.g. imported in hal0's venv with no Hermes ``providers`` package).
    """
    if ctx is not None:
        seam = getattr(ctx, "register_provider_profile", None) or getattr(
            ctx, "register_provider", None
        )
        if callable(seam):
            seam(PROFILE)
            return
    if register_provider is not None:
        register_provider(PROFILE)


# Import-time registration for loaders that discover a provider plugin by
# importing its package (the module-level seam path). Guarded + best-effort so
# a registry hiccup disables only this plugin, never the agent.
if register_provider is not None:  # pragma: no cover — Hermes-venv-only path
    # Degrade independently per the adapter contract — a registry hiccup
    # disables only this plugin, never the agent.
    with contextlib.suppress(Exception):
        register_provider(PROFILE)
