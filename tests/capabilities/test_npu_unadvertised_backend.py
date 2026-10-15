"""#2659: an NPU the host cannot run must be neither offered nor accepted.

``hardware.json`` can report ``npu.present`` while ``available_backends()``
omits ``npu`` (no FLM runner image). The catalog used to list the host
``flm list -j`` rows anyway and the apply committed ``device=npu``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities import catalog
from hal0.capabilities.catalog import models_for_capability
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.errors import BadRequest

_NPU = {"id": "npu", "label": "NPU", "short": "NPU", "provider": "flm", "multiplex": True}
_CPU = {"id": "cpu", "label": "CPU", "short": "CPU", "provider": "llama-server", "multiplex": False}

_FLM_ENTRIES: list[dict[str, Any]] = [
    {
        "tag": "embed-gemma:300m",
        "size_bytes": 300_000_000,
        "footprint_gb": 0.4,
        "capabilities": ["embed"],
        "installed": True,
    },
    {
        "tag": "whisper-v3:turbo",
        "size_bytes": 800_000_000,
        "footprint_gb": 1.0,
        "capabilities": ["stt"],
        "installed": True,
    },
]


@pytest.fixture(autouse=True)
def _flm_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: _FLM_ENTRIES)


def _advertise(monkeypatch: pytest.MonkeyPatch, *backends: dict[str, Any]) -> None:
    monkeypatch.setattr(catalog, "available_backends", lambda: list(backends))


@pytest.mark.parametrize("cap", ["embed", "stt"])
def test_catalog_hides_npu_rows_when_npu_not_advertised(
    monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    _advertise(monkeypatch, _CPU)
    assert catalog._flm_rows_for_capability(cap) == []
    for row in models_for_capability(cap, registry=None):
        assert "npu" not in [b["id"] for b in row.get("backends", [])]


def test_catalog_keeps_npu_rows_when_npu_advertised(monkeypatch: pytest.MonkeyPatch) -> None:
    _advertise(monkeypatch, _NPU, _CPU)
    ids = {r["id"] for r in catalog._flm_rows_for_capability("embed")}
    assert ids == {"embed-gemma:300m"}


async def test_apply_npu_rejected_when_not_advertised(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    _advertise(monkeypatch, _CPU)
    home = Path(tmp_hal0_home)
    caps = home / "capabilities.toml"
    before = caps.read_bytes() if caps.exists() else None
    orch = CapabilityOrchestrator(slot_manager=object())
    with pytest.raises(BadRequest) as exc:
        await orch.apply(
            "voice", "stt", {"model": "whisper-v3:turbo", "backend": "npu", "enabled": True}
        )
    assert exc.value.code == "capability.backend_unavailable"
    assert exc.value.details["available_backends"] == ["cpu"]
    after = caps.read_bytes() if caps.exists() else None
    assert after == before, "rejected apply must not write capabilities.toml"
    assert not (home / "slots" / "stt.toml").exists()


async def test_disable_npu_still_allowed_when_not_advertised(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """The gate arms only for an enabled selection; disabling must succeed."""
    _advertise(monkeypatch, _CPU)
    orch = CapabilityOrchestrator(slot_manager=object())
    try:
        await orch.apply("voice", "stt", {"backend": "npu", "enabled": False})
    except BadRequest as exc:  # pragma: no cover - the assertion is the point
        assert exc.code != "capability.backend_unavailable"
    except Exception:
        pass  # downstream slot-manager stub is not under test
