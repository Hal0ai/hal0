"""``GET /api/models`` filters, projection and paging (#3161).

The helper is pure, so most cases drive :func:`narrow_models` directly; the
route tests pin the query-parameter wiring (422 bounds, identity when no
parameter is present).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.services import models_service
from hal0.services.models_service import narrow_models


def _payload() -> dict[str, Any]:
    models = [
        {
            "id": "qwen3-8b-q4",
            "name": "Qwen3 8B",
            "type": "llm",
            "installed": True,
            "capabilities": ["chat", "vision"],
            "runs_on": ["gpu-rocm", "gpu-vulkan"],
            "size_gb": 5.0,
        },
        {
            "id": "nomic-embed",
            "name": "Nomic Embed",
            "type": "embedding",
            "installed": True,
            "capabilities": ["embed"],
            "runs_on": ["cpu"],
            "size_gb": 0.1,
        },
        {
            "id": "gpt-remote",
            "name": "GPT Remote",
            "type": "llm",
            "installed": False,
            "capabilities": ["chat"],
            "size_gb": 0,
        },
        {"id": "bare-row", "name": "Bare", "type": "llm", "installed": False},
    ]
    return {"models": models, "count": len(models), "filtered_aliases": 3}


def _ids(out: dict[str, Any]) -> list[str]:
    return [m["id"] for m in out["models"]]


def test_installed_filter() -> None:
    assert _ids(narrow_models(_payload(), installed=True)) == ["qwen3-8b-q4", "nomic-embed"]
    assert _ids(narrow_models(_payload(), installed=False)) == ["gpt-remote", "bare-row"]


def test_type_filter_accepts_a_comma_list() -> None:
    assert _ids(narrow_models(_payload(), type="embedding")) == ["nomic-embed"]
    assert _ids(narrow_models(_payload(), type="embedding, llm")) == [
        "qwen3-8b-q4",
        "nomic-embed",
        "gpt-remote",
        "bare-row",
    ]


def test_capability_filter_requires_all_and_skips_rows_without_capabilities() -> None:
    assert _ids(narrow_models(_payload(), capability="chat")) == ["qwen3-8b-q4", "gpt-remote"]
    assert _ids(narrow_models(_payload(), capability="chat,vision")) == ["qwen3-8b-q4"]
    # bare-row has no capabilities list: never matches a capability filter.
    assert "bare-row" not in _ids(narrow_models(_payload(), capability="chat"))


def test_runs_on_filter_matches_any_lane() -> None:
    assert _ids(narrow_models(_payload(), runs_on="cpu,gpu-vulkan")) == [
        "qwen3-8b-q4",
        "nomic-embed",
    ]
    assert _ids(narrow_models(_payload(), runs_on="npu")) == []


def test_q_matches_id_or_name_case_insensitively() -> None:
    assert _ids(narrow_models(_payload(), q="QWEN")) == ["qwen3-8b-q4"]
    assert _ids(narrow_models(_payload(), q="remote")) == ["gpt-remote"]
    assert _ids(narrow_models(_payload(), q="embed")) == ["nomic-embed"]


def test_filters_combine_with_and() -> None:
    out = narrow_models(_payload(), installed=True, type="llm", capability="vision", q="8b")
    assert _ids(out) == ["qwen3-8b-q4"]
    assert narrow_models(_payload(), installed=False, type="embedding")["models"] == []


def test_fields_keeps_id_and_ignores_unknown_keys() -> None:
    out = narrow_models(_payload(), fields="type,nope")
    assert out["models"][0] == {"id": "qwen3-8b-q4", "type": "llm"}
    assert all(set(m) <= {"id", "type"} for m in out["models"])


def test_paging_reports_total_before_paging_and_count_after() -> None:
    out = narrow_models(_payload(), limit=2, offset=1)
    assert _ids(out) == ["nomic-embed", "gpt-remote"]
    assert out["total"] == 4
    assert out["count"] == 2
    assert out["filtered_aliases"] == 3
    past_end = narrow_models(_payload(), limit=5, offset=9)
    assert (past_end["models"], past_end["count"], past_end["total"]) == ([], 0, 4)


def test_total_counts_filtered_rows_not_all_rows() -> None:
    out = narrow_models(_payload(), installed=True, limit=1)
    assert (out["count"], out["total"]) == (1, 2)


def test_helper_does_not_mutate_the_payload() -> None:
    payload = _payload()
    narrow_models(payload, installed=True, fields="type", limit=1)
    assert payload == _payload()


# ── route wiring ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    root = Path(tmp_hal0_home) / "models-root"
    root.mkdir(parents=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        f'[models]\nroots = ["{root}"]\nauto_scan_on_start = false\n', encoding="utf-8"
    )
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fixed_list(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    payload = _payload()

    async def _list_all(**_kwargs: Any) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(models_service, "list_all", _list_all)
    return payload


def test_route_without_new_params_returns_list_all_unchanged(
    client: TestClient, fixed_list: dict[str, Any]
) -> None:
    resp = client.get("/api/models")
    assert resp.status_code == 200, resp.text
    assert resp.json() == fixed_list
    assert "total" not in resp.json()


def test_route_applies_filters_projection_and_paging(
    client: TestClient, fixed_list: dict[str, Any]
) -> None:
    resp = client.get(
        "/api/models",
        params={"installed": "true", "fields": "type", "limit": 1, "offset": 1},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "models": [{"id": "nomic-embed", "type": "embedding"}],
        "count": 1,
        "total": 2,
        "filtered_aliases": 3,
    }


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"installed": "maybe"}],
)
def test_route_rejects_out_of_range_params(
    client: TestClient, fixed_list: dict[str, Any], params: dict[str, Any]
) -> None:
    assert client.get("/api/models", params=params).status_code == 422
