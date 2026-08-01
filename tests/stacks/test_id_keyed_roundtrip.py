"""Stacks on an ID-KEYED box — the snapshot→apply round-trip (#1510).

Every other test in ``tests/stacks/`` writes a NAME-keyed fixture
(``agent.toml`` whose embedded ``name`` is ``"agent"``), where the on-disk
stem and the display name are the same string. That is why the id-keying
seam shipped broken: on the live box ``/etc/hal0/slots/`` is fully id-keyed
(``1.toml`` … ``16.toml``, each with ``name = "agent"`` inside), so stem and
display name DIVERGE and every ``f"{entry.slot}.toml"`` in the apply path
addresses a file that does not exist.

These tests use an id-keyed fixture exclusively. They pin the three distinct
failures that divergence produced:

  1. ``_slot_toml_exists`` reported every live slot missing, so apply's
     create-on-apply path cloned it as a second, name-keyed TOML (#1510, the
     first-party producer of the duplicate-slot state in #1422);
  2. the cross-slot write guards excluded the slot's peer set by *stem*, so a
     slot passed by display name saw ITSELF as a peer and rejected its own
     plan; and
  3. the drift fingerprint was keyed by stem at record time and by display
     name at compare time, so an applied stack read as ``modified`` forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.registry.store import ModelRegistry
from hal0.stacks.apply import StackApplyEngine
from hal0.stacks.portable import snapshot_live_stack

# ── fixtures — the live box's layout, not the tests' historical one ──────────


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_id_keyed_slot(
    home: str,
    *,
    stem: str,
    name: str,
    model: str | None = "old-model",
    device: str = "gpu-vulkan",
    extra: list[str] | None = None,
) -> Path:
    """Write ``<stem>.toml`` whose embedded display name is ``name``.

    Mirrors the live shape read off lxc105: a digit stem, the flat (no
    ``[slot]`` section) body the runtime writes, and a self-describing
    ``name`` key.
    """
    lines = [
        f'name = "{name}"',
        'type = "llm"',
        f'device = "{device}"',
        'provider = "llama-server"',
        "port = 8087",
        "vision = false",
        *(extra or []),
    ]
    if model is not None:
        lines += ["[model]", f'default = "{model}"', "context_size = 8192"]
    path = _slots_dir(home) / f"{stem}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=tmp_path / "registry")


def _stack(slot: str = "agent", model: str = "new-model") -> StackConfig:
    return StackConfig(
        name="Live", slots=[StackSlotEntry(slot=slot, model=model, device="gpu-vulkan")]
    )


# ── 1. the round-trip itself ─────────────────────────────────────────────────


class TestSnapshotApplyRoundTrip:
    def test_snapshot_records_the_display_name_not_the_digit_stem(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        """A stack is a PORTABLE artefact — its slot identity is the display
        name, never this box's storage stem. This half already worked; it is
        pinned so a future "fix" doesn't make snapshot emit ``"1"``."""
        write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent", model="ace-saber")
        stack = snapshot_live_stack(registry=reg, name="Live")
        assert [e.slot for e in stack.slots] == ["agent"]

    def test_apply_resolves_a_snapshotted_slot_to_its_id_keyed_file(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        """RED before #1510: ``_slot_path`` built ``agent.toml`` and the plan
        found nothing to change, so snapshot→apply was a silent no-op."""
        path = write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent", model="ace-saber")
        stack = snapshot_live_stack(registry=reg, name="Live")
        # Re-point the snapshot at a different model so a working apply MUST
        # produce a diff on the id-keyed file.
        stack = stack.model_copy(
            update={"slots": [stack.slots[0].model_copy(update={"model": "new-model"})]}
        )

        plan = StackApplyEngine().plan("live", stack)

        touched = {fs.path for fs in plan.change_set.after}
        assert touched == {path}, f"apply must address the id-keyed file, got {touched}"
        assert plan.errors == []
        assert any(
            fs.data != b.data
            for b, fs in zip(plan.change_set.before, plan.change_set.after, strict=True)
        )

    def test_apply_commits_onto_the_id_keyed_file_and_creates_no_duplicate(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        """RED before #1510: apply wrote a brand-new ``agent.toml`` beside the
        live ``1.toml`` — exactly the duplicate-slot state #1422 reports."""
        write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent", model="ace-saber")
        engine = StackApplyEngine()
        plan = engine.plan("live", _stack())
        engine.apply_config(plan)

        stems = sorted(p.stem for p in _slots_dir(tmp_hal0_home).glob("*.toml"))
        assert stems == ["1"], f"apply must not clone the slot, found {stems}"

        import tomllib

        with open(_slots_dir(tmp_hal0_home) / "1.toml", "rb") as f:
            assert tomllib.load(f)["model"]["default"] == "new-model"


class TestMissingSlotDetection:
    def test_an_existing_id_keyed_slot_is_not_reported_missing(self, tmp_hal0_home: str) -> None:
        """RED before #1510: this is the exact call that made
        ``_create_missing_slots`` clone all 16 live slots."""
        from hal0.api.routes.stacks import _missing_slot_names

        write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent")
        assert _missing_slot_names(_stack("agent")) == []

    def test_a_genuinely_absent_slot_is_still_reported_missing(self, tmp_hal0_home: str) -> None:
        """The create-on-apply path must keep working — resolving by name must
        not swallow a slot that really doesn't exist."""
        from hal0.api.routes.stacks import _missing_slot_names

        write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent")
        assert _missing_slot_names(_stack("quick")) == ["quick"]


# ── 2. the cross-slot write guards ───────────────────────────────────────────


class TestGuardsExcludeTheSlotItself:
    def test_default_uniqueness_does_not_fire_against_the_slot_itself(
        self, tmp_hal0_home: str
    ) -> None:
        """RED before #1510: ``_iter_peer_configs`` excludes by STEM, so a slot
        addressed as ``"agent"`` never excluded ``1.toml`` — its own file — and
        the default-uniqueness guard rejected the slot's own plan."""
        write_id_keyed_slot(
            tmp_hal0_home, stem="1", name="agent", model="ace-saber", extra=["default = true"]
        )
        plan = StackApplyEngine().plan("live", _stack())
        assert plan.errors == [], f"the slot conflicted with itself: {plan.errors}"

    def test_stale_duplicate_default_state_does_not_veto_the_apply(
        self, tmp_hal0_home: str
    ) -> None:
        """SC-4 ``changed_keys`` semantics: ``default`` is not a stack field,
        so a stack apply never moves the default-uniqueness invariant — and a
        pre-existing two-defaults-on-disk state it neither created nor touches
        must not veto it (``check_default_uniqueness`` skips when the write
        carries no ``default`` key). This holds identically on an id-keyed
        box: the plan reconciles, and the model change lands."""
        write_id_keyed_slot(
            tmp_hal0_home, stem="1", name="agent", model="ace-saber", extra=["default = true"]
        )
        write_id_keyed_slot(
            tmp_hal0_home, stem="2", name="code", model="other", extra=["default = true"]
        )
        plan = StackApplyEngine().plan("live", _stack())
        assert plan.errors == [], (
            f"a stack apply that never touches default was vetoed: {plan.errors}"
        )
        assert plan.slot_names == ("agent",)


# ── 3. drift ─────────────────────────────────────────────────────────────────


class TestDriftOnAnIdKeyedBox:
    def test_a_fresh_apply_reads_clean(self, tmp_hal0_home: str) -> None:
        """RED before #1510: ``record_active`` fingerprinted a STEM-keyed
        projection (``{"1": ...}``) while ``drift_status`` compared a
        NAME-keyed one (``{"agent": ...}``), so an id-keyed box reported
        ``modified`` the instant it applied anything."""

        class _Catalog:
            def resolve(self, slug: str) -> StackConfig:
                return _stack()

        write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent", model="ace-saber")
        engine = StackApplyEngine()
        plan = engine.plan("live", _stack())
        engine.apply_config(plan)
        engine.record_active(plan, applied_at=1.0)

        assert engine.drift_status(_Catalog()) == {"active": "live", "status": "clean"}

    def test_a_hand_edit_still_reads_modified(self, tmp_hal0_home: str) -> None:
        """Drift must keep its teeth on an id-keyed box too."""

        class _Catalog:
            def resolve(self, slug: str) -> StackConfig:
                return _stack()

        write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent", model="ace-saber")
        engine = StackApplyEngine()
        plan = engine.plan("live", _stack())
        engine.apply_config(plan)
        engine.record_active(plan, applied_at=1.0)

        write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent", model="hand-edited")
        assert engine.drift_status(_Catalog())["status"] == "modified"


# ── 4. the diff rows the UI renders ──────────────────────────────────────────


class TestDiffRowsSpeakDisplayNames:
    def test_diff_row_slot_is_the_display_name_not_the_digit(self, tmp_hal0_home: str) -> None:
        """RED before #1510: ``_diff_rows`` labelled rows ``after.path.stem``,
        so the dry-run preview on an id-keyed box listed ``1`` / ``13`` instead
        of ``agent`` / ``rerank``."""
        from hal0.api.routes.stacks import _diff_rows

        write_id_keyed_slot(tmp_hal0_home, stem="1", name="agent", model="ace-saber")
        plan = StackApplyEngine().plan("live", _stack())
        assert [r["slot"] for r in _diff_rows(plan)] == ["agent"]
