"""A bounded seam call that times out must land the slot in ERROR (#1869/#1870).

Before #1869, ``providers/container.py`` issued several ``systemctl``/podman
calls with no bound at all (``timeout=None``), so a wedged child hung the
executor thread forever — the load never resolved, and the operator saw no
error at all (#1870). Now every such call goes through
``hal0.system.seam.bounded_call`` and raises the typed
:class:`hal0.system.seam.SeamTimeout` on expiry. This module checks that
error reaches the slot state machine exactly like any other load failure:
stamped onto the record as ``ERROR`` with a readable message, re-raised to
the caller, and counted against the crash-loop breaker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from hal0.system.seam import SeamTimeout
from tests.slots.conftest import FakeContainerProvider


async def test_seam_timeout_on_load_lands_error_with_the_seam_message(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """``load()`` re-raises the typed SeamTimeout and stamps ERROR with its
    message — the same "never swallow" contract every other load failure
    (SlotSpawnFailed, SlotOutputSanityFailed, ...) already gets."""
    sm = SlotManager()
    seam_error = SeamTimeout(
        ["systemctl", "restart", "hal0-slot@chat.service"], 180.0, "activating"
    )
    container_stub.fail_load = seam_error

    with pytest.raises(SeamTimeout) as excinfo:
        await sm.load("chat")

    assert excinfo.value.code == "slot.seam_timeout"
    assert sm._current_state("chat") == SlotState.ERROR

    slot = await sm.status("chat")
    message = slot.metadata.get("message") or ""
    assert "did not return within 180s" in message
    assert "activating" in message
    # Counted like any other load failure — the crash-loop breaker must see it.
    assert slot.metadata.get("load_failures") == 1


async def test_seam_timeout_on_load_is_a_hal0_error_with_a_504_status(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The typed error carries the status the API error-envelope middleware
    (``hal0.api.middleware.error_codes``) needs to render a 504, not a bare
    500 ``system.internal`` (#1424's original complaint, extended to timeouts)."""
    sm = SlotManager()
    container_stub.fail_load = SeamTimeout(["systemctl", "daemon-reload"], 20.0, None)

    with pytest.raises(SeamTimeout) as excinfo:
        await sm.load("chat")

    assert excinfo.value.status == 504
    assert excinfo.value.details["budget_s"] == 20.0
    assert excinfo.value.details["last_state"] is None
