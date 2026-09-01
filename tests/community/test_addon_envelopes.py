"""The committed addon catalog under ``community/addons/`` is a GENERATED
artifact — ``scripts/export_addons.py`` is its only author.

These tests are the staleness alarm: they re-derive every envelope from
:data:`hal0.config.schema.LEGACY_SEED_PROFILES` and compare it against what is
on disk, so a change to a legacy definition that nobody regenerated fails here
instead of shipping a catalog that disagrees with the code.
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.config.schema import LEGACY_SEED_PROFILES, ProfileConfig
from hal0.profiles import ProfileCatalog
from hal0.profiles.portable import export_envelope, import_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
ADDONS_DIR = REPO_ROOT / "community" / "addons"


def _envelope_files() -> list[Path]:
    return sorted(ADDONS_DIR.glob("*.hal0profile.json"))


def _staleness_view(envelope: dict) -> dict:
    """The part of an envelope that is derived from the profile definition.

    ``hal0_version`` is stamped by :func:`export_envelope` from the running
    ``hal0.__version__`` and is deliberately excluded: it records which release
    produced the file, not what the file says. Comparing it would fail this
    test on every version bump — and in any checkout where hal0 is not
    pip-installed, where ``__version__`` falls back to ``0.0.0+source`` — with
    no envelope actually being stale.
    """
    return {k: v for k, v in envelope.items() if k != "hal0_version"}


def test_committed_envelopes_match_legacy_definitions() -> None:
    """Every legacy seed has an envelope, and it matches its definition."""
    for name, entry in LEGACY_SEED_PROFILES.items():
        path = ADDONS_DIR / f"{name}.hal0profile.json"
        assert path.exists(), f"missing envelope for {name} — run scripts/export_addons.py"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        fresh = export_envelope(
            name,
            ProfileConfig.model_validate(entry),
            exported_at=on_disk["exported_at"],
        )
        assert _staleness_view(on_disk) == _staleness_view(fresh), (
            f"stale envelope for {name} — rerun scripts/export_addons.py"
        )
        assert isinstance(on_disk["hal0_version"], str) and on_disk["hal0_version"]


def test_no_orphan_envelopes() -> None:
    """No envelope file survives the pruning of its legacy definition."""
    on_disk = {p.name.removesuffix(".hal0profile.json") for p in _envelope_files()}
    assert on_disk == set(LEGACY_SEED_PROFILES), (
        "community/addons/ disagrees with LEGACY_SEED_PROFILES — "
        "rerun scripts/export_addons.py and delete any orphan file"
    )


def test_every_envelope_imports_clean(tmp_path: Path) -> None:
    """Each envelope survives a real import: flag screen, checksum, create."""
    files = _envelope_files()
    assert files, "no addon envelopes committed"
    for f in files:
        env = json.loads(f.read_text(encoding="utf-8"))
        catalog = ProfileCatalog(path=tmp_path / f"{env['name']}.toml")
        imported = import_profile(env, env["name"], catalog)
        assert imported.name == env["name"]
