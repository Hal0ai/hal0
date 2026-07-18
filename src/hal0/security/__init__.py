"""Security-critical, single-source-of-truth modules (KB-1 / §1).

:mod:`hal0.security.exposure` is seam S9 (hal0-rework-plan.md §23.2/§23.5):
the one route -> :class:`~hal0.security.exposure.AuthClass` classification
table that drives runtime auth enforcement (:mod:`hal0.api.auth`), the
§21.11 exposure-CI ratchet (``tests/security/test_exposure.py``), and
(later) the §22 Settings Security page.
"""

from __future__ import annotations
