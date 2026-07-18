"""Tests for the /api/models surface added in the v3 wireup.

Covers two pieces:
  * ``_derive_ns`` — the locked path-shape rule for blessed vs pulled
    (see issue #220 + the v3 brief). Three cases: blessed recipe path,
    pulled path under the model root, and the empty-path edge.
  * ``POST /api/models/inspect`` — HuggingFace metadata + tree fetch with
    httpx mocked. Validates the variant filter (.gguf only, LFS-size
    preferred), the alias body shape, the 502/404 error envelopes, and
    the 5 minute in-process cache.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes import models as models_route
from hal0.registry.model import Model, _derive_ns

# ── _derive_ns ─────────────────────────────────────────────────────────────


def test_derive_ns_blessed_for_recipe_capability_path() -> None:
    """Path under /var/lib/hal0/models/<recipe>/<capability>/ → blessed."""
    m = Model(
        id="qwen3-coder",
        path="/var/lib/hal0/models/qwen3-coder/chat/qwen3-coder-q4_k_m.gguf",
    )
    assert _derive_ns(m) == "blessed"


def test_derive_ns_pulled_for_id_only_path() -> None:
    """Default pull layout /var/lib/hal0/models/<id>/<file> → pulled."""
    m = Model(
        id="hand-pulled",
        path="/var/lib/hal0/models/hand-pulled/hand-pulled-q4_k_m.gguf",
    )
    assert _derive_ns(m) == "pulled"


def test_derive_ns_empty_path_is_pulled() -> None:
    """Edge case: a Model with an unset/whitespace path must not raise."""
    # pydantic forbids an empty path; the helper still has to tolerate
    # a Model-shaped object whose path was wiped post-construction (the
    # serialisation path runs after registry mutations and we don't
    # want a single bad row to crash the whole listing).
    m = Model(id="ghost", path="/tmp/will-be-cleared")
    object.__setattr__(m, "path", "")
    assert _derive_ns(m) == "pulled"


def test_derive_ns_blessed_root_with_only_id_segment_is_pulled() -> None:
    """Only one path segment after the blessed root → not blessed.

    The rule requires <recipe>/<capability>/<file> — anything shorter
    is the legacy single-segment pull layout.
    """
    m = Model(id="x", path="/var/lib/hal0/models/x/file.gguf")
    assert _derive_ns(m) == "pulled"


def test_derive_ns_arbitrary_root_is_pulled() -> None:
    """A path outside the blessed root is always pulled."""
    m = Model(id="ext", path="/mnt/ai-models/qwen/qwen3-8b/q4.gguf")
    assert _derive_ns(m) == "pulled"


# ── /api/models response shape ─────────────────────────────────────────────


@pytest.fixture
def inspect_app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app with the inspect cache cleared so each test sees a cold cache."""
    models_route._INSPECT_CACHE.clear()
    return create_app()


