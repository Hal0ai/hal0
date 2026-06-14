"""OpenWebUI environment file writer.

write_openwebui_env() produces /etc/hal0/openwebui.env with the variables
required to prewire OpenWebUI to the hal0 API.  Called by the installer
and (on settings changes) by the Settings API route.

Uses hal0.config.env.write_env_atomic() — the same atomic write primitive
used for slot env files (PLAN.md §5 Tier 1).

Prewired variables (PLAN.md §8):
    OPENAI_API_BASE_URLS=http://127.0.0.1:8080/v1
    WEBUI_AUTH=False
    WEBUI_NAME=hal0
    ENABLE_OPENAI_API=True
    ENABLE_OLLAMA_API=False
    DATA_DIR=/app/backend/data
    DEFAULT_LOCALE=en

OpenWebUI runs in its open-by-default posture. Operators fronting hal0
with an upstream reverse proxy that injects a trusted email header
should pass `WEBUI_AUTH=True` + `WEBUI_AUTH_TRUSTED_EMAIL_HEADER=<name>`
via the `overrides` parameter at install / setting time.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _load_write_env_atomic():
    """Load ``hal0.config.env.write_env_atomic`` without triggering
    ``hal0.config.__init__``.

    Importing ``hal0.config`` (any form) runs its ``__init__``, which
    imports ``hal0.config.loader``, which imports
    ``hal0.api.middleware.error_codes`` for the ``Hal0Error`` base —
    pulling in the entire FastAPI app factory.  That graph has a known
    circular import (``routes.hardware`` re-enters ``hal0.config.loader``
    before ``load_hardware_info`` is defined) when triggered from a
    *cold* ``python -m hal0.openwebui.env_writer`` invocation, which is
    exactly how the installer calls us.

    Loading ``env.py`` from its file path side-steps the package init
    entirely.  ``env.py`` has no hal0 imports of its own, so this is
    safe and stays in lock-step with the canonical primitive.
    """
    if "hal0.config.env" in sys.modules:
        return sys.modules["hal0.config.env"].write_env_atomic
    here = Path(__file__).resolve().parent.parent  # …/src/hal0
    env_py = here / "config" / "env.py"
    spec = importlib.util.spec_from_file_location("hal0.config.env", env_py)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise ImportError(f"cannot locate {env_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.write_env_atomic


write_env_atomic = _load_write_env_atomic()

#: Default prewired variables.  Matches PLAN.md §8.
#
# OPENAI_API_BASE_URLS:
#   PLAN.md §8 documents "http://127.0.0.1:8080/v1" but that's the
#   *host's* loopback — and OpenWebUI runs inside a container, where
#   127.0.0.1 is the container itself (serving OpenWebUI on :8080).
#   ``host.docker.internal`` is the conventional name for the host
#   gateway; the unit injects it via
#   ``--add-host=host.docker.internal:host-gateway``. podman (>=4.0)
#   honours the host-gateway magic value just like Docker does on Linux.
_DEFAULT_OPENWEBUI_ENV: dict[str, str] = {
    "DATA_DIR": "/app/backend/data",
    "DEFAULT_LOCALE": "en",
    "ENABLE_OLLAMA_API": "False",
    "ENABLE_OPENAI_API": "True",
    "OPENAI_API_BASE_URLS": "http://host.docker.internal:8080/v1",
    "WEBUI_AUTH": "False",
    "WEBUI_NAME": "hal0",
}


def _default_path() -> Path:
    """Resolve the default openwebui.env path without importing hal0.config.

    Mirrors :func:`hal0.config.paths.openwebui_env` exactly — i.e.
    ``$HAL0_HOME/etc/hal0/openwebui.env`` when ``HAL0_HOME`` is set, else
    ``/etc/hal0/openwebui.env``.  We inline the logic here so the
    installer can call ``python -m hal0.openwebui.env_writer`` without
    triggering hal0.config's package init (and its circular-import
    landmines — see the note at the top of this module).
    """
    home = os.environ.get("HAL0_HOME", "").strip()
    if home:
        return Path(home) / "etc" / "hal0" / "openwebui.env"
    return Path("/etc/hal0/openwebui.env")


def default_openwebui_env() -> dict[str, str]:
    """Return a fresh copy of the prewired defaults.

    Returns a new dict each call so callers can mutate freely without
    leaking state back into the module-level table. OpenWebUI runs in
    its open-by-default posture (no login page, no SSO header) — auth
    is upstream's job; an operator running a reverse proxy with trusted
    headers should set WEBUI_AUTH_TRUSTED_EMAIL_HEADER themselves via
    the overrides parameter.
    """
    return dict(_DEFAULT_OPENWEBUI_ENV)


def write_openwebui_env(
    path: Path | str | None = None,
    overrides: dict[str, str] | None = None,
) -> Path:
    """Write the OpenWebUI environment file atomically.

    Args:
        path:      Destination path.  Defaults to the
                   ``HAL0_HOME``-aware ``/etc/hal0/openwebui.env``.
        overrides: Optional per-key overrides merged on top of the defaults.
                   Useful for non-standard hal0 API ports or custom
                   ``WEBUI_NAME``.  ``None`` values in *overrides* delete
                   the corresponding default key.

    Returns:
        The path that was written, for the caller to log / verify.

    Raises:
        OSError:   If the file cannot be written (disk full, permission
                   denied, parent directory missing and uncreatable).
        TypeError: If an override value is not a string.
    """
    target: Path = Path(path) if path is not None else _default_path()

    env_vars = default_openwebui_env()
    if overrides:
        for key, value in overrides.items():
            if value is None:
                env_vars.pop(key, None)
            else:
                env_vars[key] = value

    write_env_atomic(target, env_vars)
    return target


def main() -> None:
    """CLI entry: ``python -m hal0.openwebui.env_writer``.

    Writes the prewired env file to its default path (honouring
    ``$HAL0_HOME``).  Used by ``installer/install.sh`` so the installer
    doesn't need to know the path layout.
    """
    written = write_openwebui_env()
    print(f"wrote {written}")


if __name__ == "__main__":
    main()
