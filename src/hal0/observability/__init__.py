"""Observability side-car wiring for hal0.

Everything in this package is OPTIONAL and fails soft: hal0 must start,
serve, and shut down identically whether or not the observability extras
are installed or configured. Nothing here may become an import-time
dependency of a hot path.

Currently one member:

    sentry.py  — optional Sentry error reporting (``pip install 'hal0ai[sentry]'``)
"""

from __future__ import annotations

__all__ = ["sentry"]
