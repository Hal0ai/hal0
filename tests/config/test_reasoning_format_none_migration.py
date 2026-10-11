"""``--reasoning-format none`` removal (#2545).

Upstream llama.cpp answers 400 "Failed to initialize samplers" to every
``response_format`` request when the server runs with
``--reasoning-format none``. The legacy seed profiles used to pass it; this
covers the seed fix and the upgrade migration that heals boxes already seeded:
profiles.toml flags, slot ``[server].extra_args`` and registry
``defaults.extra_args`` — stripped, backed up first, idempotent, and never
touching a deliberate ``--reasoning-format deepseek``.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from hal0.config import paths
from hal0.config.migrations.reasoning_format_none import (
    migrate_model_defaults,
    migrate_profiles,
    migrate_slots,
    run_migration,
    strip_flag_string,
)
from hal0.config.schema import LEGACY_SEED_PROFILES, SEED_PROFILES
from hal0.registry.model import Model, ModelDefaults
from hal0.registry.store import ModelRegistry

# ── seeds ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("catalog", [SEED_PROFILES, LEGACY_SEED_PROFILES], ids=["seed", "legacy"])
def test_no_seed_profile_passes_reasoning_format_none(catalog) -> None:
    for name, entry in catalog.items():
        flags = entry["flags"]
        assert "--reasoning-format none" not in flags, name
        assert "--reasoning-format=none" not in flags, name


def test_legacy_profiles_keep_reasoning_off_and_thinking_keeps_deepseek() -> None:
    """The goal is "no thinking, structured output works": `--reasoning off`
    stays on the non-thinking profiles; the thinking profile's deliberate
    `--reasoning-format deepseek` is not a `none` and is untouched."""
    for name in ("dense", "moe", "chadrock-dense", "chadrock-moe", "coding"):
        assert "--reasoning off" in LEGACY_SEED_PROFILES[name]["flags"], name
    assert "--reasoning-format deepseek" in LEGACY_SEED_PROFILES["thinking"]["flags"]


# ── strip_flag_string ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ("--reasoning-format none -b 2048", "-b 2048"),  # start
        ("-b 2048 --reasoning-format none", "-b 2048"),  # end
        ("--reasoning off --reasoning-format none --jinja", "--reasoning off --jinja"),  # middle
        ("--jinja --reasoning-format=none -b 2048", "--jinja -b 2048"),  # `=` form
        ("--reasoning-format none", ""),  # only token pair
    ],
)
def test_strip_flag_string_removes_the_pair(flags: str, expected: str) -> None:
    assert strip_flag_string(flags) == (expected, 1)


@pytest.mark.parametrize(
    "flags",
    [
        "--reasoning off --reasoning-format deepseek --jinja",
        "--reasoning-format=deepseek",
        "--reasoning-format auto",
        "-b 2048",
        "",
        "--chat-template-kwargs '{broken",  # malformed quoting: left byte-identical
    ],
)
def test_strip_flag_string_leaves_other_strings_byte_identical(flags: str) -> None:
    assert strip_flag_string(flags) == (flags, 0)


# ── profiles.toml ────────────────────────────────────────────────────────────

_PROFILES = """\
[profile.mine]
flags = "--jinja --reasoning off --reasoning-format none -b 2048"
intent = "custom"

[profile.thinker]
flags = "--reasoning on --reasoning-format deepseek"

[profile.clean]
flags = "-fa auto -b 2048"

[profile.noflags]
intent = "no flags key"
"""


def test_migrate_profiles_strips_backs_up_and_is_idempotent(tmp_hal0_home: str) -> None:
    target = paths.profiles_toml()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PROFILES, encoding="utf-8")

    assert migrate_profiles() == ["mine"]

    after = tomllib.loads(target.read_text(encoding="utf-8"))["profile"]
    assert after["mine"]["flags"] == "--jinja --reasoning off -b 2048"
    assert after["mine"]["intent"] == "custom"
    assert after["thinker"]["flags"] == "--reasoning on --reasoning-format deepseek"
    assert after["clean"]["flags"] == "-fa auto -b 2048"
    assert "flags" not in after["noflags"]

    backups = sorted((paths.var_lib() / "backups").glob("profiles-*.toml"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == _PROFILES  # pre-change bytes

    # Second run: nothing to do, nothing rewritten, no second backup.
    written = target.read_text(encoding="utf-8")
    assert migrate_profiles() == []
    assert target.read_text(encoding="utf-8") == written
    assert len(list((paths.var_lib() / "backups").glob("profiles-*.toml"))) == 1


def test_migrate_profiles_absent_or_unreadable_is_a_noop(tmp_hal0_home: str) -> None:
    assert migrate_profiles() == []  # no profiles.toml
    target = paths.profiles_toml()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("this is [not toml", encoding="utf-8")
    assert migrate_profiles() == []
    assert target.read_text(encoding="utf-8") == "this is [not toml"


# ── slot TOMLs ───────────────────────────────────────────────────────────────

_SLOT_DIRTY = """\
name = "chat"

[model]
default = "m"

