"""Tests for POST /api/models/add-from-path — single-file registry add.

The endpoint is a thin wrapper around ``detect()`` + the
``ModelRegistry.add()`` write path. Tests cover the happy case (one
GGUF, registry gains the row, ``models`` lists it) plus the four error
shapes the dashboard cares about: missing path, unsupported format,
duplicate id without overwrite, and bad body.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


@pytest.fixture
def add_path_client(
    tmp_hal0_home: str,
) -> Iterator[tuple[TestClient, Path]]:
    """Empty-config app + a tmp dir to drop fixture files into.

    The dir is NOT registered as a [models].roots root — add-from-path
    must work on any absolute path the operator points at, regardless of
    whether that path lives inside the configured scan roots.
    """
    drop = Path(tmp_hal0_home) / "drop"
    drop.mkdir(parents=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        "[models]\nroots = []\nauto_scan_on_start = false\n",
        encoding="utf-8",
    )
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c, drop


def test_add_from_path_registers_gguf(
    add_path_client: tuple[TestClient, Path],
) -> None:
    """Pointing at a real .gguf file lands in the registry as installed=True."""
    client, drop = add_path_client
    target = drop / "Qwen3-4B-UD-Q4_K_XL.gguf"
    target.write_bytes(b"\x00" * 256)

    r = client.post("/api/models/add-from-path", json={"path": str(target)})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["path"] == str(target.resolve())
    assert body["size_bytes"] == 256
    # detect() can't parse a fake GGUF header here, so it falls back to
    # filename + extension only: capability == "chat", backends from the
    # GGUF backend seed (vulkan/rocm/cuda/cpu).
    assert "chat" in body["capabilities"]
    assert "vulkan" in body["backends"]

    listing = client.get("/api/models").json()
    ids = {m["id"] for m in listing.get("models", [])}
    assert body["id"] in ids


def test_add_from_path_honours_explicit_id_and_labels(
    add_path_client: tuple[TestClient, Path],
) -> None:
    """Caller-supplied id + labels must win over the detector's defaults."""
    client, drop = add_path_client
    target = drop / "anything.gguf"
    target.write_bytes(b"\x00" * 64)
    r = client.post(
        "/api/models/add-from-path",
        json={
            "path": str(target),
            "id": "user.my-custom-id",
            "name": "My custom name",
            "labels": ["embed", "chat"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "user.my-custom-id"
    assert body["name"] == "My custom name"
    assert body["capabilities"] == ["embed", "chat"]


def test_add_from_path_rejects_missing_file(
    add_path_client: tuple[TestClient, Path],
) -> None:
    client, drop = add_path_client
    r = client.post(
        "/api/models/add-from-path",
        json={"path": str(drop / "does-not-exist.gguf")},
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "model.path_missing"


def test_add_from_path_rejects_unsupported_extension(
    add_path_client: tuple[TestClient, Path],
) -> None:
    """A file whose extension isn't in file_extensions must 400."""
    client, drop = add_path_client
    target = drop / "tokenizer.json"
    target.write_bytes(b"{}")
    r = client.post(
        "/api/models/add-from-path",
        json={"path": str(target)},
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "model.unsupported_format"
    assert ".gguf" in err["details"]["allowed"]


def test_add_from_path_rejects_relative_path(
    add_path_client: tuple[TestClient, Path],
) -> None:
    client, _ = add_path_client
    r = client.post(
        "/api/models/add-from-path",
        json={"path": "relative/path.gguf"},
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "model.path_relative"


def test_add_from_path_409_on_duplicate_id(
    add_path_client: tuple[TestClient, Path],
) -> None:
    """Re-adding the same path without overwrite=true is a 409."""
    client, drop = add_path_client
    target = drop / "dup.gguf"
    target.write_bytes(b"x" * 32)
    first = client.post(
        "/api/models/add-from-path",
        json={"path": str(target), "id": "user.dup"},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/api/models/add-from-path",
        json={"path": str(target), "id": "user.dup"},
    )
    assert second.status_code == 409, second.text
    err = second.json()["error"]
    assert err["code"] == "model.already_exists"


def test_add_from_path_overwrites_when_requested(
    add_path_client: tuple[TestClient, Path],
) -> None:
    """overwrite=true replaces the existing entry in place."""
    client, drop = add_path_client
    t1 = drop / "v1.gguf"
    t1.write_bytes(b"a" * 32)
    t2 = drop / "v2.gguf"
    t2.write_bytes(b"b" * 64)

    r1 = client.post(
        "/api/models/add-from-path",
        json={"path": str(t1), "id": "user.same"},
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/api/models/add-from-path",
        json={"path": str(t2), "id": "user.same", "overwrite": True},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["path"] == str(t2.resolve())
    assert r2.json()["size_bytes"] == 64


def test_add_from_path_rejects_bad_body(
    add_path_client: tuple[TestClient, Path],
) -> None:
    client, _ = add_path_client
    # Missing path
    r = client.post("/api/models/add-from-path", json={})
    assert r.status_code == 400, r.text
    # Path not a string
    r = client.post("/api/models/add-from-path", json={"path": 42})
    assert r.status_code == 400, r.text


# ── #1415: the HF hub cache layout (symlink -> extensionless blob) ───────────


def _hub_layout(root: Path, *, repo: str, revision: str, filename: str, body: bytes) -> Path:
    """Build HuggingFace's on-disk hub cache shape and return the symlink.

    ``blobs/<sha>`` holds the bytes with NO extension; the human-readable
    name exists only as ``snapshots/<rev>/<name>.gguf -> ../../blobs/<sha>``.
    This is also the shape of hal0's own ``/mnt/ai-models/local/*`` farm.
    """
    import hashlib

    hub = root / f"models--{repo.replace('/', '--')}"
    blobs = hub / "blobs"
    snap = hub / "snapshots" / revision
    blobs.mkdir(parents=True, exist_ok=True)
    snap.mkdir(parents=True, exist_ok=True)
    blob = blobs / hashlib.sha256(body).hexdigest()
    blob.write_bytes(body)
    link = snap / filename
    link.symlink_to(blob)
    return link


def test_add_from_path_accepts_gguf_symlink_into_extensionless_blob(
    add_path_client: tuple[TestClient, Path],
) -> None:
    """#1415: the allow-list must apply to the path the operator TYPED.

    Checking ``path.resolve().suffix`` rejected every model in the HF hub
    cache — hub blobs are sha-named and extensionless — with the nonsense
    message ``file extension '' not in [models].file_extensions`` for a path
    that visibly ends in ``.gguf``.
    """
    client, drop = add_path_client
    link = _hub_layout(
        drop,
        repo="unsloth/Qwen3.5-0.8B-GGUF",
        revision="6ab4614",
        filename="Qwen3.5-0.8B-UD-Q4_K_XL.gguf",
        body=b"\x00" * 256,
    )
    assert link.resolve().suffix == "", "fixture must reproduce the extensionless blob"

    r = client.post("/api/models/add-from-path", json={"path": str(link), "labels": ["chat"]})

    assert r.status_code == 201, r.text
    body = r.json()
    # The resolved blob is still what gets stored/detected — only the
    # allow-list check moved to the literal path.
    assert body["path"] == str(link.resolve())
    assert body["capabilities"] == ["chat"]
    # ...and the derived id/name come from the name the operator can see,
    # not the opaque sha the symlink happens to point at.
    assert "qwen3" in body["id"].lower()
    assert link.resolve().name not in body["id"]
    assert link.resolve().name not in body["name"]


def test_add_from_path_symlink_to_disallowed_extension_still_rejected(
    add_path_client: tuple[TestClient, Path],
) -> None:
    """The allow-list still bites: a symlink named ``.json`` is refused, and
    the error quotes the operator's path rather than the resolved target."""
    client, drop = add_path_client
    real = drop / "actual-blob"
    real.write_bytes(b"{}")
    link = drop / "tokenizer.json"
    link.symlink_to(real)

    r = client.post("/api/models/add-from-path", json={"path": str(link)})

    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "model.unsupported_format"
    assert "'.json'" in err["message"]
    assert err["details"]["path"] == str(link)


def test_add_from_path_extensionless_literal_falls_back_to_resolved(
    add_path_client: tuple[TestClient, Path],
) -> None:
    """A literal path with no suffix at all still consults the resolved
    target, so pointing straight at a hub blob keeps its old behaviour."""
    client, drop = add_path_client
    real = drop / "weights.gguf"
    real.write_bytes(b"\x00" * 32)
    link = drop / "no-extension-here"
    link.symlink_to(real)

    r = client.post("/api/models/add-from-path", json={"path": str(link)})
    assert r.status_code == 201, r.text
