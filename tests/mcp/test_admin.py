"""Unit tests for :mod:`hal0.mcp.admin` tool catalog + dispatch.

* Tool registration — every ADR-0004 §4 tool ends up on the FastMCP
  instance with the right description.
* Autonomous read tool calls dispatch httpx with the agent's Bearer.
* Autonomous write tool runs immediately (no approval enqueue).
* Gated tool returns ``{status:"pending_approval", approval_id:...}``
  and an entry lands in the queue.
* ``memory_delete`` is gated only when ``len(ids) > 1``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def mock_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch httpx.AsyncClient so REST calls are observable + no network."""

    captured: dict[str, Any] = {"calls": []}

    class _MockResponse:
        status_code = 200

        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload
            self.text = ""

        def json(self) -> dict[str, Any]:
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


def test_classification_buckets_match_adr_0004() -> None:
    """Every documented ADR-0004 §4 tool lives in exactly one bucket."""
    read = admin.AUTONOMOUS_READ_TOOLS
    write = admin.AUTONOMOUS_WRITE_TOOLS
    gated = admin.GATED_TOOLS
    # No overlap.
    assert read.isdisjoint(write)
    assert read.isdisjoint(gated)
    # memory_delete is in autonomous_write (the gating branches on args).
    assert "memory_delete" in write
    # ADR-0004 §4 destructives.
    for t in (
        "model_pull",
        "model_delete",
        "slot_create",
        "slot_delete",
        "slot_restart",
        "capability_set",
        "config_write",
        "provider_credential_write",
    ):
        assert t in gated
    # `logs_tail` was promoted from autonomous-read to GATED per
    # security review MED-1 — it stays gated until the journald
    # redactor in routes/logs.py covers Bearer + X-API-Key + provider
    # keys per ADR-0004 §7. slot_logs proxies the same journald
    # surface per-unit, so it carries the same classification.
    assert "logs_tail" in gated
    assert "slot_logs" in gated
    # ADR-0004 §4 reads.
    for t in (
        "slot_list",
        "slot_status",
        "model_list",
        "hardware_probe",
        "capability_list",
        "provider_list",
        "version_info",
    ):
        assert t in read


def test_is_gated_memory_delete_branches_on_id_count() -> None:
    assert admin.is_gated("memory_delete", {"ids": ["a"]}) is False
    assert admin.is_gated("memory_delete", {"ids": ["a", "b"]}) is True
    assert admin.is_gated("memory_add", {"text": "x", "dataset": "d"}) is False
    assert admin.is_gated("model_pull", {"model_id": "x"}) is True


@pytest.mark.asyncio
async def test_build_server_registers_full_catalog(queue: ApprovalQueue) -> None:
    server = admin.build_server(approval_queue=queue, base_url="http://t")
    tools = await server.list_tools()
    registered = {t.name for t in tools}
    expected = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    assert expected.issubset(registered)


def test_every_tool_has_annotations() -> None:
    """Every tool registered with FastMCP must carry the four MCP hints
    (readOnly / destructive / idempotent / openWorld). New tools added
    to the catalog without an annotation row trip this guard."""
    catalog = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    missing = catalog - admin._ANNOTATIONS.keys()
    assert not missing, f"tools missing MCP annotations: {sorted(missing)}"
    for name, ann in admin._ANNOTATIONS.items():
        assert ann.readOnlyHint is not None, f"{name}: readOnlyHint unset"
        assert ann.destructiveHint is not None, f"{name}: destructiveHint unset"
        assert ann.idempotentHint is not None, f"{name}: idempotentHint unset"
        assert ann.openWorldHint is not None, f"{name}: openWorldHint unset"


def test_destructive_tools_match_gated_destructive_set() -> None:
    """ADR-0004 §4's "destructive" classification must match the
    destructiveHint annotations exactly. The annotation is the
    client-facing surface; ADR-0004 is the policy. They cannot drift."""
    destructive_per_adr = {
        "model_delete",
        "slot_delete",
        "memory_delete",
        "stack_delete",
        "profile_delete",
        "upstream_delete",
        # §4.3 tier-a buildout: both remove a resource outright.
        # model_pull_delete stays autonomous (clears a TERMINAL job's
        # bookkeeping only, re-pullable); bench_queue_delete is gated
        # (drops a pending cell before it runs).
        "model_pull_delete",
        "bench_queue_delete",
    }
    destructive_per_annotation = {
        name for name, ann in admin._ANNOTATIONS.items() if ann.destructiveHint
    }
    assert destructive_per_annotation == destructive_per_adr


