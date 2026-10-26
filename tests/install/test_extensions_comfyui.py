"""TDD — Task 3.3: ComfyUI extensions-registry entry.

Assertions:
  (a) "comfyui" is in EXTENSIONS (by id).
  (b) install_extension("comfyui") runs no subprocess (#3038): the img slot is
      Quadlet-generated, so no systemctl enable and no unasked start.
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


def test_install_extension_comfyui_starts_nothing(monkeypatch):
    """install_extension('comfyui') must not touch the img unit (#3038)."""
    import hal0.install.extensions as exts

    called = []

    def _fake_run(cmd, **kw):
        called.append(cmd)

    monkeypatch.setattr(exts, "_run", _fake_run)
    monkeypatch.setattr(exts, "_run_ok", lambda cmd: called.append(cmd) or True)

    result = exts.install_extension("comfyui")
    assert result.installed is True
    assert result.error is None
    assert called == []
