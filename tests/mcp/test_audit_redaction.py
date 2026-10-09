"""The MCP audit row must not carry secret argument values (#2434).

``_audit`` feeds journald through structlog; journald is at rest on the box
and is copied into ``hal0 doctor bundle``. Read-time masking on
``/api/logs`` and ``logs_tail`` does not protect either, so the args are
masked before they are logged.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hal0.mcp import admin


@pytest.fixture
def audit_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    class _Capture:
        def info(self, event: str, **kw: Any) -> None:
            rows.append({"event": event, **kw})

    monkeypatch.setattr(admin, "audit_log", _Capture())
    return rows


def test_audit_masks_the_provider_credential_value(audit_rows: list[dict[str, Any]]) -> None:
    admin._audit(
        client_id="c1",
        tool="provider_credential_write",
        args={"name": "openai", "key": "OPENAI_API_KEY", "value": "short1"},
        gated=True,
        outcome="enqueued",
    )
    (row,) = audit_rows
    assert "short1" not in json.dumps(row)
    assert row["args"]["name"] == "openai"
    assert row["tool"] == "provider_credential_write"


def test_audit_masks_secret_shapes_and_secret_named_keys(
    audit_rows: list[dict[str, Any]],
) -> None:
    args = {
        "path": "providers.openai",
        "api_key": "FAKE0named",
        "nested": {"token": "FAKEnested", "items": ["HF_TOKEN=hf_FAKE1"]},
        "note": (
            'apikey=FAKE2 {"apiKey": "FAKE3"} Authorization: ApiKey FAKE4 '
            "https://u:FAKE5@x.test --token FAKE6 Authorization: Bearer FAKE7"
        ),
        "count": 3,
    }
    original = json.dumps(args, sort_keys=True)
    admin._audit(client_id="c1", tool="config_write", args=args, gated=True, outcome="enqueued")
    (row,) = audit_rows
    assert "FAKE" not in json.dumps(row)
    assert row["args"]["path"] == "providers.openai"
    assert row["args"]["count"] == 3
    # The caller's dict is not mutated: the executor still needs the real value.
    assert json.dumps(args, sort_keys=True) == original


def test_audit_leaves_ordinary_args_alone(audit_rows: list[dict[str, Any]]) -> None:
    args = {"name": "llm-main", "model_id": "qwen3-8b", "max_tokens": 4096}
    admin._audit(client_id="c1", tool="slot_restart", args=args, gated=False, outcome="ok")
    assert audit_rows[0]["args"] == args