[server]
extra_args = "--reasoning-format none -b 2048"
"""

_SLOT_NESTED = """\
[slot]
name = "nested"

[slot.server]
extra_args = "--jinja --reasoning-format=none"
"""

_SLOT_CLEAN = """\
name = "clean"

[server]
extra_args = "--reasoning-format deepseek -b 2048"
"""


def _write_slots(**files: str) -> Path:
    slots = paths.slots_config_dir()
    slots.mkdir(parents=True, exist_ok=True)
    for stem, text in files.items():
        (slots / f"{stem}.toml").write_text(text, encoding="utf-8")
    return slots


def test_migrate_slots_strips_extra_args_in_both_shapes(tmp_hal0_home: str) -> None:
    slots = _write_slots(chat=_SLOT_DIRTY, nested=_SLOT_NESTED, clean=_SLOT_CLEAN)

    assert migrate_slots() == ["chat", "nested"]

    chat = tomllib.loads((slots / "chat.toml").read_text(encoding="utf-8"))
    assert chat["server"]["extra_args"] == "-b 2048"
    assert chat["model"]["default"] == "m"
    nested = tomllib.loads((slots / "nested.toml").read_text(encoding="utf-8"))
    assert nested["slot"]["server"]["extra_args"] == "--jinja"
    # A deliberate non-`none` format is not rewritten: file is byte-identical.
    assert (slots / "clean.toml").read_text(encoding="utf-8") == _SLOT_CLEAN

    backups = {p.name.split("-2")[0]: p for p in (paths.var_lib() / "backups").glob("slot-*.toml")}
    assert set(backups) == {"slot-chat", "slot-nested"}
    assert backups["slot-chat"].read_text(encoding="utf-8") == _SLOT_DIRTY


def test_migrate_slots_is_idempotent(tmp_hal0_home: str) -> None:
    slots = _write_slots(chat=_SLOT_DIRTY)
    assert migrate_slots() == ["chat"]
    written = (slots / "chat.toml").read_text(encoding="utf-8")
    assert migrate_slots() == []
    assert (slots / "chat.toml").read_text(encoding="utf-8") == written
    assert len(list((paths.var_lib() / "backups").glob("slot-chat-*.toml"))) == 1


def test_migrate_slots_skips_unreadable_files(tmp_hal0_home: str) -> None:
    slots = _write_slots(chat=_SLOT_DIRTY, broken="not [valid toml")
    assert migrate_slots() == ["chat"]
    assert (slots / "broken.toml").read_text(encoding="utf-8") == "not [valid toml"


def test_migrate_slots_no_dir_is_a_noop(tmp_hal0_home: str) -> None:
    assert migrate_slots() == []


# ── registry model defaults ──────────────────────────────────────────────────


def _register(model_id: str, extra_args: str | None) -> None:
    ModelRegistry().add(
        Model(
            id=model_id,
            path=f"/tmp/{model_id}.gguf",
            capabilities=["chat"],
            defaults=ModelDefaults(extra_args=extra_args) if extra_args is not None else None,
        )
    )


def test_migrate_model_defaults_strips_persists_and_is_idempotent(tmp_hal0_home: str) -> None:
    _register("middle", "--jinja --reasoning-format none -fa auto")
    _register("equals", "--reasoning-format=none")
    _register("deepseek", "--reasoning-format deepseek --jinja")
    _register("clean", "-fa auto")
    _register("no-defaults", None)

    assert sorted(migrate_model_defaults()) == ["equals", "middle"]

    reg = ModelRegistry()
    assert reg.get("middle").defaults.extra_args == "--jinja -fa auto"
    equals = reg.get("equals").defaults  # emptied -> registry collapses to no tune
    assert equals is None or not equals.extra_args
    assert reg.get("deepseek").defaults.extra_args == "--reasoning-format deepseek --jinja"
    assert reg.get("clean").defaults.extra_args == "-fa auto"
    assert reg.get("no-defaults").defaults is None

    backups = list((paths.var_lib() / "backups").glob("model-extra-args-*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {
        "equals": "--reasoning-format=none",
        "middle": "--jinja --reasoning-format none -fa auto",
    }

    assert migrate_model_defaults() == []
    assert len(list((paths.var_lib() / "backups").glob("model-extra-args-*.json"))) == 1


def test_migrate_model_defaults_empty_registry_is_noop(tmp_hal0_home: str) -> None:
    assert migrate_model_defaults() == []


# ── run_migration (what the update / install / boot path calls) ──────────────


def test_run_migration_heals_all_three_and_converges(tmp_hal0_home: str) -> None:
    target = paths.profiles_toml()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PROFILES, encoding="utf-8")
    _write_slots(chat=_SLOT_DIRTY)
    _register("m", "--reasoning-format none")

    assert run_migration() == {"profiles": ["mine"], "slots": ["chat"], "models": ["m"]}
    assert run_migration() == {"profiles": [], "slots": [], "models": []}


def test_run_migration_one_failing_sweep_does_not_block_the_others(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = paths.profiles_toml()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PROFILES, encoding="utf-8")
    _register("m", "--reasoning-format none")

    def _boom() -> list[str]:
        raise RuntimeError("slots dir exploded")

    monkeypatch.setattr("hal0.config.migrations.reasoning_format_none.migrate_slots", _boom)
    assert run_migration() == {"profiles": ["mine"], "slots": [], "models": ["m"]}


# ── slot lock spans the whole read-modify-write ──────────────────────────────


def test_migrate_slots_reads_inside_the_lock_so_a_concurrent_edit_is_not_lost(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that gets in before the migration holds the lock first. The
    migration must read AFTER acquiring it, or it would write its stale
    snapshot over that writer's change."""
    import contextlib

    import hal0.slot_config as slot_config

    slots = _write_slots(chat=_SLOT_DIRTY)
    events: list[str] = []

    @contextlib.contextmanager
    def _racing_lock(slots_dir):
        events.append("enter")
        # Another writer's edit lands just before we acquire the lock.
        (slots / "chat.toml").write_text(
            _SLOT_DIRTY + "\n[labels]\nowner = 'operator'\n", encoding="utf-8"
        )
        try:
            yield
        finally:
            events.append("exit")

    real_write = slot_config.write_slot_toml

    def _recording_write(path, data):
        events.append("write")
        real_write(path, data)

    monkeypatch.setattr(slot_config, "slot_write_lock", _racing_lock)
    monkeypatch.setattr(slot_config, "write_slot_toml", _recording_write)

    assert migrate_slots() == ["chat"]
    assert events == ["enter", "write", "exit"]
    after = tomllib.loads((slots / "chat.toml").read_text(encoding="utf-8"))
    assert after["server"]["extra_args"] == "-b 2048"
    assert after["labels"] == {"owner": "operator"}  # the concurrent edit survived


