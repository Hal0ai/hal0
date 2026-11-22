# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of Hermes config default keys the hal0 suite provisions.

Adapter lanes: **hal0-memory** (``memory.provider`` selection),
**hal0-voice** (``tts.provider`` / ``stt.provider`` command-provider keys),
the **persona** prelude (#2839), and the cross-cutting **provisioning /
security** posture.

Copied from the reviewed official pin
``caafe5389ae585ef77387a962aee05b1bcf17029`` (Hermes v0.23.3). Since 0.22
``DEFAULT_CONFIG`` lives in ``hermes_cli/config_defaults.py``; the
``agent.*`` runtime defaults the CLI reads live in
``hermes_cli/cli_config_load.py``, and the persona overlay is resolved in
``hermes_cli/personality.py``.

SECURITY (board fold, lxc105): ``TERMINAL_BACKEND_DEFAULT`` is frozen as an
*observed* upstream default, NOT an endorsement. The pinned ref defaults
``terminal.backend`` to ``"local"`` (host command execution,
``config_defaults.py:286``). hal0 no longer inherits that posture: since #1863
the terminal tool is an explicit operator opt-in and provisioning disables the
``terminal`` + ``code_execution`` toolsets by default. The matching xfail in
``test_contract_compatibility.py`` remains as the drift watch on the UPSTREAM
default.
"""

from __future__ import annotations

# DEFAULT_CONFIG["memory"]["provider"] (config_defaults.py:1275) selection key.
# Exactly one external memory provider is active; hal0 selects "hal0-memory".
MEMORY_PROVIDER_CONFIG_KEY = "memory.provider"

# DEFAULT_CONFIG["terminal"]["backend"] — observed default.
# "local" == host execution. FLAGGED (see module docstring / handback).
TERMINAL_BACKEND_DEFAULT = "local"

# Voice command-provider selection keys (hal0-voice initial path):
# DEFAULT_CONFIG["tts"]["provider"] (config_defaults.py:1063) and the
# unseeded stt.provider (config_defaults.py:1140-1146).
TTS_PROVIDER_CONFIG_KEY = "tts.provider"
STT_PROVIDER_CONFIG_KEY = "stt.provider"

# API server env-descriptor invariant (config_defaults.py:3036-3038):
# API_SERVER_HOST documents a loopback default and states the key is required
# even on loopback binds. Frozen as the literal default string.
API_SERVER_HOST_DEFAULT = "127.0.0.1"

# DEFAULT_CONFIG["approvals"]["mode"] (config_defaults.py:1623) — observed
# default. Upstream flipped this from "manual" to "smart" in 0.19.0 (#62661):
# an auxiliary LLM approves or denies dangerous terminal commands before any
# human prompt. hal0 writes approvals.mode explicitly in its provisioning
# overlay (hal0.agents.hermes_provision.HAL0_APPROVALS_MODE, "smart" by
# operator choice), so a change here is reviewed rather than silently
# inherited.
APPROVALS_MODE_DEFAULT = "smart"
# hermes_cli/approval_mode.py:16 VALID_APPROVAL_MODES. hal0 offers the first
# two (hal0.config.schema.HERMES_COMMAND_APPROVALS_MODES); "off" is reachable
# only through overrides.yaml.
APPROVALS_MODES = ("manual", "smart", "off")

# DEFAULT_CONFIG["sessions"] (config_defaults.py:2218-2228). v0.22.2 deleted
# ENDED sessions idle for ``retention_days`` at startup unless
# ``sessions.auto_prune`` was false. v0.22.3 made pruning opt-in again (default
# false); while config.yaml has no auto_prune key, every surface asks once when
# the disk holding state.db runs low (hermes_cli/prune_advisor.py). hal0 still
# writes it false (ADR-0034 decision 7), which also keeps that question away.
SESSIONS_AUTO_PRUNE_KEY = "sessions.auto_prune"
SESSIONS_AUTO_PRUNE_DEFAULT = False
SESSIONS_RETENTION_DAYS_DEFAULT = 90

# Persona prelude (#2839). hermes_cli/personality.py:118-124
# resolve_ephemeral_system_prompt: a ``display.personality`` that names a
# known personality wins; otherwise the user-owned ``agent.system_prompt``
# overlay is the session's ephemeral system prompt. Read by the CLI
# (cli_init_mixin.py:347-349), the TUI gateway behind the dashboard chat
# (tui_gateway/agent_factory.py:631-632) and the messaging gateway, which
# since v0.22.3 also runs CLI and TUI chat turns
# (gateway/run_config_loaders.py:172-181). ``agent.system_prompt_prelude`` has
# no reader at 3ef6bbd2, d923a5eb, 4d043038 or caafe538.
PERSONA_PROMPT_CONFIG_KEY = "agent.system_prompt"
PERSONALITY_NAME_CONFIG_KEY = "display.personality"

# DEFAULT_CONFIG["dashboard"]["public_url"] (config_defaults.py:1044-1050) and
# its env override HERMES_DASHBOARD_PUBLIC_URL (dashboard_auth/prefix.py:117-130).
# A non-loopback value turns the dashboard auth gate ON even for a loopback
# bind (web_server.py:370, should_require_dashboard_auth); the gated SPA no
# longer carries __HERMES_SESSION_TOKEN__ (web_server_dashboard.py:211-221).
# hal0 must set neither. v0.23.0 added ``webapp.public_url``
# (config_defaults.py:983) for the gateway's own Webapp listener, which is off
# by default and which hal0 does not turn on.
DASHBOARD_PUBLIC_URL_CONFIG_KEY = "dashboard.public_url"
DASHBOARD_PUBLIC_URL_ENV = "HERMES_DASHBOARD_PUBLIC_URL"

# Auxiliary tasks that no longer use an aux LLM: ``web_extract`` and
# ``session_search`` since 0.22, ``skills_hub`` since v0.23.3
# (config_defaults.py:747, "no aux LLM any more, leftover blocks are ignored").
# hal0 stops writing them.
RETIRED_AUXILIARY_TASKS = frozenset({"web_extract", "session_search", "skills_hub"})
