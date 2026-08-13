"""Unit tests for :mod:`hal0.agents.anchor_window` — #1867's window preflight.

Covers the ct150 repro shape (a model declaring 96000 behind a slot whose own
``[model].context_size`` ceiling is 4096), the model-side shape it must not be
confused with, the "no evidence yet" case, and the drift guard tying hal0's
copy of Hermes' ``MINIMUM_CONTEXT_LENGTH`` to the real constant.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.agents import anchor_window as aw


def _write_slot(slots_dir: Path, name: str, body: str) -> None:
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / f"{name}.toml").write_text(body, encoding="utf-8")


class TestResolveAnchorWindow:
    """The ct150 shape and its neighbours, resolved from injected facts only."""

    def test_slot_ceiling_below_floor_is_named_with_both_numbers_and_a_fix(
        self, tmp_path: Path
    ) -> None:
        """THE repro: model declares 96000, slot ceiling is 4096, Hermes refuses.

        The operator must be able to act off this message alone — it has to
        carry the slot, that slot's configured ceiling, the required floor, and
        the command that repairs it.
        """
        slots = tmp_path / "slots"
        _write_slot(slots, "agent", '[model]\ndefault = "brain-sft"\ncontext_size = 4096\n')
        window = aw.resolve_anchor_window(
            "hal0/agent",
            # What the gateway advertises for the agent slot on that box —
            # min(96000 model window, 4096 slot ceiling).
            contexts={"agent": 4096, "brain": 96000},
            floor=64_000,
            floor_source="hermes",
            slots_dir=slots,
        )
        assert window.slot == "agent"
        assert window.effective == 4096
        assert window.ceiling == 4096
        assert window.verdict == "below_floor"
        assert window.ceiling_is_binding is True

        message = window.message()
        assert "4,096" in message  # the window Hermes sees
        assert "64,000" in message  # the floor it needs
        assert "agent" in message  # which slot
        assert str(slots / "agent.toml") in message  # where the ceiling lives
        assert "hal0 slot edit agent --ctx-size 65536" in message
        assert "hal0 slot restart agent" in message
        assert window.fix_command in message

    def test_a_ceiling_above_the_floor_with_a_small_model_blames_the_model(
        self, tmp_path: Path
    ) -> None:
        """The other way under the floor: the ceiling is fine, the model is not.

        Telling this operator to raise a 65536 ceiling would be useless advice,
        so the message must point at the model instead and must NOT hand them
        the ``--ctx-size`` command.
        """
        slots = tmp_path / "slots"
        _write_slot(slots, "agent", "[model]\ncontext_size = 65536\n")
        window = aw.resolve_anchor_window(
            "hal0/agent",
            contexts={"agent": 32768},
            floor=64_000,
            floor_source="hermes",
            slots_dir=slots,
        )
        assert window.verdict == "below_floor"
        assert window.ceiling_is_binding is False
        message = window.message()
        assert "32,768" in message
        assert "65,536" in message
        assert "--ctx-size" not in message
        assert "hal0 slot edit agent --model" in message

    def test_window_at_or_above_the_floor_is_ok(self, tmp_path: Path) -> None:
        slots = tmp_path / "slots"
        _write_slot(slots, "agent", "[model]\ncontext_size = 65536\n")
        window = aw.resolve_anchor_window(
            "hal0/agent", contexts={"agent": 65536}, floor=64_000, slots_dir=slots
        )
        assert window.verdict == "ok"
        assert "65,536" in window.message()

    def test_no_advertised_window_is_unknown_not_a_failure(self, tmp_path: Path) -> None:
        """No evidence is not evidence of a failure — a preflight that cries
        wolf when nothing is loaded is one operators learn to ignore."""
        window = aw.resolve_anchor_window(
            "hal0/agent", contexts={}, floor=64_000, slots_dir=tmp_path / "slots"
        )
        assert window.verdict == "unknown"
        assert window.effective is None
        assert "cannot check" in window.message()

    def test_a_bare_pinned_slot_id_resolves_the_same_slot(self, tmp_path: Path) -> None:
        """``HAL0_HERMES_LIVE_RESOLVE=0`` pins the raw slot alias, not the virtual."""
        slots = tmp_path / "slots"
        _write_slot(slots, "agent", "[model]\ncontext_size = 4096\n")
        window = aw.resolve_anchor_window(
            "agent", contexts={"agent": 4096}, floor=64_000, slots_dir=slots
        )
        assert window.slot == "agent"
        assert window.verdict == "below_floor"

    def test_the_virtual_id_wins_over_the_alias_when_both_are_advertised(
        self, tmp_path: Path
    ) -> None:
        window = aw.resolve_anchor_window(
            "hal0/agent",
            contexts={"hal0/agent": 65536, "agent": 4096},
            floor=64_000,
            slots_dir=tmp_path,
        )
        assert window.effective == 65536

    def test_fallback_floor_says_so_in_the_message(self, tmp_path: Path) -> None:
        window = aw.resolve_anchor_window(
            "hal0/agent",
            contexts={"agent": 4096},
            floor=64_000,
            floor_source="fallback:no-venv",
            slots_dir=tmp_path,
        )
        assert "pinned copy" in window.message()


class TestReadSlotCeiling:
    def test_missing_file_has_no_ceiling(self, tmp_path: Path) -> None:
        assert aw.read_slot_ceiling("agent", slots_dir=tmp_path) is None

    def test_garbage_ceiling_reads_as_absent(self, tmp_path: Path) -> None:
        """A hand-edited ``"64k"`` is treated as no ceiling — same posture as
        :func:`hal0.providers.container._resolve_context_size` (#1852)."""
        _write_slot(tmp_path, "agent", '[model]\ncontext_size = "64k"\n')
        assert aw.read_slot_ceiling("agent", slots_dir=tmp_path) is None

    def test_ctx_size_alias_is_accepted(self, tmp_path: Path) -> None:
        _write_slot(tmp_path, "agent", "[model]\nctx_size = 8192\n")
        assert aw.read_slot_ceiling("agent", slots_dir=tmp_path) == 8192

    def test_unparsable_toml_reads_as_absent(self, tmp_path: Path) -> None:
        _write_slot(tmp_path, "agent", "[model\ncontext_size = 4096\n")
        assert aw.read_slot_ceiling("agent", slots_dir=tmp_path) is None


class TestRecommendedCeiling:
    @pytest.mark.parametrize(
        ("floor", "expected"),
        [(64_000, 65_536), (65_536, 65_536), (65_537, 131_072), (32_000, 32_768)],
    )
    def test_next_power_of_two_at_or_above_the_floor(self, floor: int, expected: int) -> None:
        assert aw.recommended_ceiling(floor) == expected
        assert aw.recommended_ceiling(floor) >= floor


class TestReadHermesMinimumContext:
    """The floor is read FROM Hermes; the constant is only the fallback."""

    def test_reads_the_constant_out_of_the_hermes_interpreter(self, tmp_path: Path) -> None:
        """Runs a REAL interpreter against a REAL fake ``agent`` package, so the
        probe's import line is exercised rather than mocked away."""
        pkg = tmp_path / "agent"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "model_metadata.py").write_text("MINIMUM_CONTEXT_LENGTH = 70000\n", encoding="utf-8")
        shim = tmp_path / "python-shim"
        shim.write_text(
            f'#!/bin/sh\nexec {sys.executable} "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)

        def run(argv: list[str], **kwargs: Any) -> Any:
            kwargs.pop("cwd", None)
            return subprocess.run(argv, cwd=tmp_path, **kwargs)  # nosec B603 — test shim

        floor, source = aw.read_hermes_minimum_context(shim, run=run)
        assert (floor, source) == (70000, "hermes")

    def test_no_venv_falls_back_to_the_pinned_constant(self) -> None:
        floor, source = aw.read_hermes_minimum_context(None)
        assert floor == aw.HERMES_MINIMUM_CONTEXT_LENGTH
        assert source == "fallback:no-venv"

    def test_a_failing_probe_falls_back_rather_than_raising(self) -> None:
        def run(_argv: list[str], **_kwargs: Any) -> Any:
            return SimpleNamespace(returncode=1, stdout="")

        assert aw.read_hermes_minimum_context("/nope/python", run=run) == (
            aw.HERMES_MINIMUM_CONTEXT_LENGTH,
            "fallback:probe-failed",
        )

    def test_an_unparsable_answer_falls_back(self) -> None:
        def run(_argv: list[str], **_kwargs: Any) -> Any:
            return SimpleNamespace(returncode=0, stdout="lots\n")

        assert aw.read_hermes_minimum_context("/nope/python", run=run) == (
            aw.HERMES_MINIMUM_CONTEXT_LENGTH,
            "fallback:unparsable",
        )

    def test_an_exploding_runner_falls_back(self) -> None:
        def run(_argv: list[str], **_kwargs: Any) -> Any:
            raise OSError("no such binary")

        floor, source = aw.read_hermes_minimum_context("/nope/python", run=run)
        assert floor == aw.HERMES_MINIMUM_CONTEXT_LENGTH
        assert source.startswith("fallback:")


class TestFloorDoesNotDrift:
    """One number, one place — and it must still match the real Hermes."""

    def test_every_hal0_side_copy_of_the_floor_is_this_constant(self) -> None:
        """The two pre-existing test-side copies (#1827/#1852) now import this
        module's constant, so the floor cannot be raised in one place and left
        stale in another."""
        from tests.install.test_static_seeds import _HERMES_MIN_CONTEXT as seeds_floor
        from tests.providers.test_container import _HERMES_MIN_CONTEXT as container_floor

        assert seeds_floor is aw.HERMES_MINIMUM_CONTEXT_LENGTH
        assert container_floor is aw.HERMES_MINIMUM_CONTEXT_LENGTH

    def test_matches_the_installed_hermes_when_one_is_reachable(self) -> None:
        """The real drift check. Hermes is not importable from hal0's venv, so
        this can only run where a provisioned Hermes venv exists (a box, or a
        dev host with one) — it skips elsewhere rather than asserting nothing."""
        python = Path("/var/lib/hal0/venvs/hermes/bin/python")
        if not python.exists():
            pytest.skip("no provisioned Hermes venv on this host")
        floor, source = aw.read_hermes_minimum_context(python)
        if source != "hermes":
            pytest.skip(f"Hermes venv present but did not answer ({source})")
        assert floor == aw.HERMES_MINIMUM_CONTEXT_LENGTH, (
            f"the installed Hermes requires {floor} but hal0's "
            f"HERMES_MINIMUM_CONTEXT_LENGTH says {aw.HERMES_MINIMUM_CONTEXT_LENGTH} — "
            "update hal0.agents.anchor_window.HERMES_MINIMUM_CONTEXT_LENGTH"
        )
