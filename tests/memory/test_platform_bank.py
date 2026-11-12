"""[memory] bank — the engine bank behind the ``shared`` namespace (ADR-0052)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import MemoryConfig
from hal0.memory.hindsight_provider import HindsightProvider

from .test_unified_bank import RecordingClient

BANK = "Hal0::Platform"


def _provider(**kw) -> HindsightProvider:
    return HindsightProvider(client=RecordingClient(), client_id="hermes", unified_bank=True, **kw)


def test_default_banks_are_the_two_bank_layout() -> None:
    """ADR-0058 decision 7: a new store's banks. A provider built without a
    layout keeps ``shared`` (what a legacy store's record gives it)."""
    assert MemoryConfig().bank == "hal0:shared"
    assert MemoryConfig().private_bank == "hal0:private"
    assert _provider().platform_bank == "shared"


@pytest.mark.parametrize("bad", ["", "-x", "a/b", "private__hermes", "agents"])
def test_invalid_or_reserved_private_bank_names_are_rejected(bad: str) -> None:
    with pytest.raises(ValidationError, match="private_bank"):
        MemoryConfig(private_bank=bad)


def test_the_private_bank_must_differ_from_the_platform_bank() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        MemoryConfig(bank="one", private_bank="one")
    assert MemoryConfig(bank="one", private_bank="two").private_bank == "two"


def test_an_old_toml_with_unified_bank_still_loads() -> None:
    """The key left the schema; ``extra: allow`` keeps an old file loading and
    the layout capture is its only reader."""
    cfg = MemoryConfig.model_validate({"unified_bank": False, "bank": "shared"})
    assert cfg.model_extra == {"unified_bank": False}
    assert "unified_bank" not in MemoryConfig.model_fields


@pytest.mark.parametrize("bad", ["", "  ", "-x", "a b", "a/b", "private__hermes", "agents"])
def test_invalid_or_reserved_bank_names_are_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        MemoryConfig(bank=bad)


def test_platform_bank_name_is_accepted() -> None:
    assert MemoryConfig(bank=BANK).bank == BANK


@pytest.mark.asyncio
async def test_shared_and_private_writes_land_in_the_platform_bank() -> None:
    p = _provider(bank=BANK)
    await p.add("public", dataset="shared", client_id="hermes")
    await p.add("secret", dataset="private:hermes", client_id="hermes")
    assert {r["bank_id"] for r in p._client.retained} == {BANK}


@pytest.mark.asyncio
async def test_recall_reads_the_platform_bank_and_labels_items_shared() -> None:
    p = _provider(bank=BANK)
    await p.add("public", dataset="shared", client_id="hermes")
    items = await p.search("public", dataset="shared", client_id="hermes")
    assert [r["bank_id"] for r in p._client.recalled] == [BANK]
    assert items
    assert {i["dataset"] for i in items} == {"shared"}


@pytest.mark.asyncio
async def test_list_labels_items_shared() -> None:
    p = _provider(bank=BANK)
    await p.add("public", dataset="shared", client_id="hermes")
    page = await p.list_items(dataset="shared", client_id="hermes")
    assert page["items"]
    assert {i["dataset"] for i in page["items"]} == {"shared"}


@pytest.mark.asyncio
async def test_default_bank_still_writes_shared() -> None:
    p = _provider()
    await p.add("public", dataset="shared", client_id="hermes")
    assert p._client.retained[0]["bank_id"] == "shared"


def test_non_unified_private_bank_keeps_its_own_name() -> None:
    p = HindsightProvider(client=RecordingClient(), bank=BANK)
    assert p._bank_of("shared") == BANK
    assert p._bank_of("private:hermes") == "private__hermes"
