"""v6 converts the decision provider's old ``local`` default once (#3757).

The unit-file fact is injected: tests/conftest.py points
``v6.DECISION_UNIT_PATH`` at a path that does not exist, so no test reads the
host's /etc; a test that needs the unit creates that file.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from hal0.config.migrations import MIGRATIONS, run_migrations, v6
from hal0.config.schema import Hal0Config
from hal0.system import seam


@pytest.fixture
def unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "hal0-hs-decide.service"
    monkeypatch.setattr(v6, "DECISION_UNIT_PATH", path)
    return path


def _box(**decision: object) -> dict:
    data: dict = {"meta": {"schema_version": 5}, "memory": {"bank": "x"}}
    if decision:
        data["memory"]["decision"] = dict(decision)
    return data


def _provider(out: dict) -> object:
    return out.get("memory", {}).get("decision", {}).get("provider")


def test_registered_and_pinned_to_the_seam_unit_path() -> None:
    assert 6 in MIGRATIONS
    # conftest patches the module attribute; check the literal the module ships.
    source = Path(v6.__file__).read_text()
    assert f'DECISION_UNIT_PATH = Path("{seam.DECISION_UNIT_PATH}")' in source


def test_tests_never_see_the_hosts_unit_by_default() -> None:
    assert v6.DECISION_UNIT_PATH != seam.DECISION_UNIT_PATH
    assert not v6.DECISION_UNIT_PATH.exists()


@pytest.mark.parametrize(
    ("decision", "has_unit", "want"),
    [
        # stored or defaulted local, no unit -> off
        ({"provider": "local"}, False, "off"),
        ({}, False, None),  # absent already reads as the new default "off"
        ({"provider": "local", "enabled": True}, False, "off"),
        # stored or defaulted local, unit present -> explicit local
        ({"provider": "local"}, True, "local"),
        ({}, True, "local"),
        # enabled = false stays exactly as it was
        ({"provider": "local", "enabled": False}, False, "local"),
        ({"provider": "local", "enabled": False}, True, "local"),
        ({"enabled": False}, True, None),
        # other choices are left alone
        ({"provider": "hindsight-cloud"}, False, "hindsight-cloud"),
        ({"provider": "off"}, True, "off"),
        ({"provider": "local", "base_url": "http://127.0.0.1:9300"}, False, "local"),
    ],
)
def test_truth_table(unit: Path, decision: dict, has_unit: bool, want: object) -> None:
    if has_unit:
        unit.write_text("[Service]\n")
    data = _box(**decision)
    original = deepcopy(data)
    out, version = run_migrations(data, target_version=6)
    assert version == 6 and out["meta"]["schema_version"] == 6
    assert _provider(out) == want
    assert data == original
    # enabled is never written or switched on
    assert out.get("memory", {}).get("decision", {}).get("enabled") == decision.get("enabled")
    # no other default is frozen
    assert out["memory"]["bank"] == "x"
    assert set(out["memory"].get("decision", {})) <= {*decision, "provider"}
    Hal0Config.model_validate(out)


def test_a_box_already_at_v6_is_not_converted_again(unit: Path) -> None:
    data = {"meta": {"schema_version": 6}, "memory": {"decision": {"provider": "local"}}}
    out, version = run_migrations(data, target_version=6)
    assert version == 6 and _provider(out) == "local"


def test_the_old_marker_path_is_never_touched(
    tmp_hal0_home: str, unit: Path, tmp_path: Path
) -> None:
    """B1 regression: nothing writes where the removed conversion marker lived."""
    from hal0.config.paths import var_lib

    victim = tmp_path / "victim"
    victim.write_text("untouched\n")
    marker = var_lib() / "state" / "memory-decision-provider.converted"
    marker.parent.mkdir(parents=True)
    marker.symlink_to(victim)
    run_migrations(_box(provider="local"), target_version=6)
    assert victim.read_text() == "untouched\n"
    assert marker.is_symlink()


def test_no_code_writes_the_old_marker() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "hal0"
    hits = [p for p in src.rglob("*.py") if "memory-decision-provider.converted" in p.read_text()]
    assert hits == []
