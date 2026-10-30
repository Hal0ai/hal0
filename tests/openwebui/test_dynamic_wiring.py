"""Tests for the RAG / image-gen / web-search dynamic env blocks.

Two layers:

  * ``hal0.openwebui.env_writer.dynamic_env_overrides`` — pure rendering,
    already-resolved inputs in, an overrides dict out.
  * ``hal0.openwebui.wiring`` — the live-truth resolver: reads
    ``capabilities.toml`` and the ComfyUI models tree, resolves the img model
    the API can render, and calls the renderer above.

The matrix the brief asks for: no embed slot / embed slot / comfyui
present / both.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from hal0.comfyui import paths
from hal0.comfyui.catalog import CATALOG, component_path
from hal0.openwebui.env_writer import _DYNAMIC_ENV_KEYS, dynamic_env_overrides
from tests.openwebui._podman_env import parse_podman_env as _podman_env


def _write_capabilities_toml(home: str, body: str) -> None:
    etc = Path(home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "capabilities.toml").write_text(body, encoding="utf-8")


@pytest.fixture(autouse=True)
def _comfyui_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The ComfyUI models tree under ``tmp_path``: the image block needs the model installed."""
    root = tmp_path / "comfyui-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    assert paths.models_root().is_relative_to(tmp_path)
    return root


