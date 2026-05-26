"""Tests for POST /api/models/scan/preview — detection-only walk."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


@pytest.fixture
def preview_client(
    tmp_hal0_home: str,
) -> Iterator[tuple[TestClient, Path]]:
    """App + a tmp directory to drop fixture files into for preview.

    Mirrors test_models_scan's bootstrap but does NOT auto-register the
    tmp dir as a root — preview operates on caller-supplied paths, so
    leaving the config minimal proves the endpoint walks the request body
    rather than the configured roots.
    """
    extra_root = Path(tmp_hal0_home) / "preview-models"
    extra_root.mkdir(parents=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        "[models]\nroots = []\nauto_scan_on_start = false\n",
        encoding="utf-8",
    )
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c, extra_root


def test_preview_returns_detection_rows_without_registering(
    preview_client: tuple[TestClient, Path],
) -> None:
    """Preview must NOT mutate the registry — that's the whole point."""
    client, root = preview_client
    # Plain .gguf — header read will fail (no magic) so detect() falls
    # back to filename heuristic. confidence stays "low" but the row is
    # still emitted with the GGUF backend seed.
    (root / "qwen3-4b-q4_k_m.gguf").write_bytes(b"\x00" * 64)
    (root / "moonshine-en.bin").write_bytes(b"\x00" * 64)

    r = client.post(
        "/api/models/scan/preview",
        json={"paths": [str(root)], "recursive": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 1
    paths = {row["path"] for row in body["preview"]}
    # The .gguf is picked up because .gguf is in the default
    # file_extensions list; .bin is not.
    assert any(p.endswith("qwen3-4b-q4_k_m.gguf") for p in paths)

    # Confirm zero side effects on the registry.
    listing = client.get("/api/models").json()
    assert listing["models"] == []


def test_preview_walks_recursive_when_flag_set(
    preview_client: tuple[TestClient, Path],
) -> None:
    """Nested .gguf files only appear when recursive=True."""
    client, root = preview_client
    nested = root / "sub" / "dir"
    nested.mkdir(parents=True)
    (nested / "embed-bge-small.gguf").write_bytes(b"\x00" * 64)

    flat = client.post(
        "/api/models/scan/preview",
        json={"paths": [str(root)], "recursive": False},
    ).json()
    assert flat["count"] == 0

    deep = client.post(
        "/api/models/scan/preview",
        json={"paths": [str(root)], "recursive": True},
    ).json()
    assert deep["count"] == 1
    assert deep["preview"][0]["path"].endswith("embed-bge-small.gguf")


def test_preview_defaults_to_recursive(
    preview_client: tuple[TestClient, Path],
) -> None:
    """Omitting the recursive flag must walk subdirectories.

    The operator-facing flows (dashboard scan modal, CLI) almost always
    point at a model store root whose .gguf files live under per-repo
    subdirs; a shallow default produces zero rows and looks broken.
    """
    client, root = preview_client
    nested = root / "huggingface" / "Qwen3.5-4B"
    nested.mkdir(parents=True)
    (nested / "Qwen3.5-4B-Q4_K_M.gguf").write_bytes(b"\x00" * 64)

    r = client.post(
        "/api/models/scan/preview",
        json={"paths": [str(root)]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["preview"][0]["path"].endswith("Qwen3.5-4B-Q4_K_M.gguf")


def test_preview_single_file_path(
    preview_client: tuple[TestClient, Path],
) -> None:
    """A path that points at a file detects it directly."""
    client, root = preview_client
    target = root / "kokoro.gguf"
    target.write_bytes(b"\x00" * 64)
    r = client.post(
        "/api/models/scan/preview",
        json={"paths": [str(target)]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    row = body["preview"][0]
    assert row["path"].endswith("kokoro.gguf")
    # Filename heuristic recognises "kokoro" → tts backend.
    assert "kokoro" in row["suggested_backends"]


def test_preview_rejects_empty_paths(
    preview_client: tuple[TestClient, Path],
) -> None:
    client, _ = preview_client
    r = client.post("/api/models/scan/preview", json={"paths": []})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.invalid"


def test_preview_emits_no_events(
    preview_client: tuple[TestClient, Path],
) -> None:
    """Detection-only — no model.* events should fire."""
    client, root = preview_client
    (root / "qwen3-4b-q4_k_m.gguf").write_bytes(b"\x00" * 64)
    # Cursor: highest pre-call event id, so we only count new ones.
    pre = client.get("/api/events?limit=1000").json()
    pre_max = max((ev["id"] for ev in pre.get("events", [])), default=0)

    client.post(
        "/api/models/scan/preview",
        json={"paths": [str(root)]},
    )

    post = client.get(f"/api/events?since={pre_max}&type=model.*&limit=100").json()
    assert post.get("events", []) == []
