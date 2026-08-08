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
        # Legacy TOMLs may carry only ``backend``; promote it the same way
        # SlotConfig._promote_backend_to_device would, so we still emit the
        # device-derived token rather than the raw legacy string.
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


def _base_profile_for_backend(catalog: Any, backend: str) -> str:
    """Pick the canonical (non-MTP) seed profile name for a GPU backend.

    Prefers the seed profile named after the backend (``rocm`` / ``vulkan``);
    falls back to any non-MTP then any profile that declares ``backend``.

    This is the deliberate *non-MTP* counterpart of
    :func:`hal0.install.profile_derive.derive_profile`'s ``rocm-dnse``
    preference: it answers backend→base-profile from the live catalog so a
    drawer device-flip re-derives a plain base image (``rocm``/``vulkan``) and
    never silently switches a slot onto the MTP ``rocm-dnse`` image. Do NOT
    fold it into the device→profile helper (finding PS-4).
    """
    if "chat" in catalog.profile:
        return "chat"
    for name, prof in catalog.profile.items():
        if getattr(prof, "backend", None) == backend and not getattr(prof, "mtp", False):
            return str(name)
    for name, prof in catalog.profile.items():
        if getattr(prof, "backend", None) == backend:
            return str(name)
    return backend


def _reconcile_device_profile(cfg_dict: dict[str, Any], changed: set[str]) -> None:
    """Keep a GPU slot's ``device`` and ``profile.backend`` coherent in place.

    A GPU slot implies its backend twice: ``device`` (``gpu-rocm`` /
    ``gpu-vulkan``) drives the llama-server backend, while ``profile`` selects
    the container image + flags. They must agree — a vulkan device under a
    rocm-dnse profile launches a Vulkan binary with ROCm-only MTP draft flags
    (issue: utility slot). The field the operator changed wins; the stale side
    is re-derived. Both changed to conflicting backends → operator error.

    No-ops for slots without a GPU profile (npu/cpu/img profiles declare
    ``backend=None``) and for ``auto`` device (empty) unless the profile
    itself changed. Mutates ``cfg_dict`` in place.
    """
    profile_name = cfg_dict.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        return

    from hal0.config.loader import load_profiles_config

    prof = load_profiles_config().profile.get(profile_name)
    prof_backend = getattr(prof, "backend", None) if prof is not None else None
    if not prof_backend:
        # Non-GPU profile (or unknown profile with no backend) — leave alone.
        return

    from hal0.config.schema import map_backend_to_device
    from hal0.model_meta import device_to_backend

    device = cfg_dict.get("device")
    dev_backend = device_to_backend(str(device))[1] if device else None
    if dev_backend == prof_backend:
        return  # already coherent

    prof_changed = "profile" in changed
    dev_changed = "device" in changed

    if not device:
        # ``auto``/unset device: only adopt the profile's backend when the
        # operator explicitly (re)selected the profile; otherwise leave auto.
        if prof_changed:
            cfg_dict["device"] = map_backend_to_device(prof_backend)
        return

    if prof_changed and not dev_changed:
        cfg_dict["device"] = map_backend_to_device(prof_backend)
    elif dev_changed and not prof_changed and dev_backend is not None:
        catalog = load_profiles_config()
        cfg_dict["profile"] = _base_profile_for_backend(catalog, dev_backend)
    elif prof_changed and dev_changed:
        raise SlotConfigError(
            f"slot device {device!r} (backend {dev_backend!r}) conflicts with "
            f"profile {profile_name!r} (backend {prof_backend!r}); "
            "pick a device and profile with the same backend",
            details={
                "device": device,
                "profile": profile_name,
                "device_backend": dev_backend,
                "profile_backend": prof_backend,
            },
        )
    # neither changed (pre-existing on-disk drift surfaced by an unrelated
    # update): leave both fields untouched so the unrelated edit doesn't
    # silently mutate hardware intent. Drift heals on the next device/profile
    # edit.


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
        label = display if isinstance(display, str) and display and not display.isdigit() else path.stem
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


