"""TDD — Task 3.3: ComfyUI extensions-registry entry.

Assertions:
  (a) "comfyui" is in EXTENSIONS (by id).
  (b) install_extension("comfyui") calls comfy-up.sh path (not systemctl).
"""

from __future__ import annotations


def test_comfyui_in_extensions():
    from hal0.install.extensions import EXTENSIONS

    ids = [e.id for e in EXTENSIONS]
    assert "comfyui" in ids, f"comfyui missing from EXTENSIONS; got: {ids}"


def test_comfyui_extension_metadata():
    from hal0.install.extensions import get_extension

    ext = get_extension("comfyui")
    assert ext is not None
    assert ext.kind == "app"
    assert ext.default_enabled is True


def test_install_extension_comfyui_calls_comfy_up(monkeypatch):
    """install_extension('comfyui') must invoke comfy-up.sh (not systemctl)."""
    import hal0.install.extensions as exts

    called = []

    def _fake_run(cmd, **kw):
        called.append(cmd)

    monkeypatch.setattr(exts, "_run", _fake_run)
    # Also mock shutil.which so the podman guard passes
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    result = exts.install_extension("comfyui")
    assert result.installed is True or result.skipped is None
    # Must have called something with comfy-up.sh, NOT systemctl
    assert called, "install_extension('comfyui') made no subprocess call"
    flat = " ".join(str(c) for c in called[0])
    assert "comfy-up" in flat, f"Expected comfy-up.sh in call; got: {called}"
    assert "systemctl" not in flat, f"Should not call systemctl for comfyui; got: {called}"
