"""Tests: resolved_argv_detail_for_slot — the provenance-annotated resolution.

Backs the dashboard's "resolved command" drawer and GET /api/slots/{name}/resolved:
the deduped argv plus, per flag, which layer (ADR-0029: base / model / profile
/ slot) set its final value and how many duplicates were collapsed.
"""

from __future__ import annotations

from unittest.mock import patch

from hal0.config.schema import SEED_PROFILES, ProfileConfig
from hal0.providers.container import resolved_argv_detail_for_slot


def _profile() -> ProfileConfig:
    # A within-profile duplicate -b still dedups last-wins.
    return ProfileConfig(flags="-b 512 -b 8192", ctx_size=4096, mtp="off")


def test_detail_dedups_and_attributes_provenance() -> None:
    cfg = {
        "profile": "p",
        "port": 8101,
        "device": "gpu-rocm",
        "n_gpu_layers": 99,
        "model": {"default": "m"},
    }
    model_info = {
        "_model_key": "m",
        "path": "m",  # matches the registry-miss minimal shape (--model value)
    }
    with (
        patch("hal0.providers.container._resolve_profile", return_value=_profile()),
        patch("hal0.providers.container._best_effort_model_info", return_value=model_info),
    ):
        detail = resolved_argv_detail_for_slot(cfg)

    assert detail is not None
    # spec-hw-slot-ownership §3: the image is the HW-gated default (a real
    # registry ref).
    assert "/" in detail["argv"][0]
    # -b deduped to one occurrence, last value wins
    assert detail["argv"].count("-b") == 1
    assert detail["removed"] == 1

    prov = {p["flag"]: p for p in detail["provenance"]}
    assert prov["-b"]["source"] == "profile"
    assert prov["-b"]["value"] == "8192"
    # --jinja is the runner capability hal0 adds in the model layer.
    assert prov["--jinja"]["source"] == "model"
    assert prov["--jinja"]["value"] is None
    # base-layer structural flags are credited to "base"
    assert prov["--model"]["source"] == "base"
    assert prov["--model"]["value"] == "m"
    assert prov["--ctx-size"] == {"flag": "--ctx-size", "value": "4096", "source": "base"}
    # placement is credited to the slot
    assert prov["-ngl"] == {"flag": "-ngl", "value": "99", "source": "slot"}
    assert prov["--metrics"]["source"] == "slot"
    assert {p["source"] for p in detail["provenance"]} == {"base", "model", "profile", "slot"}


def test_detail_for_a_profileless_slot_uses_the_chat_seed() -> None:
    """ADR-0029: a slot naming no profile launches with its type's seed, else
    ``chat`` — the preview shows exactly that, not nothing."""
    detail = resolved_argv_detail_for_slot({"model": {"default": "m"}})
    assert detail is not None
    chat_flags = str(SEED_PROFILES["chat"]["flags"]).split()
    profile_tokens = [p["flag"] for p in detail["provenance"] if p["source"] == "profile"]
    assert profile_tokens == [t for t in chat_flags if t.startswith("-")]


def test_detail_for_a_profileless_embedding_slot_uses_the_embedding_seed() -> None:
    detail = resolved_argv_detail_for_slot({"type": "embedding", "model": {"default": "m"}})
    assert detail is not None
    assert "--embedding" in detail["argv"]
    assert "--jinja" not in detail["argv"]


def test_detail_none_when_the_named_profile_is_missing() -> None:
    """A slot naming a profile that does not exist refuses to launch
    (``slot.profile_missing``); the best-effort preview shows nothing."""
    cfg = {"profile": "no-such-profile-anywhere", "model": {"default": "m"}}
    assert resolved_argv_detail_for_slot(cfg) is None
