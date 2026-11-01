"""Register a real model file through ``POST /api/models`` (#2668).

Slot create and ``PUT /config`` refuse a model id the registry cannot resolve
(``validation.model_not_registered``), so a test that binds a model to a slot
over HTTP registers it first.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from fastapi.testclient import TestClient


def register_model_file(
    client: TestClient,
    home: str | Path,
    model_id: str,
    capabilities: Iterable[str] | None = None,
    *,
    exist_ok: bool = False,
) -> None:
    """Write a stub gguf under ``<home>/models`` and register it as ``model_id``.

    ``exist_ok`` skips a model the registry already holds (an app rebuilt over the
    same home keeps its registry).
    """
    if exist_ok and client.get(f"/api/models/{model_id}").status_code == 200:
        return
    path = Path(home) / "models" / f"{model_id}.gguf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 16)
    body: dict[str, object] = {"id": model_id, "path": str(path)}
    if capabilities is not None:
        body["capabilities"] = list(capabilities)
    response = client.post("/api/models", json=body)
    assert response.status_code == 201, response.text
