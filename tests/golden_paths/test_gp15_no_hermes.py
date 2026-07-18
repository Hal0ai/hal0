"""Golden path #15 — core operation with Hermes disabled or removed.

REWORK.md §Golden-path verification, scenario 15; guiding principle 7 ("Keep
core hal0 functional without Hermes"). Driven through the public surface with
every Hermes runtime surface disabled (no ``HERMES_*`` env — the board proxy
resolves its base URL + session token from there, so their absence is exactly
"Hermes not provisioned / removed").

Contract pinned (durable across SLOT increment B): with Hermes absent the
control plane still builds and serves its core —

    GET  /api/health        liveness
    GET  /api/slots         slot control plane
    GET  /api/models        model store
    GET  /v1/models         inference catalogue
    POST /api/brain/chat    the brain surface is REGISTERED (hal0-brain is a
                            hal0 subsystem, not a Hermes persona)

plus a "where reasonable" import-hygiene check: the brain chat ENGINE and the
core route modules pull in NO hermes module. This is asserted in a clean
subprocess (the live test process's ``sys.modules`` is polluted by the
persona-seed step of an earlier app's lifespan importing
``hal0.agents.hermes_provision``; the durable, interface-level claim is about
the core modules' OWN import graph, which the subprocess isolates).

Deploy-only remainder: proving a box provisioned WITHOUT
``hal0 agent install hermes`` (no hermes venv, no gateway process on
127.0.0.1:9119) serves the core is deploy-only — halo143 acceptance runbook,
hermes-optional step.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def _hermes_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model a box where Hermes is disabled / never installed."""
    for key in ("HERMES_SESSION_TOKEN", "HERMES_DASHBOARD_BASE_URL", "HERMES_HOME"):
        monkeypatch.delenv(key, raising=False)


def test_core_routes_live_without_hermes(client_factory) -> None:
    with client_factory() as client:
        # create_app() built + lifespan ran to completion with Hermes absent.
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/slots").status_code == 200
        assert client.get("/api/models").status_code == 200
        assert client.get("/v1/models").status_code == 200


def test_brain_chat_surface_is_exposed_without_hermes(client_factory) -> None:
    with client_factory() as client:
        # hal0-brain is a hal0 subsystem, not a Hermes persona: its route is
        # registered independently of any Hermes provisioning.
        paths = {getattr(r, "path", None) for r in client.app.routes}
        assert "/api/brain/chat" in paths


def test_brain_engine_import_graph_pulls_in_no_hermes() -> None:
    """The core chat engine + core route modules import zero hermes modules.

    Run in a clean interpreter so the check reflects the modules' OWN import
    boundary, not sys.modules polluted by an app lifespan run earlier in this
    process. This is the interface-level form of "core operates without
    Hermes": importing the public core does not drag in a hermes dependency.
    """
    snippet = (
        "import sys\n"
        "import hal0.brain.chat\n"
        "import hal0.api.routes.health\n"
        "import hal0.api.routes.slots\n"
        "import hal0.api.routes.models\n"
        "import hal0.api.routes.brain\n"
        "hits = sorted(m for m in sys.modules if 'hermes' in m.lower())\n"
        "print(repr(hits))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # The last stdout line is the repr of the hermes-module list.
    last = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1]
    assert last == "[]", f"core import graph pulled in hermes modules: {last}"