# ── backups are owner-only ───────────────────────────────────────────────────


def _mode(p: Path) -> int:
    return p.stat().st_mode & 0o777


def test_backups_are_owner_only_and_dir_is_0700_when_created(tmp_hal0_home: str) -> None:
    target = paths.profiles_toml()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PROFILES, encoding="utf-8")
    target.chmod(0o644)  # a world-readable source must not leak into the backup
    _write_slots(chat=_SLOT_DIRTY)
    _register("m", "--reasoning-format none")

    backups = paths.var_lib() / "backups"
    assert not backups.exists()
    run_migration()

    assert _mode(backups) == 0o700
    files = sorted(backups.iterdir())
    assert {f.name.split("-")[0] for f in files} == {"profiles", "slot", "model"}
    for f in files:
        assert _mode(f) == 0o600, f.name


def test_existing_backups_dir_is_left_as_the_operator_set_it(tmp_hal0_home: str) -> None:
    backups = paths.var_lib() / "backups"
    backups.mkdir(parents=True)
    backups.chmod(0o755)
    _write_slots(chat=_SLOT_DIRTY)

    assert migrate_slots() == ["chat"]
    assert _mode(backups) == 0o755
    (backup,) = backups.glob("slot-chat-*.toml")
    assert _mode(backup) == 0o600


# ── write-side screens reject; launch strips (backstop) ──────────────────────


def test_screen_profile_flags_rejects_a_new_reasoning_format_none() -> None:
    from hal0.errors import BadRequest
    from hal0.profiles import screen_profile_flags

    for flags in ("--jinja --reasoning-format none", "--reasoning-format=none"):
        with pytest.raises(BadRequest) as ei:
            screen_profile_flags(flags)
        assert ei.value.code == "slot.reasoning_format_none_denied"
        assert "response_format" in str(ei.value)
    # Other values, and `--reasoning off`, are fine.
    screen_profile_flags("--reasoning off --reasoning-format deepseek")


def test_screen_profile_flags_grandfathers_an_inherited_pair() -> None:
    """The drawer re-sends stored flags verbatim; a row that already carries
    the pair stays savable (launch strips it), but adding it anew is rejected."""
    from hal0.errors import BadRequest
    from hal0.profiles import screen_profile_flags

    stored = "--jinja --reasoning-format none"
    screen_profile_flags(stored, grandfathered=stored)
    with pytest.raises(BadRequest):
        screen_profile_flags(stored, grandfathered="--jinja")


def test_screen_model_write_rejects_a_new_reasoning_format_none() -> None:
    from hal0.errors import BadRequest
    from hal0.services.models_service import screen_model_write

    with pytest.raises(BadRequest) as ei:
        screen_model_write({"defaults": {"extra_args": "--jinja --reasoning-format none"}})
    assert ei.value.code == "slot.reasoning_format_none_denied"
    screen_model_write({"defaults": {"extra_args": "--jinja --reasoning-format deepseek"}})


def test_screen_model_write_grandfathers_an_inherited_pair() -> None:
    from hal0.errors import BadRequest
    from hal0.services.models_service import screen_model_write

    body = {"defaults": {"extra_args": "--reasoning-format none -b 2048"}}
    existing = {"defaults": {"extra_args": "--reasoning-format none"}}
    screen_model_write(body, existing=existing)
    with pytest.raises(BadRequest):
        screen_model_write(body, existing={"defaults": {"extra_args": "-b 2048"}})