@pytest.fixture
def inspect_client(inspect_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(inspect_app) as c:
        yield c


def test_list_models_attaches_ns_for_registry_entries(
    inspect_client: TestClient,
    tmp_hal0_home: str,
) -> None:
    """Local registry rows must carry the derived ``ns`` field."""
    fpath = Path(tmp_hal0_home) / "fixture.gguf"
    fpath.write_bytes(b"\x00" * 8)
    # Register two rows: one whose path looks blessed, one whose doesn't.
    inspect_client.post(
        "/api/models",
        json={
            "id": "blessed-row",
            "path": "/var/lib/hal0/models/qwen3-coder/chat/qwen3-coder.gguf",
        },
    )
    inspect_client.post(
        "/api/models",
        json={"id": "pulled-row", "path": str(fpath)},
    )

    body = inspect_client.get("/api/models").json()
    rows = {m["id"]: m for m in body["models"]}
    assert rows["blessed-row"]["ns"] == "blessed"
    assert rows["pulled-row"]["ns"] == "pulled"


def test_get_model_attaches_ns(inspect_client: TestClient, tmp_hal0_home: str) -> None:
    """GET /api/models/{id} carries the same ``ns`` derivation."""
    fpath = Path(tmp_hal0_home) / "x.gguf"
    fpath.write_bytes(b"\x00")
    inspect_client.post("/api/models", json={"id": "g1", "path": str(fpath)})
    row = inspect_client.get("/api/models/g1").json()
    assert row["ns"] == "pulled"


def test_list_models_type_is_dispatcher_vocab_for_local_rows(
    inspect_client: TestClient,
    tmp_hal0_home: str,
) -> None:
    """Local registry rows expose ``type`` in the DISPATCHER vocabulary
    (llm/embedding/reranking), NOT ``classify()``'s modality bucket
    (chat/embed/rerank). The UI joins slots↔models on ``model.type ===
    slot.type`` (dispatcher vocab), so a modality value hides every model from
    the slot pickers — the 0.9.1 regression where local rows leaked "chat"
    (line-219 stamp) collapsed every slot's model dropdown to its current
    default. The FLM path was already correct; only the local/upstream stamps
    leaked modality, so this guards the local path specifically."""
    cases = (
        ("chatty", ["chat"], "llm"),
        ("visionary", ["chat", "vision"], "llm"),  # multimodal → still llm
        ("ranker", ["rerank"], "reranking"),
        ("embedder", ["embed"], "embedding"),
    )
    for mid, caps, _want in cases:
        fp = Path(tmp_hal0_home) / f"{mid}.gguf"
        fp.write_bytes(b"\x00" * 8)
        inspect_client.post("/api/models", json={"id": mid, "path": str(fp)})
        inspect_client.put(f"/api/models/{mid}", json={"capabilities": caps})

    rows = {m["id"]: m for m in inspect_client.get("/api/models").json()["models"]}
    for mid, _caps, want in cases:
        assert rows[mid]["type"] == want, f"{mid}: {rows[mid]['type']!r} != {want!r}"


# ── POST /api/models/inspect ───────────────────────────────────────────────


def _hf_handler(
    *,
    meta_status: int = 200,
    tree_status: int = 200,
    meta_body: dict[str, Any] | None = None,
    tree_body: list[dict[str, Any]] | None = None,
    fail_with: type[Exception] | None = None,
):
    """Build an httpx MockTransport handler for the inspect tests."""

    def handler(req: httpx.Request) -> httpx.Response:
        if fail_with is not None:
            raise fail_with("simulated transport failure")
        path = req.url.path
        if path.endswith("/tree/main"):
            return httpx.Response(tree_status, json=tree_body or [])
        # Meta endpoint
        return httpx.Response(meta_status, json=meta_body or {})

    return handler


def _patch_httpx_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Patch ``httpx.AsyncClient`` so the inspect route uses our mock transport.

    The route constructs its own ``AsyncClient`` for the HF fetch. We
    intercept by replacing the class with a thin wrapper that injects
    ``transport=MockTransport(handler)``.
    """
    real_cls = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr("hal0.upstreams.huggingface.httpx.AsyncClient", factory)


def test_inspect_returns_gguf_variants_sorted_by_size(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route surfaces .gguf entries with LFS size + sorts ascending."""
    tree = [
        {"path": "README.md", "size": 4096},
        {
            "path": "qwen3-8b-q4_k_m.gguf",
            "lfs": {"size": 4_900_000_000},
            "size": 132,
        },
        {
            "path": "qwen3-8b-q8_0.gguf",
            "lfs": {"size": 8_500_000_000},
            "size": 132,
        },
        {"path": "tokenizer.json", "size": 1024},
    ]
    meta = {
        "tags": ["text-generation", "gguf"],
        "cardData": {"license": "apache-2.0", "description": "Hello world."},
    }
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body=meta, tree_body=tree),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "unsloth/Qwen3-8B-GGUF"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repo"] == "unsloth/Qwen3-8B-GGUF"
    ids = [v["id"] for v in body["variants"]]
    assert ids == ["qwen3-8b-q4_k_m.gguf", "qwen3-8b-q8_0.gguf"]
    assert body["variants"][0]["size_bytes"] == 4_900_000_000
    assert "gguf" in body["tags"]
    assert body["metadata"]["license"] == "apache-2.0"
    assert "Hello world" in body["metadata"]["readme_excerpt"]


