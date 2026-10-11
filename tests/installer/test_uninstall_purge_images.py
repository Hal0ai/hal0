"""``uninstall.sh --purge`` removes every runner image the release pins.

The purge matches ``podman images`` lines (``repo:tag``) against one shell
``case`` pattern. This test lifts that pattern out of the script and runs it
in bash against each pinned ref, so a runner added to ``manifest.json`` under
a new namespace (the external ``cuda`` image) cannot be missed again.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from hal0.runners.pins import load_runner_pins

_REPO = Path(__file__).resolve().parents[2]
_UNINSTALL = _REPO / "installer" / "uninstall.sh"


def _purge_pattern() -> str:
    text = _UNINSTALL.read_text(encoding="utf-8")
    match = re.search(r"^\s+(\*open-webui\*\|[^\n]+)\)\s*$", text, re.M)
    assert match, "purge case pattern not found in uninstall.sh"
    return match.group(1)


def _purged(image: str) -> bool:
    script = f'case "$1" in {_purge_pattern()}) exit 0 ;; *) exit 1 ;; esac'
    return subprocess.run(["bash", "-c", script, "_", image], check=False).returncode == 0


@pytest.mark.parametrize("key", sorted(load_runner_pins(_REPO / "manifest.json")))
def test_every_pinned_runner_image_is_purged(key: str) -> None:
    pin = load_runner_pins(_REPO / "manifest.json")[key]
    if not pin.tag:
        pytest.skip("unpublished pin")
    # podman images --format '{{.Repository}}:{{.Tag}}'
    assert _purged(f"{pin.image}:{pin.tag}")


def test_an_operators_own_llama_cpp_image_is_kept() -> None:
    assert not _purged("ghcr.io/ggml-org/llama.cpp:server-vulkan")
    assert not _purged("docker.io/library/alpine:latest")
