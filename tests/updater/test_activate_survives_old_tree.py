"""The root activation step survives the tree it swaps in (update-rollback P0).

``_activate_release_unlocked`` runs in a process that was started from the
OUTGOING release: the privileged seam's ``python -m hal0.updater.privileged``
(or a root ``hal0 update``) imported ``hal0.updater.updater`` before the swap.
After the swap and the re-pip, site-packages hold the INCOMING tree, so any
module that process imports for the first time from then on is the incoming
tree's copy. On a rollback that tree is older.

Since #3870 the activation step lazily imported ``hal0.install.perms`` after
the re-pip and called ``converge_hermes_plugin_trees()``. Rolling back to any
tree without that function (every 1.7.x release) raised ``AttributeError``,
the seam answered ``activate (rc=1)``, and the box was left half rolled back:
``current`` and site-packages on the old build, but no unit re-render, no
``hal0.previous`` rewrite and no ``rollback_ok``.

These tests simulate the re-pip by swapping a stand-in for the older tree's
``hal0.install.perms`` into ``sys.modules`` at the moment the fake reinstall
runs, exactly what a fresh import would find on disk.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import hal0.install
from hal0.install import perms
from hal0.updater import updater as updater_module

OLD = "hal0-1.7.1"
NEW = "hal0-1.8.0"


def _old_tree_perms(*, ownership_table: Any = None) -> types.ModuleType:
    """A ``hal0.install.perms`` as a pre-#3870 tree ships it.

    It has the registry-lock primitives (``ownership_table`` / ``plan`` /
    ``commit``, present in every release since 1.4.0) and no
    ``converge_hermes_plugin_trees``.
    """
    old = types.ModuleType("hal0.install.perms")
    old.ownership_table = ownership_table or perms.ownership_table  # type: ignore[attr-defined]
    old.plan = perms.plan  # type: ignore[attr-defined]
    old.commit = lambda *_a, **_k: []  # type: ignore[attr-defined]
    return old


def _root_activation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    old_perms: types.ModuleType,
    converge: Any,
) -> list[Path]:
    """Run as a non-editable root activation whose re-pip lands ``old_perms``.

    Returns the list the fake re-pip records its install dirs into.
    """
    monkeypatch.setattr(updater_module, "_is_editable_install", lambda: False)
    monkeypatch.setattr(updater_module.os, "geteuid", lambda: 0)
    for name in ("refresh_privileged_wrappers", "refresh_path_links", "refresh_gpu_perms_unit"):
        monkeypatch.setattr(updater_module, name, lambda *_a, **_k: {"refreshed": []})
    monkeypatch.setattr(updater_module, "remove_retired_artifacts", lambda **_k: [])
    # The activating tree's own primitives: never chown anything under test.
    monkeypatch.setattr(perms, "commit", lambda *_a, **_k: [])
    monkeypatch.setattr(perms, "converge_hermes_plugin_trees", converge)

    repipped: list[Path] = []

    def _fake_repip(install_dir: Path, *, job_id: str | None = None) -> None:
        repipped.append(install_dir)
        # What a first import after the re-pip resolves to: the older tree.
        monkeypatch.setitem(sys.modules, "hal0.install.perms", old_perms)
        monkeypatch.setattr(hal0.install, "perms", old_perms, raising=False)

    monkeypatch.setattr(updater_module, "_reinstall_into_venv", _fake_repip)
    return repipped


def _release_trees() -> tuple[Path, Path]:
    root = updater_module._usr_lib_root()
    root.mkdir(parents=True, exist_ok=True)
    old, new = root / OLD, root / NEW
    old.mkdir()
    new.mkdir()
    return old, new


def test_activation_survives_an_older_perms_without_the_converge(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The case the release validation hit: the older tree has no converge."""
    old, new = _release_trees()
    os.symlink(new, updater_module._current_symlink())
    events: list[str] = []
    repipped = _root_activation(
        monkeypatch,
        old_perms=_old_tree_perms(),
        converge=lambda: events.append("converge") or [],
    )

    result = updater_module.activate_release(OLD)

    assert repipped == [old]
    assert result["target"] == str(old)
    assert result["previous"] == str(new)
    # The activating code's own converge ran, not the older tree's (absent) one.
    assert events == ["converge"]
    assert Path(os.readlink(updater_module._current_symlink())) == old
    assert "updater.activate_ok" in capsys.readouterr().out


def test_activation_survives_a_converge_that_raises(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Best-effort by design: a failing repair logs and the activation carries on."""
    old, new = _release_trees()
    os.symlink(new, updater_module._current_symlink())

    def _boom() -> list[Path]:
        raise RuntimeError("converge exploded")

    _root_activation(monkeypatch, old_perms=_old_tree_perms(), converge=_boom)

    result = updater_module.activate_release(OLD)

    assert result["target"] == str(old)
    out = capsys.readouterr().out
    assert "updater.hermes_plugin_trees_converge_failed" in out
    assert "converge exploded" in out
    assert "updater.activate_ok" in out


def test_activation_survives_a_registry_lock_repair_that_raises(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lock repair (#2698) reaches into the swapped-in tree too.

    ``ownership_table`` resolves ``hal0.services.mdns`` lazily, so after the
    re-pip it reads the incoming tree's module. Anything it raises there must
    not abort the activation either.
    """
    old, new = _release_trees()
    os.symlink(new, updater_module._current_symlink())

    def _old_table(*_a: Any, **_k: Any) -> list[Any]:
        raise AttributeError("module 'hal0.services.mdns' has no attribute 'services_dir'")

    monkeypatch.setattr(perms, "ownership_table", _old_table)
    _root_activation(
        monkeypatch,
        old_perms=_old_tree_perms(ownership_table=_old_table),
        converge=lambda: [],
    )

    result = updater_module.activate_release(OLD)

    assert result["target"] == str(old)
    out = capsys.readouterr().out
    assert "updater.registry_lock_perms_failed" in out
    assert "updater.activate_ok" in out


@pytest.mark.parametrize("converge_raises", [False, True])
def test_rollback_to_a_tree_without_the_converge_completes(
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    converge_raises: bool,
) -> None:
    """``hal0 update --rollback`` across the #3870 boundary finishes every step."""
    old, new = _release_trees()
    os.symlink(new, updater_module._current_symlink())
    record = updater_module._previous_record()
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(str(old), encoding="utf-8")

    def _converge() -> list[Path]:
        if converge_raises:
            raise RuntimeError("converge exploded")
        return []

    _root_activation(monkeypatch, old_perms=_old_tree_perms(), converge=_converge)
    after_swap: list[str] = []

    async def _rerender(job_id: str | None) -> None:
        after_swap.append("rerender")

    async def _api_unit(job_id: str | None) -> None:
        after_swap.append("api_unit")

    monkeypatch.setattr(updater_module, "_rerender_units_after_swap", _rerender)
    monkeypatch.setattr(updater_module, "_refresh_api_unit_after_swap", _api_unit)

    result = asyncio.run(updater_module.Updater().rollback())

    assert result["rolled_back_to"] == str(old)
    assert result["previous_now"] == str(new)
    assert Path(os.readlink(updater_module._current_symlink())) == old
    # The steps the crash used to skip.
    assert after_swap == ["rerender", "api_unit"]
    assert record.read_text(encoding="utf-8") == str(new)
    out = capsys.readouterr().out
    assert "updater.activate_ok" in out
    assert "updater.rollback_ok" in out