def _install(model_id: str) -> None:
    """Put every component file of catalog model ``model_id`` where an install would."""
    root = paths.models_root()
    for component in CATALOG[model_id].components:
        path = component_path(component, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def _write_sdxl_checkpoint(relpath: str) -> None:
    """A user checkpoint under ``checkpoints/`` whose safetensors header sniffs as SDXL."""
    raw = json.dumps(
        {"conditioner.embedders.1.x": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}
    ).encode("utf-8")
    ckpt = paths.models_root() / "checkpoints" / relpath
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00")


# ── dynamic_env_overrides (pure renderer) ───────────────────────────────────


def test_no_gates_nulls_every_dynamic_key() -> None:
    """Nothing bound → every dynamic key is explicitly nulled (not merely
    omitted) so a converge re-render deletes stale keys, never leaves
    them."""
    overrides = dynamic_env_overrides(
        embed_model_id=None,
        image_model_id=None,
        search_provider=None,
    )
    assert set(overrides) == set(_DYNAMIC_ENV_KEYS)
    assert all(v is None for v in overrides.values())


def test_embed_only_renders_rag_block_and_nulls_the_rest() -> None:
    overrides = dynamic_env_overrides(
        embed_model_id="nomic-embed-text-v1.5",
        image_model_id=None,
        search_provider=None,
    )
    assert overrides["RAG_EMBEDDING_ENGINE"] == "openai"
    assert overrides["RAG_OPENAI_API_BASE_URL"] == "http://host.docker.internal:8080/v1"
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["ENABLE_IMAGE_GENERATION"] is None
    assert overrides["ENABLE_WEB_SEARCH"] is None


_IMAGE_BLOCK = {
    "ENABLE_IMAGE_GENERATION": "True",
    "IMAGE_GENERATION_ENGINE": "openai",
    "IMAGES_OPENAI_API_BASE_URL": "http://host.docker.internal:8080/v1",
    "IMAGES_OPENAI_API_KEY": "sk-hal0-local",
    "IMAGE_GENERATION_MODEL": "sdxl-lightning",
    "IMAGE_SIZE": "",
}


def test_image_only_renders_the_openai_block_and_nulls_rag() -> None:
    overrides = dynamic_env_overrides(
        embed_model_id=None, image_model_id="sdxl-lightning", search_provider=None
    )
    assert {k: overrides[k] for k in _IMAGE_BLOCK} == _IMAGE_BLOCK
    assert overrides["RAG_EMBEDDING_ENGINE"] is None
    assert "IMAGE_STEPS" not in overrides  # #3047: hal0's /v1 picks the step count


def test_no_dynamic_key_is_a_comfyui_setting() -> None:
    assert [k for k in _DYNAMIC_ENV_KEYS if k.startswith("COMFYUI_")] == []


def test_both_embed_and_image_render_together() -> None:
    overrides = dynamic_env_overrides(
        embed_model_id="nomic-embed-text-v1.5",
        image_model_id="sdxl-turbo",
        search_provider=None,
    )
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["IMAGE_GENERATION_MODEL"] == "sdxl-turbo"


def test_search_provider_renders_web_search_block() -> None:
    overrides = dynamic_env_overrides(
        embed_model_id=None,
        image_model_id=None,
        search_provider={"engine": "searxng", "query_url": "http://searxng:8080/search?q=<query>"},
    )
    assert overrides["ENABLE_WEB_SEARCH"] == "True"
    assert overrides["WEB_SEARCH_ENGINE"] == "searxng"
    assert overrides["SEARXNG_QUERY_URL"] == "http://searxng:8080/search?q=<query>"


# ── hal0.openwebui.wiring (live-truth resolver) ─────────────────────────────


_NO_SELECTIONS = "schema_version = 2\n"

_EMBED_ONLY = """
schema_version = 2
[selections.embed.embed]
device = "gpu-vulkan"
provider = "llama-server"
model = "nomic-embed-text-v1.5"
enabled = true
"""

_IMG_ONLY = """
schema_version = 2
[selections.img.img]
device = "gpu-vulkan"
provider = "comfyui"
model = "sdxl-turbo"
enabled = true
"""

_BOTH = """
schema_version = 2
[selections.embed.embed]
device = "gpu-vulkan"
provider = "llama-server"
model = "nomic-embed-text-v1.5"
enabled = true

[selections.img.img]
device = "gpu-vulkan"
provider = "comfyui"
model = "sdxl-turbo"
enabled = true
"""

_DISABLED = """
schema_version = 2
[selections.embed.embed]
device = "gpu-vulkan"
provider = "llama-server"
model = "nomic-embed-text-v1.5"
enabled = false
"""


@pytest.mark.parametrize(
    ("toml_body", "expect_documents", "expect_images"),
    [
        (_NO_SELECTIONS, False, False),
        (_EMBED_ONLY, True, False),
        (_IMG_ONLY, False, True),
        (_BOTH, True, True),
        (_DISABLED, False, False),
    ],
    ids=["none", "embed-only", "img-only", "both", "selection-present-but-disabled"],
)
def test_wiring_status_matrix(
    tmp_hal0_home: str, toml_body: str, expect_documents: bool, expect_images: bool
) -> None:
    from hal0.openwebui.wiring import openwebui_wiring_status

    _install("sdxl-turbo")  # the images chip needs the selected model installed and renderable
    _write_capabilities_toml(tmp_hal0_home, toml_body)
    status = openwebui_wiring_status()
    # Chat/voice are unconditional (env_writer's static defaults).
    assert status["chat"] is True
    assert status["voice"] is True
    assert status["documents"] is expect_documents
    assert status["images"] is expect_images
    assert status["web_search"] is False  # no provider seam wired up yet


@pytest.mark.parametrize(
    ("model_id", "install", "wired"),
    [
        ("sdxl-turbo", True, True),
        ("sdxl-turbo", False, False),  # selected, never downloaded (or removed since)
        ("wan22-t2v", False, False),  # a catalog model the API cannot render
        ("ckpt/absent", False, False),  # no such user checkpoint
        ("my-custom-checkpoint.safetensors", False, False),  # not an id at all
    ],
    ids=["installed", "not-installed", "not-renderable", "absent-checkpoint", "not-an-id"],
)
def test_images_chip_is_lit_exactly_when_the_env_block_is_written(
    tmp_hal0_home: str, model_id: str, install: bool, wired: bool
) -> None:
    """One owner: the Services chip and OWUI's image env block read the same resolution.

    A selection that does not resolve to an installed, API-renderable model writes no image
    settings, so the dashboard must not say the image button is wired.
    """
    from hal0.openwebui.wiring import openwebui_wiring_status, resolve_dynamic_env_overrides

    if install:
        _install(model_id)
    _write_capabilities_toml(tmp_hal0_home, _img_selection(model_id))

    status = openwebui_wiring_status()
    overrides = resolve_dynamic_env_overrides()

    assert (overrides["ENABLE_IMAGE_GENERATION"] == "True") is wired
    assert status["images"] is wired
    assert status["image_model"] == (model_id if wired else None)


def test_wiring_status_agents_empty_without_registry(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import openwebui_wiring_status

    _write_capabilities_toml(tmp_hal0_home, _NO_SELECTIONS)
    assert openwebui_wiring_status()["agents"] == []


def test_wiring_status_agents_lists_managed_upstream_models(tmp_hal0_home: str) -> None:
    from types import SimpleNamespace

    from hal0.openwebui.wiring import openwebui_wiring_status

    def _up(name: str, managed_by: str | None, owned: tuple[str, ...]) -> SimpleNamespace:
        return SimpleNamespace(name=name, managed_by=managed_by, owned_models=owned)

    class _Reg:
        def list(self) -> list[SimpleNamespace]:
            return [
                _up("hermes-agent", "agent:hermes", ("hermes-agent",)),
                _up("hermes-coder", "agent:hermes", ("stale-owned",)),
                _up("openai", None, ("gpt-x",)),
            ]

    _write_capabilities_toml(tmp_hal0_home, _NO_SELECTIONS)
    status = openwebui_wiring_status(
        upstreams=_Reg(), model_cache={"hermes-coder": ["coder", "hermes-agent"]}
    )
    # cache wins over owned_models; operator upstream ignored; sorted + deduped
    assert status["agents"] == ["coder", "hermes-agent"]


def test_wiring_status_agents_survives_broken_registry(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import openwebui_wiring_status

    class _Boom:
        def list(self) -> list[object]:
            raise RuntimeError("boom")

    _write_capabilities_toml(tmp_hal0_home, _NO_SELECTIONS)
    assert openwebui_wiring_status(upstreams=_Boom())["agents"] == []


def test_wiring_no_embed_slot_produces_no_rag_overrides(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _write_capabilities_toml(tmp_hal0_home, _NO_SELECTIONS)
    overrides = resolve_dynamic_env_overrides()
    assert overrides["RAG_EMBEDDING_MODEL"] is None
    assert overrides["ENABLE_IMAGE_GENERATION"] is None


def test_wiring_embed_slot_renders_rag_overrides(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _write_capabilities_toml(tmp_hal0_home, _EMBED_ONLY)
    overrides = resolve_dynamic_env_overrides()
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["ENABLE_IMAGE_GENERATION"] is None


def _img_selection(model: str) -> str:
    return f"""
schema_version = 2
[selections.img.img]
device = "gpu-vulkan"
provider = "comfyui"
model = "{model}"
enabled = true
"""


def test_wiring_writes_the_resolved_catalog_id(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _install("sdxl-lightning")
    _write_capabilities_toml(tmp_hal0_home, _img_selection("sdxl-lightning"))
    overrides = resolve_dynamic_env_overrides()
    assert {k: overrides[k] for k in _IMAGE_BLOCK} == _IMAGE_BLOCK


def test_a_user_checkpoint_with_a_space_reaches_podman_unquoted(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    from hal0.openwebui.env_writer import write_openwebui_env
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _write_sdxl_checkpoint("My Model.safetensors")
    _write_capabilities_toml(tmp_hal0_home, _img_selection("ckpt/My Model"))
    target = write_openwebui_env(
        tmp_path / "openwebui.env", overrides=resolve_dynamic_env_overrides()
    )
    env = _podman_env(target.read_text(encoding="utf-8"))
    assert env["IMAGE_GENERATION_MODEL"] == "ckpt/My Model"
    assert env["IMAGE_SIZE"] == ""


def test_a_checkpoint_name_with_a_line_break_is_not_wired(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    from hal0.comfyui.resolve import resolve_image_model
    from hal0.openwebui.env_writer import write_openwebui_env
    from hal0.openwebui.wiring import openwebui_wiring_status, resolve_dynamic_env_overrides

    _write_sdxl_checkpoint("bad\nname.safetensors")
    # Precondition: the model resolves, so only the line-break guard can leave it unwired.
    assert resolve_image_model("ckpt/bad\nname", selection_model=None).id == "ckpt/bad\nname"
    _write_capabilities_toml(
        tmp_hal0_home,
        _EMBED_ONLY + "\n[selections.img.img]\n"
        'device = "gpu-rocm"\nprovider = "comfyui"\nmodel = "ckpt/bad\\nname"\nenabled = true\n',
    )
    overrides = resolve_dynamic_env_overrides()
    assert overrides["ENABLE_IMAGE_GENERATION"] is None
    assert openwebui_wiring_status()["images"] is False
    write_openwebui_env(tmp_path / "openwebui.env", overrides=overrides)  # must not raise
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"


def test_an_unreadable_models_tree_leaves_rag_and_web_search_wired(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tree the hal0 user cannot traverse unwires images only: it must not raise out of the
    resolver and take the RAG and web-search blocks, or the converge pass, down with it."""
    from hal0.openwebui import wiring
    from hal0.openwebui.env_writer import write_openwebui_env

    def denied(*_args: object, **_kwargs: object) -> object:
        raise PermissionError(13, "Permission denied", str(paths.models_root() / "checkpoints"))

    monkeypatch.setattr("hal0.comfyui.resolve.install_state", denied)
    provider = {"engine": "searxng", "query_url": "http://searxng:8080/search?q=<query>"}
    monkeypatch.setattr(wiring, "_search_provider_lookup", lambda: provider)
    _write_capabilities_toml(tmp_hal0_home, _BOTH)

    status = wiring.openwebui_wiring_status()
    overrides = wiring.resolve_dynamic_env_overrides()

    assert status["images"] is False
    assert status["image_model"] is None
    assert status["documents"] is True
    assert overrides["ENABLE_IMAGE_GENERATION"] is None
    assert overrides["RAG_EMBEDDING_ENGINE"] == "openai"
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["ENABLE_WEB_SEARCH"] == "True"
    assert overrides["WEB_SEARCH_ENGINE"] == "searxng"
    write_openwebui_env(tmp_path / "openwebui.env", overrides=overrides)  # must not raise


def test_wiring_both_slots_renders_both_blocks(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _install("sdxl-turbo")
    _write_capabilities_toml(tmp_hal0_home, _BOTH)
    overrides = resolve_dynamic_env_overrides()
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["IMAGE_GENERATION_MODEL"] == "sdxl-turbo"
    assert overrides["ENABLE_IMAGE_GENERATION"] == "True"


def test_wiring_image_model_that_does_not_resolve_is_not_wired(
    tmp_hal0_home: str,
) -> None:
    """A model that resolves to nothing renderable is not wired, rather than a
    claim the API cannot render."""
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _write_capabilities_toml(
        tmp_hal0_home,
        """
schema_version = 2
[selections.img.img]
device = "gpu-vulkan"
provider = "comfyui"
model = "my-custom-checkpoint.safetensors"
enabled = true
""",
    )
    overrides = resolve_dynamic_env_overrides()
    assert overrides["ENABLE_IMAGE_GENERATION"] is None


# ── MCP / agents signals on the wiring status ───────────────────────────────


def test_wiring_status_mcp_and_agents_signals(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import openwebui_wiring_status

    class _Up:
        name = "hermes"
        managed_by = "agent:hermes"
        owned_models = ("hermes-agent",)

    class _Reg:
        def list(self):
            return [_Up()]

    assert openwebui_wiring_status(mcp=True)["mcp"] is True
    assert openwebui_wiring_status(mcp=False)["mcp"] is False
    assert openwebui_wiring_status(upstreams=_Reg())["agents"] == ["hermes-agent"]
    assert openwebui_wiring_status(upstreams=_Reg(), hermes_api_down=True)["agents"] == []


def test_resolve_threads_bridge_key_and_mcp(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    on = resolve_dynamic_env_overrides(bridge_key="hal0bk_x.y", mcp_enabled=True)
    assert json.loads(on["TOOL_SERVER_CONNECTIONS"])[0]["key"] == "hal0bk_x.y"
    assert resolve_dynamic_env_overrides(mcp_enabled=False)["TOOL_SERVER_CONNECTIONS"] is None


def _enable_bridge(home: str) -> None:
    etc = Path(home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text("[bridge]\nenabled = true\n", encoding="utf-8")


def test_resolve_never_creates_bridge_db(tmp_hal0_home: str) -> None:
    """Root installer path: bridge on, no DB yet → no file is created (spec B1)."""
    from hal0.config import paths
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _enable_bridge(tmp_hal0_home)
    assert not paths.bridge_db().exists()
    out = resolve_dynamic_env_overrides()
    assert out["TOOL_SERVER_CONNECTIONS"] is None
    assert not paths.bridge_db().exists()


def test_resolve_base_url_follows_openai_api_base_urls(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "openwebui.env").write_text(
        "OPENAI_API_BASE_URLS=http://10.9.8.7:9999/v1;http://other/v1\n", encoding="utf-8"
    )
    out = resolve_dynamic_env_overrides(bridge_key="hal0bk_x.y", mcp_enabled=True)
    urls = [e["url"] for e in json.loads(out["TOOL_SERVER_CONNECTIONS"])]
    assert urls and all(u.startswith("http://10.9.8.7:9999/mcp/") for u in urls)


def test_resolve_scopes_from_stored_client(tmp_hal0_home: str) -> None:
    from hal0.bridge.store import BridgeStore
    from hal0.config import paths
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _enable_bridge(tmp_hal0_home)
    store = BridgeStore(paths.bridge_db())
    store.open()
    store.add_client("openwebui", "test", scopes=["agents", "memory", "admin"])
    out = resolve_dynamic_env_overrides(bridge_key="hal0bk_x.y")
    ids = [e["info"]["id"] for e in json.loads(out["TOOL_SERVER_CONNECTIONS"])]
    assert "hal0-admin" in ids
    out = resolve_dynamic_env_overrides(bridge_key="hal0bk_x.y", bridge_scopes=["memory"])
    ids = [e["info"]["id"] for e in json.loads(out["TOOL_SERVER_CONNECTIONS"])]
    assert ids == ["hal0-memory"]
