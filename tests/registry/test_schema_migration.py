"""Schema-migration tests for the Phase-1 Model additions.

Covers:
  * New optional fields (``backends``, ``defaults``) round-trip through
    ``ModelRegistry`` cleanly when present.
  * Legacy entries on disk (no ``backends``/``defaults``/``metadata.context_length``
    keys) load with sensible defaults — no validation failure, no data loss
    on next write.
  * ``ModelDefaults`` partial fields round-trip; ``None`` leaves drop
    out of the on-disk TOML rather than crashing tomli_w.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.registry.model import Model, ModelDefaults

# ML-1: this file asserts on the literal on-disk TOML shape (tomllib reads
# of registry.toml), so it exercises the concrete TOML implementation,
# aliased to the local name `ModelRegistry`.
from hal0.registry.store import TomlModelRegistry as ModelRegistry


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=tmp_path / "registry")


def _model(model_id: str = "qwen3-4b", path: str = "/m/q.gguf", **kw) -> Model:
    return Model(id=model_id, path=path, **kw)


# ── defaults present ──────────────────────────────────────────────────────


class TestNewFieldsRoundTrip:
    def test_backends_round_trip(self, reg: ModelRegistry) -> None:
        m = _model("a", backends=["vulkan", "rocm", "cuda", "cpu"])
        reg.add(m)
        got = reg.get("a")
        assert got.backends == ["vulkan", "rocm", "cuda", "cpu"]

    def test_defaults_full_round_trip(self, reg: ModelRegistry) -> None:
        # NGL is slot-owned now (spec-hw-slot-ownership) — not a ModelDefaults
        # field. The model tune is logical flags only.
        m = _model(
            "a",
            defaults=ModelDefaults(
                context_size=8192,
                rope_freq_base=1000000.0,
                extra_args="-b 8192",
            ),
        )
        reg.add(m)
        got = reg.get("a")
        assert got.defaults is not None
        assert got.defaults.context_size == 8192
        assert got.defaults.rope_freq_base == 1000000.0
        assert got.defaults.extra_args == "-b 8192"

    def test_defaults_partial_round_trip(self, reg: ModelRegistry) -> None:
        """Only some ModelDefaults fields set — others stay None."""
        m = _model("a", defaults=ModelDefaults(context_size=4096))
        reg.add(m)
        got = reg.get("a")
        assert got.defaults is not None
        assert got.defaults.context_size == 4096
        assert got.defaults.rope_freq_base is None
        assert got.defaults.extra_args is None

    def test_defaults_none_not_written(self, reg: ModelRegistry) -> None:
        """defaults=None must not appear in the on-disk TOML at all."""
        reg.add(_model("a", defaults=None))
        with open(reg.registry_file, "rb") as f:
            raw = tomllib.load(f)
        entry = raw["models"]["a"]
        assert "defaults" not in entry

    def test_empty_defaults_collapses(self, reg: ModelRegistry) -> None:
        """All-None ModelDefaults() collapses to no on-disk section."""
        reg.add(_model("a", defaults=ModelDefaults()))
        with open(reg.registry_file, "rb") as f:
            raw = tomllib.load(f)
        assert "defaults" not in raw["models"]["a"]

    def test_metadata_context_length_round_trip(self, reg: ModelRegistry) -> None:
        m = _model("a", metadata={"context_length": 32768})
        reg.add(m)
        got = reg.get("a")
        assert got.metadata.get("context_length") == 32768

    def test_metadata_none_value_stripped(self, reg: ModelRegistry) -> None:
        """metadata values that are None get dropped on write (TOML has no null)."""
        reg.add(_model("a", metadata={"context_length": None, "upstream_url": "http://x"}))
        with open(reg.registry_file, "rb") as f:
            raw = tomllib.load(f)
        assert raw["models"]["a"]["metadata"] == {"upstream_url": "http://x"}


# ── migration: legacy on-disk entries ─────────────────────────────────────


class TestLegacyMigration:
    def test_legacy_entry_loads_with_defaults(self, tmp_path: Path) -> None:
        """An entry with no backends/defaults keys parses fine."""
        rdir = tmp_path / "registry"
        rdir.mkdir()
        # Pre-Phase-1 shape: only the fields that already existed.
        (rdir / "registry.toml").write_text(
            "[models.legacy]\n"
            'path = "/m/legacy.gguf"\n'
            'name = "Legacy 7B"\n'
            "size_bytes = 4000000000\n"
            'license = "Apache-2.0"\n'
            'capabilities = ["chat"]\n'
            "tags = []\n"
            "[models.legacy.metadata]\n"
            "discovered = true\n"
        )
        reg = ModelRegistry(registry_dir=rdir)
        m = reg.get("legacy")
        assert m.backends == []
        assert m.defaults is None
        assert m.capabilities == ["chat"]
        assert m.metadata == {"discovered": True}

    def test_legacy_update_preserves_then_adds_new_fields(self, tmp_path: Path) -> None:
        """Update on a legacy entry can add backends + defaults without losing data."""
        rdir = tmp_path / "registry"
        rdir.mkdir()
        (rdir / "registry.toml").write_text(
            '[models.legacy]\npath = "/m/legacy.gguf"\nname = "Legacy"\ncapabilities = ["chat"]\n'
        )
        reg = ModelRegistry(registry_dir=rdir)
        updated = reg.update(
            "legacy",
            {
                "backends": ["vulkan", "cpu"],
                "defaults": {"context_size": 2048},
                "metadata": {"context_length": 4096},
            },
        )
        assert updated.backends == ["vulkan", "cpu"]
        assert updated.defaults is not None
        assert updated.defaults.context_size == 2048
        assert updated.metadata.get("context_length") == 4096
        # Pre-existing fields are intact.
        assert updated.name == "Legacy"
        assert updated.capabilities == ["chat"]

        # Round-trip via a fresh registry instance.
        reg2 = ModelRegistry(registry_dir=rdir)
        m = reg2.get("legacy")
        assert m.backends == ["vulkan", "cpu"]
        assert m.defaults is not None and m.defaults.context_size == 2048
        assert m.metadata.get("context_length") == 4096

    def test_mixed_legacy_and_new_entries_coexist(self, tmp_path: Path) -> None:
        rdir = tmp_path / "registry"
        rdir.mkdir()
        (rdir / "registry.toml").write_text(
            # Legacy entry — no backends/defaults.
            "[models.old]\n"
            'path = "/m/o.gguf"\n'
            'capabilities = ["chat"]\n'
            # New-style entry — full schema.
            "[models.new]\n"
            'path = "/m/n.gguf"\n'
            'capabilities = ["embed"]\n'
            'backends = ["vulkan", "rocm"]\n'
            "[models.new.defaults]\n"
            "context_size = 4096\n"
            # A prior n_gpu_layers key under defaults is TOLERATED (ignored) —
            # NGL is slot-owned now (spec-hw-slot-ownership); no longer a
            # ModelDefaults field.
            "n_gpu_layers = -1\n"
            "[models.new.metadata]\n"
            "context_length = 4096\n"
        )
        reg = ModelRegistry(registry_dir=rdir)
        old = reg.get("old")
        new = reg.get("new")
        assert old.backends == []
        assert old.defaults is None
        assert new.backends == ["vulkan", "rocm"]
        assert new.defaults is not None
        assert new.defaults.context_size == 4096
        assert not hasattr(new.defaults, "n_gpu_layers")  # field is gone
        assert new.metadata.get("context_length") == 4096


# ── full re-read after add ────────────────────────────────────────────────


class TestFreshInstanceReread:
    def test_new_fields_survive_fresh_registry_instance(self, tmp_path: Path) -> None:
        rdir = tmp_path / "registry"
        reg = ModelRegistry(registry_dir=rdir)
        reg.add(
            _model(
                "a",
                backends=["vulkan", "cpu"],
                defaults=ModelDefaults(context_size=4096, extra_args="--no-mmap"),
                metadata={"context_length": 8192},
            )
        )
        reg2 = ModelRegistry(registry_dir=rdir)
        m = reg2.get("a")
        assert m.backends == ["vulkan", "cpu"]
        assert m.defaults is not None
        assert m.defaults.context_size == 4096
        assert m.defaults.extra_args == "--no-mmap"
        assert m.metadata.get("context_length") == 8192
