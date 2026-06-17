"""TDD — Task 3.3: ComfyUI extensions-registry entry.

Assertions:
  (a) "comfyui" is in EXTENSIONS (by id).
  (b) install_extension("comfyui") enables the slot-managed img unit.
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


def test_install_extension_comfyui_enables_img_slot(monkeypatch):
    """install_extension('comfyui') must use the slot-owned img runtime."""
    import hal0.install.extensions as exts

    called = []

    def _fake_run(cmd, **kw):
        called.append(cmd)

    monkeypatch.setattr(exts, "_run", _fake_run)

    result = exts.install_extension("comfyui")
    assert result.installed is True or result.skipped is None
    assert called, "install_extension('comfyui') made no subprocess call"
    assert called[0] == ["systemctl", "enable", "--now", "hal0-slot@img.service"]
