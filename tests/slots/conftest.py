"""Pytest fixtures and marker registration for the slots subtree.

v0.2 (PR-10): SlotManager dispatches every state change through
Lemonade. The fixtures here mock that boundary — there is no
systemctl-stub fixture any more.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.lemonade.client import LemonadeClient
from hal0.providers.lemonade import LemonadeProvider
from hal0.slots.manager import SlotManager


def pytest_configure(config: pytest.Config) -> None:
    """Register the integration marker so --strict-markers stays clean.

    The integration suite needs a real lemond daemon reachable on
    127.0.0.1:13305 and is intended for CI / release-gate runs only.
    """
    config.addinivalue_line(
        "markers",
        "integration: end-to-end slot lifecycle tests requiring a real lemond daemon on the host",
    )


# ── shared fixtures ─────────────────────────────────────────────────────────


def _mock_provider(handler) -> LemonadeProvider:
    """Build a LemonadeProvider backed by httpx.MockTransport."""
    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    return LemonadeProvider(client=LemonadeClient(http_client=transport))


@pytest.fixture
def lemonade_stub(monkeypatch: pytest.MonkeyPatch):
    """Replace the singleton LemonadeProvider with an httpx-mock-backed one.

    Tests pass the handler they want to drive Lemonade's HTTP responses
    with; the fixture installs the mock provider into
    ``hal0.providers._PROVIDERS`` so SlotManager's
    ``lemonade_provider()`` lookup finds it.

    Yields a factory: ``provider = lemonade_stub(handler)``.
    """
    import hal0.providers as providers_mod

    original = providers_mod._PROVIDERS["lemonade"]

    def _install(handler) -> LemonadeProvider:
        provider = _mock_provider(handler)
        providers_mod._PROVIDERS["lemonade"] = provider
        return provider

    yield _install

    providers_mod._PROVIDERS["lemonade"] = original


@pytest.fixture
def lemonade_loaded_stub(lemonade_stub):
    """Convenience: install a Lemonade stub that fakes a happy-path lifecycle.

    The default state advertises ``[{model_name: "qwen3-4b-q4_k_m"}]``
    in ``/v1/health.loaded[]`` — matches the model the ``slot_root``
    fixture writes into ``primary.toml``. Tests can mutate the
    returned ``state`` dict to drive eviction / drift scenarios.
    """
    state: dict[str, Any] = {
        "loaded": [{"model_name": "qwen3-4b-q4_k_m"}],
        "load_calls": [],
        "unload_calls": [],
    }

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        if req.url.path == "/v1/load":
            body = _json.loads(req.content.decode() or "{}")
            state["load_calls"].append(body)
            state["loaded"] = [{"model_name": body.get("model_name", "")}]
            return httpx.Response(200, json={"status": "loaded"})
        if req.url.path == "/v1/unload":
            body = _json.loads(req.content.decode() or "{}")
            state["unload_calls"].append(body)
            state["loaded"] = []
            return httpx.Response(200, json={"status": "unloaded"})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": state["loaded"]})
        return httpx.Response(404, json={"detail": f"unmocked {req.url.path}"})

    lemonade_stub(h)
    return state


@pytest.fixture
def slot_root(tmp_hal0_home: str) -> Path:
    """Yield the slots-config root and ensure a sample slot exists on disk."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "primary.toml").write_text(
        "\n".join(
            [
                'name = "primary"',
                "port = 8081",
                'backend = "vulkan"',
                'provider = "lemonade"',
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
__all__ = ["SlotManager", "lemonade_loaded_stub", "lemonade_stub", "slot_root"]
