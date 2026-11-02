"""A slot file that still carries tuning keys (ADR-0029) never launches.

The one-shot conversion that used to move them is gone, so the box has to say
what to do: the launch, ``hal0 config validate`` and ``/api/slots`` all name
the keys and the manual fix, and none of them points at a removed command.
"""

from __future__ import annotations

import pytest

from hal0.config import paths
from hal0.errors import UnprocessableEntity
from hal0.providers.container import _refuse_unconverted
from hal0.slot_view import CONVERSION_FIX, conversion_blocked, unconverted_slot_files

_BLOCKED = {
    "name": "agent",
    "port": 8082,
    "profile": "chat",
    "model": {"default": "m", "context_size": 8192},
    "server": {"extra_args": "-ctk q8_0"},
}
_CLEAN = {"name": "agent", "port": 8082, "profile": "chat", "model": {"default": "m"}}


def test_a_slot_carrying_tuning_keys_is_reported_with_the_manual_fix() -> None:
    blocked = conversion_blocked(_BLOCKED)
    assert blocked is not None
    assert "[model].context_size" in blocked["reason"]
    assert "[server].extra_args" in blocked["reason"]
    assert blocked["fix"] == CONVERSION_FIX
    assert "running_pre_conversion" not in blocked


def test_a_clean_slot_is_not_reported() -> None:
    assert conversion_blocked(_CLEAN) is None


def test_the_launch_refuses_and_says_what_to_do() -> None:
    with pytest.raises(UnprocessableEntity) as exc:
        _refuse_unconverted(_BLOCKED)
    assert exc.value.code == "slot.unconverted"
    assert exc.value.details["keys"] == ["[model].context_size", "[server].extra_args"]
    assert "Delete those keys from the slot file" in str(exc.value)
    assert "profile migrate" not in str(exc.value)


def test_the_typed_load_refuses_and_says_what_to_do() -> None:
    from hal0.config.schema import SlotConfig

    with pytest.raises(ValueError, match=r"slot\.unconverted") as exc:
        SlotConfig.model_validate({**_BLOCKED, "type": "llm"})
    assert "delete them from the slot file" in str(exc.value)
    assert "profile migrate" not in str(exc.value)


def test_the_scan_names_each_slot_file_with_the_fix() -> None:
    root = paths.slots_config_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "143.toml").write_text(
        '[slot]\nid = 143\nname = "agent"\nport = 8082\n[model]\ncontext_size = 8192\n'
    )
    (root / "clean.toml").write_text('name = "clean"\nport = 8083\nprofile = "chat"\n')
    (root / "broken.toml").write_text("this is = = not toml")

    found = unconverted_slot_files()

    assert [(f["target"], f["slot"]) for f in found] == [("143.toml", "agent")]
    assert "[model].context_size" in found[0]["reason"]
    assert CONVERSION_FIX in found[0]["reason"]


def test_the_scan_is_empty_without_a_slots_directory() -> None:
    assert unconverted_slot_files() == []
