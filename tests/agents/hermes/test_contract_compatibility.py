"""Compatibility freeze for the supported official Hermes release.

Every assertion here freezes one Hermes SDK / plugin / HTTP / config touchpoint
that a planned hal0 adapter lane consumes, copied verbatim from the reviewed
official pin ``9de9c25f`` (NousResearch/hermes-agent v2026.7.7.2 / 0.18.2). The
frozen fixtures live under ``tests/fixtures/hermes/contracts/`` and each is
labelled with its consuming adapter lane.

Together with the installer pin and the ``hermes-sdk-diff`` drift-watch
(``[tool.hal0.upstream-hermes]`` in pyproject.toml), this is the tripwire: a
Hermes version bump that would break an adapter must re-vendor a fixture and
consciously update a frozen signature/value here BEFORE the adapter lane builds
against a verified surface.

Lanes: hal0-memory, hal0-provider, hal0-voice, hal0-hermes-executor,
hal0-hermes-automation, plus the cross-cutting security checklist (item 4).
"""

import tomllib
from inspect import signature
from pathlib import Path

import pytest

from tests.fixtures.hermes.contracts import api_surface, config_defaults
from tests.fixtures.hermes.contracts.memory_loader import (
    MEMORY_PLUGIN_SUBDIR,
    _ProviderCollector,
)
from tests.fixtures.hermes.contracts.memory_provider import MemoryProvider
from tests.fixtures.hermes.contracts.plugin_context import PluginContext
from tests.fixtures.hermes.contracts.provider_profile import (
    ProviderProfile,
    get_provider_profile,
    register_provider,
)

HERMES_COMMIT = "9de9c25f620ff7f1ce0fd5457d596052d5159596"
HERMES_REPO = "https://github.com/NousResearch/hermes-agent"
_REPO_ROOT = Path(__file__).parents[3]


# ── lane: hal0-provider ────────────────────────────────────────────────────


def test_provider_uses_chat_completions_contract() -> None:
    profile = ProviderProfile(name="hal0", base_url="http://127.0.0.1/v1")

    assert profile.api_mode == "chat_completions"


def test_provider_profile_discovery_fields_are_frozen() -> None:
    profile = ProviderProfile(name="hal0")

    # Discovery/auth/capability seams the provider lane reads.
    assert profile.auth_type == "api_key"
    assert profile.supports_health_check is True
    assert profile.supports_vision is False
    assert profile.aliases == ()
    assert profile.models_url == ""


def test_provider_profile_fetch_models_signature() -> None:
    assert str(signature(ProviderProfile.fetch_models)) == (
        "(self, *, api_key: 'str | None' = None, base_url: 'str | None' = None, "
        "timeout: 'float' = 8.0) -> 'list[str] | None'"
    )


def test_provider_registration_seam_signatures() -> None:
    assert str(signature(register_provider)) == "(profile: 'ProviderProfile') -> 'None'"
    assert str(signature(get_provider_profile)) == "(name: 'str') -> 'ProviderProfile | None'"


def test_provider_registration_name_override() -> None:
    # A later registration under the same name replaces the earlier one, so a
    # hal0 profile under plugins/model-providers/hal0/ overrides a bundled one.
    register_provider(ProviderProfile(name="hal0-x", aliases=("main",)))
    register_provider(ProviderProfile(name="hal0-x", base_url="http://127.0.0.1/v1"))

    assert get_provider_profile("hal0-x").base_url == "http://127.0.0.1/v1"
    assert get_provider_profile("main").name == "hal0-x"


# ── lane: hal0-memory ──────────────────────────────────────────────────────

# Frozen roster: every MemoryProvider lifecycle seam the design doc references.
_MEMORY_ROSTER = frozenset(
    {
        "name",
        "is_available",
        "initialize",
        "system_prompt_block",
        "prefetch",
        "queue_prefetch",
        "sync_turn",
        "get_tool_schemas",
        "handle_tool_call",
        "shutdown",
        "on_turn_start",
        "on_session_end",
        "on_session_switch",
        "on_pre_compress",
        "on_delegation",
        "get_config_schema",
        "save_config",
        "on_memory_write",
        "backup_paths",
    }
)


def test_memory_provider_roster_is_frozen() -> None:
    public = {n for n in dir(MemoryProvider) if not n.startswith("_")}
    assert public == _MEMORY_ROSTER


