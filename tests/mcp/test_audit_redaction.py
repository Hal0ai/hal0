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


def test_audit_masks_every_mcp_server_env_value_and_keeps_the_names(
    audit_rows: list[dict[str, Any]],
) -> None:
    """``InstalledServer.header_value_keys`` sends every non-empty ``[env]``
    literal as an HTTP header, so each value is a credential whatever its
    name: an ``Authorization: Basic`` pair or an opaque ``X-Auth`` token."""
    args = {
        "server_id": "notes",
        "env": {"Authorization": "Basic dXNlcjpwYXNz", "X-Auth": "opaque3", "EMPTY": ""},
        "enabled": True,
    }
    admin._audit(
        client_id="c1", tool="mcp_server_config_write", args=args, gated=True, outcome="enqueued"
    )
    (row,) = audit_rows
    dumped = json.dumps(row)
    assert "dXNlcjpwYXNz" not in dumped
    assert "opaque3" not in dumped
    assert sorted(row["args"]["env"]) == ["Authorization", "EMPTY", "X-Auth"]
    assert row["args"]["server_id"] == "notes"
    assert row["args"]["enabled"] is True
    assert args["env"]["X-Auth"] == "opaque3"


@pytest.mark.parametrize("name", ["x-api-key", "auth-token", "X-Secret-Header", "client-password"])
def test_secret_named_values_mask_hyphenated_names(name: str) -> None:
    """A hyphenated header-style name is as secret as its ``_`` spelling,
    matching ``is_sensitive_key`` (#2384)."""
    from hal0.redaction import MASK, redact_secret_named_values

    assert redact_secret_named_values({name: "opaque9"}) == {name: MASK}


@pytest.mark.parametrize("name", ["max-tokens", "token-env", "tokenizer-id"])
def test_secret_named_values_leave_hyphenated_benign_names(name: str) -> None:
    from hal0.redaction import redact_secret_named_values

    assert redact_secret_named_values({name: "4096"}) == {name: "4096"}


def test_audit_row_pass_masks_a_truncated_value_to_the_end_of_the_line() -> None:
    from hal0.redaction import MASK, redact_audit_row_secret_args

    line = "mcp.tool.invoked tool=provider_credential_write args={'value': 'OPAQUEtrunc"
    out = redact_audit_row_secret_args(line)
    assert "OPAQUE" not in out
    assert out.endswith(f"'value': {MASK}")


def test_audit_row_pass_is_idempotent_and_ignores_other_rows() -> None:
    from hal0.redaction import redact_audit_row_secret_args

    row = (
        "mcp.tool.invoked args={'env': {'X-Auth': 'OPAQUE1', 'n': 3}} tool=mcp_server_config_write"
    )
    once = redact_audit_row_secret_args(row)
    assert "OPAQUE" not in once
    assert "'X-Auth':" in once and "'n':" in once
    assert redact_audit_row_secret_args(once) == once
    other = "slot.started args={'value': 'keep'} tool=provider_credential_write"
    assert redact_audit_row_secret_args(other) == other
