"""Forward-only SQL migrations for the agent-bridge store (``bridge.db``).

Plain ``.sql`` files named ``NNN_name.sql``, applied in ascending order by
:func:`hal0.db.migrate.migrate` against ``bridge.db``. This package exists so
the migration files ship inside the installed wheel; never add Python logic
here. See ADR-0043.
"""

from __future__ import annotations
