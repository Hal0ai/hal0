"""GpuArbiter — exclusive llm/img GPU group arbitration (spec §7, Phase D).

Strix Halo has ONE iGPU shared via GTT. LLM containers and the ComfyUI
image-gen container cannot share it, so the arbiter flips the GPU between
two exclusive groups:

  - ``llm`` — GPU llama-server containers (chat, agent, utility, rerank…)
  - ``img`` — the ComfyUI container slot

Groups are DERIVED from slot configs (:func:`gpu_exclusive_group`), never
declared: a slot is arbitrated iff it runs on the GPU (``gpu-rocm`` /
``gpu-vulkan``) AND in a container. NPU and CPU slots are never arbitrated.

Crash-recovery ordering (locked): the arbiter persists its target state to
``/var/lib/hal0/gpu_arbiter.json`` BEFORE the first unload, so an api
restart mid-switch can still restore the saved LLM set.

Concurrency model (single asyncio event loop):

  - ``ensure_img()`` / ``restore_llm()`` — the only mutating switches —
    serialize on one ``asyncio.Lock``; concurrent flips are impossible.
  - ``guard_llm_dispatch()`` / ``status()`` / ``touch_img_activity()`` /
    ``set_pin()`` are synchronous and lock-free. This is safe because they
    contain no ``await`` points: on a single event loop a sync method runs
    to completion atomically relative to the (async) switch methods, which
    can only interleave at their own awaits. The shared ``_state`` dict is
    only ever mutated from the loop thread, and every mutation is followed
    by an atomic same-directory ``os.replace`` persist, so on-disk readers
    never observe a torn file. The one deliberate "race": ensure_img
    persists ``mode=img`` before draining, so the guard refuses NEW llm
    dispatches during the drain window — that is desired (prevents drain
    starvation).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from hal0.config import paths
from hal0.errors import Hal0Error, NotFound
from hal0.slots.state import SlotState

if TYPE_CHECKING:  # pragma: no cover — import cycle guard (manager owns us)
    from hal0.slots.manager import SlotManager

log = logging.getLogger(__name__)

#: Devices that contend for the single iGPU.
_GPU_DEVICES = frozenset({"gpu-rocm", "gpu-vulkan"})

#: Drain budget before proceeding with unloads anyway (manual pin is the
#: escape hatch per spec §7 — never wedge image mode behind a stuck request).
_DRAIN_TIMEOUT_S = 120.0
_DRAIN_POLL_S = 0.5

#: Minimum Retry-After hint carried by GpuImageMode (matches the dispatcher's
#: slot-loading convention in dispatcher/router.py).
_RETRY_AFTER_FLOOR_S = 15

#: Readiness poll budget after img slot load() returns — containers go
#: STARTING → WARMING → READY asynchronously; load() only waits for systemd
#: start, not for the health probe to pass.
_IMG_READY_TIMEOUT_S = 120.0
_READY_POLL_S = 0.5

_DEFAULT_STATE: dict[str, Any] = {
    "mode": "llm",
    "pinned": False,
    "saved_llm_slots": [],
    "last_img_activity": None,
}


def gpu_exclusive_group(slot_cfg: dict[str, Any]) -> Literal["llm", "img"] | None:
    """Derive the exclusive-GPU group for a raw slot-config dict.

    Returns ``"img"`` / ``"llm"`` for GPU container slots, ``None`` for
    everything else (npu/cpu devices) — those are never arbitrated. The img signal is provider/profile ``comfyui`` or slot
    ``type == "image"``; any other GPU container slot is an llm-group slot.

    "Container" mirrors ``SlotManager._is_container_slot`` exactly:
    ``runtime == "container"`` OR a non-empty ``profile`` — the profile is
    the primary signal (schema doc: profile wins regardless of ``runtime``;
    spec §9 deprecates ``runtime``). A profile-only GPU slot MUST be
    arbitrated or it would collide with the img slot on the single iGPU.

    A missing ``device`` defaults to ``gpu-rocm`` (mirrors
    ``SlotManager.add_slot`` / ``hal0.config.schema.DEFAULT_DEVICE``); a
    missing ``runtime`` defaults to ``container``.
    """
    device = str(slot_cfg.get("device") or "gpu-rocm")
    runtime = str(slot_cfg.get("runtime") or "container")
    profile = str(slot_cfg.get("profile") or "")
    is_container = runtime == "container" or bool(profile.strip())
    if device not in _GPU_DEVICES or not is_container:
        return None
    provider = str(slot_cfg.get("provider") or "")
    slot_type = str(slot_cfg.get("type") or "")
    if provider == "comfyui" or profile == "comfyui" or slot_type == "image":
        return "img"
    return "llm"


class GpuMode(StrEnum):
    LLM = "llm"
    IMG = "img"


class GpuImageMode(Hal0Error):
    """LLM dispatch refused — the GPU is in exclusive image mode."""

    code = "gpu.image_mode"
    status = 503


class ArbiterPinned(Hal0Error):
    """Restore refused — image mode is manually pinned (force to override)."""

    code = "gpu.pinned"
    status = 409


class GpuImgNotReady(Hal0Error):
    """Image slot did not become ready within the readiness timeout.

    Raised when the img slot fails to reach a dispatchable state after
    load() returns (e.g. crashed at start with exit 125, or health probe
    never passed within _IMG_READY_TIMEOUT_S seconds).  Triggers the same
    rollback as a load() exception so the guard is never left wedged.
    """

    code = "gpu.img_not_ready"
    status = 503


class GpuArbiter:
    """Owns the llm ⇄ img exclusive-GPU switch for one :class:`SlotManager`."""

    def __init__(
        self,
        manager: SlotManager,
        *,
        state_path: Path,
        idle_restore_minutes: int = 5,
    ) -> None:
        self._manager = manager
        self.state_path = Path(state_path)
        self.idle_restore_minutes = int(idle_restore_minutes)
        # ONE lock around any whole switch — no concurrent flips.
        self._switch_lock = asyncio.Lock()
        # Lazily-read persisted state (None until first access).
        self._state: dict[str, Any] | None = None
        # name → group, refreshed from iter_configs() on every switch; lets
        # the sync guard answer without I/O for slots seen this process.
        self._group_cache: dict[str, str | None] = {}

    # ── persisted state ──────────────────────────────────────────────────────

    def _load_state(self) -> dict[str, Any]:
        """Lazily read gpu_arbiter.json; tolerate missing/corrupt files."""
        if self._state is None:
            st = dict(_DEFAULT_STATE)
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    st.update({k: data[k] for k in _DEFAULT_STATE if k in data})
            except FileNotFoundError:
                pass
            except (OSError, json.JSONDecodeError) as exc:
                log.warning(
                    "gpu_arbiter.state_unreadable",
                    extra={"path": str(self.state_path), "error": str(exc)},
                )
            if st.get("mode") not in (GpuMode.LLM.value, GpuMode.IMG.value):
                st["mode"] = GpuMode.LLM.value
            st["saved_llm_slots"] = [str(s) for s in st.get("saved_llm_slots") or []]
            self._state = st
        return self._state

    def _persist(self) -> None:
        """Atomic same-directory tmpfile + os.replace (state.json pattern)."""
        st = self._load_state()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            fd, tmp_str = tempfile.mkstemp(
                prefix=".hal0-gpu-arbiter-", suffix=".tmp", dir=self.state_path.parent
            )
            tmp_path = Path(tmp_str)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(st, f, indent=2)
            os.replace(tmp_path, self.state_path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    @property
    def mode(self) -> GpuMode:
        return GpuMode(self._load_state()["mode"])

    @property
    def pinned(self) -> bool:
        return bool(self._load_state()["pinned"])

    @property
    def saved_llm_slots(self) -> tuple[str, ...]:
        return tuple(self._load_state()["saved_llm_slots"])

    # ── group resolution ─────────────────────────────────────────────────────

    def _refresh_group_cache(self, cfgs: list[dict[str, Any]]) -> None:
        self._group_cache = {str(c.get("name") or ""): gpu_exclusive_group(c) for c in cfgs}

    def _slot_group(self, name: str) -> str | None:
        """Sync group lookup for the dispatch guard.

        Cache-first (populated by every switch), then a direct slot-TOML
        read (restart case — mirrors the sync-read precedent of
        ``SlotManager.idle_timeout_by_model``), then saved-set membership
        as the last resort (in-memory-only slots without a TOML).
        """
        if name in self._group_cache:
            return self._group_cache[name]
        cfg = self._read_slot_toml(name)
        if cfg is not None:
            group = gpu_exclusive_group(cfg)
            self._group_cache[name] = group
            return group
        if name in self._load_state()["saved_llm_slots"]:
            return "llm"
        return None

    def _read_slot_toml(self, name: str) -> dict[str, Any] | None:
        """Best-effort sync read of the slot's TOML; None if missing/bad."""
        import tomllib

        config_file = getattr(self._manager, "_config_file", None)
        path = (
            config_file(name)
            if callable(config_file)
            else (paths.slots_config_dir() / f"{name}.toml")
        )
        try:
            with open(path, "rb") as f:
                return tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return None

    def _resolve_alias(self, name: str) -> str:
        resolve = getattr(self._manager, "_resolve_alias", None)
        return resolve(name) if callable(resolve) else name

    # ── switches ─────────────────────────────────────────────────────────────

    async def _rollback_to_llm(
        self, llm_slots: list[str], img_name: str, *, unload_img: bool = False
    ) -> None:
        """Best-effort rollback: reload saved LLM set, persist mode=llm.

        Shared by the load()-raises path (D5) and the readiness-timeout path
        (#714). Always clears mode=img and saved_llm_slots regardless of
        whether individual slot reloads succeed — the guard must never be
        left wedged.

        ``unload_img=True`` triggers a best-effort ``unload(img_name)`` first
        (used by the readiness-timeout path where the container may be
        crash-looping and holding GTT).  The D5 load()-raises path skips this
        because load() already failed — nothing was running to unload.
        """
        if unload_img:
            try:
                await self._manager.unload(img_name)
            except Exception as exc:  # best-effort — container may be dead
                log.warning(
                    "gpu_arbiter.rollback_img_unload_failed",
                    extra={"img_slot": img_name, "error": str(exc)},
                )
        restored: list[str] = []
        failed: list[str] = []
        for slot in llm_slots:
            try:
                await self._manager.load(slot, None)
                restored.append(slot)
            except Exception as rollback_exc:  # best-effort rollback
                failed.append(slot)
                log.warning(
                    "gpu_arbiter.rollback_load_failed",
                    extra={"slot": slot, "error": str(rollback_exc)},
                )
        st = self._load_state()
        st["mode"] = GpuMode.LLM.value
        # Failed slots stay offline (operator reloads manually); a
        # saved set is meaningless in llm mode, so clear it.
        st["saved_llm_slots"] = []
        st["pinned"] = False
        self._persist()
        log.warning(
            "gpu_arbiter.img_load_failed_rolled_back",
            extra={
                "img_slot": img_name,
                "restored": restored,
                "failed": failed,
            },
        )

    async def ensure_img(self, *, pin: bool = False) -> None:
        """Flip the GPU to exclusive image mode (no-op when already there).

        Order (locked): snapshot running llm slots → persist target state
        FIRST → drain in-flight requests (bounded) → unload llm slots →
        load the img slot's default model → poll for readiness → stamp activity.
        """
        async with self._switch_lock:
            st = self._load_state()
            if st["mode"] == GpuMode.IMG.value:
                if pin and not st["pinned"]:
                    st["pinned"] = True
                    self._persist()
                return

            cfgs = await self._manager.iter_configs()
            self._refresh_group_cache(cfgs)
            img_cfg = next((c for c in cfgs if gpu_exclusive_group(c) == "img"), None)
            if img_cfg is None:
                raise NotFound(
                    "no image-group slot is configured; cannot enter image mode",
                    details={"hint": "seed the img (ComfyUI) slot first"},
                    code="gpu.img_slot_missing",
                )
            llm_slots = [
                str(c.get("name") or "")
                for c in cfgs
                if gpu_exclusive_group(c) == "llm"
                and self._manager.is_ready_for_dispatch(str(c.get("name") or ""))
            ]

            # Persist BEFORE unloads begin (crash-recovery ordering): a
            # restart from here on knows the saved set and the target mode.
            st["mode"] = GpuMode.IMG.value
            st["saved_llm_slots"] = llm_slots
            st["pinned"] = bool(st["pinned"] or pin)
            st["last_img_activity"] = time.time()
            self._persist()

            # Drain in-flight llm requests, bounded — proceed on timeout
            # (manual pin is the escape hatch per spec §7).
            deadline = time.monotonic() + _DRAIN_TIMEOUT_S
            while any(self._manager.in_flight_count(s) for s in llm_slots):
                if time.monotonic() >= deadline:
                    log.warning(
                        "gpu_arbiter.drain_timeout",
                        extra={
                            "slots": llm_slots,
                            "timeout_s": _DRAIN_TIMEOUT_S,
                            "in_flight": {s: self._manager.in_flight_count(s) for s in llm_slots},
                        },
                    )
                    break
                await asyncio.sleep(_DRAIN_POLL_S)

            for slot in llm_slots:
                await self._manager.unload(slot)

            img_name = str(img_cfg.get("name") or "img")
            model_section = img_cfg.get("model")
            model_default = (
                str(model_section.get("default") or "") if isinstance(model_section, dict) else ""
            )
            try:
                await self._manager.load(img_name, model_default or None)
            except Exception:
                # D5 quality-gate I1: load() raised — rollback, then re-raise
                # the ORIGINAL load exception so callers see the root cause.
                await self._rollback_to_llm(llm_slots, img_name, unload_img=False)
                raise

            # #714: load() only waits for systemd start, not health-probe
            # completion (containers go STARTING → WARMING → READY async).
            # Poll until the slot reaches a dispatchable state or we time out.
            # Call state() exactly once per iteration — terminal states
            # (ERROR/OFFLINE) abort the poll immediately so we don't burn the
            # full 120s on a crash-at-start.
            _TERMINAL: frozenset[SlotState] = frozenset({SlotState.ERROR, SlotState.OFFLINE})
            _DISPATCHABLE: frozenset[SlotState] = frozenset(
                {SlotState.READY, SlotState.SERVING, SlotState.IDLE}
            )
            ready_deadline = time.monotonic() + _IMG_READY_TIMEOUT_S
            img_slot_state: SlotState | str = SlotState.WARMING
            while True:
                img_slot_state = self._manager.state(img_name)
                if img_slot_state in _DISPATCHABLE:
                    break
                if img_slot_state in _TERMINAL:
                    log.warning(
                        "gpu_arbiter.img_terminal_state_during_readiness",
                        extra={"img_slot": img_name, "state": str(img_slot_state)},
                    )
                    await self._rollback_to_llm(llm_slots, img_name, unload_img=True)
                    raise GpuImgNotReady(
                        f"img slot {img_name!r} reached terminal state "
                        f"{img_slot_state!r} after load; rolled back to LLM mode",
                        details={"slot": img_name, "state": str(img_slot_state)},
                    )
                if time.monotonic() >= ready_deadline:
                    log.warning(
                        "gpu_arbiter.img_readiness_timeout",
                        extra={
                            "img_slot": img_name,
                            "timeout_s": _IMG_READY_TIMEOUT_S,
                            "state": str(img_slot_state),
                        },
                    )
                    await self._rollback_to_llm(llm_slots, img_name, unload_img=True)
                    raise GpuImgNotReady(
                        f"img slot {img_name!r} did not become ready within "
                        f"{_IMG_READY_TIMEOUT_S}s (last state: {img_slot_state!r}); "
                        f"rolled back to LLM mode",
                        details={
                            "slot": img_name,
                            "timeout_s": _IMG_READY_TIMEOUT_S,
                            "state": str(img_slot_state),
                        },
                    )
                await asyncio.sleep(_READY_POLL_S)

            # last_img_activity was stamped in the pre-unload persist above;
            # no second persist needed here (spec-review tidy).
            log.info(
                "gpu_arbiter.img_mode",
                extra={"saved_llm_slots": llm_slots, "img_slot": img_name},
            )

    async def restore_llm(self, *, force: bool = False) -> None:
        """Restore the saved LLM set (no-op in LLM mode).

        Refuses while pinned unless ``force=True`` (which also clears the
        pin). Order: unload img FIRST, then reload the saved set, then
        persist ``mode=llm`` — a crash mid-restore leaves mode IMG on disk
        so the restore can simply be retried.
        """
        async with self._switch_lock:
            st = self._load_state()
            if st["mode"] != GpuMode.IMG.value:
                return
            if st["pinned"] and not force:
                raise ArbiterPinned(
                    "GPU image mode is pinned; pass force=true to restore LLM slots",
                    details={"pinned": True},
                )

            cfgs = await self._manager.iter_configs()
            self._refresh_group_cache(cfgs)
            img_name = next(
                (str(c.get("name") or "") for c in cfgs if gpu_exclusive_group(c) == "img"),
                "img",
            )
            saved = list(st["saved_llm_slots"])

            await self._manager.unload(img_name)
            for slot in saved:
                await self._manager.load(slot, None)

            st["mode"] = GpuMode.LLM.value
            st["saved_llm_slots"] = []
            st["pinned"] = False
            self._persist()
            log.info("gpu_arbiter.llm_mode", extra={"restored": saved})

    # ── idle-restore loop (D6) ───────────────────────────────────────────────

    async def run_idle_loop(self, *, interval_s: float = 30.0) -> None:
        """Background loop auto-restoring the LLM set after img idles out.

        Runs forever, one tick per ``interval_s``. A tick restores iff the
        mode is IMG, not pinned, the idle window has elapsed since the last
        img activity (``idle_restore_minutes``; 0 = manual-only restore per
        the #599 schema), and the img slot has no in-flight job — a long
        Wan video render defers the restore until it finishes.

        Tick exceptions are logged (``gpu_arbiter.idle_loop_error``) and the
        loop keeps running; ``CancelledError`` propagates so the lifespan
        can shut the task down cleanly.
        """
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self._idle_tick()
            except asyncio.CancelledError:  # pragma: no cover — clean shutdown
                raise
            except Exception as exc:
                log.warning(
                    "gpu_arbiter.idle_loop_error",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                )

    async def _idle_tick(self) -> None:
        """One idle-restore evaluation; restores the LLM set when eligible."""
        if self.idle_restore_minutes <= 0:
            return  # 0 = manual-only restore (#599 schema), never auto
        # status() is the single source of truth for the window math:
        # idle_restore_at is None unless mode==img, unpinned, activity known.
        restore_at = self.status()["idle_restore_at"]
        if restore_at is None or time.time() < restore_at:
            return
        # Same img-group lookup ensure_img/restore_llm use — never hardcoded.
        cfgs = await self._manager.iter_configs()
        img_name = next(
            (str(c.get("name") or "") for c in cfgs if gpu_exclusive_group(c) == "img"),
            None,
        )
        if img_name is not None and self._manager.in_flight_count(img_name) > 0:
            return  # in-flight img job — defer until it completes
        await self.restore_llm()
        log.info(
            "gpu_arbiter.idle_restored",
            extra={"idle_restore_minutes": self.idle_restore_minutes},
        )

    # ── sync surface (lock-free; see module docstring for why safe) ─────────

    def guard_llm_dispatch(self, slot_name: str) -> None:
        """Raise :class:`GpuImageMode` when *slot_name* can't dispatch.

        Cheap in the hot path: a single in-memory mode check in LLM mode;
        group derivation only happens while the GPU is in image mode.
        """
        st = self._load_state()
        if st["mode"] != GpuMode.IMG.value:
            return
        name = self._resolve_alias(slot_name)
        if self._slot_group(name) != "llm":
            return
        raise GpuImageMode(
            f"GPU is in exclusive image mode; LLM slot {name!r} is unavailable "
            f"until image mode ends",
            details={"slot": name, "retry_after_s": self._retry_after_s(st)},
        )

    def _retry_after_s(self, st: dict[str, Any]) -> int:
        """Remaining idle-restore window, floored at 15s (D6 owns the loop)."""
        last = st.get("last_img_activity")
        if not isinstance(last, (int, float)):
            return _RETRY_AFTER_FLOOR_S
        restore_at = float(last) + self.idle_restore_minutes * 60
        return max(_RETRY_AFTER_FLOOR_S, int(restore_at - time.time()))

    def touch_img_activity(self) -> None:
        """Stamp last_img_activity + persist (cheap json write)."""
        st = self._load_state()
        st["last_img_activity"] = time.time()
        self._persist()

    def set_pin(self, pinned: bool) -> None:
        st = self._load_state()
        st["pinned"] = bool(pinned)
        self._persist()

    def status(self) -> dict[str, Any]:
        """Snapshot for the API / the D6 idle-restore loop."""
        st = self._load_state()
        idle_restore_at: float | None = None
        last = st.get("last_img_activity")
        if st["mode"] == GpuMode.IMG.value and not st["pinned"] and isinstance(last, (int, float)):
            idle_restore_at = float(last) + self.idle_restore_minutes * 60
        return {
            "mode": st["mode"],
            "pinned": bool(st["pinned"]),
            "saved_llm_slots": list(st["saved_llm_slots"]),
            "idle_restore_at": idle_restore_at,
        }
