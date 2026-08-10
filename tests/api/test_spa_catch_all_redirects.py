"""SPA catch-all redirects for probe-shaped bare paths (#1796).

A production box has the UI dist built and mounted, so the SPA catch-all
was quietly serving 200 HTML at ``/health``, ``/openapi.json``, ``/docs``,
``/redoc`` — the un-prefixed names of real ``/api/...`` endpoints. An
uptime probe pointed at the bare ``/health`` (a very natural guess) reported
"healthy" forever off static HTML, never reaching the real check.

``_mount_dashboard`` only runs when a UI dist directory is found
(``HAL0_UI_DIST`` env or a repo-relative ``ui/dist``), so this test builds a
minimal fake dist to exercise the real mount path end-to-end rather than
mocking the route directly.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app


@pytest.fixture
def spa_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    (dist / "favicon.svg").write_text("<svg/>")
    monkeypatch.setenv("HAL0_UI_DIST", str(dist))
    app = create_app()
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.mark.parametrize(
    "bare_path,api_path",
    [
        ("/health", "/api/health"),
        ("/openapi.json", "/api/openapi.json"),
        ("/docs", "/api/docs"),
        ("/redoc", "/api/redoc"),
    ],
)
def test_bare_api_lookalike_redirects_to_api(
    spa_client: TestClient, bare_path: str, api_path: str
) -> None:
    r = spa_client.get(bare_path)
    assert r.status_code == 307, r.text
    assert r.headers["location"] == api_path


def test_unrelated_spa_route_still_serves_index(spa_client: TestClient) -> None:
    r = spa_client.get("/some/dashboard/route")
    assert r.status_code == 200
    assert r.text == "<html>spa</html>"
