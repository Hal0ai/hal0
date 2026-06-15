"""Tests for the Layer-3 ownership-drift check behind ``hal0 doctor perms`` (#843).

Read-only: it reports when Hermes state is ``root:root`` (the silent
root-clobber failure) or when a split-brain ``/root/.hermes`` exists. It never
repairs — that's the explicit ``bootstrap --repair`` path. The owner lookup and
existence checks are injected seams so the logic is testable without root.
"""

from __future__ import annotations

from pathlib import Path

from hal0.cli import doctor_commands as dc

_HOME = Path("/var/lib/hal0/.hermes")
_VENV = Path("/var/lib/hal0/venvs/hermes")
_STRAY = Path("/root/.hermes")


def _check(owners: dict[Path, str | None], *, present: set[Path]):
    return dc.check_hermes_ownership(
        owner_of=lambda p: owners.get(p),
        exists=lambda p: p in present,
    )


def test_clean_install_reports_no_drift() -> None:
    owners = {
        _HOME: "hal0",
        _HOME / "config.yaml": "hal0",
        _HOME / "runtime.json": "hal0",
        _VENV: "hal0",
    }
    rows = _check(owners, present=set(owners))
    assert dc.has_ownership_drift(rows) is False
    assert all(r["status"] in ("ok", "absent") for r in rows)


def test_root_owned_config_is_drift() -> None:
    owners = {
        _HOME: "hal0",
        _HOME / "config.yaml": "root",  # the clobber
        _HOME / "runtime.json": "hal0",
        _VENV: "hal0",
    }
    rows = _check(owners, present=set(owners))
    assert dc.has_ownership_drift(rows) is True
    drift = [r for r in rows if r["status"] == "drift"]
    assert any("config.yaml" in r["path"] for r in drift)


def test_stray_root_home_is_drift() -> None:
    owners = {_HOME: "hal0", _VENV: "hal0"}
    rows = _check(owners, present=set(owners) | {_STRAY})
    assert dc.has_ownership_drift(rows) is True
    assert any(str(_STRAY) in r["path"] and r["status"] == "drift" for r in rows)


def test_missing_paths_are_absent_not_drift() -> None:
    rows = _check({}, present=set())
    assert dc.has_ownership_drift(rows) is False
    assert all(r["status"] == "absent" for r in rows if str(_STRAY) not in r["path"])
