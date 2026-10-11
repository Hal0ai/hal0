"""``remove_retired_artifacts`` — the one post-activation sweep for root-owned
files older releases installed and nothing replaces.

``install.sh`` used to be the only place that removed them, and ``hal0 update``
never runs ``install.sh``, so a box that was only ever updated kept them. The
sweep now runs from ``activate_release`` (root side) on every update, and from
``install.sh``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import hal0.system.seam_check as seam_check
from hal0.updater import updater as U

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    bin_dir = tmp_path / "usr-lib-hal0-bin"
    sudoers = tmp_path / "sudoers.d"
    units = tmp_path / "systemd-system"
    for d in (bin_dir, sudoers, units):
        d.mkdir()
    monkeypatch.setattr(seam_check, "SEAM_BIN_DIR", bin_dir)
    monkeypatch.setattr(seam_check, "SUDOERS_DIR", sudoers)
    monkeypatch.setattr(U, "SYSTEMD_UNIT_DIR", units)
    monkeypatch.setattr(U.os, "geteuid", lambda: 0)
    runs: list[list[str]] = []
    monkeypatch.setattr(U.subprocess, "run", lambda argv, *a, **k: runs.append(list(argv)) or None)
    return {"bin": bin_dir, "sudoers": sudoers, "units": units, "runs": runs}


def test_removes_the_slotctl_grant_and_the_static_slot_template(roots: dict[str, Any]) -> None:
    slotctl = roots["bin"] / "hal0-slotctl"
    grant = roots["sudoers"] / "hal0-slotctl"
    template = roots["units"] / "hal0-slot@.service"
    dropin = roots["units"] / "hal0-slot@chat.service.d"
    for f in (slotctl, grant, template):
        f.write_text("x", encoding="utf-8")
    dropin.mkdir()
    (dropin / "override.conf").write_text("[Service]\n", encoding="utf-8")

    removed = U.remove_retired_artifacts(job_id="j")

    assert sorted(removed) == sorted(str(p) for p in (slotctl, grant, template, dropin))
    for p in (slotctl, grant, template, dropin):
        assert not p.exists()
    assert ["systemctl", "daemon-reload"] in roots["runs"]


def test_removes_the_comfyui_grant(roots: dict[str, Any]) -> None:
    """hal0-comfyui left the product in #984 and stays on long-lived boxes."""
    grant = roots["sudoers"] / "hal0-comfyui"
    grant.write_text(
        "hal0 ALL=(root) NOPASSWD: /usr/bin/systemctl restart hal0-slot@img.service\n",
        encoding="utf-8",
    )
    assert U.remove_retired_artifacts() == [str(grant)]
    assert not grant.exists()
    assert roots["runs"] == []


def test_every_shipped_sudoers_grant_survives(roots: dict[str, Any]) -> None:
    """The sweep must never touch a grant the release still ships."""
    shipped = sorted(p.name for p in (_REPO / "packaging" / "sudoers").iterdir())
    assert shipped, "packaging/sudoers is empty — wrong repo root?"
    assert not set(shipped) & set(U._RETIRED_SUDOERS)
    for name in shipped:
        (roots["sudoers"] / name).write_text("x", encoding="utf-8")
    U.remove_retired_artifacts()
    assert sorted(p.name for p in roots["sudoers"].iterdir()) == shipped


def test_uninstall_removes_every_shipped_and_retired_grant() -> None:
    text = (_REPO / "installer" / "uninstall.sh").read_text(encoding="utf-8")
    shipped = {p.name for p in (_REPO / "packaging" / "sudoers").iterdir()}
    for name in sorted(shipped | set(U._RETIRED_SUDOERS)):
        assert f'rm_path "/etc/sudoers.d/{name}"' in text, name


def test_leaves_current_files_alone(roots: dict[str, Any]) -> None:
    keep = [
        roots["bin"] / "hal0-systemctl",
        roots["sudoers"] / "hal0-systemctl",
        roots["units"] / "hal0-api.service",
        roots["units"] / "hal0-agent@.service",
    ]
    for f in keep:
        f.write_text("x", encoding="utf-8")
    (roots["units"] / "hal0-agent@hermes.service.d").mkdir()

    assert U.remove_retired_artifacts() == []
    for f in keep:
        assert f.exists()
    assert (roots["units"] / "hal0-agent@hermes.service.d").is_dir()
    assert roots["runs"] == []


def test_no_daemon_reload_when_only_the_grant_goes(roots: dict[str, Any]) -> None:
    (roots["sudoers"] / "hal0-slotctl").write_text("x", encoding="utf-8")
    assert U.remove_retired_artifacts() == [str(roots["sudoers"] / "hal0-slotctl")]
    assert roots["runs"] == []


def test_is_a_noop_when_not_root(roots: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(U.os, "geteuid", lambda: 1000)
    grant = roots["sudoers"] / "hal0-slotctl"
    grant.write_text("x", encoding="utf-8")
    assert U.remove_retired_artifacts() == []
    assert grant.exists()


def test_activate_release_runs_the_sweep() -> None:
    import inspect

    assert "remove_retired_artifacts(job_id=job_id)" in inspect.getsource(U.activate_release)


def test_install_sh_runs_the_same_sweep() -> None:
    text = (_REPO / "installer" / "install.sh").read_text(encoding="utf-8")
    assert "from hal0.updater.updater import remove_retired_artifacts" in text
    # The old inline bash cleanups are gone: the sweep is the one owner.
    assert "/bin/hal0-slotctl" not in text
    assert "/etc/sudoers.d/hal0-slotctl" not in text
    assert "LEGACY_SLOT_UNIT" not in text
