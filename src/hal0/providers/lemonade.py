"""LemonadeProvider — v0.2 unified-runtime Provider (PR-8 + PR-10).

In v0.1.x every backend (llama-server, FLM, Moonshine, Kokoro, …) was a
separate Provider class that spawned its own toolbox container under a
systemd template unit. v0.2 (ADR-0008) replaces that with a single
Lemonade Server daemon (``lemond``) that owns process lifecycle for
every modality. ``LemonadeProvider`` is the thin adapter between hal0's
slot abstraction and Lemonade's HTTP control plane.

Where this fits in the abstraction stack:

  - SlotManager keeps its v0.1.x public surface (``load`` / ``unload`` /
    ``swap`` / ``status`` / …). No caller signatures change. After
    PR-10, SlotManager unconditionally dispatches through this class —
    the prior ``HAL0_BACKEND`` env gate retired and the legacy
    docker/systemd code paths inside SlotManager went with it.
  - SlotManager calls :meth:`LemonadeProvider.load` / :meth:`unload`
    instead of writing an override.conf and running ``systemctl
    start``. The active Lemonade daemon (``hal0-lemonade.service``,
    port 13305) owns every child process. There is no per-slot
    container to render.
  - Legacy Provider classes (``LlamaServerProvider``, ``FLMProvider``,
    ``MoonshineProvider``, ``KokoroProvider``, ``ComfyUIProvider``)
    survive in :mod:`hal0.providers` for now — non-SlotManager callers
    (image-gen pipeline in ``api/routes/v1.py``, NPU footprint probes
    in ``api/routes/hardware.py``, FLM catalog reads in
    ``registry/pull.py``) still reference them. Their SlotManager
    dispatch role is dead, but the class surfaces are not yet retired.

ABC compliance notes:

The :class:`hal0.providers.base.Provider` ABC has docker/systemd-shaped
methods (``build_env``, ``start_cmd``, ``container_spec``,
``render_systemd_override``) that have no operational meaning for a
daemon-backed runtime. We implement them either as informational stubs
(``build_env`` returns a slot-identity env block useful for diagnostics
+ audit) or by raising ``NotImplementedError`` with a pointer back to
this docstring. The ``Provider.render_systemd_override`` path is only
reached via :mod:`hal0.slots.unit_template`, which SlotManager skips on
the Lemonade-active branch — so the raising stubs are unreachable in
production code, but the ABC contract still needs them defined.

Inference forward (``/v1/chat/completions``, ``/v1/embeddings``, …) is
NOT this class's responsibility; it goes through the existing
dispatcher unchanged. LemonadeProvider only owns control plane
(``/v1/load``, ``/v1/unload``, ``/v1/health``) — same separation as
``LemonadeClient``.

ADR cross-references:

- ADR-0008 §1 — Lemonade as the v0.2 unified runtime
- ADR-0008 §2 — Lemonade IS the only provider in v0.2
- ADR-0008 §3 — no preload validation (removed in #155)
- ADR-0008 §6 — SlotManager.start becomes Lemonade /v1/load
- docs/internal/lemonade-adoption-plan-2026-05-22.md §4.1 — device →
  recipe/backend mapping
- docs/internal/lemonade-adoption-plan-2026-05-22.md §11 PR-8 — the
  capability-dispatch wiring this class enables
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

from hal0.lemonade.client import LemonadeClient
from hal0.providers.base import ContainerSpec, Provider

log = logging.getLogger(__name__)


# ── device → Lemonade recipe/backend mapping ─────────────────────────────────
#
# Plan §4.1 + ADR-0008 §6 locked the four-way mapping. ``gpu-*`` slots
# load through llama.cpp with an explicit backend flag; ``cpu`` is the
# same recipe with CPU-only inference; ``npu`` uses Lemonade's FLM
# recipe and does not take a llamacpp_backend (FLM is its own backend).
#
# Returned tuple shape: ``(recipe, llamacpp_backend)``. ``recipe=None``
# means "let Lemonade pick its default" (currently the llama.cpp recipe
# for gpu/cpu).  Either value being ``None`` causes
# :meth:`LemonadeClient.load` to omit the key from the request body —
# Lemonade then falls through to its internal sentinel logic.


def device_to_backend(device: str | None) -> tuple[str | None, str | None]:
    """Map hal0's ``device`` enum onto Lemonade's recipe+backend pair.

    Args:
        device: One of ``gpu-rocm`` | ``gpu-vulkan`` | ``cpu`` | ``npu``.
                Empty / unknown values fall back to ``(None, None)`` so
                Lemonade picks its own defaults — same semantics as
                omitting the keys from the load body.

    Returns:
        ``(recipe, llamacpp_backend)``. Either may be ``None`` to mean
        "don't send this key in the /v1/load body". The two are
        mutually exclusive in practice — NPU uses ``recipe="flm"`` with
        no llamacpp_backend; everything else uses ``recipe=None`` with
        a concrete llamacpp_backend.
    """
    if not device:
        return (None, None)
    d = device.strip().lower()
    if d == "gpu-rocm":
        return (None, "rocm")
    if d == "gpu-vulkan":
        return (None, "vulkan")
    if d == "cpu":
        return (None, "cpu")
    if d == "npu":
        # FLM recipe; ``llamacpp_backend`` is meaningless here. Lemonade
        # routes the load to its fastflowlm_server backend.
        return ("flm", None)
    log.warning(
        "lemonade.provider.unknown_device",
        extra={"device": device},
    )
    return (None, None)


# ── actual-backend introspection (B2) ────────────────────────────────────────
#
# DECLARED backend (``device_to_backend``) is the slot's *intent*. ACTUAL
# backend is the build directory of the live ``llama-server`` child that
# lemond spawned for the loaded model. They can diverge when a model is
# loaded outside the normal slot-load path (e.g. name-based lazy-load with
# no explicit ``llamacpp_backend`` in the /v1/load body → lemond's global
# config.json default wins). Surfacing both lets the dashboard render a
# drift warning instead of silently lying about which backend ran.
#
# Resolution path (per ADR-0022 sourceOfTruth):
#   loaded_entry.backend_url → port → child PID listening on that port →
#   /proc/<pid>/exe (fallback /proc/<pid>/cmdline) → classify the path:
#     ``/vulkan/``      → "vulkan"
#     ``/rocm-stable/`` → "rocm"
#     a cpu binary / no GPU marker → "cpu"
# Returns None on ANY failure (lemond down, not loaded, race, unreadable
# /proc). Never raises — this runs on the dashboard hot path.

# Map a substring in the resolved binary path to the actual backend token.
# Order matters: the GPU build dirs are checked before the generic cpu
# fallback so a rocm-stable/llama-server doesn't get mis-tagged.
_BACKEND_PATH_MARKERS: tuple[tuple[str, str], ...] = (
    ("/vulkan/", "vulkan"),
    ("/rocm-stable/", "rocm"),
    # Custom ROCmFP4 fork (charlie12345/rocmfp4-llama) wired via lemond
    # rocm_bin — its install path has no /rocm-stable/ marker, so without
    # this it falls through to the cpu fallback and the dashboard shows a
    # bogus declared≠actual mismatch for rocm slots serving FP4 models.
    ("/rocmfp4-llama/", "rocm"),
)


def _classify_backend_path(path: str) -> str | None:
    """Classify a llama-server binary path into a backend token.

    Returns ``"vulkan"`` / ``"rocm"`` when the path sits under the
    corresponding install build dir, ``"cpu"`` for a recognisable
    llama-server binary with no GPU marker, else ``None``.
    """
    if not path:
        return None
    p = path.lower()
    for marker, backend in _BACKEND_PATH_MARKERS:
        if marker in p:
            return backend
    # No GPU build-dir marker. If this is clearly a llama-server binary we
    # can attribute it to a CPU build; otherwise we don't know.
    if "llama-server" in p or "llama_server" in p:
        return "cpu"
    return None


def _port_from_backend_url(backend_url: str | None) -> int | None:
    """Extract the TCP port from a lemond loaded[] backend_url. None on miss."""
    if not backend_url or not isinstance(backend_url, str):
        return None
    try:
        parsed = urlparse(backend_url if "://" in backend_url else f"http://{backend_url}")
        if parsed.port:
            return int(parsed.port)
    except (ValueError, TypeError):
        return None
    # Fall back to a bare ``host:port`` regex if urlparse didn't find one.
    m = re.search(r":(\d{2,5})(?:/|$)", backend_url)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _pid_listening_on_port(port: int) -> int | None:
    """Find the PID of the process listening on 127.0.0.1:<port>.

    Tries psutil first (fast, no subprocess); falls back to ``ss`` then
    ``lsof``. Returns None when nothing is found or the lookup fails.
    Never raises.
    """
    # Preferred: psutil.net_connections (no subprocess spawn).
    try:
        import psutil  # type: ignore

        for conn in psutil.net_connections(kind="inet"):
            laddr = getattr(conn, "laddr", None)
            if not laddr:
                continue
            lport = getattr(laddr, "port", None)
            status = getattr(conn, "status", "")
            if lport == port and status == psutil.CONN_LISTEN and conn.pid:
                return int(conn.pid)
    except Exception:
        pass

    # Fallback: ``ss -ltnp`` — parse the "pid=NNN" out of the LISTEN row.
    try:
        import subprocess

        out = subprocess.run(
            ["ss", "-ltnp"],
            capture_output=True,
            text=True,
            timeout=2.0,
        ).stdout
        for line in out.splitlines():
            # Match the local-address column ending in :<port> (handles both
            # space- and tab-delimited ss output across versions).
            if not re.search(rf":{port}\b", line):
                continue
            m = re.search(r"pid=(\d+)", line)
            if m:
                return int(m.group(1))
    except Exception:
        pass

    # Last resort: ``lsof -ti tcp:<port> -s TCP:LISTEN``.
    try:
        import subprocess

        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-s", "TCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=2.0,
        ).stdout
        for tok in out.split():
            if tok.strip().isdigit():
                return int(tok.strip())
    except Exception:
        pass
    return None


def _exe_path_for_pid(pid: int) -> str | None:
    """Resolve a PID's executable path via /proc/<pid>/exe, then cmdline."""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        pass
    # Fallback: argv[0] from cmdline (NUL-separated).
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
        if raw:
            argv0 = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            if argv0:
                return argv0
    except OSError:
        pass
    return None


