"""Static contract between the validation kit registry and its workflow script.

``tests/release-validation/kit.toml`` declares the model tier per phase; the
tiers only hold if ``.claude/workflows/rc-validate.js`` actually pins them on
its ``agent()`` calls — an unpinned call silently inherits the invoking
session's tier (kit v5's motivating defect). These tests keep the two files
from diverging without either PR noticing.
"""

import re
import tomllib
from pathlib import Path

KIT = Path("tests/release-validation/kit.toml")
SCRIPT = Path(".claude/workflows/rc-validate.js")


def _defaults() -> dict[str, str]:
    return tomllib.loads(KIT.read_text())["defaults"]


def _script() -> str:
    return SCRIPT.read_text()


def test_kit_declares_a_tier_for_every_phase_the_script_pins() -> None:
    defaults = _defaults()
    for key in (
        "preflight",
        "readonly",
        "stateful",
        "update",
        "regression_mechanical",
        "regression_judgment",
        "triage",
        "verify",
        "synthesis",
        "curation",
    ):
        assert key in defaults, f"kit.toml [defaults] lost its {key} tier"


def test_preflight_pin_matches_kit_defaults() -> None:
    script = _script()
    match = re.search(r"schema: PREFLIGHT, model: '([a-z]+)'", script)
    assert match is not None, "preflight agent() call has no model pin"
    assert match.group(1) == _defaults()["preflight"]


def test_lane_agent_pin_matches_kit_defaults() -> None:
    """laneAgent serves readonly, stateful, and update lanes; kit.toml
    declares the same tier for all three, so one base pin covers them."""
    defaults = _defaults()
    assert defaults["readonly"] == defaults["stateful"] == defaults["update"]
    match = re.search(r"schema: LANE_RESULT, model: '([a-z]+)'", _script())
    assert match is not None, "laneAgent base opts have no model pin"
    assert match.group(1) == defaults["readonly"]


def test_regression_batch_pins_match_kit_defaults() -> None:
    defaults = _defaults()
    script = _script()
    mech = re.search(r"tier: 'mechanical', model: '([a-z]+)'", script)
    judg = re.search(r"tier: 'judgment', model: '([a-z]+)'", script)
    assert mech is not None, "mechanical regression batch has no model pin"
    assert judg is not None, "judgment regression batch has no model pin"
    assert mech.group(1) == defaults["regression_mechanical"]
    assert judg.group(1) == defaults["regression_judgment"]
    serialized = re.search(r"label: 'regressions:serialized'[^}]*model: '([a-z]+)'", script)
    assert serialized is not None, "serialized regression agent has no model pin"
    assert serialized.group(1) == defaults["regression_judgment"]


def test_synthesis_phase_pins_match_kit_defaults() -> None:
    defaults = _defaults()
    script = _script()
    for label, key in (("triage", "triage"), ("report", "synthesis"), ("curate", "curation")):
        match = re.search(rf"label: '{label}'[^}}]*model: '([a-z]+)'", script)
        assert match is not None, f"{label} agent() call has no model pin"
        assert match.group(1) == defaults[key]
    verify = re.search(r"label: `verify:\$\{c\.key\}`[^}]*model: '([a-z]+)'", script)
    assert verify is not None, "verify agent() call has no model pin"
    assert verify.group(1) == defaults["verify"]
