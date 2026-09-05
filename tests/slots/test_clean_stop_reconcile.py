"""#2130 — a deliberate stop must read OFFLINE, never ERROR.

The v1.0.0 shape: the documented ``hal0 slot migrate-flags --apply
--stop-services`` path runs a bare ``systemctl stop`` on every active slot
unit. The stop is graceful (SIGTERM, application shutdown logged) but podman
propagates it as exit code 143, the pre-#2130 unit had no
``SuccessExitStatus=143``, systemd parked the unit in ``failed``, and the next
``hal0 slot list`` painted every slot on a healthy mid-upgrade box as a red
``error``. Worse, the verdict was sticky: ``systemctl reset-failed`` fixed
systemd's view but hal0 kept its cached ERROR until a full ``slot load``.

Pinned here, against the manager's ``status()`` reconciler:

  * deliberate stop → OFFLINE, with no crash-line stamped and no crash-loop
    breaker bookkeeping — a clean stop is not a failure;
  * real crash (systemd's terminal verdict) → still ERROR;
  * cached ERROR + positive "deliberately stopped" evidence from the unit →
    converges to OFFLINE (the ``reset-failed`` remediation works);
  * cached ERROR + inconclusive probe → stays ERROR (absence of evidence
    never manufactures an OFFLINE).

The provider-side halves (``SuccessExitStatus=143`` in the rendered unit, the
SIGTERM-stop mapping in ``unit_failure_reason`` / ``unit_stopped_cleanly``)
are pinned in ``tests/providers/test_container.py`` and
``tests/providers/test_container_unit_failure.py``.
"""

from __future__ import annotations

from pathlib import Path

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider

_CRASH_REASON = (
    "hal0-slot@chat.service failed (result=exit-code, restarts=1) — "
    "see `journalctl -u hal0-slot@chat.service`"
)


async def _load_ready(sm: SlotManager) -> None:
    slot = await sm.load("chat")
    assert slot.state == SlotState.READY


async def test_deliberate_stop_reads_offline_not_error(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The unit stopped on purpose; the probe reads nothing terminal (#2130:
    a SIGTERM-stop no longer produces a failure reason) → grey OFFLINE."""
    sm = SlotManager()
    await _load_ready(sm)

    # Out-of-band `systemctl stop`: the unit goes inactive; the failure
    # probe stays silent (the post-#2130 mapper answer for a SIGTERM stop).
    container_stub.active.discard("chat")

    slot = await sm.status("chat")

    assert slot.state == SlotState.OFFLINE
    # A clean stop is not a crash: no crash-line evidence may be stamped and
    # the crash-loop breaker must not have counted anything.
    assert "last_crash_line" not in slot.metadata
    assert "load_failures" not in slot.metadata
    assert slot.metadata.get("breaker") is None
    assert sm._load_failures == {}


async def test_real_crash_still_reads_error(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """systemd's terminal verdict (a genuinely failed unit) keeps the red
    ERROR — #2130 must not blunt the #1791/#2126 crash reporting."""
    sm = SlotManager()
    await _load_ready(sm)

    container_stub.active.discard("chat")
    container_stub.unit_failure_by_slot["chat"] = _CRASH_REASON

    slot = await sm.status("chat")

    assert slot.state == SlotState.ERROR
    assert slot.metadata.get("message") == _CRASH_REASON


async def test_cached_error_converges_once_unit_reads_deliberately_stopped(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """#2130 second half: the ERROR was sticky. An operator who ran
    ``systemctl reset-failed`` (or whose unit shows the plain SIGTERM-stop
    signature) and re-checked was told the problem was still there — the
    cached state never re-derived from the unit. With positive
    "deliberately stopped" evidence the display must converge to OFFLINE."""
    sm = SlotManager()
    await _load_ready(sm)

    # First: the pre-fix failure shape stamps a cached ERROR.
    container_stub.active.discard("chat")
    container_stub.unit_failure_by_slot["chat"] = _CRASH_REASON
    slot = await sm.status("chat")
    assert slot.state == SlotState.ERROR

    # The operator's remediation: `systemctl reset-failed` → the unit now
    # reads inactive/Result=success, i.e. deliberately stopped.
    container_stub.unit_failure_by_slot.pop("chat")
    container_stub.stopped_cleanly.add("chat")

    slot = await sm.status("chat")

    assert slot.state == SlotState.OFFLINE
    # Durable too — the persisted record converged, not just the snapshot.
    assert sm._current_state("chat") == SlotState.OFFLINE


async def test_cached_error_stays_without_positive_evidence(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """An inconclusive probe (no failure reason, but no clean-stop evidence
    either — e.g. a unit mid-restart between crashes, or a removed unit)
    must never retire a cached ERROR: absence of evidence is not OFFLINE."""
    sm = SlotManager()
    await _load_ready(sm)

    container_stub.active.discard("chat")
    container_stub.unit_failure_by_slot["chat"] = _CRASH_REASON
    slot = await sm.status("chat")
    assert slot.state == SlotState.ERROR

    # Probe goes silent, but nothing positively says "stopped on purpose".
    container_stub.unit_failure_by_slot.pop("chat")

    slot = await sm.status("chat")

    assert slot.state == SlotState.ERROR
    # The crash verdict (operator-facing message) survives untouched.
    assert slot.metadata.get("message") == _CRASH_REASON
