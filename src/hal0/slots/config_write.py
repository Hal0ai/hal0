"""Shared slot-config write pipeline (guards for every writer).

``SlotManager.update_config``, ``SlotManager.create``, and the stacks apply
engine (:mod:`hal0.stacks.apply`) all project "partial updates onto an
existing slot config". Historically only ``update_config``/``create`` ran
the full guard pipeline; the stacks engine hand-rolled its own merge and
could persist a vulkan-device+rocm-profile incoherence or a second NPU
anchor. These module-level, synchronous functions are the ONE pipeline
every writer calls.

P3-slots §1f extraction: moved verbatim out of ``hal0.slots.manager``
(was module scope there too). ``hal0.slots.manager`` re-exports every name
here (P3-slots §5 contract) — ``stacks/apply.py`` and
``slot_view/__init__.py`` keep importing from ``hal0.slots.manager``
unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.slot_config import merge_slot_config
from hal0.slots._cfg_helpers import _cfg_to_dict
from hal0.slots.activation import claims_npu_anchor as _claims_npu_anchor
from hal0.slots.state import NpuExclusivityViolation, SlotConfigError

# ── device/profile coherence ────────────────────────────────────────────────


def _cfg_effective_backend(cfg: Any) -> str | None:
    """Derive the EFFECTIVE runtime backend token from a slot config.

    W3 truth fix: ``device`` is the authoritative hardware-intent
    field. The dashboard's SlotCard backend chip must reflect what
    ``device`` will run — NOT the legacy, never-resynced ``backend``
    TOML field which drifts the moment a user flips backend (which only
    rewrites ``device``).

    Returns the normalized token ``rocm`` | ``vulkan`` | ``cpu`` |
    ``flm`` (NPU → ``flm``), or ``None`` when neither ``device`` nor a
    legacy ``backend`` is set so callers can fall through to "unknown".
    Pure/synchronous — safe on the status hot path.
    """
    d = _cfg_to_dict(cfg)
    device = d.get("device")
    if not device:
        # An unconverted TOML may carry only ``backend`` (the profile-centric
        # conversion rewrites it to ``device``); map it so a raw reader still
        # emits the device-derived token rather than the raw legacy string.
        legacy = d.get("backend")
        if not legacy:
            return None
        from hal0.config.schema import map_backend_to_device

        device = map_backend_to_device(str(legacy))
    # Reuse the single device→(recipe, llamacpp_backend) mapping so the
    # displayed token can never diverge from what the load path derives.
    from hal0.model_meta import device_to_backend

    recipe, llamacpp_backend = device_to_backend(str(device))
    # NPU → recipe="flm" with no llamacpp_backend; surface "flm".
    return llamacpp_backend or (recipe if recipe == "flm" else None)


def _cfg_effective_provider(cfg: Any) -> str | None:
    """The provider that actually launches a slot, from its profile (ADR-0029).

    A slot's runtime comes from its profile's runtime family, else the
    launch dispatch's device/type fallbacks (:func:`hal0.providers.container.
    _spec_provider_for` — the exact function the launch uses), so the
    reported provider can never disagree with what runs. ``llama-server``
    is the dispatch's own answer when no single-purpose engine claims the
    slot, never a default stamped over a Kokoro/Moonshine/ComfyUI slot.

    An explicit ``provider`` key is consulted only when the dispatch itself
    fails (it never raises for a missing profile); ``None`` when nothing is
    known, so ``provider_requires_model`` stays permissive.
    """
    d = _cfg_to_dict(cfg)
    flat = dict(d)
    nested = d.get("slot")
    if isinstance(nested, dict):
        # Nested on-disk shape: [slot] is authoritative for slot scalars.
        flat.update(nested)
    try:
        from hal0.providers.container import _spec_provider_for

        provider = _spec_provider_for(flat)
    except Exception:
        explicit = flat.get("provider")
        return str(explicit) if isinstance(explicit, str) and explicit else None
    return str(provider.name) if provider is not None else "llama-server"


def _reconcile_device_profile(cfg_dict: dict[str, Any], changed: set[str]) -> None:
    """Apply a runner-carrying profile's runtime to the slot (profile-carried runtime).

    When the operator (re)selects a profile that names a ``runner``, the slot's
    ``binary`` becomes that runner and, for a single-backend runner, its
    ``device`` follows (a ROCm-only runner on a host with no ROCm lane is
    refused before any write). A profile without a runner selects no
    hardware (ADR-0029) and leaves the slot alone. Mutates ``cfg_dict`` in
    place — through
    :func:`hal0.slot_config.slot_scalar_table` (#1685), since ``device``/
    ``profile`` are scalar fields that live under ``[slot]`` on a
    nested-shape config, not at ``cfg_dict``'s root.
    """
    from hal0.slot_config import slot_scalar_table

    scalars = slot_scalar_table(cfg_dict)
    profile_name = scalars.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        return

    from hal0.config.loader import load_profiles_config

    prof = load_profiles_config().profile.get(profile_name)

    from hal0.config.schema import map_backend_to_device

    prof_runner = getattr(prof, "runner", None) if prof is not None else None
    if prof_runner and "profile" in changed:
        from hal0.runners import get_runner

        key = prof_runner
        try:
            runner = get_runner(key)
        except Exception:
            return  # unknown key in a hand-edited profiles.toml: leave slot alone
        sups = runner.supported_backends
        new_device = None
        if len(sups) == 1:
            lane = sups[0]
            if lane == "rocm":
                # Physical feasibility gate (D4): a ROCm-only runtime on a
                # host without the ROCm lane can never spawn — fail the apply
                # with a plain message instead of writing a doomed config.
                # The lane is /dev/kfd AND a render node (#2313): the picker
                # hides ROCm-only rows on a kfd-only box (#2354), so the apply
                # must not accept one there either. Vulkan-lane feasibility
                # stays with the existing spawn-time Vulkan-gated-pin check
                # (hal0.runners.pins.vulkan_capable_refs). Checked BEFORE any mutation so an infeasible apply
                # leaves ``scalars`` untouched (no partial write).
                from hal0.providers._gpu import rocm_lane_present

                if not rocm_lane_present():
                    raise SlotConfigError(
                        f"profile {profile_name!r} needs the {runner.title or key} "
                        "runtime (ROCm), but this host has no /dev/kfd with a "
                        "/dev/dri/renderD* render node — "
                        "pick a Vulkan-capable runtime or profile",
                        details={"profile": profile_name, "runner": key, "lane": lane},
                    )
                new_device = map_backend_to_device(lane)
            elif lane in ("vulkan", "cuda"):
                new_device = map_backend_to_device(lane)
            elif runner.device_class == "cpu":
                new_device = "cpu"
        # Every write below happens only after the feasibility gate above has
        # already passed (or there was nothing to gate) — no partial write.
        scalars["binary"] = key
        if new_device is not None:
            scalars["device"] = new_device
        return


# ── guard/merge pipeline (shared with stacks/apply.py) ──────────────────────


def _read_slot_toml_dict(path: Path) -> dict[str, Any] | None:
    """Best-effort raw read of one ``slots/*.toml`` (with the [slot] hoist).

    Returns ``None`` on a missing or malformed file — guard peer-walks skip
    those rather than blocking the caller's legitimate write (the malformed
    slot surfaces its own error on its own paths).
    """
    import tomllib

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    slot_tbl = data.pop("slot", None)
    if isinstance(slot_tbl, dict):
        for k, v in slot_tbl.items():
            data[k] = v
    return data


def _iter_peer_configs(
    slot_name: str, slots_dir: Path | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """(name, cfg) for every readable configured slot other than ``slot_name``.

    ``slot_name`` is the DISPLAY name. On an id-keyed box (#1569) the file
    stem is the slot's numeric id and the display name lives in the body, so
    exclusion must also match the embedded name — keyed on stems alone, the
    slot's own file (``flm`` stored as ``8.toml``) counted as a peer and the
    cross-slot guards vetoed every write against the slot itself. Peers are
    likewise reported by their embedded display name when one exists.
    """
    base = Path(slots_dir) if slots_dir is not None else paths.slots_config_dir()
    if not base.is_dir():
        return []
    peers: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(base.glob("*.toml")):
        if path.stem == slot_name:
            continue
        peer = _read_slot_toml_dict(path)
        if peer is None:
            continue
        display = peer.get("name")
        if display == slot_name:
            continue
        label = (
            display if isinstance(display, str) and display and not display.isdigit() else path.stem
        )
        peers.append((label, peer))
    return peers


def check_npu_exclusivity(
    slot_name: str,
    cfg_dict: dict[str, Any],
    *,
    slots_dir: Path | None = None,
) -> None:
    """Reject a write that would land a second model-bound NPU LLM anchor.

    Sync core of :meth:`SlotManager._check_npu_exclusivity` (see its
    docstring for the full contract), shared with the stacks apply engine.
    ``slots_dir`` overrides the default config dir for engines constructed
    against a custom directory (tests).

    A slot claims the AMDXDNA chat context when it is ``device=npu,
    type=llm`` AND has a non-empty ``[model].default`` — model-presence is
    the activation signal since #1369, so a model-less NPU LLM slot is inert
    config that may coexist and the model write is what gets refused.
    """
    if not _claims_npu_anchor(cfg_dict):
        return
    offenders = [
        name for name, peer in _iter_peer_configs(slot_name, slots_dir) if _claims_npu_anchor(peer)
    ]
    if offenders:
        raise NpuExclusivityViolation(
            "only one NPU LLM slot may have a model configured at a time "
            f"(slot {slot_name!r} would conflict with {offenders[0]!r})",
            details={
                "slot": slot_name,
                "conflicting_slots": sorted(offenders),
                "hint": "clear the existing NPU LLM slot's model before configuring another",
            },
        )


def check_default_uniqueness(
    slot_name: str,
    cfg_dict: dict[str, Any],
    *,
    slots_dir: Path | None = None,
    changed_keys: set[str] | None = None,
) -> None:
    """Reject a write that would land a second ``default=true`` per type.

    Sync core of :meth:`SlotManager._check_default_uniqueness` (see its
    docstring for the full contract), shared with the stacks apply engine.

    ``changed_keys`` is the set of keys this write actually touches. When it is
    supplied and does NOT contain ``"default"``, the check is skipped outright:
    the write isn't moving the invariant, so a pre-existing pair of stale
    ``default=true`` peers on disk is not this write's problem to fix — and
    must not veto it. (Without this, a plain ``update_config("b",
    {"n_gpu_layers": 40})`` on a slot that merely *happens* to carry
    ``default=true`` in an already-violated state gets rejected for a conflict
    it did not create.) ``None`` — the default — keeps the historical behaviour
    of validating the full merged dict, which is what ``create()`` wants: there
    every key is new.
    """
    if changed_keys is not None and "default" not in changed_keys:
        return
    if cfg_dict.get("default") is not True:
        return
    type_ = cfg_dict.get("type")
    if not type_:
        return
    offenders = [
        name
        for name, peer in _iter_peer_configs(slot_name, slots_dir)
        if peer.get("type") == type_ and peer.get("default") is True
    ]
    if offenders:
        raise SlotConfigError(
            f"slot type {type_!r} already has a default=true slot "
            f"(slot {slot_name!r} would conflict with {offenders[0]!r})",
            details={
                "slot": slot_name,
                "type": type_,
                "conflicting_slots": sorted(offenders),
                "hint": "clear the existing default before setting another",
            },
        )


# ── model ↔ engine fit ──────────────────────────────────────────────────────


def slot_is_flm(cfg: Any) -> bool:
    """True when the slot drives the NPU / FastFlowLM engine.

    Read off the slot's own scalars (``device`` normalised through
    ``BACKEND_TO_DEVICE`` so an old ``backend``-era token still counts, or an
    explicit ``provider = "flm"``) rather than a profile lookup: this runs on
    the write path, which must not resolve (and possibly persist) a profile.
    """
    from hal0.model_meta import BACKEND_TO_DEVICE
    from hal0.slot_config import slot_scalar_table

    scalars = slot_scalar_table(_cfg_to_dict(cfg))
    device = str(scalars.get("device") or "").strip().lower()
    if BACKEND_TO_DEVICE.get(device, device) == "npu":
        return True
    if str(scalars.get("provider") or "").strip().lower() == "flm":
        return True
    return _profile_is_flm(scalars.get("profile"))


def _profile_is_flm(profile_name: Any) -> bool:
    """Whether the slot's profile belongs to the FLM runtime family (read-only).

    The launch path picks FastFlowLM from the profile's runtime family even
    with no ``device``/``provider`` on the slot (``_spec_provider_for``), so
    the fit check must too. Reads ``profiles.toml`` directly, never
    ``ProfileCatalog.resolve`` (which can persist a shipped seed): an unseeded
    ``flm`` profile is recognised by name, as ``_runtime_family`` does.
    """
    if not isinstance(profile_name, str) or not profile_name:
        return False
    try:
        from hal0.config.loader import load_profiles_config
        from hal0.profiles import runtime_family_of

        prof = load_profiles_config().profile.get(profile_name)
        if prof is None:
            return profile_name == "flm"
        return runtime_family_of(profile_name, prof) == "flm"
    except Exception:
        return False


def model_is_flm(model_id: str, registry: Any) -> bool | None:
    """Whether ``model_id`` is an FLM (NPU) model; ``None`` when unknown.

    Registry rows answer through the same ``provider`` -> backend-tag
    derivation ``/api/models`` serves as ``provider_effective``. FLM-owned
    ``<tag>-FLM`` ids that are installed host-side (never registry rows) are
    FLM too. An id neither source knows is *unknown*, not "not FLM": the guard
    only refuses a positively identified mismatch.
    """
    from hal0.model_meta import derive_model_provider

    if registry is not None:
        try:
            model = registry.get(model_id)
        except Exception:
            model = None
        if model is not None:
            provider = getattr(model, "provider", None) or derive_model_provider(
                getattr(model, "backends", None)
            )
            return provider == "flm"
    from hal0.providers.flm import is_installed_flm_id

    return True if is_installed_flm_id(model_id) else None


def check_model_fits_slot(slot_name: str, cfg: Any, model_id: str | None, registry: Any) -> None:
    """Refuse binding an FLM model to a non-FLM slot, or a non-FLM one to an FLM slot.

    FLM models run only on the NPU (``flm`` runner, format ``flm``); the GPU/CPU
    llama.cpp runners (format ``gguf``) cannot load them, and an FLM slot cannot
    serve a GGUF. Without this the bind is accepted and the slot crash-loops at
    load. ``cfg`` is the slot's config AFTER the write (merged), so a write that
    changes ``device`` and ``model`` together is judged on the final pair.
    Unknown models pass (see :func:`model_is_flm`).

    Raises:
        SlotConfigError: a positively identified engine mismatch (HTTP 400,
            code ``slot.model_engine_mismatch``).
    """
    if not model_id:
        return
    flm_model = model_is_flm(str(model_id), registry)
    if flm_model is None:
        return
    flm_slot = slot_is_flm(cfg)
    if flm_model == flm_slot:
        return
    if flm_model:
        msg = (
            f"model {model_id!r} is an FLM (NPU) model and cannot run on slot "
            f"{slot_name!r}, which is not an NPU slot"
        )
        hint = "pick a GGUF model for this slot, or bind the FLM model to an NPU slot"
    else:
        msg = f"model {model_id!r} is not an FLM model and cannot run on NPU slot {slot_name!r}"
        hint = "pick an FLM model for this NPU slot"
    raise SlotConfigError(
        msg,
        details={"slot": slot_name, "model_id": str(model_id), "hint": hint},
        code="slot.model_engine_mismatch",
    )


def guard_slot_write_payload(updates: dict[str, Any]) -> None:
    """Run the slot-write PARTITION boundary over an in-process write payload.

    The key-space partitions the HTTP slot-config routes enforce
    (``api.routes.slots._reject_slot_tuning_keys`` +
    ``_reject_unknown_config_keys`` → ``reject_removed_slot_keys``), applied
    to a raw ``updates`` dict so an IN-PROCESS writer that never passes
    through a FastAPI handler is held to the same contract:

      - :func:`~hal0.slot_config.reject_slot_tuning_keys` — every tune
        (context window, ``[server].extra_args``, ``parallel``,
        ``chat_template``, ``mtp``, ``enable_thinking``, ``vision``,
        ``workers``) belongs on the slot's profile (ADR-0029 §5). Without
        this an in-process writer would persist a file that then fails to
        load as ``slot.unconverted``.
      - :func:`~hal0.slot_config.reject_unknown_slot_keys` — a key
        ``SlotConfig`` does not declare (``extra="forbid"``) would fail the
        slot's next load, and a key hal0 removed (``enabled``, #1369) would
        be inert debris while the caller believes the setting took. Removed
        keys are reported with their replacement.

    Checked against ``updates`` (the write payload), NEVER the merged result:
    converting a slot file is the one-shot converter's job
    (``hal0 profile migrate``), not this guard's.

    Raises:
        hal0.errors.BadRequest: the payload crosses a partition.
    """
    from hal0.slot_config import reject_slot_tuning_keys, reject_unknown_slot_keys

    reject_slot_tuning_keys(updates)
    reject_unknown_slot_keys(updates)


def reconcile_slot_updates(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Normalize + merge ``updates`` onto ``base`` and keep device/profile coherent.

    The write-side projection shared by ``SlotManager.update_config`` and the
    stacks apply engine: the copy-safe one-level merge
    (:func:`hal0.slot_config.merge_slot_config`) followed by
    :func:`_reconcile_device_profile` driven by exactly the keys the caller
    changed. Returns a fresh dict; ``base`` is never mutated.

    ADR-0029 §5: a tuning key in ``updates`` is refused here
    (``slot.tuning_key_denied``), so no in-process writer — including
    ``SlotManager.update_config`` — can persist a slot file that would then
    fail to load as ``slot.unconverted``.

    Raises:
        hal0.errors.BadRequest: ``updates`` carries a tuning key.
    """
    from hal0.slot_config import reject_slot_tuning_keys

    reject_slot_tuning_keys(updates)
    merged = merge_slot_config(base, updates)
    _reconcile_device_profile(merged, set(updates.keys()))
    return merged


def reconcile_and_guard_slot_config(
    slot_name: str,
    base: dict[str, Any],
    updates: dict[str, Any],
    *,
    slots_dir: Path | None = None,
) -> dict[str, Any]:
    """The full guarded write pipeline: partition + normalize + merge + guards.

    Raises :class:`SlotConfigError` / :class:`NpuExclusivityViolation` when the
    projected config is incoherent (conflicting device+profile backends) or
    violates a cross-slot invariant (second model-bound NPU anchor, second
    default=true of a type). Used by the stacks apply engine so a stack can no
    longer persist what ``update_config`` would have refused.

    Runs :func:`guard_slot_write_payload` FIRST so the key-space partition is
    enforced before any merge work: previously this pipeline checked only the
    two cross-slot invariants, so ``POST /api/stacks/{slug}/apply`` could land
    profile-owned ``mtp``/``enable_thinking``/``vision`` (and unscreened
    ``[server].extra_args``) on an existing slot's TOML that
    ``PUT /api/slots/{name}/config`` 400s on. The guard lives HERE, at the one
    shared in-process guarded write path, rather than at the stacks call site,
    so every future caller inherits it — this function's whole reason to exist
    is "a writer can no longer persist what ``update_config`` would refuse".

    Raises:
        hal0.errors.BadRequest: ``updates`` carries a key the slot does not own.
    """
    guard_slot_write_payload(updates)
    merged = reconcile_slot_updates(base, updates)
    check_npu_exclusivity(slot_name, merged, slots_dir=slots_dir)
    # Only the keys this apply actually carries drive the SC-4 guard — an
    # unrelated stack field must not be blocked by a stale duplicate-default
    # state it neither created nor touches.
    check_default_uniqueness(
        slot_name,
        merged,
        slots_dir=slots_dir,
        changed_keys=set(updates.keys()),
    )
    return merged


__all__ = [
    "_cfg_effective_backend",
    "_cfg_effective_provider",
    "_iter_peer_configs",
    "_read_slot_toml_dict",
    "_reconcile_device_profile",
    "check_default_uniqueness",
    "check_model_fits_slot",
    "check_npu_exclusivity",
    "guard_slot_write_payload",
    "model_is_flm",
    "reconcile_and_guard_slot_config",
    "reconcile_slot_updates",
    "slot_is_flm",
]
