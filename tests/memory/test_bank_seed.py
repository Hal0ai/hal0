"""Default banks are seeded when hal0-api first gets the engine (#3518).

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_bank_seed.py -q
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog

from hal0.memory import SelfHealingMemoryProvider, bank_seed
from hal0.memory.hindsight_provider import HindsightProvider

SHARED = bank_seed.SEED_BANKS["shared"]
PRIVATE = bank_seed.SEED_BANKS["private:hermes"]


class Engine:
    """The slice of the Hindsight client the seeding uses.

    ``banks`` maps an engine bank id to ``{"documents", "facts", "overrides"}``;
    a bank that is not in it does not exist.
    """

    def __init__(self, banks: dict[str, dict[str, Any]] | None = None) -> None:
        self.banks = banks if banks is not None else {}
        self.fail_import: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def _error(self, method: str, path: str, status: int) -> httpx.HTTPStatusError:
        request = httpx.Request(method, f"http://engine{path}")
        return httpx.HTTPStatusError(
            "error", request=request, response=httpx.Response(status, request=request)
        )

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path))
        bank, leaf = path.split("/")[4], path.rsplit("/", 1)[-1]
        if method == "GET":
            if bank not in self.banks:
                raise self._error(method, path, 404)
            row = self.banks[bank]
            if leaf == "stats":
                assert kwargs["params"] == {"refresh": "true"}
                return {"total_documents": row["documents"], "total_nodes": row["facts"]}
            return {"bank_id": bank, "config": {}, "overrides": row["overrides"]}
        if bank in self.fail_import:
            raise self._error(method, path, 500)
        row = self.banks.setdefault(bank, {"documents": 0, "facts": 0, "overrides": {}})
        row["overrides"] = dict(kwargs["json_body"]["bank"])
        return {"bank_id": bank, "config_applied": True}

    def imports(self) -> list[str]:
        return [path.split("/")[4] for method, path in self.calls if method == "POST"]


def bank(documents: int = 0, facts: int = 0, **overrides: Any) -> dict[str, Any]:
    return {"documents": documents, "facts": facts, "overrides": overrides}


def provider(engine: Engine, **kwargs: Any) -> HindsightProvider:
    kwargs.setdefault("unified_bank", True)  # a legacy unified store
    return HindsightProvider(client=engine, **kwargs)


def two_bank(engine: Engine, **kwargs: Any) -> HindsightProvider:
    """A new store's provider (ADR-0058 decision 7)."""
    kwargs.setdefault("bank", "hal0:shared")
    return HindsightProvider(client=engine, two_bank=True, **kwargs)


def run_hook(prov: Any) -> bool:
    return asyncio.run(bank_seed.heal_hook(prov)())


# ── which banks ───────────────────────────────────────────────────────────────


def test_a_legacy_unified_box_seeds_only_the_platform_bank() -> None:
    """Unified bank: private memory lives in the platform bank, so no private bank."""
    engine = Engine()

    assert run_hook(provider(engine)) is True
    assert engine.imports() == ["shared"]
    assert engine.banks["shared"]["overrides"] == SHARED["bank"]


def test_a_renamed_platform_bank_is_the_one_seeded() -> None:
    """[memory] bank (ADR-0052): no stray bank called ``shared`` is created."""
    engine = Engine()

    assert run_hook(provider(engine, bank="hal0-brain")) is True
    assert engine.imports() == ["hal0-brain"]
    assert "shared" not in engine.banks
    assert not any("/banks/shared/" in path for _, path in engine.calls)


def test_a_legacy_split_box_seeds_only_the_platform_bank() -> None:
    """A store on its old layout is folded into the two banks, not seeded into
    new ones, and its per-agent banks are left as they are."""
    engine = Engine()

    assert run_hook(provider(engine, unified_bank=False)) is True
    assert engine.imports() == ["shared"]
    assert "private__hermes" not in engine.banks


def test_a_two_bank_box_seeds_both_banks() -> None:
    engine = Engine()

    assert run_hook(two_bank(engine)) is True
    assert engine.imports() == ["hal0:shared", "hal0:private"]
    assert engine.banks["hal0:shared"]["overrides"] == SHARED["bank"]
    assert engine.banks["hal0:private"]["overrides"] == PRIVATE["bank"]
    assert "shared" not in engine.banks and "private__hermes" not in engine.banks


