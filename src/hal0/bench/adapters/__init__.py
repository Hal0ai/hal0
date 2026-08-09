"""hal0.bench.adapters — OSS load-gen/eval tool adapters (bench Phase 3).

Each module here is a self-contained adapter for one external tool: it owns
(1) building that tool's CLI argv for a cell-shaped request, (2) running it
via an injectable ``runner`` callable (tests inject fakes; nothing here shells
out to the real tool), and (3) parsing the tool's output into the same pure
result shape ``hal0.bench.parsers.Parsed`` produces, ready for
``hal0.bench.runner._assemble``. Adapters import ``schema.py``/``parsers.py``
types but do not modify them and do not touch ``runner.py``/``planner.py``/
``cli.py`` — wiring an adapter into the planner's ``KNOWN_KINDS`` is
integration's job, not the adapter's (docs/superpowers/plans/
2026-08-09-bench-phase3-oss-adapters.md).
"""

import os
import shutil
import sys
from pathlib import Path

# Pinned tool versions (re-verified 2026-08-09; plan: docs/superpowers/plans/
# 2026-08-09-bench-phase3-oss-adapters.md). guidellm is a PyPI extra of the
# hal0ai distribution (`pip install 'hal0ai[bench-guidellm]'`); the other two
# have no PyPI release and hal0ai's own PyPI publishing forbids git direct
# references in pyproject extras, so their pins live HERE as the single source
# of truth for the box-side install step (and for doctor checks).
GUIDELLM_PIN = "guidellm==0.7.3"
# Tag v0.4.0. Bus-factor-1 upstream — pin the sha, never a branch.
LLAMA_BENCHY_PIN = (
    "llama-benchy @ git+https://github.com/eugr/llama-benchy"
    "@446dd42fde2ebbaa1d68a0dfe9dc1e5b833f95ad"
)
TOOL_EVAL_BENCH_PIN = (
    "tool-eval-bench @ git+https://github.com/SeraphimSerapis/tool-eval-bench@v2.5.0"
)


def resolve_tool(name: str) -> str | None:
    """Absolute path of an adapter tool binary, or None if absent.

    ``$PATH`` first, then the running interpreter's own bin directory —
    console scripts land there when a tool is pip-installed into hal0's
    venv, and the service PATH never includes the venv bin (found on-box
    2026-08-09: every venv-installed tool was invisible to the plan-time
    gate and to the default subprocess runners)."""
    found = shutil.which(name)
    if found:
        return found
    cand = Path(sys.executable).parent / name
    if cand.is_file() and os.access(cand, os.X_OK):
        return str(cand)
    return None
