"""OpenWebUI environment file writer.

write_openwebui_env() produces /etc/hal0/openwebui.env with the variables
required to prewire OpenWebUI to the hal0 API.  Called by the installer
(`python -m hal0.openwebui.env_writer`, via :func:`main`) with no
overrides, and by hal0.components.openwebui_arm's converge/reconcile
functions with the RAG/image-gen/web-search overrides from
hal0.openwebui.wiring (see the "Dynamic wiring" section below). There is
still no settings route that calls this directly.

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
page; hal0's own optional key auth (KB-1) gates the hal0 API only and does
not extend to this companion, so auth here is the perimeter's job — and
`hal0-openwebui.service`
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

Dynamic wiring (RAG / image-gen / web-search). Beyond the fixed defaults
above, three more blocks are rendered — but only when there is a real
backend to point at, never a claim OpenWebUI can't cash:

    RAG_EMBEDDING_ENGINE=openai              — an embed-capable slot is bound
    RAG_OPENAI_API_BASE_URL / _MODEL / _KEY
    ENABLE_IMAGE_GENERATION=True             — a ComfyUI (img) slot is bound
    IMAGE_GENERATION_ENGINE=comfyui
    COMFYUI_BASE_URL / _MODEL / COMFYUI_WORKFLOW / _WORKFLOW_NODES
    ENABLE_WEB_SEARCH=True                   — a search provider is installed
    WEB_SEARCH_ENGINE / SEARXNG_QUERY_URL / …

:func:`dynamic_env_overrides` renders these as a pure function of already-
resolved values (model ids, a baked ComfyUI workflow) — it never reads slot
state itself, so it stays as import-light as the rest of this module. The
live-truth resolver that gathers those values from ``capabilities.toml`` and
the registry is :mod:`hal0.openwebui.wiring` (a separate module precisely so
*that* import weight — ``hal0.capabilities``, ``hal0.registry``,
``hal0.providers.comfyui_workflows`` — never lands on this module's cold
``python -m`` path).

Ownership (#2256). hal0 owns a dynamic key only while it has something to
say about it. Every dynamic key is explicitly nulled when its gate is false,
not merely omitted — but ``None`` means "hal0 has no claim here", not "delete
this line". ``preserve_existing`` keeps whatever a prior write left in the
file for any key an override doesn't mention, so a capability that WAS wired
and got unwired (slot deleted, capability disabled) needs the ``None`` to
actually disappear on the next render — an omitted key would survive as a
stale claim (ODS's own Apple footgun — a VRAM fallback that "assumes zero
current usage" and can over-report what fits,
``ods/extensions/services/dashboard-api/routers/features.py:27-32`` in the ODS
reference tree — never claim a capability that isn't there). But a ``None``
removes only a key hal0 itself wrote: every claim hal0 renders is recorded on
a ``# hal0-managed: KEY,KEY`` line in the file header, and a line the operator
wrote by hand — say a self-hosted SearXNG's ``WEB_SEARCH_ENGINE`` while no
search provider ships — is not on that list, so the same ``None`` leaves it
alone. The converse holds too: while hal0 does have a claim (an embed slot is
bound) it overwrites a hand edit to those keys, and the header says so.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
from collections.abc import Iterable
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


#: Opening lines of the openwebui.env header. The generic slot header
#: ``write_env_atomic`` defaults to says edits "will be overwritten on next
#: slot load" — wrong on both counts for this file since #1514. The full
#: header, including the managed-keys exception and the ``# hal0-managed:``
#: marker, is built by :func:`_env_header`.
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


#: hal0's own ``/v1`` as seen from inside the OpenWebUI container.
_HAL0_V1_URL = "http://host.docker.internal:8080/v1"

#: Placeholder key OpenWebUI sends when the box has no client key. hal0 with
#: auth off ignores it; with auth on it is refused, which is why a real key
#: is wired in below whenever one exists.
_PLACEHOLDER_KEY = "sk-hal0-local"

#: Each key OpenWebUI sends to an OpenAI-compatible base URL, paired with the
#: base-URL variable it belongs to. The box client key is written only while
#: that base URL still points at hal0, so an operator who re-pointed STT, TTS
#: or chat at another service never has hal0's key sent there.
_CLIENT_KEY_TARGETS: tuple[tuple[str, str], ...] = (
    ("OPENAI_API_KEYS", "OPENAI_API_BASE_URLS"),
    ("AUDIO_STT_OPENAI_API_KEY", "AUDIO_STT_OPENAI_API_BASE_URL"),
    ("AUDIO_TTS_OPENAI_API_KEY", "AUDIO_TTS_OPENAI_API_BASE_URL"),
    ("RAG_OPENAI_API_KEY", "RAG_OPENAI_API_BASE_URL"),
)


def _client_key(target: Path) -> str | None:
    """The box client key OpenWebUI should present to hal0's ``/v1``.

    ``HAL0_CLIENT_KEY`` from the environment (the running hal0-api, whose
    rotation updates it live), else from the ``api.env`` beside *target*,
    parsed inline for the same cold-import reason as :func:`_default_path`.
    ``None`` when the box has none: OpenWebUI then keeps sending the
    placeholder, which works while auth is off.
    """
    value = os.environ.get("HAL0_CLIENT_KEY", "").strip()
    if value:
        return value
    api_env = target.with_name("api.env")
    try:
        text = api_env.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        key, sep, val = raw.strip().partition("=")
        if sep and key.strip() == "HAL0_CLIENT_KEY":
            val = val.strip().strip('"').strip("'")
            return val or None
    return None


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


#: Every env key any dynamic block can emit. Used to explicitly null out a
#: block's keys when its gate is false — see the module docstring's
#: "Dynamic wiring" section for why an explicit None (not omission) is
#: required for a capability to actually stop being claimed.
_DYNAMIC_ENV_KEYS: tuple[str, ...] = (
    "RAG_EMBEDDING_ENGINE",
    "RAG_OPENAI_API_BASE_URL",
    "RAG_OPENAI_API_KEY",
    "RAG_EMBEDDING_MODEL",
    "ENABLE_IMAGE_GENERATION",
    "IMAGE_GENERATION_ENGINE",
    "COMFYUI_BASE_URL",
    "IMAGE_SIZE",
    "IMAGE_GENERATION_MODEL",
    "COMFYUI_WORKFLOW",
    "COMFYUI_WORKFLOW_NODES",
    "ENABLE_WEB_SEARCH",
    "ENABLE_SEARCH_QUERY_GENERATION",
    "WEB_SEARCH_ENGINE",
    "SEARXNG_QUERY_URL",
    "WEB_SEARCH_RESULT_COUNT",
)

#: Prefix of the header line recording which keys hal0 itself wrote, as
#: ``# hal0-managed: KEY,KEY``. It is the only thing that lets a later render
#: tell a stale hal0 claim (remove it) from a value the operator set by hand
#: (leave it) — see :func:`write_openwebui_env`.
_MANAGED_MARKER = "# hal0-managed:"


def _env_header(managed: Iterable[str]) -> tuple[str, ...]:
    """The full header: the preserve promise, its one exception, and the
    machine-readable ``# hal0-managed:`` line for *managed* keys."""
    keys = textwrap.wrap(
        ", ".join((*_DYNAMIC_ENV_KEYS, *(k for k, _ in _CLIENT_KEY_TARGETS[:3]))),
        width=74,
        initial_indent="#   ",
        subsequent_indent="#   ",
    )
    return (
        *_ENV_HEADER,
        "#",
        "# One exception: hal0 wires these keys from live state (an embed slot,",
        "# an img slot, a search provider, the box client key):",
        *keys,
        "# While hal0 has such a backend to point at, it writes them and replaces a",
        "# hand edit. When the backend goes away it removes only the keys it wrote",
        "# itself, listed on the next line; a value you set by hand while hal0 has",
        "# nothing to claim is left alone.",
        f"{_MANAGED_MARKER} {','.join(sorted(managed))}".rstrip(),
    )