def test_two_bank_targets_follow_the_configured_names() -> None:
    prov = two_bank(Engine(), bank="a:shared", private_bank="a:private")
    assert bank_seed.seed_targets(prov) == {"a:shared": SHARED, "a:private": PRIVATE}


def test_targets_are_empty_for_a_provider_that_is_not_hindsight() -> None:
    class _Other:
        hindsight_client = None

    assert bank_seed.seed_targets(_Other()) == {}
    assert run_hook(_Other()) is True


# ── which state ───────────────────────────────────────────────────────────────


def test_a_missing_bank_is_seeded() -> None:
    engine = Engine()

    run_hook(provider(engine))

    assert engine.imports() == ["shared"]


def test_an_empty_bank_with_no_mission_is_seeded() -> None:
    engine = Engine({"shared": bank()})

    run_hook(provider(engine))

    assert engine.imports() == ["shared"]


def test_a_bank_with_facts_and_no_mission_is_not_touched() -> None:
    engine = Engine({"shared": bank(documents=3, facts=40)})

    assert run_hook(provider(engine)) is True
    assert engine.imports() == []
    assert engine.banks["shared"]["overrides"] == {}


def test_a_bank_with_documents_only_is_not_touched() -> None:
    engine = Engine({"shared": bank(documents=1)})

    run_hook(provider(engine))

    assert engine.imports() == []


def test_an_empty_bank_with_an_operators_mission_is_not_touched() -> None:
    engine = Engine({"shared": bank(retain_mission="mine")})

    run_hook(provider(engine))

    assert engine.imports() == []
    assert engine.banks["shared"]["overrides"] == {"retain_mission": "mine"}


def test_unreadable_counts_leave_the_bank_alone() -> None:
    engine = Engine({"shared": bank()})
    engine.banks["shared"]["documents"] = None

    run_hook(provider(engine))

    assert engine.imports() == []


def test_a_second_run_writes_nothing() -> None:
    engine = Engine()
    prov = provider(engine)
    run_hook(prov)
    engine.calls.clear()

    assert run_hook(prov) is True
    assert engine.imports() == []


# ── failures and logging ──────────────────────────────────────────────────────


def test_one_failed_bank_does_not_stop_the_other_and_rearms_the_hook() -> None:
    engine = Engine()
    engine.fail_import = {"hal0:shared"}
    prov = two_bank(engine)

    assert run_hook(prov) is False
    assert "hal0:private" in engine.banks
    engine.fail_import = set()
    assert run_hook(prov) is True


def test_logs_once_when_it_seeds_and_nothing_when_it_skips() -> None:
    engine = Engine()
    prov = two_bank(engine)

    with structlog.testing.capture_logs() as first:
        run_hook(prov)
    with structlog.testing.capture_logs() as second:
        run_hook(prov)

    assert [(e["event"], e["log_level"], e["banks"]) for e in first] == [
        ("hal0.memory.banks_seeded", "info", ["hal0:shared", "hal0:private"])
    ]
    assert second == []


def test_the_hook_runs_when_the_degraded_provider_heals() -> None:
    """The seam hal0-api arms it on: SelfHealingMemoryProvider's heal hooks."""
    engine = Engine()

    class _Degraded:
        degraded = True
        hindsight_client = None

    shell = SelfHealingMemoryProvider(_Degraded(), cfg=None)
    assert shell.add_heal_hook(bank_seed.heal_hook(shell)) is True
    shell._target = provider(engine, bank="hal0-brain")  # what try_heal swaps in

    assert asyncio.run(shell.run_heal_hooks()) is True
    assert list(engine.banks) == ["hal0-brain"]
    assert shell.pending_heal_hooks == 0


# ── an embedder switch owns the banks until it has finished ──────────────────


