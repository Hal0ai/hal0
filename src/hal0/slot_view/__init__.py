"""slot_view — stateless read-model aggregation for the slots dashboard (issue #698).

``GET /api/slots`` used to inline five enrichment concerns in the route
handler (``api/routes/slots.py:list_slots``): slot-state serialization,
config-field lifting, container systemctl/port probing, per-slot memory
accounting, and metric injection. Testing any one concern meant mocking
five pieces of app state and calling the route over HTTP.

This module lifts each concern into a named, directly-testable unit:

  - :func:`serialize_slot` — Slot snapshot → wire dict.
  - :func:`config_enrichment` — pure: configs → per-slot TOML-derived
    fields (drawer seeds, NPU trio grouping).
  - :func:`container_enrichment` — per-slot systemctl + /health
    + image probes (provider injectable for tests).
  - :func:`synthesize_upstream_entries` — virtual entries for configured
    upstreams that have no local slot yet.
  - :class:`SlotViewAggregator` — eager composition of all of the above;
    ``snapshot()`` returns typed :class:`SlotView` records the route
    serializes verbatim.

The aggregator is **stateless** — constructor deps are stored, nothing
is cached between ``snapshot()`` calls; the route constructs one per
request. There are intentionally NO per-concern composition methods or
``include_*`` flags: ``list_slots`` is the only caller today, so the
eager one-shot ``snapshot()`` is the whole surface (design review on
the issue).

Device→backend translation comes from :mod:`hal0.model_meta`
(:func:`hal0.model_meta.device_to_backend`) — no re-derived heuristics
here. (``model_meta.classify`` is the designated classifier should a
modality bucket ever be needed in this view; the current wire shape
carries none.)

NOTE: kept import-light, mirroring ``hal0.slot_config``. Heavy
neighbours (``hal0.providers.*``, ``hal0.slots.manager``,
``hal0.slots.capacity``, ``hal0.api.routes.models``) are imported
lazily inside functions both to avoid import cycles (slots.manager is
in this module's call graph and api.routes.slots imports us) and so
route-level tests that monkeypatch those modules' attributes keep
working unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from hal0.model_meta import device_to_backend
from hal0.slots.activation import (
    autoload_enabled,
    claims_npu_anchor,
    effective_npu_table,
    npu_modality_active,
)
from hal0.slots.naming import slot_instance_token, slot_unit_name
from hal0.slots.reaper import eviction_priority
from hal0.slots.reaper import is_pinned as reaper_is_pinned

log = logging.getLogger(__name__)

__all__ = [
    "SlotMetricsView",
    "SlotView",
    "SlotViewAggregator",
    "config_enrichment",
    "container_enrichment",
    "loaded_model_names_from_slots",
    "serialize_slot",
    "synthesize_upstream_entries",
]


# ── typed records ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class SlotMetricsView:
    """Card-shaped live metrics for one slot (``slot.metrics.*`` on the wire)."""

    toks: float = 0.0
    ttft: int | None = None
    ctx: int = 0
    kv: float | None = None
    mem: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "toks": self.toks,
            "ttft": self.ttft,
            "ctx": self.ctx,
            "kv": self.kv,
            "mem": self.mem,
        }


@dataclass(slots=True)
class SlotView:
    """One row of the ``GET /api/slots`` response, typed.

    ``payload`` carries the enriched wire fields exactly as the legacy
    inline dict built them (the enrichment key set is conditional —
    ``backend_url`` / ``coresident_group`` / ``container_*`` / … appear
    only when applicable, and the dashboard distinguishes absent from
    null). The aggregator-owned stamps (``mem_mb``, ``metrics``) are
    typed fields appended by :meth:`to_dict` in the legacy key order so
    the response stays byte-identical.
    """

    name: str
    kind: str
    status: str
    synthetic: bool
    #: Resident memory attribution in MB. ``None`` on synthetic entries —
    #: the legacy response omits the key there (they have no local cgroup).
    mem_mb: float | int | None
    metrics: SlotMetricsView
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.payload)
        if self.mem_mb is not None:
            out["mem_mb"] = self.mem_mb
        out["metrics"] = self.metrics.to_dict()
        return out


# ── concern 1: slot-state serialization ──────────────────────────────────────


def serialize_slot(slot: Any, model_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    """Serialise a real Slot snapshot into the API shape.

    Adds ``kind="local"`` so the UI can distinguish real slots from the
    synthetic upstream-backed entries (which carry ``kind="remote"`` or
    similar and ``_synthetic: true``).

    When ``model_cache`` is provided (a ``{slot_name: [model, ...]}``
    map), also includes a ``models`` list pulled from it. For an FLM
    slot serving chat + embed + asr concurrently, this surfaces all
    three tags so the dashboard can render the slot as multi-model
    instead of showing only the chat tag. ``model_cache=None`` omits the
    ``models`` key entirely (legacy ``_slot_to_dict(slot)`` behaviour).
    """
    base = slot.as_dict()
    base["kind"] = "local"
    base["status"] = slot.state.value
    # Lift backend / provider out of metadata to the top level so the UI
    # doesn't have to dig — the slot snapshot's `backend` is only set on
    # transitions that pass it explicitly, but metadata carries both
    # consistently after create / update_config.
    meta = base.get("metadata") or {}
    if not base.get("backend") and meta.get("backend"):
        base["backend"] = meta.get("backend")
    if not base.get("provider") and meta.get("provider"):
        base["provider"] = meta.get("provider")
    if "config_drift" in meta:
        base["config_drift"] = meta.get("config_drift")
    if model_cache is not None:
        loaded = list(model_cache.get(slot.name, []))
        if slot.model_id and slot.model_id in loaded:
            loaded.remove(slot.model_id)
            loaded.insert(0, slot.model_id)
        base["models"] = loaded
        # #792: a model-requiring slot that reports "serving" with zero
        # resident models is lying — nothing is actually loaded to serve.
        # The provided model_cache is the authoritative live loaded set, so
        # an empty list means nothing is resident. Downgrade the surfaced
        # status to idle — the documented "process up but /v1/models empty"
        # state (issue #31). Only SERVING is corrected: an empty cache for a
        # READY/IDLE slot can mean "not yet observed" rather than "definitely
        # empty". The true FSM ``state`` is preserved; only ``status`` moves.
        if base.get("status") == "serving" and not loaded:
            # Lazy import: top-level would cycle (hal0.slots.__init__ pulls in
            # the heavy manager, which imports this module). See module note.
            from hal0.slots.state import provider_requires_model

            if provider_requires_model(base.get("provider")):
                base["status"] = "idle"
    return base


# ── loaded-model derivation ──────────────────────────────────────────────────


def loaded_model_names_from_slots(slots: list[Any]) -> set[str]:
    """Model ids currently served by dispatchable slots.

    Derives the loaded set from Slot snapshots: a model counts as
    loaded when its slot is in the dispatchable ready-set (READY /
    SERVING / IDLE, per #696 / DR-8). Junk entries are skipped.
    """
    # Lazy import: top-level ``from hal0.slots...`` would cycle (hal0.slots
    # __init__ pulls in the heavy manager, which imports this module). Mirror
    # the pattern used in ``serialize_slot`` above. ``is_dispatchable_state``
    # owns the shared str/StrEnum normalisation, so pass the raw snapshot state.
    from hal0.slots.state import is_dispatchable_state

    names: set[str] = set()
    for slot in slots:
        if not is_dispatchable_state(getattr(slot, "state", "")):
            continue
        model_id = getattr(slot, "model_id", None)
        if isinstance(model_id, str) and model_id:
            names.add(model_id)
    return names


# ── concern 2: config-field lifting ──────────────────────────────────────────


def config_enrichment(configs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-slot TOML-derived fields for slot snapshots. Pure.

    Lifts the edit-drawer seed fields (type, model default + labels,
    n_gpu_layers, rope_freq_base, idle_timeout_s, workers, llamacpp_args)
    plus the NPU trio grouping and per-shadow modality state so the dashboard
    renders cards + drawers without a per-slot ``/config`` fetch.
    ``enable_thinking`` / ``mtp`` / ``vision`` are NOT lifted here — they
    are model-owned tri-state caps now (spec-hw-slot-ownership §1),
    surfaced via ``/api/models`` instead. Neither is ``enabled``: the field
    is gone (#1369) and the UI derives "grey / not configured" from
    ``model_default`` being absent.

    Coresident grouping:
      A slot of type=llm + device=npu serving as the chat anchor and
      any sibling ``stt`` / ``embed`` alias records share a
      ``coresident_group=npu-flm-trio`` marker. The dashboard uses this to
      render a "trio" badge linking the cards.
    """
    # First pass — is there an NPU LLM anchor with a model bound? That, not a
    # separate flag, is what makes the trio real (#1369).
    trio_active = any(claims_npu_anchor(cfg) for cfg in configs)

    out: dict[str, dict[str, Any]] = {}
    for cfg in configs:
        name = str(cfg.get("name", ""))
        if not name:
            continue
        model_default = ""
        model_labels: list[str] = []
        model_section = cfg.get("model")
        if isinstance(model_section, dict):
            model_default = str(model_section.get("default") or "")
            raw_labels = model_section.get("labels", ())
            if isinstance(raw_labels, (list, tuple)):
                model_labels = [str(x) for x in raw_labels]
        entry: dict[str, Any] = {}

        # PR-18: lift slot ``type`` + model ``labels`` + model ``default``
        # so the dashboard's chat surface can build the persona dropdown
        # (which chat-type slots have a model?) and decide whether to opt
        # in to OmniRouter (does the active persona's model carry the
        # ``tool-calling`` label?) without making a second call to
        # /api/slots/{name}/config per slot. ``model_default`` doubles as
        # the activation signal since #1369 — absent means grey/unconfigured,
        # so no separate ``enabled`` key is lifted.
        slot_type = cfg.get("type")
        if isinstance(slot_type, str) and slot_type:
            entry["type"] = slot_type
        if model_default:
            entry["model_default"] = model_default
        if model_labels:
            entry["labels"] = model_labels

        # §21.10 operator pin (#1367): lift the *effective* pin state —
        # explicit ``pinned`` key wins, else the default anchor set
        # (``agent``/``utility``/``npu``) applies — so the drawer's
        # Pinned/Unpinned toggle and the card badge render without a
        # per-slot /config fetch.
        entry["pinned"] = reaper_is_pinned(name, cfg)

        # Spec 2026-08-02: lift the EFFECTIVE autoload (migration shim
        # applied — absent key + bound model reads true) and eviction
        # priority so the drawer's controls render without a per-slot
        # /config fetch, same pattern as the pinned lift above.
        entry["autoload"] = autoload_enabled(cfg)
        entry["priority"] = eviction_priority(cfg)

        # SC-4 default marker: exactly one slot per ``type`` may carry
        # ``default = true`` (check_default_uniqueness). Lifted verbatim so the
        # slot list can render a "Set as default" action only on the slots that
        # are NOT already their type's default — the create modal no longer
        # asks, so this is the one place the operator re-points it.
        entry["default"] = cfg.get("default") is True

        # ctx_max: the configured context window. The canonical TOML key is
        # ``[model].context_size`` (the dashboard's ``ctx_size`` alias is
        # folded to it on write — see slots.manager). Surfaced so the
        # Inference engine pane can render "ctx used / max" without a
        # per-slot /config fetch. Absent → key omitted; the UI shows an
        # em-dash rather than a fabricated number.
        if isinstance(model_section, dict):
            ctx_max = model_section.get("context_size")
            if ctx_max is None:
                ctx_max = model_section.get("ctx_size")
            if isinstance(ctx_max, int):
                entry["ctx_max"] = ctx_max

        # Spec 1 / Component 1: surface the edit-panel config fields so the
        # card + drawer seed their controls without a per-slot /config fetch.
        # ``n_gpu_layers`` falls back to the ModelConfig default sentinel
        # (-1 = all layers) when unset.
        #
        # spec-hw-slot-ownership §1: ``enable_thinking`` and ``mtp`` are no
        # longer slot facts — they moved to ``ModelDefaults.enable_thinking``
        # / ``.mtp`` (the model drawer reads/writes them via /api/models).
        # This lift is deliberately dropped (was ``entry["enable_thinking"] =
        # cfg.get("enable_thinking")`` / ``entry["mtp"] = cfg.get("mtp")``).
        #
        # Per-slot chat_template override (tri-valued: string/None/absent → null).
        # Surfaced so the edit-drawer Template row seeds its override select
        # from disk instead of defaulting to "auto" on every reopen.
        entry["chat_template"] = cfg.get("chat_template")
        # NGL — spec-hw-slot-ownership §2: NGL is now a TOP-LEVEL slot-owned
        # hardware field. Prefer the top-level value; fall back to the prior
        # nested [model].n_gpu_layers for pre-migration slots (the §6 migration
        # folds it up), then the -1 "all layers" sentinel.
        top_ngl = cfg.get("n_gpu_layers")
        nested_ngl = model_section.get("n_gpu_layers") if isinstance(model_section, dict) else None
        if isinstance(top_ngl, int):
            entry["n_gpu_layers"] = top_ngl
        elif isinstance(nested_ngl, int):
            entry["n_gpu_layers"] = nested_ngl
        else:
            entry["n_gpu_layers"] = -1
        # THREADS / BINARY / image_pin — the rest of the slot HW grid
        # (spec-hw-slot-ownership §2/§3), all top-level. Surfaced verbatim
        # (absent → null) so the edit-drawer dirty-tracks real on-disk state:
        #   threads: 0 = unset (runtime default)
        #   binary:  "" = derive the HW-gated default from device
        #   image_pin: null = release default (RUNNER_IMAGES[binary])
        threads_val = cfg.get("threads")
        entry["threads"] = threads_val if isinstance(threads_val, int) else None
        binary_val = cfg.get("binary")
        entry["binary"] = binary_val if isinstance(binary_val, str) else None
        entry["image_pin"] = cfg.get("image_pin")
        # Issue #548: expose rope_freq_base so the Edit drawer can dirty-track
        # it and avoid clobbering the on-disk value on unrelated saves.
        # Absent (None) is surfaced as-is — frontend treats null as "use
        # model default" (0.0 sentinel) and only writes back when changed.
        rope = model_section.get("rope_freq_base") if isinstance(model_section, dict) else None
        entry["rope_freq_base"] = rope if isinstance(rope, (int, float)) else None

        # Spec 1 / Component 2 (issue #587): surface idle_timeout_s,
        # workers, and the slot's freeform llamacpp_args so the Edit
        # drawer can seed from real on-disk values. The on-disk field
        # for the freeform overlay is ``[server].extra_args``
        # (ServerConfig); the dashboard's wire key is ``llamacpp_args``
        # (the dashboard's historical wire key), so we map at this
        # boundary. Defaults are NOT applied here — the wire payload is
        # the truth source the dashboard uses to dirty-track changes,
        # so an absent on-disk field surfaces as null/None, not the
        # schema default.
        # (former #901 per-slot vision toggle lift removed — spec-hw-slot-
        # ownership §1: vision is now ``ModelDefaults.vision``, read from
        # /api/models by the model drawer, not lifted onto the slot payload.)
        entry["idle_timeout_s"] = cfg.get("idle_timeout_s")
        entry["workers"] = cfg.get("workers")
        # Continuous batching: --parallel sequence slots (absent → null so the
        # drawer dirty-tracks "inherit the profile" vs an explicit override).
        entry["parallel"] = cfg.get("parallel")
        server_cfg = cfg.get("server")
        if server_cfg is None:
            extra_args: Any = None
        elif isinstance(server_cfg, dict):
            extra_args = server_cfg.get("extra_args")
        else:
            # ServerConfig pydantic model — read via attribute to stay
            # consistent with the .get() pattern above.
            extra_args = getattr(server_cfg, "extra_args", None)
        entry["llamacpp_args"] = extra_args

        # [npu] table: expose asr/embed toggles so the dashboard can seed
        # its NPU modality controls. effective_npu_table() also folds in the
        # pre-[npu]-table [defaults].load_asr/load_embed fallback
        # providers/flm.py still honors when a pre-1.0 config has no [npu]
        # table at all (#1670) -- without it the pill renders off even while
        # the modality is actually running.
        npu_table = effective_npu_table(cfg)
        if npu_table:
            entry["npu"] = {
                "asr": bool(npu_table.get("asr")),
                "embed": bool(npu_table.get("embed")),
                # chat defaults ON (NpuConfig.chat=True) when the key is absent,
                # so the drawer's Chat pill seeds from the real persisted value.
                "chat": npu_table.get("chat", True) is not False,
            }

        # declared_backend: normalized token (rocm|vulkan|cpu|flm) from the
        # configured ``device`` so the UI renders a stable backend chip.
        _recipe, _llamacpp = device_to_backend(cfg.get("device"))
        declared_backend = _llamacpp or (_recipe if _recipe == "flm" else None)
        if declared_backend:
            entry["declared_backend"] = declared_backend

        # npu_modality_active: for a trio SHADOW, whether the anchor is
        # actually serving its modality (the anchor's ``[npu].asr`` / ``.embed``
        # — i.e. the ``flm serve`` flags). The shadow's own config cannot answer
        # this: it carries a placeholder model and, pre-#1369, a mirrored
        # ``enabled`` flag that drifted from what FLM launched with. Lifting the
        # resolved answer here keeps the shadow card's ON/OFF pill from having
        # to hunt for the anchor in the slot list.
        if cfg.get("device") == "npu" and cfg.get("type") != "llm":
            entry["npu_modality_active"] = npu_modality_active(configs, str(cfg.get("type") or ""))

        # Coresident grouping — every device=npu slot (anchor + stt/embed
        # shadows) backs the same FLM process, so they share a group marker.
        # Keyed on device, NOT slot name: deployment renamed the trio to
        # npu/stt/embed, so the old name-based set never matched in prod.
        # Membership requires only a live anchor: the shadows are records for
        # that one process and carry placeholder models, so per-shadow
        # membership was never independently meaningful (#1369).
        if cfg.get("device") == "npu" and trio_active:
            entry["coresident_group"] = "npu-flm-trio"

        out[name] = entry
    return out


def _resolve_ctx_max(
    raw_ctx_max: int | None,
    model_registry: Any,
    model_id: str,
    slot_name: str,
) -> int | None:
    """Best-effort EFFECTIVE context resolution for the dashboard's ``ctx_max``.

    #1788: ``config_enrichment`` lifts ``ctx_max`` straight off the slot
    TOML — the raw hardware ceiling, not what llama-server actually runs
    with now that the MODEL owns context size (v1.0 ownership split; see
    :func:`hal0.providers.container._resolve_context_size`). The aggregator
    calls this after merging ``config_enrichment``'s output so the
    "ctx used / max" pane reports the same number the launch path resolved,
    using the model registry the aggregator already carries.

    Imported lazily (heavy neighbour, see module docstring) and never
    raises: a resolution failure degrades to the raw TOML ceiling — the
    pre-fix behavior — rather than breaking the slots list.
    """
    try:
        from hal0.providers.container import resolve_effective_context_size

        return resolve_effective_context_size(
            raw_ctx_max, model_registry, model_id, slot_name=slot_name
        )
    except Exception:
        return raw_ctx_max


# ── concern 3: container probe ───────────────────────────────────────────────


def _resolve_container_provider(provider: Any) -> Any:
    """Injected provider or the process-wide real one (lazy import)."""
    if provider is not None:
        return provider
    from hal0.providers.container import container_provider

    return container_provider()


#: Wall-clock bound on ONE slot's container probe (issue #1427).
#:
#: The probe is four independent IO waits (``systemctl is-active``, GET
#: /health on the slot port, ``podman inspect`` on the container, ``podman
#: image inspect`` on the declared image). Each has its own inner timeout,
#: but nothing bounded their SUM, and the whole thing ran serially across
#: every slot — which is where ``GET /api/slots`` got its measured 17-41 s.
#: A slot that blows this budget degrades to ``stopped`` / unhealthy for
#: that poll, exactly as a probe exception already did; the next poll
#: (2.5 s later) re-probes it.
_PROBE_TIMEOUT_S = 8.0

#: How many slot probes may be in flight at once. The probes are dominated
#: by subprocess waits that land in the default thread-pool executor, so an
#: unbounded fan-out over ~30 slots would just queue there while spawning
#: ~90 podman/systemctl children at once. Wide enough that a typical
#: deployment finishes in one wave.
_PROBE_CONCURRENCY = 8

#: ``podman image inspect`` answers ("is this image present on disk?") change
#: only when an image is pulled or pruned — nowhere near the dashboard's
#: 2.5 s poll cadence. Cache per image ref so a 20-slot box sharing 3 images
#: pays 3 inspects per TTL window instead of 20 per poll. Kept short enough
#: that a just-finished pull flips missing→present within one refresh.
_IMAGE_PRESENT_TTL_S = 15.0
_image_present_cache: dict[str, tuple[float, bool]] = {}


def _image_present_cached(cp: Any, image: str) -> bool:
    """TTL-cached ``cp.image_present`` (subprocess-backed, executor-called)."""
    now = time.monotonic()
    hit = _image_present_cache.get(image)
    if hit is not None and now - hit[0] < _IMAGE_PRESENT_TTL_S:
        return hit[1]
    present = bool(cp.image_present(image))
    _image_present_cache[image] = (time.monotonic(), present)
    return present


async def container_enrichment(
    configs: list[dict[str, Any]],
    *,
    pull_jobs: dict[str, Any] | None = None,
    provider: Any = None,
) -> dict[str, dict[str, Any]]:
    """Per-slot live container state.

    For each slot, probes two live sources:
      1. ``provider.is_active`` (systemctl) → ``container_status``
         (``running`` | ``stopped`` | ``starting`` | ``crashed``)
      2. GET /health on the slot port → ``container_health`` (bool)

    plus image facts: ``actual_image`` (podman inspect, #663),
    ``image_mismatch`` against the declared profile image, and
    ``image_status`` (present | pulling | missing | not-configured — an
    in-flight job in ``pull_jobs`` wins without an inspect syscall;
    ``not-configured`` marks a slot with no declared profile/image, #1226).

    ``provider`` is injectable for unit tests; ``None`` resolves the real
    ContainerProvider lazily so route-level patches on the class keep
    working. Never raises — probe failures degrade to ``stopped`` /
    ``False`` rather than surfacing as a 500.

    Slots are probed **concurrently** (bounded by
    :data:`_PROBE_CONCURRENCY`) and each slot's probe is bounded by
    :data:`_PROBE_TIMEOUT_S`. Issue #1427: this loop used to run serially,
    so ``GET /api/slots`` paid the sum of every slot's connect timeouts —
    17-41 s measured on a 19-29 slot box. Concurrency turns that sum into a
    max, and the per-slot deadline bounds the max.
    """
    from hal0.slots.manager import _cfg_port  # type: ignore[attr-defined]

    jobs = pull_jobs or {}
    named = [(str(cfg.get("name", "")), cfg) for cfg in configs]
    named = [(name, cfg) for name, cfg in named if name]
    if not named:
        return {}

    # The profile catalogue is process-global config, not per-slot state —
    # load it ONCE instead of re-reading and re-parsing profiles.toml inside
    # every slot's probe.
    try:
        from hal0.config.loader import load_profiles_config

        profiles: dict[str, Any] | None = load_profiles_config().profile
    except Exception:
        # ``None`` (not ``{}``) so an unreadable catalogue degrades exactly as
        # the old per-slot ``except`` did: image + resolved_command are nulled
        # rather than half-resolved.
        profiles = None

    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def _probe_status(name: str, cfg: dict[str, Any]) -> tuple[str, bool]:
        """(container_status, container_health) for one slot."""
        try:
            cp = _resolve_container_provider(provider)
            # 1) systemctl is-active (synchronous — run in executor, #1427: on
            # the event loop it would serialise every slot's check). The CONFIG
            # goes down, not the name: the unit is keyed by the slot's instance
            # token, which is the durable id on a post-migration box (#1417).
            active = await loop.run_in_executor(None, cp.is_active, cfg)
            if active:
                # 2) /health probe on the slot port to distinguish running vs starting.
                # ComfyUI ships no llama-style /health — its readiness probe is
                # GET /system_stats (ComfyUIProvider.health). Probing the
                # generic path 404'd forever, so a healthy img slot rendered
                # "starting"/unhealthy in the dashboard indefinitely. Only when
                # no provider was injected (tests inject fakes and expect them
                # to answer): production resolves the real family provider.
                port = _cfg_port(cfg)
                if port:
                    health_probe = cp.health
                    if provider is None:
                        from hal0.providers.comfyui import ComfyUIProvider
                        from hal0.providers.container import _spec_provider_for

                        sp = _spec_provider_for(cfg)
                        if isinstance(sp, ComfyUIProvider):
                            health_probe = sp.health
                    health = await health_probe(port)
                    container_health = bool(health.get("ok"))
                    return ("running" if container_health else "starting", container_health)
                return "running", False
            # Distinguish crashed (failed) from clean stop by checking unit state
            # via is-active exit codes: 0=active, 3=inactive, other=failed
            try:
                import subprocess

                # In an executor: subprocess.run blocks, and on the event loop
                # it would serialise every stopped slot's 5 s worst case into
                # the request (#1427). Through the naming seam, not an
                # f-string on the name: this discriminator was the last place
                # that hardcoded a name-keyed unit (#1417), so on an
                # id-keyed box it reported every slot "stopped" and never
                # "crashed".
                result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        ["systemctl", "is-active", slot_unit_name(slot_instance_token(cfg))],
                        capture_output=True,
                        timeout=5,
                    ),
                )
                if result.stdout.decode().strip() == "failed":
                    return "crashed", False
            except Exception:
                pass
            return "stopped", False
        except Exception:
            return "stopped", False

    async def _one(name: str, cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        entry: dict[str, Any] = {}

        container_status, container_health = await _probe_status(name, cfg)
        entry["container_status"] = container_status
        entry["container_health"] = container_health

        # [npu] table: expose asr/embed toggles so the dashboard can seed
        # its NPU modality controls without a separate /config fetch.
        # effective_npu_table() also folds in the pre-[npu]-table
        # [defaults].load_asr/load_embed fallback providers/flm.py still
        # honors when a pre-1.0 config has no [npu] table at all (#1670).
        npu_table = effective_npu_table(cfg)
        if npu_table:
            entry["npu"] = {
                "asr": bool(npu_table.get("asr")),
                "embed": bool(npu_table.get("embed")),
                # chat defaults ON (NpuConfig.chat=True) when the key is absent,
                # so the drawer's Chat pill seeds from the real persisted value.
                "chat": npu_table.get("chat", True) is not False,
            }

        # Emit runtime / profile / image so the UI doesn't have to dig
        # into metadata, and resolved_command so the drawer can show the
        # real podman argv instead of fabricating flags client-side.
        entry["runtime"] = "container"
        profile_name = str(cfg.get("profile") or "")
        entry["profile"] = profile_name
        image: str | None = None
        if profile_name:
            try:
                if profiles is None:
                    raise RuntimeError("profiles catalogue unreadable")
                prof = profiles.get(profile_name)
                if prof is not None:
                    from hal0.providers.container import _resolve_image_ref

                    image = _resolve_image_ref(cfg, prof)
                entry["image"] = image
                # Lift device_class + backend from the resolved profile so the
                # UI groups by silicon class and colours by GPU runtime without
                # re-deriving. backend is None for non-GPU profiles (the card
                # then falls back to device_class). Merged via setdefault, so a
                # backend already lifted from slot metadata still wins.
                if prof is not None:
                    entry["device_class"] = prof.device_class
                    entry["backend"] = prof.backend
                # resolved_command = llama-server argv starting from the image.
                # Off the event loop: it re-reads config + resolves the runner
                # image, which is filesystem work, not a pure function.
                from hal0.providers.container import resolved_command_for_slot

                entry["resolved_command"] = await loop.run_in_executor(
                    None, resolved_command_for_slot, cfg
                )
            except Exception:
                entry["image"] = None
                entry["resolved_command"] = None
        else:
            entry["image"] = None
            entry["resolved_command"] = None

        # #663: deterministic backend-of-record - the running container's image
        # IS the backend. Surface actual_image (via podman inspect) and compute
        # image_mismatch against the slot's declared profile image. Replaces the
        # fragile /proc actual_backend sniff. Degrades silently - never 500
        # the hot path.
        # Only a live container can have a running image — skip the podman
        # inspect for stopped/crashed slots entirely. On a box where most
        # slots are offline this was the bulk of the probe's subprocess
        # spawns, and its answer was always None (#1507 latency follow-up).
        running_image = None
        if container_status in ("running", "starting"):
            try:
                from hal0.providers.container import _image_mismatch

                cp = _resolve_container_provider(provider)
                # By CONFIG: the container is named after the instance token
                # (#1417) — a bare name inspected a container that never exists on
                # an id-keyed box, leaving the image backend-of-record inert.
                running_image = await loop.run_in_executor(None, cp.running_image, cfg)
            except Exception:
                running_image = None
        if running_image:
            entry["actual_image"] = running_image
            if image:
                entry["image_mismatch"] = _image_mismatch(running_image, image)

        # image_status: present | pulling | missing | not-configured
        # Check the pull-jobs registry first so an in-flight pull
        # surfaces as "pulling" without an extra inspect syscall.
        active_job = jobs.get(name)
        if active_job is not None and getattr(active_job, "state", None) == "pulling":
            entry["image_status"] = "pulling"
        elif image:
            try:
                cp = _resolve_container_provider(provider)
                present = await loop.run_in_executor(None, _image_present_cached, cp, image)
                entry["image_status"] = "present" if present else "missing"
            except Exception:
                entry["image_status"] = "missing"
        else:
            # No profile/image declared — the slot runs on the default toolbox.
            # "No expected image" is NOT a missing-image fault (#1226): report
            # it distinctly so operators (and the hal0-brain steward) don't
            # learn to ignore a real "missing" every 30s.
            entry["image_status"] = "not-configured"

        return name, entry

    async def _bounded(name: str, cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        # Acquire FIRST, then start the clock — a slot queued behind the
        # semaphore must not spend its deadline waiting for a turn.
        async with sem:
            try:
                return await asyncio.wait_for(_one(name, cfg), timeout=_PROBE_TIMEOUT_S)
            except TimeoutError:
                log.warning("slot_view.container_probe_timeout slot=%s", name)
                return name, {
                    "container_status": "stopped",
                    "container_health": False,
                    "runtime": "container",
                    "profile": str(cfg.get("profile") or ""),
                    "image": None,
                    "resolved_command": None,
                    "image_status": "not-configured",
                }

    out: dict[str, dict[str, Any]] = {}
    for item in await asyncio.gather(
        *(_bounded(name, cfg) for name, cfg in named), return_exceptions=True
    ):
        if isinstance(item, BaseException):
            continue
        name, entry = item
        out[name] = entry

    # #733: trio shadow slots (embed/stt — device=npu, non-llm) never run a
    # unit or container of their own; the npu anchor's FLM child serves them
    # coresident. Their own probe always reads "stopped", so inherit the
    # anchor's live status instead and mark the relationship for the UI.
    anchor_cfg = next((c for c in configs if claims_npu_anchor(c)), None)
    if anchor_cfg is not None:
        anchor_name = str(anchor_cfg.get("name", ""))
        anchor_entry = out.get(anchor_name)
        if anchor_entry is not None:
            for cfg in configs:
                name = str(cfg.get("name", ""))
                if (
                    name
                    and name != anchor_name
                    and cfg.get("device") == "npu"
                    and cfg.get("type") != "llm"
                    and name in out
                ):
                    out[name]["container_status"] = anchor_entry["container_status"]
                    out[name]["container_health"] = anchor_entry["container_health"]
                    out[name]["served_by"] = anchor_name
    return out


# ── synthetic upstream entries ───────────────────────────────────────────────


def synthesize_upstream_entries(
    upstreams: Any,
    model_cache: dict[str, Any],
    last_used_model: dict[str, str],
    loaded_models: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build virtual slot entries from configured upstreams.

    Until every upstream has a corresponding local slot, the dashboard
    still needs to show remote-backed inference targets. Each upstream
    surfaces as a read-only slot entry. ``status`` is computed by kind:

      * local composite (``kind="slot"``) — ``serving`` only when one of
        the upstream's advertised models appears in the live loaded
        set (``loaded_models``). The catalogue cache lists every configured
        chat model, so it is NOT a liveness signal; consulting the loaded
        set is what keeps the dashboard from showing evicted models as
        resident. Falls back to the catalogue heuristic only when health
        was unreadable (``loaded_models is None``).
      * remote (``kind="remote"``) — ``serving`` when its model cache is
        populated, since that cache is a live ``/v1/models`` probe of the
        remote. ``offline`` otherwise.

    The slot's ``model`` reflects the most recently dispatched model id
    for this upstream (tracked in ``last_used_model``); falls back to
    the first non-alias from the catalog before any inference has
    happened.

    ``upstreams`` is whatever the caller passes — real callers (see
    ``hal0.api.routes.slots._synthesize_slots_from_upstreams``) fold the
    direct-read composite catalogue entry into the list before calling
    this function, so it needs no hal0-specific special-casing here.
    """
    out: list[dict[str, Any]] = []
    for u in upstreams.list():
        models = model_cache.get(u.name, [])
        from hal0.api.routes.models import _is_alias  # local to avoid cycle

        real_models = [m for m in models if not _is_alias(m)]
        primary_model = (
            last_used_model.get(u.name)
            or (real_models[0] if real_models else "")
            or (models[0] if models else "")
        )
        if u.kind == "slot":
            # Local composite upstream: ``models`` comes from the slot
            # CATALOGUE (config), so a non-empty list says nothing about
            # what is resident. Truth comes from the live loaded set.
            # If health was unreadable (loaded_models is None) fall back to
            # the catalogue heuristic rather than flapping to offline on a
            # transient probe error.
            serving = bool(models) if loaded_models is None else bool(set(models) & loaded_models)
        else:
            # Remote upstream: ``models`` is a live /v1/models probe of the
            # remote, so a populated list is a genuine liveness signal.
            serving = bool(models)
        out.append(
            {
                "name": u.name,
                "kind": u.kind,
                "model": primary_model,
                "status": "serving" if serving else "offline",
                "backend": "remote" if u.kind == "remote" else "vulkan",
                "provider": "remote-upstream" if u.kind == "remote" else "llama-server",
                "url": u.url,
                "advertised_models": len(models),
                "last_used_model": last_used_model.get(u.name) or None,
                "_synthetic": True,
                "_synthetic_reason": (
                    "Backed by remote upstream; install a local slot of the same name to take over."
                ),
            }
        )
    return out


# ── the aggregator ───────────────────────────────────────────────────────────


class SlotViewAggregator:
    """Eagerly compose the full ``/api/slots`` read model.

    Constructor deps mirror what the route used to pull off
    ``request.app.state`` (plus the two provider seams), so tests inject
    fakes instead of crossing HTTP:

      - ``slot_manager`` — ``list()`` + ``iter_configs()``.
      - ``registry`` — model registry handed to the capacity probe for
        memory attribution (``app.state.model_registry``).
      - ``metrics`` — async zero-arg callable returning the raw per-slot
        metrics rows (the route passes a bound ``slot_metrics`` call).
      - ``container_provider`` — duck-typed ContainerProvider for the
        container probe; defaults to the real one, lazily.
      - ``model_cache`` / ``upstreams`` / ``last_used_model`` /
        ``slot_pull_jobs`` — the remaining ``app.state`` reads
        (serialization's multi-model list, synthetic upstream entries,
        and the in-flight image-pull registry).

    ``snapshot()`` is **eager** — one computation, no per-concern
    composition methods, no ``include_*`` flags (single caller today;
    see module docstring).
    """

    def __init__(
        self,
        slot_manager: Any,
        registry: Any = None,
        metrics: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        *,
        container_provider: Any = None,
        model_cache: dict[str, Any] | None = None,
        upstreams: Any = None,
        last_used_model: dict[str, str] | None = None,
        slot_pull_jobs: dict[str, Any] | None = None,
    ) -> None:
        self._slot_manager = slot_manager
        self._registry = registry
        self._metrics = metrics
        self._container_provider = container_provider
        self._model_cache = model_cache if model_cache is not None else {}
        self._upstreams = upstreams
        self._last_used_model = last_used_model if last_used_model is not None else {}
        self._slot_pull_jobs = slot_pull_jobs if slot_pull_jobs is not None else {}

    # ── snapshot ─────────────────────────────────────────────────────────

    async def snapshot(self) -> list[SlotView]:
        """One eager pass over every concern; returns typed rows in wire order.

        Real SlotManager-backed entries first (manager order), then
        synthetic upstream-backed ones for upstreams without a local
        slot of the same name — real slots win on name collision.
        """
        real_slots = await self._slot_manager.list()
        payloads: list[dict[str, Any]] = [
            serialize_slot(s, model_cache=self._model_cache) for s in real_slots
        ]
        real_names = {str(p["name"]) for p in payloads}

        configs = await self._safe_configs()

        enrichment = config_enrichment(configs)
        # #1427: the container probe, the capacity probe and the metrics
        # fan-out are three independent IO waits over the same slot set —
        # there is no data dependency between them, so awaiting them one
        # after another just added their latencies together on every
        # dashboard poll. Run them concurrently.
        c_enrichment, per_slot_mem, raw_metrics = await asyncio.gather(
            container_enrichment(
                configs,
                pull_jobs=self._slot_pull_jobs,
                provider=self._container_provider,
            ),
            self._safe_per_slot_mem(real_slots),
            self._safe_metrics(),
        )
        for payload in payloads:
            slot_name = str(payload["name"])
            extra = enrichment.get(slot_name)
            if extra:
                for k, v in extra.items():
                    payload.setdefault(k, v)
            c_extra = c_enrichment.get(slot_name)
            if c_extra:
                for k, v in c_extra.items():
                    payload.setdefault(k, v)

            # #1788: re-resolve ctx_max to the EFFECTIVE window (only when
            # there's a model bound, or an explicit raw ceiling was lifted —
            # a model-less slot with neither has nothing to resolve).
            raw_ctx_max = payload.get("ctx_max")
            model_id = str(payload.get("model_default") or "")
            if model_id or isinstance(raw_ctx_max, int):
                payload["ctx_max"] = _resolve_ctx_max(
                    raw_ctx_max if isinstance(raw_ctx_max, int) else None,
                    self._registry,
                    model_id,
                    slot_name,
                )

        # Per-slot resident memory (model weights + KV-cache estimate) so the
        # dashboard memory map (W4) attributes a real footprint per slot. Only
        # resident slots get a non-zero row; everything else reads 0. Never let
        # a memory-probe failure break the slots list. (Probed above, in the
        # concurrent gather.)
        mem_by_name: dict[str, float | int] = {}
        for payload in payloads:
            slot_name = str(payload["name"])
            row = per_slot_mem.get(slot_name)
            mem_by_name[slot_name] = round(float(row.get("mem_mb", 0) or 0), 1) if row else 0

        synthetic: list[dict[str, Any]] = []
        if self._upstreams is not None:
            synthetic = synthesize_upstream_entries(
                self._upstreams,
                self._model_cache,
                self._last_used_model,
                loaded_models=loaded_model_names_from_slots(real_slots),
            )

        views: list[SlotView] = []
        for payload in payloads:
            slot_name = str(payload.get("name"))
            mem_mb = mem_by_name.get(slot_name, 0)
            views.append(
                SlotView(
                    name=slot_name,
                    kind=str(payload.get("kind", "")),
                    status=str(payload.get("status", "")),
                    synthetic=False,
                    mem_mb=mem_mb,
                    metrics=_metrics_view(raw_metrics, slot_name, mem_mb),
                    payload=payload,
                )
            )
        for entry in synthetic:
            if entry["name"] in real_names:
                continue
            slot_name = str(entry.get("name"))
            views.append(
                SlotView(
                    name=slot_name,
                    kind=str(entry.get("kind", "")),
                    status=str(entry.get("status", "")),
                    synthetic=True,
                    # Synthetic entries have no local cgroup — the legacy
                    # response omits mem_mb there entirely.
                    mem_mb=None,
                    metrics=_metrics_view(raw_metrics, slot_name, 0),
                    payload=entry,
                )
            )
        return views

    # ── never-raise dependency wrappers ──────────────────────────────────

    async def _safe_configs(self) -> list[dict[str, Any]]:
        try:
            return list(await self._slot_manager.iter_configs())
        except Exception:
            return []

    async def _safe_per_slot_mem(self, real_slots: list[Any]) -> dict[str, Any]:
        try:
            from hal0.slots import capacity

            return await capacity.build_per_slot(real_slots, registry=self._registry)
        except Exception:
            return {}

    async def _safe_metrics(self) -> dict[str, Any]:
        if self._metrics is None:
            return {}
        try:
            raw = await self._metrics()
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}


def _metrics_view(raw_metrics: dict[str, Any], name: str, mem_mb: float | int) -> SlotMetricsView:
    """Remap one raw metrics row to the card-expected shape (#26 / BE-METRICS).

    The dashboard reads ``slot.metrics.{toks,ttft,ctx,kv,mem}``. Absent
    rows leave the frontend's zero/null defaults in place.
    """
    rm = raw_metrics.get(name) or {}
    kv = rm.get("kv_cache_usage")
    ttft_s = rm.get("ttft_seconds")
    return SlotMetricsView(
        toks=round(float(rm.get("tokens_per_sec") or 0), 1),
        ttft=round(float(ttft_s) * 1000) if ttft_s else None,
        ctx=int(rm.get("ctx") or 0),
        kv=round(float(kv) * 100, 1) if kv is not None else None,
        mem=round(float(mem_mb or 0) / 1024.0, 2),
    )
