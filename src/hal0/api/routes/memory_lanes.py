"""Memory showcase surface — engine passthrough, lanes, agents, models.

Four endpoints backing the memory-showcase UI branch:

* ``/api/memory/engine/{path:path}`` — a narrowly allowlisted passthrough to
  the Hindsight REST API, used where the dashboard needs a raw upstream
  shape the composed admin routes don't already expose (bank-template
  schema, version/health probes). Table-driven allowlist, not a wildcard
  proxy — same posture as :mod:`hal0.api.routes.memory_admin`.
* ``GET /api/memory/lanes`` — decision-model health + LLM lane config +
  per-bank queue depth, for the showcase's "lanes" status board.
* ``GET /api/memory/agents`` — coding-agent banks + the hindsight
  coding-agents installer one-liner.
* ``GET /api/memory/models`` — the configured embeddings/reranker/decision
  models, with live decision-server reachability.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request, Response

from hal0.api.routes.memory import MemoryUnavailable
from hal0.config.loader import load_hal0_config
from hal0.errors import BadRequest, Forbidden, Hal0Error, NotFound

router = APIRouter()

log = logging.getLogger(__name__)

#: Allowlisted upstream path prefixes for the raw engine passthrough.
#: Exact matches or, for the banks tree, any sub-path.
_EXACT_PATHS = {"v1/bank-template-schema", "version", "health"}
_BANKS_PREFIX = "v1/default/banks"

_DEFAULT_TIMEOUT_S = 30.0
_REFLECT_TIMEOUT_S = 600.0


class MemoryEngineUnreachable(Hal0Error):
    code = "memory.engine_unreachable"
    status = 503


class MemoryEngineError(Hal0Error):
    code = "memory.engine_error"
    status = 502


#: The only engine paths DELETE may reach through the passthrough: a single
#: directive or mental model. Every bank-scoped delete (the bank itself, its
#: memories, config, documents) has a dedicated, classified route in
#: memory_admin — the #1024 bank wipe carries an echoed ?confirm= gate there —
#: and must not be reachable as a one-call generic DELETE here.
_DELETE_ALLOWED = re.compile(r"^v1/default/banks/[^/]+/(directives|mental-models)/[^/]+/?$")

#: Non-DELETE verbs that still destroy data (bulk delete / clear / reset
#: endpoints) are refused outright for the same reason.
_DESTRUCTIVE_VERB_SUFFIX = re.compile(r"/(delete|clear|reset|wipe|purge)/?$")


def _check_method(method: str, path: str) -> None:
    if method == "DELETE" and not _DELETE_ALLOWED.match(path):
        raise Forbidden(
            f"DELETE {path!r} is not allowed through the engine passthrough; "
            "use the dedicated /api/memory/banks/... route",
            code="memory.engine_delete_not_allowed",
        )
    if method != "GET" and _DESTRUCTIVE_VERB_SUFFIX.search(path):
        raise Forbidden(
            f"{method} {path!r} is a destructive engine endpoint and is not "
            "allowed through the passthrough",
            code="memory.engine_delete_not_allowed",
        )


def _allowed(path: str) -> bool:
    if ".." in path:
        return False
    if path in _EXACT_PATHS:
        return True
    return path == _BANKS_PREFIX or path.startswith(_BANKS_PREFIX + "/")


def _client(request: Request) -> Any:
    provider = getattr(request.app.state, "memory_provider", None)
    if provider is None:
        raise MemoryUnavailable("memory engine is not available on this hal0 instance")
    client = getattr(provider, "hindsight_client", None)
    if client is None:
        raise MemoryUnavailable("the memory engine passthrough requires the hindsight engine")
    return client


def _timeout_for(method: str, path: str) -> float:
    if method == "POST" and path.rstrip("/").endswith("/reflect"):
        return _REFLECT_TIMEOUT_S
    return _DEFAULT_TIMEOUT_S


async def _passthrough(request: Request, path: str, method: str) -> Response:
    if not _allowed(path):
        raise NotFound(f"no such engine path: {path!r}", code="memory.engine_path_not_allowed")
    _check_method(method, path)

    client = _client(request)

    raw = await request.body()
    json_body: Any | None = None
    if raw:
        import json as _json

        try:
            json_body = _json.loads(raw)
        except ValueError as exc:
            raise BadRequest("request body must be valid JSON") from exc

    params = dict(request.query_params)
    timeout_s = _timeout_for(method, path)

    try:
        data = await client.request_json(
            method,
            f"/{path}",
            params=params or None,
            json_body=json_body,
            timeout_s=timeout_s,
        )
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        try:
            detail: Any = exc.response.json()
        except ValueError:
            detail = {"body": exc.response.text[:500]}
        if not isinstance(detail, dict):
            detail = {"body": detail}
        if upstream_status >= 500:
            raise MemoryEngineError(
                f"hindsight returned {upstream_status}", details=detail
            ) from exc
        err = MemoryEngineError(f"hindsight returned {upstream_status}", details=detail)
        err.status = upstream_status
        raise err from exc
    except httpx.HTTPError as exc:
        raise MemoryEngineUnreachable(f"could not reach the memory engine: {exc}") from exc

    return Response(content=__import__("json").dumps(data), media_type="application/json")


@router.api_route("/engine/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def engine_passthrough(path: str, request: Request) -> Response:
    """Allowlisted raw passthrough to the Hindsight REST API."""
    return await _passthrough(request, path, request.method)


# ── /api/memory/lanes ───────────────────────────────────────────────────


async def _decision_health(decision_cfg: Any) -> dict[str, Any]:
    url = f"http://127.0.0.1:{decision_cfg.port}"
    out: dict[str, Any] = {"url": url, "reachable": False}
    if not decision_cfg.enabled or decision_cfg.provider == "off":
        out["reachable"] = False
        return out
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as http:
            resp = await http.get(f"{url}/health")
            resp.raise_for_status()
            body = resp.json()
    except Exception:
        return out
    out.update(body)
    out["reachable"] = True
    return out


def _lane_op(op_cfg: Any, lanes_cfg: Any) -> dict[str, Any]:
    return {
        "max_concurrent": op_cfg.max_concurrent or lanes_cfg.llm_max_concurrent,
        "model": op_cfg.virtual_model or "hal0/memory",
    }


def _bank_queue_row(bank_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    pending = 0
    processing = 0
    failed = 0
    oldest_pending_age_s: float | None = None
    now = time.time()
    for op in operations:
        status = str(op.get("status", "")).lower()
        if status in ("pending", "queued"):
            pending += 1
            created_at = op.get("created_at")
            age = _age_s(created_at, now)
            if age is not None and (oldest_pending_age_s is None or age > oldest_pending_age_s):
                oldest_pending_age_s = age
        elif status in ("processing", "running", "in_progress"):
            processing += 1
        elif status in ("failed", "error"):
            failed += 1
    return {
        "bank_id": bank_id,
        "pending": pending,
        "processing": processing,
        "failed": failed,
        "oldest_pending_age_s": oldest_pending_age_s,
    }


def _age_s(created_at: Any, now: float) -> float | None:
    if not created_at:
        return None
    try:
        from datetime import datetime

        ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return max(0.0, now - ts.timestamp())
    except Exception:
        return None


def _engine_env_report(memory_cfg: Any) -> dict[str, Any]:
    """The unit-env comparison (seam for tests; blocking — call off the loop)."""
    from hal0.memory.extraction_env import engine_env_report

    return engine_env_report(memory_cfg)


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _effective_llm(env: dict[str, str]) -> dict[str, Any]:
    """The lane values hindsight-api's unit actually carries (``None`` = unset)."""
    from hal0.memory.extraction_env import LANE_OP_ENV_PREFIX

    global_timeout = _int_or_none(env.get("HINDSIGHT_API_LLM_TIMEOUT"))
    per_op: dict[str, Any] = {}
    for op, prefix in LANE_OP_ENV_PREFIX.items():
        per_op[op] = {
            "max_concurrent": _int_or_none(env.get(f"HINDSIGHT_API_{prefix}_LLM_MAX_CONCURRENT")),
            "model": env.get(f"HINDSIGHT_API_{prefix}_LLM_MODEL")
            or env.get("HINDSIGHT_API_LLM_MODEL"),
            "timeout": _int_or_none(env.get(f"HINDSIGHT_API_{prefix}_LLM_TIMEOUT"))
            or global_timeout,
        }
    return {
        "model": env.get("HINDSIGHT_API_LLM_MODEL"),
        "max_concurrent": _int_or_none(env.get("HINDSIGHT_API_LLM_MAX_CONCURRENT")),
        "timeout": global_timeout,
        "per_op": per_op,
    }


