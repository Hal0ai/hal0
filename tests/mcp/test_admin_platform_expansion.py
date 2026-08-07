"""Behavioral tests for the platform-management catalog expansion.

Covers the services / comfyui / updater / hardware / slots / models /
benchmarks / journal / activity / approvals / runner-images / backends /
mcp-self-management / profiles tool families added on top of the original
ADR-0004 §4 catalog. Static overlay/route-map coherence (no dupes, every
tool has an annotation + description, drift guards, etc.) is already
covered generically by ``tests/mcp/test_admin.py`` and
``tests/mcp/test_unclassified_routes.py`` — these tests exercise the actual
dispatch path (REST method/URL/payload construction, gating, redaction,
bare-list wrapping) the same way ``test_admin.py`` does for the original
catalog, using the route-map fixture wired in ``tests/mcp/conftest.py``
(``_install_admin_route_map`` installs the REAL live app's route map before
every test in this package, so no fake app is needed here).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def mock_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Same recording fake httpx.AsyncClient as test_admin.py's fixture."""

    captured: dict[str, Any] = {"calls": []}

    class _MockResponse:
        status_code = 200

        def __init__(self, payload: Any) -> None:
            self._payload = payload
            self.text = ""

        def json(self) -> Any:
            return self._payload

    class _MockClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            captured["base_url"] = base_url
            captured["timeout"] = timeout

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, params: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("GET", url, params, dict(headers or {})))
            return _MockResponse({"ok": "get"})

        async def post(self, url: str, json: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("POST", url, json, dict(headers or {})))
            return _MockResponse({"ok": "post"})

        async def delete(self, url: str, params: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("DELETE", url, params, dict(headers or {})))
            return _MockResponse({"ok": "delete"})

        async def put(self, url: str, json: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("PUT", url, json, dict(headers or {})))
            return _MockResponse({"ok": "put"})

        async def patch(self, url: str, json: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("PATCH", url, json, dict(headers or {})))
            return _MockResponse({"ok": "patch"})

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    return captured


# ── Reads: GET-verb tools dispatch immediately, no approval ─────────────────

