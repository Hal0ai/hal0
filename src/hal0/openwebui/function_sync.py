"""Idempotent provisioning for hal0's bundled Open WebUI Function.

Only individual Function endpoints are used.  Open WebUI's ``/sync`` endpoint
treats its request as the full desired set and deletes omitted Functions, so it
must never be used to provision one hal0-managed filter.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

FUNCTION_ID = "hal0_hindsight_rag"
FUNCTION_NAME = "hal0 Hindsight RAG"
_DEFAULT_BASE_URL = "http://127.0.0.1:3001"

Response = tuple[int, Any]
Requester = Callable[[str, str, dict[str, Any] | None], Response]


def bundled_source() -> str:
    path = Path(__file__).with_name("functions") / f"{FUNCTION_ID}.py"
    return path.read_text(encoding="utf-8")


def bundled_source_hash() -> str:
    return hashlib.sha256(bundled_source().encode("utf-8")).hexdigest()


def _managed(function: dict[str, Any]) -> bool:
    meta = function.get("meta") if isinstance(function.get("meta"), dict) else {}
    manifest = meta.get("manifest") if isinstance(meta.get("manifest"), dict) else {}
    return bool(
        meta.get("hal0_managed")
        or manifest.get("hal0_managed")
        or "hal0_managed: true" in str(function.get("content") or "")
    )


def _function_form(source: str) -> dict[str, Any]:
    return {
        "id": FUNCTION_ID,
        "name": FUNCTION_NAME,
        "content": source,
        "meta": {
            "description": "Adds shared Hindsight memory to Open WebUI RAG.",
            "hal0_managed": True,
        },
        "commit_message": "Update hal0-managed Hindsight RAG filter",
    }


def _http_requester(base_url: str, token: str, timeout: float) -> Requester:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(method: str, path: str, payload: dict[str, Any] | None = None) -> Response:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method
        )
        try:
            with opener.open(req, timeout=timeout) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                detail = json.loads(raw) if raw else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = raw.decode("utf-8", errors="replace")[:500]
            return exc.code, detail

    return request


def reconcile_hindsight_rag_function(
    *,
    token: str | None = None,
    base_url: str | None = None,
    requester: Requester | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Create/update the bundled filter without disturbing user Functions.

    Existing managed Functions retain valves and manual active/global state.
    A colliding user-owned ID is reported and never overwritten.
    """

    token = token if token is not None else os.environ.get("HAL0_OPENWEBUI_ADMIN_TOKEN", "").strip()
    if requester is None and not token:
        return {"status": "auth_required", "function_id": FUNCTION_ID}
    base_url = base_url or os.environ.get("HAL0_OPENWEBUI_INTERNAL_URL", _DEFAULT_BASE_URL)
    request = requester or _http_requester(base_url, token or "", timeout)
    source = bundled_source()
    expected_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    try:
        status, listed = request("GET", "/api/v1/functions/list", None)
        if status in {401, 403}:
            return {"status": "auth_required", "function_id": FUNCTION_ID}
        if status < 200 or status >= 300 or not isinstance(listed, list):
            return {"status": "unavailable", "function_id": FUNCTION_ID, "http_status": status}
        summary = next(
            (
                entry
                for entry in listed
                if isinstance(entry, dict) and entry.get("id") == FUNCTION_ID
            ),
            None,
        )
        if summary is None:
            status, _created = request("POST", "/api/v1/functions/create", _function_form(source))
            if status < 200 or status >= 300:
                return {"status": "failed", "function_id": FUNCTION_ID, "http_status": status}
            status, active = request("POST", f"/api/v1/functions/id/{FUNCTION_ID}/toggle", None)
            if status < 200 or status >= 300:
                return {"status": "failed", "function_id": FUNCTION_ID, "http_status": status}
            status, global_ = request(
                "POST", f"/api/v1/functions/id/{FUNCTION_ID}/toggle/global", None
            )
            if status < 200 or status >= 300:
                return {"status": "failed", "function_id": FUNCTION_ID, "http_status": status}
            return {
                "status": "installed",
                "function_id": FUNCTION_ID,
                "source_hash": expected_hash,
                "active": bool((active or {}).get("is_active")),
                "global": bool((global_ or {}).get("is_global")),
            }

        status, existing = request("GET", f"/api/v1/functions/id/{FUNCTION_ID}", None)
        if status < 200 or status >= 300 or not isinstance(existing, dict):
            return {"status": "unavailable", "function_id": FUNCTION_ID, "http_status": status}
        if not _managed(existing):
            return {"status": "conflict", "function_id": FUNCTION_ID}

        current_source = str(existing.get("content") or "")
        current_hash = hashlib.sha256(current_source.encode("utf-8")).hexdigest()
        if current_hash != expected_hash:
            status, updated = request(
                "POST", f"/api/v1/functions/id/{FUNCTION_ID}/update", _function_form(source)
            )
            if status < 200 or status >= 300:
                return {"status": "failed", "function_id": FUNCTION_ID, "http_status": status}
            existing = updated if isinstance(updated, dict) else existing
            state = "updated"
        else:
            state = "converged"
        return {
            "status": state,
            "function_id": FUNCTION_ID,
            "source_hash": expected_hash,
            "active": bool(existing.get("is_active")),
            "global": bool(existing.get("is_global")),
        }
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return {"status": "unavailable", "function_id": FUNCTION_ID}


__all__ = [
    "FUNCTION_ID",
    "bundled_source",
    "bundled_source_hash",
    "reconcile_hindsight_rag_function",
]