@router.get("/lanes")
async def get_lanes(request: Request) -> dict[str, Any]:
    cfg = load_hal0_config()
    lanes_cfg = cfg.memory.lanes
    decision_cfg = cfg.memory.decision

    decision = await _decision_health(decision_cfg)

    # ``model`` / ``max_concurrent`` / ``timeout`` / ``per_op`` stay the
    # configured values (existing clients read them). #3520: ``effective`` is what the
    # hindsight-api unit really carries — a later-sorting drop-in can override
    # hal0's — and ``drift`` lists every managed key where the two disagree.
    # ``effective`` is ``None`` when the unit env cannot be read.
    llm: dict[str, Any] = {
        "model": "hal0/memory",
        "max_concurrent": lanes_cfg.llm_max_concurrent,
        "timeout": lanes_cfg.timeout_s,
        "per_op": {
            "retain": _lane_op(lanes_cfg.retain, lanes_cfg),
            "consolidation": _lane_op(lanes_cfg.consolidation, lanes_cfg),
            "reflect": _lane_op(lanes_cfg.reflect, lanes_cfg),
            "mental_model_refresh": _lane_op(lanes_cfg.mental_model_refresh, lanes_cfg),
        },
        "effective": None,
        "drift": [],
        "legacy_drop_in": None,
        "warning": None,
    }
    try:
        from hal0.memory.extraction_env import engine_env_warning

        report = await asyncio.to_thread(_engine_env_report, cfg.memory)
        if report.get("checked"):
            llm["effective"] = _effective_llm(report.get("effective") or {})
        llm["drift"] = report.get("drift") or []
        llm["legacy_drop_in"] = report.get("legacy_drop_in")
        llm["warning"] = engine_env_warning(report)
    except Exception as exc:  # status board: degrade, never 500
        log.debug("memory lanes: engine env report failed: %s", exc)

    banks: list[dict[str, Any]] = []
    provider = getattr(request.app.state, "memory_provider", None)
    client = getattr(provider, "hindsight_client", None) if provider is not None else None
    if client is not None:
        try:
            bank_resp = await client.request_json("GET", "/v1/default/banks")
            bank_ids = [
                b.get("bank_id") or b.get("id")
                for b in (bank_resp.get("banks") or bank_resp.get("items") or [])
            ]
            for bank_id in bank_ids:
                if not bank_id:
                    continue
                try:
                    ops_resp = await client.request_json(
                        "GET", f"/v1/default/banks/{bank_id}/operations"
                    )
                    ops = ops_resp.get("operations") or ops_resp.get("items") or []
                except Exception as exc:
                    banks.append({"bank_id": bank_id, "error": str(exc)})
                    continue
                banks.append(_bank_queue_row(bank_id, ops))
        except Exception:
            banks = []

    npu = None
    try:
        from hal0.api.routes.npu import npu_occupancy

        occ = await npu_occupancy(request)
        if occ and occ.get("present"):
            npu = occ
    except Exception:
        npu = None

    return {"decision": decision, "llm": llm, "banks": banks, "npu": npu}


