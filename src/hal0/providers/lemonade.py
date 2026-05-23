"""LemonadeProvider — v0.2 unified-runtime Provider (PR-8).

In v0.1.x every backend (llama-server, FLM, Moonshine, Kokoro, …) was a
separate Provider class that spawned its own toolbox container under a
systemd template unit. v0.2 (ADR-0008) replaces that with a single
Lemonade Server daemon (``lemond``) that owns process lifecycle for
every modality. ``LemonadeProvider`` is the thin adapter between hal0's
slot abstraction and Lemonade's HTTP control plane.

Where this fits in the abstraction stack:

  - SlotManager keeps its v0.1.x public surface (``load`` / ``unload`` /
    ``swap`` / ``status`` / …). Per PR-8 contract, no caller signatures
    change. SlotManager internally gates between the legacy
    docker/systemd path and the Lemonade path on ``HAL0_BACKEND``.
  - Under ``HAL0_BACKEND=lemonade``, SlotManager calls
    :meth:`LemonadeProvider.load` / :meth:`unload` instead of writing an
    override.conf and running ``systemctl start``. The active Lemonade
    daemon (``hal0-lemonade.service``, port 13305) owns every child
    process. There is no per-slot container to render.
  - Under the legacy backend mode, every other Provider in
    :mod:`hal0.providers` continues to work unchanged. Phase 3's PR-10
    will retire those providers and the systemd path; until then they
    live alongside this class.

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
from typing import Any

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
    """Pull ``model.context_size`` if set. None when unset (Lemonade default)."""
    model_section = slot_cfg.get("model") or {}
    if isinstance(model_section, dict):
        ctx = model_section.get("context_size")
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


# ── env override (test seam) ────────────────────────────────────────────────


def _env_backend() -> str:
    """Read the active backend mode from the environment.

    Centralised so tests can monkeypatch one symbol instead of poking
    ``os.environ`` directly. The match is case-insensitive + stripped
    so accidental whitespace in ``hal0-api.service`` env files doesn't
    silently disable the Lemonade path.
    """
    return (os.environ.get("HAL0_BACKEND") or "").strip().lower()


def lemonade_active() -> bool:
    """True when ``HAL0_BACKEND=lemonade`` is in effect.

    Public helper so SlotManager + tests can gate on the same predicate
    without duplicating the env-var spelling. Matches the gating in
    :func:`hal0.api._maybe_start_lemonade_idle_driver`.
    """
    return _env_backend() == "lemonade"


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
        :func:`hal0.api._maybe_start_lemonade_idle_driver`). Subsequent
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
        which SlotManager skips on the Lemonade-active branch. Raising
        ensures we fail loudly if the skip is ever wired wrong.
        """
        raise NotImplementedError(
            "LemonadeProvider has no per-slot systemd unit; lemond owns "
            "process lifecycle. SlotManager must skip render_override "
            "when HAL0_BACKEND=lemonade."
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
        """
        cfg = _to_dict(slot_cfg)
        model_name = _slot_model(cfg)
        if not model_name:
            log.info(
                "lemonade.provider.unload_noop",
                extra={"slot": cfg.get("name")},
            )
            return {"ok": True, "noop": "no model to unload"}
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
        for key in ("loaded", "all_models_loaded"):
            value = health.get(key) if isinstance(health, dict) else None
            if not isinstance(value, list):
                continue
            for entry in value:
                if not isinstance(entry, dict):
                    continue
                if entry.get("model_name") == model_name:
                    return {
                        "loaded": True,
                        "model_name": model_name,
                        "backend_url": entry.get("backend_url"),
                        "last_use": entry.get("last_use"),
                        "raw": entry,
                    }
        return {
            "loaded": False,
            "model_name": model_name,
            "reason": "model not in /v1/health.loaded[]",
        }


__all__ = [
    "LemonadeProvider",
    "device_to_backend",
    "lemonade_active",
]
