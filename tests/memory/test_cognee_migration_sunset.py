"""No Cognee migration path may exist — it has had no reachable caller since v1.0.0-rc.1.

``hal0/memory/migrate.py`` shipped a single function,
``migrate_cognee_to_hindsight_dryrun``, which read a Cognee sidecar SQLite and
reported how many rows would map to Hindsight banks. It was never wired to a
CLI command: ``hal0 memory migrate`` is (and was) a Hindsight-side command in
``hal0/cli/memory_migrate_commands.py`` with no ``cognee`` subcommand or
``--from cognee`` engine. Its only importers were its own unit tests, which
made it look maintained while nothing could reach it.

Two independent facts had already retired the feature underneath it:

* ADR-0023 removed the Cognee wrapper — Hindsight is the platform engine.
* The ``cognee`` value of ``[memory].engine`` was retired in v1.0.0-rc.1
  (``HAL0-SUNSET: v1.0.0`` in ``config/schema.py``); a hal0.toml still
  carrying it now fails validation rather than resolving.

So there is no supported configuration from which a Cognee store can be the
live engine, which means there is nothing for a Cognee migration to migrate
*from*. Dead code that reads a schema no supported install can produce is
worse than absent code: the next reader has to re-derive all of the above to
find out it is unreachable.

This test is the ratchet. It fails while the module exists, and keeps anyone
from reintroducing the path without also un-retiring the engine literal — at
which point the assertion at the bottom fires and forces the decision to be
made deliberately.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest


def test_memory_migrate_module_is_gone() -> None:
    """hal0.memory.migrate must not exist — no importer, no engine to serve."""
    assert importlib.util.find_spec("hal0.memory.migrate") is None, (
        "hal0/memory/migrate.py is back. It has no reachable caller: "
        "`hal0 memory migrate` never dispatched to it, and the 'cognee' engine "
        "literal was retired in v1.0.0-rc.1."
    )


def test_no_cognee_migration_symbol_is_exported() -> None:
    """Nor may the function reappear on the memory package's surface."""
    import hal0.memory as memory_pkg

    assert not hasattr(memory_pkg, "migrate_cognee_to_hindsight_dryrun")
    assert not any("cognee" in name.lower() for name in memory_pkg.__all__), (
        f"cognee symbol re-exported from hal0.memory: {memory_pkg.__all__}"
    )


def test_cognee_engine_literal_stays_retired() -> None:
    """The premise of the deletion: no supported config selects Cognee.

    If this ever starts failing, the engine has been un-retired and the
    deletion above needs revisiting — that is the whole point of asserting it
    here rather than trusting the comment.
    """
    from pydantic import ValidationError

    from hal0.config.schema import MemoryConfig

    with pytest.raises(ValidationError):
        MemoryConfig(engine="cognee")


def test_memory_migrate_cli_has_no_cognee_command() -> None:
    """`hal0 memory migrate` must expose no cognee entry point."""
    from hal0.cli import memory_migrate_commands as mod

    source_names = {name.lower() for name in dir(mod)}
    assert not any("cognee" in name for name in source_names), (
        f"a cognee symbol appeared on the migrate CLI: {sorted(source_names)}"
    )
