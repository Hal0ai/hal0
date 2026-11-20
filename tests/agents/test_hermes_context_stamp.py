"""#3854: config_write stamps the window that really serves ``hal0/agent``.

With a model-less ``agent`` slot, ``hal0/agent`` is served by the ADR-0023
fallback (the ``brain`` slot on ct151, 65,536 tokens). Hermes learns a window
only from a ``/v1/models`` row it can match or from a local-server probe, and
neither sees the fallback, so it planned against its 256,000-token default.
The by-id route ``GET /v1/models/hal0/agent`` resolves the fallback chain, so
config_write stamps its answer into ``model.context_length`` and records in
``hal0.model_context_length`` that the value is hal0's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp

from ._hermes_fakes import fake_hermes_run

yaml = pytest.importorskip("yaml")

_GATEWAY = "http://127.0.0.1:8080/v1"
_FALLBACK_ROW = {"id": "hal0/agent", "owned_by": "hal0", "context_length": 65536}


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", tmp_path / "isolated-seed" / "hermes.toml")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-overrides.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")


def _config_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anchor_row: dict[str, Any] | None,
    *,
    live: bool = True,
) -> tuple[hp.PhaseResult, dict[str, Any], list[tuple[str, str]]]:
    monkeypatch.setattr(hp, "_live_resolve_enabled", lambda: live)
    asked: list[tuple[str, str]] = []

    def anchor(model_id: str, base_url: str = "", gateway_url: str = ""):  # type: ignore[no-untyped-def]
        asked.append((model_id, base_url))
        return (anchor_row, [])

    # The ct151 shape: the agent slot has no model and only brain is bound, so
    # /v1/models carries no `agent` row for Hermes to match.
    slots = [
        {"name": "agent", "type": "llm", "state": "offline", "model": None},
        {"name": "brain", "type": "llm", "state": "ready", "model": "brain-model"},
    ]
    io = hp.InstallIO(
        fetch_slots=lambda: slots,
        fetch_model_contexts=lambda: {"brain": 65536},
        fetch_model_route_ready=lambda _m, _u: None,
        fetch_anchor_models=anchor,
        run=fake_hermes_run(),
    )
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    data = yaml.safe_load(Path(out.details["config_path"]).read_text(encoding="utf-8"))
    return out, data, asked


def test_stamps_the_fallback_served_agent_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, data, asked = _config_write(tmp_path, monkeypatch, _FALLBACK_ROW)
    assert out.status == hp.PhaseStatus.OK
    assert data["model"]["default"] == "hal0/agent"
    assert data["model"]["context_length"] == 65536
    assert data["hal0"]["model_context_length"] == 65536
    assert out.details["model_context_length"] == 65536
    # Asked on the endpoint Hermes' turns reach, by the id Hermes will use.
    assert asked == [("hal0/agent", _GATEWAY)]


def test_keeps_the_stamp_current_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _data, _ = _config_write(tmp_path, monkeypatch, _FALLBACK_ROW)
    again, _data, _ = _config_write(tmp_path, monkeypatch, _FALLBACK_ROW)
    assert again.hash == first.hash
    assert again.details["changed"] is False
    # The operator then binds an agent model with its own window: reprovision
    # follows the resolver, because the old value is the one hal0 wrote.
    bound = {**_FALLBACK_ROW, "context_length": 131072}
    _out, data, _ = _config_write(tmp_path, monkeypatch, bound)
    assert data["model"]["context_length"] == 131072
    assert data["hal0"]["model_context_length"] == 131072


def test_keeps_the_last_stamp_when_the_window_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_write(tmp_path, monkeypatch, _FALLBACK_ROW)
    # Unreachable gateway or no route: "cannot see" is not "remove".
    out, data, _ = _config_write(tmp_path, monkeypatch, None)
    assert out.status == hp.PhaseStatus.OK
    assert data["model"]["context_length"] == 65536
    assert data["hal0"]["model_context_length"] == 65536


@pytest.mark.parametrize(
    "row", [None, {"id": "hal0/agent"}, {"id": "hal0/agent", "context_length": 0}]
)
def test_never_stamps_without_a_resolved_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: dict[str, Any] | None
) -> None:
    out, data, _ = _config_write(tmp_path, monkeypatch, row)
    assert out.status == hp.PhaseStatus.OK
    assert "context_length" not in data["model"]
    assert "model_context_length" not in (data.get("hal0") or {})
    assert out.details["model_context_length"] is None


@pytest.mark.parametrize("recorded", [None, 65536])
def test_leaves_an_operator_window_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: int | None
) -> None:
    """The #3854 workaround (a hand-set value, no record) and a value edited
    after hal0 stamped it are both the operator's."""
    home = tmp_path / "hh"
    home.mkdir()
    seed: dict[str, Any] = {"model": {"default": "hal0/agent", "context_length": 40000}}
    if recorded is not None:
        seed["hal0"] = {"model_context_length": recorded}
    (home / "config.yaml").write_text(yaml.safe_dump(seed), encoding="utf-8")
    out, data, _ = _config_write(tmp_path, monkeypatch, _FALLBACK_ROW)
    assert out.status == hp.PhaseStatus.OK
    assert data["model"]["context_length"] == 40000
    # Ownership is relinquished, so a later run does not reclaim the key.
    assert "model_context_length" not in (data.get("hal0") or {})
    assert out.details["model_context_length"] == 40000


def test_drops_its_stamp_when_the_operator_picked_their_own_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3309 keeps an operator's model wiring; hal0's hal0/agent window would
    then be applied to that model, so it goes."""
    _config_write(tmp_path, monkeypatch, _FALLBACK_ROW)
    path = tmp_path / "hh" / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["model"].update(default="some/cloud-model", provider="openrouter")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    out, data, asked = _config_write(tmp_path, monkeypatch, _FALLBACK_ROW)
    assert out.details["operator_model_kept"] == "some/cloud-model"
    assert asked == []
    assert "context_length" not in data["model"]
    assert "model_context_length" not in (data.get("hal0") or {})


def test_drops_its_stamp_when_hal0_agent_is_not_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hermes scopes ``model.context_length`` to ``model.default``. With
    live-resolve off the default is a physical id that the pinned llama-server
    reports itself, so hal0's ``hal0/agent`` number must not linger there."""
    _config_write(tmp_path, monkeypatch, _FALLBACK_ROW)
    out, data, asked = _config_write(tmp_path, monkeypatch, _FALLBACK_ROW, live=False)
    assert out.status == hp.PhaseStatus.OK
    assert asked == []
    assert "context_length" not in data["model"]
    assert "model_context_length" not in (data.get("hal0") or {})
    assert out.details["model_context_length"] is None
