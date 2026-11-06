"""GET /api/slots/flm/models distinguishes an empty catalog from a failed probe."""

from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from hal0.api.routes import slots as slots_routes


@pytest.mark.parametrize(
    ("catalog", "probe_ok"),
    [
        ([], True),
        (None, False),
    ],
)
async def test_flm_models_reports_probe_outcome(
    monkeypatch: pytest.MonkeyPatch,
    catalog: list[dict] | None,
    probe_ok: bool,
) -> None:
    """Only a successful empty probe may be read as 'no models installed'."""
    monkeypatch.setattr(slots_routes._flm_catalog, "catalog", lambda: catalog)
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(auth=AsyncMock()))
    )

    body = await slots_routes.list_flm_models(request)  # type: ignore[arg-type]

    assert body == {"models": [], "probe_ok": probe_ok}
