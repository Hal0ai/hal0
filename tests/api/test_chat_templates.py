"""Tests for /api/chat-templates — catalog endpoint + bundled library + store seeding.

Covers:
  - seed_chat_templates() populates the store dir on startup (chatml, llama3).
  - GET /api/chat-templates returns at minimum: auto, chatml, llama3.
  - POST /api/chat-templates with valid id writes the file + appears in GET.
  - POST /api/chat-templates with path-traversal id returns 4xx, writes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.templates import seed_chat_templates


@pytest.fixture
def store_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """App with model store rooted in tmp_path."""
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    # Also need HAL0_HOME for the broader app bootstrap
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0home"))
    return create_app()


@pytest.fixture
def store_client(store_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(store_app) as c:
        yield c


# ── seed_chat_templates ───────────────────────────────────────────────────────


def test_seed_populates_chatml_and_llama3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """seed_chat_templates() writes bundled templates absent-only."""
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    seed_chat_templates()
    templates_dir = tmp_path / "chat-templates"
    assert (templates_dir / "chatml.jinja").exists(), "chatml.jinja should be seeded"
    assert (templates_dir / "llama3.jinja").exists(), "llama3.jinja should be seeded"


def test_seed_does_not_overwrite_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """seed_chat_templates() skips files that already exist."""
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    templates_dir = tmp_path / "chat-templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "chatml.jinja").write_text("custom content")
    seed_chat_templates()
    assert (templates_dir / "chatml.jinja").read_text() == "custom content"


# ── GET /api/chat-templates ───────────────────────────────────────────────────


def test_get_catalog_includes_auto(store_client: TestClient) -> None:
    r = store_client.get("/api/chat-templates")
    assert r.status_code == 200, r.text
    ids = {entry["id"] for entry in r.json()}
    assert "auto" in ids


def test_get_catalog_includes_bundled_templates(store_client: TestClient) -> None:
    r = store_client.get("/api/chat-templates")
    assert r.status_code == 200, r.text
    ids = {entry["id"] for entry in r.json()}
    assert "chatml" in ids, f"chatml missing from {ids}"
    assert "llama3" in ids, f"llama3 missing from {ids}"


def test_get_catalog_auto_is_first(store_client: TestClient) -> None:
    r = store_client.get("/api/chat-templates")
    assert r.status_code == 200, r.text
    entries = r.json()
    assert entries[0]["id"] == "auto", "auto should be the first entry"


# ── POST /api/chat-templates ──────────────────────────────────────────────────


def test_post_custom_template_appears_in_get(
    store_client: TestClient,
    tmp_path: Path,
) -> None:
    r = store_client.post(
        "/api/chat-templates",
        json={"id": "mycustom", "content": "{{ x }}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "mycustom"

    # Subsequent GET must include mycustom
    r2 = store_client.get("/api/chat-templates")
    ids = {entry["id"] for entry in r2.json()}
    assert "mycustom" in ids


def test_post_custom_template_writes_file(
    store_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_client.post(
        "/api/chat-templates",
        json={"id": "wtest", "content": "hello {{ world }}"},
    )
    templates_dir = tmp_path / "chat-templates"
    written = templates_dir / "wtest.jinja"
    assert written.exists(), "POST should write the .jinja file"
    assert "hello {{ world }}" in written.read_text()


def test_post_invalid_id_path_traversal_returns_4xx(store_client: TestClient) -> None:
    r = store_client.post(
        "/api/chat-templates",
        json={"id": "../evil", "content": "bad"},
    )
    assert r.status_code in (400, 422), f"Expected 4xx, got {r.status_code}"


def test_post_invalid_id_uppercase_returns_4xx(store_client: TestClient) -> None:
    r = store_client.post(
        "/api/chat-templates",
        json={"id": "MyTemplate", "content": "bad"},
    )
    assert r.status_code in (400, 422), f"Expected 4xx, got {r.status_code}"


def test_post_invalid_id_writes_nothing(
    store_client: TestClient,
    tmp_path: Path,
) -> None:
    store_client.post(
        "/api/chat-templates",
        json={"id": "../evil", "content": "bad"},
    )
    evil_path = tmp_path / "evil.jinja"
    assert not evil_path.exists(), "Path traversal must not write outside store"
