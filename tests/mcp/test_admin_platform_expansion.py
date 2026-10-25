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

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

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
        def __init__(self, *, base_url: str, timeout: float, **_: Any) -> None:
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
    ("comfyui_models", {}, "http://t/api/comfyui/models"),
    ("comfyui_jobs", {}, "http://t/api/comfyui/jobs"),
    ("comfyui_files", {}, "http://t/api/comfyui/files"),
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
        "comfyui_model_install",
        {"model_id": "sdxl-lightning"},
        "POST",
        "http://t/api/comfyui/models/sdxl-lightning/install",
        None,
    ),
    (
        "comfyui_model_remove",
        {"model_id": "sdxl-lightning"},
        "DELETE",
        "http://t/api/comfyui/models/sdxl-lightning",
        None,
    ),
    (
        "comfyui_file_delete",
        {"subdir": "checkpoints", "relpath": "mine.safetensors"},
        "DELETE",
        "http://t/api/comfyui/files/checkpoints/mine.safetensors",
        None,
    ),
    (
        "comfyui_file_delete",
        {"subdir": "loras", "relpath": "styles%2Fmine.safetensors"},
        "DELETE",
        "http://t/api/comfyui/files/loras/styles%2Fmine.safetensors",
        None,
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
    ids=[f"{c[0]}-{i}" for i, c in enumerate(_GATED_CASES)],
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


# ── comfyui_file_delete: a nested relpath rides as %2F, a raw slash fails closed ──


@pytest.mark.asyncio
async def test_comfyui_file_delete_refuses_a_raw_slash_in_relpath(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """``_split_args`` keeps a '/' out of every path arg, so a nested file is named ``a%2Fb``."""
    queued = await admin.dispatch(
        tool="comfyui_file_delete",
        args={"subdir": "loras", "relpath": "styles/mine.safetensors"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )

    approved = await queue.approve(queued["approval_id"])

    assert approved["result"]["error"]["code"] == "mcp.missing_arg"
    assert mock_transport["calls"] == []


# The URL carries path args unquoted and the server percent-decodes them, so an approval card
# showing ``%6Bine.safetensors`` would delete ``kine.safetensors``. The tool therefore accepts
# only ``%2F`` / ``%2f`` as an escape and no URL delimiter, and says so before any approval.
_UNSAFE_FILE_DELETE_ARGS: list[tuple[str, str, str]] = [
    ("checkpoints", "%6Bine.safetensors", "escape"),
    ("checkpoints", "%6bine.safetensors", "lowercase escape"),
    ("checkpoints", "styles%2Fa%6B.safetensors", "escape beside a valid %2F"),
    ("checkpoints", "%2e%2e%2fx", "encoded dot dot"),
    ("checkpoints", "a%252Fb.safetensors", "double encoding"),
    ("checkpoints", "100%.safetensors", "bare percent"),
    ("checkpoints", "a%zz.safetensors", "percent without hex digits"),
    ("checkpoints", "a%2.safetensors", "truncated escape"),
    ("checkpoints", "junk.safetensors?x", "query delimiter"),
    ("checkpoints", "junk.safetensors#x", "fragment delimiter"),
    ("checkpoints", "?x/junk.safetensors", "leading query delimiter"),
    ("chec%6Bpoints", "mine.safetensors", "escape in subdir"),
    ("checkpoints?x", "mine.safetensors", "delimiter in subdir"),
    ("checkpoints#x", "mine.safetensors", "fragment in subdir"),
    ("checkpoints", 5, "non-string relpath"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subdir", "relpath", "why"),
    _UNSAFE_FILE_DELETE_ARGS,
    ids=[c[2] for c in _UNSAFE_FILE_DELETE_ARGS],
)
async def test_comfyui_file_delete_refuses_args_the_url_would_rewrite_before_any_approval(
    queue: ApprovalQueue,
    mock_transport: dict[str, Any],
    subdir: str,
    relpath: str,
    why: str,
) -> None:
    result = await admin.dispatch(
        tool="comfyui_file_delete",
        args={"subdir": subdir, "relpath": relpath},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "mcp.invalid_arg"
    assert queue.list_all() == []  # the operator is never asked to approve it
    assert mock_transport["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subdir", "relpath", "why"),
    _UNSAFE_FILE_DELETE_ARGS,
    ids=[c[2] for c in _UNSAFE_FILE_DELETE_ARGS],
)
async def test_comfyui_file_delete_executor_refuses_the_same_args(
    mock_transport: dict[str, Any], subdir: str, relpath: str, why: str
) -> None:
    """The check also guards the point the REST call is made, whoever calls it."""
    result = await admin._execute_tool(
        tool="comfyui_file_delete",
        args={"subdir": subdir, "relpath": relpath},
        bearer="t",
        base_url="http://t",
        memory_dispatcher=None,
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "mcp.invalid_arg"
    assert mock_transport["calls"] == []


@pytest.mark.asyncio
async def test_approving_an_escaped_relpath_never_reaches_rest(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """The review's case: ``%6Bine.safetensors`` approved must not delete ``kine.safetensors``."""
    queued = await queue.enqueue(
        tool="comfyui_file_delete",
        args={"subdir": "checkpoints", "relpath": "%6Bine.safetensors"},
        client_id="pi",
        executor=lambda approved: admin._execute_tool(
            tool="comfyui_file_delete",
            args=approved,
            bearer="t",
            base_url="http://t",
            memory_dispatcher=None,
        ),
    )

    approved = await queue.approve(queued)

    assert approved["result"]["error"]["code"] == "mcp.invalid_arg"
    assert mock_transport["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("slash", ["%2F", "%2f"])
async def test_comfyui_file_delete_accepts_an_encoded_slash_in_either_case(
    queue: ApprovalQueue, mock_transport: dict[str, Any], slash: str
) -> None:
    result = await admin.dispatch(
        tool="comfyui_file_delete",
        args={"subdir": "loras", "relpath": f"styles{slash}a.safetensors"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "pending_approval"

    await queue.approve(result["approval_id"])

    method, url, _, _ = mock_transport["calls"][-1]
    assert (method, url) == (
        "DELETE",
        f"http://t/api/comfyui/files/loras/styles{slash}a.safetensors",
    )


def test_comfyui_file_delete_encoded_slash_reaches_the_route_as_a_slash(
    client: TestClient,
    mock_transport: dict[str, Any],
    queue: ApprovalQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the tool sends is what the route deletes: ``styles%2Fa`` is ``styles/a``."""
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui-data"))
    nested = tmp_path / "comfyui-data" / "models" / "loras" / "styles" / "a.safetensors"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"x")

    async def approve() -> str:
        result = await admin.dispatch(
            tool="comfyui_file_delete",
            args={"subdir": "loras", "relpath": "styles%2Fa.safetensors"},
            client_id="pi",
            bearer="t",
            base_url="http://t",
            approval_queue=queue,
        )
        await queue.approve(result["approval_id"])
        return str(mock_transport["calls"][-1][1])

    url = asyncio.run(approve())

    resp = client.delete(url.removeprefix("http://t"))
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "loras/styles/a.safetensors"}
    assert not nested.exists()


def test_comfyui_file_delete_is_wired_like_the_model_remove_it_sits_beside() -> None:
    assert "comfyui_file_delete" in admin.GATED_TOOLS
    assert admin.TOOL_NAME_ALIASES["DELETE:/api/comfyui/files/{subdir}/{relpath}"] == (
        "comfyui_file_delete",
    )
    assert admin._ANNOTATIONS["comfyui_file_delete"].destructiveHint is True
    description = admin.TOOL_DESCRIPTIONS["comfyui_file_delete"]
    assert "%2F" in description
    assert "catalog" in description
    assert admin.tool_param_schema("comfyui_file_delete")["required"] == ["subdir", "relpath"]


# ── comfyui_job_cancel: an autonomous write, like model_pull_cancel ──────────


@pytest.mark.asyncio
async def test_comfyui_job_cancel_dispatches_autonomously(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    assert "comfyui_job_cancel" in admin.AUTONOMOUS_WRITE_TOOLS
    result = await admin.dispatch(
        tool="comfyui_job_cancel",
        args={"model_id": "sdxl-lightning"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "post"}
    assert queue.list_pending() == []
    method, url, _payload, _headers = mock_transport["calls"][-1]
    assert method == "POST"
    assert url == "http://t/api/comfyui/jobs/sdxl-lightning/cancel"


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
        def __init__(self, *, base_url: str, timeout: float, **_: Any) -> None:
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
        def __init__(self, *, base_url: str, timeout: float, **_: Any) -> None:
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


def test_slot_edit_hint_points_tuning_at_the_profile() -> None:
    """ADR-0029: tuning is not a slot field. ``slot_edit`` documents the
    ``profile`` pointer and no longer advertises a context ceiling."""
    schema = admin.tool_param_schema("slot_edit")
    assert "profile" in schema["properties"]
    model_desc = schema["properties"]["model"]["description"]
    assert "context_size" not in model_desc
    assert "profile" in model_desc
    desc = admin.TOOL_DESCRIPTIONS["slot_edit"]
    assert "profile_update" in desc
    assert "ctx-size" not in desc


def test_slot_create_hint_documents_the_profile() -> None:
    schema = admin.tool_param_schema("slot_create")
    assert "profile" in schema["properties"]
    assert "context_size" not in admin.TOOL_DESCRIPTIONS["slot_create"]


def test_model_edit_hint_documents_the_top_level_file_facts() -> None:
    schema = admin.tool_param_schema("model_edit")
    assert schema["required"] == ["model_id"]
    props = schema["properties"]
    for fact in ("chat_template", "tokenizer_repo", "mtp", "jinja"):
        assert fact in props, fact
        assert "File fact" in props[fact]["description"], fact
    assert props["mtp"]["type"] == ["boolean", "null"]
    assert props["jinja"]["type"] == ["boolean", "null"]
    assert props["chat_template"]["type"] == ["string", "null"]
    # The nested table is gone from the hint, and the tool description says so.
    assert "defaults" not in props
    desc = admin.TOOL_DESCRIPTIONS["model_edit"]
    assert "model.defaults_removed" in desc
    assert "profile_update" in desc


@pytest.mark.parametrize("tool", ["profile_create", "profile_update"])
def test_profile_write_hints_carry_the_profile_tune(tool: str) -> None:
    """The profile owns every tune: ctx_size, thinking, vision (auto|off), speculation (auto|off|ngram)
    and the runner — and no longer device_class / backend / quant / image."""
    props = admin.tool_param_schema(tool)["properties"]
    for field in ("flags", "ctx_size", "enable_thinking", "vision", "speculation", "runner"):
        assert field in props, field
    assert props["vision"]["enum"] == ["auto", "off"]
    assert props["speculation"]["enum"] == ["auto", "off", "ngram"]
    assert "mtp" not in props
    for gone in ("device_class", "backend", "quant", "image"):
        assert gone not in props, gone
    desc = admin.TOOL_DESCRIPTIONS[tool]
    assert "device class" not in desc
    assert "image" not in desc


def test_profile_create_requires_a_name() -> None:
    assert admin.tool_param_schema("profile_create")["required"] == ["name"]
    assert admin.tool_param_schema("profile_update")["required"] == ["name"]


def test_profile_generate_description_names_the_new_fields() -> None:
    desc = admin.TOOL_DESCRIPTIONS["profile_generate"]
    for field in ("ctx_size", "enable_thinking", "vision", "speculation"):
        assert field in desc


def test_mcp_server_action_and_service_action_hints_reach_schema() -> None:
    service_schema = admin.tool_param_schema("service_action")
    assert service_schema["required"] == ["service_id", "action"]
    action_schema = admin.tool_param_schema("mcp_server_action")
    assert set(action_schema["required"]) == {"server_id", "action"}