_GET_READS: list[tuple[str, dict[str, Any], str]] = [
    ("service_list", {}, "http://t/api/services"),
    ("service_health", {}, "http://t/api/services/health"),
    ("comfyui_status", {}, "http://t/api/comfyui/status"),
    ("comfyui_workflows", {}, "http://t/api/comfyui/workflows"),
    ("updater_state", {}, "http://t/api/updates/state"),
    ("updater_check", {}, "http://t/api/updates/check"),
    ("updater_channel_get", {}, "http://t/api/updates/channel"),
    ("updater_slot_drift", {}, "http://t/api/updates/slot-drift"),
    ("updater_job_status", {"job_id": "abc123"}, "http://t/api/updates/status/abc123"),
    ("doctor_report", {}, "http://t/api/doctor"),
    ("health_system", {}, "http://t/api/health/system"),
    ("feature_list", {}, "http://t/api/features"),
    ("metrics_snapshot", {}, "http://t/api/metrics"),
    ("hardware_snapshot", {}, "http://t/api/hardware"),
    ("slot_stats", {}, "http://t/api/stats/slots"),
    ("stats_overview", {}, "http://t/api/stats"),
    ("request_metrics", {}, "http://t/api/stats/requests"),
    ("system_stats", {}, "http://t/api/system-stats"),
    ("power_stats", {}, "http://t/api/stats/power"),
    ("throughput_history", {}, "http://t/api/stats/throughput/history"),
    ("npu_occupancy", {}, "http://t/api/npu/occupancy"),
    ("npu_swap_status", {}, "http://t/api/npu/swap-status"),
    ("model_health_check", {}, "http://t/api/models/health"),
    ("slot_config", {"name": "agent"}, "http://t/api/slots/agent/config"),
    ("slot_voices", {"name": "tts"}, "http://t/api/slots/tts/voices"),
    ("flm_model_list", {}, "http://t/api/slots/flm/models"),
    ("slot_pull_status", {"name": "agent"}, "http://t/api/slots/agent/pull/status"),
    ("hf_search", {}, "http://t/api/hf/search"),
    ("model_config_read", {}, "http://t/api/config/models"),
    ("bench_roster", {}, "http://t/api/benchmarks/roster"),
    ("bench_plan", {}, "http://t/api/benchmarks/plan"),
    ("bench_cells", {}, "http://t/api/benchmarks/cells"),
    ("bench_history", {}, "http://t/api/benchmarks/history"),
    ("bench_evals", {}, "http://t/api/benchmarks/evals"),
    ("journal_snapshot", {}, "http://t/api/journal"),
    ("activity_list", {}, "http://t/api/activity"),
    ("activity_export", {}, "http://t/api/activity/export"),
    ("approval_list", {}, "http://t/api/agent/approvals"),
    ("runner_image_list", {}, "http://t/api/runner-images"),
    ("runner_image_downloaded", {}, "http://t/api/runner-images/downloaded"),
    ("backend_detail", {"backend_id": "rocm"}, "http://t/api/backends/rocm"),
    ("mcp_server_list", {}, "http://t/api/mcp/servers"),
    ("mcp_client_list", {}, "http://t/api/mcp/clients"),
    ("mcp_catalog", {}, "http://t/api/mcp/catalog"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args,expected_url", _GET_READS, ids=[t[0] for t in _GET_READS])
async def test_platform_reads_dispatch_get(
    queue: ApprovalQueue,
    mock_transport: dict[str, Any],
    tool: str,
    args: dict[str, Any],
    expected_url: str,
) -> None:
    assert tool in admin.AUTONOMOUS_READ_TOOLS, f"{tool} must be classified AUTONOMOUS_READ"
    result = await admin.dispatch(
        tool=tool,
        args=args,
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "get"}
    assert queue.list_pending() == []
    method, url, _params, headers = mock_transport["calls"][-1]
    assert method == "GET"
    assert url == expected_url
    assert headers["Authorization"] == "Bearer t"


# ── Read-shaped POSTs (model_validate, profile_generate) ────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,expected_url",
    [
        ("model_validate", "http://t/api/models/validate"),
        ("profile_generate", "http://t/api/profiles/generate"),
    ],
)
async def test_read_shaped_posts_dispatch_autonomously(
    queue: ApprovalQueue,
    mock_transport: dict[str, Any],
    tool: str,
    expected_url: str,
) -> None:
    """model_validate / profile_generate are POST routes classified
    AUTONOMOUS_READ (they never persist anything) — mirrors model_inspect's
    existing read-shaped-POST treatment."""
    assert tool in admin.AUTONOMOUS_READ_TOOLS
    result = await admin.dispatch(
        tool=tool,
        args={"hf_repo": "org/repo"} if tool == "profile_generate" else {},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "post"}
    assert queue.list_pending() == []
    method, url, _payload, _headers = mock_transport["calls"][-1]
    assert method == "POST"
    assert url == expected_url


# ── Gated tools: enqueue, then forward on approve ────────────────────────────

_GATED_CASES: list[tuple[str, dict[str, Any], str, str, dict[str, Any] | None]] = [
    (
        "service_action",
        {"service_id": "hermes", "action": "restart"},
        "POST",
        "http://t/api/services/hermes/action",
        {"action": "restart"},
    ),
    (
        "comfyui_switchover",
        {"mode": "generation"},
        "POST",
        "http://t/api/comfyui/switchover",
        {"mode": "generation"},
    ),
    ("comfyui_pin", {"pinned": True}, "POST", "http://t/api/comfyui/pin", {"pinned": True}),
    (
        "comfyui_models_fetch",
        {"auto": True},
        "POST",
        "http://t/api/comfyui/models/fetch",
        {"auto": True},
    ),
    ("comfyui_render_cancel", {}, "POST", "http://t/api/comfyui/render/cancel", None),
    ("comfyui_restart", {}, "POST", "http://t/api/comfyui/restart", None),
    (
        "comfyui_workflow_launch",
        {"name": "portrait"},
        "POST",
        "http://t/api/comfyui/workflows/portrait/launch",
        None,
    ),
    ("slot_pull_image", {"name": "agent"}, "POST", "http://t/api/slots/agent/pull", None),
    (
        "model_config_write",
        {"roots": ["/mnt/models"]},
        "PUT",
        "http://t/api/config/models",
        {"roots": ["/mnt/models"]},
    ),
    ("bench_run", {"suite": "roster"}, "POST", "http://t/api/benchmarks/run", {"suite": "roster"}),
    ("runner_image_sync", {}, "POST", "http://t/api/runner-images/sync", None),
    (
        "npu_backend_load",
        {"model_id": "lfm2:1.2b"},
        "POST",
        "http://t/api/backends/npu/load",
        {"model_id": "lfm2:1.2b"},
    ),
    (
        "npu_backend_unload",
        {"slot_name": "npu-lfm2-1-2b"},
        "POST",
        "http://t/api/backends/npu/unload",
        {"slot_name": "npu-lfm2-1-2b"},
    ),
    (
        "mcp_server_install",
        {"url": "npm:@example/thing"},
        "POST",
        "http://t/api/mcp/install",
        {"url": "npm:@example/thing"},
    ),
    ("mcp_server_uninstall", {"server_id": "foo"}, "DELETE", "http://t/api/mcp/foo", None),
    (
        "mcp_server_config_write",
        {"server_id": "foo", "enabled": False},
        "PATCH",
        "http://t/api/mcp/foo/config",
        {"enabled": False},
    ),
    (
        "mcp_server_action",
        {"server_id": "foo", "action": "start"},
        "POST",
        "http://t/api/mcp/foo/start",
        None,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args,expected_method,expected_url,expected_payload",
    _GATED_CASES,
    ids=[c[0] for c in _GATED_CASES],
)
async def test_gated_platform_tools_enqueue_then_forward_on_approve(
    queue: ApprovalQueue,
    mock_transport: dict[str, Any],
    tool: str,
    args: dict[str, Any],
    expected_method: str,
    expected_url: str,
    expected_payload: dict[str, Any] | None,
) -> None:
    assert tool in admin.GATED_TOOLS, f"{tool} must be classified GATED"
    result = await admin.dispatch(
        tool=tool,
        args=args,
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "pending_approval"
    assert mock_transport["calls"] == []  # nothing hits REST until approved

    aid = result["approval_id"]
    await queue.approve(aid)
    method, url, payload, headers = mock_transport["calls"][-1]
    assert method == expected_method
    assert url == expected_url
    if expected_payload is not None:
        assert payload == expected_payload
    assert headers["Authorization"] == "Bearer t"


# ── comfyui_logs: gated (MED-1 journald posture) + redacted before return ───


@pytest.fixture
def _comfyui_logs_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    class _MockResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "lines": [
                    "[00:00] comfyui starting",
                    "[00:01] outbound Authorization: Bearer sk-or-LEAK-9 to provider",
                ]
            }

    class _MockClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, params: Any = None, headers: Any = None) -> _MockResponse:
            return _MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)


