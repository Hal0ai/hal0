"""Provider-suite fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pin_podman5(monkeypatch: pytest.MonkeyPatch):
    """Pin the quadlet renderer to the podman-5 native-key branch.

    The renderer probes `podman --version` to decide between native 5.0 keys
    (GroupAdd=/SecurityOpt=/AutoRemove=) and the 4.x PodmanArgs= compat
    translation (halo150 O8). CI runners and dev sandboxes have no podman →
    version 0 → compat branch, which flips every native-key assertion in
    this suite. Pin to 5 so the canonical render is what's under test;
    tests that exercise the 4.x branch override with their own monkeypatch
    (see TestQuadletAutoRemoveGate).
    """
    from hal0.providers import container as _c

    monkeypatch.setattr(_c, "_podman_major_version", lambda: 5)
