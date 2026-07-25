"""A slot's model file must be bind-mounted even when it lives outside every
configured model root.

Live regression (Strix Halo box, 150): 42 models under ``/mnt/ai-models`` while
``[models].store`` and ``[models].pull_root`` both pointed at
``/var/lib/hal0/models``. ``model_mount_roots()`` then returned only
``/var/lib/hal0/models``, so a slot whose registry model path was under
``/mnt/ai-models`` got a container with that file UNREACHABLE — llama-server
exits at load and the slot's container "crashes" (flaps warming↔error). The
renderer must mount the model file's own directory whenever no configured root
covers it.
"""

from __future__ import annotations

import pytest

from hal0.providers import container as C
from hal0.providers.container import _llama_launch_plan


def _plan(model_path: str):
    return _llama_launch_plan(
        image="img",
        port=8090,
        model_path=model_path,
        flags_str="",
        devices=[],
        group_ids=[],
    )


def test_model_outside_configured_roots_is_mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    # Configured roots cover only /var/lib/hal0/models (the 150 drift).
    monkeypatch.setattr(C, "model_mount_roots", lambda: ["/var/lib/hal0/models"])
    plan = _plan("/mnt/ai-models/qwopus3.5-4b-coder-mtp/model.gguf")
    sources = {m.source for m in plan.mounts}
    # The model file's own directory is mounted so the file is reachable...
    assert "/mnt/ai-models/qwopus3.5-4b-coder-mtp" in sources, sources
    # ...and the configured root is still mounted too.
    assert "/var/lib/hal0/models" in sources, sources


def test_model_under_configured_root_adds_no_extra_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A model already under a configured root needs no extra mount (no churn
    # for the common case — e.g. the brain slot on 150).
    monkeypatch.setattr(C, "model_mount_roots", lambda: ["/var/lib/hal0/models"])
    plan = _plan("/var/lib/hal0/models/chat/hal0-brain-sft-fpx8/model.gguf")
    sources = [m.source for m in plan.mounts]
    assert sources == ["/var/lib/hal0/models"], sources
