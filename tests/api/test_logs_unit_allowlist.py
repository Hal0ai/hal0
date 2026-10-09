"""``/api/logs`` only reads hal0-owned units (#2435).

hal0-api now runs with ``SupplementaryGroups=systemd-journal`` so the route
can read the journal at all. That group reads *every* unit's journal, so the
route narrows what it will hand to ``journalctl -u``: the API, and the agent
through the MCP ``logs_tail`` tool (``GET /api/logs``), must not become a
reader for sshd, sudo, or any other unit on the host.

The accepted set is every unit a real caller asks for: the dashboard's
Services drawer (one ``unit`` per :data:`hal0.services.registry.SERVICES`
entry), ``hal0 doctor logs`` (default ``hal0-api``), and the units the
installer ships under ``installer/systemd/`` and writes from ``install.sh``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api.routes.logs import LogsError, _validate_unit

_REPO = Path(__file__).resolve().parents[2]

# Units the installer writes (install.sh heredocs + copies) and the
# per-instance names hal0 creates from its templates at runtime.
_INSTALLER_UNITS = [
    "hal0-api",
    "hal0-api.service",
    "hal0.target",
    "hal0-gpu-perms.service",
    "hal0-bench.service",
    "hal0-bench.timer",
    "hal0-bench-worker.service",
    "hal0-openwebui.service",
    "hal0-podman-forward.service",
    "hal0-agent@hermes.service",
    "hal0-agent@hermes",
    "hal0-slot@primary",
    "hal0-slot@primary.service",
    "hal0-slot@img.service",
    "hindsight-api",
    "hindsight-api.service",
    "hermes-gateway",
    "hermes-gateway.service",
]


@pytest.mark.parametrize("unit", _INSTALLER_UNITS)
def test_allowlist_accepts_every_hal0_unit(unit: str) -> None:
    assert _validate_unit(unit) == unit


def test_allowlist_accepts_every_shipped_unit_file() -> None:
    """Every unit file in installer/systemd/ (template or not) is readable."""
    shipped = [
        p.name
        for p in (_REPO / "installer" / "systemd").iterdir()
        if p.suffix in {".service", ".timer", ".target"}
    ]
    assert shipped, "installer/systemd/ has no unit files?"
    for name in shipped:
        # Templates are read through an instance, e.g. hal0-agent@hermes.
        unit = name.replace("@.", "@x.")
        assert _validate_unit(unit) == unit


def test_allowlist_accepts_every_services_drawer_unit() -> None:
    """The dashboard Services drawer asks for ``sdef.unit`` verbatim."""
    from hal0.services.registry import SERVICES

    units = [s.unit for s in SERVICES if s.unit]
    assert units
    for unit in units:
        assert _validate_unit(unit) == unit


def test_allowlist_accepts_doctor_logs_default() -> None:
    import inspect

    from hal0.cli import doctor_commands

    src = inspect.getsource(doctor_commands)
    assert '"hal0-api"' in src
    assert _validate_unit("hal0-api") == "hal0-api"


@pytest.mark.parametrize(
    "unit",
    [
        "sshd",
        "ssh.service",
        "sshd.service",
        "sudo",
        "systemd-journald",
        "systemd-logind.service",
        "cron",
        "docker.service",
        "postgresql",
        "hal0api",  # no separator: not a hal0 unit
        "hal0x-api",
        "myhal0-api",
        "hindsight-api-evil",
        "hindsight",
        "hermes-gateway-other.service",
        "hal0.evil",
    ],
)
def test_allowlist_rejects_non_hal0_units(unit: str) -> None:
    with pytest.raises(LogsError) as exc:
        _validate_unit(unit)
    assert exc.value.status == 400
    assert "hal0" in str(exc.value)


def test_list_route_rejects_foreign_unit(client: TestClient) -> None:
    r = client.get("/api/logs", params={"unit": "sshd"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "system.logs_error"
    assert body["error"]["details"]["unit"] == "sshd"


def test_stream_route_rejects_foreign_unit(client: TestClient) -> None:
    r = client.get("/api/logs/stream", params={"unit": "ssh.service"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "system.logs_error"