# ── /api/memory/agents ──────────────────────────────────────────────────

_HARNESSES = [
    "claude-code",
    "codex",
    "opencode",
    "pi",
    "cursor-cli",
    "copilot-cli",
    "qwen-code",
]


def _public_hindsight_url(request: Request, cfg: Any) -> str:
    public_url = getattr(cfg.memory, "public_url", None)
    if public_url:
        return str(public_url)
    host = request.url.hostname or "127.0.0.1"
    return f"http://{host}:9177"


def _console_url(request: Request, cfg: Any) -> str | None:
    """Hindsight Control Plane console URL from ``[memory.console]``, or None.

    Returns None when the console is disabled. If ``[memory.console]`` has no
    explicit ``url``, derive one from ``memory.public_url`` (host) plus the
    console port; else fall back to the inbound request's hostname.
    """
    console = getattr(cfg.memory, "console", None)
    if console is None or not getattr(console, "enabled", False):
        return None
    url = getattr(console, "url", None)
    if url:
        return str(url)
    port = getattr(console, "port", 9999)
    public_url = getattr(cfg.memory, "public_url", None)
    host = None
    if public_url:
        parsed = urlparse(str(public_url))
        host = parsed.hostname
    if not host:
        host = request.url.hostname or "127.0.0.1"
    return f"http://{host}:{port}"


@router.get("/agents")
async def get_agents(request: Request) -> dict[str, Any]:
    cfg = load_hal0_config()
    bank_ids: list[str] = []
    provider = getattr(request.app.state, "memory_provider", None)
    client = getattr(provider, "hindsight_client", None) if provider is not None else None
    if client is not None:
        try:
            bank_resp = await client.request_json("GET", "/v1/default/banks")
            all_ids = [
                b.get("bank_id") or b.get("id")
                for b in (bank_resp.get("banks") or bank_resp.get("items") or [])
            ]
            bank_ids = [b for b in all_ids if b and str(b).startswith("coding-agent::")]
        except Exception:
            bank_ids = []

    url = _public_hindsight_url(request, cfg)
    command = (
        "npx @vectorize-io/hindsight-coding-agents@0.8.5 install <harness> "
        f"--server self-hosted --api-url {url}"
    )
    return {
        "banks": bank_ids,
        "install": {"harnesses": _HARNESSES, "command": command},
        "console_url": _console_url(request, cfg),
    }


# ── /api/memory/models ──────────────────────────────────────────────────


@router.get("/models")
async def get_models(request: Request) -> dict[str, Any]:
    cfg = load_hal0_config()
    models_cfg = cfg.memory.models
    decision_cfg = cfg.memory.decision

    decision_health = await _decision_health(decision_cfg)

    return {
        "embeddings": {
            "provider": models_cfg.embeddings_provider,
            "model": models_cfg.embeddings_model,
        },
        "reranker": {
            "provider": models_cfg.reranker_provider,
            "model": models_cfg.reranker_model,
        },
        "decision": {
            "enabled": decision_cfg.enabled,
            "provider": decision_cfg.provider,
            "model": decision_cfg.model,
            "reachable": decision_health.get("reachable", False),
        },
    }
