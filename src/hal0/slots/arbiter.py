"""GpuArbiter — exclusive llm/img GPU group arbitration (spec §7, Phase D).

Strix Halo has ONE iGPU shared via GTT. LLM containers and the ComfyUI
image-gen container cannot share it, so the arbiter flips the GPU between
two exclusive groups:

  - ``llm`` — GPU llama-server containers (chat, agent, utility, rerank…)
  - ``img`` — the ComfyUI container slot

Groups are DERIVED from slot configs (:func:`gpu_exclusive_group`), never
declared: a slot is arbitrated iff it runs on the GPU (``gpu-rocm`` /
``gpu-vulkan``) AND in a container. NPU and CPU slots are never arbitrated.

Resident-container design: what is exclusive is the GPU **memory** (LLM
weights vs diffusion models), NOT the ComfyUI process — an idle ComfyUI
holds ~0 GTT on unified memory, and its web UI must stay reachable in llm
mode so users can build workflows before flipping the switch. So the
arbiter never stops the img container once it is up:

  - ``ensure_img``  — drain + unload the llm set; the img container is only
    (cold-)started when it isn't already dispatchable.
  - ``restore_llm`` — ``POST /free`` to ComfyUI (drop its models from GTT),
    then reload the saved llm set. The container keeps serving the UI.
  - direct ComfyUI ``/prompt`` submission goes through the host-mounted
    ``hal0_gpu_gate`` custom node, which asks hal0-api to enter image mode
    before forwarding the prompt.

Image mode lasts only while image work runs. Image work is a
``/v1/images/generations`` request (it holds :meth:`GpuArbiter.image_job`
from before the switch until its render returns), a request forwarded to the
img slot (``in_flight_count``), or a render in ComfyUI's own queue (the
editor's prompts never pass through hal0). Two callers decide *when* the saved
LLM set comes back, both through :meth:`GpuArbiter._restore_if_quiet`:

  - the restore loop (:meth:`GpuArbiter.run_restore_loop`), ``_RESTORE_GRACE_S``
    after the last render ended;
  - LLM demand (:meth:`GpuArbiter.make_room_for_llm`), at once, awaited by an
    LLM request right before its dispatch guard.

A manual pin holds image mode against both.

Crash-recovery ordering (locked): the arbiter persists its target state to
``/var/lib/hal0/gpu_arbiter.json`` BEFORE the first unload, so an api
restart mid-switch can still restore the saved LLM set. A restore persists
``mode=llm`` last, so a crash mid-restore leaves image mode on disk and the
restore loop retries it after the restart.

Concurrency model (single asyncio event loop):

  - ``ensure_img()`` and every restore serialize on one ``asyncio.Lock``;
    concurrent flips are impossible.
  - ``image_job()``'s counter changes only on the loop thread, with no await
    between its read and its write, so a request is counted before the first
    await of its switch.
  - ``guard_dispatch()`` / ``status()`` / ``set_pin()`` are synchronous and
    lock-free. This is safe because they contain no ``await`` points: on a
    single event loop a sync method runs to completion atomically relative to
    the (async) switch methods, which can only interleave at their own awaits.
    The shared ``_state`` dict is only ever mutated from the loop thread, and
    every mutation is followed by an atomic same-directory ``os.replace``
    persist, so on-disk readers never observe a torn file. The one deliberate
    "race": ensure_img persists ``mode=img`` before draining, so the guard
    refuses NEW llm dispatches during the drain window — that is desired
    (prevents drain starvation).
  - ``_restore_if_quiet`` runs its checks under the lock and re-checks the
    image-job counter after each of its awaits, so a request that arrives
    while it reads ComfyUI's queue cancels the restore.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
from collections.abc import AsyncIterator
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from hal0.config import paths
from hal0.errors import Hal0Error, NotFound
from hal0.install.perms import ensure_shared_dir
from hal0.slots.state import DISPATCHABLE_STATES, SlotState

if TYPE_CHECKING:  # pragma: no cover — import cycle guard (manager owns us)
    from hal0.slots.manager import SlotManager

log = logging.getLogger(__name__)

#: Devices that contend for the single iGPU.
_GPU_DEVICES = frozenset({"gpu-rocm", "gpu-vulkan"})

#: Drain budget before proceeding with unloads anyway (manual pin is the
#: escape hatch per spec §7 — never wedge image mode behind a stuck request).
_DRAIN_TIMEOUT_S = 120.0
_DRAIN_POLL_S = 0.5

#: Retry-After hint carried by GpuImageMode (matches the dispatcher's
#: slot-loading convention in dispatcher/router.py). Fixed: image mode ends one
#: grace after the last render, so there is no longer window to count down.
_RETRY_AFTER_FLOOR_S = 15

#: Seconds with no image work before the restore loop hands the GPU back: four
#: times the longest machine-paced gap between renders (the gate node queues an
#: editor prompt at most ~2.5 s after the switch), so back-to-back renders share
#: one image mode instead of reloading the LLM set between them.
_RESTORE_GRACE_S = 10.0
#: Restore-loop tick: the GPU returns at most grace + tick (12 s) after the last
#: render when no LLM request asks sooner; an idle tick does no I/O.
_RESTORE_TICK_S = 2.0
#: How long an LLM request waits for the restore it triggered: reloading the
#: saved set waits for each slot to reach READY, like the drain and img-readiness
#: budgets.
_LLM_DEMAND_WAIT_S = 120.0

#: Readiness poll budget after img slot load() returns — containers go
#: STARTING → WARMING → READY asynchronously; load() only waits for systemd
#: start, not for the health probe to pass.
_IMG_READY_TIMEOUT_S = 120.0
_READY_POLL_S = 0.5

#: Slot states that can take a dispatch / mean "the img container is up".
#: Aliases the canonical set (DR-8) — do not re-declare the literal here.
_DISPATCHABLE: frozenset[SlotState] = DISPATCHABLE_STATES
#: Terminal states that abort the readiness poll immediately.
_TERMINAL: frozenset[SlotState] = frozenset({SlotState.ERROR, SlotState.OFFLINE})

#: ComfyUI HTTP budget — short connect so a dead engine surfaces fast.
_COMFYUI_HTTP_TIMEOUT_S = 5.0
_COMFYUI_CONNECT_TIMEOUT_S = 1.5


def _comfyui_base_url() -> str:
    """Operational ComfyUI HTTP base (mirrors api/routes/comfyui.py).

    Duplicated on purpose: the slots layer must not import the API layer.
    """
    return os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip("/")


async def _comfyui_free() -> bool:
    """Best-effort ``POST /free`` — drop ComfyUI's models from GTT.

    True on a 200; False on any HTTP/connect failure (an unreachable
    ComfyUI holds no GPU memory, so the restore proceeds either way).
    """
    import httpx

    from hal0.http_client import async_client

    timeout = httpx.Timeout(_COMFYUI_HTTP_TIMEOUT_S, connect=_COMFYUI_CONNECT_TIMEOUT_S)
    try:
        async with async_client(timeout=timeout) as client:
            resp = await client.post(
                f"{_comfyui_base_url()}/free",
                json={"unload_models": True, "free_memory": True},
            )
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


class ComfyUIQueueUnknown(Exception):
    """``GET /queue`` answered, but not with a usable queue (non-200, bad body, timeout)."""


async def _comfyui_queue_state() -> tuple[int, int] | None:
    """Strict ``GET /queue``, read before every restore.

    ``(running, pending)`` from a good answer; ``None`` ONLY when nothing is
    listening (connection refused — then nothing is on the GPU either);
    :class:`ComfyUIQueueUnknown` for every other failure (HTTP error, timeout,
    unparseable body). A restore that frees ComfyUI's models must not take
    "the read failed" as "nothing is rendering". Renders queued straight into
    ComfyUI's web UI never pass through hal0, so this read is the only way to
    see them.
    """
    import httpx

    from hal0.http_client import async_client

    timeout = httpx.Timeout(_COMFYUI_HTTP_TIMEOUT_S, connect=_COMFYUI_CONNECT_TIMEOUT_S)
    try:
        async with async_client(timeout=timeout) as client:
            resp = await client.get(f"{_comfyui_base_url()}/queue")
    except httpx.ConnectError:
        return None
    except httpx.HTTPError as exc:
        raise ComfyUIQueueUnknown(str(exc)) from exc
    if resp.status_code != 200:
        raise ComfyUIQueueUnknown(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ComfyUIQueueUnknown("unparseable /queue body") from exc
    if not isinstance(data, dict):
        raise ComfyUIQueueUnknown("unexpected /queue shape")
    running = data.get("queue_running")
    pending = data.get("queue_pending")
    return (
        len(running) if isinstance(running, list) else 0,
        len(pending) if isinstance(pending, list) else 0,
    )


_DEFAULT_STATE: dict[str, Any] = {
    "mode": "llm",
    "pinned": False,
    "saved_llm_slots": [],
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


class GpuInferenceMode(Hal0Error):
    """Image dispatch refused — the GPU is serving the LLM set.

    The resident ComfyUI container stays READY in llm mode (its web UI is
    always up). The OpenAI-compatible image route switches before dispatch,
    so this guard mainly protects lower-level/img-slot dispatch callers.
    """

    code = "gpu.inference_mode"
    status = 503


class ArbiterPinned(Hal0Error):
    """Restore refused — image mode is manually pinned (force to override)."""

    code = "gpu.pinned"
    status = 409


class ArbiterQueueBusy(Hal0Error):
    """Restore not attempted — image work was still running.

    A hal0 image request still rendering, or ComfyUI still had running or
    pending renders when the wait ran out.
    """

    code = "gpu.queue_busy"
    status = 409


#: Poll interval while :meth:`GpuArbiter.restore_llm_when_idle` waits for the
#: ComfyUI queue to drain.
_QUEUE_IDLE_POLL_S = 2.0
#: Default wait budget for that drain. A render on the shipped hardware is
#: seconds to a few minutes; ten minutes covers a queued batch without holding
#: a caller forever.
_QUEUE_IDLE_MAX_WAIT_S = 600.0


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
        restore_grace_s: float = _RESTORE_GRACE_S,
    ) -> None:
        self._manager = manager
        self.state_path = Path(state_path)
        self.restore_grace_s = float(restore_grace_s)
        # ONE lock around any whole switch — no concurrent flips.
        self._switch_lock = asyncio.Lock()
        # Lazily-read persisted state (None until first access).
        self._state: dict[str, Any] | None = None
        # name → group, refreshed from iter_configs() on every switch; lets
        # the sync guard answer without I/O for slots seen this process.
        self._group_cache: dict[str, str | None] = {}
        # In-memory only (a restart has no render in flight). /v1 image
        # requests open now (image_job); the monotonic time image work last
        # ended, None while it runs or before this process has seen image mode;
        # set while ensure_img holds the lock; the restore an LLM request
        # started, shared by the requests that arrive while it runs.
        self._image_jobs: int = 0
        self._quiet_since: float | None = None
        self._entering_img: bool = False
        self._demand_restore: asyncio.Task[bool] | None = None

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
        ensure_shared_dir(self.state_path.parent)  # 2775, umask-proof (#1896)
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
        """Flip the GPU to exclusive image mode.

        Order (locked): snapshot running llm slots → persist target state
        FIRST → drain in-flight requests (bounded) → unload llm slots →
        cold-start the img container ONLY when it isn't already dispatchable
        (resident design: restore_llm never stops it). The quiet clock starts
        when the switch finishes, not when it began, so a slow cold start
        can't eat the restore grace and a restore queued behind the switch
        waits a full grace.

        Already in image mode, nothing is flipped, but the img slot is still
        checked and cold-started when it is down (#3048), since a crash or a
        manual unload can stop it without telling the arbiter. If that reload
        fails, the usual rollback to LLM mode applies, and it drops a pin.

        A pin is taken from this call only: one set while the GPU was in LLM
        mode does not carry into the switch, so a stray pin cannot turn the
        next render into a permanent image mode.
        """
        async with self._switch_lock:
            # LLM demand returns at once while this runs (it would otherwise
            # wait out the switch and then undo it).
            self._entering_img = True
            try:
                await self._ensure_img_locked(pin=pin)
            finally:
                self._entering_img = False
                self._quiet_since = time.monotonic()

    async def _ensure_img_locked(self, *, pin: bool) -> None:
        """:meth:`ensure_img`'s body; the caller holds the switch lock."""
        st = self._load_state()
        if st["mode"] == GpuMode.IMG.value:
            if pin and not st["pinned"]:
                st["pinned"] = True
                self._persist()
            await self._revive_img(st)
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
        st["pinned"] = bool(pin)
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
        # Resident container: when the img slot is already dispatchable
        # the switch is just the llm unloads above — no load, no poll.
        if self._manager.state(img_name) not in _DISPATCHABLE:
            await self._cold_start_img(img_cfg, img_name, llm_slots)

        log.info(
            "gpu_arbiter.img_mode",
            extra={"saved_llm_slots": llm_slots, "img_slot": img_name},
        )

    async def _revive_img(self, st: dict[str, Any]) -> None:
        """Cold-start the img slot when image mode finds it down (#3048).

        Called by ``ensure_img`` with the switch lock held, in image mode. The
        persisted mode can outlive the slot: a crash or a manual unload stops
        it without telling the arbiter. Nothing to do when no img-group slot is
        configured or it is already dispatchable. Otherwise the existing cold
        start runs, with its rollback to LLM mode if the reload fails.
        """
        cfgs = await self._manager.iter_configs()
        self._refresh_group_cache(cfgs)
        img_cfg = next((c for c in cfgs if gpu_exclusive_group(c) == "img"), None)
        if img_cfg is None:
            return
        img_name = str(img_cfg.get("name") or "img")
        state = self._manager.state(img_name)
        if state in _DISPATCHABLE:
            return
        log.warning(
            "gpu_arbiter.img_down_in_img_mode",
            extra={"img_slot": img_name, "state": str(state)},
        )
        await self._cold_start_img(img_cfg, img_name, list(st["saved_llm_slots"]))

    async def _cold_start_img(
        self, img_cfg: dict[str, Any], img_name: str, llm_slots: list[str]
    ) -> None:
        """Load the img container and poll it to a dispatchable state.

        Only runs when the resident container is down (first boot, crash,
        deploy restart). Rollback semantics unchanged from Phase D.
        """
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
        ready_deadline = time.monotonic() + _IMG_READY_TIMEOUT_S
        img_slot_state: SlotState | str = SlotState.WARMING
        while True:
            img_slot_state = self._manager.state(img_name)
            if img_slot_state in _DISPATCHABLE:
                return
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

    async def restore_llm(self, *, force: bool = False) -> None:
        """Restore the saved LLM set (no-op in LLM mode).

        Refuses while pinned unless ``force=True`` (which also clears the
        pin), and refuses with :class:`ArbiterQueueBusy` while hal0 image work
        is in flight unless ``force=True``. Order: free ComfyUI's models FIRST
        (``POST /free`` — the resident container is never unloaded, its web
        UI stays up), then reload the saved set, then persist ``mode=llm``
        (see :meth:`_restore_llm_locked`).
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
            if not force and await self._hal0_image_work():
                raise ArbiterQueueBusy(
                    "an image request is still rendering; LLM mode was not restored",
                    details={"image_jobs": self._image_jobs},
                )
            await self._restore_llm_locked(st)

    async def _restore_llm_locked(self, st: dict[str, Any]) -> None:
        """Free ComfyUI's models, reload the saved LLM set, and end image mode.

        The caller holds the switch lock. Each reload is best-effort, like
        :meth:`_rollback_to_llm`: a slot that fails to load stays down and the
        dispatcher lazy-loads it on its next request, and image mode always
        ends, so one bad slot can never hold the GPU in image mode and refuse
        chat. ``mode=llm`` is persisted last: a crash mid-restore leaves image
        mode on disk, and the restore loop retries it after the restart.
        """
        cfgs = await self._manager.iter_configs()
        self._refresh_group_cache(cfgs)
        saved = list(st["saved_llm_slots"])

        # Best-effort: an unreachable ComfyUI holds no GPU memory, so a
        # failed /free never blocks the restore.
        if not await _comfyui_free():
            log.warning("gpu_arbiter.comfyui_free_failed")
        restored: list[str] = []
        failed: list[str] = []
        for slot in saved:
            try:
                await self._manager.load(slot, None)
                restored.append(slot)
            except Exception as exc:  # best-effort, like the rollback
                failed.append(slot)
                log.warning(
                    "gpu_arbiter.restore_load_failed",
                    extra={"slot": slot, "error": str(exc)},
                )

        st["mode"] = GpuMode.LLM.value
        st["saved_llm_slots"] = []
        st["pinned"] = False
        self._persist()
        self._quiet_since = None
        log.info("gpu_arbiter.llm_mode", extra={"restored": restored, "failed": failed})

    async def restore_llm_when_idle(
        self, *, force: bool = False, max_wait_s: float = _QUEUE_IDLE_MAX_WAIT_S
    ) -> None:
        """:meth:`restore_llm`, but only once no image work is left.

        ``restore_llm`` frees ComfyUI's models and reloads the LLM set; called
        while a render is still active — another request's, or one a cancelled
        caller abandoned on the queue — it would drop that render. This waits
        until hal0 has no image work in flight (an open :meth:`image_job` or a
        request forwarded to the img slot) and ``GET /queue`` reports nothing
        running or pending, then restores. A refused connection counts as idle
        (nothing listening holds no GPU memory, the same stance
        ``restore_llm`` takes for ``/free``); any other failed read is NOT
        evidence the render stopped and counts as busy. Raises
        :class:`ArbiterQueueBusy` if the work has not drained within
        ``max_wait_s``; nothing is freed or reloaded in that case. A pinned
        mode raises :class:`ArbiterPinned` immediately, before any queue read.
        No-op in LLM mode, like ``restore_llm``.

        The omni tool loop uses this after an image round (#2191).
        """
        if self.mode is not GpuMode.IMG:
            return
        # A pinned image mode is an operator's choice that no queue drain will
        # change (and "someone is rendering" is the normal pinned case): answer
        # now rather than wait out the queue and then report queue_busy (#2639).
        if self.pinned and not force:
            raise ArbiterPinned(
                "GPU image mode is pinned; pass force=true to restore LLM slots",
                details={"pinned": True},
            )
        deadline = time.monotonic() + max_wait_s
        while True:
            counts: tuple[int, int] | None
            if await self._hal0_image_work():
                # hal0's own render: no need to ask ComfyUI.
                counts = None
                reason = (
                    f"{self._image_jobs} image request(s) still rendering"
                    if self._image_jobs
                    else "a request to the img slot is still running"
                )
            else:
                try:
                    counts = await _comfyui_queue_state()
                except ComfyUIQueueUnknown as exc:
                    counts = None
                    reason = f"ComfyUI queue could not be read ({exc})"
                else:
                    if counts is None or sum(counts) == 0:
                        break
                    reason = (
                        f"ComfyUI still has {counts[0]} running and {counts[1]} pending render(s)"
                    )
            if time.monotonic() >= deadline:
                raise ArbiterQueueBusy(
                    f"{reason} after {max_wait_s:.0f}s; LLM mode was not restored",
                    details={
                        "running": counts[0] if counts else None,
                        "pending": counts[1] if counts else None,
                    },
                )
            await asyncio.sleep(_QUEUE_IDLE_POLL_S)
        await self.restore_llm(force=force)

    # ── image work and the restore decision ─────────────────────────────────

    @contextlib.asynccontextmanager
    async def image_job(self) -> AsyncIterator[None]:
        """Count one hal0 image request as image work for as long as it runs.

        The ``/v1/images/generations`` route enters it before ``ensure_img``
        and leaves it when the render returns or fails. No restore runs while
        a job is open. The count goes up before the first await of the
        caller's switch, so a restore can never slip in between the switch and
        the render. When the last job ends the quiet clock starts: an LLM
        request then restores at once, and the restore loop one grace later.
        """
        self._image_jobs += 1
        self._quiet_since = None
        try:
            yield
        finally:
            self._image_jobs = max(0, self._image_jobs - 1)
            if self._image_jobs == 0:
                self._quiet_since = time.monotonic()

    async def _hal0_image_work(self) -> bool:
        """True while hal0 itself has image work in flight.

        An open :meth:`image_job`, or a request forwarded to the img-group
        slot (its dispatch ticket and ``serving()`` context count in
        ``in_flight_count``). ComfyUI's own queue is read separately.
        """
        if self._image_jobs:
            return True
        cfgs = await self._manager.iter_configs()
        img_name = next(
            (str(c.get("name") or "") for c in cfgs if gpu_exclusive_group(c) == "img"),
            None,
        )
        if img_name is not None and self._manager.in_flight_count(img_name) > 0:
            return True
        # A request may have opened a job during the await above.
        return self._image_jobs > 0

    def _grace_passed(self) -> bool:
        return (
            self._quiet_since is not None
            and time.monotonic() - self._quiet_since >= self.restore_grace_s
        )

    async def _restore_if_quiet(self, *, honour_grace: bool) -> bool:
        """Restore the saved LLM set when no image work is left; True if it did.

        The one restore decision behind the restore loop (``honour_grace``)
        and LLM demand (no grace). Under the switch lock, in order: the mode
        is image and not pinned; the grace has passed (when honoured); hal0
        has no image work in flight; ComfyUI's strict queue read shows nothing
        running or pending (a refused connection counts as idle; a busy or
        unreadable queue restarts the quiet clock); and no image request
        opened, and no pin was set, during those awaits.
        """
        async with self._switch_lock:
            st = self._load_state()
            if st["mode"] != GpuMode.IMG.value or st["pinned"]:
                return False
            if honour_grace and not self._grace_passed():
                return False
            if await self._hal0_image_work():
                return False
            try:
                counts = await _comfyui_queue_state()
            except ComfyUIQueueUnknown:
                self._quiet_since = time.monotonic()
                return False
            if counts is not None and sum(counts) > 0:
                self._quiet_since = time.monotonic()
                return False
            if self._image_jobs or st["pinned"]:
                return False
            await self._restore_llm_locked(st)
            return True

    async def make_room_for_llm(
        self, slot_name: str, *, max_wait_s: float = _LLM_DEMAND_WAIT_S
    ) -> None:
        """End an idle image mode for an LLM request, and wait for the reload.

        Awaited right before each LLM dispatch guard. When nothing is
        rendering it restores the saved LLM set now, skipping the grace, and
        the request waits for the reload (at most ``max_wait_s``) instead of
        getting a 503. The restore is shared by every request that arrives
        while it runs and shielded from them, so a request that times out or
        goes away never leaves the set half reloaded.

        Returns at once, leaving the answer to the guard, in LLM mode, when
        pinned, while an image request is open or a switch into image mode
        holds the lock, and for a slot outside the llm group. Never raises:
        a timeout logs ``gpu_arbiter.llm_demand_wait_timeout``, and a failed
        restore is logged once by the task's done-callback.
        """
        st = self._load_state()
        if st["mode"] != GpuMode.IMG.value or st["pinned"]:
            return
        if self._image_jobs or self._entering_img:
            return
        if self._slot_group(self._resolve_alias(slot_name)) != "llm":
            return
        task = self._demand_restore
        if task is None or task.done():
            task = asyncio.ensure_future(self._restore_if_quiet(honour_grace=False))
            task.add_done_callback(_log_demand_restore_failure)
            self._demand_restore = task
        try:
            await asyncio.wait_for(asyncio.shield(task), max_wait_s)
        except TimeoutError:
            log.warning(
                "gpu_arbiter.llm_demand_wait_timeout",
                extra={"slot": slot_name, "wait_s": max_wait_s},
            )
        except Exception:  # logged once by _log_demand_restore_failure
            return

    # ── restore loop ─────────────────────────────────────────────────────────

    async def run_restore_loop(self, *, interval_s: float = _RESTORE_TICK_S) -> None:
        """Background loop handing the GPU back to the LLM set after image work.

        Runs forever, one :meth:`_restore_tick` per ``interval_s``. It is the
        only restore trigger for renders queued in ComfyUI's editor, and it
        heals an api restart in image mode (the first tick starts the quiet
        clock).

        Tick exceptions are logged (``gpu_arbiter.restore_loop_error``) and
        the loop keeps running; ``CancelledError`` propagates so the lifespan
        can shut the task down cleanly.
        """
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self._restore_tick()
            except asyncio.CancelledError:  # pragma: no cover — clean shutdown
                raise
            except Exception as exc:
                log.warning(
                    "gpu_arbiter.restore_loop_error",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                )

    async def _restore_tick(self) -> None:
        """One restore-loop evaluation; restores once the grace has passed."""
        st = self._load_state()
        if st["mode"] != GpuMode.IMG.value or st["pinned"] or self._image_jobs:
            return
        if self._quiet_since is None:
            # Image mode this process has not seen end (an api restart): start
            # the clock, so the restore follows one grace from now.
            self._quiet_since = time.monotonic()
            return
        if not self._grace_passed():
            return
        if await self._restore_if_quiet(honour_grace=True):
            log.info("gpu_arbiter.render_restore", extra={"grace_s": self.restore_grace_s})

    # ── sync surface (lock-free; see module docstring for why safe) ─────────

    def guard_dispatch(self, slot_name: str) -> None:
        """Raise when *slot_name* can't dispatch in the current GPU mode.

        Both directions of the exclusive split:

          - img mode + llm-group slot → :class:`GpuImageMode` (the arbiter
            unloaded the slot to hand the GPU to ComfyUI; an LLM request
            reaches this only while a render runs, the mode is pinned, or the
            restore it asked for did not finish in time);
          - llm mode + img-group slot → :class:`GpuInferenceMode` (the
            resident ComfyUI container is READY but must not grab GTT under
            the loaded LLM weights — flip the switch first).

        Cheap in the hot path: group lookups are cache-first and the cache
        is warmed by every switch / first lookup.
        """
        st = self._load_state()
        name = self._resolve_alias(slot_name)
        group = self._slot_group(name)
        if st["mode"] == GpuMode.IMG.value and group == "llm":
            raise GpuImageMode(
                f"GPU is in exclusive image mode; LLM slot {name!r} is unavailable "
                f"until image mode ends",
                details={"slot": name, "retry_after_s": _RETRY_AFTER_FLOOR_S},
            )
        if st["mode"] == GpuMode.LLM.value and group == "img":
            raise GpuInferenceMode(
                f"GPU is in inference mode; switch to image generation first "
                f"(POST /api/comfyui/switchover mode=generation) before "
                f"dispatching to slot {name!r}",
                details={"slot": name},
            )

    def set_pin(self, pinned: bool) -> None:
        """Set or clear the manual pin that holds image mode.

        Clearing it starts the quiet clock, so an unpinned image mode with
        nothing rendering ends one grace later.
        """
        st = self._load_state()
        st["pinned"] = bool(pinned)
        self._persist()
        if not pinned:
            self._quiet_since = time.monotonic()

    def status(self) -> dict[str, Any]:
        """Snapshot for the API.

        ``switching`` is true while a switch or a restore holds the lock;
        ``image_jobs`` counts the open ``/v1`` image requests.
        """
        st = self._load_state()
        return {
            "mode": st["mode"],
            "pinned": bool(st["pinned"]),
            "saved_llm_slots": list(st["saved_llm_slots"]),
            "switching": self._switch_lock.locked(),
            "image_jobs": self._image_jobs,
        }


def _log_demand_restore_failure(task: asyncio.Task[bool]) -> None:
    """Done-callback for an LLM-demand restore: retrieve and log its failure.

    The requests waiting on it may all have timed out or gone away, so the
    task's exception is retrieved here, once, rather than left unobserved.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning(
            "gpu_arbiter.llm_demand_restore_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
