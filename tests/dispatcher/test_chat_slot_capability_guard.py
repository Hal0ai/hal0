"""#2672: /v1/embeddings and /v1/rerank naming a plain chat slot get a typed 409.

The reverse of #1894 (chat request naming an embed slot). Without the guard the
request is forwarded to the chat llama-server, which answers with a raw 501
("Start it with ``--embeddings``") that a hal0 user cannot act on.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from hal0.dispatcher._capability_resolve import (
    guard_capability_mismatch,
    guard_chat_slot_capability,
)
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


def test_shipped_flm_toml_is_a_noop() -> None:
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    raw = tomllib.loads((root / "installer/etc-hal0/slots/flm.toml").read_text())
    cfg = {**raw, **(raw.get("slot") or {})}
    guard_chat_slot_capability(cfg, EMBED_PATH, model="flm")


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        {},
        {"name": "flm", "type": "llm", "provider": "flm"},
        # Shipped installer/etc-hal0/slots/flm.toml shape: no ``provider``.
        {"name": "flm", "type": "llm", "device": "npu", "profile": "flm"},
        # A slot type outside the capability table is not evidence either way.
        {"name": "odd", "type": "something-new"},
    ],
)
def test_unknown_or_npu_slots_are_a_noop(cfg: dict[str, Any] | None) -> None:
    guard_chat_slot_capability(cfg, EMBED_PATH)
    guard_chat_slot_capability(cfg, RERANK_PATH)


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/completions", "/v1/models"])
def test_chat_shaped_paths_are_untouched(path: str) -> None:
    guard_chat_slot_capability(_chat_cfg(), path)


# ── #3807: non-llm capability slots, keyed on type ────────────────────────────


def test_embedding_slot_rejects_rerank() -> None:
    """Case B: /v1/rerank landing on the embed slot (no --reranking) is a 409."""
    with pytest.raises(CapabilityMismatch) as exc:
        guard_chat_slot_capability(
            {"name": "embed", "type": "embedding"},
            RERANK_PATH,
            model="nomic-embed-text-v1.5-q8_0",
            resolution_path="passthrough:embed",
        )
    assert exc.value.status == 409
    assert exc.value.details["slot"] == "embed"
    assert exc.value.details["slot_type"] == "embedding"
    assert exc.value.details["requires"] == "reranking"
    assert "--reranking" not in exc.value.message


def test_embedding_slot_serves_embeddings() -> None:
    guard_chat_slot_capability({"name": "embed", "type": "embedding"}, EMBED_PATH)


def test_embedding_typed_slot_with_reranking_flag_serves_rerank() -> None:
    guard_chat_slot_capability(
        {"name": "embed2", "type": "embedding", "flags": "--reranking"}, RERANK_PATH
    )


@pytest.mark.parametrize("name", ["rerank", "zzrr"])
def test_reranking_slot_serves_rerank_and_embeddings(name: str) -> None:
    cfg = {"name": name, "type": "reranking"}
    guard_chat_slot_capability(cfg, RERANK_PATH)
    guard_chat_slot_capability(cfg, "/v1/rerankings")
    guard_chat_slot_capability(cfg, EMBED_PATH)


@pytest.mark.parametrize(
    "cfg",
    [{"name": "tts", "type": "tts"}, {"name": "img", "type": "image"}],
)
@pytest.mark.parametrize("path", [EMBED_PATH, RERANK_PATH])
def test_tts_and_image_slots_reject_embeddings_and_rerank(cfg: dict[str, Any], path: str) -> None:
    with pytest.raises(CapabilityMismatch):
        guard_chat_slot_capability(cfg, path)


@pytest.mark.parametrize(
    ("slot", "slot_type"),
    [
        ("zzrr", "reranking"),
        ("my-embedder", "embedding"),
        ("voice2", "tts"),
        ("pics", "image"),
        ("ears", "transcription"),
    ],
)
@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/completions"])
def test_custom_named_capability_slot_rejects_chat(slot: str, slot_type: str, path: str) -> None:
    """Case A: the chat-direction gate keys on the slot's type, not its name."""
    with pytest.raises(CapabilityMismatch) as exc:
        guard_capability_mismatch(slot, path, slot_type=slot_type, model=slot)
    assert exc.value.status == 409
    assert exc.value.details["slot"] == slot
    assert exc.value.details["slot_type"] == slot_type


def test_llm_typed_slot_is_never_chat_gated_whatever_its_name() -> None:
    # Type wins over the stock-name table: an llm slot an operator named
    # "embed" serves chat.
    guard_capability_mismatch("embed", "/v1/chat/completions", slot_type="llm")


