"""Per spec §1.2 / §10: family_defaults.toml data is cleared for 1.0.

The schema layer (the [family] table) stays so a future spec can re-introduce
family-specific recipes as a layer; the data is gone because the 1.0 catalog
moves family-specific recipes into profile.<family>-<variant> entries.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_FAMILY_DEFAULTS = (
    Path(__file__).resolve().parents[2]
    / "src/hal0/config/data/family_defaults.toml"
)


def test_family_defaults_has_no_data() -> None:
    """[family] table exists but contains no entries (cleared for 1.0)."""
    raw = tomllib.loads(_FAMILY_DEFAULTS.read_text())
    family_table = raw.get("family", {})
    assert family_table == {}, (
        f"family_defaults.toml [family] table must be empty for 1.0; "
        f"got: {family_table}"
    )


def test_family_defaults_parseable() -> None:
    """Cleared file parses as valid TOML (no syntax breakage)."""
    raw = tomllib.loads(_FAMILY_DEFAULTS.read_text())
    assert isinstance(raw, dict), (
        f"family_defaults.toml did not parse to a dict; got {type(raw)}"
    )