@pytest.mark.asyncio
async def test_comfyui_logs_is_gated_and_redacted_on_approve(
    queue: ApprovalQueue,
    _comfyui_logs_transport: None,
) -> None:
    assert "comfyui_logs" in admin.GATED_TOOLS
    result = await admin.dispatch(
        tool="comfyui_logs",
        args={"tail": 60},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "pending_approval"
    approved = await queue.approve(result["approval_id"])
    payload = approved["result"]
    text = str(payload)
    assert "sk-or-LEAK-9" not in text
    assert "Bearer ***REDACTED***" in text
    assert any("comfyui starting" in line for line in payload["lines"])


# ── Bare-list wrapping: runner_image_pulls / backend_list ───────────────────


@pytest.fixture
def list_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"payload": [{"id": "x"}]}

    class _MockResponse:
        status_code = 200
        text = ""

        def __init__(self, payload: Any) -> None:
            self._payload = payload

        def json(self) -> Any:
            return self._payload

    class _MockClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, params: Any = None, headers: Any = None) -> _MockResponse:
            return _MockResponse(captured["payload"])

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    return captured


@pytest.mark.asyncio
async def test_runner_image_pulls_wraps_bare_list(
    queue: ApprovalQueue, list_transport: dict[str, Any]
) -> None:
    list_transport["payload"] = [{"image_id": "hal0ai/toolbox-cpu", "state": "running"}]
    result = await admin.dispatch(
        tool="runner_image_pulls",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"pulls": list_transport["payload"], "count": 1}


