"""NPU FLM-trio shadow reconciler (P3-slots §1d).

The NPU runs a single ``flm serve`` process (the ``device=npu type=llm``
anchor). That one process also serves transcription + embedding, which
surface as *shadow* slot records (``{anchor}-stt`` / ``{anchor}-embed``) so
they show on the slots page and gate trio dispatch. This module owns the
shadow predicate (:func:`is_npu_trio_shadow`) and the startup reconciler
(:func:`reconcile_trio_slots`) that keeps those shadow records coherent.

§23.2 seam note (plan §11.1): this whole parallel NPU-trio shadow lifecycle
is *interim* — a future uniform id-keyed slot lifecycle dissolves the shadow
path. Kept as one importable function (not a mixin) so that future work can
delete it cleanly.

``SlotManager`` keeps ``reconcile_npu_trio_slots`` as a public delegator
(called from ``api/__init__.py``); ``is_npu_trio_shadow`` is re-exported
from both ``hal0.slots.manager`` and ``hal0.slots`` (P3-slots §5) since it's
imported directly by ``tests/slots/test_npu_trio_shadow.py`` and referenced
from several core call sites (``load``, ``status``, ``compute_config_drift``,
``reconcile_container_upstreams``, ``_probe_health``).
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any, Protocol

from hal0.slot_config import write_slot_toml
from hal0.slots._cfg_helpers import _cfg_to_dict
from hal0.slots.layout import is_id_stem, resolve_slot_stem

if TYPE_CHECKING:
    from pathlib import Path

    from hal0.config.schema import SlotConfig

log = logging.getLogger(__name__)


def _shadow_path(slots_dir: Path, name: str) -> Path | None:
    """The on-disk TOML for a shadow addressed by display name, or ``None``.

    Bilingual (#1664): a name-keyed box resolves ``<name>.toml`` with a single
    ``exists()``; an id-keyed one falls back to the display-name index and
    resolves ``<id>.toml``. ``None`` means "this shadow does not exist" — which
    is the only signal that should reach the create branch.
    """
    stem = resolve_slot_stem(slots_dir, name)
    return None if stem is None else slots_dir / f"{stem}.toml"


def is_npu_trio_shadow(cfg: SlotConfig | dict[str, Any]) -> bool:
    """True if *cfg* is an NPU FLM trio **shadow** (stt/embed), not the anchor.

    The NPU runs a single FLM process — the chat anchor (``device=npu
    type=llm``) — which also serves transcription/embedding when the
    anchor's ``[npu]`` toggles are on. The ``stt``/``embed`` slots
    are therefore *shadows*: served by the anchor's process and NOT
    independently loadable. Issuing a standalone ``/v1/load`` for them on the
    busy single-tenant NPU returns HTTP 500, so callers skip the spawn and
    derive their state from the anchor. The anchor itself (``type=llm``) is
    deliberately excluded.
    """
    d = _cfg_to_dict(cfg)
    return d.get("device") == "npu" and d.get("type") in ("transcription", "embedding")


#: FLM-trio shadow spec: (name suffix, slot type, anchor ``[npu]`` toggle
#: key, default model id). Drives :func:`reconcile_trio_slots`.
# (name suffix, slot type, placeholder model id). The placeholder model is
# structural, not an operator choice: FLM has no per-role model selection, so
# the shadow's ``[model].default`` just names the bundled whisper / embed-gemma
# it will serve. Whether the modality actually runs is the ANCHOR's ``[npu]``
# table (hal0.slots.activation.NPU_MODALITY_KEY) — which is why the shadows
# never carried a meaningful activation state of their own (#1369).
_TRIO_SHADOW_SPEC: tuple[tuple[str, str, str], ...] = (
    ("stt", "transcription", "whisper-v3:turbo"),
    ("embed", "embedding", "embed-gemma:300m"),
)


class NpuTrioHost(Protocol):
    """Narrow seam :func:`reconcile_trio_slots` needs from ``SlotManager``."""

    async def iter_configs(self) -> list[dict[str, Any]]: ...
    async def create(self, slot_name: str, slot_cfg: dict[str, Any]) -> Any: ...
    async def rename(self, slot_name: str, new_name: str) -> Any: ...
    def _invalidate_cfg_cache(self, slot_name: str) -> None: ...


async def reconcile_trio_slots(mgr: NpuTrioHost) -> int:
    """Startup pass: reconcile the FLM-trio shadow slots to canon.

    The NPU runs one ``flm serve`` container (the ``device=npu type=llm``
    anchor, canonically ``flm``) that also serves transcription +
    embedding. Those two modalities surface as *shadow* slot records
    (``{anchor}-stt`` / ``{anchor}-embed``) so they show on the slots
    page and gate trio dispatch (:func:`hal0.api.routes.v1._is_npu_trio_request`).
    This pass keeps them coherent on every API start — for fresh installs
    (anchor present, shadows never seeded), existing installs (legacy
    names / drifted fields), and post-upgrade:

      1. **Legacy rename.** ``stt-npu`` / ``embed-npu`` TOMLs — the old
         naming that matched neither the occupancy pane's ``s.name+"-stt"``
         synthesis nor the ``_touch_npu_shadow_count`` counters, and that
         (ending ``-npu``, not ``-stt``/``-embed``) even leaked in as a
         standalone occupancy tile — are moved to ``{anchor}-stt`` /
         ``{anchor}-embed``. Skipped (and logged) when the canon target
         already exists, so operator state is never clobbered.
      2. **Ensure + normalize.** Each shadow is created if missing and its
         structural fields are forced to the coresident shape: ``device=npu``,
         ``profile=flm`` (so ``slot_view`` lifts ``device_class=npu`` — a
         shadow without a resolvable npu profile makes the edit drawer take
         the wrong branch, per the flm-satellite-slots-fix incident),
         ``served_by=<anchor>``, ``port=<anchor port>``, and the trio
         ``type``. A new shadow is seeded with the placeholder model for its
         modality; an existing shadow's ``model.default`` is the operator's
         and left untouched. Whether the modality dispatches is read off the
         ANCHOR's ``[npu]`` table at request time (#1369), so nothing here
         needs to mirror it.

    No-op when there is no container NPU anchor. Best-effort: per-shadow
    failures are logged and never block startup.

    Returns the number of shadow records created or rewritten.
    """
    import tomllib

    from hal0.config import paths
    from hal0.dispatcher._npu_common import is_container_npu_cfg

    # Locate the container NPU anchor (type=llm, device=npu). Mirrors
    # CapabilityOrchestrator._set_flm_modality's scan.
    try:
        configs = await mgr.iter_configs()
    except Exception as exc:
        log.warning("slot.reconcile_trio_iter_failed", extra={"error": str(exc)})
        return 0
    anchor_cfg: dict[str, Any] | None = None
    for cfg in configs:
        if cfg.get("type") == "llm" and cfg.get("device") == "npu":
            anchor_cfg = cfg
            break
    if anchor_cfg is None or not is_container_npu_cfg(anchor_cfg):
        return 0
    anchor_name = str(anchor_cfg.get("name", "")).strip()
    anchor_port = anchor_cfg.get("port")
    if not anchor_name or not anchor_port:
        return 0
    changed = 0
    slots_dir = paths.slots_config_dir()
    for suffix, slot_type, default_model in _TRIO_SHADOW_SPEC:
        canon = f"{anchor_name}-{suffix}"
        legacy = f"{suffix}-npu"  # stt-npu / embed-npu
        # Shadows are addressed by DISPLAY name, but the on-disk stem is this
        # box's storage detail: after ``hal0 slot migrate-id-keying`` the same
        # shadow lives at ``<id>.toml`` with the name in the body. Probing the
        # literal ``<name>.toml`` (#1664) missed it on every boot, so step 2
        # (structural normalization — the point of this routine) was skipped
        # and step 3 re-attempted a create that ``SlotManager``'s bilingual
        # clobber guard rejected. Resolve through the ONE layout seam instead.
        canon_path = _shadow_path(slots_dir, canon)
        legacy_path = _shadow_path(slots_dir, legacy)
        try:
            # 1. Legacy rename — only when the canon target is free.
            if legacy_path is not None:
                if canon_path is not None:
                    log.warning(
                        "slot.trio_shadow_rename_skipped",
                        extra={
                            "legacy": legacy,
                            "canon": canon,
                            "reason": "canon target already exists",
                        },
                    )
                elif is_id_stem(legacy_path.stem):
                    # Id-keyed: a rename is a RELABEL of a stable id, and the
                    # identity row carries that label. A bare file move would
                    # strand the row under the legacy name and leave the shadow
                    # unresolvable by its new one, so delegate to the canonical
                    # :meth:`SlotManager.rename` — it rewrites the embedded
                    # ``name`` in place, moves the identity row + state record,
                    # and invalidates the caches as one unit.
                    await mgr.rename(legacy, canon)
                    canon_path = legacy_path
                    log.info("slot.trio_shadow_renamed", extra={"from": legacy, "to": canon})
                else:
                    legacy_raw = tomllib.loads(legacy_path.read_text(encoding="utf-8"))
                    legacy_raw["name"] = canon
                    canon_path = slots_dir / f"{canon}.toml"
                    write_slot_toml(canon_path, legacy_raw)
                    with contextlib.suppress(FileNotFoundError):
                        legacy_path.unlink()
                    mgr._invalidate_cfg_cache(legacy)
                    mgr._invalidate_cfg_cache(canon)
                    log.info(
                        "slot.trio_shadow_renamed",
                        extra={"from": legacy, "to": canon},
                    )

            # 2. Ensure + normalize the canon shadow record. After a rename
            #    canon_path now exists, so the same iteration normalizes it.
            if canon_path is not None:
                raw = tomllib.loads(canon_path.read_text(encoding="utf-8"))
                desired = {
                    "device": "npu",
                    "profile": "flm",
                    "served_by": anchor_name,
                    "port": int(anchor_port),
                    "type": slot_type,
                }
                if any(raw.get(k) != v for k, v in desired.items()):
                    raw.update(desired)
                    raw.setdefault("name", canon)
                    write_slot_toml(canon_path, raw)
                    mgr._invalidate_cfg_cache(canon)
                    changed += 1
                    log.info("slot.trio_shadow_normalized", extra={"slot": canon})
                continue

            # 3. Missing → create it. Dispatch eligibility is the anchor's
            #    ``[npu]`` toggle, read at request time — not mirrored here.
            cfg_dict = {
                "name": canon,
                "port": int(anchor_port),
                "device": "npu",
                "provider": "flm",
                "profile": "flm",
                "served_by": anchor_name,
                "type": slot_type,
                "model": {"default": default_model},
            }
            await mgr.create(canon, cfg_dict)
            changed += 1
            log.info("slot.trio_shadow_created", extra={"slot": canon})
        except Exception as exc:
            log.warning(
                "slot.reconcile_trio_shadow_failed",
                extra={"slot": canon, "error": str(exc)},
            )
    return changed


__all__ = [
    "NpuTrioHost",
    "is_npu_trio_shadow",
    "reconcile_trio_slots",
]
