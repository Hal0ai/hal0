"""Safe, one-ID Open WebUI Function provisioning."""

from __future__ import annotations

from copy import deepcopy

from hal0.openwebui.function_sync import (
    FUNCTION_ID,
    bundled_source,
    reconcile_hindsight_rag_function,
)


class FakeFunctionsAPI:
    def __init__(self, existing=None):
        self.functions = {item["id"]: deepcopy(item) for item in (existing or [])}
        self.calls = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, deepcopy(payload)))
        if path == "/api/v1/functions/list":
            return 200, [
                {
                    "id": row["id"],
                    "name": row.get("name"),
                    "is_active": row.get("is_active", False),
                    "is_global": row.get("is_global", False),
                }
                for row in self.functions.values()
            ]
        if path == "/api/v1/functions/create":
            row = {**payload, "is_active": False, "is_global": False, "type": "filter"}
            self.functions[payload["id"]] = row
            return 200, row
        if path == f"/api/v1/functions/id/{FUNCTION_ID}":
            return (200, deepcopy(self.functions[FUNCTION_ID]))
        if path == f"/api/v1/functions/id/{FUNCTION_ID}/update":
            before = self.functions[FUNCTION_ID]
            self.functions[FUNCTION_ID] = {
                **before,
                **payload,
                "is_active": before["is_active"],
                "is_global": before["is_global"],
                "valves": before.get("valves"),
            }
            return 200, deepcopy(self.functions[FUNCTION_ID])
        if path == f"/api/v1/functions/id/{FUNCTION_ID}/toggle":
            self.functions[FUNCTION_ID]["is_active"] = not self.functions[FUNCTION_ID]["is_active"]
            return 200, deepcopy(self.functions[FUNCTION_ID])
        if path == f"/api/v1/functions/id/{FUNCTION_ID}/toggle/global":
            self.functions[FUNCTION_ID]["is_global"] = not self.functions[FUNCTION_ID]["is_global"]
            return 200, deepcopy(self.functions[FUNCTION_ID])
        raise AssertionError(f"unexpected call: {method} {path}")


def _managed(**overrides):
    return {
        "id": FUNCTION_ID,
        "name": "hal0 Hindsight RAG",
        "content": bundled_source(),
        "meta": {"manifest": {"hal0_managed": True}},
        "is_active": True,
        "is_global": True,
        **overrides,
    }


def test_missing_token_is_honest_and_does_not_touch_network(monkeypatch):
    monkeypatch.delenv("HAL0_OPENWEBUI_ADMIN_TOKEN", raising=False)
    assert reconcile_hindsight_rag_function()["status"] == "auth_required"


def test_create_uses_individual_endpoints_and_enables_global_filter():
    api = FakeFunctionsAPI([{"id": "user_filter", "name": "mine"}])
    result = reconcile_hindsight_rag_function(requester=api)
    assert result["status"] == "installed"
    assert api.functions[FUNCTION_ID]["is_active"] is True
    assert api.functions[FUNCTION_ID]["is_global"] is True
    assert "user_filter" in api.functions
    assert all(path != "/api/v1/functions/sync" for _, path, _ in api.calls)


def test_update_preserves_valves_and_manual_disabled_state():
    api = FakeFunctionsAPI(
        [
            _managed(
                content="hal0_managed: true\n# old",
                is_active=False,
                is_global=False,
                valves={"max_sources": 3},
            )
        ]
    )
    result = reconcile_hindsight_rag_function(requester=api)
    assert result["status"] == "updated"
    assert result["active"] is False
    assert result["global"] is False
    assert api.functions[FUNCTION_ID]["valves"] == {"max_sources": 3}
    assert not any(
        path.endswith("/toggle") or path.endswith("/toggle/global") for _, path, _ in api.calls
    )


def test_converged_function_is_a_noop():
    api = FakeFunctionsAPI([_managed()])
    result = reconcile_hindsight_rag_function(requester=api)
    assert result["status"] == "converged"
    assert [method for method, _, _ in api.calls] == ["GET", "GET"]


def test_user_owned_id_collision_is_never_overwritten():
    api = FakeFunctionsAPI(
        [
            {
                "id": FUNCTION_ID,
                "name": "User function",
                "content": "class Filter: pass",
                "meta": {},
                "is_active": True,
                "is_global": True,
            }
        ]
    )
    assert reconcile_hindsight_rag_function(requester=api)["status"] == "conflict"
    assert not any(method == "POST" for method, _, _ in api.calls)


def test_auth_failure_is_reported_without_fallback_to_bulk_sync():
    calls = []

    def denied(method, path, payload=None):
        calls.append(path)
        return 401, {"detail": "unauthorized"}

    assert reconcile_hindsight_rag_function(requester=denied)["status"] == "auth_required"
    assert calls == ["/api/v1/functions/list"]