@pytest.mark.asyncio
async def test_backend_list_wraps_bare_list(
    queue: ApprovalQueue, list_transport: dict[str, Any]
) -> None:
    list_transport["payload"] = [{"id": "rocm"}, {"id": "cpu"}]
    result = await admin.dispatch(
        tool="backend_list",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"backends": list_transport["payload"], "count": 2}


# ── Policy records: excluded labels + the runner-image slash-path skip ──────


def test_new_excluded_tool_labels_are_recorded_with_rationale() -> None:
    for label in (
        "approval_approve",
        "approval_deny",
        "secrets_crud",
        "installer_wizard",
        "proxmox_config",
        "auth_session",
        "agent_lifecycle",
    ):
        assert label in admin.EXCLUDED_TOOLS
        assert admin.EXCLUDED_TOOLS[label].strip()


def test_runner_image_per_id_routes_are_deliberately_unaliased() -> None:
    """image_id embeds a GHCR repo path ('/'), which _split_args rejects
    as a path-arg value — these routes stay unaliased rather than shipping
    a tool that always 400s. See the TOOL_NAME_ALIASES comment block."""
    alias_tools = {t for names in admin.TOOL_NAME_ALIASES.values() for t in names}
    for ghost in (
        "runner_image_pull",
        "runner_image_pull_status",
        "runner_image_pull_cancel",
        "runner_image_detail",
    ):
        assert ghost not in alias_tools


def test_comfyui_preview_and_profile_generate_route_are_handled_correctly() -> None:
    """comfyui_preview (binary image response) is never aliased; the
    unrelated invalid-workflow-launch guard route doesn't leak a second
    tool name either."""
    alias_tools = {t for names in admin.TOOL_NAME_ALIASES.values() for t in names}
    assert "comfyui_preview" not in alias_tools
    assert "comfyui_invalid_workflow_launch" not in alias_tools


# ── New TOOL_PARAM_HINTS reach the shared schema builder ────────────────────


def test_slot_edit_and_slot_create_hints_document_autoload_and_priority() -> None:
    for tool in ("slot_create", "slot_edit"):
        schema = admin.tool_param_schema(tool)
        assert "autoload" in schema["properties"]
        assert schema["properties"]["autoload"]["type"] == "boolean"
        assert "priority" in schema["properties"]
        assert schema["properties"]["priority"]["type"] == "integer"


def test_slot_set_defaults_hint_documents_model_subtable_fields() -> None:
    schema = admin.tool_param_schema("slot_set_defaults")
    assert "context_size" in schema["properties"]
    assert "n_gpu_layers" in schema["properties"]
    # slot_set_defaults merges into [model], not top-level SlotConfig —
    # autoload/priority belong to slot_create/slot_edit, not here.
    assert "autoload" not in schema["properties"]
    assert "priority" not in schema["properties"]


def test_mcp_server_action_and_service_action_hints_reach_schema() -> None:
    service_schema = admin.tool_param_schema("service_action")
    assert service_schema["required"] == ["service_id", "action"]
    action_schema = admin.tool_param_schema("mcp_server_action")
    assert set(action_schema["required"]) == {"server_id", "action"}
