"""README.md's front-page status blockquote must track every version cut (#1992).

``scripts/set-version.py`` used to rewrite pyproject.toml, ui/package.json,
ui/package-lock.json, manifest.json, and uv.lock — but never README.md. The
public repo's front page (the version-bearing status blockquote near the top)
went stale for two consecutive release candidates as a result (fixed by hand
in #1957, and again by hand for rc.7).

These tests exercise the README-specific behaviour in isolation:

- the happy path rewrites the blockquote's version token in lockstep with
  every other file;
- a missing anchor pattern fails loudly (raises) rather than silently
  leaving README.md untouched — a silent no-op is exactly what recreated the
  bug in the first place;
- a regression guard reads the *shipped* README.md (not a fixture) to prove
  the anchor pattern set-version.py actually looks for still matches the
  real front-page blockquote, the same "guard the guard" shape
  tests/release/test_notes.py uses for the shipped CHANGELOG.md.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_set_version() -> object:
    """Load scripts/set-version.py as a module (hyphen → underscore)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "set_version_script_readme", _REPO_ROOT / "scripts" / "set-version.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["set_version_script_readme"] = mod
    spec.loader.exec_module(mod)
    return mod


def _populate_project(
    tmp_path: Path, version: str, readme_body: str | None = None
) -> dict[str, Path]:
    """Write minimal hermetic copies of every file set_version() touches.

    ``readme_body``, when given, replaces README.md's contents verbatim so
    tests can probe the missing-pattern path; otherwise a README carrying the
    real front-page blockquote shape is written.
    """
    paths: dict[str, Path] = {}

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

    # uv.lock — needs a real lock with [[package]] name = "hal0ai"
    import tomllib

    src_lock = _REPO_ROOT / "uv.lock"
    lock_text = src_lock.read_text(encoding="utf-8")
    data = tomllib.loads(lock_text)
    assert any(pkg.get("name") == "hal0ai" for pkg in data.get("package", []))
    lines = lock_text.splitlines()
    result: list[str] = []
    found_hal0 = False
    for line in lines:
        if line.rstrip() == 'name = "hal0ai"':
            found_hal0 = True
            result.append(line)
        elif found_hal0 and line.strip().startswith("version "):
            indent = line[: len(line) - len(line.lstrip())]
            result.append(f'{indent}version = "{version}"')
            found_hal0 = False
        else:
            result.append(line)
    p = tmp_path / "uv.lock"
    p.write_text("\n".join(result), encoding="utf-8")
    paths["uv.lock"] = p

    ui_dir = tmp_path / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    ui_pkg = {"name": "hal0-ui", "private": True, "version": version, "type": "module"}
    p = ui_dir / "package.json"
    p.write_text(json.dumps(ui_pkg, indent=2), encoding="utf-8")
    paths["ui/package.json"] = p

    pkg_lock = {
        "name": "hal0-ui",
        "version": version,
        "lockfileVersion": 3,
        "requires": True,
        "packages": {"": {"name": "hal0-ui", "version": version}},
    }
    p = ui_dir / "package-lock.json"
    p.write_text(json.dumps(pkg_lock, indent=2), encoding="utf-8")
    paths["ui/package-lock.json"] = p

    manifest = {
        "_schema": "hal0.manifest.v1",
        "version": version,
        "channel": "stable",
        "toolbox_images": {},
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    paths["manifest.json"] = p

    if readme_body is None:
        readme_body = textwrap.dedent(f"""\
        <div align="center">

        ### Open-source home AI inference platform

        </div>

        > **v{version} is the GA release.** It is what the `stable` channel
        > has been waiting for since 0.9.8 shipped. From v{version} the
        > project follows semver proper.
        """)
    p = tmp_path / "README.md"
    p.write_text(readme_body, encoding="utf-8")
    paths["README.md"] = p

    return paths


def _original_bytes(paths: dict[str, Path]) -> dict[str, bytes]:
    return {name: path.read_bytes() for name, path in paths.items()}


# ── Happy path ───────────────────────────────────────────────────────────────


def test_readme_status_line_version_is_rewritten(tmp_path: Path) -> None:
    """set_version() rewrites README.md's status blockquote in lockstep with
    every other version-bearing file."""
    _populate_project(tmp_path, "1.0.0")
    mod = _load_set_version()

    mod.set_version(tmp_path, "1.0.1")

    readme_text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "v1.0.1 is the GA release" in readme_text
    assert "v1.0.0 is the GA release" not in readme_text


def test_readme_only_the_status_line_version_token_changes(tmp_path: Path) -> None:
    """Everything around the version token in the blockquote is preserved —
    this is a targeted rewrite, not a line replacement."""
    _populate_project(tmp_path, "1.0.0")
    mod = _load_set_version()

    mod.set_version(tmp_path, "1.0.1")

    readme_text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "is the GA release.** It is what the `stable` channel" in readme_text
    assert "### Open-source home AI inference platform" in readme_text


# ── Loud failure when the anchor pattern is missing ─────────────────────────


def test_readme_missing_pattern_raises_loudly(tmp_path: Path) -> None:
    """A README without the status blockquote must hard-fail, not silently
    leave the file (and the front page) untouched (#1992)."""
    paths = _populate_project(
        tmp_path,
        "1.0.0",
        readme_body="# hal0\n\nNo status blockquote here at all.\n",
    )
    originals = _original_bytes(paths)
    mod = _load_set_version()

    with pytest.raises(ValueError, match=r"README\.md"):
        mod.set_version(tmp_path, "1.0.1")

    # Nothing was rewritten — a partial or silent update would be worse than
    # the loud failure.
    assert {name: path.read_bytes() for name, path in paths.items()} == originals


def test_readme_missing_pattern_error_names_the_fix(tmp_path: Path) -> None:
    """The failure message points at the pattern to update, not just 'not found'."""
    _populate_project(tmp_path, "1.0.0", readme_body="# hal0\n\nnothing to see here\n")
    mod = _load_set_version()

    with pytest.raises(ValueError, match=r"set-version\.py"):
        mod.set_version(tmp_path, "1.0.1")


# ── Regression: the *shipped* README.md, not a fixture (#1992) ─────────────
#
# The hermetic tests above only ever exercise a synthetic README that was
# written to match whatever pattern set-version.py currently looks for — a
# fixture like that agrees with the implementation by construction and can't
# catch the pattern drifting out of sync with the real file. This reads the
# actual README.md that ships at the repo root, the same "guard the guard"
# shape tests/release/test_notes.py uses for CHANGELOG.md's heading
# conventions (see its #1874 regression tests).


def test_shipped_readme_status_line_matches_the_set_version_pattern() -> None:
    """The real README.md must contain the version-bearing status line
    set-version.py's regex looks for — otherwise every future version cut
    hard-fails (correctly) but nobody intended that today."""
    mod = _load_set_version()
    readme_text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

    match = mod._README_STATUS_LINE_RE.search(readme_text)
    assert match is not None, (
        "README.md no longer contains a line matching "
        f"{mod._README_STATUS_LINE_RE.pattern!r} — update the pattern in "
        "scripts/set-version.py to match the current front-page status line"
    )


def test_shipped_readme_status_line_rewrite_round_trips() -> None:
    """Rewriting the shipped README.md's status line to a new version and
    back must be a pure, single-line substitution — no collateral damage."""
    mod = _load_set_version()
    readme_text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    match = mod._README_STATUS_LINE_RE.search(readme_text)
    assert match is not None
    original_version = match.group(2)

    bumped = mod._update_readme_status_line(readme_text, "9.9.9-rc.1")
    assert "v9.9.9-rc.1" in bumped
    assert f"v{original_version} is the GA release" not in bumped

    restored = mod._update_readme_status_line(bumped, original_version)
    assert restored == readme_text
