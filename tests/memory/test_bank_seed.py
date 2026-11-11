"""Default banks are seeded when hal0-api first gets the engine (#3518).

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_bank_seed.py -q
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
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
    kwargs.setdefault("unified_bank", True)  # the shipped default
    return HindsightProvider(client=engine, **kwargs)


def run_hook(prov: Any) -> bool:
    return asyncio.run(bank_seed.heal_hook(prov)())


# ── which banks ───────────────────────────────────────────────────────────────


def test_default_box_seeds_only_the_platform_bank() -> None:
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


def test_without_the_unified_bank_the_private_bank_is_the_providers_engine_bank() -> None:
    engine = Engine()

    assert run_hook(provider(engine, unified_bank=False)) is True
    assert engine.imports() == ["shared", "private__hermes"]
    assert engine.banks["private__hermes"]["overrides"] == PRIVATE["bank"]
    assert "private:hermes" not in engine.banks


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
    engine.fail_import = {"shared"}
    prov = provider(engine, unified_bank=False)

    assert run_hook(prov) is False
    assert "private__hermes" in engine.banks
    engine.fail_import = set()
    assert run_hook(prov) is True


def test_logs_once_when_it_seeds_and_nothing_when_it_skips() -> None:
    engine = Engine()
    prov = provider(engine, unified_bank=False)

    with structlog.testing.capture_logs() as first:
        run_hook(prov)
    with structlog.testing.capture_logs() as second:
        run_hook(prov)

    assert [(e["event"], e["log_level"], e["banks"]) for e in first] == [
        ("hal0.memory.banks_seeded", "info", ["shared", "private__hermes"])
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
