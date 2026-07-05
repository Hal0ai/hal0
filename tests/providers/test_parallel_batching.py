"""Continuous-batching plumbing: _effective_parallel + launch-gated warnings.

P1 of the concurrency-batching plan. The argv emission (--parallel / --kv-unified
precedence) is pinned in test_container_assembler.py; this covers the resolver
helper and the two launch-gated side-effect logs (MTP x batching, workers
deprecation).
"""

from __future__ import annotations

import logging

from hal0.config.schema import ProfileConfig
from hal0.providers.container import _effective_parallel, _resolve_llama_scalars


def _profile(mtp: bool = False) -> ProfileConfig:
    return ProfileConfig(
        image="img", flags="-fa on --parallel 1", mtp=mtp, device_class="gpu", backend="rocm"
    )


# ── _effective_parallel ───────────────────────────────────────────────────────


def test_effective_parallel_values():
    assert _effective_parallel({"parallel": 8}) == 8
    assert _effective_parallel({"parallel": 1}) == 1
    assert _effective_parallel({"parallel": None}) is None  # inherit profile
    assert _effective_parallel({}) is None
    assert _effective_parallel({"parallel": 0}) is None  # <1 → inherit
    assert _effective_parallel({"parallel": "bad"}) is None


# ── scalars threads slot_parallel ─────────────────────────────────────────────


def test_scalars_carries_slot_parallel():
    scalars = _resolve_llama_scalars({"name": "s", "parallel": 4}, {"_model_key": "m"}, _profile())
    assert scalars["slot_parallel"] == 4
    scalars2 = _resolve_llama_scalars({"name": "s"}, {"_model_key": "m"}, _profile())
    assert scalars2["slot_parallel"] is None


# ── launch-gated warnings ─────────────────────────────────────────────────────


def _mtp_model():
    return {"_model_key": "chad-mtp", "tags": ["chat", "mtp"]}


def test_mtp_batched_warn_launch_only(caplog):
    slot = {"name": "code", "mtp": True, "parallel": 8}  # MTP on + batched
    with caplog.at_level(logging.INFO, logger="hal0.providers.container"):
        # preview path: silent
        _resolve_llama_scalars(slot, _mtp_model(), _profile(mtp=True), for_launch=False)
        assert not [r for r in caplog.records if "batched_speculation" in r.getMessage()]
        # launch path: one warning
        _resolve_llama_scalars(slot, _mtp_model(), _profile(mtp=True), for_launch=True)
        assert len([r for r in caplog.records if "batched_speculation" in r.getMessage()]) == 1


def test_no_mtp_batched_warn_when_parallel_1(caplog):
    slot = {"name": "code", "mtp": True, "parallel": 1}
    with caplog.at_level(logging.INFO, logger="hal0.providers.container"):
        _resolve_llama_scalars(slot, _mtp_model(), _profile(mtp=True), for_launch=True)
    assert not [r for r in caplog.records if "batched_speculation" in r.getMessage()]


def test_workers_deprecation_warn_launch_only(caplog):
    slot = {"name": "s", "workers": 4}
    with caplog.at_level(logging.WARNING, logger="hal0.providers.container"):
        _resolve_llama_scalars(slot, {"_model_key": "m"}, _profile(), for_launch=False)
        assert not [r for r in caplog.records if "workers_deprecated" in r.getMessage()]
        _resolve_llama_scalars(slot, {"_model_key": "m"}, _profile(), for_launch=True)
        assert len([r for r in caplog.records if "workers_deprecated" in r.getMessage()]) == 1


def test_workers_default_is_silent(caplog):
    with caplog.at_level(logging.WARNING, logger="hal0.providers.container"):
        _resolve_llama_scalars(
            {"name": "s", "workers": 1}, {"_model_key": "m"}, _profile(), for_launch=True
        )
    assert not [r for r in caplog.records if "workers_deprecated" in r.getMessage()]
