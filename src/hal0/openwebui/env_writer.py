"""OpenWebUI environment file writer.

write_openwebui_env() produces /etc/hal0/openwebui.env with the variables
required to prewire OpenWebUI to the hal0 API.  Called by the installer
(`python -m hal0.openwebui.env_writer`, via :func:`main`); there is no
settings route for it today.

Uses hal0.config.env.write_env_atomic() — the same atomic write primitive
used for slot env files (PLAN.md §5 Tier 1).

Prewired variables (PLAN.md §8):
    OPENAI_API_BASE_URLS=http://127.0.0.1:8080/v1
    WEBUI_AUTH=False
    HAL0_OWUI_BIND_HOST=0.0.0.0     (consumed by the systemd unit, not OWUI)
    WEBUI_NAME=hal0
    ENABLE_OPENAI_API=True
    ENABLE_OLLAMA_API=False
    ENABLE_PERSISTENT_CONFIG=False
    DATA_DIR=/app/backend/data
    DEFAULT_LOCALE=en

Voice / Call-mode variables:
    AUDIO_STT_ENGINE=openai
    AUDIO_STT_OPENAI_API_BASE_URL=http://host.docker.internal:8080/v1
    AUDIO_STT_OPENAI_API_KEY=sk-hal0-local
    AUDIO_STT_MODEL=whisper-v3:turbo
    AUDIO_TTS_ENGINE=openai
    AUDIO_TTS_OPENAI_API_BASE_URL=http://host.docker.internal:8080/v1
    AUDIO_TTS_OPENAI_API_KEY=sk-hal0-local
    AUDIO_TTS_MODEL=kokoro-v1
    AUDIO_TTS_VOICE=af_heart

Exposure (#1515). OpenWebUI runs in its open-by-default posture — no login
page, auth is upstream's job (ADR-0012) — and `hal0-openwebui.service`
publishes it on port 3001. Two knobs, both read from the environment here
and both threaded through `installer/install.sh`, so the posture is
reachable without editing code:

    HAL0_BIND_HOST                  the box's one bind choice; rendered into
                                    HAL0_OWUI_BIND_HOST, which the unit
                                    expands into `podman run -p`. Setting it
                                    to 127.0.0.1 now takes the chat UI off
                                    the LAN too, not just the API.
    HAL0_OWUI_TRUSTED_EMAIL_HEADER  name of the header an upstream reverse
                                    proxy injects; setting it turns
                                    WEBUI_AUTH on and wires
                                    WEBUI_AUTH_TRUSTED_EMAIL_HEADER to it.

Both also survive a re-run: since #1514 the installer path merges rather
than replaces, so editing /etc/hal0/openwebui.env by hand is a supported
way to set them. The previous instruction here — "pass them via the
`overrides` parameter" — named a parameter no shipped caller ever passed.
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
    # Voice / Call mode — point Open WebUI's STT+TTS at hal0's own /v1 audio
    # endpoints (Call mode in the browser does mic capture + playback; hal0
    # provides the engines). API key is a placeholder — hal0 ignores auth.
    "AUDIO_STT_ENGINE": "openai",
    "AUDIO_STT_MODEL": "whisper-v3:turbo",
    "AUDIO_STT_OPENAI_API_BASE_URL": "http://host.docker.internal:8080/v1",
    "AUDIO_STT_OPENAI_API_KEY": "sk-hal0-local",
    "AUDIO_TTS_ENGINE": "openai",
    "AUDIO_TTS_MODEL": "kokoro-v1",
    "AUDIO_TTS_OPENAI_API_BASE_URL": "http://host.docker.internal:8080/v1",
    "AUDIO_TTS_OPENAI_API_KEY": "sk-hal0-local",
    "AUDIO_TTS_VOICE": "af_heart",
    "DATA_DIR": "/app/backend/data",
    "DEFAULT_LOCALE": "en",
    "ENABLE_OLLAMA_API": "False",
    "ENABLE_OPENAI_API": "True",
    # Disable OWUI's PersistentConfig so env vars always win.  Without this,
    # OWUI pins values like OPENAI_API_BASE_URLS in its DB on first boot and
    # ignores env on subsequent boots — so a stale DB entry (e.g. the container
    # loopback 127.0.0.1:8080) silently overrides the correct env value.
    "ENABLE_PERSISTENT_CONFIG": "False",
    "OPENAI_API_BASE_URLS": "http://host.docker.internal:8080/v1",
    "WEBUI_AUTH": "False",
    "WEBUI_NAME": "hal0",
}


#: Written at the top of openwebui.env. The generic slot header
#: ``write_env_atomic`` defaults to says edits "will be overwritten on next
#: slot load" — wrong on both counts for this file since #1514.
_ENV_HEADER: tuple[str, str, str] = (
    "# hal0 OpenWebUI environment — written by hal0.openwebui.env_writer",
    "# Hand edits are PRESERVED across install/upgrade runs; hal0 only adds",
    "# keys it ships that are missing. Delete a line to get its default back.",
)


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


#: Mirror of :data:`hal0.install.network.DEFAULT_BIND_HOST`. Duplicated rather
#: than imported for the same reason ``write_env_atomic`` is loaded by path:
#: this module must stay importable from a cold ``python -m`` with no hal0
#: package init. ``tests/security/test_owui_exposure.py`` asserts the two agree.
DEFAULT_BIND_HOST = "0.0.0.0"

#: Env var naming the header an upstream reverse proxy injects. Set it and the
#: prewire turns OpenWebUI's auth on and points it at that header (#1515).
TRUSTED_EMAIL_HEADER_ENV = "HAL0_OWUI_TRUSTED_EMAIL_HEADER"

#: Key the systemd unit expands into ``podman run -p <bind>:3001:8080``. It is
#: written into openwebui.env (which holds no secrets) rather than read from
#: api.env, so the unit never has to source the file carrying provider tokens.
BIND_HOST_KEY = "HAL0_OWUI_BIND_HOST"


def _resolved_bind_host() -> str:
    """The box's one bind choice, or the shared default.

    ``hal0.install.network`` states the rule this restores: *one*
    ``HAL0_BIND_HOST`` drives every listening surface. Before #1515 the
    OpenWebUI unit hardcoded ``0.0.0.0``, so an operator who bound the API to
    loopback still published an unauthenticated chat UI on the LAN. A blank
    value falls back rather than expanding to ``-p :3001:8080``.
    """
    return os.environ.get("HAL0_BIND_HOST", "").strip() or DEFAULT_BIND_HOST


def _trusted_email_header() -> str:
    return os.environ.get(TRUSTED_EMAIL_HEADER_ENV, "").strip()


def default_openwebui_env() -> dict[str, str]:
    """Return a fresh copy of the prewired defaults.

    Returns a new dict each call so callers can mutate freely without
    leaking state back into the module-level table.

    Two entries are resolved from the environment rather than fixed (#1515):

    * ``HAL0_OWUI_BIND_HOST`` follows ``HAL0_BIND_HOST`` — see
      :func:`_resolved_bind_host`.
    * Setting ``HAL0_OWUI_TRUSTED_EMAIL_HEADER`` flips ``WEBUI_AUTH`` to
      ``True`` and wires ``WEBUI_AUTH_TRUSTED_EMAIL_HEADER`` to it. Naming the
      header IS the opt-in: auth on with no header is a login page with no
      identity source behind it, and a header with auth off is ignored, so the
      two are never settable independently.

    Default posture is unchanged — ``WEBUI_AUTH=False`` and a wildcard bind —
    because #1515 is "stop ignoring the operator's choice", not a silent flip
    that would strand every existing LAN user on upgrade.
    """
    env = dict(_DEFAULT_OPENWEBUI_ENV)
    env[BIND_HOST_KEY] = _resolved_bind_host()
    header = _trusted_email_header()
    if header:
        env["WEBUI_AUTH"] = "True"
        env["WEBUI_AUTH_TRUSTED_EMAIL_HEADER"] = header
    return env


def _read_existing_env(target: Path) -> dict[str, str]:
    """Parse an existing env file into ``{key: value}``; ``{}`` if absent.

    Deliberately permissive — this reads an operator-edited file, so a line
    it cannot parse is skipped rather than raising. The quoting matches
    :func:`hal0.config.env._quote_value`, whose output this round-trips.
    """
    try:
        text = target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return {}
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out[key] = value
    return out


def write_openwebui_env(
    path: Path | str | None = None,
    overrides: dict[str, str] | None = None,
    preserve_existing: bool = False,
) -> Path:
    """Write the OpenWebUI environment file atomically.

    Args:
        path:      Destination path.  Defaults to the
                   ``HAL0_HOME``-aware ``/etc/hal0/openwebui.env``.
        overrides: Optional per-key overrides merged on top of the defaults.
                   Useful for non-standard hal0 API ports or custom
                   ``WEBUI_NAME``.  ``None`` values in *overrides* delete
                   the corresponding default key.
        preserve_existing: Merge instead of replace (#1514).  Every key
                   already in the file keeps its value — including keys hal0
                   does not ship — and only genuinely new defaults are added.
                   ``installer/README.md`` promises existing config files are
                   "never clobbered on re-run"; ``hal0.toml`` and
                   ``upstreams.toml`` keep that promise with a ``[[ ! -f ]]``
                   guard, but this file was regenerated every run, erasing any
                   operator edit. Merging rather than skipping the write means
                   a box installed before a key existed still receives it on
                   upgrade — skipping would freeze the file forever and ship a
                   half-configured OpenWebUI with no signal.

                   Precedence, weakest to strongest: shipped default < value
                   already in the file < explicit ``overrides``. An override is
                   the caller stating intent; a preserved value is merely an
                   absent one.

    Returns:
        The path that was written, for the caller to log / verify.

    Raises:
        OSError:   If the file cannot be written (disk full, permission
                   denied, parent directory missing and uncreatable).
        TypeError: If an override value is not a string.
    """
    target: Path = Path(path) if path is not None else _default_path()

    env_vars = default_openwebui_env()
    if preserve_existing:
        env_vars.update(_read_existing_env(target))
    if overrides:
        for key, value in overrides.items():
            if value is None:
                env_vars.pop(key, None)
            else:
                env_vars[key] = value

    write_env_atomic(target, env_vars, header=_ENV_HEADER)
    return target


def main() -> None:
    """CLI entry: ``python -m hal0.openwebui.env_writer``.

    Writes the prewired env file to its default path (honouring
    ``$HAL0_HOME``).  Used by ``installer/install.sh`` so the installer
    doesn't need to know the path layout.

    Merges rather than replaces (#1514): ``install.sh`` runs this on every
    repair and upgrade, and it used to erase whatever the operator had put in
    the file — including the trusted-header pair the exposure fix (#1515)
    tells them to set, which made that instruction self-defeating.
    """
    written = write_openwebui_env(preserve_existing=True)
    print(f"wrote {written}")


if __name__ == "__main__":
    main()
