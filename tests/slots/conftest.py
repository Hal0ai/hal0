"""Pytest fixtures and marker registration for the slots subtree.

Phase E (#687): SlotManager dispatches every state change through
ContainerProvider (podman systemd units). The fixtures here mock that
boundary with an in-memory provider double.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.slots.manager import SlotManager


def pytest_configure(config: pytest.Config) -> None:
    """Register the integration marker so --strict-markers stays clean.

    The integration suite needs real podman + systemd on the host and is
    intended for CI / release-gate runs only.
    """
    config.addinivalue_line(
        "markers",
        "integration: end-to-end slot lifecycle tests requiring real podman/systemd on the host",
    )


# ── shared fixtures ─────────────────────────────────────────────────────────


class FakeContainerProvider:
    """In-memory ContainerProvider double for SlotManager dispatch tests.

    Mirrors the surface SlotManager touches: ``load_sync`` /
    ``unload_sync`` (executor-run sync calls), ``is_active`` (systemctl
    probe), and the async ``wait_ready`` / ``health`` readiness probes.

    State is mutable so tests can drive drift scenarios:
      * ``active`` — set of slot names whose unit is "running". Clear or
        ``discard()`` an entry to simulate the unit stopping out-of-band.
      * ``load_calls`` / ``unload_calls`` — recorded dispatches.
      * ``fail_load`` — when set, ``load_sync`` raises it (spawn failure).
    """

    def __init__(self) -> None:
        self.active: set[str] = set()
        self.load_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.unload_calls: list[dict[str, Any]] = []
        self.fail_load: Exception | None = None
        # /health probe result. Default True: an active unit is also ready.
        # Set False to simulate a still-loading / wedged model server (unit
        # active but the inference server isn't answering /health yet).
        self.healthy: bool = True

    # — SlotManager._spawn_locked / terminate (sync, executor-run) —

    def load_sync(self, cfg: dict[str, Any], model_info: dict[str, Any]) -> None:
        if self.fail_load is not None:
            raise self.fail_load
        self.load_calls.append((dict(cfg), dict(model_info)))
        self.active.add(str(cfg.get("name")))

    def unload_sync(self, cfg: dict[str, Any]) -> None:
        self.unload_calls.append(dict(cfg))
        self.active.discard(str(cfg.get("name")))

    # — probes —

    def is_active(self, slot_name: str) -> bool:
        return slot_name in self.active

    async def wait_ready(self, port: int, timeout_s: float | None = None) -> None:
        return None

    async def health(self, port: int) -> dict[str, Any]:
        return {"ok": self.healthy}

    # — slot_view container_enrichment extras —

    def running_image(self, slot_name: str) -> str | None:
        return None

    def image_present(self, image: str) -> bool:
        return False


@pytest.fixture
def container_stub(monkeypatch: pytest.MonkeyPatch) -> FakeContainerProvider:
    """Replace the process-wide ContainerProvider with the in-memory fake.

    SlotManager imports ``container_provider`` lazily from
    ``hal0.providers.container`` inside each method, so patching the
    module attribute covers every dispatch site.
    """
    fake = FakeContainerProvider()
    monkeypatch.setattr(
        "hal0.providers.container.container_provider",
        lambda: fake,
    )
    return fake


@pytest.fixture
def slot_root(tmp_hal0_home: str) -> Path:
    """Yield the slots-config root and ensure a sample slot exists on disk."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


# Keep ``SlotManager`` importable from this conftest so tests that
# reach into the module-level namespace (e.g. monkeypatching) don't
# have to re-import. Tests use it via the fixture above; the public
# symbol is exported for ergonomics.
__all__ = ["FakeContainerProvider", "SlotManager", "container_stub", "slot_root"]
