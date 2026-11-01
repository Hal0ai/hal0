"""Tests for stack import: parse/validate + resolve matrix + create.

Targeted file run:
    uv run pytest tests/stacks/test_import.py -q
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


class TestSchemaGate:
    """Schema 3 is the oldest readable stack envelope (ADR-0029)."""

    def _old_envelope(self, reg: ModelRegistry) -> dict:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["schema_version"] = 2
        env["stack"]["schema_version"] = 2
        # The shape a 1.4 export carried on every row.
        env["stack"]["slots"][0].update(vision=False, mtp=True, server_extra_args="-ctk q8_0")
        return env

    def test_schema_2_envelope_refused_with_a_clear_message(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")
        with pytest.raises(BadRequest) as exc:
            import_stack(self._old_envelope(reg), "old", catalog, registry=reg)
        assert exc.value.code == "stacks.envelope_schema_unsupported"
        assert "v2" in str(exc.value) and "re-export" in str(exc.value)
        assert not any(r.slug == "old" for r in catalog.list())

    def test_parse_refuses_it_too_so_dry_run_agrees(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        with pytest.raises(BadRequest) as exc:
            parse_envelope(self._old_envelope(reg))
        assert exc.value.code == "stacks.envelope_schema_unsupported"

    def test_schema_1_envelope_refused(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["schema_version"] = 1
        env["stack"]["schema_version"] = 1
        with pytest.raises(BadRequest) as exc:
            parse_envelope(env)
        assert exc.value.code == "stacks.envelope_schema_unsupported"

    def test_nothing_is_stripped_silently(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        """A schema-3 envelope that still carries a retired row key is invalid,
        not quietly cleaned up."""
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["slots"][0]["mtp"] = True
        with pytest.raises(BadRequest) as exc:
            parse_envelope(env)
        assert exc.value.code == "stacks.bad_envelope"


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

    def test_import_screens_embedded_profile_flags_before_writing(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        """An embedded profile carrying a flag the profile routes refuse (here a
        model-fact flag) blocks the whole import: no profile, no stack."""
        from hal0.config.loader import load_profiles_config

        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["profiles"] = {"custom-bad": {"flags": "--jinja -fa on"}}
        env["stack"]["slots"][0]["profile"] = "custom-bad"
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")
        with pytest.raises(BadRequest) as exc:
            import_stack(env, "s3", catalog, registry=reg)
        assert exc.value.code == "profiles.model_fact_flag_denied"
        assert "custom-bad" not in load_profiles_config().profile
        assert not any(r.slug == "s3" for r in catalog.list())

    def test_import_screens_a_halogen_profile_by_its_runtime_family(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        """An embedded halogen profile carries HALOGEN_* tokens (accepted); the
        same text on a llama profile, or a managed variable on a halogen one,
        blocks the whole import."""
        from hal0.config.loader import load_profiles_config
        from hal0.errors import UnprocessableEntity

        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")

        ok = export_envelope(_stack(), exported_at="t", registry=reg)
        ok["stack"]["profiles"] = {"flash-ok": {"flags": "HALOGEN_KV_SLOTS=4", "runner": "halogen"}}
        ok["stack"]["slots"][0]["profile"] = "flash-ok"
        import_stack(ok, "s4", catalog, registry=reg)
        assert load_profiles_config().profile["flash-ok"].flags == "HALOGEN_KV_SLOTS=4"

        managed = export_envelope(_stack(), exported_at="t", registry=reg)
        managed["stack"]["profiles"] = {
            "flash-bad": {"flags": "HALOGEN_API_PORT=9000", "runner": "halogen"}
        }
        managed["stack"]["slots"][0]["profile"] = "flash-bad"
        with pytest.raises(UnprocessableEntity) as exc:
            import_stack(managed, "s5", catalog, registry=reg)
        assert exc.value.code == "profiles.halogen_flag_denied"
        assert "flash-bad" not in load_profiles_config().profile

        llama = export_envelope(_stack(), exported_at="t", registry=reg)
        llama["stack"]["profiles"] = {"llama-bad": {"flags": "HALOGEN_KV_SLOTS=4"}}
        llama["stack"]["slots"][0]["profile"] = "llama-bad"
        with pytest.raises(BadRequest) as bad:
            import_stack(llama, "s6", catalog, registry=reg)
        assert bad.value.code == "slot.positional_arg_denied"
        assert "llama-bad" not in load_profiles_config().profile

    def test_import_refuses_a_halogen_context_past_the_native_window(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        from hal0.config.loader import load_profiles_config
        from hal0.errors import UnprocessableEntity

        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["profiles"] = {"flash-big": {"ctx_size": 262145, "runner": "halogen"}}
        env["stack"]["slots"][0]["profile"] = "flash-big"
        with pytest.raises(UnprocessableEntity) as exc:
            import_stack(env, "s9", catalog, registry=reg)
        assert exc.value.code == "profiles.halogen_flag_out_of_range"
        assert "flash-big" not in load_profiles_config().profile

    def test_import_refuses_ngram_speculation_on_a_halogen_profile(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        """The halogen engine has no n-gram drafter: an embedded halogen profile
        asking for it blocks the whole import, as the catalog's create does."""
        from hal0.config.loader import load_profiles_config
        from hal0.errors import UnprocessableEntity

        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["profiles"] = {
            "flash-ngram": {
                "flags": "HALOGEN_KV_SLOTS=4",
                "runner": "halogen",
                "speculation": "ngram",
            },
            "llama-ngram": {"flags": "-fa on", "speculation": "ngram"},
        }
        env["stack"]["slots"][0]["profile"] = "flash-ngram"
        with pytest.raises(UnprocessableEntity) as exc:
            import_stack(env, "s7", catalog, registry=reg)
        assert exc.value.code == "profiles.speculation_unsupported"
        assert not {"flash-ngram", "llama-ngram"} & set(load_profiles_config().profile)

        # llama-server runs n-gram lookup, so the same value stands there.
        ok = export_envelope(_stack(), exported_at="t", registry=reg)
        ok["stack"]["profiles"] = {"llama-ngram": {"flags": "-fa on", "speculation": "ngram"}}
        ok["stack"]["slots"][0]["profile"] = "llama-ngram"
        import_stack(ok, "s8", catalog, registry=reg)
        assert load_profiles_config().profile["llama-ngram"].speculation == "ngram"


def test_a_v140_stack_envelope_with_embedded_mtp_profiles_is_refused(
    reg: ModelRegistry, tmp_hal0_home: str
) -> None:
    """Stack schema 3 from hal0 1.4.0 embedded profiles still carrying ``mtp``;
    1.5 and newer have no such field, so the envelope is refused whole."""
    env = export_envelope(_stack(), exported_at="t", registry=reg)
    env["stack"]["profiles"] = {
        "quiet": {"flags": "-fa on", "mtp": "off"},
        "loud": {"flags": "-b 64", "speculation": "auto"},
    }
    with pytest.raises(BadRequest) as exc:
        parse_envelope(env)
    assert exc.value.code == "stacks.envelope_schema_unsupported"
    assert exc.value.details["profiles"] == ["quiet"]
    assert "re-export the stack from hal0 1.5 or newer" in str(exc.value)
