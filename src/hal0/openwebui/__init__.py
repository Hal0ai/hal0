"""hal0.openwebui — OpenWebUI companion service configuration.

Writes /etc/hal0/openwebui.env with the prewired variables that configure
OpenWebUI to use the hal0 API as its backend. Called by the installer at
install time (``python -m hal0.openwebui.env_writer``, static defaults
only) and by the OpenWebUI component's converge arm
(:func:`hal0.components.openwebui_arm.converge_openwebui`) on every
convergence pass, which additionally renders the RAG/image-gen/web-search
blocks from live capability state via :mod:`hal0.openwebui.wiring`.

Uses the same atomic write primitive as slot env files (hal0.config.env).

New module (no haloai equivalent).
See PLAN.md §8 (OpenWebUI integration) and §5 Tier 1 (atomic writes).

Key exports:
    write_openwebui_env  — write the OpenWebUI env file atomically.
    dynamic_env_overrides — render the RAG/image-gen/web-search blocks
                             (pure; see hal0.openwebui.wiring for the
                             live-truth resolver that feeds it).
"""

from __future__ import annotations

from hal0.openwebui.env_writer import dynamic_env_overrides, write_openwebui_env

__all__ = [
    "dynamic_env_overrides",
    "write_openwebui_env",
]