def resolve_actual_backend(loaded_entry: dict[str, Any] | None) -> str | None:
    """Resolve the runtime backend of a loaded model's llama-server child.

    Takes a single ``/v1/health.loaded[]`` entry (shape
    ``{model_name, backend_url, ...}`` — note there is NO ``backend``
    field, per ADR-0022) and introspects the listening child process to
    determine which llama.cpp build is actually serving it.

    Returns one of ``"vulkan"`` / ``"rocm"`` / ``"cpu"``, or ``None`` when
    the backend can't be determined (lemond down, model not loaded, no
    listener on the port, unreadable /proc, or a race where the child has
    just exited). Never raises — this is on the dashboard hot path.
    """
    if not isinstance(loaded_entry, dict):
        return None
    try:
        port = _port_from_backend_url(loaded_entry.get("backend_url"))
        if port is None:
            return None
        pid = _pid_listening_on_port(port)
        if pid is None:
            return None
        exe = _exe_path_for_pid(pid)
        if exe is None:
            return None
        return _classify_backend_path(exe)
    except Exception:
        # Defensive catch-all: introspection must never raise into the
        # status/enrichment hot path.
        return None


# ── slot config helpers (provider-side) ──────────────────────────────────────
#
# Local versions of the dict-or-pydantic coercion that already lives in
# ``hal0.slots.manager`` and ``hal0.slots.unit_template``. Duplicated
# here so this module doesn't pull in slots/manager.py (which would
# create a circular import — manager.py is going to import this class).


