"""The ``hal0_gpu_gate`` asset, loaded the way the tests of its consumers need it.

The asset is copied into ComfyUI's ``custom_nodes`` and cannot import hal0, so a
test that wants what the node itself produces (the 503 body of a refused
prompt) loads the file by path. Importing it outside ComfyUI is harmless: its
installer fails soft.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

GATE_ASSET = (
    Path(__file__).resolve().parents[2] / "src" / "hal0" / "comfyui" / "assets" / "hal0_gpu_gate.py"
)


def load_gate_asset() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hal0_gpu_gate_fixture", GATE_ASSET)
    assert spec is not None and spec.loader is not None
    node = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(node)
    return node


def refused_prompt_body(reason: str) -> dict[str, Any]:
    """What the gate answers ``/prompt`` with when it refuses a prompt for *reason*."""
    body: dict[str, Any] = load_gate_asset().refused_prompt_body(reason)
    return body
