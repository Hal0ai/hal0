"""Generic MCP streamable-http/sse probe for user-installed servers.

`hal0.agents.hermes_provision._probe_mcp_server` speaks the same
JSON-RPC protocol but hard-codes hal0's own mount convention — it always
appends `/mcp` to the given URL (built for `_default_mcp_servers()`'s
mount-root entries, see its call site at
`hermes_provision.py:_phase_mcp_wire`) and only ever sends hal0's own
`X-hal0-Agent` + box-service bearer. A user-installed server's `url`
field is already the exact transport endpoint (ADR-0015 §Decision 1), and
its auth comes from its own `[secrets]`/`[env]` blocks, not hal0's
service identity — reusing that function as-is would silently
double-append `/mcp` onto an arbitrary third-party endpoint and send it
a bearer meant for hal0's own mounts. This module is the generic
counterpart used by `hal0 mcp test` / `POST /{id}/test` and by
`hal0.mcp.hermes_join`'s post-mutation re-probe.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hal0.mcp.installed import InstalledServer

_PROBE_TIMEOUT_S = 5.0


def resolve_secret(secret_key: str) -> str | None:
    """Resolve a ``[secrets]`` reference (a key name in api.env) to its value.

    hal0-api keeps ``os.environ`` in lockstep with ``/etc/hal0/api.env``
    on every write (``routes/secrets.py``) — an in-process read is always
    current, no separate file read needed.
    """
    return os.environ.get(secret_key)


def build_headers(record: InstalledServer, *, agent_id: str = "hermes") -> dict[str, str]:
    """Headers a probe / Hermes join sends for one installed record.

    ``[secrets]`` entries resolve to live values and are dropped (never
    sent empty) when unset, so a server missing a required secret still
    gets probed — the upstream 401/403 is the real signal, not a local
    skip. ``[env]`` literals are layered too (lower precedence than a
    same-named secret, since ``[secrets]`` is the more specific override
    path for anything credential-shaped).
    """
    headers: dict[str, str] = {"X-hal0-Agent": agent_id}
    headers.update(record.env)
    for env_name, secret_key in record.secrets.items():
        value = resolve_secret(secret_key)
        if value:
            headers[env_name] = value
    return headers


def _parse_jsonrpc(body: str) -> dict[str, Any]:
    body = (body or "").strip()
    if not body:
        return {}
    if body[0] == "{":
        parsed: dict[str, Any] = json.loads(body)
        return parsed
    for line in body.splitlines():
        if line.startswith("data: "):
            try:
                parsed = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            return parsed
    return {}


def probe_installed_server_sync(
    record: InstalledServer, *, timeout: float = _PROBE_TIMEOUT_S
) -> dict[str, Any]:
    """Speak MCP streamable-http JSON-RPC against ``record.url`` directly.

    POSTs ``initialize`` then ``tools/list`` at the record's own URL (no
    path rewriting — unlike ``_probe_mcp_server``, this is the exact
    endpoint the operator/manifest gave). Returns
    ``{"ok": bool, "tools": [str, ...], "error": str | None}``. Never
    raises — every transport failure becomes ``ok=False`` + a message.

    stdlib-only (mirrors ``_probe_mcp_server``'s own rationale): a probe
    triggered from the API process shouldn't need an extra dependency
    beyond what's already vendored for the rest of the MCP surface.
    """
    if record.transport not in ("streamable-http", "sse"):
        return {"ok": False, "tools": [], "error": f"transport {record.transport!r} not probeable"}
    if not record.url:
        return {"ok": False, "tools": [], "error": "no url configured"}

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **build_headers(record),
    }

    def _post(payload: dict[str, Any], session_id: str | None) -> tuple[dict[str, Any], str | None]:
        req_headers = dict(headers)
        if session_id:
            req_headers["Mcp-Session-Id"] = session_id
        req = Request(
            record.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            new_session = resp.headers.get("Mcp-Session-Id") or session_id
            return _parse_jsonrpc(body), new_session

    try:
        init_payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hal0-mcp-test", "version": "1"},
            },
        }
        _init_result, session_id = _post(init_payload, None)
        tools_payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        }
        result, _ = _post(tools_payload, session_id)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "tools": [], "error": str(exc)}

    if "error" in result:
        err = result["error"]
        return {"ok": False, "tools": [], "error": str(err.get("message", err))}

    tools = (result.get("result") or {}).get("tools") or []
    names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
    return {"ok": True, "tools": names, "error": None}


__all__ = ["build_headers", "probe_installed_server_sync", "resolve_secret"]