def test_memory_provider_preserves_turn_lifecycle_signatures() -> None:
    assert str(signature(MemoryProvider.system_prompt_block)) == "(self) -> 'str'"
    assert str(signature(MemoryProvider.prefetch)) == (
        "(self, query: 'str', *, session_id: 'str' = '') -> 'str'"
    )
    assert str(signature(MemoryProvider.queue_prefetch)) == (
        "(self, query: 'str', *, session_id: 'str' = '') -> 'None'"
    )
    assert str(signature(MemoryProvider.sync_turn)) == (
        "(self, user_content: 'str', assistant_content: 'str', *, "
        "session_id: 'str' = '', messages: 'Optional[List[Dict[str, Any]]]' = None) "
        "-> 'None'"
    )


def test_memory_provider_identity_and_availability_signatures() -> None:
    assert str(signature(MemoryProvider.is_available)) == "(self) -> 'bool'"
    assert str(signature(MemoryProvider.initialize)) == (
        "(self, session_id: 'str', **kwargs) -> 'None'"
    )


def test_memory_provider_capture_and_compression_hook_signatures() -> None:
    assert str(signature(MemoryProvider.on_pre_compress)) == (
        "(self, messages: 'List[Dict[str, Any]]') -> 'str'"
    )
    assert str(signature(MemoryProvider.on_session_end)) == (
        "(self, messages: 'List[Dict[str, Any]]') -> 'None'"
    )
    assert str(signature(MemoryProvider.on_memory_write)) == (
        "(self, action: 'str', target: 'str', content: 'str', "
        "metadata: 'Optional[Dict[str, Any]]' = None) -> 'None'"
    )
    assert str(signature(MemoryProvider.on_delegation)) == (
        "(self, task: 'str', result: 'str', *, child_session_id: 'str' = '', **kwargs) -> 'None'"
    )


def test_memory_provider_setup_schema_signatures() -> None:
    assert str(signature(MemoryProvider.get_config_schema)) == "(self) -> 'List[Dict[str, Any]]'"
    assert str(signature(MemoryProvider.save_config)) == (
        "(self, values: 'Dict[str, Any]', hermes_home: 'str') -> 'None'"
    )
    assert str(signature(MemoryProvider.backup_paths)) == "(self) -> 'List[str]'"


def test_memory_loader_layout_and_registration_collector() -> None:
    # Installed seed lives under the specialized subtree, NOT top-level plugins/.
    assert MEMORY_PLUGIN_SUBDIR == "plugins/memory"
    # The memory loader's collector exposes register_memory_provider; the general
    # PluginContext deliberately does not.
    assert hasattr(_ProviderCollector, "register_memory_provider")
    assert not hasattr(PluginContext, "register_memory_provider")


def test_memory_provider_config_selection_key() -> None:
    assert config_defaults.MEMORY_PROVIDER_CONFIG_KEY == "memory.provider"


# ── lane: hal0-voice ───────────────────────────────────────────────────────


def test_voice_registration_callables_match_plugin_context() -> None:
    assert str(signature(PluginContext.register_tts_provider)) == "(self, provider) -> 'None'"
    assert str(signature(PluginContext.register_transcription_provider)) == (
        "(self, provider) -> 'None'"
    )


def test_voice_command_provider_config_keys() -> None:
    assert config_defaults.TTS_PROVIDER_CONFIG_KEY == "tts.provider"
    assert config_defaults.STT_PROVIDER_CONFIG_KEY == "stt.provider"


# ── shared registration seams (executor bridge / tools / auxiliary) ────────


def test_plugin_context_registration_signatures() -> None:
    assert str(signature(PluginContext.register_context_engine)) == "(self, engine) -> 'None'"
    assert str(signature(PluginContext.register_hook)) == (
        "(self, hook_name: 'str', callback: 'Callable') -> 'None'"
    )
    assert str(signature(PluginContext.dispatch_tool)) == (
        "(self, tool_name: 'str', args: 'dict', **kwargs) -> 'str'"
    )
    assert str(signature(PluginContext.inject_message)) == (
        "(self, content: 'str', role: 'str' = 'user') -> 'bool'"
    )
    assert str(signature(PluginContext.register_auxiliary_task)) == (
        "(self, key: 'str', *, display_name: 'str', description: 'str', "
        "defaults: 'Optional[Dict[str, Any]]' = None) -> 'None'"
    )


# ── lane: hal0-hermes-executor (run + session HTTP surface) ────────────────


def test_executor_run_routes_are_frozen() -> None:
    assert api_surface.RUN_ROUTES == (
        ("post", "/v1/runs"),
        ("get", "/v1/runs/{run_id}"),
        ("get", "/v1/runs/{run_id}/events"),
        ("post", "/v1/runs/{run_id}/approval"),
        ("post", "/v1/runs/{run_id}/stop"),
    )