def test_open_world_tools_are_the_hf_surface_only() -> None:
    """Exactly three tools reach outside hal0's own surface — all of
    them HuggingFace-facing: pull downloads weights, update re-pulls in
    place, inspect fetches repo metadata. Anything else with
    openWorldHint=True needs a deliberate ADR update."""
    open_world = {name for name, ann in admin._ANNOTATIONS.items() if ann.openWorldHint}
    assert open_world == {"model_pull", "model_update", "model_inspect"}


@pytest.mark.asyncio
async def test_registered_tools_carry_their_annotations(queue: ApprovalQueue) -> None:
    """The annotation table must actually reach FastMCP's tool list —
    not just sit in a dict no one looks at."""
    server = admin.build_server(approval_queue=queue, base_url="http://t")
    tools = await server.list_tools()
    by_name = {t.name: t for t in tools}
    sample = "model_delete"  # destructive — easiest to spot a regression on
    assert by_name[sample].annotations is not None
    assert by_name[sample].annotations.destructiveHint is True
    assert by_name[sample].annotations.readOnlyHint is False


def test_tool_param_schema_is_the_shared_flat_shape() -> None:
    """The shared builder yields path args as required strings, merges
    curated body hints, and stays open for undeclared fields — the exact
    shape the dashboard agent chat pins on its side."""
    pull = admin.tool_param_schema("model_pull")
    assert pull["required"] == ["model_id"]
    assert pull["properties"]["model_id"]["type"] == "string"
    # Curated body hint from TOOL_PARAM_HINTS reached the schema.
    assert "hf_repo" in pull["properties"]
    assert pull["additionalProperties"] is True
    # A no-arg read has empty properties/required but still open.
    reads = admin.tool_param_schema("profile_list")
    assert reads["required"] == []
    assert reads["properties"] == {}


@pytest.mark.asyncio
async def test_build_server_advertises_shared_param_schema(queue: ApprovalQueue) -> None:
    """tools/list must carry the shared per-tool schema under the nested
    ``args`` object — not the opaque ``{args: object}`` FastMCP derives
    from the passthrough signature. Same source of truth as the chat."""
    server = admin.build_server(approval_queue=queue, base_url="http://t")
    by_name = {t.name: t for t in await server.list_tools()}

    pull = by_name["model_pull"].inputSchema
    inner = pull["properties"]["args"]
    assert inner["properties"]["model_id"]["type"] == "string"
    assert "hf_repo" in inner["properties"]
    assert inner["required"] == ["model_id"]
    assert inner["additionalProperties"] is True
    # A tool with a required field makes the nested ``args`` object required.
    assert pull["required"] == ["args"]

    # A no-arg read leaves ``args`` optional.
    assert by_name["profile_list"].inputSchema["required"] == []


def test_catalog_validation_catches_param_hint_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TOOL_PARAM_HINTS entry naming a non-catalog tool must fail import
    validation rather than advertise a schema for a tool that isn't there."""
    monkeypatch.setitem(admin.TOOL_PARAM_HINTS, "ghost_tool", {"properties": {}})
    with pytest.raises(RuntimeError, match="ghost_tool"):
        admin._validate_catalog()


@pytest.mark.asyncio
async def test_autonomous_read_dispatches_get_with_bearer(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="slot_list",
        args={},
        client_id="pi",
        bearer="token-abc",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "get"}
    call = mock_transport["calls"][-1]
    method, url, _params, headers = call
    assert method == "GET"
    assert url == "http://t/api/slots"
    assert headers["Authorization"] == "Bearer token-abc"
    assert headers["X-Requested-With"] == "XMLHttpRequest"


