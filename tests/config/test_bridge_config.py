"""hal0.toml ``[bridge]`` schema and apply plan (ADR-0043)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.api._settings_apply import REGISTRY
from hal0.config.schema import BridgeConfig, Hal0Config


def test_bridge_defaults() -> None:
    cfg = Hal0Config()
    assert cfg.bridge.enabled is True
    assert (
        cfg.bridge.max_concurrent_runs_per_client,
        cfg.bridge.run_retention_days,
        cfg.bridge.clarify_timeout_s,
    ) == (2, 7, 900)


def test_bridge_forbids_unknown_and_bounds() -> None:
    with pytest.raises(ValidationError):
        BridgeConfig(enabledd=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        BridgeConfig(clarify_timeout_s=10)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_concurrent_runs_per_client": 0},
        {"max_concurrent_runs_per_client": 17},
        {"run_retention_days": 0},
        {"run_retention_days": 366},
        {"clarify_timeout_s": 59},
        {"clarify_timeout_s": 3601},
    ],
)
def test_bridge_bounds_reject_out_of_range(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        BridgeConfig(**kwargs)


def test_bridge_bounds_accept_edges() -> None:
    cfg = BridgeConfig(
        max_concurrent_runs_per_client=16, run_retention_days=365, clarify_timeout_s=60
    )
    assert (cfg.max_concurrent_runs_per_client, cfg.run_retention_days) == (16, 365)
    assert BridgeConfig(clarify_timeout_s=3600).clarify_timeout_s == 3600


def test_bridge_apply_classes() -> None:
    assert REGISTRY["bridge.enabled"]["apply_class"] == "service-restart"
    assert REGISTRY["bridge.enabled"]["services"] == ["hal0-api"]
    for key in (
        "bridge.max_concurrent_runs_per_client",
        "bridge.run_retention_days",
        "bridge.clarify_timeout_s",
    ):
        assert REGISTRY[key] == {"apply_class": "immediate", "services": []}
