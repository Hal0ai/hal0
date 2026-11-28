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


def test_every_labelled_agent_call_is_model_pinned_or_whitelisted() -> None:
    """Completeness: a NEWLY added agent() call without a model pin should fail
    here, not silently inherit the invoking session's tier. The regression
    batches build their opts dynamically and are asserted separately above;
    file-issues is the one documented inherit-the-session-tier exception."""
    inherit_ok = {"file-issues"}
    dynamic = {"regressions:"}  # label built via `regressions:${b.tier}...`, opts via Object.assign
    opts_literals = re.findall(r"\{ label: (?:[^{}]|\$\{[^}]*\})*\}", _script())
    assert len(opts_literals) >= 8, "opts-literal extraction broke — update this test"
    for literal in opts_literals:
        label = re.search(r"label: [`']([^`']+)[`']", literal)
        assert label is not None, f"unparseable opts literal: {literal}"
        name = label.group(1)
        if any(name.startswith(d) for d in dynamic) or name.split(":")[0] in inherit_ok:
            continue
        assert "model:" in literal, f"agent() call '{name}' has no model pin and no whitelist entry"


# ── box registry ─────────────────────────────────────────────────────────────
# The script carries a box id -> role map because a workflow script cannot read
# the fleet file. The map drifted from the fleet once already (#4084): one box
# id said "cpu" while the box served the Vulkan lane, and a destroyed box stayed
# registered for six releases.

BOXES = Path("tests/release-validation/boxes.example.toml")


def _example_boxes() -> dict[str, dict]:
    return tomllib.loads(BOXES.read_text())["boxes"]


def _script_box_roles() -> dict[str, str]:
    block = re.search(r"const BOX_ROLES = \{(.*?)\n\}", _script(), re.S)
    assert block, "rc-validate.js lost its BOX_ROLES table"
    return dict(re.findall(r"'([^']+)':\s*'([^']+)'", block.group(1)))


def test_script_box_roles_mirror_the_example_fleet_file() -> None:
    expected = {name: box["role"] for name, box in _example_boxes().items()}
    assert _script_box_roles() == expected, (
        "BOX_ROLES in rc-validate.js and [boxes.*] in boxes.example.toml list different "
        "boxes or roles; change both in the same pull request"
    )


def test_default_box_is_a_registered_fresh_box() -> None:
    default = re.search(r"const BOX_IDS = [^\n]*: \['([^']+)'\]", _script())
    assert default, "rc-validate.js lost its default BOX_IDS entry"
    assert _script_box_roles().get(default.group(1)) == "fresh", (
        f"the default box {default.group(1)!r} is not registered with role 'fresh'"
    )


def test_every_role_the_script_sweeps_has_a_box() -> None:
    swept = set(re.findall(r"BOX_ROLES\[b\] === '([^']+)'", _script()))
    assert swept, "rc-validate.js no longer picks its sweep boxes by role"
    missing = swept - set(_script_box_roles().values())
    assert not missing, f"rc-validate.js sweeps role(s) no box holds: {sorted(missing)}"


def test_no_box_id_says_cpu_while_the_box_has_a_gpu() -> None:
    wrong = [
        name
        for name, box in _example_boxes().items()
        if "cpu" in name.split("-") and box.get("gpu")
    ]
    assert not wrong, (
        f"box id(s) {wrong} say 'cpu' but the box has `gpu = true`; a report reader takes the "
        "id as proof that a CPU-only install was tested (#4084)"
    )


def test_fleet_file_carries_the_coverage_note_the_script_points_at() -> None:
    assert "fleet coverage note" in _script(), (
        "rc-validate.js no longer points the report agent at the fleet coverage note; "
        "drop this test with it"
    )
    assert "FLEET COVERAGE" in BOXES.read_text(), (
        "boxes.example.toml has no FLEET COVERAGE note, but rc-validate.js tells the report "
        "agent to read it"
    )


def test_an_unknown_box_id_stops_the_run_before_any_agent_starts() -> None:
    script = _script()
    check = script.find("unknown box id(s)")
    first_phase = script.find("phase('Preflight')")
    assert check != -1, "rc-validate.js no longer rejects a box id missing from BOX_ROLES"
    assert first_phase != -1, "rc-validate.js lost its Preflight phase marker"
    assert check < first_phase, (
        "the unknown-box check runs after Preflight has already spawned one agent per box; "
        "a renamed id must stop the run before that (#4084)"
    )


def test_preflight_blocks_on_a_fleet_file_without_the_box_table() -> None:
    script = _script()
    assert "has no\n[boxes.${boxId}] table, set ready=false" in script, (
        "the preflight prompt no longer tells the agent to block when the operator's "
        "boxes.toml has no table for its box; without it the agent guesses from a "
        "similarly named table"
    )
