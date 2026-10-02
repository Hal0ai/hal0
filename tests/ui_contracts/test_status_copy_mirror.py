"""The UI's consequence-first status copy covers every SlotState wire value.

Mirrors ``tests/ui_contracts/test_flag_aliases_mirror.py``'s approach: parse
the JS/TS object literal out of ``status-copy.ts`` so a new
``hal0.slots.state.SlotState`` member fails CI here instead of silently
shipping a slot phase with no operator-facing "what this means" sentence.
"""

import json
import re
from pathlib import Path

from hal0.slots.state import SlotState


def _parse_ts_string_record(src: str, export_name: str) -> dict[str, str]:
    """Extract ``export const <export_name>: ... = { ... }``'s strings.

    ``status-copy.ts`` writes each entry as ``key: 'single-quoted value',`` —
    real TS (with a type annotation on the const), not strict JSON, so this
    pulls out the object body and re-quotes it into JSON rather than relying
    on ``json.loads`` directly.
    """
    m = re.search(export_name + r"[^=]*=\s*\{(.*?)\n\}", src, re.S)
    assert m, f"{export_name} object literal not found in status-copy.ts"
    body = m.group(1)
    entries = re.findall(r"(\w+):\s*'((?:[^'\\]|\\.)*)'", body)
    assert entries, f"no key: 'value' entries parsed for {export_name}"
    return {key: value for key, value in entries}


def test_slot_state_copy_covers_every_slotstate_member() -> None:
    src = Path("ui/src/dash/status-copy.ts").read_text()
    ui = _parse_ts_string_record(src, "SLOT_STATE_COPY")
    backend = {s.value for s in SlotState}
    assert set(ui) == backend
    for state, copy in ui.items():
        assert len(copy) > 10, f"{state} has a suspiciously short status line"


def test_service_health_copy_covers_up_stopped_down() -> None:
    src = Path("ui/src/dash/status-copy.ts").read_text()
    ui = _parse_ts_string_record(src, "SERVICE_HEALTH_COPY")
    assert set(ui) == {"up", "stopped", "down"}


def test_status_copy_json_round_trips_as_a_sanity_check() -> None:
    """Belt-and-braces: the same entries also parse as valid JSON values once
    re-quoted, so a stray unescaped quote in a copy string fails loudly here
    instead of silently truncating the regex match above."""
    src = Path("ui/src/dash/status-copy.ts").read_text()
    ui = _parse_ts_string_record(src, "SLOT_STATE_COPY")
    dumped = json.dumps(ui)
    assert json.loads(dumped) == ui
