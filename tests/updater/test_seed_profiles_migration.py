"""ensure_seed_profiles — the virtual-seed prune/rescue migration.

Seeds are virtual (overlaid from code); this migration rewrites an on-disk
profiles.toml that materialised them. The data-safety contract under test:

  - byte-identical stale seed copies are pruned,
  - DIVERGENT seed-named entries (hand-edited seed, or an operator profile
    whose name only later became a seed — e.g. ``embed``) are RESCUED to
    ``<name>-custom``, never silently deleted,
  - the pre-prune file is backed up once,
  - operator (non-seed) profiles pass through untouched.
"""

from __future__ import annotations

import tomllib

from hal0.config.paths import profiles_toml
from hal0.config.schema import SEED_PROFILES
from hal0.updater.updater import ensure_seed_profiles


def _write_profiles(text: str) -> None:
    target = profiles_toml()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _seed_table(name: str) -> str:
    """Render a seed profile as TOML, byte-identical to SEED_PROFILES."""
    seed = SEED_PROFILES[name]
    lines = [f"[profile.{name}]"]
    for key, val in seed.items():
        if isinstance(val, bool):
            lines.append(f"{key} = {'true' if val else 'false'}")
        elif isinstance(val, str):
            lines.append(f'{key} = "{val}"')
    return "\n".join(lines) + "\n"


def test_absent_file_is_noop(tmp_hal0_home: str) -> None:
    assert ensure_seed_profiles() == 0
    assert not profiles_toml().exists()


def test_identical_materialised_seed_is_pruned(tmp_hal0_home: str) -> None:
    _write_profiles(_seed_table("chat"))
    assert ensure_seed_profiles() == 1
    on_disk = tomllib.loads(profiles_toml().read_text(encoding="utf-8"))
    assert on_disk.get("profile", {}) == {}


def test_divergent_seed_named_entry_is_rescued_not_deleted(tmp_hal0_home: str) -> None:
    """An operator profile that collides with a (possibly newer) seed name is
    renamed to <name>-custom — the content survives the migration."""
    _write_profiles(
        "[profile.embedding]\n"
        'flags = "--embedding -ub 4096"\n'
        "mtp = false\n"
        'device_class = "gpu"\n'
        'backend = "vulkan"\n'
    )
    assert ensure_seed_profiles() == 1
    on_disk = tomllib.loads(profiles_toml().read_text(encoding="utf-8"))
    profiles = on_disk.get("profile", {})
    assert "embedding" not in profiles  # seed name freed for the code overlay
    assert profiles["embedding-custom"]["flags"] == "--embedding -ub 4096"


def test_prune_writes_backup_once(tmp_hal0_home: str) -> None:
    _write_profiles(_seed_table("chat"))
    ensure_seed_profiles()
    backup = profiles_toml().with_name(profiles_toml().name + ".pre-virtual-seeds.bak")
    assert backup.exists()
    original = backup.read_text(encoding="utf-8")
    # A second migration run must not clobber the original backup.
    _write_profiles(_seed_table("dense"))
    ensure_seed_profiles()
    assert backup.read_text(encoding="utf-8") == original


def test_operator_profiles_pass_through(tmp_hal0_home: str) -> None:
    _write_profiles(_seed_table("chat") + '\n[profile.my-own]\nflags = "-fa on"\n')
    assert ensure_seed_profiles() == 1
    on_disk = tomllib.loads(profiles_toml().read_text(encoding="utf-8"))
    profiles = on_disk.get("profile", {})
    assert "chat" not in profiles
    assert profiles["my-own"]["flags"] == "-fa on"
