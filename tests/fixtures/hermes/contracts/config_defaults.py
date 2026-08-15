# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of Hermes ``config.py`` default keys the hal0 suite provisions.

Adapter lanes: **hal0-memory** (``memory.provider`` selection),
**hal0-voice** (``tts.provider`` / ``stt.provider`` command-provider keys),
and the cross-cutting **provisioning / security** posture.

Copied from the reviewed official pin
``9de9c25f620ff7f1ce0fd5457d596052d5159596`` (``hermes_cli/config.py``,
Hermes v2026.7.7.2 / 0.18.2).

SECURITY (board fold, lxc105): ``TERMINAL_BACKEND_DEFAULT`` is frozen as an
*observed* upstream default, NOT an endorsement. The pinned ref defaults
``terminal.backend`` to ``"local"`` (host command execution). hal0 no longer
inherits that posture: since #1863 the terminal tool is an explicit operator
opt-in and provisioning disables the ``terminal`` + ``code_execution`` toolsets
by default. The matching xfail in ``test_contract_compatibility.py`` remains as
the drift watch on the UPSTREAM default.
"""

from __future__ import annotations

# hermes_cli/config.py _DEFAULTS["memory"]["provider"] selection key. Exactly
# one external memory provider is active; hal0 selects "hal0-memory".
MEMORY_PROVIDER_CONFIG_KEY = "memory.provider"

# hermes_cli/config.py _DEFAULTS["terminal"]["backend"] — observed default.
# "local" == host execution. FLAGGED (see module docstring / handback).
TERMINAL_BACKEND_DEFAULT = "local"

# Voice command-provider selection keys (hal0-voice initial path).
TTS_PROVIDER_CONFIG_KEY = "tts.provider"
STT_PROVIDER_CONFIG_KEY = "stt.provider"

# API server env-descriptor invariant (hermes_cli/config.py env schema):
# API_SERVER_HOST documents a loopback default and states the key is required
# even on loopback binds. Frozen as the literal default string.
API_SERVER_HOST_DEFAULT = "127.0.0.1"