def test_inspect_surfaces_bare_mmproj_sidecar(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``.mmproj`` sidecar (no ``.gguf`` suffix) must appear as a variant.

    Regression: repos like ``jcbtc/chadrock-35b-…`` ship the projector as
    ``mmproj-…-F32.mmproj``. The inspect filter used to admit only ``.gguf``,
    so the Add-by-HF modal's vision picker showed "no mmproj files in repo"
    and vision pulls silently shipped without a projector.
    """
    tree = [
        {
            "path": "CHADROCK-35B-MoEQuality-7.07BPW.gguf",
            "lfs": {"size": 31_410_670_848},
            "size": 136,
        },
        {
            "path": "mmproj-CHADROCK-35B-Ace-Saber-F32.mmproj",
            "lfs": {"size": 902_821_824},
            "size": 134,
        },
        {"path": "README.md", "size": 19037},
    ]
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=tree),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "jcbtc/chadrock-35b-ace-saber-rocmfp4-mtp"},
    )
    assert r.status_code == 200, r.text
    variants = {v["id"]: v for v in r.json()["variants"]}
    # Both the main quant and the bare .mmproj sidecar are surfaced.
    assert "CHADROCK-35B-MoEQuality-7.07BPW.gguf" in variants
    assert "mmproj-CHADROCK-35B-Ace-Saber-F32.mmproj" in variants
    # The sidecar is labelled so the modal (and operator) can tell it apart.
    assert "mmproj" in variants["mmproj-CHADROCK-35B-Ace-Saber-F32.mmproj"]["info"]


def test_inspect_surfaces_flm_npu_repo(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An FLM/NPU repo (config.json + tokenizer + ``.q4nx`` weights, no GGUF)
    surfaces a single whole-repo variant flagged ``flm`` rather than inspecting
    as "no variants" — the GGUF/mmproj filter alone would skip every file.
    """
    tree = [
        {"path": "config.json", "size": 2048},
        {"path": "tokenizer.json", "size": 1_800_000},
        {"path": "model.q4nx", "lfs": {"size": 2_400_000_000}, "size": 133},
        {"path": "README.md", "size": 4096},
    ]
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": ["npu", "fastflowlm"]}, tree_body=tree),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "fastflowlm/Qwen3-4B-NPU"},
    )
    assert r.status_code == 200, r.text
    variants = r.json()["variants"]
    assert len(variants) == 1
    v = variants[0]
    assert v["flm"] is True
    assert v["id"] == "fastflowlm/Qwen3-4B-NPU"  # whole-repo id, routes to flm pull
    assert "FLM (NPU)" in v["info"]
    # Sum of all files (config + tokenizer + weight + readme), LFS-aware.
    assert v["size_bytes"] == 2048 + 1_800_000 + 2_400_000_000 + 4096


def test_inspect_safetensors_repo_is_not_flm(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain safetensors transformers repo shares config.json + tokenizer but
    must NOT be misread as FLM — it has no ``…nx`` NPU weight, so it yields no
    variants (nothing GGUF/mmproj/FLM to pull)."""
    tree = [
        {"path": "config.json", "size": 2048},
        {"path": "tokenizer.json", "size": 1_800_000},
        {"path": "model.safetensors", "lfs": {"size": 8_000_000_000}, "size": 133},
    ]
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=tree),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "Qwen/Qwen3-4B"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["variants"] == []


def test_inspect_ignores_non_mmproj_non_gguf_files(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stray ``.mmproj``-less, ``.gguf``-less file stays out of variants."""
    tree = [
        {"path": "model-q4.gguf", "lfs": {"size": 4_000_000_000}, "size": 132},
        {"path": "config.json", "size": 2048},
        {"path": "tokenizer.mmproj.txt", "size": 512},
    ]
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=tree),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "foo/bar"},
    )
    assert r.status_code == 200, r.text
    ids = [v["id"] for v in r.json()["variants"]]
    assert ids == ["model-q4.gguf"]


