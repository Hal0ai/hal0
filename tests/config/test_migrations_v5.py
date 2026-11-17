"""v5 converts the stored engine lane limits once (#3758, #3759, #3762).

``meta.schema_version`` is the record that the conversion ran: a box at v5 is
never converted again, so a later explicit ``llm_max_concurrent = 1`` survives.
"""

from copy import deepcopy
from pathlib import Path

import pytest

from hal0.config.migrations import MIGRATIONS, run_migrations, v5
from hal0.config.schema import Hal0Config
from hal0.memory import extraction_env


@pytest.fixture
def dropins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(v5, "DROP_IN_DIR", tmp_path)
    return tmp_path


def _lanes(**lanes: object) -> dict:
    return {"meta": {"schema_version": 4}, "memory": {"lanes": lanes}}


def test_registered_and_pinned_to_the_engine_drop_in_directory() -> None:
    assert 5 in MIGRATIONS
    # conftest points DROP_IN_DIR at an empty temp dir for every test; the
    # shipped value is the literal in the source.
    assert f'Path("{extraction_env.DROP_IN_DIR}")' in Path(v5.__file__).read_text()


def test_stored_default_of_one_is_removed(dropins: Path) -> None:
    data = _lanes(llm_max_concurrent=1, timeout_s=900)
    original = deepcopy(data)
    out, version = run_migrations(data, target_version=5)
    assert version == 5
    assert out["meta"]["schema_version"] == 5
    assert "llm_max_concurrent" not in out["memory"]["lanes"]
    assert out["memory"]["lanes"]["timeout_s"] == 900
    assert data == original
    assert Hal0Config.model_validate(out).memory.lanes.llm_max_concurrent is None


def test_any_other_stored_value_is_an_operator_choice_and_stays(dropins: Path) -> None:
    out, _ = run_migrations(_lanes(llm_max_concurrent=6), target_version=5)
    assert out["memory"]["lanes"]["llm_max_concurrent"] == 6


def test_a_box_already_at_v5_is_not_converted_again(dropins: Path) -> None:
    data = {"meta": {"schema_version": 5}, "memory": {"lanes": {"llm_max_concurrent": 1}}}
    out, version = run_migrations(data, target_version=5)
    assert version == 5
    assert out["memory"]["lanes"]["llm_max_concurrent"] == 1


def test_a_later_explicit_one_survives_a_second_run(dropins: Path) -> None:
    out, _ = run_migrations(_lanes(llm_max_concurrent=1), target_version=5)
    assert "llm_max_concurrent" not in out["memory"]["lanes"]
    out["memory"]["lanes"]["llm_max_concurrent"] = 1  # the operator sets it afterwards
    again, version = run_migrations(out, target_version=5)
    assert version == 5
    assert again["memory"]["lanes"]["llm_max_concurrent"] == 1


def test_no_memory_table_is_left_alone(dropins: Path) -> None:
    out, _ = run_migrations({"meta": {"schema_version": 4}}, target_version=5)
    assert "memory" not in out


# ── an operator's hand-made drop-in ──


def test_operator_drop_in_values_are_adopted(dropins: Path) -> None:
    (dropins / "10-wall.conf").write_text(
        "[Service]\nEnvironment=HINDSIGHT_API_REFLECT_WALL_TIMEOUT=7200\n"
        'Environment="HINDSIGHT_API_CONSOLIDATION_LLM_BATCH_SIZE=2"\n'
    )
    out, _ = run_migrations({"meta": {"schema_version": 4}}, target_version=5)
    assert out["memory"]["lanes"]["reflect"]["wall_timeout_s"] == 7200
    assert out["memory"]["lanes"]["consolidation"]["batch_size"] == 2
    assert (dropins / "10-wall.conf").exists()  # never deleted
    assert Hal0Config.model_validate(out).memory.lanes.reflect.wall_timeout_s == 7200


def test_an_explicit_hal0_toml_value_wins_over_the_drop_in(dropins: Path) -> None:
    (dropins / "10-wall.conf").write_text("Environment=HINDSIGHT_API_REFLECT_WALL_TIMEOUT=7200\n")
    data = _lanes(reflect={"wall_timeout_s": 900})
    out, _ = run_migrations(data, target_version=5)
    assert out["memory"]["lanes"]["reflect"]["wall_timeout_s"] == 900


def test_managed_files_are_not_adopted_from(dropins: Path) -> None:
    (dropins / "showcase-lanes.conf").write_text(
        "Environment=HINDSIGHT_API_REFLECT_WALL_TIMEOUT=1800\n"
    )
    (dropins / "extraction-model.conf").write_text(
        "Environment=HINDSIGHT_API_CONSOLIDATION_LLM_BATCH_SIZE=4\n"
    )
    out, _ = run_migrations({"meta": {"schema_version": 4}}, target_version=5)
    assert "memory" not in out


def test_the_last_drop_in_in_sort_order_wins(dropins: Path) -> None:
    (dropins / "10-a.conf").write_text("Environment=HINDSIGHT_API_REFLECT_WALL_TIMEOUT=3600\n")
    (dropins / "20-b.conf").write_text("Environment=HINDSIGHT_API_REFLECT_WALL_TIMEOUT=7200\n")
    out, _ = run_migrations({"meta": {"schema_version": 4}}, target_version=5)
    assert out["memory"]["lanes"]["reflect"]["wall_timeout_s"] == 7200


