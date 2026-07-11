"""hal0-memory Hermes plugin — REST client contract.

The shipped plugin (``installer/agents/hermes/plugins/hal0-memory/`` — the
canonical, only copy) is a package with a synchronous ``Hal0MemoryClient``
(``_client.py``) and a ``Hal0MemoryProvider`` (``provider.py``). It runs
inside the agent venv, so we load it from disk via :mod:`importlib` with a
stubbed upstream ``agent.memory_provider`` ABC.

Two contracts are pinned here:

1. **#317: never send a ``dataset`` field.** The server resolves the bank from
   the ``X-hal0-Agent`` + ``X-hal0-Private`` headers (PR #366). Sending an
   explicit ``private:<id>`` re-trips the ``_AGENT_ID_PATTERN`` reject.
2. **Two-bank routing.** ``private=True`` (default) sets ``X-hal0-Private: 1``
   (the hermes-private bank); ``private=False`` sets ``0`` (the shared bank).
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = REPO_ROOT / "installer" / "agents" / "hermes" / "plugins" / "hal0-memory"


@pytest.fixture
def hal0_memory_module():
    """Load the plugin *package* with a stubbed upstream ``MemoryProvider`` ABC.

    The plugin lives under ``installer/`` so it isn't normally importable, and
    it's a package (``from .provider import …`` / ``from ._client import …``),
    so we register it in ``sys.modules`` with ``submodule_search_locations``
    before exec so the relative imports resolve.
    """
    if "agent" not in sys.modules:
        pkg = types.ModuleType("agent")
        pkg.__path__ = []  # mark as namespace package
        sys.modules["agent"] = pkg
    if "agent.memory_provider" not in sys.modules:
        memprov_mod = types.ModuleType("agent.memory_provider")

        class _MemoryProvider:
            """Bare-bones ABC stub matching the upstream surface."""

            name = ""

            def initialize(self, *args, **kwargs): ...

            def system_prompt_block(self) -> str:
                return ""

            def prefetch(self, query: str, **kwargs):
                return ""

            def sync_turn(self, user: str, assistant: str, **kwargs): ...

            def get_tool_schemas(self):
                return []

            def handle_tool_call(self, name: str, args, **kwargs):
                return ""

            def shutdown(self): ...

        memprov_mod.MemoryProvider = _MemoryProvider
        sys.modules["agent.memory_provider"] = memprov_mod

    # Clean any prior load so each test gets a fresh package tree.
    for name in [
        n for n in sys.modules if n == "hal0_memory_plugin" or n.startswith("hal0_memory_plugin.")
    ]:
        del sys.modules[name]

    spec = importlib.util.spec_from_file_location(
        "hal0_memory_plugin",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["hal0_memory_plugin"] = module  # so `from .provider …` resolves
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self) -> dict:
        return {"status": "ok", "id": "mem_id"}


class _FakeHttpClient:
    """Records the last request the client issued; never hits the network."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def request(self, method, path, *, headers=None, json=None, params=None):
        self.calls.append(
            {"method": method, "path": path, "headers": headers, "json": json, "params": params}
        )
        return _FakeResponse()

    def close(self) -> None: ...


def _client_class():
    return importlib.import_module("hal0_memory_plugin._client").Hal0MemoryClient


def test_client_add_omits_dataset_and_sets_private_header(hal0_memory_module) -> None:
    """#317: ``add`` sends no ``dataset`` key; the private bank uses
    ``X-hal0-Private: 1``."""
    http = _FakeHttpClient()
    client = _client_class()(agent_id="hermes", http_client=http)

    client.add("a durable fact", tags=["chat"])

    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["path"] == "/api/memory/add"
    assert "dataset" not in call["json"], (
        f"add must omit dataset (server resolves from headers); got {call['json']}"
    )
    assert call["headers"]["X-hal0-Agent"] == "hermes"
    assert call["headers"]["X-hal0-Private"] == "1"


def test_client_add_shared_flips_private_header(hal0_memory_module) -> None:
    """``private=False`` routes the write to the shared bank."""
    http = _FakeHttpClient()
    client = _client_class()(agent_id="hermes", http_client=http)

    client.add("a shared fact", private=False)

    call = http.calls[0]
    assert "dataset" not in call["json"]
    assert call["headers"]["X-hal0-Private"] == "0"


