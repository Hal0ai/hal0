"""Tests for scripts/set-version.py — atomic version synchronization.

Hermetic: uses temporary copies of pyproject.toml, uv.lock, ui/package.json,
ui/package-lock.json, and manifest.json.  Loads the script via
importlib.util.spec_from_file_location() because the module name contains
a hyphen.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _cd_to_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Change cwd to tmp_path so `uv lock` runs in a clean directory."""
    monkeypatch.chdir(tmp_path)


def _load_set_version() -> object:
    """Load scripts/set_version.py as a module (hyphen → underscore)."""
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent.parent
    spec = importlib.util.spec_from_file_location(
        "set_version_script", repo_root / "scripts" / "set-version.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["set_version_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _populate_project(tmp_path: Path, version: str) -> dict[str, Path]:
    """Copy real project files (with *version* patched) into *tmp_path*.

    Returns a mapping of logical key → tmp_path file path.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent

    paths: dict[str, Path] = {}

    # pyproject.toml — minimal enough to regenerate
    pyproject = textwrap.dedent(f"""\
    [build-system]
    requires = ["hatchling>=1.21"]
    build-backend = "hatchling.build"

    [project]
    name = "hal0ai"
    version = "{version}"
    description = "test"
    requires-python = ">=3.12"
    """)
    p = tmp_path / "pyproject.toml"
    p.write_text(pyproject, encoding="utf-8")
    paths["pyproject.toml"] = p

    # uv.lock — need a real lock that has [[package]] name = "hal0ai"
    src_lock = repo_root / "uv.lock"
    lock_text = src_lock.read_text(encoding="utf-8")
    # Replace the hal0ai version line
    lock_text = _replace_lock_version(lock_text, version)
    p = tmp_path / "uv.lock"
    p.write_text(lock_text, encoding="utf-8")
    paths["uv.lock"] = p

    # ui/package.json
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    ui_pkg = {
        "name": "hal0-ui",
        "private": True,
        "version": version,
        "type": "module",
        "scripts": {"build": "vite build"},
    }
    p = ui_dir / "package.json"
    p.write_text(json.dumps(ui_pkg, indent=2), encoding="utf-8")
    paths["ui/package.json"] = p

    # ui/package-lock.json — needs top-level version + root packages[""].version
    pkg_lock = {
        "name": "hal0-ui",
        "version": version,
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {
                "name": "hal0-ui",
                "version": version,
            },
        },
    }
    p = ui_dir / "package-lock.json"
    p.write_text(json.dumps(pkg_lock, indent=2), encoding="utf-8")
    paths["ui/package-lock.json"] = p

    # manifest.json
    manifest = {
        "_schema": "hal0.manifest.v1",
        "version": version,
        "channel": "stable",
        "toolbox_images": {},
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    paths["manifest.json"] = p

    return paths


def _replace_lock_version(lock_text: str, new_version: str) -> str:
    """Replace the PEP 440 version line for ``name = "hal0ai"``."""
    import tomllib

    data = tomllib.loads(lock_text)
    for pkg in data.get("package", []):
        if pkg.get("name") == "hal0ai":
            # Make an educated guess: uv uses PEP 440 format, e.g. 1.0.0a0
            # We'll just do a simple string replacement on the file text.
            break

    # Simpler: find the line after 'name = "hal0ai"' and replace version
    lines = lock_text.splitlines()
    result: list[str] = []
    found_hal0 = False
    for line in lines:
        if line.rstrip() == 'name = "hal0ai"':
            found_hal0 = True
            result.append(line)
        elif found_hal0 and line.strip().startswith("version "):
            # e.g. version = "1.0.0a0" → version = "0.9.6.1"
            # Preserve exact formatting
            indent = line[: len(line) - len(line.lstrip())]
            result.append(f'{indent}version = "{new_version}"')
            found_hal0 = False
        else:
            result.append(line)
    return "\n".join(result)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSetVersion:
    """Tests for set_version() core logic."""

    def test_set_version_updates_all_public_files(
        self, tmp_path: Path
    ) -> None:
        """set_version writes SemVer to all public files, PEP 440 to hal0ai
        lock package, and the policy-derived channel to manifest.json."""
        _populate_project(tmp_path, "1.0.0-alpha.0")
        mod = _load_set_version()

        mod.set_version(tmp_path, "1.0.0-alpha.2")

        import tomllib

        # pyproject.toml
        pyproj_text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        assert (
            tomllib.loads(pyproj_text)["project"]["version"] == "1.0.0-alpha.2"
        )

        # ui/package.json
        ui_pkg = json.loads((tmp_path / "ui" / "package.json").read_text(encoding="utf-8"))
        assert ui_pkg["version"] == "1.0.0-alpha.2"

        # ui/package-lock.json — top-level and packages[""]
        ui_lock = json.loads(
            (tmp_path / "ui" / "package-lock.json").read_text(encoding="utf-8")
        )
        assert ui_lock["version"] == "1.0.0-alpha.2"
        assert ui_lock["packages"][""]["version"] == "1.0.0-alpha.2"

        # manifest.json — version + channel (other fields preserved)
        manifest = json.loads(
            (tmp_path / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["version"] == "1.0.0-alpha.2"
        assert manifest["channel"] == "preview"

        # uv.lock — hal0ai version in PEP 440
        lock_text = (tmp_path / "uv.lock").read_text(encoding="utf-8")
        lock_data = tomllib.loads(lock_text)
        hal0ai = next(p for p in lock_data["package"] if p["name"] == "hal0ai")
        assert hal0ai["version"] == "1.0.0a2"

    def test_nightly_is_rejected(self, tmp_path: Path) -> None:
        """Nightly versions are rejected because they don't rewrite source."""
        _populate_project(tmp_path, "1.0.0-alpha.0")
        mod = _load_set_version()

        with pytest.raises((ValueError, RuntimeError)) as exc:
            mod.set_version(tmp_path, "1.0.0-nightly.20260720123456")
        assert "nightly" in str(exc.value).lower()

    def test_raises_on_missing_version_field(
        self, tmp_path: Path
    ) -> None:
        """Missing version field raises without replacing any file."""
        _populate_project(tmp_path, "1.0.0-alpha.0")

        # Corrupt pyproject.toml: remove version
        bad_toml = textwrap.dedent("""\
        [project]
        name = "hal0ai"
        """)
        (tmp_path / "pyproject.toml").write_text(bad_toml, encoding="utf-8")

        mod = _load_set_version()

        with pytest.raises((ValueError, KeyError, RuntimeError)):
            mod.set_version(tmp_path, "1.0.0-alpha.2")

        # No file should be replaced — contents still "1.0.0-alpha.0"

        # pyproject has no version, set_version may read from different path
        # but ui/package.json should be untouched
        ui_pkg = json.loads(
            (tmp_path / "ui" / "package.json").read_text(encoding="utf-8")
        )
        assert ui_pkg["version"] == "1.0.0-alpha.0"

        manifest = json.loads(
            (tmp_path / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["version"] == "1.0.0-alpha.0"

    def test_duplicate_version_field_raises(
        self, tmp_path: Path
    ) -> None:
        """A file with duplicate version fields raises without replacing
        any file."""
        _populate_project(tmp_path, "1.0.0-alpha.0")

        # Write a ui/package.json with TWO version fields (malformed)
        bad_json = '{\n"version": "1.0.0-alpha.0",\n"version": "0.0.1",\n"name": "hal0-ui"\n}'
        (tmp_path / "ui" / "package.json").write_text(bad_json, encoding="utf-8")

        mod = _load_set_version()

        with pytest.raises((ValueError, RuntimeError)):
            mod.set_version(tmp_path, "1.0.0-alpha.2")

        # pyproject.toml should still have the original version
        import tomllib

        pyproj = tomllib.loads(
            (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        )
        assert pyproj["project"]["version"] == "1.0.0-alpha.0"