def _read_managed_keys(target: Path) -> set[str]:
    """Keys the previous render recorded on its ``# hal0-managed:`` line;
    empty if the file or the line is absent (a file hal0 never claimed
    anything in — every value in it is the operator's)."""
    try:
        text = target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return set()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(_MANAGED_MARKER):
            names = line[len(_MANAGED_MARKER) :].split(",")
            return {name.strip() for name in names if name.strip()}
    return set()


def _rag_env_block(embed_model_id: str) -> dict[str, str]:
    """RAG embeddings routed through hal0's own ``/v1`` (not OWUI's default
    sentence-transformers download, which can block a fresh install's first
    boot before port 8080 even binds — same rationale as ODS's own
    ``RAG_EMBEDDING_ENGINE`` comment)."""
    return {
        "RAG_EMBEDDING_ENGINE": "openai",
        "RAG_OPENAI_API_BASE_URL": "http://host.docker.internal:8080/v1",
        "RAG_OPENAI_API_KEY": "sk-hal0-local",
        "RAG_EMBEDDING_MODEL": embed_model_id,
    }


def _image_gen_env_block(
    model_id: str, workflow_json: str, workflow_nodes_json: str
) -> dict[str, str]:
    """Image generation routed through the ComfyUI slot on the host.

    ComfyUI runs ``network_mode="host"`` (see
    ``hal0.providers.comfyui.ComfyUIProvider.container_spec``), so from
    inside the OpenWebUI container it is reached the same way hal0's own
    ``/v1`` is: via the host gateway, not a compose-network service name.
    ``workflow_json`` / ``workflow_nodes_json`` are pre-rendered by the
    caller (see :mod:`hal0.openwebui.wiring`) from the SAME translator
    hal0's own ``/v1/images/generations`` route uses, so the baked default
    never drifts from what the slot actually runs.
    """
    return {
        "ENABLE_IMAGE_GENERATION": "True",
        "IMAGE_GENERATION_ENGINE": "comfyui",
        "COMFYUI_BASE_URL": "http://host.docker.internal:8188",
        "IMAGE_SIZE": "1024x1024",
        "IMAGE_GENERATION_MODEL": model_id,
        "COMFYUI_WORKFLOW": workflow_json,
        "COMFYUI_WORKFLOW_NODES": workflow_nodes_json,
    }


