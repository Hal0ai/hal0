"""Compatibility freeze for the supported official Hermes release.

Every assertion here freezes one Hermes SDK / plugin / HTTP / config touchpoint
that a planned hal0 adapter lane consumes, copied verbatim from the reviewed
official pin ``4d043038`` (NousResearch/hermes-agent v0.22.4; v0.22.2
``d923a5eb`` before it, ADR-0034 decision 8). The frozen fixtures live under ``tests/fixtures/hermes/contracts/``
and each is labelled with its consuming adapter lane. ADR-0032 decision 15
re-vendors them at every pin bump.

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

from tests.fixtures.hermes.contracts import api_surface, config_defaults, kanban_runs
from tests.fixtures.hermes.contracts.memory_loader import (
    MEMORY_PLUGIN_SUBDIR,
    USER_PLUGIN_SUBDIR,
    _ProviderCollector,
)
from tests.fixtures.hermes.contracts.memory_provider import MemoryProvider
from tests.fixtures.hermes.contracts.plugin_context import PluginContext, PluginRegistration
from tests.fixtures.hermes.contracts.provider_profile import (
    ProviderProfile,
    get_provider_profile,
    register_provider,
)

HERMES_COMMIT = "4d04303818b61bf3aa09278af48356315515965b"
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


def test_provider_profile_default_vision_model_hook() -> None:
    # Added upstream in 0.19.0; hal0-provider inherits the None default.
    assert str(signature(ProviderProfile.default_vision_model)) == "(self) -> 'str | None'"
    assert ProviderProfile(name="hal0").default_vision_model() is None


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

# Frozen roster: every public MemoryProvider member at the pin.
_MEMORY_ROSTER = frozenset(
    {
        "name",
        "is_available",
        "initialize",
        "unavailable_reason",
        "recall_status",
        "identity_signature",
        "pre_compress_checkpoint_api_version",
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


def test_memory_provider_required_methods_are_unchanged() -> None:
    # hal0-memory implements exactly these; a new abstract method would make
    # Hermes refuse to instantiate the provider.
    assert frozenset({"name", "is_available", "initialize", "get_tool_schemas"}) == (
        MemoryProvider.__abstractmethods__
    )


def test_memory_provider_optional_hooks_added_since_0_19_have_safe_defaults() -> None:
    assert str(signature(MemoryProvider.unavailable_reason)) == "(self) -> 'str'"
    assert str(signature(MemoryProvider.recall_status)) == "(self) -> 'Optional[RecallStatus]'"
    assert str(signature(MemoryProvider.identity_signature)) == "(self) -> 'dict[str, Any]'"
    assert MemoryProvider.pre_compress_checkpoint_api_version == 1
    assert str(signature(MemoryProvider.on_turn_start)) == (
        "(self, turn_number: 'int', message: 'str', **kwargs) -> 'None'"
    )
    assert str(signature(MemoryProvider.on_session_switch)) == (
        "(self, new_session_id: 'str', *, parent_session_id: 'str' = '', "
        "reset: 'bool' = False, rewound: 'bool' = False, **kwargs) -> 'None'"
    )


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
        "session_id: 'str' = '', messages: 'Optional[list[dict[str, Any]]]' = None, "
        "turn_author: 'Optional[dict[str, Any]]' = None) -> 'None'"
    )


def test_memory_provider_identity_and_availability_signatures() -> None:
    assert str(signature(MemoryProvider.is_available)) == "(self) -> 'bool'"
    assert str(signature(MemoryProvider.initialize)) == (
        "(self, session_id: 'str', **kwargs) -> 'None'"
    )


def test_memory_provider_capture_and_compression_hook_signatures() -> None:
    assert str(signature(MemoryProvider.on_pre_compress)) == (
        "(self, messages: 'list[dict[str, Any]]') -> 'str'"
    )
    assert str(signature(MemoryProvider.on_session_end)) == (
        "(self, messages: 'list[dict[str, Any]]') -> 'None'"
    )
    assert str(signature(MemoryProvider.on_memory_write)) == (
        "(self, action: 'str', target: 'str', content: 'str', "
        "metadata: 'Optional[dict[str, Any]]' = None) -> 'None'"
    )
    assert str(signature(MemoryProvider.on_delegation)) == (
        "(self, task: 'str', result: 'str', *, child_session_id: 'str' = '', **kwargs) -> 'None'"
    )


def test_memory_provider_setup_schema_signatures() -> None:
    assert str(signature(MemoryProvider.get_config_schema)) == "(self) -> 'list[dict[str, Any]]'"
    assert str(signature(MemoryProvider.save_config)) == (
        "(self, values: 'dict[str, Any]', hermes_home: 'str') -> 'None'"
    )
    assert str(signature(MemoryProvider.backup_paths)) == "(self) -> 'list[str]'"


def test_memory_loader_layout_and_registration_collector() -> None:
    # Bundled providers live under plugins/memory/<name>/; a user provider (the
    # hal0-memory seed) under $HERMES_HOME/plugins/<name>/.
    assert MEMORY_PLUGIN_SUBDIR == "plugins/memory"
    assert USER_PLUGIN_SUBDIR == "plugins"
    # The memory loader's collector captures register_memory_provider. Since
    # 0.22 the general PluginContext also has one, but it is inert: it records
    # the provider and returns None, and activation stays with memory.provider.
    assert hasattr(_ProviderCollector, "register_memory_provider")
    assert str(signature(PluginContext.register_memory_provider)) == "(self, provider) -> 'None'"


def test_hal0_memory_seed_installs_under_the_user_plugin_root() -> None:
    # The provisioning target must match the loader's user root.
    import inspect

    from hal0.agents import hermes_provision as hp

    src = inspect.getsource(hp)
    assert '"hal0-memory": hermes_home / "plugins" / "hal0-memory"' in src


def test_memory_provider_config_selection_key() -> None:
    assert config_defaults.MEMORY_PROVIDER_CONFIG_KEY == "memory.provider"


# ── persona prompt (#2839) ─────────────────────────────────────────────────


def _overlay(**over: object) -> dict:
    from hal0.agents import hermes_provision as hp

    base: dict = dict(
        primary={"model_id": "m", "backend_url": "http://127.0.0.1:8080/v1"},
        chat_slots=[],
        delegation=None,
        auxiliary_tasks={},
        mcp_servers=[],
        agent_id="hermes",
        live_resolve_enabled=True,
    )
    base.update(over)
    return dict(hp._build_config_overlay(**base))


def test_persona_prompt_goes_to_the_key_hermes_reads() -> None:
    # Hermes resolves the session's ephemeral system prompt from
    # agent.system_prompt (personality.py:118-123). hal0 used to write
    # agent.system_prompt_prelude, which no Hermes version reads; this keeps
    # the persona key pinned to the reader so it cannot go dead again.
    from hal0.agents import hermes_provision as hp
    from hal0.agents.personas import _seed_hermes, build_prompt_addendum

    assert hp.PERSONA_PROMPT_CONFIG_KEY == config_defaults.PERSONA_PROMPT_CONFIG_KEY
    prompt = build_prompt_addendum(_seed_hermes("hermes"), mcp_servers=hp._default_mcp_servers())
    layer = hp._persona_prompt_layer(prompt)
    section, key = config_defaults.PERSONA_PROMPT_CONFIG_KEY.split(".")
    assert layer == {section: {key: prompt}}
    # The prompt goes through the YAML layer, never `hermes config set`, which
    # at v0.22.2 refuses a multi-line value whose lines look like a YAML list
    # ("- hal0-admin: ...") and writes nothing.
    keys = _overlay()
    assert not any(k.startswith(f"{section}.{key}") for k in keys)
    assert "agent.system_prompt_prelude" not in keys
    # display.personality would override the prompt when it names a known
    # personality; hal0 leaves it to the operator.
    assert config_defaults.PERSONALITY_NAME_CONFIG_KEY not in keys


def test_retired_persona_key_is_dropped_from_existing_configs() -> None:
    from hal0.agents.hermes_provision import RETIRED_HAL0_CONFIG_KEYS

    assert "agent.system_prompt_prelude" in RETIRED_HAL0_CONFIG_KEYS


# ── upstream defaults hal0 overrides or stops writing (ADR-0034) ───────────


def test_hal0_turns_session_pruning_off() -> None:
    # v0.22.2 pruned ended sessions after 90 idle days unless this was false;
    # v0.22.3 made it opt-in again, but asks once on low disk while the key is
    # absent (config_defaults.py:2279-2288). ADR-0034 decision 7: hal0 writes
    # false either way, so a box that flips back to v0.22.2 keeps its history.
    from hal0.agents.hermes_provision import SESSIONS_AUTO_PRUNE_CONFIG_KEY

    assert config_defaults.SESSIONS_AUTO_PRUNE_DEFAULT is False
    assert config_defaults.SESSIONS_RETENTION_DAYS_DEFAULT == 90
    assert SESSIONS_AUTO_PRUNE_CONFIG_KEY == config_defaults.SESSIONS_AUTO_PRUNE_KEY
    assert _overlay()[config_defaults.SESSIONS_AUTO_PRUNE_KEY] is False


def test_hal0_writes_no_retired_auxiliary_task() -> None:
    # web_extract and session_search lost their aux LLM in v0.22
    # (config_defaults.py:775, :792); a leftover block is ignored.
    from hal0.agents import hermes_provision as hp

    written = set(hp._MAIN_AUX_TASKS) | set(hp._UTILITY_AUX_TASKS)
    assert not written & config_defaults.RETIRED_AUXILIARY_TASKS
    for task in config_defaults.RETIRED_AUXILIARY_TASKS:
        assert f"auxiliary.{task}" in hp.RETIRED_HAL0_CONFIG_KEYS


# ── lane: hal0-voice ───────────────────────────────────────────────────────


def test_voice_registration_callables_match_plugin_context() -> None:
    assert str(signature(PluginContext.register_tts_provider)) == (
        "(self, provider) -> 'Optional[PluginRegistration]'"
    )
    assert str(signature(PluginContext.register_transcription_provider)) == (
        "(self, provider) -> 'Optional[PluginRegistration]'"
    )


def test_voice_command_provider_config_keys() -> None:
    assert config_defaults.TTS_PROVIDER_CONFIG_KEY == "tts.provider"
    assert config_defaults.STT_PROVIDER_CONFIG_KEY == "stt.provider"


# ── shared registration seams (executor bridge / tools / auxiliary) ────────


def test_plugin_context_registration_signatures() -> None:
    assert str(signature(PluginContext.register_context_engine)) == (
        "(self, engine) -> 'Optional[PluginRegistration]'"
    )
    assert str(signature(PluginContext.register_hook)) == (
        "(self, hook_name: 'str', callback: 'Callable') -> 'PluginRegistration'"
    )
    assert str(signature(PluginContext.dispatch_tool)) == (
        "(self, tool_name: 'str', args: 'dict', **kwargs) -> 'str'"
    )
    assert str(signature(PluginContext.inject_message)) == (
        "(self, content: 'str', role: 'str' = 'user', *, session_key: 'str | None' = None, "
        "origin: 'Mapping[str, Any] | None' = None) -> 'bool'"
    )
    assert str(signature(PluginContext.register_auxiliary_task)) == (
        "(self, key: 'str', *, display_name: 'str', description: 'str', "
        "defaults: 'Optional[dict[str, Any]]' = None, "
        "inherit_from: 'Optional[str]' = None) -> 'PluginRegistration'"
    )
    assert str(signature(PluginContext.register_tool)) == (
        "(self, name: 'str', toolset: 'str', schema: 'dict', handler: 'Callable', "
        "check_fn: 'Callable | None' = None, requires_env: 'list | None' = None, "
        "is_async: 'bool' = False, description: 'str' = '', emoji: 'str' = '', "
        "override: 'bool' = False) -> 'Optional[PluginRegistration]'"
    )
    assert [f for f in PluginRegistration.__dataclass_fields__] == [
        "kind",
        "key",
        "release",
        "plugin_key",
    ]


# ── lane: hal0-hermes-executor (run + session HTTP surface) ────────────────


def test_executor_run_routes_are_frozen() -> None:
    # Since 0.22 these come from gateway/platforms/api_server_runs.py:241-249;
    # /steer is new since 0.19.0, /clarify and /resolve-unknown since 0.22.2.
    assert api_surface.RUN_ROUTES == (
        ("post", "/v1/runs"),
        ("get", "/v1/runs/{run_id}"),
        ("get", "/v1/runs/{run_id}/events"),
        ("post", "/v1/runs/{run_id}/approval"),
        ("post", "/v1/runs/{run_id}/clarify"),
        ("post", "/v1/runs/{run_id}/steer"),
        ("post", "/v1/runs/{run_id}/resolve-unknown"),
        ("post", "/v1/runs/{run_id}/stop"),
    )


def test_api_server_route_table_is_complete() -> None:
    # The fixture tables together are the whole _http_route_table() at the pin
    # (48 rows); the 11 rows new since 0.19.0 and the 2 new since 0.22.2 are all
    # accounted for, and no row was removed.
    tables = (
        api_surface.RUN_ROUTES,
        api_surface.SESSION_ROUTES,
        api_surface.CHAT_ROUTES,
        api_surface.JOB_ROUTES,
        api_surface.PLATFORM_CALLBACK_ROUTES,
        api_surface.OTHER_ROUTES,
    )
    rows = [row for table in tables for row in table]
    assert len(rows) == len(set(rows)) == 48
    assert len(api_surface.ADDED_SINCE_0_19_0) == 11
    assert set(rows) >= api_surface.ADDED_SINCE_0_19_0
    assert len(api_surface.ADDED_SINCE_0_22_2) == 2
    assert set(rows) >= api_surface.ADDED_SINCE_0_22_2


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


# ── lane: hal0-hermes-kanban (board executor bridge, KB-5) ────────────────
# R5 §7 "drift-watch blind spot": hal0.board.hermes_executor.HermesBoardExecutor
# dispatches board attempts to the Hermes kanban plugin's run ledger, and
# hal0.agents.hermes_provision provisions the kanban DB schema — neither
# surface was in tracked_files/contract fixtures until this lane.


def test_kanban_db_table_roster_is_frozen() -> None:
    # The 7-table schema hal0's kanban_db_init provisioning phase
    # (hal0.agents.hermes_provision._phase_kanban_db_init) checks for.
    assert {
        "tasks",
        "task_links",
        "task_comments",
        "task_events",
        "task_runs",
        "task_attachments",
        "kanban_notify_subs",
    } == kanban_runs.KANBAN_DB_TABLES


def test_kanban_runs_routes_present() -> None:
    # Routes HermesBoardExecutor.inspect()/.reconcile() read today (both GET
    # {WORKER_BASE_PATH}/{run_id}).
    assert ("get", "/runs/{run_id}") in kanban_runs.KANBAN_RUNS_ROUTES
    assert kanban_runs.KANBAN_PLUGIN_MOUNT_PREFIX == "/api/plugins/kanban"


def test_kanban_runs_creation_and_cancel_routes_are_a_known_gap() -> None:
    # KNOWN GAP (see kanban_runs.py module docstring + the hermes-bump
    # runbook): HermesBoardExecutor.dispatch()/.cancel() call
    # POST {WORKER_BASE_PATH} and POST {WORKER_BASE_PATH}/{run_id}/cancel,
    # but the pinned upstream kanban plugin has neither route — only
    # POST /dispatch (batch auto-dispatch) and POST /runs/{run_id}/terminate.
    # This test is a canary: if a future Hermes bump adds either route, THIS
    # ASSERTION STARTS FAILING — that is a signal to extend
    # KANBAN_RUNS_ROUTES and re-check the executor bridge, not to delete the
    # test. Do not "fix" this by adding the routes to the fixture without
    # re-verifying upstream source.
    assert ("post", "/runs") not in kanban_runs.KANBAN_RUNS_ROUTES
    assert ("post", "/runs/{run_id}/cancel") not in kanban_runs.KANBAN_RUNS_ROUTES
    assert ("post", "/runs/{run_id}/terminate") in kanban_runs.KANBAN_RUNS_ROUTES


@pytest.mark.parametrize("module", ["hal0.board", "hal0.board.hermes_executor"])
def test_board_token_harvest_matches_both_injection_forms(module: str) -> None:
    # hal0.board._TOKEN_RE and hal0.board.hermes_executor._TOKEN_RE harvest the
    # session token from the dashboard's ungated HTML. At 0.22.2 it is injected
    # two ways (web_server_dashboard.py:165 SPA index, :134 headless page); the
    # real regexes must read the token back from both. Still true at v0.22.4
    # (web_server_dashboard.py:139, :172).
    import importlib
    import json
    import secrets

    token_re = importlib.import_module(module)._TOKEN_RE
    token = secrets.token_urlsafe(32)  # how Hermes mints it (web_server.py:235)
    forms = (
        kanban_runs.SESSION_TOKEN_INJECTION_TEMPLATE.format(token=token),
        kanban_runs.SESSION_TOKEN_HEADLESS_TEMPLATE.format(
            token_json=json.dumps(token), msg="headless"
        ),
    )
    for rendered in forms:
        match = token_re.search(rendered)
        assert match is not None, rendered
        assert match.group(1) == token


def test_hal0_never_sets_the_dashboard_public_url() -> None:
    # From 0.22.2 a non-loopback dashboard.public_url (or its env override
    # HERMES_DASHBOARD_PUBLIC_URL) turns the dashboard auth gate on even for a
    # loopback bind (web_server.py:369-379 at v0.22.4;
    # dashboard_auth/prefix.py:119-125). The gated SPA drops
    # __HERMES_SESSION_TOKEN__, so the board's token harvest would fail. hal0
    # must set neither: not in the config overlay, and nowhere in the code or
    # the installer that writes Hermes's config, env files or units.
    keys = _overlay(mcp_servers=[{"name": "hal0-admin", "url": "http://x"}])
    assert not any(k.startswith(config_defaults.DASHBOARD_PUBLIC_URL_CONFIG_KEY) for k in keys)
    assert config_defaults.DASHBOARD_PUBLIC_URL_ENV not in keys

    from hal0.agents import hermes_provision as hp

    assert not any(k == "dashboard" or k.startswith("dashboard.") for k in hp.HAL0_CONFIG_LIST_KEYS)
    offenders = []
    for root in ("src", "installer"):
        for path in (_REPO_ROOT / root).rglob("*"):
            if not path.is_file() or path.suffix in {".pyc", ".png", ".svg", ".woff2"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if (
                config_defaults.DASHBOARD_PUBLIC_URL_ENV in text
                or config_defaults.DASHBOARD_PUBLIC_URL_CONFIG_KEY in text
            ):
                offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == [], offenders


# ── SECURITY CHECKLIST (board fold, lxc105 — item 4) ───────────────────────


def test_upstream_approvals_default_is_frozen_and_hal0_pins_its_own() -> None:
    # Upstream default since 0.19.0 (#62661). If upstream moves it again this
    # trips, and hal0's explicit choice gets re-reviewed against the new
    # upstream semantics.
    from hal0.agents.hermes_provision import HAL0_APPROVALS_MODE

    assert config_defaults.APPROVALS_MODE_DEFAULT == "smart"
    assert HAL0_APPROVALS_MODE == "smart"


def test_hal0_approval_modes_are_a_subset_of_upstream_modes() -> None:
    # Upstream accepts manual | smart | off (approval_mode.py:16); hal0 offers
    # only smart | manual and leaves "off" to overrides.yaml.
    from hal0.config.schema import HERMES_COMMAND_APPROVALS_MODES

    assert config_defaults.APPROVALS_MODES == ("manual", "smart", "off")
    assert set(HERMES_COMMAND_APPROVALS_MODES) <= set(config_defaults.APPROVALS_MODES)
    assert "off" not in HERMES_COMMAND_APPROVALS_MODES


def test_platform_callback_route_is_frozen() -> None:
    # Not API_SERVER_KEY-authenticated (platform-adapter verifier instead), so
    # it is frozen for the security checklist. The server still binds loopback.
    assert api_surface.PLATFORM_CALLBACK_ROUTES == (("post", "/api/platforms/{platform}/events"),)
    assert api_surface.PROFILE_ROUTE_PREFIX == "/p/{profile}"


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
        "SECURITY item-4 deviation: terminal.backend is 'local' (host command "
        "execution) whenever the terminal tool is enabled, which the lxc105 "
        "checklist flags as an unsandboxed-by-default local terminal. The pin "
        "is NOT what forces this: the pinned ref also ships sandboxed backends "
        "(built-in names local, docker, singularity, modal, daytona, "
        "vercel_sandbox, ssh at hermes_cli/plugins.py:1035-1042 at v0.22.4, and "
        "tools/environments/{docker,singularity,modal,daytona,ssh}.py exist at "
        "d923a5eb and 4d043038), so no pin bump is needed to change it. None is viable here: a containerised shell cannot "
        "reach the host filesystem or 127.0.0.1:8080, which would break the "
        "bundled host-management skills (hal0-service-management, hal0-bench, "
        "hal0-tune). The 'by default' half is now answered "
        "(#1863): hal0 ships the terminal tool OFF and subtracts the `terminal`, "
        "`code_execution` and `delegation` toolsets via agent.disabled_toolsets "
        "unless the operator explicitly opts in, in which case 'local' is what "
        "they get. "
        "This xfail asserts on the UPSTREAM default, which is still 'local', so "
        "it stays as the drift watch."
    ),
    strict=True,
)
def test_terminal_backend_default_is_sandboxed() -> None:
    assert config_defaults.TERMINAL_BACKEND_DEFAULT != "local"


# ── pin + drift-watch lockstep ─────────────────────────────────────────────


def test_installer_pins_reviewed_official_commit() -> None:
    requirements = (_REPO_ROOT / "installer/agents/hermes/requirements.txt").read_text()

    assert (
        "hermes-agent[web,mcp] @ git+https://github.com/NousResearch/hermes-agent.git@"
        f"{HERMES_COMMIT}" in requirements
    )


def test_installer_requests_the_mcp_extra() -> None:
    """Upstream gates its MCP client behind the ``mcp`` extra. Shipping
    ``hermes-agent[web]`` alone builds a venv with NO ``mcp`` package — both
    hal0-provisioned MCP servers dead while every self-check read green
    (#2021, ``hermes-venv-missing-mcp-extra``). The active requirement line
    must always request it."""
    from hal0.agents.hermes_provision import _hermes_requirement_line

    requirements = (_REPO_ROOT / "installer/agents/hermes/requirements.txt").read_text()
    line = _hermes_requirement_line(requirements)
    extras = line.split("[", 1)[1].split("]", 1)[0].split(",")
    assert "mcp" in [e.strip() for e in extras], line


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
        "hermes_cli/kanban_db.py",
        "hermes_cli/kanban_db_connect.py",
        "plugins/kanban/dashboard/plugin_api.py",
        # Added at the v0.22.2 bump.
        "gateway/platforms/api_server_runs.py",
        "hermes_cli/web_server_dashboard.py",
        "hermes_cli/config_defaults.py",
        "hermes_cli/personality.py",
        "hermes_cli/gateway.py",
        "hermes_cli/_launchers.py",
        "hermes_cli/main.py",
        "agent/model_metadata.py",
        "setup.py",
        "pyproject.toml",
    }
    missing = required - tracked
    assert not missing, f"drift-watch missing contract surfaces: {sorted(missing)}"
    # The stale earendil-only path must not linger (does not exist at the pin).
    assert "agent/events.py" not in tracked
