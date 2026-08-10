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
from tests.fixtures.slot_probe import probe_slot_name


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
      * ``unit_failure_by_slot`` — systemd's verdict per slot (#1791). A
        non-empty string is the manager's PROOF that systemd gave up on the
        unit (crash loop / ``start-limit-hit`` / unit gone); the default empty
        string is the "unit is fine or the probe was inconclusive" answer that
        every pre-#1791 test implicitly relied on.
    """

    def __init__(self) -> None:
        self.active: set[str] = set()
        self.load_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.unload_calls: list[dict[str, Any]] = []
        self.fail_load: Exception | None = None
        self.running_argv_by_slot: dict[str, list[str] | None] = {}
        self.expected_argv_by_slot: dict[str, list[str] | None] = {}
        # /health probe result. Default True: an active unit is also ready.
        # Set False to simulate a still-loading / wedged model server (unit
        # active but the inference server isn't answering /health yet).
        self.healthy: bool = True
        # #1791 — systemd's terminal verdict per slot, and the reset-failed
        # calls the manager/provider made. Empty by default so every existing
        # test keeps the old "nothing is wrong with the unit" behaviour.
        self.unit_failure_by_slot: dict[str, str] = {}
        self.reset_failed_calls: list[str] = []

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

    def is_active(self, slot: Any) -> bool:
        return probe_slot_name(slot) in self.active

    async def wait_ready(self, port: int, timeout_s: float | None = None) -> None:
        return None

    def unit_failure_reason(self, slot: Any) -> str:
        """Mirror ``ContainerProvider.unit_failure_reason`` (#1791)."""
        return self.unit_failure_by_slot.get(probe_slot_name(slot), "")

    def reset_failed(self, slot: Any) -> None:
        """Mirror ``ContainerProvider.reset_failed`` (#1424/#1791)."""
        self.reset_failed_calls.append(probe_slot_name(slot))

    async def health(self, port: int, slot_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        # Mirrors ContainerProvider.health's (port, slot_cfg) signature —
        # the manager passes the slot config so FLM slots get the Tier-1
        # real-inference probe in production.
        return {"ok": self.healthy}

    # — slot_view container_enrichment extras —

    def running_image(self, slot: Any) -> str | None:
        return None

    def running_argv(self, slot: Any) -> list[str] | None:
        return self.running_argv_by_slot.get(probe_slot_name(slot))

    def expected_argv(
        self, slot_cfg: dict[str, Any], model_info: dict[str, Any]
    ) -> list[str] | None:
        return self.expected_argv_by_slot.get(str(slot_cfg.get("name")))

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