def _screen_slot_extra_args(updates: dict[str, Any]) -> None:
    """Reject hal0-owned flags in a slot write's ``[server].extra_args``.

    The slot counterpart of the model
    (:func:`hal0.services.models_service.screen_model_write`) and profile
    (``api.routes.profiles._screen_profile_flags``) freeform-flag screens. Those
    two live at their HTTP route layers, so the slot surface had NO screen at
    all on any in-process writer: the stacks apply engine (and stack
    create-on-apply) hand ``[server].extra_args`` straight to slot TOML, and
    ``SlotManager.create`` never screened either. The launch path only screens
    against :data:`~hal0.slots.argv.MANAGED_ARGS_DENYLIST` — and only at load
    time, inside :func:`hal0.slots.argv.resolve_argv` — so a hardware flag
    persisted here was never caught at all.

    Hardware flags are checked FIRST so ``-ngl``/``-dev``/``--threads`` get the
    actionable "belongs on the slot's hardware grid" message rather than the
    generic managed-arg one (``-ngl`` is in both sets) — the same ordering
    ``screen_model_write`` and ``_screen_profile_flags`` use.

    No-op when the payload carries no ``[server].extra_args`` or it is blank.
    Unparseable quoting is deferred to the schema/TOML layer rather than masked
    with a partition message (mirrors ``_screen_profile_flags``).

    Raises:
        hal0.errors.BadRequest: ``slot.hardware_flag_denied`` /
            ``slot.managed_arg_denied``.
    """
    server = updates.get("server")
    if not isinstance(server, dict):
        return
    raw = server.get("extra_args")
    if not isinstance(raw, str) or not raw.strip():
        return

    import shlex

    from hal0.slots.argv import _deny_managed_flags, _deny_slot_hardware_flags

    segment = "slot [server].extra_args"
    try:
        # Strip a trailing backslash that would make shlex.split() raise
        # "No escaped character" (a common copy-paste artefact), same as
        # models_service.screen_model_write.
        tokens = shlex.split(raw.rstrip().rstrip("\\"))
    except ValueError:
        return
    _deny_slot_hardware_flags(tokens, segment=segment)
    _deny_managed_flags(tokens, segment=segment)


def guard_slot_write_payload(updates: dict[str, Any]) -> None:
    """Run the slot-write PARTITION boundary over an in-process write payload.

    The three key-space partitions the HTTP slot-config routes enforce
    (``api.routes.slots._reject_model_owned_config_keys`` +
    ``_reject_unknown_config_keys`` → ``reject_removed_slot_keys``), plus the
    freeform-flag screen, applied to a raw ``updates`` dict so an IN-PROCESS
    writer that never passes through a FastAPI handler is held to the same
    contract:

      - :func:`~hal0.slot_config.reject_model_owned_slot_keys` —
        ``mtp``/``enable_thinking``/``vision`` belong on the MODEL
        (spec-hw-slot-ownership §1). ``SlotConfig`` is ``extra="allow"``, so
        without this an in-process writer silently persists the pre-partition
        on-disk shape that ``PUT /api/slots/{name}/config`` 400s on.
      - :func:`~hal0.slot_config.reject_removed_slot_keys` — ``enabled`` and
        friends (#1369) round-trip as inert debris while the caller believes
        the setting took.
      - :func:`_screen_slot_extra_args` — hardware / hal0-managed flags in
        freeform ``[server].extra_args``.

    Checked against ``updates`` (the write payload), NEVER the merged result:
    an old pre-partition key already sitting on disk must not block an
    unrelated legitimate edit to that slot. Converging the old shape is the
    migration's job
    (``config.migrations.model_owned_caps``), not this guard's.

    Raises:
        hal0.errors.BadRequest: the payload crosses a partition.
    """
    from hal0.slot_config import reject_model_owned_slot_keys, reject_removed_slot_keys

    reject_model_owned_slot_keys(updates)
    reject_removed_slot_keys(updates)
    _screen_slot_extra_args(updates)


def reconcile_slot_updates(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Normalize + merge ``updates`` onto ``base`` and keep device/profile coherent.

    The write-side projection shared by ``SlotManager.update_config`` and the
    stacks apply engine: the copy-safe one-level merge + #585 ctx_size fold
    (:func:`hal0.slot_config.merge_slot_config`) followed by
    :func:`_reconcile_device_profile` driven by exactly the keys the caller
    changed. Returns a fresh dict; ``base`` is never mutated.
    """
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
    model-owned ``mtp``/``enable_thinking``/``vision`` (and unscreened
    ``[server].extra_args``) on an existing slot's TOML that
    ``PUT /api/slots/{name}/config`` 400s on. The guard lives HERE, at the one
    shared in-process guarded write path, rather than at the stacks call site,
    so every future caller inherits it — this function's whole reason to exist
    is "a writer can no longer persist what ``update_config`` would refuse".

    Raises:
        hal0.errors.BadRequest: ``updates`` crosses the slot/model partition.
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
    "_base_profile_for_backend",
    "_cfg_effective_backend",
    "_iter_peer_configs",
    "_read_slot_toml_dict",
    "_reconcile_device_profile",
    "_screen_slot_extra_args",
    "check_default_uniqueness",
    "check_npu_exclusivity",
    "guard_slot_write_payload",
    "reconcile_and_guard_slot_config",
    "reconcile_slot_updates",
]
