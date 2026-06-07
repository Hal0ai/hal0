"""Tests for the Lemonade-derived state enrichment on /api/slots (PR-11).

The list endpoint enriches each real slot entry with three optional
fields lifted from Lemonade's ``/v1/health.loaded[]``:

  - ``lemonade_state``: ``loaded`` | ``idle`` | ``disabled`` | ``error``
  - ``backend_url``: per-model child server URL (only for loaded models)
  - ``coresident_group``: shared ID for the NPU FLM trio (chat + ASR + embed)

All three are additive — legacy fields stay untouched so v0.1.x clients
keep rendering.
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

import hal0.providers as providers_mod
from hal0.api import create_app
from hal0.lemonade.client import LemonadeClient
from hal0.providers.lemonade import LemonadeProvider


@pytest.fixture
def lemonade_health_state() -> dict[str, Any]:
    """Mutable handle for the lemond stub's /v1/health response.

    Tests mutate ``state["loaded"]`` to drive different enrichment
    scenarios without re-installing the provider.
    """
    return {"loaded": []}


@pytest.fixture
def installed_lemonade_stub(
    lemonade_health_state: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """Install a Lemonade stub whose /v1/health echoes lemonade_health_state.

    Other lemonade endpoints (/v1/load, /v1/unload) return innocuous
    success responses so adjacent fixture setup doesn't 4xx.
    """
    state = lemonade_health_state

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": state["loaded"]})
        if req.url.path in ("/v1/load", "/v1/unload"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"detail": f"unmocked {req.url.path}"})

    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(h),
        base_url="http://test",
    )
    provider = LemonadeProvider(client=LemonadeClient(http_client=transport))
    original = providers_mod._PROVIDERS["lemonade"]
    providers_mod._PROVIDERS["lemonade"] = provider
    try:
        yield state
    finally:
        providers_mod._PROVIDERS["lemonade"] = original


def _seed_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def npu_trio_slot_root(tmp_hal0_home: str) -> Path:
    """Lay down the NPU FLM trio (agent + stt-npu + embed-npu) on disk."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt-npu",
        [
            'name = "stt-npu"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = true",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "embed-npu",
        [
            'name = "embed-npu"',
            "port = 8085",
            'device = "npu"',
            'type = "embedding"',
            "enabled = true",
            "[model]",
            'default = "embed-gemma"',
        ],
    )
    return root


@pytest.fixture
def isolated_app(tmp_hal0_home: str) -> FastAPI:
    return create_app()


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(isolated_app) as c:
        yield c


# ── lemonade_state field ───────────────────────────────────────────────────


