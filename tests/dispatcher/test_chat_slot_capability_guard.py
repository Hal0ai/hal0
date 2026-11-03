"""#2672: /v1/embeddings and /v1/rerank naming a plain chat slot get a typed 409.

The reverse of #1894 (chat request naming an embed slot). Without the guard the
request is forwarded to the chat llama-server, which answers with a raw 501
("Start it with ``--embeddings``") that a hal0 user cannot act on.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from hal0.dispatcher._capability_resolve import guard_chat_slot_capability
from hal0.dispatcher.router import Dispatcher
from hal0.errors import CapabilityMismatch
from hal0.upstreams.registry import Upstream, UpstreamRegistry

EMBED_PATH = "/v1/embeddings"
RERANK_PATH = "/v1/rerank"


def _chat_cfg(**extra: Any) -> dict[str, Any]:
    return {"name": "utility", "type": "llm", "provider": "llama-server", **extra}


# ── pure guard ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [EMBED_PATH, RERANK_PATH, "/v1/rerankings"])
def test_plain_chat_slot_is_rejected(path: str) -> None:
    with pytest.raises(CapabilityMismatch) as exc:
        guard_chat_slot_capability(_chat_cfg(), path, model="utility", resolution_path="x")
    assert exc.value.code == "dispatch.capability_mismatch"
    assert exc.value.status == 409
    assert exc.value.details["slot"] == "utility"
    assert exc.value.details["requires"] == ("reranking" if "rerank" in path else "embedding")
    assert "--embeddings" not in exc.value.message


def test_chat_slot_with_embedding_flag_serves_embeddings() -> None:
    guard_chat_slot_capability(_chat_cfg(flags="--embedding -fa auto"), EMBED_PATH)
    guard_chat_slot_capability(_chat_cfg(argv=["--embeddings"]), EMBED_PATH)


def test_embedding_flag_alone_does_not_serve_rerank() -> None:
    with pytest.raises(CapabilityMismatch):
        guard_chat_slot_capability(_chat_cfg(flags="--embedding"), RERANK_PATH)


def test_reranking_flag_serves_rerank_and_embeddings() -> None:
    cfg = _chat_cfg(flags="--reranking -fa auto")
    guard_chat_slot_capability(cfg, RERANK_PATH)
    guard_chat_slot_capability(cfg, EMBED_PATH)


def test_profile_carrying_the_mode_flag_passes() -> None:
    # Seeded ``embedding`` profile resolves to ``--embedding ...`` flags.
    guard_chat_slot_capability(_chat_cfg(profile="embedding"), EMBED_PATH)


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        {},
        {"name": "embed", "type": "embedding"},
        {"name": "flm", "type": "llm", "provider": "flm"},
        {"name": "img", "type": "image"},
    ],
)
def test_non_chat_or_unknown_slots_are_a_noop(cfg: dict[str, Any] | None) -> None:
    guard_chat_slot_capability(cfg, EMBED_PATH)
    guard_chat_slot_capability(cfg, RERANK_PATH)


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/completions", "/v1/models"])
def test_chat_shaped_paths_are_untouched(path: str) -> None:
    guard_chat_slot_capability(_chat_cfg(), path)


# ── dispatch choke point ──────────────────────────────────────────────────────


class _Registry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._fake = {u.name: u for u in upstreams}

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._fake.get(name)

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._fake.values())


class _SlotManager:
    def __init__(self, configs: list[dict[str, Any]] | Exception) -> None:
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        if isinstance(self._configs, Exception):
            raise self._configs
        return self._configs


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "app": None,
        }
    )


def _dispatcher(configs: list[dict[str, Any]] | Exception) -> Dispatcher:
    chat = Upstream(
        name="utility", kind="slot", url="http://127.0.0.1:8090/v1", slot_name="utility"
    )
    return Dispatcher(
        upstream_registry=_Registry([chat]),
        cached_models=lambda name: ["utility"] if name == "utility" else [],
        slot_manager=_SlotManager(configs),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [EMBED_PATH, RERANK_PATH])
async def test_dispatch_embeddings_or_rerank_naming_chat_slot_raises_typed_mismatch(
    path: str,
) -> None:
    dispatcher = _dispatcher([_chat_cfg()])
    with pytest.raises(CapabilityMismatch) as exc:
        await dispatcher.dispatch(_request(path), body={"model": "utility", "input": "x"})
    assert exc.value.details["slot"] == "utility"
    assert exc.value.details["model"] == "utility"


@pytest.mark.asyncio
async def test_dispatch_chat_slot_with_embedding_flag_still_routes() -> None:
    dispatcher = _dispatcher([_chat_cfg(flags="--embedding")])
    call = await dispatcher.dispatch(_request(EMBED_PATH), body={"model": "utility", "input": "x"})
    assert call.slot_name == "utility"


@pytest.mark.asyncio
async def test_dispatch_inventory_failure_does_not_block_the_request() -> None:
    dispatcher = _dispatcher(RuntimeError("toml unreadable"))
    call = await dispatcher.dispatch(_request(EMBED_PATH), body={"model": "utility", "input": "x"})
    assert call.slot_name == "utility"
