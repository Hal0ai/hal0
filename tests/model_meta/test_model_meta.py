"""Table-driven tests for :mod:`hal0.model_meta` (issue #695).

One parametrized table per function. These tables capture the EXACT
behaviour of the five prior copies before consolidation:

  * ``classify``                  ← ``routes/models.py:_classify_type``
  * ``device_to_backend``         ← the legacy provider's device mapping
  * ``is_resolvable``             ← ``routes/slots.py:_model_resolvable``
  * ``canonical_device``          ← ``orchestrator._canonical_device_id`` /
                                    ``_canonical_backend_id`` /
                                    ``_slot_device_for_catalog_id``
  * ``device_to_legacy_backend``  ← ``orchestrator._slot_backend_for_catalog_id``
  * ``labels_of``                 ← ``omni_router/filter.py:_labels_of_model`` /
                                    ``SlotManager.route_for_request._labels_of``

Behaviour-preserving: no expectation here may change as part of #695.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.model_meta import (
    canonical_device,
    classify,
    device_to_backend,
    device_to_legacy_backend,
    is_resolvable,
    labels_of,
)

# ── classify ─────────────────────────────────────────────────────────────────
#
# (model_id, capabilities, expected)


@pytest.mark.parametrize(
    ("model_id", "capabilities", "expected"),
    [
        # Capability-driven classification (capabilities win over id).
        ("anything", ["chat"], "chat"),
        ("", ["vision"], "chat"),
        ("", ["embed"], "embed"),
        ("", ["rerank"], "rerank"),
        ("", ["asr"], "stt"),
        ("", ["stt"], "stt"),
        ("", ["tts"], "tts"),
        ("", ["image"], "img"),
        ("", ["img"], "img"),
        # Priority when several capabilities are advertised: rerank >
        # embed > stt > tts > img > chat.
        ("", ["chat", "embed"], "embed"),
        ("", ["embed", "rerank"], "rerank"),
        ("", ["chat", "tts", "stt"], "stt"),
        ("", ["chat", "img", "tts"], "tts"),
        ("", ["chat", "image"], "img"),
        # Capabilities are normalised: strip + lower.
        ("", ["  CHAT  "], "chat"),
        ("", ["Embed"], "embed"),
        # Capabilities beat id heuristics when they match something.
        ("whisper-large-v3", ["embed"], "embed"),
        # Unknown capability strings fall through to id heuristics.
        ("bge-reranker-v2-m3", ["bogus"], "rerank"),
        # Tuple is accepted like a list.
        ("", ("rerank",), "rerank"),
        # Non-list capabilities are ignored → id heuristics.
        ("nomic-embed-text-v1.5", "chat", "embed"),
        ("kokoro-82m", None, "tts"),
        # Id heuristics (no capabilities): rerank.
        ("bge-reranker-v2-m3-q4_k_m", None, "rerank"),
        # rerank wins over embed in the elif chain.
        ("bge-reranker-embed", None, "rerank"),
        # embed: "embed" / "bge" / "nomic".
        ("nomic-embed-text", None, "embed"),
        ("bge-m3", None, "embed"),
        ("nomic-text-v2", None, "embed"),
        # stt: "whisper" / "moonshine" / "-stt" / "asr".
        ("whisper-large-v3-turbo", None, "stt"),
        ("moonshine-base", None, "stt"),
        ("parakeet-stt", None, "stt"),
        ("canary-asr-1b", None, "stt"),
        # tts: "tts" / "kokoro" / "vibevoice" / "-voice".
        ("tts-1-hd", None, "tts"),
        ("kokoro-v1.0", None, "tts"),
        ("vibevoice-1.5b", None, "tts"),
        ("my-voice-model", None, "tts"),
        # img: "flux" / "sdxl" / "stable-diffusion" / "-img".
        ("flux-schnell", None, "img"),
        ("sdxl-turbo", None, "img"),
        ("stable-diffusion-3.5", None, "img"),
        ("dream-img", None, "img"),
        # Id heuristics are case-insensitive.
        ("WHISPER-LARGE", None, "stt"),
        # Default: chat.
        ("llama-3.1-8b-instruct-q4", None, "chat"),
        ("", None, "chat"),
        ("", [], "chat"),
    ],
)
def test_classify(model_id: str, capabilities: Any, expected: str) -> None:
    assert classify(model_id, capabilities=capabilities) == expected


# ── device_to_backend ────────────────────────────────────────────────────────
#
# (device, expected (recipe, llamacpp_backend)) — plan §4.1 + ADR-0008 §6.


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("gpu-rocm", (None, "rocm")),
        ("gpu-vulkan", (None, "vulkan")),
        ("cpu", (None, "cpu")),
        # NPU uses the FLM recipe; no llamacpp_backend.
        ("npu", ("flm", None)),
        # Empty / None → let the load path pick its own defaults.
        ("", (None, None)),
        (None, (None, None)),
        # Unknown devices fall back to (None, None) rather than us
        # inventing a backend tag.
        ("rocm-xtreme-edition", (None, None)),
        # Case / whitespace insensitive.
        ("GPU-ROCM", (None, "rocm")),
        ("  npu  ", ("flm", None)),
    ],
)
def test_device_to_backend(device: str | None, expected: tuple[str | None, str | None]) -> None:
    assert device_to_backend(device) == expected


# ── is_resolvable ────────────────────────────────────────────────────────────


class _FakeRegistry:
    def __init__(self, ids: set[str]) -> None:
        self._ids = ids

    def has(self, model_id: str) -> bool:
        return model_id in self._ids


_FLM_PROBE = [
    {"tag": "gemma4-it:e4b", "installed": True},
    {"tag": "qwen3:0.6b", "installed": False},
]


@pytest.mark.parametrize(
    ("model_id", "registry_ids", "expected"),
    [
        # Registry membership resolves.
        ("llama-3.1-8b", {"llama-3.1-8b"}, True),
        # Not in registry, not FLM → unresolvable.
        ("llama-3.1-8b", set(), False),
        # Installed FLM model resolves without registry membership.
        ("gemma4-it-e4b-FLM", set(), True),
        # FLM tag known but NOT installed → unresolvable.
        ("qwen3-0.6b-FLM", set(), False),
        # Unknown -FLM id → unresolvable.
        ("nope-7b-FLM", set(), False),
        # No registry at all: only FLM resolvability remains.
        ("gemma4-it-e4b-FLM", None, True),
        ("llama-3.1-8b", None, False),
    ],
)
def test_is_resolvable(
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    registry_ids: set[str] | None,
    expected: bool,
) -> None:
    import hal0.providers.flm as flm

    monkeypatch.setattr(flm, "flm_served_models", lambda: _FLM_PROBE)
    registry = _FakeRegistry(registry_ids) if registry_ids is not None else None
    assert is_resolvable(model_id, registry) is expected


# ── canonical_device ─────────────────────────────────────────────────────────
#
# (value, expected) — backend/device → canonical v0.2 device enum.


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Empty → no opinion.
        ("", ""),
        # Already-canonical device ids pass through.
        ("gpu-rocm", "gpu-rocm"),
        ("gpu-vulkan", "gpu-vulkan"),
        ("cpu", "cpu"),
        ("npu", "npu"),
        # Legacy backend tokens map forward (ADR-0006 §7).
        ("rocm", "gpu-rocm"),
        ("vulkan", "gpu-vulkan"),
        ("flm", "npu"),
        ("moonshine", "cpu"),
        ("kokoro", "cpu"),
        # Unknown values fall back to cpu (safe default, warning logged).
        ("totally-bogus", "cpu"),
    ],
)
def test_canonical_device(value: str, expected: str) -> None:
    assert canonical_device(value) == expected


# ── device_to_legacy_backend ─────────────────────────────────────────────────
#
# (device, expected) — catalog device id → deprecated SlotConfig.backend
# token. NOTE(#695): unlike ``device_to_backend``, unknown values pass
# through UNCHANGED (the orchestrator preserved hand-edited tokens for
# downgrade legibility) — do not "unify" this onto (None, None) semantics.


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("gpu-vulkan", "vulkan"),
        ("gpu-rocm", "rocm"),
        ("npu", "flm"),
        ("cpu", "cpu"),
        ("", ""),
        ("weird-token", "weird-token"),
    ],
)
def test_device_to_legacy_backend(device: str, expected: str) -> None:
    assert device_to_legacy_backend(device) == expected


# ── labels_of ────────────────────────────────────────────────────────────────
#
# (slot config dict, expected label set)


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({"model": {"labels": ["tool-calling", "vision"]}}, {"tool-calling", "vision"}),
        ({"model": {"labels": ("a", "b")}}, {"a", "b"}),
        # Non-string labels are stringified.
        ({"model": {"labels": [1, 2]}}, {"1", "2"}),
        # labels not a list/tuple → empty.
        ({"model": {"labels": "tool-calling"}}, set()),
        # No labels key → empty.
        ({"model": {}}, set()),
        # model not a dict → empty.
        ({"model": "primary"}, set()),
        ({"model": None}, set()),
        # No model key at all → empty.
        ({}, set()),
        ({"model": {"labels": []}}, set()),
    ],
)
def test_labels_of(cfg: dict[str, Any], expected: set[str]) -> None:
    assert labels_of(cfg) == expected
