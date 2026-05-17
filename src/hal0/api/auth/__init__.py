"""FastAPI-side auth helpers introduced by ADR-0001 (Child A).

This sub-package houses the in-process password + session-cookie surface
that complements the existing bearer-token store (``hal0.auth.tokens``).
The split keeps the Caddy-era token machinery untouched while we bolt
on the password/session path that will eventually let Child B remove
Caddy's edge auth entirely.

Public surface today:

  - :mod:`hal0.api.auth.password` — password hashing (bcrypt cost 12)
    and signed session-token mint/verify (HS256 over a key derived from
    ``HAL0_HOME``).
"""

from __future__ import annotations
