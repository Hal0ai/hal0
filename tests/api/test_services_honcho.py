"""Registry + packaging checks for the Honcho service entry.

Companion to ``tests/api/test_services_page.py`` (which already covers the
generic list/probe/action machinery via ``_EXPECTED_IDS``). This file pins
the Honcho-specific ``ServiceDef`` fields and cross-checks the packaging
artifacts (systemd units, compose file) referenced by the registry actually
exist and are well-formed.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from hal0.services import systemd as svc_systemd
from hal0.services.registry import LIFECYCLE_ACTIONS, service_by_id

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_registry_honcho_fields() -> None:
    sdef = service_by_id("honcho")
    assert sdef is not None
    assert sdef.unit == "hal0-honcho.service"
    assert sdef.probe == "http"
    assert sdef.probe_url == "http://127.0.0.1:8000/health"
    assert sdef.probe_url_env == "HAL0_HONCHO_PROBE_URL"
    assert sdef.public_url_env == "HAL0_HONCHO_PUBLIC_URL"
    assert sdef.loopback_port == 8000
    assert sdef.port is None  # loopback-only, no LAN host:port fallback
    assert set(sdef.actions) == set(LIFECYCLE_ACTIONS)
    assert svc_systemd.valid_unit(sdef.unit)


def test_honcho_unit_file_shipped() -> None:
    unit = _REPO_ROOT / "installer" / "systemd" / "hal0-honcho.service"
    assert unit.is_file()
    text = unit.read_text(encoding="utf-8")
    assert "ExecStart=" in text
    assert "podman compose" in text
    assert "hal0-honcho" in text
    assert "[Install]" in text and "WantedBy=multi-user.target" in text


def test_honcho_sync_unit_and_timer_shipped() -> None:
    service = _REPO_ROOT / "installer" / "systemd" / "hal0-honcho-sync.service"
    timer = _REPO_ROOT / "installer" / "systemd" / "hal0-honcho-sync.timer"
    assert service.is_file()
    assert timer.is_file()
    assert "Type=oneshot" in service.read_text(encoding="utf-8")
    timer_text = timer.read_text(encoding="utf-8")
    assert "OnCalendar=hourly" in timer_text
    assert "WantedBy=timers.target" in timer_text


def test_honcho_compose_file_valid_yaml_and_shape() -> None:
    compose_path = _REPO_ROOT / "installer" / "honcho" / "docker-compose.yml"
    assert compose_path.is_file()
    doc = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    assert doc["name"] == "hal0-honcho"
    services = doc["services"]
    assert set(services) == {"api", "deriver", "database", "redis"}

    # Only api publishes a host port, and only on loopback.
    assert services["api"]["ports"] == ["127.0.0.1:8000:8000"]
    assert "ports" not in services["database"]
    assert "ports" not in services["redis"]

    # api + deriver can reach hal0-api on the host.
    for name in ("api", "deriver"):
        assert "host.docker.internal:host-gateway" in services[name]["extra_hosts"]

    # No named volumes left — everything is a bind mount under the hal0 data root.
    assert "volumes" not in doc
    for vol in services["database"]["volumes"] + services["redis"]["volumes"]:
        assert vol.startswith("/var/lib/hal0/honcho/")
