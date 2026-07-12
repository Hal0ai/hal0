"""Finding 5: _scripts_dir() / _workflows_src_dir() FHS-aware resolution.

Same class of bug as #1284 (preflight.sh) / #1285 (agent installer
scripts): ``hal0.comfyui.fetch`` used to resolve its scripts + curated
workflow assets ONLY via the editable-checkout path
(``Path(__file__).parent * 4``). Under the non-editable wheel install
``install.sh`` ships in production, ``__file__`` resolves into
site-packages, which has no ``installer/`` sibling, so every ComfyUI
model download 202'd and then immediately failed. These tests pin the
fix: try the editable candidate first, fall back to the FHS code root
(:func:`hal0.config.paths.usr_lib`), mirroring
``hal0.agents.manager.installer_script_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.comfyui import fetch as fetch_mod

# ── _scripts_dir ────────────────────────────────────────────────────────────


def test_scripts_dir_resolves_editable_when_present() -> None:
    """Editable / dev checkout: this test runs against the real checkout,
    which has ``installer/comfyui/scripts`` three parents up from
    ``src/hal0/comfyui/fetch.py`` — no monkeypatching needed, this
    exercises the real resolution path."""
    resolved = fetch_mod._scripts_dir()
    assert resolved.is_dir()
    assert resolved == (
        Path(fetch_mod.__file__).resolve().parents[3] / "installer" / "comfyui" / "scripts"
    )


def test_scripts_dir_falls_back_to_fhs_for_wheel_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-editable wheel install has ``fetch.py`` under
    ``<venv>/lib/pythonX/site-packages/hal0/comfyui/fetch.py`` — three
    parents up from there is a venv dir, not a repo root, so it has no
    ``installer/`` sibling. The function must fall back to the FHS code
    root (:func:`hal0.config.paths.usr_lib`, ``/usr/lib/hal0/current`` in
    production) and find the scripts dir there — this was the root cause
    of ComfyUI model-fetch jobs 202-ing and then immediately failing on a
    real FHS install."""
    fake_module_path = (
        tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "hal0" / "comfyui" / "fetch.py"
    )
    fake_module_path.parent.mkdir(parents=True)
    monkeypatch.setattr(fetch_mod, "__file__", str(fake_module_path))

    fhs_root = tmp_path / "usr-lib-hal0-current"
    scripts_dir = fhs_root / "installer" / "comfyui" / "scripts"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(fetch_mod._paths, "usr_lib", lambda: fhs_root)

    resolved = fetch_mod._scripts_dir()
    assert resolved == scripts_dir
    assert resolved.is_dir()


def test_scripts_dir_prefers_editable_when_both_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an editable-shaped repo root has the scripts dir, it wins over
    the FHS candidate (resolution order: editable first, FHS fallback)."""
    fake_module_path = tmp_path / "repo" / "src" / "hal0" / "comfyui" / "fetch.py"
    fake_module_path.parent.mkdir(parents=True)
    monkeypatch.setattr(fetch_mod, "__file__", str(fake_module_path))

    editable_scripts_dir = tmp_path / "repo" / "installer" / "comfyui" / "scripts"
    editable_scripts_dir.mkdir(parents=True)

    fhs_root = tmp_path / "fhs"
    fhs_scripts_dir = fhs_root / "installer" / "comfyui" / "scripts"
    fhs_scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(fetch_mod._paths, "usr_lib", lambda: fhs_root)

    resolved = fetch_mod._scripts_dir()
    assert resolved == editable_scripts_dir


def test_scripts_dir_missing_everywhere_returns_fhs_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the scripts dir exists in neither location, the function
    still returns a path rather than raising/None — the FHS candidate —
    so a downstream "no such file" subprocess error points at the real
    production path, not a venv path nobody would recognise."""
    fake_module_path = (
        tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "hal0" / "comfyui" / "fetch.py"
    )
    fake_module_path.parent.mkdir(parents=True)
    monkeypatch.setattr(fetch_mod, "__file__", str(fake_module_path))

    fhs_root = tmp_path / "usr-lib-hal0-current"
    monkeypatch.setattr(fetch_mod._paths, "usr_lib", lambda: fhs_root)

    resolved = fetch_mod._scripts_dir()
    assert resolved == fhs_root / "installer" / "comfyui" / "scripts"


# ── _workflows_src_dir ───────────────────────────────────────────────────────


def test_workflows_src_dir_resolves_editable_when_present() -> None:
    """Editable / dev checkout: real checkout has
    ``installer/comfyui/workflows`` three parents up from
    ``src/hal0/comfyui/fetch.py``."""
    resolved = fetch_mod._workflows_src_dir()
    assert resolved.is_dir()
    assert resolved == (
        Path(fetch_mod.__file__).resolve().parents[3] / "installer" / "comfyui" / "workflows"
    )


def test_workflows_src_dir_falls_back_to_fhs_for_wheel_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same FHS-fallback contract as _scripts_dir(), for the curated
    workflow JSON source dir."""
    fake_module_path = (
        tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "hal0" / "comfyui" / "fetch.py"
    )
    fake_module_path.parent.mkdir(parents=True)
    monkeypatch.setattr(fetch_mod, "__file__", str(fake_module_path))

    fhs_root = tmp_path / "usr-lib-hal0-current"
    workflows_dir = fhs_root / "installer" / "comfyui" / "workflows"
    workflows_dir.mkdir(parents=True)
    monkeypatch.setattr(fetch_mod._paths, "usr_lib", lambda: fhs_root)

    resolved = fetch_mod._workflows_src_dir()
    assert resolved == workflows_dir
    assert resolved.is_dir()


# ── fetch_model() / _provision_workflow() actually use the FHS fallback ─────


def test_provision_workflow_finds_asset_via_fhs_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: with fetch.py "installed" as a non-editable wheel,
    _provision_workflow must still locate + copy the curated workflow
    JSON by falling back to the FHS workflows dir, instead of silently
    logging comfyui.workflow_asset_missing (the production symptom in
    Finding 5)."""
    fake_module_path = (
        tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "hal0" / "comfyui" / "fetch.py"
    )
    fake_module_path.parent.mkdir(parents=True)
    monkeypatch.setattr(fetch_mod, "__file__", str(fake_module_path))

    fhs_root = tmp_path / "usr-lib-hal0-current"
    workflows_src_dir = fhs_root / "installer" / "comfyui" / "workflows"
    workflows_src_dir.mkdir(parents=True)
    (workflows_src_dir / "sdxl.json").write_text("{}")
    monkeypatch.setattr(fetch_mod._paths, "usr_lib", lambda: fhs_root)

    dest_dir = tmp_path / "workflows-dest"
    monkeypatch.setattr(fetch_mod, "_workflows_dir", lambda: dest_dir)

    variant = type("V", (), {"workflow": "sdxl.json"})()
    result = fetch_mod._provision_workflow(variant)

    assert result == str(dest_dir / "sdxl.json")
    assert (dest_dir / "sdxl.json").is_file()