@pytest.mark.asyncio
async def test_autonomous_path_arg_resolution(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """slot_status carries ``name`` as a URL path arg."""
    await admin.dispatch(
        tool="slot_status",
        args={"name": "primary"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    _, url, _, _ = mock_transport["calls"][-1]
    assert url == "http://t/api/slots/primary"


@pytest.mark.asyncio
async def test_autonomous_write_runs_now_not_queued(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """model_swap is autonomous; it goes through REST immediately."""
    result = await admin.dispatch(
        tool="model_swap",
        args={"name": "primary", "model_id": "qwen3:0.6b"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "post"}
    # Queue stays empty.
    assert queue.list_pending() == []
    # POST hits the live slot-swap route (see admin.py drift note).
    method, url, payload, _ = mock_transport["calls"][-1]
    assert method == "POST"
    assert url == "http://t/api/slots/primary/swap"
    assert payload == {"model_id": "qwen3:0.6b"}


@pytest.mark.asyncio
async def test_gated_tool_returns_pending_approval_and_enqueues(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="model_pull",
        args={"model_id": "qwen3:0.6b"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "pending_approval"
    assert isinstance(result["approval_id"], str)
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0]["tool"] == "model_pull"
    # REST was NOT hit while the call sits pending.
    assert mock_transport["calls"] == []


@pytest.mark.asyncio
async def test_gated_executor_hits_rest_on_approve(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="slot_delete",
        args={"name": "scratch"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    aid = result["approval_id"]
    await queue.approve(aid)
    method, url, _params, headers = mock_transport["calls"][-1]
    assert method == "DELETE"
    assert url == "http://t/api/slots/scratch"
    assert headers["Authorization"] == "Bearer t"


@pytest.mark.asyncio
async def test_memory_delete_single_id_autonomous(queue: ApprovalQueue) -> None:
    """Single-id memory_delete must run through the dispatcher, not enqueue."""
    dispatcher = AsyncMock(return_value={"status": "ok", "deleted": 1})
    result = await admin.dispatch(
        tool="memory_delete",
        args={"ids": ["a"]},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
        memory_dispatcher=dispatcher,
    )
    assert result == {"status": "ok", "deleted": 1}
    assert queue.list_pending() == []
    dispatcher.assert_awaited_once()


@pytest.mark.asyncio
async def test_memory_delete_bulk_gated(queue: ApprovalQueue) -> None:
    dispatcher = AsyncMock(return_value={"status": "ok", "deleted": 2})
    result = await admin.dispatch(
        tool="memory_delete",
        args={"ids": ["a", "b"]},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
        memory_dispatcher=dispatcher,
    )
    assert result["status"] == "pending_approval"
    assert queue.list_pending()[0]["tool"] == "memory_delete"
    dispatcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_tool_returns_typed_error(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="rm_rf_root",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "mcp.unknown_tool"


@pytest.mark.asyncio
async def test_missing_path_arg_returns_typed_error(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="slot_status",
        args={},  # missing 'name'
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "mcp.missing_arg"


@pytest.fixture
def list_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch httpx.AsyncClient so GET returns a bare JSON *list*.

    Mirrors the real /api/slots and /api/providers routes, which return
    ``list[dict]`` rather than a top-level object. Used to prove the MCP
    wrapper wraps the list into a dict (the DictModel-validation fix).
    """
    captured: dict[str, Any] = {"payload": [{"name": "primary"}, {"name": "embed"}]}

    class _MockResponse:
        status_code = 200

        def __init__(self, payload: Any) -> None:
            self._payload = payload
            self.text = ""

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
async def test_slot_list_wraps_bare_list_into_dict(
    queue: ApprovalQueue, list_transport: dict[str, Any]
) -> None:
    """slot_list's REST route returns a bare list; the MCP tool must
    return a top-level dict (FastMCP's result model rejects lists with
    'Input should be a valid dictionary')."""
    result = await admin.dispatch(
        tool="slot_list",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert isinstance(result, dict)
    assert result == {"slots": [{"name": "primary"}, {"name": "embed"}], "count": 2}


@pytest.mark.asyncio
async def test_provider_list_wraps_bare_list_into_dict(
    queue: ApprovalQueue, list_transport: dict[str, Any]
) -> None:
    """provider_list's REST route returns a bare list; same dict-wrap
    requirement as slot_list."""
    list_transport["payload"] = [{"name": "openrouter"}]
    result = await admin.dispatch(
        tool="provider_list",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert isinstance(result, dict)
    assert result == {"providers": [{"name": "openrouter"}], "count": 1}


@pytest.mark.asyncio
async def test_list_wrap_does_not_touch_dict_payloads(
    queue: ApprovalQueue, list_transport: dict[str, Any]
) -> None:
    """A non-list REST payload (e.g. an error envelope) for slot_list
    round-trips unchanged — the wrapper never masks a dict response."""
    list_transport["payload"] = {"status": "error", "http_status": 503}
    result = await admin.dispatch(
        tool="slot_list",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"status": "error", "http_status": 503}


def test_wrap_list_payload_generic_fallback_and_dict_passthrough() -> None:
    """Any bare-list payload wraps (named key when mapped, ``items``
    otherwise) so a newly-mapped bare-list route can't crash FastMCP's
    dict result model; dict payloads always pass through untouched."""
    data = [{"id": "x"}]
    assert admin._wrap_list_payload("upstream_list", data) == {"upstreams": data, "count": 1}
    assert admin._wrap_list_payload("bench_runs", data) == {"items": data, "count": 1}
    assert admin._wrap_list_payload("hardware_probe", {"ok": 1}) == {"ok": 1}
    assert admin._wrap_list_payload("model_list", {"data": data}) == {"data": data}


def test_catalog_validation_catches_rest_map_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """A classified tool losing its _REST_MAP row must fail at import
    validation, not at call time with an opaque envelope."""
    admin._validate_catalog()  # coherent as shipped
    monkeypatch.delitem(admin._REST_MAP, "slot_list")
    with pytest.raises(RuntimeError, match="slot_list"):
        admin._validate_catalog()


def test_catalog_validation_catches_path_arg_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """_PATH_ARGS must mirror the {placeholders} in each route template."""
    monkeypatch.delitem(admin._PATH_ARGS, "model_pull")
    with pytest.raises(RuntimeError, match="model_pull"):
        admin._validate_catalog()


def test_catalog_validation_catches_unsupported_http_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A _REST_MAP entry whose method _call_rest can't forward (typo, or
    a new verb nobody wired a branch for) must fail import validation —
    this is the guard that would have caught upstream_update's PATCH
    route shipping mapped but unforwardable, instead of the gated tool
    surviving operator approval and raising deep in ``_call_rest``."""
    admin._validate_catalog()  # coherent as shipped
    monkeypatch.setitem(admin._REST_MAP, "slot_list", ("TRACE", "/api/slots"))
    with pytest.raises(RuntimeError, match="slot_list"):
        admin._validate_catalog()


def test_catalog_validation_catches_description_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool classified but missing from TOOL_DESCRIPTIONS (or vice
    versa) fails import validation — registration iterates that dict, so
    a gap would otherwise silently drop the tool from tools/list."""
    monkeypatch.setattr(admin, "GATED_TOOLS", admin.GATED_TOOLS | {"ghost_tool"})
    with pytest.raises(RuntimeError, match="ghost_tool"):
        admin._validate_catalog()


async def test_upstream_update_forwards_as_patch(
    mock_transport: dict[str, Any],
) -> None:
    """upstream_update maps to a PATCH route (_REST_MAP). Regression for
    the bug where _REST_MAP declared PATCH but _call_rest only knew
    GET/DELETE/POST/PUT — the gated tool survived operator approval and
    then raised ``ValueError: unsupported HTTP method`` inside the
    executor. Exercised via _execute_tool, the same entry point the
    approval-queue executor calls once an operator approves."""
    result = await admin._execute_tool(
        tool="upstream_update",
        args={"name": "openai", "base_url": "https://api.openai.com"},
        bearer="t",
        base_url="http://t",
        memory_dispatcher=None,
    )
    assert result == {"ok": "patch"}
    method, url, body, _headers = mock_transport["calls"][-1]
    assert method == "PATCH"
    assert url == "http://t/api/upstreams/openai"
    assert body == {"base_url": "https://api.openai.com"}


def test_model_edit_and_model_update_route_to_distinct_endpoints() -> None:
    """model_edit is the metadata PUT; model_update is the in-place HF
    re-pull POST. The two are easy to cross-wire — pin them apart."""
    assert admin._REST_MAP["model_edit"] == ("PUT", "/api/models/{model_id}")
    assert admin._REST_MAP["model_update"] == ("POST", "/api/models/{model_id}/update")


def test_model_scan_is_a_write_and_preview_is_a_read() -> None:
    """Scanning registers new files (a mutation); only the dry-run
    preview classifies as an autonomous read."""
    assert "model_scan" in admin.AUTONOMOUS_WRITE_TOOLS
    assert "model_scan_preview" in admin.AUTONOMOUS_READ_TOOLS
    assert "model_scan" not in admin.AUTONOMOUS_READ_TOOLS


# ── per-persona ToolPolicy overlay ───────────────────────────────────────────


def _policy(**kw: Any) -> admin.ToolPolicy:
    return admin.ToolPolicy(**kw)


def test_policy_classify_default_ask_mirrors_server_verdict() -> None:
    p = _policy()
    assert p.classify("slot_load", server_gated=False) == "run"
    assert p.classify("model_pull", server_gated=True) == "gated"


def test_policy_tools_allowed_hides_everything_else() -> None:
    p = _policy(tools_allowed=("slot_*", "model_list"))
    assert p.allows("slot_create")
    assert p.allows("model_list")
    assert not p.allows("model_pull")
    assert p.classify("model_pull", server_gated=True) == "denied"


def test_policy_require_approval_tightens_autonomous_tool() -> None:
    p = _policy(require_approval=("slot_load",))
    assert p.classify("slot_load", server_gated=False) == "gated"


def test_policy_auto_approve_loosens_gated_tool_but_not_floor() -> None:
    p = _policy(auto_approve=("model_pull", "model_delete"))
    assert p.classify("model_pull", server_gated=True) == "run"
    # model_delete is on POLICY_NO_LOOSEN — the grant is ignored.
    assert p.classify("model_delete", server_gated=True) == "gated"


def test_policy_default_auto_approve_respects_floor() -> None:
    p = _policy(default_policy="auto-approve")
    assert p.classify("stack_apply", server_gated=True) == "run"
    assert p.classify("config_write", server_gated=True) == "gated"


def test_policy_require_approval_beats_auto_approve() -> None:
    p = _policy(auto_approve=("slot_*",), require_approval=("slot_edit",))
    assert p.classify("slot_edit", server_gated=False) == "gated"


def test_policy_never_refuses_gated_and_runs_autonomous() -> None:
    p = _policy(default_policy="never")
    assert p.classify("model_pull", server_gated=True) == "refused"
    assert p.classify("slot_list", server_gated=False) == "run"


def test_policy_from_persona_duck_types() -> None:
    class _Approval:
        default_policy = "ask"
        auto_approve = ("model_pull",)
        require_approval = ("slot_load",)

    class _Persona:
        tools_allowed = ("slot_*", "model_*")
        approval = _Approval()

    p = admin.ToolPolicy.from_persona(_Persona())
    assert p.classify("model_pull", server_gated=True) == "run"
    assert p.classify("slot_load", server_gated=False) == "gated"
    assert p.classify("stack_apply", server_gated=True) == "denied"


@pytest.mark.asyncio
async def test_dispatch_policy_denied_returns_typed_error(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="model_pull",
        args={"model_id": "m"},
        client_id="brain",
        bearer=None,
        base_url="http://t",
        approval_queue=queue,
        policy=_policy(tools_allowed=("slot_*",)),
    )
    assert result["error"]["code"] == "mcp.tool_not_allowed"
    assert queue.list_pending() == []


@pytest.mark.asyncio
async def test_dispatch_policy_never_refuses_instead_of_queueing(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="model_pull",
        args={"model_id": "m"},
        client_id="brain",
        bearer=None,
        base_url="http://t",
        approval_queue=queue,
        policy=_policy(default_policy="never"),
    )
    assert result["error"]["code"] == "mcp.gated_tool_refused"
    assert queue.list_pending() == []


@pytest.mark.asyncio
async def test_dispatch_policy_loosened_pull_runs_immediately(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """auto_approve=model_pull → no approval hop; REST POST fires now."""
    result = await admin.dispatch(
        tool="model_pull",
        args={"model_id": "m"},
        client_id="brain",
        bearer="tok",
        base_url="http://t",
        approval_queue=queue,
        policy=_policy(auto_approve=("model_pull",)),
    )
    assert result == {"ok": "post"}
    assert queue.list_pending() == []
    methods = [c[0] for c in mock_transport["calls"]]
    assert methods == ["POST"]


@pytest.mark.asyncio
async def test_dispatch_policy_floor_still_queues_despite_grant(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="model_delete",
        args={"model_id": "m"},
        client_id="brain",
        bearer=None,
        base_url="http://t",
        approval_queue=queue,
        policy=_policy(auto_approve=("model_delete",)),
    )
    assert result["status"] == "pending_approval"
    assert [p["tool"] for p in queue.list_pending()] == ["model_delete"]


@pytest.mark.asyncio
async def test_dispatch_policy_tightened_autonomous_tool_queues(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="slot_load",
        args={"name": "agent"},
        client_id="brain",
        bearer=None,
        base_url="http://t",
        approval_queue=queue,
        policy=_policy(require_approval=("slot_load",)),
    )
    assert result["status"] == "pending_approval"
    assert [p["tool"] for p in queue.list_pending()] == ["slot_load"]


@pytest.mark.asyncio
async def test_dispatch_policy_bulk_memory_delete_stays_gated(queue: ApprovalQueue) -> None:
    """memory_delete is on the floor — a grant can't disarm the bulk gate."""
    result = await admin.dispatch(
        tool="memory_delete",
        args={"ids": ["a", "b"]},
        client_id="brain",
        bearer=None,
        base_url="http://t",
        approval_queue=queue,
        policy=_policy(auto_approve=("memory_delete",)),
    )
    assert result["status"] == "pending_approval"


# ── §4.3 tier-a buildout: rename/by-id/by-name/resolved/state/defaults, ─────
# model default+duplicate, pulls delete, system-info, bench queue delete,
# memory_recall, and the memory_search/memory_list read reclassification.


def test_memory_reads_are_autonomous_read_not_write() -> None:
    """memory_search/memory_list have always carried readOnlyHint=True —
    the classification bucket just hadn't caught up (spec §4.3)."""
    assert "memory_search" in admin.AUTONOMOUS_READ_TOOLS
    assert "memory_list" in admin.AUTONOMOUS_READ_TOOLS
    assert "memory_search" not in admin.AUTONOMOUS_WRITE_TOOLS
    assert "memory_list" not in admin.AUTONOMOUS_WRITE_TOOLS


def test_memory_recall_is_catalogued_as_autonomous_read() -> None:
    """memory_recall's handler already existed in hal0.mcp.memory —
    §4.3 adds it to the admin catalog so tools/list actually advertises
    it (the module docstring already promised 'every tool')."""
    assert "memory_recall" in admin.AUTONOMOUS_READ_TOOLS
    assert admin._ANNOTATIONS["memory_recall"].readOnlyHint is True


@pytest.mark.asyncio
async def test_memory_recall_dispatches_through_memory_dispatcher(queue: ApprovalQueue) -> None:
    """memory_recall bypasses REST entirely — same in-process path as
    the other memory_* tools."""
    dispatcher = AsyncMock(return_value={"status": "ok", "results": []})
    result = await admin.dispatch(
        tool="memory_recall",
        args={"query": "what did we decide about ports"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
        memory_dispatcher=dispatcher,
    )
    assert result == {"status": "ok", "results": []}
    dispatcher.assert_awaited_once_with(
        "memory_recall", {"query": "what did we decide about ports"}
    )


@pytest.mark.asyncio
async def test_slot_by_name_and_by_id_dispatch_get(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    await admin.dispatch(
        tool="slot_by_name",
        args={"name": "agent"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    method, url, _, _ = mock_transport["calls"][-1]
    assert (method, url) == ("GET", "http://t/api/slots/by-name/agent")

    await admin.dispatch(
        tool="slot_by_id",
        args={"slot_id": "3"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    method, url, _, _ = mock_transport["calls"][-1]
    assert (method, url) == ("GET", "http://t/api/slots/by-id/3")


@pytest.mark.asyncio
async def test_slot_resolved_and_state_dispatch_get(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    await admin.dispatch(
        tool="slot_resolved",
        args={"name": "agent"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    method, url, _, _ = mock_transport["calls"][-1]
    assert (method, url) == ("GET", "http://t/api/slots/agent/resolved")

    await admin.dispatch(
        tool="slot_state",
        args={"name": "agent"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    method, url, _, _ = mock_transport["calls"][-1]
    assert (method, url) == ("GET", "http://t/api/slots/agent/state")


@pytest.mark.asyncio
async def test_slot_set_defaults_dispatches_patch_autonomously(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """PATCH .../defaults is autonomous-write, same blast radius class as
    slot_edit's PUT — and exercises the generic PATCH verb support that
    landed in Phase 0 (_REST_VERB_PAYLOAD_KWARG)."""
    result = await admin.dispatch(
        tool="slot_set_defaults",
        args={"name": "agent", "ctx_size": 8192},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "patch"}
    assert queue.list_pending() == []
    method, url, payload, _ = mock_transport["calls"][-1]
    assert method == "PATCH"
    assert url == "http://t/api/slots/agent/defaults"
    assert payload == {"ctx_size": 8192}


@pytest.mark.asyncio
async def test_slot_rename_is_gated(queue: ApprovalQueue, mock_transport: dict[str, Any]) -> None:
    result = await admin.dispatch(
        tool="slot_rename",
        args={"name": "agent", "new_name": "steward"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "pending_approval"
    assert mock_transport["calls"] == []
    aid = result["approval_id"]
    await queue.approve(aid)
    method, url, payload, _ = mock_transport["calls"][-1]
    assert method == "POST"
    assert url == "http://t/api/slots/agent/rename"
    assert payload == {"new_name": "steward"}


@pytest.mark.asyncio
async def test_model_set_default_and_duplicate_are_autonomous_write(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="model_set_default",
        args={"model_id": "qwen3-4b"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "post"}
    assert queue.list_pending() == []
    method, url, payload, _ = mock_transport["calls"][-1]
    assert (method, url) == ("POST", "http://t/api/models/qwen3-4b/default")
    assert payload == {}

    result = await admin.dispatch(
        tool="model_duplicate",
        args={"model_id": "qwen3-4b", "new_id": "qwen3-4b-cpu"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "post"}
    assert queue.list_pending() == []
    method, url, payload, _ = mock_transport["calls"][-1]
    assert (method, url) == ("POST", "http://t/api/models/qwen3-4b/duplicate")
    assert payload == {"new_id": "qwen3-4b-cpu"}


@pytest.mark.asyncio
async def test_model_pull_delete_is_autonomous_write(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """Clears a TERMINAL pull job's bookkeeping only — low blast radius,
    stays autonomous like model_pull_cancel (not gated like model_delete)."""
    result = await admin.dispatch(
        tool="model_pull_delete",
        args={"model_id": "qwen3-4b"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "delete"}
    assert queue.list_pending() == []
    method, url, _, _ = mock_transport["calls"][-1]
    assert (method, url) == ("DELETE", "http://t/api/models/pulls/qwen3-4b")


@pytest.mark.asyncio
async def test_system_info_is_autonomous_read(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="system_info",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "get"}
    method, url, _, _ = mock_transport["calls"][-1]
    assert (method, url) == ("GET", "http://t/api/system-info")


@pytest.mark.asyncio
async def test_bench_queue_delete_is_gated(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="bench_queue_delete",
        args={"item_id": "abcd1234"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "pending_approval"
    assert mock_transport["calls"] == []
    aid = result["approval_id"]
    await queue.approve(aid)
    method, url, _, _ = mock_transport["calls"][-1]
    assert (method, url) == ("DELETE", "http://t/api/benchmarks/queue/abcd1234")


def test_excluded_tools_never_collide_with_the_live_catalog() -> None:
    """EXCLUDED_TOOLS documents deliberate non-exposure (tier b, §4.3) —
    a label landing in both tables would mean a tool is simultaneously
    'shipped' and 'excluded', which _validate_catalog must reject."""
    catalog = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    assert set(admin.EXCLUDED_TOOLS).isdisjoint(catalog)
    assert all(reason.strip() for reason in admin.EXCLUDED_TOOLS.values())


def test_validate_catalog_catches_excluded_tools_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(admin.EXCLUDED_TOOLS, "slot_list", "bogus collision")
    with pytest.raises(RuntimeError, match="slot_list"):
        admin._validate_catalog()
