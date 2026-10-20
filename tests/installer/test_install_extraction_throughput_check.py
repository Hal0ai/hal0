"""#2353: the installer's memory-enable path checks the extraction slot's speed.

The Hindsight block stands the memory engine up and seeds its banks; from then
on every retain goes through the extraction slot. A slot too slow to finish one
extraction inside ``[memory.graph].llm_timeout_s`` must be reported there, as a
warning: the check can never fail the install (a fresh box usually has no
model loaded yet).
"""

from __future__ import annotations

import re
from pathlib import Path

from typer.main import get_command

from hal0.cli import memory_commands

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
CHECK = '"${HAL0_BIN}" memory graph check'


def _hindsight_block() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("# ── Memory engine (Hindsight)")
    end = text.index("# Escape hatch: HAL0_SKIP_OPENWEBUI", start)
    return text[start:end]


def test_the_check_runs_once_the_engine_is_healthy() -> None:
    block = _hindsight_block()
    healthy = block.index('if [[ "${hs_up}" -eq 1 ]]; then')
    unhealthy = block.index('warn "hindsight-api not healthy yet', healthy)
    assert healthy < block.index(CHECK) < unhealthy


def test_the_check_can_never_fail_the_install() -> None:
    block = _hindsight_block()
    after = block[block.index(CHECK) :]
    # The command's own line continues straight into an `|| warn` fallback.
    assert re.match(r'"\$\{HAL0_BIN\}" memory graph check \\\n\s+\|\| warn ', after)


def test_the_installer_calls_a_command_the_cli_has() -> None:
    graph = get_command(memory_commands.app).commands["graph"]  # type: ignore[attr-defined]
    assert "check" in graph.commands