@pytest.mark.parametrize("value", ["30", "999999", "abc", "0", "\u00b2", "\u0663\u0660"])
def test_a_value_outside_the_schema_bounds_is_not_adopted(dropins: Path, value: str) -> None:
    (dropins / "10-wall.conf").write_text(
        f"Environment=HINDSIGHT_API_REFLECT_WALL_TIMEOUT={value}\n"
    )
    out, _ = run_migrations({"meta": {"schema_version": 4}}, target_version=5)
    assert "memory" not in out


def test_an_absent_or_unreadable_directory_fails_soft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(v5, "DROP_IN_DIR", tmp_path / "missing")
    out, version = run_migrations(_lanes(llm_max_concurrent=1), target_version=5)
    assert version == 5
    assert "llm_max_concurrent" not in out["memory"]["lanes"]

    unreadable = tmp_path / "bad.conf"
    unreadable.write_bytes(b"\xff\xfe\x00 not utf-8")
    monkeypatch.setattr(v5, "DROP_IN_DIR", tmp_path)
    assert v5.operator_dropin_values(tmp_path).values == {}


# ── forms of the Environment= line, and warnings for the ones not adopted ──

WALL = "HINDSIGHT_API_REFLECT_WALL_TIMEOUT"
BATCH = "HINDSIGHT_API_CONSOLIDATION_LLM_BATCH_SIZE"


def _run(dropins: Path, text: str) -> tuple[dict, list[dict]]:
    from structlog.testing import capture_logs

    (dropins / "10-op.conf").write_text("[Service]\n" + text)
    with capture_logs() as logs:
        out, _ = run_migrations({"meta": {"schema_version": 4}}, target_version=5)
    return out, logs


@pytest.mark.parametrize(
    "line",
    [
        f"Environment={WALL}=7200",
        f'Environment="{WALL}=7200"',
        f'Environment={WALL}="7200"',
        f"Environment={BATCH}=2 {WALL}=7200",
        f'Environment="{BATCH}=2" "{WALL}=7200"',
        f"Environment=A=1 {WALL}=7200 {BATCH}=2",
    ],
)
def test_every_supported_form_is_adopted(dropins: Path, line: str) -> None:
    out, logs = _run(dropins, line + "\n")
    assert out["memory"]["lanes"]["reflect"]["wall_timeout_s"] == 7200
    assert not [e for e in logs if e["event"] == "updater.lane_limits_adopt_skipped"]


def test_an_empty_environment_line_resets_earlier_assignments(dropins: Path) -> None:
    out, _ = _run(dropins, f"Environment={WALL}=7200\nEnvironment=\n")
    assert "memory" not in out
    out, _ = _run(dropins, f"Environment={WALL}=7200\nEnvironment=\nEnvironment={WALL}=900\n")
    assert out["memory"]["lanes"]["reflect"]["wall_timeout_s"] == 900


def test_a_reset_in_a_later_file_clears_an_earlier_one(dropins: Path) -> None:
    (dropins / "10-a.conf").write_text(f"Environment={WALL}=7200\n")
    (dropins / "20-b.conf").write_text("Environment=\n")
    out, _ = run_migrations({"meta": {"schema_version": 4}}, target_version=5)
    assert "memory" not in out


def test_comments_and_other_sections_are_ignored(dropins: Path) -> None:
    out, _ = _run(
        dropins, f"# Environment={WALL}=1\n; Environment={WALL}=2\nEnvironment={WALL}=7200\n"
    )
    assert out["memory"]["lanes"]["reflect"]["wall_timeout_s"] == 7200


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (f"Environment={WALL}=30", "out of bounds"),
        (f"Environment={WALL}=abc", "unparseable"),
        (f"Environment={WALL}=\u00b2", "non-ascii digit"),
        (f'Environment="{WALL}=7200', "unbalanced quote"),
    ],
)
def test_a_value_not_adopted_is_warned_with_the_file_name(
    dropins: Path, line: str, why: str
) -> None:
    out, logs = _run(dropins, line + "\n")
    assert "memory" not in out, why
    warned = [e for e in logs if e["event"] == "updater.lane_limits_adopt_skipped"]
    assert warned, why
    assert warned[0]["drop_in"] == "10-op.conf"
    assert warned[0]["log_level"] == "warning"
    assert "managed value now applies" in warned[0]["note"]


def test_the_warning_event_is_reported_in_the_update_summary() -> None:
    from hal0.cli.update_commands import _MIGRATION_PASS_LABELS
    from hal0.updater.updater import _NON_FATAL_MIGRATION_FAILURE_EVENTS

    for event in ("updater.lane_limits_converted", "updater.lane_limits_adopt_skipped"):
        assert event in _NON_FATAL_MIGRATION_FAILURE_EVENTS
        assert event in _MIGRATION_PASS_LABELS


def test_an_odd_drop_in_can_never_fail_the_migration(dropins: Path) -> None:
    (dropins / "10-odd.conf").write_text(
        f"Environment={WALL}=\u00b2\nEnvironment={BATCH}=\u0663\n", encoding="utf-8"
    )
    (dropins / "20-bin.conf").write_bytes(b"\xff\xfe")
    out, version = run_migrations(_lanes(llm_max_concurrent=1), target_version=5)
    assert version == 5
    assert "llm_max_concurrent" not in out["memory"]["lanes"]
