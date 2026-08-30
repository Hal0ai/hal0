"""``ctx_max`` / ``context_length`` parity for a DEGRADED specialty slot.

Spec 2026-08-29 (#1946), finding I2 — the read-only surfaces must report the
window the launch path actually resolves. A PromptForge model on a runner that
does not list the ``promptforge`` specialty launches GGUF-only, so its context
resolves like a plain model (the 8192 safe floor here), NOT the card's 262144.

Two surfaces are pinned at their own level, not at
``resolve_effective_context_size``:

* ``GET /api/slots/{name}`` — the real HTTP detail route.
* :func:`hal0.api.hal0_slot_alias_models` — the builder that composes the
  ``/v1/models`` slot-alias rows (the same helper
  ``tests/api/test_v1_slot_alias_models.py`` exercises that surface through).

Plus the cost pin for the third surface: the hot ``GET /api/slots`` list poll
must NOT resolve a preview bundle, even for a specialty model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import hal0_slot_alias_models
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry

_PF_META = {
    "specialty": "promptforge",
    "companions": {
        "promptforge_ffn": "/var/lib/hal0/models/pf/ffn.pfs",
        "promptforge_gdn": "/var/lib/hal0/models/pf/gdn.pfs",
        "promptforge_output_k8": "/var/lib/hal0/models/pf/k8.pfs",
    },
}

# The card window the accelerated path resolves, vs what a degraded (plain
# GGUF-only) launch resolves for this fixture: no defaults.context_size and no
# metadata.context_length, so it lands on the safe floor like any plain model.
_CARD_CTX = 262_144
_PLAIN_CTX = 8192


def _seed_pf_slot(home: str, name: str, binary: str) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text(
        "\n".join(
            [
                f'name = "{name}"',
                "port = 8099",
                'device = "gpu-rocm"',
                'provider = "llama-server"',
                'runtime = "container"',
                'profile = "promptforge"',
                f'binary = "{binary}"',
                "[model]",
                'default = "qwen-pf"',
                "context_size = 300000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _seed_pf_model(home: str) -> ModelRegistry:
    """Register the specialty model in the REAL registry.

    ``_best_effort_model_info`` (the preview bundle's model lookup) constructs
    its own :class:`ModelRegistry`, so a fake on ``app.state`` is not enough —
    the row has to exist on disk for the guard to see ``metadata.specialty``.
    """
    blob = Path(home) / "models" / "qwen-pf.gguf"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"GGUF\x00\x00\x00\x00")
    registry = ModelRegistry()
    registry.add(
        Model(
            id="qwen-pf",
            name="Qwen PromptForge",
            path=str(blob),
            size_bytes=8,
            capabilities=["chat"],
            metadata=dict(_PF_META),
        )
    )
    return registry


# ── surface 1: GET /api/slots/{name} ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("binary", "expected"),
    [("rocmfpx", _PLAIN_CTX), ("promptforge", _CARD_CTX)],
)
def test_detail_route_ctx_max_matches_the_launch_resolution(
    tmp_hal0_home: str,
    isolated_app_client: tuple[FastAPI, TestClient],
    binary: str,
    expected: int,
) -> None:
    """The detail body already carries ``specialty_degraded``; its ``ctx_max``
    must agree with it. Pre-fix both rows reported 262144."""
    app, client = isolated_app_client
    _seed_pf_slot(tmp_hal0_home, "pf", binary)
    app.state.model_registry = _seed_pf_model(tmp_hal0_home)

    r = client.get("/api/slots/pf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ctx_max"] == expected

    # Whenever the body also carries the guard's verdict, the two must agree
    # — that agreement is the whole point of I2. (The key is only stamped for a
    # slot with a live state record; an unloaded slot omits it, so this is a
    # conditional cross-check, not the assertion under test.)
    degraded = body.get("specialty_degraded")
    if isinstance(degraded, dict):
        assert binary == "rocmfpx"
        assert degraded["code"] == "slot.specialty_degraded"
        assert body["ctx_max"] == _PLAIN_CTX


# ── surface 2: the /v1/models slot-alias rows ────────────────────────────────


class _FakeSlotManager:
    def __init__(self, configs: list[dict[str, Any]]):
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


class _AliasRegistry:
    def __init__(self, metadata: dict[str, Any]):
        self._metadata = metadata

    def get(self, model_id: str) -> Any:
        if model_id != "qwen-pf":
            raise KeyError(model_id)
        meta = self._metadata

        class _M:
            name = "Qwen PromptForge"

            @staticmethod
            def model_dump() -> dict[str, Any]:
                return {"defaults": {}, "metadata": dict(meta)}

        return _M()


def _pf_alias_cfg(binary: str) -> list[dict[str, Any]]:
    return [
        {
            "name": "pf",
            "type": "llm",
            "port": 8099,
            "device": "gpu-rocm",
            "profile": "promptforge",
            "binary": binary,
            "model": {"default": "qwen-pf", "context_size": 300000},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("binary", "expected"),
    [("rocmfpx", _PLAIN_CTX), ("promptforge", _CARD_CTX)],
)
async def test_v1_models_context_matches_the_launch_resolution(binary: str, expected: int) -> None:
    """``/v1/models``' per-slot alias row advertises the window the slot really
    launches with — a client that sizes a request off the advertised (larger)
    number would otherwise overrun the real window and get truncated."""
    model_info = {"_model_key": "qwen-pf", "metadata": dict(_PF_META)}
    with patch("hal0.providers.container._best_effort_model_info", return_value=model_info):
        entries = await hal0_slot_alias_models(
            _FakeSlotManager(_pf_alias_cfg(binary)),
            _AliasRegistry(_PF_META),
            now=1000,
        )

    row = {e["id"]: e for e in entries}["pf"]
    assert row["context_length"] == expected
    assert row["max_context_window"] == expected


# ── surface 3 (cost pin): the hot GET /api/slots list poll ───────────────────


def test_list_route_never_resolves_a_preview_bundle_for_a_specialty_model(
    tmp_hal0_home: str,
    isolated_app_client: tuple[FastAPI, TestClient],
) -> None:
    """``GET /api/slots`` is the ~2s dashboard poll. Resolving the specialty
    guard's verdict there would run ``_resolve_preview_bundle`` — and open a
    fresh ModelRegistry/SQLite connection via ``_best_effort_model_info`` —
    once per specialty slot per poll, the exact cost
    ``SlotManager.status`` refuses by gating ``specialty_degraded`` behind
    ``include_config_drift``. The list path therefore passes NO slot cfg, and
    its ``ctx_max`` keeps the accelerated/plain resolution (documented
    asymmetry: the detail route is the degraded-aware surface).
    """
    app, client = isolated_app_client
    _seed_pf_slot(tmp_hal0_home, "pf", "rocmfpx")
    app.state.model_registry = _seed_pf_model(tmp_hal0_home)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("hot list poll must not resolve a preview bundle")

    with patch("hal0.providers.container._resolve_preview_bundle", _boom):
        r = client.get("/api/slots")

    assert r.status_code == 200, r.text
    row = {e["name"]: e for e in r.json()}["pf"]
    assert row["ctx_max"] == _CARD_CTX  # accelerated resolution, no guard call
