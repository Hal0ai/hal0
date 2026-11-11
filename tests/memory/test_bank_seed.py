"""Default banks are seeded when hal0-api first gets the engine (#3518).

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_bank_seed.py -q
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from hal0.memory import SelfHealingMemoryProvider, bank_seed


class Engine:
    """The slice of the Hindsight client the seeding uses."""

    def __init__(self, overrides: dict[str, dict[str, Any]] | None = None) -> None:
        self.overrides = overrides if overrides is not None else {}
        self.missing: set[str] = set()
        self.fail_import: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path))
        bank = path.split("/")[4]
        request = httpx.Request(method, f"http://engine{path}")
        if method == "GET":
            if bank in self.missing:
                raise httpx.HTTPStatusError(
                    "not found", request=request, response=httpx.Response(404, request=request)
                )
            return {"bank_id": bank, "config": {}, "overrides": self.overrides.get(bank, {})}
        if bank in self.fail_import:
            raise httpx.HTTPStatusError(
                "boom", request=request, response=httpx.Response(500, request=request)
            )
        self.missing.discard(bank)
        self.overrides[bank] = dict(kwargs["json_body"]["bank"])
        return {"bank_id": bank, "config_applied": True}


def _seed(engine: Engine) -> dict[str, str]:
    return asyncio.run(bank_seed.seed_missing_banks(engine))


def test_unseeded_banks_get_the_manifests() -> None:
    engine = Engine()
    engine.missing = {"private:hermes"}

    assert _seed(engine) == {"shared": "seeded", "private:hermes": "seeded"}
    assert engine.overrides["shared"] == bank_seed.SEED_BANKS["shared"]["bank"]
    assert engine.overrides["private:hermes"]["retain_mission"].startswith("This agent")
    assert ("POST", "/v1/default/banks/private:hermes/import") in engine.calls


def test_a_second_run_writes_nothing() -> None:
    engine = Engine()
    _seed(engine)
    engine.calls.clear()

    assert _seed(engine) == {"shared": "present", "private:hermes": "present"}
    assert all(method == "GET" for method, _ in engine.calls)


def test_an_operators_mission_is_never_written_over() -> None:
    engine = Engine({"shared": {"retain_mission": "mine"}})

    assert _seed(engine)["shared"] == "present"
    assert engine.overrides["shared"] == {"retain_mission": "mine"}


def test_one_failed_bank_does_not_stop_the_other() -> None:
    engine = Engine()
    engine.fail_import = {"shared"}

    assert _seed(engine) == {"shared": "failed", "private:hermes": "seeded"}


class _Provider:
    def __init__(self, client: Any) -> None:
        self.hindsight_client = client


def test_heal_hook_asks_to_be_run_again_only_on_a_failure() -> None:
    engine = Engine()
    engine.fail_import = {"shared"}
    hook = bank_seed.heal_hook(_Provider(engine))

    assert asyncio.run(hook()) is False
    engine.fail_import = set()
    assert asyncio.run(hook()) is True


def test_heal_hook_has_nothing_to_do_without_a_hindsight_client() -> None:
    assert asyncio.run(bank_seed.heal_hook(_Provider(None))()) is True


def test_the_hook_runs_when_the_degraded_provider_heals() -> None:
    """The seam hal0-api arms it on: SelfHealingMemoryProvider's heal hooks."""
    engine = Engine()

    class _Degraded:
        degraded = True
        hindsight_client = None

    shell = SelfHealingMemoryProvider(_Degraded(), cfg=None)
    assert shell.add_heal_hook(bank_seed.heal_hook(shell)) is True
    shell._target = _Provider(engine)  # what try_heal swaps in

    assert asyncio.run(shell.run_heal_hooks()) is True
    assert set(engine.overrides) == {"shared", "private:hermes"}
    assert shell.pending_heal_hooks == 0
