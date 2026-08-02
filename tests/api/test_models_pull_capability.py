"""_resolve_pull_capability — capability + comfyui_subdir for a pull (P3).

The helper moved to ``hal0.registry.pull_jobs`` (P3-routers §J); ``routes.models``
keeps a re-export binding, but ``get_curated`` is now resolved in ``pull_jobs``,
so that is the monkeypatch target below.
"""

from __future__ import annotations

from types import SimpleNamespace

from hal0.api.routes.models import _resolve_pull_capability


class _Reg:
    def __init__(self, entry=None):
        self._entry = entry

    def get(self, _mid):
        if self._entry is None:
            raise KeyError(_mid)
        return self._entry


def _req(entry=None):
    app = SimpleNamespace(state=SimpleNamespace(model_registry=_Reg(entry)))
    return SimpleNamespace(app=app)


def test_body_capability_wins():
    cap, subdir = _resolve_pull_capability(_req(), "anything", {"capability": "embed"})
    assert cap == "embed"
    assert subdir is None


def test_registry_capability_used_when_no_body():
    entry = SimpleNamespace(capabilities=["stt", "chat"], hf_repo="r", hf_filename="f")
    cap, _ = _resolve_pull_capability(_req(entry), "m", None)
    assert cap == "stt"


def test_curated_capability_and_subdir_fallback(monkeypatch):
    import hal0.registry.pull_jobs as m

    curated = SimpleNamespace(capability="image", comfyui_subdir="checkpoints")
    monkeypatch.setattr(m, "get_curated", lambda _mid: curated)
    cap, subdir = _resolve_pull_capability(_req(), "sd-turbo", None)
    assert cap == "image"
    assert subdir == "checkpoints"


def test_unknown_model_returns_none(monkeypatch):
    import hal0.registry.pull_jobs as m

    monkeypatch.setattr(m, "get_curated", lambda _mid: None)
    cap, subdir = _resolve_pull_capability(_req(), "ad-hoc", None)
    assert cap is None
    assert subdir is None


def test_registered_curated_image_model_keeps_comfyui_subdir(monkeypatch):
    """RE-pulling an already-registered curated image model keeps the subdir.

    The repair path for deleted checkpoint bytes: the registry row survives,
    so resolution took the registry branch — which used to return
    ``subdir=None`` and landed the safetensors at ``image/<id>/model.gguf``
    where ComfyUI never finds it.
    """
    import hal0.registry.pull_jobs as m

    curated = SimpleNamespace(capability="image", comfyui_subdir="checkpoints")
    monkeypatch.setattr(m, "get_curated", lambda _mid: curated)
    entry = SimpleNamespace(capabilities=["image"], hf_repo="r", hf_filename="f")
    cap, subdir = _resolve_pull_capability(_req(entry), "sdxl-turbo", None)
    assert cap == "image"
    assert subdir == "checkpoints"


def test_body_capability_keeps_curated_subdir(monkeypatch):
    """An explicit body capability doesn't discard the curated subdir."""
    import hal0.registry.pull_jobs as m

    curated = SimpleNamespace(capability="image", comfyui_subdir="checkpoints")
    monkeypatch.setattr(m, "get_curated", lambda _mid: curated)
    cap, subdir = _resolve_pull_capability(_req(), "sdxl-turbo", {"capability": "image"})
    assert cap == "image"
    assert subdir == "checkpoints"
