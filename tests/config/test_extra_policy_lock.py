"""Locks hal0.config.schema's per-model ``extra`` policy (P3-schema Part C).

PLAN.md §5 Tier 1 promises that a typo like ``backend = "vukan"`` raises a
ValidationError with the field path at load time -- that promise only holds
if every LEAF tunable table actually rejects unknown keys (``extra="forbid"``)
rather than silently swallowing them (``"allow"``) or dropping them
(``"ignore"``). This test iterates every pydantic model defined in
``hal0.config.schema`` and asserts its ``model_config["extra"]`` matches the
canonical policy table below (spec-p3-schema.final.md, Part C) -- so a
future PR that adds a new leaf config table without deciding its extra
policy fails loudly here instead of silently defaulting to permissive.

Escape-hatch table (why each non-"forbid" entry is intentional):

  - Hal0Config          -- allow: top-level forward-compat (a newer hal0
                           writing a future table must survive an older
                           reader).
  - SlotConfig          -- allow: ``extra`` + the hoist/tuck round-trip is
                           THE escape hatch for future fields/provider knobs.
  - ModelConfig         -- allow: ``[model].extra`` is the documented
                           provider-passthrough escape hatch.
  - HardwareInfo/GPUInfo/NPUInfo
                        -- allow: additive probe facts from newer probes
                           must round-trip on older readers.
  - ServerConfig        -- forbid, with ``extra_args``/``env`` as the
                           declared-field escape hatch (freeform CLI
                           passthrough / arbitrary env-var dict).
  - memory/honcho models (MemoryGraphConfig, MemoryEmbeddingConfig,
    HonchoLLMFeatureConfig, HonchoLLMConfig, HonchoConfig)
                        -- ignore: deliberate silent drop of retired
                           cognee/route keys on load.
  - ProvidersConfig/UpstreamsConfig/MemoryConfig/ModelsConfig/AgentConfig/
    AgentMetadataConfig/AgentMCPConfig/MCPServerConfig
                        -- allow: containers that nest other typed models
                           (list/dict-of-model or model-of-models), kept
                           forward-compat like Hal0Config rather than
                           forbid-hardened as leaf tunables.
  - everything else     -- forbid: no legitimate unknown key: a typo fails
                           at load time with the field path.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

from hal0.config import schema

# Canonical policy table (spec-p3-schema.final.md Part C). Keep in sync with
# schema.py -- a class not listed here fails test_every_model_is_accounted_for.
_EXPECTED_EXTRA_POLICY: dict[str, str] = {
    # Escape-hatch / forward-compat containers -> allow
    "Hal0Config": "allow",
    "SlotConfig": "allow",
    "ModelConfig": "allow",
    "HardwareInfo": "allow",
    "GPUInfo": "allow",
    "NPUInfo": "allow",
    "ProvidersConfig": "allow",
    "UpstreamsConfig": "allow",
    "MemoryConfig": "allow",
    "ModelsConfig": "allow",
    "AgentConfig": "allow",
    "AgentMetadataConfig": "allow",
    "AgentMCPConfig": "allow",
    "MCPServerConfig": "allow",
    # ServerConfig: forbid + declared-field escape hatch (extra_args/env)
    "ServerConfig": "forbid",
    # Deliberate silent-drop of retired keys -> ignore
    "MemoryGraphConfig": "ignore",
    "MemoryEmbeddingConfig": "ignore",
    "HonchoLLMFeatureConfig": "ignore",
    "HonchoLLMConfig": "ignore",
    "HonchoConfig": "ignore",
    # Leaf tunables / entries with no legitimate unknown key -> forbid
    "NpuConfig": "forbid",
    "ImageGenConfig": "forbid",
    "ProviderEntry": "forbid",
    "ProfileConfig": "forbid",
    "ProfilesConfig": "forbid",
    "StackModelMeta": "forbid",
    "StackCapabilityRow": "forbid",
    "StackSlotEntry": "forbid",
    "StackConfig": "forbid",
    "StacksConfig": "forbid",
    "UpstreamModelFilters": "forbid",
    "UpstreamEntry": "forbid",
    "MetaConfig": "forbid",
    "SlotsConfig": "forbid",
    "DispatcherConfig": "forbid",
    "TelemetryConfig": "forbid",
    "AgentAuthConfig": "forbid",
    "ToolPolicy": "forbid",
    "ActivityConfig": "forbid",
    "BrainChatConfig": "forbid",
    # [security] — the auth-enforcement toggle (O19). Forbid: a typo'd key in
    # a SECURITY table must fail loudly, never silently no-op.
    "SecurityConfig": "forbid",
}


def _schema_model_classes() -> dict[str, type[BaseModel]]:
    """Every BaseModel subclass DEFINED in hal0.config.schema (not imported)."""
    out: dict[str, type[BaseModel]] = {}
    for name, obj in vars(schema).items():
        if (
            inspect.isclass(obj)
            and issubclass(obj, BaseModel)
            and obj is not BaseModel
            and obj.__module__ == schema.__name__
        ):
            out[name] = obj
    return out


class TestExtraPolicyLock:
    def test_every_model_is_accounted_for(self) -> None:
        actual_names = set(_schema_model_classes().keys())
        expected_names = set(_EXPECTED_EXTRA_POLICY.keys())
        missing_from_table = actual_names - expected_names
        stale_in_table = expected_names - actual_names
        assert not missing_from_table, (
            f"model class(es) with no entry in _EXPECTED_EXTRA_POLICY: {sorted(missing_from_table)} "
            "-- decide their extra policy and add them to the table (spec Part C)"
        )
        assert not stale_in_table, (
            f"_EXPECTED_EXTRA_POLICY entries with no matching class (renamed/removed?): "
            f"{sorted(stale_in_table)}"
        )

    @pytest.mark.parametrize("name", sorted(_EXPECTED_EXTRA_POLICY.keys()))
    def test_policy_matches(self, name: str) -> None:
        cls = _schema_model_classes()[name]
        actual = cls.model_config.get("extra")
        expected = _EXPECTED_EXTRA_POLICY[name]
        assert actual == expected, (
            f"{name}.model_config['extra'] = {actual!r}, expected {expected!r} "
            "(spec-p3-schema.final.md Part C policy table)"
        )