def test_executor_session_routes_present() -> None:
    # dispatch / inspect / fork / cancel-by-delete surface the bridge needs.
    assert ("post", "/api/sessions") in api_surface.SESSION_ROUTES
    assert ("get", "/api/sessions") in api_surface.SESSION_ROUTES
    assert ("delete", "/api/sessions/{session_id}") in api_surface.SESSION_ROUTES
    assert ("post", "/api/sessions/{session_id}/fork") in api_surface.SESSION_ROUTES


def test_provider_chat_completions_route_present() -> None:
    assert ("post", "/v1/chat/completions") in api_surface.CHAT_ROUTES


# ── lane: hal0-hermes-automation (Jobs API + managed-cron webhook) ─────────


def test_automation_jobs_routes_are_frozen() -> None:
    assert api_surface.JOB_ROUTES == (
        ("get", "/api/jobs"),
        ("post", "/api/jobs"),
        ("get", "/api/jobs/{job_id}"),
        ("patch", "/api/jobs/{job_id}"),
        ("delete", "/api/jobs/{job_id}"),
        ("post", "/api/jobs/{job_id}/pause"),
        ("post", "/api/jobs/{job_id}/resume"),
        ("post", "/api/jobs/{job_id}/run"),
        ("post", "/api/cron/fire"),
    )


# ── SECURITY CHECKLIST (board fold, lxc105 — item 4) ───────────────────────


def test_api_server_never_defaults_to_non_loopback_bind() -> None:
    # API_SERVER_HOST default must stay loopback; a bump to 0.0.0.0 trips here.
    assert api_surface.DEFAULT_HOST == "127.0.0.1"
    assert api_surface.DEFAULT_HOST != "0.0.0.0"
    assert config_defaults.API_SERVER_HOST_DEFAULT == "127.0.0.1"


def test_api_server_refuses_placeholder_or_weak_key() -> None:
    # Server refuses to start without API_SERVER_KEY (even on loopback) and
    # rejects placeholder / <16-char keys — no shipped "change-me-local-dev".
    assert api_surface.API_SERVER_KEY_REQUIRED_TO_START is True
    assert api_surface.API_SERVER_KEY_MIN_LENGTH == 16
    assert "changeme" in api_surface.PLACEHOLDER_SECRET_VALUES
    assert len(api_surface.PLACEHOLDER_SECRET_VALUES) >= 12


@pytest.mark.xfail(
    reason=(
        "SECURITY item-4 deviation: the pinned Hermes ref defaults "
        "terminal.backend to 'local' (host command execution), which the "
        "lxc105 checklist flags as an unsandboxed-by-default local terminal. "
        "hal0's compensating control is the systemd-sandboxed Hermes service; "
        "hal0's own provisioning (src/hal0/agents/hermes_provision.py) also "
        "explicitly sets terminal.backend='local'. Orchestrator decides whether "
        "to require an explicit sandboxed backend. Recorded, not silently "
        "accepted — see the handback."
    ),
    strict=True,
)
def test_terminal_backend_default_is_sandboxed() -> None:
    assert config_defaults.TERMINAL_BACKEND_DEFAULT != "local"


# ── pin + drift-watch lockstep ─────────────────────────────────────────────


def test_installer_pins_reviewed_official_commit() -> None:
    requirements = (_REPO_ROOT / "installer/agents/hermes/requirements.txt").read_text()

    assert (
        "hermes-agent[web] @ git+https://github.com/NousResearch/hermes-agent.git@"
        f"{HERMES_COMMIT}" in requirements
    )


def _upstream_pin() -> dict:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    return data["tool"]["hal0"]["upstream-hermes"]


def test_sdk_diff_pin_matches_frozen_commit() -> None:
    # The drift-watch, the installer pin, and these fixtures stay in lockstep.
    pin = _upstream_pin()
    assert pin["commit"] == HERMES_COMMIT
    assert pin["repo"] == HERMES_REPO


def test_sdk_diff_tracks_full_adapter_contract_surface() -> None:
    tracked = set(_upstream_pin()["tracked_files"])
    # Every frozen contract fixture's upstream source file must be drift-watched.
    required = {
        "agent/memory_provider.py",
        "plugins/memory/__init__.py",
        "providers/base.py",
        "providers/__init__.py",
        "agent/tts_registry.py",
        "agent/transcription_registry.py",
        "gateway/platforms/api_server.py",
        "hermes_cli/plugins.py",
        "hermes_cli/config.py",
        "hermes_cli/auth.py",
    }
    missing = required - tracked
    assert not missing, f"drift-watch missing contract surfaces: {sorted(missing)}"
    # The stale earendil-only path must not linger (does not exist at the pin).
    assert "agent/events.py" not in tracked
