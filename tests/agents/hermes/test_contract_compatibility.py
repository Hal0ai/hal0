"""Compatibility freeze for the supported official Hermes release."""

from inspect import signature
from pathlib import Path

from tests.fixtures.hermes.contracts.memory_provider import MemoryProvider
from tests.fixtures.hermes.contracts.provider_profile import ProviderProfile
from tests.fixtures.hermes.contracts.voice import PluginContext

HERMES_COMMIT = "9de9c25f620ff7f1ce0fd5457d596052d5159596"


def test_provider_uses_chat_completions_contract() -> None:
    profile = ProviderProfile(name="hal0", base_url="http://127.0.0.1/v1")

    assert profile.api_mode == "chat_completions"


def test_memory_provider_preserves_turn_lifecycle_signatures() -> None:
    assert str(signature(MemoryProvider.system_prompt_block)) == "(self) -> 'str'"
    assert str(signature(MemoryProvider.prefetch)) == (
        "(self, query: 'str', *, session_id: 'str' = '') -> 'str'"
    )
    assert str(signature(MemoryProvider.sync_turn)) == (
        "(self, user_content: 'str', assistant_content: 'str', *, "
        "session_id: 'str' = '', messages: 'Optional[List[Dict[str, Any]]]' = None) "
        "-> 'None'"
    )


def test_voice_registration_callables_match_plugin_context() -> None:
    assert str(signature(PluginContext.register_tts_provider)) == ("(self, provider) -> 'None'")
    assert str(signature(PluginContext.register_transcription_provider)) == (
        "(self, provider) -> 'None'"
    )


def test_installer_pins_reviewed_official_commit() -> None:
    requirements = (
        Path(__file__).parents[3] / "installer/agents/hermes/requirements.txt"
    ).read_text()

    assert (
        "hermes-agent[web] @ git+https://github.com/NousResearch/hermes-agent.git@"
        f"{HERMES_COMMIT}" in requirements
    )