def test_name_table_still_applies_when_the_type_is_unknown() -> None:
    # No slot config available (no slot manager, inventory failure): the
    # stock role names keep the #1894 gate.
    with pytest.raises(CapabilityMismatch):
        guard_capability_mismatch("embed", "/v1/chat/completions")


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


def _slot(name: str, port: int) -> Upstream:
    return Upstream(name=name, kind="slot", url=f"http://127.0.0.1:{port}/v1", slot_name=name)


def _dispatcher(
    configs: list[dict[str, Any]] | Exception,
    upstreams: list[Upstream] | None = None,
    advertised: dict[str, list[str]] | None = None,
) -> Dispatcher:
    ups = upstreams if upstreams is not None else [_slot("utility", 8090)]
    adv = advertised if advertised is not None else {"utility": ["utility"]}
    return Dispatcher(
        upstream_registry=_Registry(ups),
        cached_models=lambda name: adv.get(name, []),
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


# ── #3807 through dispatch ────────────────────────────────────────────────────

_EMBED_MODEL = "nomic-embed-text-v1.5-q8_0"
_RERANK_MODEL = "bge-reranker-v2-m3-q4_k_m"


def _stock_capability_dispatcher(extra: list[dict[str, Any]] | None = None) -> Dispatcher:
    configs = [
        {"name": "embed", "type": "embedding"},
        {"name": "rerank", "type": "reranking"},
        *(extra or []),
    ]
    ups = [_slot("embed", 8082), _slot("rerank", 8083)]
    adv = {"embed": [_EMBED_MODEL], "rerank": [_RERANK_MODEL]}
    for cfg in extra or []:
        ups.append(_slot(cfg["name"], 8199))
        adv[cfg["name"]] = [_RERANK_MODEL]
    return _dispatcher(configs, ups, adv)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [RERANK_PATH, "/v1/rerankings"])
async def test_dispatch_rerank_naming_the_embed_model_raises_typed_mismatch(path: str) -> None:
    """Case B: the stock embed slot resolves by passthrough and cannot rerank."""
    dispatcher = _stock_capability_dispatcher()
    with pytest.raises(CapabilityMismatch) as exc:
        await dispatcher.dispatch(
            _request(path), body={"model": _EMBED_MODEL, "query": "q", "documents": ["a"]}
        )
    assert exc.value.details["slot"] == "embed"
    assert exc.value.details["resolution_path"] == "passthrough:embed"


@pytest.mark.asyncio
async def test_dispatch_rerank_naming_embed_slot_still_routes_to_rerank() -> None:
    """Control from the issue: model=embed on /v1/rerank goes to legacy_slot:rerank."""
    dispatcher = _stock_capability_dispatcher()
    call = await dispatcher.dispatch(
        _request(RERANK_PATH), body={"model": "embed", "query": "q", "documents": ["a"]}
    )
    assert call.slot_name == "rerank"
    assert call.resolution_path == "legacy_slot:rerank"


@pytest.mark.asyncio
async def test_dispatch_embeddings_naming_the_embed_model_still_routes() -> None:
    dispatcher = _stock_capability_dispatcher()
    call = await dispatcher.dispatch(
        _request(EMBED_PATH), body={"model": _EMBED_MODEL, "input": "x"}
    )
    assert call.slot_name == "embed"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/completions"])
async def test_dispatch_chat_naming_custom_reranking_slot_raises_typed_mismatch(
    path: str,
) -> None:
    """Case A: a user-created ``--type reranking`` slot with a non-stock name."""
    dispatcher = _stock_capability_dispatcher([{"name": "zzrr", "type": "reranking"}])
    with pytest.raises(CapabilityMismatch) as exc:
        await dispatcher.dispatch(
            _request(path), body={"model": "zzrr", "messages": [{"role": "user", "content": "hi"}]}
        )
    assert exc.value.details["slot"] == "zzrr"
    assert exc.value.details["slot_type"] == "reranking"


@pytest.mark.asyncio
async def test_dispatch_rerank_to_custom_reranking_slot_routes() -> None:
    dispatcher = _stock_capability_dispatcher([{"name": "zzrr", "type": "reranking"}])
    # Advertised on both rerank slots; whichever resolves must pass the gate.
    call = await dispatcher.dispatch(
        _request(RERANK_PATH), body={"model": _RERANK_MODEL, "query": "q", "documents": ["a"]}
    )
    assert call.slot_name in {"rerank", "zzrr"}
