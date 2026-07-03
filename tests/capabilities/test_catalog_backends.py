"""SC-2 — the NPU picker probe must honour a podman-only runtime.

``available_backends`` gates the NPU/FLM backend on
:func:`hal0.capabilities.catalog._flm_image_present`, which checks whether
the FLM toolbox image is already pulled locally. It used to shell a
hard-coded ``docker image inspect``; on a podman-only host (the default
install) that ``docker`` binary is absent, so the probe always returned
False and the NPU backend was silently dropped even when the image was
present under podman.

These tests pin that the probe resolves the runtime through the shared
``hal0.providers.container._container_runtime`` path (podman → docker →
raise) rather than a literal ``docker`` argv, and that the result is
cached so repeated ``/api/capabilities`` GETs don't re-spawn the probe.
"""

from __future__ import annotations

import subprocess
import types
from typing import Any

import pytest

from hal0.capabilities import catalog


def _npu_only_hw() -> Any:
    """A HardwareInfo-shaped stub: NPU present, no GPUs."""
    return types.SimpleNamespace(
        npu=types.SimpleNamespace(present=True),
        gpus=[],
    )


@pytest.fixture(autouse=True)
def _reset_probe_cache() -> Any:
    """Ensure each test starts and ends with a clean image-present cache."""
    catalog.reset_flm_image_present_cache()
    yield
    catalog.reset_flm_image_present_cache()


def test_npu_advertised_under_podman_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """NPU is advertised when podman (not docker) reports the image present."""
    monkeypatch.setenv("HAL0_CONTAINER_RUNTIME", "podman")
    monkeypatch.setattr(catalog, "load_hardware_info", _npu_only_hw)

    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    backends = catalog.available_backends()

    assert backends[0]["id"] == "npu", f"NPU not advertised on podman-only host: {backends!r}"
    assert calls, "the image-present probe never ran"
    assert calls[0][0] == "podman", f"probe argv[0] must be the podman binary: {calls[0]!r}"
    assert calls[0][0] != "docker"


def test_npu_hidden_when_image_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime resolves but the image inspect fails → no NPU backend."""
    monkeypatch.setenv("HAL0_CONTAINER_RUNTIME", "podman")
    monkeypatch.setattr(catalog, "load_hardware_info", _npu_only_hw)

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        return types.SimpleNamespace(returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    backends = catalog.available_backends()
    ids = [b["id"] for b in backends]
    assert "npu" not in ids, f"NPU advertised despite missing image: {backends!r}"


def test_flm_probe_not_hardcoded_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolved argv never equals literal ``docker`` under podman, and the
    probe is cached across repeated ``available_backends`` calls."""
    monkeypatch.setenv("HAL0_CONTAINER_RUNTIME", "podman")
    monkeypatch.setattr(catalog, "load_hardware_info", _npu_only_hw)

    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    catalog.available_backends()
    catalog.available_backends()

    assert calls, "the image-present probe never ran"
    assert all(argv[0] != "docker" for argv in calls), f"probe hard-coded docker: {calls!r}"
    assert len(calls) == 1, f"probe re-ran instead of using the cache: {calls!r}"
