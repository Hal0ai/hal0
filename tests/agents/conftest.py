"""Shared fixtures for the hermes-provision test suite.

After the D hardened-perms env seam (hal0-agentenv), the provisioner's env
writers (``_write_secrets_env`` / ``_write_driver_env``) branch on
``os.geteuid()``: root writes the root:root files directly, an unprivileged
process delegates to ``sudo -n hal0-agentenv``. CI runs pytest as a NON-root
user, so without this fixture every pre-existing test that reaches those writers
would fall into the seam branch and shell out to sudo.

Default the whole suite to euid == 0 — the unchanged, historical direct-write
path these tests were written against. The dedicated seam tests in
``test_hermes_env_seam.py`` (and the gateway/ownership tests that already set
euid explicitly) override per-test, which takes precedence over this autouse
default for the duration of that test.
"""

from __future__ import annotations

import pytest

from hal0.agents import hermes_provision as hp


@pytest.fixture(autouse=True)
def _euid_root_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run hermes-provision tests as if euid == 0 (the direct-write path).

    Takes the test's OWN ``monkeypatch`` rather than an independent
    ``patch.object`` context: ``hp.os`` IS the global :mod:`os` module, and a
    per-test override set through ``monkeypatch`` would otherwise be undone
    AFTER this fixture had already restored the real ``geteuid`` — re-installing
    the euid-0 stub process-wide for every test that followed in the same xdist
    worker. One monkeypatch instance makes the undo order LIFO and correct.
    """
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