def test_nothing_is_seeded_while_an_embedder_switch_holds_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The engine can be back at its normal address a moment before the switch
    lifts its gate, with a bank between its delete and its rename: that bank
    looks missing, and seeding it would collide with the rename. The hook
    stays armed and seeds once the switch is done."""
    from hal0.memory import embedder_switch, write_gate

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    monkeypatch.setattr(embedder_switch, "isolated_port", lambda: None)
    write_gate.engage_gate(run_id="3f9a1c2e")
    engine = Engine()
    prov = provider(engine)

    assert run_hook(prov) is False
    assert engine.calls == []

    write_gate.lift_gate()
    assert run_hook(prov) is True
    assert engine.imports() == ["shared"]


def test_an_isolated_engine_is_not_seeded_either(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.memory import embedder_switch

    monkeypatch.setattr(embedder_switch, "blocking_reason", lambda: "isolated on 45123")
    engine = Engine()

    assert run_hook(provider(engine)) is False
    assert engine.calls == []


def test_a_check_that_cannot_be_made_is_treated_as_a_switch_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hal0.memory import embedder_switch

    def broken() -> str | None:
        raise OSError("state directory unreadable")

    monkeypatch.setattr(embedder_switch, "blocking_reason", broken)
    assert "could not be checked" in (bank_seed.switch_in_progress() or "")


# ── the installer's helper: namespace -> this box's engine bank ──────────────


def _layout(monkeypatch, layout) -> None:
    from hal0.memory import bank_layout as bl

    monkeypatch.setattr(bl, "current_layout", lambda _cfg, **_kw: layout)


def test_the_installer_seeds_the_two_banks_on_a_two_bank_box(monkeypatch) -> None:
    from hal0.config.schema import MemoryConfig
    from hal0.memory import bank_layout as bl

    _layout(monkeypatch, bl.BankLayout(bl.LAYOUT_TWO_BANK))
    cfg = MemoryConfig()
    assert bank_seed.engine_bank_for("shared", cfg) == "hal0:shared"
    assert bank_seed.engine_bank_for("private:hermes", cfg) == "hal0:private"


def test_the_installer_seeds_only_the_platform_bank_on_an_old_layout(monkeypatch) -> None:
    from hal0.config.schema import MemoryConfig
    from hal0.memory import bank_layout as bl

    _layout(monkeypatch, bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared"))
    cfg = MemoryConfig()
    assert bank_seed.engine_bank_for("shared", cfg) == "shared"
    assert bank_seed.engine_bank_for("private:hermes", cfg) is None


def _main_box(monkeypatch, layout) -> None:
    from types import SimpleNamespace

    from hal0.config import loader
    from hal0.config.schema import MemoryConfig

    monkeypatch.setattr(
        loader, "load_hal0_config", lambda _path=None: SimpleNamespace(memory=MemoryConfig())
    )
    monkeypatch.setattr(bank_seed, "switch_in_progress", lambda: None)
    _layout(monkeypatch, layout)


def test_seed_box_seeds_the_platform_bank_alone_on_an_old_layout(monkeypatch) -> None:
    """#3557: no engine bank named after a namespace (``private:hermes``)."""
    from hal0.memory import bank_layout as bl

    _main_box(monkeypatch, bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared"))
    engine = Engine()
    assert bank_seed.seed_box(client=engine) == ({"shared": "seeded"}, None)
    assert engine.imports() == ["shared"]
    assert engine.banks["shared"]["overrides"] == SHARED["bank"]


def test_seed_box_seeds_both_banks_on_a_two_bank_store(monkeypatch) -> None:
    from hal0.memory import bank_layout as bl

    _main_box(monkeypatch, bl.BankLayout(bl.LAYOUT_TWO_BANK))
    engine = Engine()
    results, deferred = bank_seed.seed_box(client=engine)
    assert deferred is None
    assert results == {"hal0:shared": "seeded", "hal0:private": "seeded"}
    assert engine.banks["hal0:private"]["overrides"] == PRIVATE["bank"]
    assert "private:hermes" not in engine.banks and "shared" not in engine.banks


@pytest.mark.parametrize(
    ("row", "seeded"),
    [
        (bank(documents=3, facts=40, retain_mission="theirs"), False),
        (bank(documents=1, facts=0), False),
        (bank(retain_mission="the operator's"), False),
        (bank(), True),
    ],
)
def test_an_installer_re_run_never_reconfigures_a_bank_that_holds_anything(
    monkeypatch, row, seeded
) -> None:
    """A re-run used to POST /import to the box's live banks and reset their
    mission. It goes through the heal hook's guard (_wants_seeding)."""
    from hal0.memory import bank_layout as bl

    _main_box(monkeypatch, bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared"))
    engine = Engine({"shared": row})
    results, _ = bank_seed.seed_box(client=engine)
    assert results == {"shared": "seeded" if seeded else "skipped"}
    assert engine.imports() == (["shared"] if seeded else [])


def test_seed_box_seeds_nothing_during_an_embedder_switch(monkeypatch) -> None:
    from hal0.memory import bank_layout as bl

    _main_box(monkeypatch, bl.BankLayout(bl.LAYOUT_TWO_BANK))
    monkeypatch.setattr(bank_seed, "switch_in_progress", lambda: "the switch is running")
    engine = Engine()
    assert bank_seed.seed_box(client=engine) == ({}, "the switch is running")
    assert engine.calls == []


def test_seed_box_reports_an_unreachable_engine_as_failed(monkeypatch) -> None:
    from hal0.memory import bank_layout as bl

    _main_box(monkeypatch, bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared"))

    class _Down(Engine):
        async def request_json(self, method, path, **kwargs):
            raise ConnectionError("engine down")

    assert bank_seed.seed_box(client=_Down()) == ({"shared": "failed"}, None)


def test_seed_box_fails_closed_when_the_layout_is_undecided(monkeypatch) -> None:
    from hal0.memory import bank_layout as bl

    _main_box(monkeypatch, bl.BankLayout(bl.LAYOUT_TWO_BANK))

    def undecided(_cfg, **_kw):
        raise bl.LayoutRecordUnsaved("no")

    monkeypatch.setattr(bl, "current_layout", undecided)
    engine = Engine()
    with pytest.raises(bank_seed.SeedUndecided):
        bank_seed.seed_box(client=engine)
    assert engine.calls == []


def test_seed_box_reads_the_installers_paths(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from hal0.config import loader
    from hal0.config.schema import MemoryConfig
    from hal0.memory import bank_layout as bl

    seen: dict[str, object] = {}

    def load(path=None):
        seen["toml"] = path
        return SimpleNamespace(memory=MemoryConfig())

    def layout(_cfg, path=None):
        seen["layout"] = path
        return bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared")

    monkeypatch.setattr(loader, "load_hal0_config", load)
    monkeypatch.setattr(bl, "current_layout", layout)
    monkeypatch.setattr(bank_seed, "switch_in_progress", lambda: None)
    bank_seed.seed_box(
        toml_path=str(tmp_path / "hal0.toml"), state_dir=str(tmp_path / "state"), client=Engine()
    )
    assert seen == {
        "toml": tmp_path / "hal0.toml",
        "layout": tmp_path / "state" / "memory-layout.json",
    }


# ── ``hal0 memory bank seed`` ────────────────────────────────────────────────


def _cli(monkeypatch, outcome):
    from typer.testing import CliRunner

    from hal0.cli import memory_bank_commands as cmd

    seen: dict[str, Any] = {}

    def fake(**kwargs):
        seen.update(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(bank_seed, "seed_box", fake)
    return CliRunner(), cmd.app, seen


def test_cli_seed_passes_the_paths_and_prints_each_bank(monkeypatch) -> None:
    runner, app, seen = _cli(
        monkeypatch, ({"hal0:shared": "seeded", "hal0:private": "skipped"}, None)
    )
    res = runner.invoke(app, ["seed", "--config", "/x/hal0.toml", "--state-dir", "/x/state"])
    assert res.exit_code == 0, res.output
    assert seen == {"toml_path": "/x/hal0.toml", "state_dir": "/x/state"}
    assert "hal0:shared: seeded" in res.output and "hal0:private: skipped" in res.output


def test_cli_seed_exits_1_when_a_bank_failed(monkeypatch) -> None:
    runner, app, _ = _cli(monkeypatch, ({"hal0:shared": "failed"}, None))
    assert runner.invoke(app, ["seed"]).exit_code == 1


def test_cli_seed_exits_1_when_undecided(monkeypatch) -> None:
    runner, app, _ = _cli(monkeypatch, bank_seed.SeedUndecided("no record"))
    res = runner.invoke(app, ["seed", "--json"])
    assert res.exit_code == 1
    assert "no record" in res.output


def test_cli_seed_during_a_switch_is_not_a_failure(monkeypatch) -> None:
    runner, app, _ = _cli(monkeypatch, ({}, "the switch is running"))
    res = runner.invoke(app, ["seed"])
    assert res.exit_code == 4
    assert "not seeding now: the switch is running" in res.output


@pytest.mark.parametrize("results", [{"hal0:shared": "skipped", "hal0:private": "skipped"}, {}])
def test_cli_seed_with_nothing_seeded_exits_3(monkeypatch, results) -> None:
    """The installer must not say "seeded" when every bank was already set up."""
    runner, app, _ = _cli(monkeypatch, (results, None))
    res = runner.invoke(app, ["seed", "--json"])
    assert res.exit_code == 3
    assert json.loads(res.output)["banks"] == results
