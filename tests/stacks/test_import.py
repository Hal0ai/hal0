"""Tests for stack import: parse/validate + resolve matrix + create.

Targeted file run:
    cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_import.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.errors import BadRequest
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.stacks import StacksCatalog
from hal0.stacks.portable import (
    export_envelope,
    import_stack,
    parse_envelope,
    resolve_models,
    verify_checksum,
)


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    r = ModelRegistry(registry_dir=tmp_path / "registry")
    r.add(Model(id="present-model", path="/m/p.gguf", hf_repo="x/p", hf_filename="p.gguf"))
    return r


def _stack() -> StackConfig:
    return StackConfig(
        name="S",
        slots=[
            StackSlotEntry(slot="agent", model="present-model"),
            StackSlotEntry(slot="chat", model="pullable-model"),
            StackSlotEntry(slot="util", model="ghost-model"),
        ],
    )


def _envelope(reg: ModelRegistry) -> dict:
    # Stack references 3 models; embed_references bare-ids the two absent ones.
    # Inject hf metadata for pullable-model so the resolve pass classifies it pullable.
    env = export_envelope(_stack(), exported_at="t", registry=reg)
    env["stack"]["models"]["pullable-model"]["hf_repo"] = "y/pull"
    env["stack"]["models"]["pullable-model"]["hf_filename"] = "pull.gguf"
    return env


class TestParseEnvelope:
    def test_valid_envelope_parses(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        parsed = parse_envelope(env)
        assert parsed.kind == "hal0.stack"
        assert parsed.stack.name == "S"

    def test_wrong_kind_rejected(self, tmp_hal0_home: str) -> None:
        with pytest.raises(BadRequest):
            parse_envelope({"kind": "not-a-stack", "stack": {}})

    def test_non_dict_rejected(self, tmp_hal0_home: str) -> None:
        with pytest.raises(BadRequest):
            parse_envelope("nope")  # type: ignore[arg-type]

    def test_too_new_schema_rejected(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["schema_version"] = 9999
        with pytest.raises(BadRequest):
            import_stack(
                env,
                "s",
                StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml"),
                registry=reg,
            )


class TestVerifyChecksum:
    def test_intact_checksum_verifies(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        assert verify_checksum(env) is True

    def test_tampered_body_fails(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["name"] = "TAMPERED"
        assert verify_checksum(env) is False


class TestResolveModels:
    def test_resolve_matrix(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        parsed = parse_envelope(_envelope(reg))
        report = resolve_models(parsed.stack, reg)
        by_id = {r.model_id: r.status for r in report.resolutions}
        assert by_id["present-model"] == "present"
        assert by_id["pullable-model"] == "pullable"
        assert by_id["ghost-model"] == "unresolvable"

    def test_report_buckets(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        report = resolve_models(parse_envelope(_envelope(reg)).stack, reg)
        assert report.present == ["present-model"]
        assert report.pullable == ["pullable-model"]
        assert report.unresolvable == ["ghost-model"]


class TestImportStack:
    def test_import_creates_stack_and_returns_report(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")
        resolved, report = import_stack(_envelope(reg), "saber", catalog, registry=reg)
        assert resolved.slug == "saber"
        assert any(r.slug == "saber" for r in catalog.list())
        assert report.pullable == ["pullable-model"]

    def test_import_reconciles_embedded_profile(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        from hal0.config.loader import load_profiles_config
        from hal0.config.schema import ProfileConfig

        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["profiles"] = {"custom-x": ProfileConfig().model_dump(mode="python")}
        env["stack"]["slots"][0]["profile"] = "custom-x"
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")
        import_stack(env, "s2", catalog, registry=reg)
        assert "custom-x" in load_profiles_config().profile, (
            "embedded profile must be reconciled into profiles.toml"
        )
