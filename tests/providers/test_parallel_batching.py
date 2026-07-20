"""Continuous-batching plumbing: _effective_parallel + launch-gated warnings.

P1 of the concurrency-batching plan. FLAGS-own (spec-flags-ownership §4): the
slot ``parallel`` knob is now INERT at launch — a model that wants batching
carries ``--parallel N`` (+ ``--kv-unified``) in its own ``defaults.extra_args``
(see test_container_assembler.py::test_parallel_from_model_extra_args_*). This
file keeps the ``_effective_parallel`` helper contract and the workers-deprecation
launch-gated warning; the old MTP-x-batching breadcrumb is gone with the slot knob.
"""

from __future__ import annotations

import logging

from hal0.config.schema import ProfileConfig
from hal0.providers.container import _effective_parallel, _resolve_llama_scalars


def _profile(mtp: bool = False) -> ProfileConfig:
    return ProfileConfig(flags="-fa on --parallel 1", mtp=mtp, device_class="gpu", backend="rocm")


# ── _effective_parallel ───────────────────────────────────────────────────────


def test_effective_parallel_values():
    assert _effective_parallel({"parallel": 8}) == 8
    assert _effective_parallel({"parallel": 1}) == 1
    assert _effective_parallel({"parallel": None}) is None  # inherit profile
    assert _effective_parallel({}) is None
    assert _effective_parallel({"parallel": 0}) is None  # <1 → inherit
    assert _effective_parallel({"parallel": "bad"}) is None


# ── scalars no longer threads slot_parallel to launch ─────────────────────────


def test_slot_parallel_is_inert_in_scalars():
    """FLAGS-own: the slot ``parallel`` knob is inert — the resolver never
    threads it to the launch argv (``slot_parallel`` is always None)."""
    scalars = _resolve_llama_scalars({"name": "s", "parallel": 4}, {"_model_key": "m"}, _profile())
    assert scalars["slot_parallel"] is None
    scalars2 = _resolve_llama_scalars({"name": "s"}, {"_model_key": "m"}, _profile())
    assert scalars2["slot_parallel"] is None


# ── launch-gated warnings ─────────────────────────────────────────────────────


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
