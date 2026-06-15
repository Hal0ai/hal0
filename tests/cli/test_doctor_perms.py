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


# ── editable-checkout group-share (#843 fix surface) ──────────────────────────

import stat as _stat  # noqa: E402
import subprocess  # noqa: E402

_ROOT = Path("/opt/hal0")

# A fully group-shared dir: setgid + group-writable.
_SHARED_MODE = _stat.S_IFDIR | 0o2775
# A root-clobbered dir: plain 0755, no setgid, no group write.
_CLOBBERED_MODE = _stat.S_IFDIR | 0o0755


def _tree(
    *,
    group: str | None,
    mode: int,
    shared: str | None,
    root: Path | None = _ROOT,
):
    return dc.check_tree_group_share(
        root,
        group="hal0",
        group_of=lambda p: group,
        mode_of=lambda p: mode,
        git_shared_of=lambda p: shared,
    )


def test_group_shared_tree_reports_no_drift() -> None:
    rows = _tree(group="hal0", mode=_SHARED_MODE, shared="group")
    assert dc.has_ownership_drift(rows) is False
    assert all(r["status"] == "ok" for r in rows)


def test_root_owned_tree_is_drift_on_every_axis() -> None:
    # The live CT105 state before the fix: group=root, 0755, sharedRepository unset.
    rows = _tree(group="root", mode=_CLOBBERED_MODE, shared=None)
    assert dc.has_ownership_drift(rows) is True
    drift = {r["label"] for r in rows if r["status"] == "drift"}
    assert "tree group == hal0" in drift
    assert "tree group-writable" in drift
    assert "dirs setgid (new files inherit group)" in drift
    assert "git core.sharedRepository" in drift


def test_group_writable_but_no_setgid_still_drifts() -> None:
    # Group/g+w fixed by a bare `chgrp`+`chmod g+w` but no setgid → new files
    # won't inherit the group, so the creep returns. Must still flag.
    rows = _tree(group="hal0", mode=_stat.S_IFDIR | 0o0775, shared="group")
    assert dc.has_ownership_drift(rows) is True
    assert {r["label"] for r in rows if r["status"] == "drift"} == {
        "dirs setgid (new files inherit group)"
    }


def test_git_shared_accepts_group_synonyms() -> None:
    for val in ("group", "true", "1", "all"):
        rows = _tree(group="hal0", mode=_SHARED_MODE, shared=val)
        row = next(r for r in rows if r["label"] == "git core.sharedRepository")
        assert row["status"] == "ok", f"{val!r} should read as shared"


def test_fhs_install_has_nothing_to_share() -> None:
    rows = _tree(group=None, mode=0, shared=None, root=None)
    assert dc.has_ownership_drift(rows) is False
    assert rows[0]["status"] == "absent"


def test_detect_editable_root_walks_up_to_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src" / "hal0" / "cli"
    nested.mkdir(parents=True)
    assert dc.detect_editable_root(nested) == tmp_path


def test_detect_editable_root_none_without_git(tmp_path: Path) -> None:
    nested = tmp_path / "usr" / "lib" / "hal0"
    nested.mkdir(parents=True)
    assert dc.detect_editable_root(nested) is None


def test_repair_runs_chgrp_setgid_and_git_share() -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    ok, msg = dc.repair_tree_group_share(_ROOT, "hal0", run=fake_run)
    assert ok is True
    assert "applied" in msg
    joined = [" ".join(c) for c in calls]
    assert any(c.startswith("chgrp -R hal0") for c in joined)
    assert any("chmod g+s" in c for c in joined)
    assert any("core.sharedRepository group" in c for c in joined)


def test_repair_short_circuits_on_failure() -> None:
    def fake_run(argv, **_kw):
        rc = 1 if argv[0] == "chgrp" else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="operation not permitted")

    ok, msg = dc.repair_tree_group_share(_ROOT, "hal0", run=fake_run)
    assert ok is False
    assert "chgrp failed" in msg
    assert "operation not permitted" in msg