def test_sync_turn_writes_private_with_chat_tags(hal0_memory_module) -> None:
    """``sync_turn`` persists the exchange to the hermes-private bank, tagged."""
    captured: dict = {}

    def _fake_add(text, *, tags=None, metadata=None, private=True):
        captured.update(text=text, tags=tags, private=private)
        return {"status": "ok", "id": "mem_id"}

    provider = hal0_memory_module.Hal0MemoryProvider()
    provider.initialize(session_id="s1")
    provider._client.add = _fake_add  # type: ignore[assignment]

    provider.sync_turn("hello", "world")

    assert "User: hello" in captured["text"]
    assert "Assistant: world" in captured["text"]
    assert "chat" in captured["tags"]
    assert captured["private"] is True


def test_sync_turn_skips_non_primary_contexts(hal0_memory_module) -> None:
    """cron / flush / subagent loops must not write to the user namespace."""
    calls: list = []

    def _fake_add(*a, **kw):
        calls.append((a, kw))
        return {"status": "ok"}

    provider = hal0_memory_module.Hal0MemoryProvider()
    provider.initialize(session_id="s1", agent_context="cron")
    provider._client.add = _fake_add  # type: ignore[assignment]

    provider.sync_turn("u", "a")

    assert calls == []


# ── Ported from the deleted memory_hindsight mirror ──────────────────────────
#
# The provider name / no-dataset-source / recall-route / prefetch-uses-recall
# / author-tag contracts used to be pinned against
# ``src/hal0/agents/hermes/plugins/memory_hindsight/`` (an async, tool-less
# fork that drifted out of sync and was deleted). These assertions still
# apply to the shipped installer copy, so they're ported here rather than
# lost.


def test_provider_name_is_hal0_memory(hal0_memory_module) -> None:
    assert hal0_memory_module.Hal0MemoryProvider().name == "hal0-memory"


def test_no_dataset_field_ever_sent(hal0_memory_module) -> None:
    src = inspect.getsource(_client_class().add)
    assert '"dataset"' not in src and "'dataset'" not in src


def test_client_recall_hits_recall_route(hal0_memory_module) -> None:
    http = _FakeHttpClient()
    client = _client_class()(agent_id="hermes", http_client=http)

    client.recall("what do I know", types=["observation", "world"], max_tokens=2048)

    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["method"] == "POST" and call["path"] == "/api/memory/recall"
    assert call["json"]["types"] == ["observation", "world"]
    assert "dataset" not in call["json"]


def test_prefetch_omits_types_to_inherit_server_default(hal0_memory_module) -> None:
    """prefetch() must not pin an explicit ``types`` list.

    The server's default recall mix (``_DEFAULT_RECALL_TYPES`` in
    ``hindsight_provider.py``) is world + experience + observation. An
    earlier version of this provider pinned ``["observation", "world"]``,
    silently dropping "experience" from prefetch context.
    """
    http = _FakeHttpClient()
    provider = hal0_memory_module.Hal0MemoryProvider(
        client=_client_class()(agent_id="hermes", http_client=http)
    )
    provider.initialize(session_id="s1")

    provider.prefetch("what do I know")

    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["path"] == "/api/memory/recall"
    assert "types" not in call["json"]


def test_prefetch_uses_recall_not_search(hal0_memory_module) -> None:
    src = inspect.getsource(hal0_memory_module.Hal0MemoryProvider.prefetch)
    assert ".recall(" in src and ".search(" not in src


def test_writes_stamp_the_author_tag(hal0_memory_module) -> None:
    # Convention: tag the author (agent:<id>), bank the scope. Hermes's
    # automatic writes must carry agent:hermes so they're filterable by
    # author without giving each author its own bank.
    for fn in (
        hal0_memory_module.Hal0MemoryProvider.sync_turn,
        hal0_memory_module.Hal0MemoryProvider.on_memory_write,
    ):
        assert '"agent:hermes"' in inspect.getsource(fn)


def test_list_and_delete_hit_documented_routes(hal0_memory_module) -> None:
    """Lock the client's list/delete paths against the real router.

    The router only serves GET /api/memory/list and POST /api/memory/delete
    — a client calling GET /api/memory or DELETE /api/memory/{id} would 404.
    """
    from hal0.api.routes.memory import router

    http = _FakeHttpClient()
    client = _client_class()(agent_id="hermes", http_client=http)

    client.list_items(limit=10)
    client.delete("some-id")

    router_paths = set()
    for route in router.routes:
        for method in getattr(route, "methods", set()):
            router_paths.add((method, f"/api/memory{route.path}"))
    assert ("GET", "/api/memory/list") in router_paths, f"router paths: {router_paths}"
    assert ("POST", "/api/memory/delete") in router_paths, f"router paths: {router_paths}"

    seen = {(c["method"], c["path"]) for c in http.calls}
    assert ("GET", "/api/memory/list") in seen, f"seen: {seen}"
    assert ("POST", "/api/memory/delete") in seen, f"seen: {seen}"
