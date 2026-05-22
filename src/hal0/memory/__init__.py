"""hal0 memory subsystem (ADR-0005).

Public contract for `/mcp/memory` — wraps `cognee` as the embedded
memory engine. Re-exports :class:`CogneeWrapper` so MCP-backend code
can ``from hal0.memory import CogneeWrapper``.

Only the wrapper is public. Anything in
:mod:`hal0.memory.cognee_wrapper` not re-exported here is internal.
"""

from __future__ import annotations

from hal0.memory.cognee_wrapper import CogneeWrapper, MemoryRecord

__all__ = ["CogneeWrapper", "MemoryRecord"]
