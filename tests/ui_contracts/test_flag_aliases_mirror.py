"""The UI mirrors hal0.slots.argv.FLAG_ALIASES for pill canonicalization.

Parses the JS object literal out of flags-tune.js so a server-side alias
change fails CI here instead of silently desyncing the drawer's pills.
"""

import json
import re
from pathlib import Path

from hal0.slots.argv import FLAG_ALIASES


def test_ui_flag_alias_mirror_matches_server():
    src = Path("ui/src/dash/flags-tune.js").read_text()
    m = re.search(r"export const FLAG_ALIASES = (\{[^}]*\})", src, re.S)
    assert m, "FLAG_ALIASES export not found in flags-tune.js"
    # the literal is written as strict JSON (double-quoted keys/values) by Task 1
    ui = json.loads(m.group(1))
    assert ui == dict(FLAG_ALIASES)