def _to_dict(slot_cfg: Any) -> dict[str, Any]:
    if hasattr(slot_cfg, "model_dump"):
        return slot_cfg.model_dump()  # type: ignore[no-any-return]
    if isinstance(slot_cfg, dict):
        return dict(slot_cfg)
    raise TypeError(f"slot_cfg must be SlotConfig or dict, got {type(slot_cfg).__name__}")


def _slot_device(slot_cfg: dict[str, Any]) -> str:
    """Pull the v0.2 ``device`` field with v0.1.x ``backend`` fallback."""
    device = slot_cfg.get("device")
    if device:
        return str(device)
    # SlotConfig._promote_backend_to_device promotes on load, but raw
    # dicts (test injection, capability orchestrator scratch) may carry
    # only ``backend`` — map through the same helper used elsewhere.
    backend = slot_cfg.get("backend")
    if not backend:
        return ""
    from hal0.config.schema import map_backend_to_device

    return map_backend_to_device(str(backend))


def _slot_model(slot_cfg: dict[str, Any]) -> str:
    """Pull ``model.default`` from a slot config. Empty when unset."""
    model_section = slot_cfg.get("model") or {}
    if isinstance(model_section, dict):
        return str(model_section.get("default") or "")
    return ""


def _slot_ctx(slot_cfg: dict[str, Any]) -> int | None:
    """Pull the slot's context window if set. None when unset (Lemonade default).

    Accepts both the legacy ``model.ctx_size`` alias and the canonical
    ``model.context_size`` (SlotConfig's field). Without this a ctx set from
    the dashboard (which writes ``ctx_size``) never reached ``/v1/load`` on
    Lemonade slots (#585).

    ``ctx_size`` is checked first — same precedence as the display shim
    ``hal0.api._slot_ctx_size`` and SlotManager's write-normalization — so
    that on a transient dual-key TOML the fresher dashboard write (the alias)
    wins consistently across read, display, and write. Writes normalize back
    to ``context_size``, so the alias only lingers on TOMLs not yet re-saved.
    """
    model_section = slot_cfg.get("model") or {}
    if isinstance(model_section, dict):
        for key in ("ctx_size", "context_size"):
            ctx = model_section.get(key)
            if isinstance(ctx, int) and ctx > 0:
                return ctx
    return None


