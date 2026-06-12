"""Tests for #317 — MCP caller identity via the ``X-hal0-Agent`` header.

Before the fix ``client_id_resolver`` returned the Bearer token, so an MCP
client sending ``X-hal0-Agent`` + ``X-hal0-Private`` collapsed to
``anonymous`` and its private writes silently landed in ``shared``. The
resolver now reads + validates ``X-hal0-Agent`` exactly like the REST
memory surface, so private writes resolve to ``private:<agent>``.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from hal0.api import mcp_mount
from hal0.memory.namespace import resolve_write_dataset


def _fake_request(headers: dict[str, str]) -> Request:
    """Minimal Starlette Request exposing the given headers."""
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw})


@pytest.fixture
def patch_request(monkeypatch: pytest.MonkeyPatch):
    """Return a setter that points the MCP request context at fake headers."""

    def _set(headers: dict[str, str] | None) -> None:
        req = _fake_request(headers) if headers is not None else None
        monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: req)

    return _set


def test_client_id_from_agent_header(patch_request) -> None:
    patch_request({"X-hal0-Agent": "bob"})
    assert mcp_mount.client_id_resolver() == "bob"


def test_client_id_anonymous_when_header_absent(patch_request) -> None:
    patch_request({})
    assert mcp_mount.client_id_resolver() == "anonymous"


def test_client_id_anonymous_outside_request(patch_request) -> None:
    patch_request(None)
    assert mcp_mount.client_id_resolver() == "anonymous"


def test_client_id_rejects_private_prefix(patch_request) -> None:
    patch_request({"X-hal0-Agent": "private:bob"})
    assert mcp_mount.client_id_resolver() == "anonymous"


@pytest.mark.parametrize("bad", ["../etc", "has space", "x" * 65, "tab\tname"])
def test_client_id_rejects_malformed(patch_request, bad: str) -> None:
    patch_request({"X-hal0-Agent": bad})
    assert mcp_mount.client_id_resolver() == "anonymous"


def test_private_resolver_reads_header(patch_request) -> None:
    patch_request({"X-hal0-Private": "1"})
    assert mcp_mount.private_resolver() is True
    patch_request({})
    assert mcp_mount.private_resolver() is False


def test_private_write_lands_in_agent_namespace(patch_request) -> None:
    """End-to-end #317: X-hal0-Agent + private → private:<agent>, not shared."""
    patch_request({"X-hal0-Agent": "bob", "X-hal0-Private": "1"})
    client_id = mcp_mount.client_id_resolver()
    private = mcp_mount.private_resolver()
    dataset = resolve_write_dataset(None, private=private, client_id=client_id)
    assert dataset == "private:bob"