def _web_search_env_block(provider: dict[str, str]) -> dict[str, str]:
    """Web search routed through an installed provider.

    ``provider`` carries ``{"engine": ..., "query_url": ...}`` — resolved by
    the caller's registry lookup, never a literal here (no search provider
    ships with hal0 today; this block only ever fires once an extension
    registers one — see :mod:`hal0.openwebui.wiring`'s seam).
    """
    return {
        "ENABLE_WEB_SEARCH": "True",
        "ENABLE_SEARCH_QUERY_GENERATION": "True",
        "WEB_SEARCH_ENGINE": provider["engine"],
        "SEARXNG_QUERY_URL": provider["query_url"],
        "WEB_SEARCH_RESULT_COUNT": "5",
    }


def dynamic_env_overrides(
    *,
    embed_model_id: str | None,
    image_model_id: str | None,
    image_workflow_json: str | None,
    image_workflow_nodes_json: str | None,
    search_provider: dict[str, str] | None,
) -> dict[str, str | None]:
    """Render the RAG / image-gen / web-search blocks as a
    :func:`write_openwebui_env` ``overrides`` dict.

    Pure function of already-resolved values — no I/O, no slot/registry
    reads (that's :func:`hal0.openwebui.wiring.resolve_dynamic_env_overrides`).
    Every key in :data:`_DYNAMIC_ENV_KEYS` is present in the result: ``None``
    for any block whose gate is false, so a converge re-render always
    deletes a capability's keys the moment it stops being true, rather than
    leaving them to survive as a stale claim under ``preserve_existing``.

    ``image_model_id`` requires both workflow strings — a caller that
    resolved a model id but failed to build its workflow (see the
    ``WorkflowTemplateError`` catch in :mod:`hal0.openwebui.wiring`) must
    pass ``None`` for all three rather than a half-built block.
    """
    merged: dict[str, str | None] = dict.fromkeys(_DYNAMIC_ENV_KEYS, None)
    if embed_model_id:
        merged.update(_rag_env_block(embed_model_id))
    if image_model_id and image_workflow_json and image_workflow_nodes_json:
        merged.update(
            _image_gen_env_block(image_model_id, image_workflow_json, image_workflow_nodes_json)
        )
    if search_provider:
        merged.update(_web_search_env_block(search_provider))
    return merged


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
    overrides: dict[str, str | None] | None = None,
    preserve_existing: bool = False,
) -> Path:
    """Write the OpenWebUI environment file atomically.

    Args:
        path:      Destination path.  Defaults to the
                   ``HAL0_HOME``-aware ``/etc/hal0/openwebui.env``.
        overrides: Optional per-key overrides merged on top of the defaults.
                   Useful for non-standard hal0 API ports or custom
                   ``WEBUI_NAME``.  A ``None`` value means "hal0 has no
                   claim on this key": it deletes the shipped default, and
                   with *preserve_existing* it also deletes a line hal0
                   itself wrote earlier (recorded on the ``# hal0-managed:``
                   header line) — but never a line the operator wrote.
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
                   absent one. A ``None`` override states no intent about an
                   operator's own value, so it does not displace it (#2256).

    Returns:
        The path that was written, for the caller to log / verify.

    Raises:
        OSError:   If the file cannot be written (disk full, permission
                   denied, parent directory missing and uncreatable).
        TypeError: If an override value is not a string.
    """
    target: Path = Path(path) if path is not None else _default_path()

    env_vars = default_openwebui_env()
    existing: dict[str, str] = {}
    managed: set[str] = set()
    if preserve_existing:
        existing = _read_existing_env(target)
        managed = _read_managed_keys(target)
        env_vars.update(existing)
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                env_vars[key] = value
                managed.add(key)
            elif key in managed or key not in existing:
                # No claim any more: drop what hal0 wrote, or a shipped
                # default that was never in the file at all.
                env_vars.pop(key, None)
                managed.discard(key)
            # else: the operator's own line — hal0 has nothing to say about
            # it, so it stays (#2256).

    _apply_client_key(env_vars, managed, _client_key(target))

    # Carry the record forward across a render that doesn't mention a key (the
    # installer's override-less pass), but never list a key that isn't there.
    managed &= env_vars.keys()
    write_env_atomic(target, env_vars, header=_env_header(managed))
    return target


