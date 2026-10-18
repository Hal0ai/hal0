"""The committed addon catalog under ``community/addons/`` is a GENERATED
artifact — ``scripts/export_addons.py`` is its only author.

These tests are the staleness alarm: they re-derive every envelope from
:data:`scripts/export_addons.ADDON_PROFILES` and compare it against what is
on disk, so a change to a legacy definition that nobody regenerated fails here
instead of shipping a catalog that disagrees with the code.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from hal0.config.schema import SEED_PROFILES, ProfileConfig
from hal0.profiles import ProfileCatalog, _runtime_family
from hal0.profiles.portable import export_envelope, import_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
ADDONS_DIR = REPO_ROOT / "community" / "addons"
INDEX = ADDONS_DIR / "index.json"


def _addon_profiles() -> dict[str, dict]:
    return _generator().ADDON_PROFILES


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


def test_committed_envelopes_match_definitions() -> None:
    """Every published addon has an envelope, and it matches its definition."""
    for name, entry in _addon_profiles().items():
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
    """No envelope file survives the removal of its definition."""
    on_disk = {p.name.removesuffix(".hal0profile.json") for p in _envelope_files()}
    assert on_disk == set(_addon_profiles()), (
        "community/addons/ disagrees with ADDON_PROFILES — "
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


# ── index.json — the hal0.dev catalog contract ───────────────────────────────


def _index() -> dict:
    assert INDEX.exists(), "missing community/addons/index.json — run scripts/export_addons.py"
    return json.loads(INDEX.read_text(encoding="utf-8"))


def test_index_envelope_shape() -> None:
    index = _index()
    assert index["kind"] == "hal0.addon-index"
    assert index["schema_version"] == 1
    assert isinstance(index["addons"], list)


def test_index_covers_exactly_the_committed_envelopes() -> None:
    rows = _index()["addons"]
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows), "rows must sort by name"
    assert {r["file"] for r in rows} == {p.name for p in _envelope_files()}
    for row in rows:
        assert (ADDONS_DIR / row["file"]).exists()


def test_index_rows_agree_with_their_envelopes() -> None:
    for row in _index()["addons"]:
        envelope = json.loads((ADDONS_DIR / row["file"]).read_text(encoding="utf-8"))
        assert row["name"] == envelope["name"]
        assert row["checksum"] == envelope["checksum"]
        assert row["description"] == envelope["profile"]["intent"]
        assert row["runner"] == envelope["profile"].get("runner")
        # `title` is the intent's headline: everything before the first
        # separator (` · `) or parenthetical.
        assert row["description"].startswith(row["title"])
        assert row["title"] == row["title"].strip()


def test_index_runtime_family_matches_classification() -> None:
    for row in _index()["addons"]:
        profile = ProfileConfig.model_validate(_addon_profiles()[row["name"]])
        assert row["runtime_family"] == _runtime_family(row["name"], profile)


def test_index_declares_the_schema_2_release() -> None:
    """`min_hal0_version` is the first release that reads schema-2 envelopes."""
    for row in _index()["addons"]:
        assert row["min_hal0_version"] == "1.5.0"


def test_envelopes_are_schema_2_in_the_profile_centric_shape() -> None:
    """Schema 2 bodies: no retired keys, no model-fact flags, a screened tune."""
    retired = {"device_class", "backend", "quant"}
    for path in _envelope_files():
        env = json.loads(path.read_text(encoding="utf-8"))
        assert env["schema_version"] == 2
        body = env["profile"]
        assert not retired & set(body)
        assert body.get("speculation", "auto") in {"auto", "off", "ngram"}
        assert "mtp" not in body
        for model_fact in ("--jinja", "--no-mmproj", "--mmproj", "--chat-template"):
            assert model_fact not in body["flags"].split()


def test_no_envelope_shadows_a_seed_profile() -> None:
    """An addon named like a code-shipped seed could never be imported (409)."""
    assert not set(_addon_profiles()) & set(SEED_PROFILES)


# ── scripts/export_addons.py --check ─────────────────────────────────────────


def _generator() -> ModuleType:
    """Import ``scripts/export_addons.py`` (not a package) by path."""
    path = REPO_ROOT / "scripts" / "export_addons.py"
    spec = importlib.util.spec_from_file_location("export_addons", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_passes_on_the_committed_catalog() -> None:
    assert _generator().main(["--check"]) == 0


def test_check_is_blind_to_the_hal0_version_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--check`` must agree with the pytest staleness test about what stale MEANS.

    ``export_envelope`` stamps ``hal0.__version__``, which resolves from
    INSTALLED package metadata — so it differs between a release checkout, a
    version bump, and a tree where hal0 is not pip-installed (``0.0.0+source``).
    A raw-text comparison flags all eight envelopes stale in any of those, with
    a message pointing at the wrong problem. The stamp is provenance, not
    content; everything the checksum covers is still compared strictly.
    """
    monkeypatch.setattr("hal0.profiles.portable.__version__", "9.9.9-drift")
    assert _generator().main(["--check"]) == 0


def test_check_still_catches_real_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """The blindness is scoped to ``hal0_version`` — nothing else."""
    module = _generator()
    monkeypatch.setattr(module, "EXPORTED_AT", "1999-01-01T00:00:00Z")
    assert module.main(["--check"]) == 1
