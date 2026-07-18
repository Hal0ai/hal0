"""Forward-only SQL migrations for the hal0 ``db/`` foundation.

Plain ``.sql`` files named ``NNN_name.sql``, applied in ascending numeric
order by :func:`hal0.db.migrate.migrate`. This package only exists so the
migration files ship inside the installed wheel and are read via
``importlib.resources`` rather than a runtime filesystem path — never add
Python logic here.
"""

from __future__ import annotations