def _apply_client_key(env_vars: dict[str, str], managed: set[str], client_key: str | None) -> None:
    """Point every hal0-bound OpenWebUI key at the box client key (in place).

    With a client key, each ``_CLIENT_KEY_TARGETS`` pair whose base URL is
    still hal0's ``/v1`` gets the key and is recorded as hal0-managed, so
    OpenWebUI keeps working once auth is enabled. Without one (or once it is
    gone), a value hal0 wrote earlier falls back to the placeholder, and
    ``OPENAI_API_KEYS``, which ships no default, is dropped. A key whose base
    URL points elsewhere, or that the operator set by hand, is never touched.
    """
    for key_var, url_var in _CLIENT_KEY_TARGETS:
        points_at_hal0 = env_vars.get(url_var, "").rstrip("/") == _HAL0_V1_URL
        if client_key and points_at_hal0:
            env_vars[key_var] = client_key
            managed.add(key_var)
        elif key_var in managed and env_vars.get(key_var, _PLACEHOLDER_KEY) != _PLACEHOLDER_KEY:
            # A key hal0 wrote earlier is still there but no longer wanted.
            if key_var == "OPENAI_API_KEYS" or not points_at_hal0:
                env_vars.pop(key_var, None)
                managed.discard(key_var)
            else:
                env_vars[key_var] = _PLACEHOLDER_KEY
                # The RAG block owns its key's lifecycle; the audio keys are
                # plain shipped defaults again.
                if key_var != "RAG_OPENAI_API_KEY":
                    managed.discard(key_var)


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
