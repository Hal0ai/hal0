"""Unit tests for the [honcho] + memory.agent_providers/agent_private schema.

Honcho is an opt-in alternative memory provider selected per-agent via
``memory.agent_providers``; hal0's own ``[memory].engine`` stays 'hindsight'
regardless of what agents are routed to Honcho.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import (
    Hal0Config,
    HonchoConfig,
    HonchoLLMConfig,
    HonchoLLMFeatureConfig,
    MemoryConfig,
)


class TestHonchoDefaults:
    def test_top_level_default(self) -> None:
        c = Hal0Config()
        assert isinstance(c.honcho, HonchoConfig)
        assert c.honcho.enabled is False
        assert c.honcho.port == 8000
        assert c.honcho.workspace == "hal0"
        assert c.honcho.user_peer == "operator"
        assert c.honcho.auth_enabled is False
        assert isinstance(c.honcho.llm, HonchoLLMConfig)

    def test_llm_feature_defaults(self) -> None:
        llm = HonchoLLMConfig()
        for feature in (llm.deriver, llm.dialectic, llm.summary, llm.dream, llm.embedding):
            assert isinstance(feature, HonchoLLMFeatureConfig)
            assert feature.transport == "openai"
            assert feature.model == ""
            assert feature.base_url == ""
            assert feature.api_key_env == ""
        assert llm.embedding_dimensions == 1024

    def test_round_trips(self) -> None:
        dumped = HonchoConfig().model_dump()
        rebuilt = HonchoConfig.model_validate(dumped)
        assert rebuilt.workspace == "hal0"

    def test_hal0_config_round_trips_with_honcho(self) -> None:
        c = Hal0Config()
        dumped = c.model_dump()
        rebuilt = Hal0Config.model_validate(dumped)
        assert rebuilt.honcho.enabled is False


class TestHonchoTransportValidator:
    @pytest.mark.parametrize("transport", ["openai", "anthropic", "gemini"])
    def test_valid_transports(self, transport: str) -> None:
        f = HonchoLLMFeatureConfig(transport=transport)
        assert f.transport == transport

    def test_invalid_transport_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            HonchoLLMFeatureConfig(transport="cohere")
        assert "transport" in str(ei.value)


class TestHonchoNameValidators:
    @pytest.mark.parametrize("name", ["hal0", "a", "a-b_c", "A1", "op-2"])
    def test_valid_workspace_and_peer_names(self, name: str) -> None:
        c = HonchoConfig(workspace=name, user_peer=name)
        assert c.workspace == name
        assert c.user_peer == name

    @pytest.mark.parametrize("name", ["bad name!", "a:b", "", "a/b"])
    def test_invalid_workspace_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError) as ei:
            HonchoConfig(workspace=name)
        assert "workspace" in str(ei.value)

    @pytest.mark.parametrize("name", ["bad name!", "a:b", ""])
    def test_invalid_user_peer_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError) as ei:
            HonchoConfig(user_peer=name)
        assert "user_peer" in str(ei.value)


class TestMemoryAgentProviders:
    def test_defaults_empty(self) -> None:
        m = MemoryConfig()
        assert m.agent_providers == {}
        assert m.agent_private == {}

    def test_engine_unaffected_by_agent_providers(self) -> None:
        m = MemoryConfig(agent_providers={"hermes": "honcho"})
        assert m.engine == "hindsight"
        assert m.agent_providers == {"hermes": "honcho"}

    @pytest.mark.parametrize("provider", ["hindsight", "honcho"])
    def test_valid_provider_values(self, provider: str) -> None:
        m = MemoryConfig(agent_providers={"hermes": provider})
        assert m.agent_providers["hermes"] == provider

    def test_agent_providers_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError) as ei:
            MemoryConfig(agent_providers={"hermes": "cognee"})
        assert "agent_providers" in str(ei.value)

    def test_agent_private_flag(self) -> None:
        m = MemoryConfig(agent_private={"hermes": True})
        assert m.agent_private["hermes"] is True

    def test_hal0_config_carries_agent_providers(self) -> None:
        c = Hal0Config.model_validate(
            {"memory": {"agent_providers": {"hermes": "honcho"}, "agent_private": {"hermes": False}}}
        )
        assert c.memory.agent_providers == {"hermes": "honcho"}
        assert c.memory.agent_private == {"hermes": False}
        assert c.memory.engine == "hindsight"
