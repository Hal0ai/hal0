"""Provider-first resolution (Task 2 of the slot/model drawer overhaul).

The explicit ``Model.provider`` field (Task 1,
:func:`hal0.model_meta.derive_model_provider`) must now win over the legacy
backend-tag resolution in both catalog surfaces:

* :func:`hal0.capabilities.catalog._provider_for_backend` — the picker row's
  ``provider`` column.
* :func:`hal0.capabilities.catalog._backend_variants` — the picker row's
  fan-out of runnable host-backend lanes.

Plus the migration-safety gate: for every model already in a populated
registry, deriving the provider from ``backends`` (Task 1's pure function)
must agree with what the legacy tag-based catalog resolution already
produces — the split must be a pure refactor, not a behaviour change, for
pre-existing rows that have no explicit ``provider`` set yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities import catalog
from hal0.model_meta import RUNTIME_FAMILIES, derive_model_provider
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry


def _hosts(*ids: str) -> list[dict[str, Any]]:
    """A minimal ``available_backends()``-shaped stand-in, host ids only."""
    return [{"id": i} for i in ids]


class _Entry:
    def __init__(self, provider: str | None = None, backend: str = "", backends: Any = ()):
        self.provider = provider
        self.backend = backend
        self.backends = list(backends)


def test_provider_for_backend_prefers_explicit_field() -> None:
    e = _Entry(provider="kokoro", backends=["vulkan"])  # tags would say llama-server
    assert catalog._provider_for_backend("", "gpu-vulkan", entry=e) == "kokoro"


def test_provider_for_backend_legacy_paths_unchanged() -> None:
    # No provider attr / None → exact pre-split behavior.
    e = _Entry(provider=None, backends=["moonshine"])
    assert catalog._provider_for_backend("", "cpu", entry=e) == "moonshine"
    assert catalog._provider_for_backend("", "npu", entry=_Entry()) == "flm"


def test_backend_variants_provider_specialty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Provider set → lanes come from the runtime map, tags ignored.
    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("cpu"))
    e = _Entry(provider="moonshine", backends=["vulkan", "rocm"])
    assert catalog._backend_variants(e) == ["cpu"]


def test_backend_variants_provider_specialty_filters_by_host_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit provider's lanes are still intersected with what the host
    # actually advertises — same rule the legacy ``_RUNTIME_TO_HOST_BACKENDS``
    # tag branch applies (moonshine's CPU-only wheel, ComfyUI's Vulkan-only
    # image, …). Kokoro's runtime map offers ("gpu-vulkan", "cpu").
    e = _Entry(provider="kokoro", backends=["vulkan"])

    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("cpu"))
    assert catalog._backend_variants(e) == ["cpu"], "gpu-vulkan absent from host, must be dropped"

    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("gpu-vulkan", "cpu"))
    assert catalog._backend_variants(e) == ["gpu-vulkan", "cpu"]


def test_backend_variants_provider_specialty_npu_unfiltered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirrors the untouched legacy ``{"flm", "npu"}`` tag branch: NPU is
    # offered unconditionally, with no ``available_backends()`` intersection.
    # A host with NO backends at all (not even the NPU) still gets the lane —
    # if this ever regresses to consulting host facts, this test must fail.
    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts())
    e = _Entry(provider="flm")
    assert catalog._backend_variants(e) == ["npu"]


class _StripProvider:
    """Proxy that exposes everything about a Model EXCEPT ``.provider``.

    Used to force ``_provider_for_backend``/``_backend_variants`` down the
    legacy tag-resolution path even though the underlying ``Model`` may
    carry an explicit ``provider`` value.
    """

    def __init__(self, model: Model) -> None:
        self._model = model

    def __getattr__(self, name: str) -> Any:
        if name == "provider":
            raise AttributeError(name)
        return getattr(self._model, name)


@pytest.fixture
def seeded_registry(tmp_path: Path) -> ModelRegistry:
    """A populated registry spanning every legacy backend-tag family."""
    registry = ModelRegistry(registry_dir=tmp_path / "registry")
    seed = [
        Model(id="chat-a", path=str(tmp_path / "chat-a.gguf"), backends=["llama-server"]),
        Model(id="chat-b", path=str(tmp_path / "chat-b.gguf"), backends=["vulkan"]),
        Model(id="chat-c", path=str(tmp_path / "chat-c.gguf"), backends=["rocm"]),
        Model(id="npu-a", path=str(tmp_path / "npu-a"), backends=["flm"]),
        Model(id="stt-a", path=str(tmp_path / "stt-a"), backends=["moonshine"]),
        Model(id="tts-a", path=str(tmp_path / "tts-a"), backends=["kokoro"]),
        Model(id="tts-b", path=str(tmp_path / "tts-b"), backends=["qwen3tts"]),
        Model(id="img-a", path=str(tmp_path / "img-a"), backends=["comfyui"]),
        Model(id="unknown-a", path=str(tmp_path / "unknown-a"), backends=[]),
    ]
    for model in seed:
        registry.add(model)
    return registry


def test_provider_split_matches_legacy_resolution(seeded_registry: ModelRegistry) -> None:
    resolved: set[str] = set()
    for model in seeded_registry.list():
        legacy = catalog._provider_for_backend("", "cpu", entry=_StripProvider(model))
        assert derive_model_provider(model.backends) == legacy, model.id
        resolved.add(legacy)
    # Total over RUNTIME_FAMILIES — no family is excluded from the parity
    # check. If a future runtime family is added to either map without a
    # matching seed row (or without keeping both maps in sync), this fails
    # instead of silently passing on a narrower fixture.
    assert resolved == set(RUNTIME_FAMILIES)


# ── empty-``backends`` llama fall-through (final-review fix) ──────────────────
#
# runs_on_for_model(Model(backends=[], provider="llama-server")) used to
# return [] — the tag fan-out in ``_backend_variants`` only ran off
# ``entry.backend``/``entry.backends`` tags, and an explicit llama-server row
# (or a legacy provider=None row, which derives to llama-server too) with no
# tags supplied neither of those. Both must now fall through to the same
# host-present AMD/CPU fan-out as a ``backends=["llama-server"]`` row.


def test_backend_variants_llama_provider_empty_backends_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("gpu-vulkan", "cpu"))
    m = Model(
        id="llama-a", path=str(tmp_path / "llama-a.gguf"), backends=[], provider="llama-server"
    )
    assert catalog._backend_variants(m) == ["gpu-vulkan", "cpu"]


def test_backend_variants_legacy_none_provider_empty_backends_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # provider=None (pre-Task-1 row) derives to llama-server; must get the
    # same fall-through as an explicit provider="llama-server" row.
    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("gpu-vulkan", "cpu"))
    m = Model(id="legacy-a", path=str(tmp_path / "legacy-a.gguf"), backends=[], provider=None)
    assert derive_model_provider(m.backends) == "llama-server"
    assert catalog._backend_variants(m) == ["gpu-vulkan", "cpu"]


def test_runs_on_for_model_non_empty_for_empty_backends_llama_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """model_to_dict(model)["runs_on"] must not be silently empty — the
    coverage gap this branch's final review flagged."""
    from hal0.services.models_service import model_to_dict

    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("gpu-vulkan", "cpu"))
    m = Model(
        id="llama-b", path=str(tmp_path / "llama-b.gguf"), backends=[], provider="llama-server"
    )
    assert model_to_dict(m)["runs_on"] == ["gpu-vulkan", "cpu"]