def test_inspect_accepts_hf_url_alias(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hf_url`` is accepted as an alias for ``hf_repo``."""
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=[]),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_url": "https://huggingface.co/foo/bar"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repo"] == "foo/bar"
    assert body["variants"] == []


def test_inspect_caches_response_for_repeated_calls(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 5 minute in-process cache prevents a second HF hit on the
    second click."""
    hits = {"count": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        hits["count"] += 1
        if req.url.path.endswith("/tree/main"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"tags": []})

    _patch_httpx_transport(monkeypatch, handler)
    r1 = inspect_client.post("/api/models/inspect", json={"hf_repo": "org/cached"})
    r2 = inspect_client.post("/api/models/inspect", json={"hf_repo": "org/cached"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    # Each fresh fetch issues two requests (meta + tree); a cached call
    # issues none.
    assert hits["count"] == 2


def test_inspect_returns_502_when_hf_unreachable(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport failure surfaces as ``hf.unreachable`` with status 502."""
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(fail_with=httpx.ConnectError),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "org/down"},
    )
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["error"]["code"] == "hf.unreachable"
    assert body["error"]["details"]["repo"] == "org/down"


def test_inspect_returns_404_when_repo_missing(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 from HF surfaces as ``hf.repo_not_found``."""
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_status=404, meta_body={"error": "not found"}),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "org/missing"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "hf.repo_not_found"


def test_inspect_rejects_missing_repo_input(inspect_client: TestClient) -> None:
    """Either ``hf_repo`` or ``hf_url`` must be present + non-empty."""
    r = inspect_client.post("/api/models/inspect", json={})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "hf.bad_request"


def test_inspect_rejects_non_org_name_input(inspect_client: TestClient) -> None:
    """Single-token inputs like 'qwen' are rejected as not org/name."""
    r = inspect_client.post("/api/models/inspect", json={"hf_repo": "qwen"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "hf.bad_request"


def test_inspect_bad_json_returns_400(inspect_client: TestClient) -> None:
    """Non-JSON bodies are rejected with the validation envelope."""
    r = inspect_client.post(
        "/api/models/inspect",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400, r.text


# ── Negative: inspect must not eat HF's pointer-file sizes ─────────────────


def test_inspect_falls_back_to_top_level_size_when_no_lfs(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For non-LFS files we fall back to the top-level ``size``."""
    tree = [
        {"path": "tiny.gguf", "size": 12_345},
    ]
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=tree),
    )

    r = inspect_client.post("/api/models/inspect", json={"hf_repo": "org/tiny"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["variants"][0]["size_bytes"] == 12_345


# ── Smoke: ensure JSON content-type is what gets returned ──────────────────


def test_inspect_response_is_application_json(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=[]),
    )
    r = inspect_client.post("/api/models/inspect", json={"hf_repo": "org/x"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    # And the body is parseable JSON.
    json.loads(r.content)


def test_list_models_surfaces_installed_flm_models(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed FLM models appear in /api/models as npu models so the NPU slot
    pickers can select any on-disk model, not just the slot default. Multimodal
    chat tags classify as chat; not-installed tags are omitted."""
    fake = [
        {
            "tag": "gemma4-it:e4b",
            "capabilities": ["chat", "stt"],  # multimodal — must classify chat
            "installed": True,
            "size_bytes": 1,
            "footprint_gb": 0.0,
            "family": "gemma4",
        },
        {
            "tag": "embed-gemma:300m",
            "capabilities": ["embed"],
            "installed": True,
            "size_bytes": 1,
            "footprint_gb": 0.0,
            "family": "embed-gemma",
        },
        {
            "tag": "qwen3:0.6b",
            "capabilities": ["chat"],
            "installed": False,  # not on disk — must be omitted
            "size_bytes": 1,
            "footprint_gb": 0.0,
            "family": "qwen3",
        },
    ]
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: fake)

    rows = {m["id"]: m for m in inspect_client.get("/api/models").json()["models"]}
    g4 = rows["gemma4-it-e4b-FLM"]
    # FLM-seed shape the NPU slot pickers (slots.jsx isFlmModel) gate on.
    assert g4["device"] == "npu"
    assert g4["backend"] == "flm"
    assert g4["upstream"] == "npu"
    assert g4["installed"] is True
    # dispatcher vocab (chat→llm), chat-first for the multimodal tag.
    assert g4["type"] == "llm"
    assert g4["capability"] == "chat"
    # embed model → dispatcher "embedding".
    assert rows["embed-gemma-300m-FLM"]["type"] == "embedding"
    # not-installed FLM tag omitted.
    assert "qwen3-0.6b-FLM" not in rows


# ── upstream provenance in /api/models ─────────────────────────────────────


def test_slot_backed_upstreams_never_stamp_origin_upstream(
    inspect_client: TestClient,
) -> None:
    """The composite ``hal0`` aggregate and container slots serve LOCAL
    models — their advertised ids must not appear as origin="upstream"
    rows. Regression: a chat slot's raw GGUF id (casing differs from the
    normalized registry id) surfaced in the Models page Upstream tab as
    "via hal0"."""
    from hal0.upstreams.registry import Upstream

    app = inspect_client.app
    reg = app.state.upstreams
    for u in list(reg.list()):
        reg.remove(u.name)
    reg.add(Upstream(name="hal0", kind="slot", url="http://127.0.0.1:8080/v1"))
    reg.add(
        Upstream(
            name="ops",
            kind="remote",
            url="http://127.0.0.1:8091/v1",
            slot_name="ops",
        )
    )
    reg.add(
        Upstream(
            name="openrouter",
            kind="remote",
            url="https://openrouter.ai/api/v1",
        )
    )
    app.state.upstream_models = {
        "hal0": ["Qwopus3.5-4B-Coder-MTP-Q6_K"],
        "ops": ["qwopus3-5-4b-coder-mtp-q6-k"],
        "openrouter": ["anthropic/claude-sonnet-4"],
    }

    rows = {m["id"]: m for m in inspect_client.get("/api/models").json()["models"]}
    # Slot-backed advertisements are suppressed entirely.
    assert "Qwopus3.5-4B-Coder-MTP-Q6_K" not in rows
    assert "qwopus3-5-4b-coder-mtp-q6-k" not in rows
    # Genuine remotes still contribute upstream rows.
    assert rows["anthropic/claude-sonnet-4"]["origin"] == "upstream"
    assert rows["anthropic/claude-sonnet-4"]["upstream"] == "openrouter"


def test_disabled_or_filtered_remote_curates_api_models(
    inspect_client: TestClient,
) -> None:
    """/api/models honors enabled + advertise_models + model_filters for
    remote upstreams (same curation as /v1/models)."""
    import dataclasses

    from hal0.upstreams.filters import ModelFilters
    from hal0.upstreams.registry import Upstream

    app = inspect_client.app
    reg = app.state.upstreams
    for u in list(reg.list()):
        reg.remove(u.name)
    base = Upstream(name="openrouter", kind="remote", url="https://openrouter.ai/api/v1")
    reg.add(
        dataclasses.replace(
            base,
            model_filters=ModelFilters.from_lists(include=["anthropic/*"], exclude=["*:free"]),
        )
    )
    reg.add(
        dataclasses.replace(base, name="disabled-one", enabled=False),
    )
    app.state.upstream_models = {
        "openrouter": [
            "anthropic/claude-sonnet-4",
            "anthropic/claude-haiku:free",
            "nvidia/nemotron-70b",
        ],
        "disabled-one": ["mistral/mistral-large"],
    }

    rows = {m["id"]: m for m in inspect_client.get("/api/models").json()["models"]}
    assert "anthropic/claude-sonnet-4" in rows
    assert "anthropic/claude-haiku:free" not in rows  # exclude wins
    assert "nvidia/nemotron-70b" not in rows  # not included
    assert "mistral/mistral-large" not in rows  # disabled upstream


def test_update_model_rejects_managed_args_in_extra_args(
    inspect_client: TestClient, tmp_hal0_home: str
) -> None:
    """Save-time §21.7 screen: a managed flag in defaults.extra_args fails the
    PUT with the same envelope launch would raise, instead of persisting a
    tune that can never load."""
    fp = Path(tmp_hal0_home) / "screened.gguf"
    fp.write_bytes(b"\x00" * 8)
    inspect_client.post("/api/models", json={"id": "screened", "path": str(fp)})

    r = inspect_client.put(
        "/api/models/screened",
        json={"defaults": {"extra_args": "--port 9999 -fa on"}},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.managed_arg_denied"

    # Clean tune still saves.
    ok = inspect_client.put(
        "/api/models/screened",
        json={"defaults": {"extra_args": "-fa on -b 512"}},
    )
    assert ok.status_code == 200, ok.text
