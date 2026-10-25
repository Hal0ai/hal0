"""The version of the ``hal0_gpu_gate`` node that this hal0 ships.

The node sends it as ``X-Hal0-Gate-Version`` on every request to hal0-api, so
hal0 can tell an engine still running an older gate (it keeps the gate it
started with until ``hal0 slot restart img``) from one running the current gate.
``GET /api/comfyui/status`` reports the result as ``gate.state``.

The asset ``assets/hal0_gpu_gate.py`` is copied into ComfyUI's ``custom_nodes``
and cannot import hal0, so it repeats this literal as its own ``GATE_VERSION``;
``tests/comfyui/test_hal0_gpu_gate.py`` holds the two together. Bump both when
the node's behaviour changes in a way an engine restart should pick up.
"""

from __future__ import annotations

#: The header the gate stamps on each of its requests.
GATE_VERSION_HEADER = "X-Hal0-Gate-Version"

#: The gate builds its requests with ``urllib.request``, whose default User-Agent is
#: ``Python-urllib/<major>.<minor>`` (a test sends a real gate request and reads it).
#: hal0 reads it to tell an old gate's header-less call from a browser's or curl's.
GATE_USER_AGENT_PREFIX = "Python-urllib/"

#: The current gate. A gate released before the header existed sends none, and
#: reads as stale like any other version that is not this one.
GATE_VERSION = "2"

__all__ = ["GATE_USER_AGENT_PREFIX", "GATE_VERSION", "GATE_VERSION_HEADER"]
