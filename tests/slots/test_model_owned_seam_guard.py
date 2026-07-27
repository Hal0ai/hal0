"""spec-hw-slot-ownership §1: the model-owned-key guard lives at the WRITE SEAM.

``mtp`` / ``enable_thinking`` / ``vision`` are typed MODEL tuning. The guard
that refuses them on a slot write used to be called from exactly one place —
``api/routes/slots._reject_model_owned_config_keys`` — so it only covered
requests arriving over HTTP. Every in-process writer walked around it, and the
same defect surfaced three times:

  1. the slot drawer carried the caps (drawer-ownership merge),
  2. stack-CREATED slots were born with them (#1356),
  3. stack-RECONCILED slots kept being written with them — #1356's own comment
     asserted ``_reconciled_stack_slot`` had already stopped, which was false.

Symptom-patching one caller at a time is what produced three rounds. The guard
now runs inside ``hal0.slots.config_write.reconcile_slot_updates`` (the seam
``SlotManager.update_config`` and the stacks apply engine share) and inside
``SlotManager.create`` (which has no ``base`` to merge onto, so it cannot use
the seam). These tests pin the seam contract per bypass path, so a fourth
construction path cannot quietly reintroduce the class.

Targeted run:
    python -m pytest tests/slots/test_model_owned_seam_guard.py -q
"""

from __future__ import annotations

import pytest

from hal0.errors import BadRequest
from hal0.slot_config import MODEL_OWNED_SLOT_KEYS
from hal0.slots.config_write import reconcile_slot_updates
from hal0.slots.manager import SlotManager


def _base() -> dict:
    return {"name": "agent", "port": 8087, "device": "gpu-rocm", "model": {"default": "m"}}


class TestSeamRejectsWriteIntent:
    """``reconcile_slot_updates`` — the shared update_config / stacks seam."""

    @pytest.mark.parametrize("key", sorted(MODEL_OWNED_SLOT_KEYS))
    def test_each_model_owned_key_is_refused(self, key: str) -> None:
        with pytest.raises(BadRequest) as ei:
            reconcile_slot_updates(_base(), {key: True})
        assert ei.value.code == "slot.model_owned_key_denied"
        assert key in ei.value.details["keys"]

    def test_none_valued_key_is_still_refused(self) -> None:
        """``{"mtp": None}`` is a delete-key write, not an absence.

        ``merge_slot_config`` treats ``None`` as delete. The stacks engine used
        this deliberately to reset a slot to "Auto", which is precisely a write
        of a model-owned key and must be refused like any other.
        """
        with pytest.raises(BadRequest):
            reconcile_slot_updates(_base(), {"mtp": None})

    def test_all_offenders_are_reported_together(self) -> None:
        with pytest.raises(BadRequest) as ei:
            reconcile_slot_updates(
                _base(), {"vision": True, "mtp": False, "enable_thinking": True}
            )
        assert ei.value.details["keys"] == ["enable_thinking", "mtp", "vision"]

    def test_legitimate_updates_still_pass(self) -> None:
        out = reconcile_slot_updates(_base(), {"model": {"context_size": 4096}})
        assert out["model"]["context_size"] == 4096
        assert out["model"]["default"] == "m", "sibling [model] keys survive the merge"


class TestSeamGuardsIntentNotState:
    """A not-yet-folded slot must stay writable."""

    def test_preexisting_key_in_base_does_not_block_an_unrelated_update(self) -> None:
        """The guard checks ``updates``, never the merged result.

        A slot whose TOML still carries ``mtp`` from before the one-shot
        ``hal0 slot migrate-caps`` fold has to remain updatable — otherwise
        every PATCH to a pre-migration slot would 400 and the box would be
        stuck until migration ran. The stale key is swept by
        ``migrate-caps`` and ``updater._strip_ineligible_slot_mtp``, not by
        refusing unrelated writes.
        """
        base = {**_base(), "mtp": True}
        out = reconcile_slot_updates(base, {"port": 8090})
        assert out["port"] == 8090
        assert out["mtp"] is True, "pre-existing value is preserved, not stripped"


class TestCreatePathGuard:
    """``SlotManager.create`` builds its config directly — guarded explicitly."""

    async def test_create_refuses_a_slot_born_with_a_model_owned_key(
        self, tmp_hal0_home: str
    ) -> None:
        sm = SlotManager()
        with pytest.raises(BadRequest) as ei:
            await sm.create(
                "born-bad",
                {
                    "name": "born-bad",
                    "port": 8091,
                    "type": "llm",
                    "device": "gpu-rocm",
                    "vision": True,
                },
            )
        assert ei.value.code == "slot.model_owned_key_denied"

    async def test_create_without_model_owned_keys_succeeds(self, tmp_hal0_home: str) -> None:
        sm = SlotManager()
        slot = await sm.create(
            "born-ok",
            {"name": "born-ok", "port": 8092, "type": "llm", "device": "gpu-rocm"},
        )
        assert slot is not None


class TestUpdateConfigPathGuard:
    """``SlotManager.update_config`` inherits the guard via the seam."""

    async def test_update_config_refuses_a_model_owned_key(self, tmp_hal0_home: str) -> None:
        sm = SlotManager()
        await sm.create(
            "updatable",
            {"name": "updatable", "port": 8093, "type": "llm", "device": "gpu-rocm"},
        )
        with pytest.raises(BadRequest) as ei:
            await sm.update_config("updatable", {"enable_thinking": True})
        assert ei.value.code == "slot.model_owned_key_denied"
