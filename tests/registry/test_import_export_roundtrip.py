"""TOML ⇄ SQLite round-trip tests for the ML-1 registry pilot.

Covers:
  * `import_toml_to_sqlite` copies every field losslessly (name, path,
    size_bytes, quant, license, capabilities, hf_repo, hf_filename, tags,
    backends, mmproj, defaults.*, metadata incl. upstream_url) — the exact
    fields the ML-1 spec calls out as missing from the illustrative §8.2
    DDL draft.
  * `INSERT OR IGNORE` idempotency: re-running the import never clobbers
    a model id already present in SQLite, even one edited post-import.
  * Malformed TOML entries are skipped, not raised, mirroring the TOML
    store's own read-path tolerance.
  * Export (`model_to_toml_dict` over `SqliteModelRegistry.list()`)
    reproduces the same TOML shape the original TOML store would have
    written.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w

from hal0.registry.import_toml import import_toml_to_sqlite
from hal0.registry.model import Model, ModelDefaults
from hal0.registry.sqlite_store import SqliteModelRegistry
from hal0.registry.store import model_to_toml_dict


def _write_registry_toml(path: Path, models: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump({"models": models}, f)


_FULL_ENTRY = {
    "name": "Qwen3 4B (Q4_K_M)",
    "path": "/models/qwen3-4b.gguf",
    "size_bytes": 4_000_000_000,
    "quant": "Q4_K_M",
    "license": "Apache-2.0",
    "capabilities": ["chat", "embed"],
    "hf_repo": "Qwen/Qwen3-4B-GGUF",
    "hf_filename": "qwen3-4b-q4_k_m.gguf",
    "tags": ["curated", "vision"],
    "backends": ["vulkan", "rocm", "cpu"],
    "mmproj": "/models/qwen3-4b-mmproj.gguf",
    "defaults": {
        "context_size": 8192,
        "n_gpu_layers": -1,
        "rope_freq_base": 1000000.0,
        "extra_args": "--threads 8",
        "chat_template": "chatml",
        "profile": "balanced",
    },
    "metadata": {
        "context_length": 32768,
        "upstream_url": "http://127.0.0.1:8081",
        "discovered": True,
    },
}


class TestLosslessImport:
    def test_every_field_round_trips(self, tmp_path: Path) -> None:
        registry_dir = tmp_path / "registry"
        rfile = registry_dir / "registry.toml"
        _write_registry_toml(rfile, {"qwen3-4b": _FULL_ENTRY})

        report = import_toml_to_sqlite(registry_file=rfile, db_path=tmp_path / "hal0.db")
        assert report.imported == 1
        assert report.skipped_existing == 0
        assert report.skipped_invalid == 0

        registry = SqliteModelRegistry(db_path=tmp_path / "hal0.db")
        got = registry.get("qwen3-4b")

        assert got.id == "qwen3-4b"
        assert got.name == "Qwen3 4B (Q4_K_M)"
        assert got.path == "/models/qwen3-4b.gguf"
        assert got.size_bytes == 4_000_000_000
        assert got.quant == "Q4_K_M"
        assert got.license == "Apache-2.0"
        assert got.capabilities == ["chat", "embed"]
        assert got.hf_repo == "Qwen/Qwen3-4B-GGUF"
        assert got.hf_filename == "qwen3-4b-q4_k_m.gguf"
        assert got.tags == ["curated", "vision"]
        assert got.backends == ["vulkan", "rocm", "cpu"]
        assert got.mmproj == "/models/qwen3-4b-mmproj.gguf"

        assert got.defaults is not None
        assert got.defaults.context_size == 8192
        assert got.defaults.n_gpu_layers == -1
        assert got.defaults.rope_freq_base == 1000000.0
        assert got.defaults.extra_args == "--threads 8"
        assert got.defaults.chat_template == "chatml"
        assert got.defaults.profile == "balanced"

        assert got.metadata == {
            "context_length": 32768,
            "upstream_url": "http://127.0.0.1:8081",
            "discovered": True,
        }
        assert registry.route_for("qwen3-4b") == "http://127.0.0.1:8081"

    def test_minimal_entry_round_trips_with_defaults(self, tmp_path: Path) -> None:
        """An entry with only the required fields (id/path) still imports
        cleanly with every optional field at its Model default."""
        registry_dir = tmp_path / "registry"
        rfile = registry_dir / "registry.toml"
        _write_registry_toml(rfile, {"bare": {"path": "/m/bare.gguf"}})

        import_toml_to_sqlite(registry_file=rfile, db_path=tmp_path / "hal0.db")
        registry = SqliteModelRegistry(db_path=tmp_path / "hal0.db")
        got = registry.get("bare")

        assert got.path == "/m/bare.gguf"
        assert got.name == ""
        assert got.size_bytes == 0
        assert got.quant is None
        assert got.license == "unknown"
        assert got.capabilities == []
        assert got.tags == []
        assert got.backends == []
        assert got.defaults is None
        assert got.metadata == {}

    def test_missing_registry_file_is_a_noop(self, tmp_path: Path) -> None:
        report = import_toml_to_sqlite(
            registry_file=tmp_path / "does-not-exist" / "registry.toml",
            db_path=tmp_path / "hal0.db",
        )
        assert report.imported == 0
        assert report.skipped_existing == 0
        assert report.skipped_invalid == 0

    def test_malformed_entry_is_skipped_not_raised(self, tmp_path: Path) -> None:
        rfile = tmp_path / "registry" / "registry.toml"
        rfile.parent.mkdir(parents=True, exist_ok=True)
        # `bad` has no `path` (required) -> fails Model validation; `good`
        # is valid and must still import.
        rfile.write_text(
            '[models.bad]\nname = "no path here"\n[models.good]\npath = "/m/good.gguf"\n'
        )
        report = import_toml_to_sqlite(registry_file=rfile, db_path=tmp_path / "hal0.db")
        assert report.imported == 1
        assert report.skipped_invalid == 1

        registry = SqliteModelRegistry(db_path=tmp_path / "hal0.db")
        assert registry.has("good")
        assert not registry.has("bad")


class TestImportIdempotency:
    def test_rerunning_import_does_not_reimport(self, tmp_path: Path) -> None:
        rfile = tmp_path / "registry" / "registry.toml"
        _write_registry_toml(rfile, {"a": {"path": "/m/a.gguf"}})
        db_path = tmp_path / "hal0.db"

        first = import_toml_to_sqlite(registry_file=rfile, db_path=db_path)
        second = import_toml_to_sqlite(registry_file=rfile, db_path=db_path)

        assert first.imported == 1
        assert second.imported == 0
        assert second.skipped_existing == 1

    def test_rerunning_import_never_clobbers_a_post_import_edit(self, tmp_path: Path) -> None:
        """The whole point of INSERT OR IGNORE over REPLACE: a SQLite edit
        made after the first import must survive a second import run."""
        rfile = tmp_path / "registry" / "registry.toml"
        _write_registry_toml(rfile, {"a": {"path": "/m/a.gguf", "name": "Original"}})
        db_path = tmp_path / "hal0.db"

        import_toml_to_sqlite(registry_file=rfile, db_path=db_path)

        registry = SqliteModelRegistry(db_path=db_path)
        registry.update("a", {"name": "Edited after import"})

        import_toml_to_sqlite(registry_file=rfile, db_path=db_path)

        assert registry.get("a").name == "Edited after import"

    def test_first_boot_import_fires_automatically_on_empty_db(self, tmp_path: Path) -> None:
        """SqliteModelRegistry itself triggers the one-shot import the
        first time it touches an empty database that sits next to an
        existing registry.toml — the ML-1 cutover path for existing
        installs, with no lifespan/startup wiring required."""
        registry_dir = tmp_path / "registry"
        _write_registry_toml(
            registry_dir / "registry.toml", {"pre-existing": {"path": "/m/p.gguf"}}
        )

        registry = SqliteModelRegistry(registry_dir=registry_dir)
        assert registry.has("pre-existing")

    def test_first_boot_import_does_not_reimport_on_second_instance(self, tmp_path: Path) -> None:
        registry_dir = tmp_path / "registry"
        _write_registry_toml(registry_dir / "registry.toml", {"a": {"path": "/m/a.gguf"}})

        reg1 = SqliteModelRegistry(registry_dir=registry_dir)
        reg1.update("a", {"name": "edited"})

        # A second instance against the same (now non-empty) DB must not
        # re-trigger the import and stomp the edit.
        reg2 = SqliteModelRegistry(registry_dir=registry_dir)
        assert reg2.get("a").name == "edited"


class TestExport:
    def test_export_reproduces_toml_shape(self, tmp_path: Path) -> None:
        registry_dir = tmp_path / "registry"
        registry = SqliteModelRegistry(registry_dir=registry_dir)
        registry.add(
            Model(
                id="a",
                path="/m/a.gguf",
                name="A",
                capabilities=["chat"],
                defaults=ModelDefaults(context_size=4096),
                metadata={"upstream_url": "http://x"},
            )
        )

        models = registry.list()
        payload = {"models": {m.id: model_to_toml_dict(m) for m in models}}
        raw = tomllib.loads(tomli_w.dumps(payload))

        entry = raw["models"]["a"]
        assert entry["path"] == "/m/a.gguf"
        assert entry["name"] == "A"
        assert entry["capabilities"] == ["chat"]
        assert entry["defaults"] == {"context_size": 4096}
        assert entry["metadata"] == {"upstream_url": "http://x"}
        # None-valued fields never appear (TOML has no null) — same
        # contract the TOML store's _model_to_toml always guaranteed.
        assert "quant" not in entry or entry.get("quant") is not None