# ── rocm tag host gate (#2029) ────────────────────────────────────────────────
#
# The ``elif low in {"rocm", "gpu-rocm"}`` tag branch used to append gpu-rocm
# unconditionally, with no ``available_backends()`` intersection. Every
# ``hal0 model add`` GGUF gets ``backends=["vulkan","rocm","cuda","cpu"]``
# (``registry.detect._GGUF_BACKENDS``), so on a kfd-less host each user-added
# model advertised a gpu-rocm lane at fit_status: allowed that
# require_kfd_for_gpu_slot then refused at load time. The rocm branch must go
# through the same host intersection as its llamacpp / runtime-map siblings.


def test_backend_variants_rocm_tag_gated_off_kfdless_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Host with no compute_capable GPU: available_backends() omits gpu-rocm.
    # A registry GGUF's default tag list must not leak a gpu-rocm lane.
    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("gpu-vulkan", "cpu"))
    monkeypatch.setattr(catalog, "host_is_amd_gpu", lambda: True)
    m = Model(
        id="added-a",
        path=str(tmp_path / "added-a.gguf"),
        backends=["vulkan", "rocm", "cuda", "cpu"],
    )
    variants = catalog._backend_variants(m)
    assert "gpu-rocm" not in variants, f"kfd-less host must not advertise gpu-rocm: {variants!r}"
    assert variants == ["gpu-vulkan", "cpu"]


def test_backend_variants_bare_rocm_tag_gated_off_kfdless_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A rocm-only tag on a host without gpu-rocm yields no lanes at all —
    # consistent with how a vulkan-only tag behaves on a GPU-less host.
    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("cpu"))
    monkeypatch.setattr(catalog, "host_is_amd_gpu", lambda: False)
    m = Model(id="rocm-only", path=str(tmp_path / "rocm-only.gguf"), backends=["rocm"])
    assert catalog._backend_variants(m) == []


def test_backend_variants_rocm_tag_kept_on_kfd_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # kfd-present AMD host: behaviour unchanged — gpu-rocm first (via the
    # llamacpp fan-out's AMD ordering), vulkan and cpu beside it.
    monkeypatch.setattr(
        catalog, "available_backends", lambda: _hosts("gpu-rocm", "gpu-vulkan", "cpu")
    )
    monkeypatch.setattr(catalog, "host_is_amd_gpu", lambda: True)
    m = Model(
        id="added-b",
        path=str(tmp_path / "added-b.gguf"),
        backends=["vulkan", "rocm", "cuda", "cpu"],
    )
    assert catalog._backend_variants(m) == ["gpu-rocm", "gpu-vulkan", "cpu"]

    # And a bare rocm tag still advertises the lane when the host has it.
    bare = Model(id="rocm-bare", path=str(tmp_path / "rocm-bare.gguf"), backends=["rocm"])
    assert catalog._backend_variants(bare) == ["gpu-rocm"]


def test_backend_variants_qwen3tts_yields_gpu_rocm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(catalog, "available_backends", lambda: _hosts("gpu-rocm", "cpu"))
    m = Model(id="tts-c", path=str(tmp_path / "tts-c"), backends=[], provider="qwen3tts")
    assert catalog._backend_variants(m) == ["gpu-rocm"]