def _slot_extra_args(slot_cfg: dict[str, Any]) -> str | None:
    """Pull ``server.extra_args`` if set. None means Lemonade uses defaults.

    The wire format is a single space-separated string (per the
    ``hal0_lemonade_v1_load_schema`` memory + LemonadeClient.load
    docstring). Empty string is rejected as a sentinel ambiguity — we
    omit the key entirely instead.
    """
    server_section = slot_cfg.get("server") or {}
    if isinstance(server_section, dict):
        extra = server_section.get("extra_args")
        if isinstance(extra, str) and extra.strip():
            return extra.strip()
    return None


# ── provider ────────────────────────────────────────────────────────────────


class LemonadeProvider(Provider):
    """Provider adapter for the Lemonade Server runtime.

    Lifecycle calls (``load`` / ``unload`` / ``status``) translate
    SlotConfig fields onto :class:`LemonadeClient` calls. All other
    Provider ABC methods either return informational stubs or raise —
    see module docstring. Stateless aside from the held client.

    The held ``LemonadeClient`` is shared with the idle-unload driver
    (see :mod:`hal0.lemonade.idle`) so both subsystems use one
    connection pool. Construct one provider per hal0-api process —
    :func:`hal0.providers.lemonade_provider` is the singleton getter
    matching the pattern used elsewhere in :mod:`hal0.providers`.
    """

    name: str = "lemonade"

    def __init__(self, client: LemonadeClient | None = None) -> None:
        # Lazy client init: the singleton in ``providers/__init__.py``
        # is constructed at import time; we don't want to open an httpx
        # client until somebody actually uses it. Tests inject their
        # own pre-mocked client.
        self._client = client
        self._owns_client: bool = client is None

    # ── client accessor ────────────────────────────────────────────────────

    def client(self) -> LemonadeClient:
        """Return the held LemonadeClient, constructing one on first use.

        Reads ``LEMONADE_API_KEY`` from the environment on first
        construction (same convention as
        :func:`hal0.api._start_lemonade_idle_driver`). Subsequent
        calls reuse the same instance.
        """
        if self._client is None:
            self._client = LemonadeClient(
                api_key=os.environ.get("LEMONADE_API_KEY") or None,
            )
        return self._client

    async def aclose(self) -> None:
        """Close the held client if we own it. Idempotent."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ── Provider ABC implementations ───────────────────────────────────────

    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        """Return an informational env block for the slot.

        Under Lemonade there's no per-slot env file written to disk —
        ``lemond`` reads its own ``/var/lib/hal0/lemonade/config.json``
        and we never invoke a docker run line. But callers (tests,
        audit logs, ``hal0 slot inspect``) still want a stable "what
        does this slot identify as" view, so we return the same
        ``HAL0_*`` keys the toolbox providers emit, sourced from the
        slot config + LemonadeProvider's runtime mapping.
        """
        cfg = _to_dict(slot_cfg)
        device = _slot_device(cfg)
        recipe, llamacpp_backend = device_to_backend(device)
        env: dict[str, str] = {
            "HAL0_SLOT_NAME": str(cfg.get("name") or ""),
            "HAL0_PORT": str(cfg.get("port") or 0),
            "HAL0_BIND_HOST": "127.0.0.1",
            "HAL0_DEVICE": device,
            "HAL0_PROVIDER": "lemonade",
            "HAL0_MODEL_ID": _slot_model(cfg),
            # Diagnostic: what Lemonade is told for this slot.
            "HAL0_LEMONADE_RECIPE": recipe or "",
            "HAL0_LEMONADE_LLAMACPP_BACKEND": llamacpp_backend or "",
        }
        ctx = _slot_ctx(cfg)
        if ctx is not None:
            env["HAL0_CTX"] = str(ctx)
        # Propagate model_info path when available (audit + UI).
        path = (model_info or {}).get("path")
        if path:
            env["HAL0_MODEL_PATH"] = str(path)
        return env

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Return the conceptual control-plane invocation.

        Lemonade slots don't spawn a per-slot process — the daemon
        ``lemond`` is shared. The returned argv is informational only;
        it points at ``hal0-lemonade.service``'s ExecStart so anybody
        introspecting the slot can find the responsible unit.
        """
        return [
            "/opt/lemonade/lemond",
            "/var/lib/hal0/lemonade",
            # Mirror SlotConfig identity so diagnostics line up.
            f"--slot-name={env.get('HAL0_SLOT_NAME', '')}",
        ]

    async def health(self, port: int) -> dict[str, Any]:
        """Probe Lemonade's daemon health.

        ``port`` is the slot's nominal port (the v0.1.x interface); we
        ignore it and probe lemond on its actual port. Returns a dict
        shaped like other providers' ``health()`` output: ``{ok,
        status, ...}``. Failures are reported via the ``ok=False`` +
        ``status`` keys instead of raising — matches the LlamaServer /
        FLM provider pattern.
        """
        try:
            body = await self.client().health()
        except Exception as exc:
            return {
                "ok": False,
                "status": "unavailable",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        return {"ok": True, "status": "ready", "health": body}

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Pass-through inference is NOT this provider's responsibility.

        The dispatcher (``hal0.dispatcher.router``) speaks OpenAI to
        Lemonade directly on port 13305 — there is no per-provider
        infer indirection in the Lemonade path. Raising here surfaces
        a clear error if anyone wires inference through the provider
        layer by mistake.
        """
        raise NotImplementedError(
            "LemonadeProvider does not own request forward; the dispatcher "
            "speaks OpenAI directly to lemond on port 13305."
        )

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> ContainerSpec:
        """Lemonade has no per-slot container — this method is unreachable
        in production. Raises so a stray caller fails loudly instead of
        silently building a bogus docker line.
        """
        raise NotImplementedError(
            "LemonadeProvider has no container abstraction; lemond owns "
            "process lifecycle. See ADR-0008 §1 + the module docstring."
        )

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Return a stable lemonade:// identifier for this slot.

        Not a real image ref — this string lands in audit logs + the
        slot inspector. Encodes the recipe the device maps to so two
        slots on different devices show distinguishable identifiers.
        """
        cfg = _to_dict(slot_cfg)
        device = _slot_device(cfg)
        recipe, llamacpp_backend = device_to_backend(device)
        if recipe:
            return f"lemonade://recipe/{recipe}"
        if llamacpp_backend:
            return f"lemonade://llamacpp/{llamacpp_backend}"
        return "lemonade://default"

    def render_systemd_override(
        self,
        slot_name: str,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
        *,
        env_file_path: Any,
        container_runtime: str = "/usr/bin/docker",
    ) -> str:
        """No per-slot systemd unit under Lemonade.

        Reachable only via :func:`hal0.slots.unit_template.render_override`,
        which SlotManager no longer calls (PR-10 retired the legacy
        systemd render path). Raising ensures we fail loudly if any
        future caller wires this through by mistake.
        """
        raise NotImplementedError(
            "LemonadeProvider has no per-slot systemd unit; lemond owns "
            "process lifecycle (ADR-0008 §1)."
        )

    # ── lifecycle (new methods on top of the ABC) ──────────────────────────

    async def load(
        self,
        slot_cfg: dict[str, Any] | Any,
        model_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Tell Lemonade to load the slot's model.

        Translates ``slot_cfg.device`` to ``(recipe, llamacpp_backend)``
        via :func:`device_to_backend`, then calls
        :meth:`LemonadeClient.load` with the slot's
        ``model.default`` / ``model.context_size`` /
        ``server.extra_args``.

        Returns Lemonade's parsed JSON response body on success.
        Propagates :class:`LemonadeLoadError` / other
        ``LemonadeError`` subclasses unchanged — SlotManager catches
        and re-states the slot to ERROR with the original message.

        Args:
            slot_cfg: SlotConfig pydantic model or raw dict.
            model_info: Optional registry metadata; currently unused
                under Lemonade (the daemon resolves models via its own
                ``server_models.json``). Accepted for symmetry with the
                toolbox-provider signatures.
        """
        cfg = _to_dict(slot_cfg)
        model_name = _slot_model(cfg)
        if not model_name:
            # Mirror the SlotConfigError surface used elsewhere — bare
            # ValueError is what SlotManager wraps into a typed error.
            raise ValueError(
                f"slot {cfg.get('name')!r} has no model.default set; "
                "Lemonade /v1/load requires a model_name"
            )
        device = _slot_device(cfg)
        recipe, llamacpp_backend = device_to_backend(device)
        ctx_size = _slot_ctx(cfg)
        llamacpp_args = _slot_extra_args(cfg)

        log.info(
            "lemonade.provider.load",
            extra={
                "slot": cfg.get("name"),
                "model_name": model_name,
                "device": device,
                "recipe": recipe,
                "llamacpp_backend": llamacpp_backend,
            },
        )
        return await self.client().load(
            model_name,
            recipe=recipe,
            ctx_size=ctx_size,
            llamacpp_backend=llamacpp_backend,
            llamacpp_args=llamacpp_args,
        )

    async def unload(
        self,
        slot_cfg: dict[str, Any] | Any,
    ) -> dict[str, Any]:
        """Tell Lemonade to unload the slot's model. Idempotent.

        Returns Lemonade's parsed JSON response. When the slot has no
        ``model.default`` (never been loaded), returns ``{"ok": True,
        "noop": "no model to unload"}`` without hitting the network.

        Also short-circuits when the slot's model exists in config but
        isn't currently in Lemonade's ``/v1/health.loaded[]`` — calling
        ``/v1/unload`` against a not-loaded model name 404s, which the
        seed-vs-runtime distinction (see memory
        ``hal0_primary_slot_seed_vs_runtime``) makes a routine
        occurrence: the dashboard's Unload button on a slot whose
        seed ``model.default`` has never been loaded would otherwise
        surface as a 500 error to the user.
        """
        cfg = _to_dict(slot_cfg)
        model_name = _slot_model(cfg)
        if not model_name:
            log.info(
                "lemonade.provider.unload_noop",
                extra={"slot": cfg.get("name")},
            )
            return {"ok": True, "noop": "no model to unload"}
        # Probe loaded[] before issuing /v1/unload to avoid 404s on
        # seed-but-never-loaded slots. status() never raises.
        snap = await self.status(cfg)
        if not snap.get("loaded"):
            log.info(
                "lemonade.provider.unload_noop_not_loaded",
                extra={
                    "slot": cfg.get("name"),
                    "model_name": model_name,
                    "reason": snap.get("reason"),
                },
            )
            return {
                "ok": True,
                "noop": "model not currently loaded",
                "model_name": model_name,
            }
        log.info(
            "lemonade.provider.unload",
            extra={"slot": cfg.get("name"), "model_name": model_name},
        )
        return await self.client().unload(model_name)

    async def status(
        self,
        slot_cfg: dict[str, Any] | Any,
    ) -> dict[str, Any]:
        """Derive a per-slot status snapshot from Lemonade's /v1/health.

        Filters ``loaded[]`` for the slot's model_name and returns:

          - ``{"loaded": True, "model_name": ..., "backend_url": ...,
             "last_use": ..., "raw": <entry>}`` when found
          - ``{"loaded": False, "model_name": ..., "reason": ...}``
            when not found / no model assigned / health probe failed

        Never raises — this method is on the dashboard hot path and a
        lemond hiccup must not surface as a 500.
        """
        cfg = _to_dict(slot_cfg)
        model_name = _slot_model(cfg)
        if not model_name:
            return {"loaded": False, "model_name": "", "reason": "no model assigned"}
        try:
            health = await self.client().health()
        except Exception as exc:
            return {
                "loaded": False,
                "model_name": model_name,
                "reason": "lemonade unavailable",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        # DECLARED backend: the slot's intent, normalized to the backend
        # token (rocm|vulkan|cpu|flm). Always known for a configured slot.
        device = _slot_device(cfg)
        recipe, declared_llamacpp = device_to_backend(device)
        # NPU → recipe="flm" with no llamacpp_backend; surface "flm".
        declared_backend = declared_llamacpp or (recipe if recipe == "flm" else None)
        for key in ("loaded", "all_models_loaded"):
            value = health.get(key) if isinstance(health, dict) else None
            if not isinstance(value, list):
                continue
            for entry in value:
                if not isinstance(entry, dict):
                    continue
                if entry.get("model_name") == model_name:
                    out: dict[str, Any] = {
                        "loaded": True,
                        "model_name": model_name,
                        "backend_url": entry.get("backend_url"),
                        "last_use": entry.get("last_use"),
                        "raw": entry,
                    }
                    if declared_backend:
                        out["declared_backend"] = declared_backend
                    # ACTUAL backend: introspect the live child process.
                    # Omit the key (don't set null) when undeterminable —
                    # the client treats absence as "unknown, no badge".
                    actual_backend = resolve_actual_backend(entry)
                    if actual_backend:
                        out["actual_backend"] = actual_backend
                        # backend_mismatch only when BOTH are known.
                        if declared_backend:
                            out["backend_mismatch"] = actual_backend != declared_backend
                    return out
        return {
            "loaded": False,
            "model_name": model_name,
            "reason": "model not in /v1/health.loaded[]",
        }


__all__ = [
    "LemonadeProvider",
    "device_to_backend",
    "resolve_actual_backend",
]
