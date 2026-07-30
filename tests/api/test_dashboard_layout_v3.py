"""PUT/GET /api/user/dashboard-layout must speak v3 (#1460).

The dashboard moved to the fixed-band v3 schema in #1061
(``{v:3, cells, quickActions}`` — ui/src/api/hooks/useDashLayout.ts) but the
backend still required v2 (``order/enabled/spans/pinned``), so every save
422'd with ``layout.invalid`` and every customization was lost on reload.

v3 is now the canonical schema; v2 is TOLERATED (a stale cached bundle must
not start 422-ing, and a pre-#1061 file on disk is preserved, not erased).
GET dispatches its reconcile by the STORED payload's version so a v3 file is
never mangled by the v2 pin/span rules.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes import dashboard_layout as dashboard_layout_routes


@pytest.fixture
def layout_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    app.include_router(dashboard_layout_routes.router, prefix="/api/user", tags=["user"])
    with TestClient(app) as c:
        yield c


# The exact body ui/src/api/hooks/useDashLayout.ts PUTs.
_V3_LAYOUT = {
    "v": 3,
    "cells": {
        "memory": "memorybar",
        "a1": "power",
        "a2": "utilization",
        "a3": "requests",
        "slots": "slots",
        "c1": "activity",
        "c2": "quickchat",
        "c3": "attention",
    },
    "quickActions": False,
}

_V2_LAYOUT = {
    "v": 2,
    "order": ["slots", "memory", "throughput"],
    "enabled": {"slots": True, "memory": False, "throughput": True},
    "spans": {"slots": 6, "memory": 3, "throughput": 4},
    "pinned": [],
}


def test_put_v3_is_accepted_and_persisted(layout_client: TestClient) -> None:
    r = layout_client.put("/api/user/dashboard-layout", json=_V3_LAYOUT)
    assert r.status_code == 204, r.text

    body = layout_client.get("/api/user/dashboard-layout").json()
    assert body["v"] == 3
    assert body["cells"] == _V3_LAYOUT["cells"]
    assert body["quickActions"] is False
    # The v2 reconcile's keys must NOT be grafted onto a v3 payload.
    assert "order" not in body
    assert "spans" not in body
    assert "pinned" not in body


def test_put_v3_reconciles_against_the_cell_whitelist(layout_client: TestClient) -> None:
    """A widget outside the cell's `accepts` list falls back to that cell's default."""
    bad = {**_V3_LAYOUT, "cells": {**_V3_LAYOUT["cells"], "a3": "power"}}
    assert layout_client.put("/api/user/dashboard-layout", json=bad).status_code == 204

    cells = layout_client.get("/api/user/dashboard-layout").json()["cells"]
    assert cells["a3"] == "requests"  # a3 accepts only requests|clients


def test_put_v3_rejects_an_unbuilt_widget_to_the_cell_default(layout_client: TestClient) -> None:
    """`heatmap` is whitelisted for c1 but not built yet — must not be persisted."""
    bad = {**_V3_LAYOUT, "cells": {**_V3_LAYOUT["cells"], "c1": "heatmap"}}
    assert layout_client.put("/api/user/dashboard-layout", json=bad).status_code == 204

    cells = layout_client.get("/api/user/dashboard-layout").json()["cells"]
    assert cells["c1"] == "activity"


def test_put_v3_fills_in_a_missing_cell(layout_client: TestClient) -> None:
    partial = {"v": 3, "cells": {"a1": "power"}, "quickActions": True}
    assert layout_client.put("/api/user/dashboard-layout", json=partial).status_code == 204

    cells = layout_client.get("/api/user/dashboard-layout").json()["cells"]
    assert cells["a1"] == "power"
    assert cells["memory"] == "memorybar"
    assert cells["slots"] == "slots"
    assert cells["c3"] == "attention"


def test_put_v3_with_a_non_object_cells_is_422(layout_client: TestClient) -> None:
    bad = {"v": 3, "cells": ["memorybar"], "quickActions": True}
    r = layout_client.put("/api/user/dashboard-layout", json=bad)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "layout.invalid"


def test_put_unknown_version_is_422(layout_client: TestClient) -> None:
    r = layout_client.put("/api/user/dashboard-layout", json={"v": 4, "cells": {}})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "layout.invalid"


def test_v2_body_is_still_tolerated(layout_client: TestClient) -> None:
    """A stale cached bundle emitting v2 must not start 422-ing."""
    assert layout_client.put("/api/user/dashboard-layout", json=_V2_LAYOUT).status_code == 204

    body = layout_client.get("/api/user/dashboard-layout").json()
    assert body["v"] == 2
    assert body["order"] == ["slots", "memory", "throughput"]