def test_list_slots_emits_idle_for_enabled_slot_with_no_loaded_model(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Enabled slot, /v1/health.loaded[] empty → lemonade_state=idle."""
    installed_lemonade_stub["loaded"] = []
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["agent"]["lemonade_state"] == "idle"
    assert "backend_url" not in by_name["agent"]


def test_list_slots_emits_loaded_with_backend_url(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Model present in /v1/health.loaded[] → lemonade_state=loaded + backend_url lifted."""
    installed_lemonade_stub["loaded"] = [
        {"model_name": "gemma3-1b", "backend_url": "http://127.0.0.1:14002/v1"},
    ]
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200
    by_name = {e["name"]: e for e in r.json()}
    agent = by_name["agent"]
    assert agent["lemonade_state"] == "loaded"
    assert agent["backend_url"] == "http://127.0.0.1:14002/v1"


def test_list_slots_emits_disabled_for_enabled_false_slot(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """enabled=false slots surface as lemonade_state=disabled regardless of /v1/health."""
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = false",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    # Even if lemond claims it's loaded, the disabled flag wins.
    installed_lemonade_stub["loaded"] = [{"model_name": "gemma3-1b"}]
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["agent"]["lemonade_state"] == "disabled"


# ── coresident_group field ─────────────────────────────────────────────────


def test_list_slots_emits_coresident_group_for_npu_trio(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """When NPU LLM anchor is enabled, all three trio slots get coresident_group."""
    installed_lemonade_stub["loaded"] = [{"model_name": "gemma3-1b"}]
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    for slot_name in ("agent", "stt-npu", "embed-npu"):
        assert by_name[slot_name].get("coresident_group") == "npu-flm-trio", (
            f"slot {slot_name} missing coresident_group: {by_name[slot_name]}"
        )


def test_list_slots_coresident_group_uses_device_not_legacy_names(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """coresident_group must key off device==npu, not the legacy slot names.

    Deployment uses the real names ``npu``/``stt``/``embed`` (not the seed
    names ``agent``/``stt-npu``/``embed-npu``). The dead ``_FLM_TRIO_SLOTS``
    frozenset only matched the seed names, so the trio badge never rendered
    in production. Device-based detection fixes that.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "npu",
        [
            'name = "npu"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-4b-FLM"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt",
        [
            'name = "stt"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = true",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "embed",
        [
            'name = "embed"',
            "port = 8085",
            'device = "npu"',
            'type = "embedding"',
            "enabled = true",
            "[model]",
            'default = "embed-gemma"',
        ],
    )
    installed_lemonade_stub["loaded"] = [{"model_name": "gemma3-4b-FLM"}]
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    for slot_name in ("npu", "stt", "embed"):
        assert by_name[slot_name].get("coresident_group") == "npu-flm-trio", (
            f"slot {slot_name} missing coresident_group: {by_name[slot_name]}"
        )


def test_list_slots_no_coresident_group_when_npu_anchor_disabled(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Disabled NPU LLM anchor → no trio markers on the sibling slots."""
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = false",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt-npu",
        [
            'name = "stt-npu"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = true",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["stt-npu"].get("coresident_group") is None


# ── config-field exposure (Spec 1 / Component 1) ───────────────────────────
#
# The slot-edit panel seeds its card + drawer controls from the slot list
# payload. Three SlotConfig fields must ride along so the UI doesn't have to
# fetch /config per slot: ``enabled`` (top-level), ``enable_thinking``
# (top-level), ``n_gpu_layers`` (from [model]).


def test_list_slots_exposes_enable_thinking_and_n_gpu_layers(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """A slot's enable_thinking + [model].n_gpu_layers ride along in the payload."""
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "enable_thinking = true",
            "[model]",
            'default = "qwen3"',
            "n_gpu_layers = 99",
        ],
    )
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["enable_thinking"] is True
    assert primary["n_gpu_layers"] == 99
    assert primary["enabled"] is True


def test_list_slots_enable_thinking_null_when_unset(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """No enable_thinking in TOML → payload reports it as null (effective OFF)."""
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["enable_thinking"] is None
    # n_gpu_layers absent from [model] → field still present, default sentinel
    assert "n_gpu_layers" in primary


# ── Spec 1 / Component 2 (issue #587) ──────────────────────────────────────
#
# The slot-edit drawer seeds idle_timeout_s / workers / llamacpp_args from
# the list payload. Before #587 the list omitted all three so the drawer
# used hardcoded constants (900 / 1 / "--flash-attn on --no-mmap") and
# clobbered the on-disk values on every Save. After the fix the payload
# carries the slot's real on-disk values so the drawer (and its dirty-
# tracking) can leave untouched fields alone.


def test_list_slots_exposes_idle_timeout_workers_llamacpp_args(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """idle_timeout_s / workers / llamacpp_args ride along on /api/slots.

    The on-disk shape is:
      - ``workers`` + ``idle_timeout_s`` are flat top-level SlotConfig
        fields (hoisted from the [slot] TOML table by the loader).
      - ``llamacpp_args`` is the dashboard's wire name; the on-disk field
        lives under ``[server].extra_args`` (ServerConfig). The list
        payload maps to the dashboard's key so the drawer can seed
        directly.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "workers = 4",
            "idle_timeout_s = 1200",
            "[model]",
            'default = "qwen3"',
            "[server]",
            'extra_args = "--threads 6 --no-mmap"',
        ],
    )
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["idle_timeout_s"] == 1200
    assert primary["workers"] == 4
    assert primary["llamacpp_args"] == "--threads 6 --no-mmap"


def test_list_slots_llamacpp_args_none_when_server_table_absent(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Slot with no [server] table → payload's llamacpp_args is null.

    Mirror the existing enable_thinking behaviour: absent on-disk → null
    in the wire payload (effective unset), not omitted. The dashboard
    uses null to skip sending the field on Save.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "workers = 2",
            "idle_timeout_s = 600",
            "[model]",
            'default = "qwen3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["idle_timeout_s"] == 600
    assert primary["workers"] == 2
    assert primary["llamacpp_args"] is None


# ── Issue #548: rope_freq_base ───────────────────────────────────────────────
#
# rope_freq_base is a [model] float field. The list payload must expose it so
# the Edit drawer can dirty-track and avoid clobbering the on-disk value.
# The PUT round-trip must persist it through update_config's deep merge.


def test_list_slots_exposes_rope_freq_base(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """rope_freq_base set on disk → exposed in /api/slots payload."""
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3"',
            "rope_freq_base = 500000.0",
        ],
    )
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["rope_freq_base"] == 500000.0


def test_list_slots_rope_freq_base_null_when_absent(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """rope_freq_base absent on disk → payload carries null (not 0.0)."""
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["rope_freq_base"] is None


def test_put_config_rope_freq_base_roundtrip(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """PUT /api/slots/{name}/config with model.rope_freq_base persists the value
    and leaves the model default key intact (deep-merge, not table replacement).
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    r = isolated_client.put(
        "/api/slots/primary/config",
        json={"model": {"rope_freq_base": 1000000.0}},
    )
    assert r.status_code == 200, r.text

    # The list payload should now reflect the written value.
    r2 = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r2.json()}
    assert by_name["primary"]["rope_freq_base"] == 1000000.0

    # Deep-merge must not have wiped the model default key.
    cfg = isolated_client.get("/api/slots/primary/config").json()
    assert cfg["model"]["rope_freq_base"] == 1000000.0
    assert cfg["model"]["default"] == "qwen3-4b"


def test_list_slots_skips_coresident_for_disabled_sibling(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """A disabled sibling slot doesn't claim coresident membership."""
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt-npu",
        [
            'name = "stt-npu"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = false",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    # Anchor still marked.
    assert by_name["agent"].get("coresident_group") == "npu-flm-trio"
    # Disabled sibling is NOT marked.
    assert by_name["stt-npu"].get("coresident_group") is None


# ── Per-slot endpoint enrichment ───────────────────────────────────────────


def test_get_slot_includes_lemonade_state(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """GET /api/slots/{name} is enriched same shape as the list endpoint."""
    installed_lemonade_stub["loaded"] = [
        {"model_name": "gemma3-1b", "backend_url": "http://127.0.0.1:14002/v1"},
    ]
    r = isolated_client.get("/api/slots/agent")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lemonade_state"] == "loaded"
    assert body["backend_url"] == "http://127.0.0.1:14002/v1"
    assert body["coresident_group"] == "npu-flm-trio"


# ── Backwards compatibility ────────────────────────────────────────────────


def test_legacy_fields_still_present(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """v0.1.x clients consuming /api/slots see every legacy key unchanged."""
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    legacy_keys = {"name", "status", "port", "model_id", "backend", "kind"}
    for slot in ("agent", "stt-npu", "embed-npu"):
        present = set(by_name[slot].keys())
        missing = legacy_keys - present
        assert not missing, f"slot {slot} missing legacy keys: {missing}"


# ── PR-18 persona-surface fields ───────────────────────────────────────────


def test_list_slots_emits_type_and_model_default_for_persona_dropdown(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """PR-18: each entry carries ``type`` + ``model_default`` + ``enabled``.

    The dashboard's persona dropdown filters /api/slots to ``type=llm``
    rows and uses ``model_default`` as the value posted in
    ``body.model``. Without these fields the dropdown would need a
    second per-slot config fetch — adding them at the list level keeps
    page-load to a single round trip.
    """
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200
    by_name = {e["name"]: e for e in r.json()}

    # The chat anchor is type=llm with a default model.
    agent = by_name["agent"]
    assert agent["type"] == "llm"
    assert agent["model_default"] == "gemma3-1b"
    assert agent["enabled"] is True

    # The transcription sibling is type=transcription — the dashboard's
    # persona dropdown filters this row OUT.
    stt = by_name["stt-npu"]
    assert stt["type"] == "transcription"
    assert stt["model_default"] == "whisper-v3"


def test_list_slots_emits_labels_for_tool_calling_gate(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """PR-18: ``labels`` is lifted from ``[model] labels = [...]``.

    The dashboard's OmniRouter toggle is auto-enabled when the active
    persona's model advertises ``tool-calling``. The label list arrives
    on the list endpoint so the UI doesn't need a per-slot /config
    fetch to decide whether to show the toggle.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
            'labels = ["tool-calling", "vision"]',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["primary"]["labels"] == ["tool-calling", "vision"]
    assert by_name["primary"]["type"] == "llm"
    assert by_name["primary"]["model_default"] == "qwen3-4b"


def test_list_slots_omits_labels_when_none_declared(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Labels list is omitted (not empty) when the slot config has no
    ``model.labels`` entry. Keeps the wire payload tight and matches
    the existing pattern of only emitting fields with content.
    """
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    # The NPU trio TOMLs in the fixture don't carry a labels field.
    assert "labels" not in by_name["agent"]


def test_list_degrades_when_lemonade_unreachable(
    npu_trio_slot_root: Path,
    isolated_client: TestClient,
) -> None:
    """A down lemond doesn't break /api/slots — entries omit lemonade_state cleanly.

    No ``installed_lemonade_stub`` fixture: the default LemonadeProvider
    tries to reach 127.0.0.1:13305 and fails (no daemon under test).
    The enrichment helper swallows the error.
    """
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200
    # The legacy entries must still come back; lemonade_state may be
    # absent (lemond unreachable) or "idle" (which the enrichment
    # treats as the not-loaded fallback). Either is acceptable; what
    # matters is that the endpoint doesn't 500.
    body = r.json()
    assert isinstance(body, list)
    assert any(e["name"] == "agent" for e in body)


def test_loaded_state_uses_chat_anchor_model(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Each slot's lemonade_state keys off ITS OWN model.default, not the chat anchor.

    The NPU trio's siblings carry their own model_name (whisper, embed-gemma).
    A naive implementation could short-circuit on the chat anchor's model
    name and over-report 'loaded' for the trio siblings.
    """
    # Only the chat model is loaded; siblings should NOT be 'loaded'.
    installed_lemonade_stub["loaded"] = [{"model_name": "gemma3-1b"}]
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["agent"]["lemonade_state"] == "loaded"
    assert by_name["stt-npu"]["lemonade_state"] == "idle"
    assert by_name["embed-npu"]["lemonade_state"] == "idle"


def test_list_route_handles_alternate_health_key(
    npu_trio_slot_root: Path,
    isolated_client: TestClient,
) -> None:
    """/v1/health may emit the ``all_models_loaded`` key alongside ``loaded``.

    LemonadeProvider.status() already accepts both — the enrichment
    helper inherits that tolerance so a Lemonade build flip doesn't
    blank the dashboard.
    """
    state = {"all_models_loaded": [{"model_name": "gemma3-1b"}]}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json=state)
        return httpx.Response(404, json={"detail": f"unmocked {req.url.path}"})

    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(h),
        base_url="http://test",
    )
    provider = LemonadeProvider(client=LemonadeClient(http_client=transport))
    original = providers_mod._PROVIDERS["lemonade"]
    providers_mod._PROVIDERS["lemonade"] = provider
    try:
        r = isolated_client.get("/api/slots")
        by_name = {e["name"]: e for e in r.json()}
        assert by_name["agent"]["lemonade_state"] == "loaded"
    finally:
        providers_mod._PROVIDERS["lemonade"] = original


# ── B2: declared / actual backend enrichment (ADR-0022) ────────────────────


def test_list_slots_emits_declared_backend_when_loaded(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """A loaded slot carries declared_backend (normalized token), even when
    the actual backend can't be introspected (no live child under test)."""
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-vulkan"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    installed_lemonade_stub["loaded"] = [
        {"model_name": "qwen3-4b", "backend_url": "http://127.0.0.1:14002/v1"},
    ]
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["lemonade_state"] == "loaded"
    assert primary["declared_backend"] == "vulkan"
    # No live child → actual_backend + backend_mismatch are absent (not null).
    assert "actual_backend" not in primary
    assert "backend_mismatch" not in primary


def test_list_slots_surfaces_actual_backend_and_mismatch(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With resolve_actual_backend monkeypatched to a divergent backend, the
    enrichment surfaces actual_backend + backend_mismatch=True."""
    import hal0.providers.lemonade as lemonade_mod

    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-vulkan"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    installed_lemonade_stub["loaded"] = [
        {"model_name": "qwen3-4b", "backend_url": "http://127.0.0.1:14002/v1"},
    ]
    # Declared vulkan but the child is actually running rocm → mismatch.
    monkeypatch.setattr(lemonade_mod, "resolve_actual_backend", lambda _e: "rocm")
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["declared_backend"] == "vulkan"
    assert primary["actual_backend"] == "rocm"
    assert primary["backend_mismatch"] is True


def test_list_slots_omits_backend_fields_when_not_loaded(
    tmp_hal0_home: str,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """A not-loaded (idle) slot carries no declared/actual backend keys."""
    _seed_slot_toml(
        tmp_hal0_home,
        "primary",
        [
            'name = "primary"',
            "port = 8081",
            'device = "gpu-vulkan"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    installed_lemonade_stub["loaded"] = []
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["primary"]
    assert primary["lemonade_state"] == "idle"
    assert "declared_backend" not in primary
    assert "actual_backend" not in primary
    assert "backend_mismatch" not in primary


def test_json_serialisation_roundtrips(
    npu_trio_slot_root: Path,
    installed_lemonade_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """The enriched body must be valid JSON (no exotic types leaked)."""
    installed_lemonade_stub["loaded"] = [
        {"model_name": "gemma3-1b", "backend_url": "http://127.0.0.1:14002/v1"},
    ]
    r = isolated_client.get("/api/slots")
    # text + parsing both succeed → no infinite floats or set leaks
    body = json.loads(r.text)
    assert isinstance(body, list)
