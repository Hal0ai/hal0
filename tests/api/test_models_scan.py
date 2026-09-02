"""Tests for POST /api/models/scan — discovery + auto-register."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


@pytest.fixture
def isolated_client_with_root(
    tmp_hal0_home: str,
    tmp_path: pytest.TempPathFactory,
) -> Iterator[tuple[TestClient, Path]]:
    """App + a tmp root containing one .gguf, with the root pre-configured.

    The lifespan auto-scan picks up the file before the test runs; the
    scan POST under test must then report ``added: []`` because the entry
    is already in the registry, OR detect new files if we drop them in
    between.
    """
    extra_root = Path(tmp_hal0_home) / "extra-models"
    extra_root.mkdir(parents=True)

    # Bootstrap the [models] config so the auto-scan walks our temp dir
    # and not /var/lib/hal0/models (which is empty / unwritable here).
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        f'[models]\nroots = ["{extra_root}"]\nauto_scan_on_start = false\n',
        encoding="utf-8",
    )

    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c, extra_root


def test_scan_registers_new_gguf(
    isolated_client_with_root: tuple[TestClient, Path],
) -> None:
    client, root = isolated_client_with_root
    target = root / "qwen3-4b-instruct-q4_k_m.gguf"
    target.write_bytes(b"x" * 512)

    r = client.post("/api/models/scan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "added" in body
    assert "skipped" in body
    assert "scanned_roots" in body
    # qwen3-4b is the curated id matched by the hf_file basename.
    assert "qwen3-4b" in body["added"]

    listing = client.get("/api/models").json()
    ids = {m["id"] for m in listing.get("models", [])}
    assert "qwen3-4b" in ids


def test_scan_idempotent(
    isolated_client_with_root: tuple[TestClient, Path],
) -> None:
    client, root = isolated_client_with_root
    (root / "qwen3-4b-instruct-q4_k_m.gguf").write_bytes(b"x" * 128)

    first = client.post("/api/models/scan").json()
    assert "qwen3-4b" in first["added"]
    second = client.post("/api/models/scan").json()
    assert second["added"] == []


def test_commit_rows_stamps_provider_alongside_backends(tmp_hal0_home: str) -> None:
    """Task 3: committing a preview row (POST /api/models/scan {"rows": [...]})
    stamps ``provider`` on the new registry row alongside the operator-edited
    (or detection-derived) ``backends`` tag — not left ``None``."""
    fp = Path(tmp_hal0_home) / "kokoro-voice.gguf"
    fp.write_bytes(b"\x00" * 8)

    app: FastAPI = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/models/scan",
            json={"rows": [{"path": str(fp), "id": "kokoro-voice", "backends": ["kokoro"]}]},
        )
        assert r.status_code == 200, r.text
        assert "kokoro-voice" in r.json()["added"]

        row = client.get("/api/models/kokoro-voice").json()
        assert row["provider"] == "kokoro"
        assert row["backends"] == ["kokoro"]


def test_scan_commit_rejects_out_of_vocab_provider(tmp_hal0_home: str) -> None:
    """An operator-supplied ``provider`` on a scan-commit row must be screened
    against RUNTIME_FAMILIES same as the POST /api/models create path — an
    out-of-vocab token like "whispercpp" (a curated-only tag, never valid on
    a registry row) must be skipped, not persisted unscreened."""
    fp = Path(tmp_hal0_home) / "mystery-voice.gguf"
    fp.write_bytes(b"\x00" * 8)

    app: FastAPI = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/models/scan",
            json={
                "rows": [
                    {"path": str(fp), "id": "mystery-voice", "provider": "whispercpp"},
                ]
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "mystery-voice" not in body["added"]
        assert any(s.get("path") == str(fp) for s in body["skipped"]), body["skipped"]

        listing = client.get("/api/models").json()
        ids = {m["id"] for m in listing.get("models", [])}
        assert "mystery-voice" not in ids


def test_scan_commit_persists_valid_explicit_provider(tmp_hal0_home: str) -> None:
    fp = Path(tmp_hal0_home) / "explicit-qwen.gguf"
    fp.write_bytes(b"\x00" * 8)

    app: FastAPI = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/models/scan",
            json={
                "rows": [
                    {"path": str(fp), "id": "explicit-qwen", "provider": "qwen3tts"},
                ]
            },
        )
        assert r.status_code == 200, r.text
        assert "explicit-qwen" in r.json()["added"]

        row = client.get("/api/models/explicit-qwen").json()
        assert row["provider"] == "qwen3tts"
