"""#2446 repair path: ``hal0 doctor perms --fix`` relinks flm's cache onto the store.

On a box that already hit #2446, flm's ``$HOME/.config/flm/models`` is a real
directory under a root-owned ``.config/flm``, holding the models the user
pulled, while the NPU slot mounts the empty ``[models].flm_store``. The root-run
repair hands the HOME-side parents to the service user, applies the container
ownership to the resolved store, moves the stranded models into the store, and
replaces the default dir with the symlink. It never deletes user data: a name
present on both sides stops the repair and is reported.

``chown`` is injected and the service-user ids are patched, so the tests run
unprivileged.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hal0.providers import flm


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Relocated store + flm default under a fake service HOME; return (default, store)."""
    home = tmp_path / "var-lib-hal0"
    default = home / ".config" / "flm" / "models"
    store = home / "models" / "flm" / "models"
    home.mkdir()
    monkeypatch.setattr("hal0.config.paths.default_flm_models_dir", lambda: str(default))
    monkeypatch.setattr("hal0.config.paths.flm_models_dir", lambda: str(store))
    monkeypatch.setattr(flm, "_service_ids", lambda: (os.getuid(), os.getgid()))
    return default, store


class _Chown:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def __call__(self, path: str, uid: int, gid: int) -> None:
        self.calls.append((path, uid, gid))


def _drift(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if r["status"] == "drift"]


def test_stranded_models_are_migrated_and_relinked(layout: tuple[Path, Path]) -> None:
    default, store = layout
    model = default / "Gemma3-1B-NPU2"
    model.mkdir(parents=True)
    (model / "model.q4nx").write_text("weights", encoding="utf-8")

    rows = flm.audit_host_flm_store_link()
    assert _drift(rows), rows
    assert any(str(default) == r["path"] for r in _drift(rows))

    chown = _Chown()
    actions = flm.repair_host_flm_store_link(chown=chown)

    assert default.is_symlink()
    assert default.resolve() == store.resolve()
    assert (store / "Gemma3-1B-NPU2" / "model.q4nx").read_text(encoding="utf-8") == "weights"
    uid, gid = os.getuid(), os.getgid()
    # HOME-side parents go to the service user so the runtime can relink later.
    assert (str(default.parent.parent), uid, gid) in chown.calls
    assert (str(default.parent), uid, gid) in chown.calls
    # The RESOLVED store gets the container uid + service group.
    assert (str(store), flm._FLM_CONTAINER_UID, gid) in chown.calls
    assert actions
    assert _drift(flm.audit_host_flm_store_link()) == []


def test_conflict_is_reported_and_nothing_is_deleted(layout: tuple[Path, Path]) -> None:
    default, store = layout
    (default / "Model-NPU2").mkdir(parents=True)
    (default / "Model-NPU2" / "w.bin").write_text("home-copy", encoding="utf-8")
    (store / "Model-NPU2").mkdir(parents=True)
    (store / "Model-NPU2" / "w.bin").write_text("store-copy", encoding="utf-8")

    with pytest.raises(flm.FLMStoreLinkError) as exc_info:
        flm.repair_host_flm_store_link(chown=_Chown())

    assert "Model-NPU2" in exc_info.value.message
    assert (default / "Model-NPU2" / "w.bin").read_text(encoding="utf-8") == "home-copy"
    assert (store / "Model-NPU2" / "w.bin").read_text(encoding="utf-8") == "store-copy"
    assert not default.is_symlink()


def test_nothing_to_repair_on_a_box_without_flm(layout: tuple[Path, Path]) -> None:
    default, store = layout
    assert _drift(flm.audit_host_flm_store_link()) == []
    chown = _Chown()
    assert flm.repair_host_flm_store_link(chown=chown) == []
    assert chown.calls == []
    assert not os.path.lexists(default)
    assert not store.exists()


def test_parent_not_owned_by_service_user_is_drift(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fresh-install shape: default absent, but ``.config/flm`` is root-owned.

    The runtime (User=hal0) could not create the link at pull time.
    """
    default, _store = layout
    default.parent.mkdir(parents=True)
    monkeypatch.setattr(flm, "_service_ids", lambda: (os.getuid() + 1, os.getgid()))
    drift = _drift(flm.audit_host_flm_store_link())
    assert any(r["path"] == str(default.parent) for r in drift), drift


def test_correct_link_audits_clean(layout: tuple[Path, Path]) -> None:
    default, store = layout
    store.mkdir(parents=True)
    store.chmod(0o2775)
    default.parent.mkdir(parents=True)
    default.symlink_to(store)
    rows = flm.audit_host_flm_store_link()
    assert _drift(rows) == [], rows
