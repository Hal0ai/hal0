"""Contract tests for GET /api/meta/enums (canonical vocabulary surface).

The dashboard codes against these EXACT field names — the payload is the
wire form of the canonical taxonomy in :mod:`hal0.model_meta`, so every
key, the device-card shape, and the recommended flag are pinned here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0 import model_meta

_EXPECTED_KEYS = {
    "devices",
    "backends",
    "selectable_backends",
    "device_classes",
    "slot_types",
    "model_capabilities",
    "capability_aliases",
    "model_backends",
    "runtime_families",
    "backend_to_device",
    "device_default_profiles",
}

_DEVICE_FIELDS = {
    "id",
    "label",
    "device_class",
    "default_profile",
    "legacy_backend",
    "recommended",
    "description",
}


def _get_enums(client: TestClient) -> dict:
    r = client.get("/api/meta/enums")
    assert r.status_code == 200, r.text
    return r.json()


def test_contract_shape(client: TestClient) -> None:
    body = _get_enums(client)
    assert set(body.keys()) == _EXPECTED_KEYS
    # List-valued vocabularies.
    for key in (
        "backends",
        "selectable_backends",
        "device_classes",
        "slot_types",
        "model_capabilities",
        "model_backends",
        "runtime_families",
    ):
        assert isinstance(body[key], list) and body[key], key
        assert all(isinstance(x, str) for x in body[key]), key
    # Map-valued vocabularies.
    for key in ("capability_aliases", "backend_to_device", "device_default_profiles"):
        assert isinstance(body[key], dict) and body[key], key


def test_devices_complete_and_shaped(client: TestClient) -> None:
    devices = _get_enums(client)["devices"]
    assert [d["id"] for d in devices] == ["gpu-rocm", "gpu-vulkan", "cpu", "npu"]
    for d in devices:
        assert set(d.keys()) == _DEVICE_FIELDS, d
        assert isinstance(d["recommended"], bool)
        assert d["label"] and d["description"]
        assert d["device_class"] in model_meta.DEVICE_CLASSES
        assert d["legacy_backend"] in model_meta.LEGACY_BACKENDS
        # Every device names a real seed default profile — a fresh slot
        # with profile="" fails to load, so "" is never valid here.
        assert d["default_profile"], d["id"]


def test_gpu_rocm_is_the_only_recommended_device(client: TestClient) -> None:
    # CONTEXT.md: gpu-rocm is the recommended default on Strix Halo;
    # gpu-vulkan is the slower fallback.
    devices = _get_enums(client)["devices"]
    recommended = [d["id"] for d in devices if d["recommended"]]
    assert recommended == ["gpu-rocm"]


def test_payload_matches_canonical_taxonomy(client: TestClient) -> None:
    body = _get_enums(client)
    assert body["backends"] == list(model_meta.LEGACY_BACKENDS)
    assert body["selectable_backends"] == ["rocm", "vulkan", "cpu", "auto"]
    assert body["device_classes"] == list(model_meta.DEVICE_CLASSES)
    assert body["slot_types"] == list(model_meta.SLOT_TYPES)
    assert body["model_capabilities"] == list(model_meta.MODEL_CAPABILITIES)
    assert body["capability_aliases"] == model_meta.CAPABILITY_ALIASES
    assert body["model_backends"] == list(model_meta.MODEL_BACKENDS)
    assert body["runtime_families"] == list(model_meta.RUNTIME_FAMILIES)
    assert body["backend_to_device"] == model_meta.BACKEND_TO_DEVICE
    assert body["device_default_profiles"] == model_meta.DEVICE_TO_DEFAULT_PROFILE
    # The cpu default profile is the deliberate "cpu-llm" unification
    # (the stacks route used to carry a divergent "" entry).
    assert body["device_default_profiles"]["cpu"] == "cpu-llm"


def test_capability_aliases_point_at_canonical_spellings(client: TestClient) -> None:
    body = _get_enums(client)
    caps = set(body["model_capabilities"])
    for alias, canonical in body["capability_aliases"].items():
        assert canonical in caps, (alias, canonical)
        assert alias not in caps, alias  # an alias is never also canonical


def test_cache_headers_and_304_revalidation(client: TestClient) -> None:
    r = client.get("/api/meta/enums")
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag
    assert "max-age" in r.headers.get("cache-control", "")
    r2 = client.get("/api/meta/enums", headers={"If-None-Match": etag})
    assert r2.status_code == 304
