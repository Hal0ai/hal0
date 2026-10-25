"""Migration 014: drop the registry rows that describe ComfyUI image models.

The migration deletes registry rows only. It must hit exactly the target set,
never decrement a refcount, and never touch a file.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import hal0.db.migrations as packaged
from hal0.db.connection import connect
from hal0.db.migrate import migrate

_SNAPSHOT_QUERIES = {
    "model": "SELECT id, path, capabilities FROM model ORDER BY id",
    "model_file": "SELECT model_id, rel, dest, sha256 FROM model_file ORDER BY model_id, rel",
    "model_backend": "SELECT model_id, backend FROM model_backend ORDER BY model_id, backend",
    "store_blob": "SELECT sha256, blob_path, refcount FROM store_blob ORDER BY sha256",
}


def _pre_014(tmp_path: Path) -> Path:
    """The packaged migrations up to 013, so a test can seed old rows."""
    pre = tmp_path / "pre014"
    pre.mkdir()
    for sql in Path(packaged.__file__).parent.glob("*.sql"):
        if int(sql.name[:3]) < 14:
            shutil.copy(sql, pre)
    return pre


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        table: [tuple(row) for row in conn.execute(query).fetchall()]
        for table, query in _SNAPSHOT_QUERIES.items()
    }


def _seed(conn: sqlite3.Connection, tmp_path: Path) -> dict[str, Path]:
    """Seed a pre-014 database; return the real files it points at."""
    comfy = tmp_path / "box" / "comfyui" / "models" / "checkpoints"
    comfy.mkdir(parents=True)
    store = tmp_path / "box" / "models"
    store.mkdir(parents=True)
    files = {
        "sd_turbo": comfy / "sd-turbo.safetensors",
        "cap_only": comfy / "cap-only.safetensors",
        "shared": comfy / "shared.safetensors",
        "llm": store / "qwen.gguf",
    }
    for name, path in files.items():
        path.write_bytes(name.encode())

    def model(model_id: str, path: Path | str, capabilities: str | None) -> None:
        conn.execute(
            "INSERT INTO model (id, path, capabilities) VALUES (?, ?, ?)",
            (model_id, str(path), capabilities),
        )

    def model_file(model_id: str, rel: str, dest: Path, sha: str | None) -> None:
        conn.execute(
            "INSERT INTO model_file (model_id, rel, dest, sha256) VALUES (?, ?, ?, ?)",
            (model_id, rel, str(dest), sha),
        )

    def blob(sha: str, path: Path, refcount: int) -> None:
        conn.execute(
            "INSERT INTO store_blob (sha256, size_bytes, blob_path, refcount) VALUES (?, 1, ?, ?)",
            (sha, str(path), refcount),
        )

    # Target by path: no capability at all.
    model("sd-turbo", files["sd_turbo"], None)
    model_file("sd-turbo", "sd-turbo.safetensors", files["sd_turbo"], "sha-turbo")
    model_file("sd-turbo", "config.json", comfy / "config.json", None)  # non-LFS: no blob
    conn.execute("INSERT INTO model_backend VALUES ('sd-turbo', 'comfyui')")
    blob("sha-turbo", files["sd_turbo"], 1)

    # Target by capability: its path is outside the ComfyUI tree.
    model("flux-by-capability", store / "flux.gguf", '["image"]')
    model_file("flux-by-capability", "cap-only.safetensors", files["cap_only"], "sha-cap-only")
    model_file("flux-by-capability", "shared.safetensors", files["shared"], "sha-shared")
    conn.execute("INSERT INTO model_backend VALUES ('flux-by-capability', 'comfyui')")
    blob("sha-cap-only", files["cap_only"], 1)

    # Survivors. The first has "image" inside its id; the second has a capability that merely
    # starts with "image".
    model("qwen-image-understanding-gguf", files["llm"], '["chat"]')
    model_file("qwen-image-understanding-gguf", "qwen.gguf", files["llm"], "sha-llm")
    model_file("qwen-image-understanding-gguf", "shared.gguf", files["shared"], "sha-shared")
    conn.execute("INSERT INTO model_backend VALUES ('qwen-image-understanding-gguf', 'rocm')")
    blob("sha-llm", files["llm"], 1)
    model("vision-llm", store / "vision.gguf", '["chat","image-input"]')
    conn.execute("INSERT INTO model_backend VALUES ('vision-llm', 'vulkan')")

    # One blob shared by a target file and a survivor's file.
    blob("sha-shared", files["shared"], 2)
    return files


def _assert_only_targets_gone(conn: sqlite3.Connection, files: dict[str, Path]) -> None:
    survivors = {"qwen-image-understanding-gguf", "vision-llm"}
    assert {row["id"] for row in conn.execute("SELECT id FROM model")} == survivors

    assert {(row["model_id"], row["rel"]) for row in conn.execute("SELECT * FROM model_file")} == {
        ("qwen-image-understanding-gguf", "qwen.gguf"),
        ("qwen-image-understanding-gguf", "shared.gguf"),
    }
    assert {
        (row["model_id"], row["backend"]) for row in conn.execute("SELECT * FROM model_backend")
    } == {("qwen-image-understanding-gguf", "rocm"), ("vision-llm", "vulkan")}

    # Blobs only the target files held are forgotten; a blob a survivor still references stays
    # at its old refcount (a decrement would let GC unlink the shared file).
    assert {row["sha256"]: row["refcount"] for row in conn.execute("SELECT * FROM store_blob")} == {
        "sha-llm": 1,
        "sha-shared": 2,
    }

    # Rows only: every file is still on disk, bytes intact.
    for name, path in files.items():
        assert path.read_bytes() == name.encode()


def test_migration_014_idempotent_and_targeted(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        migrate(conn, _pre_014(tmp_path))
        files = _seed(conn, tmp_path)

        assert migrate(conn) == [14]
        _assert_only_targets_gone(conn, files)

        # A second boot is a no-op ...
        after = _snapshot(conn)
        assert migrate(conn) == []
        # ... and so is replaying the migration itself.
        conn.execute("DELETE FROM schema_migrations WHERE version = 14")
        assert migrate(conn) == [14]
        assert _snapshot(conn) == after
        _assert_only_targets_gone(conn, files)


def test_migration_014_does_not_rely_on_foreign_keys(tmp_path: Path) -> None:
    """With enforcement off nothing cascades, so the child rows must be deleted explicitly."""
    with connect(tmp_path / "t.db") as conn:
        migrate(conn, _pre_014(tmp_path))
        files = _seed(conn, tmp_path)
        conn.execute("PRAGMA foreign_keys=OFF")

        assert migrate(conn) == [14]

        _assert_only_targets_gone(conn, files)
        for table in ("model_file", "model_backend"):
            orphans = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE model_id NOT IN (SELECT id FROM model)"
            ).fetchone()[0]
            assert orphans == 0, table


def test_migration_014_on_an_empty_registry_is_a_noop(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        assert migrate(conn)[-1] == 14
        assert _snapshot(conn) == {table: [] for table in _SNAPSHOT_QUERIES}
