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
from hal0.model_meta import derive_model_provider
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry


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


def test_backend_variants_provider_specialty() -> None:
    # Provider set → lanes come from the runtime map, tags ignored. No host
    # facts are consulted for this branch (no monkeypatching needed here) —
    # if this hangs or errors reaching for host facts, the specialty branch
    # isn't returning early.
    e = _Entry(provider="moonshine", backends=["vulkan", "rocm"])
    assert catalog._backend_variants(e) == ["cpu"]


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
        Model(id="img-a", path=str(tmp_path / "img-a"), backends=["comfyui"]),
        Model(id="unknown-a", path=str(tmp_path / "unknown-a"), backends=[]),
    ]
    for model in seed:
        registry.add(model)
    return registry


def test_provider_split_matches_legacy_resolution(seeded_registry: ModelRegistry) -> None:
    for model in seeded_registry.list():
        legacy = catalog._provider_for_backend(
            "", "cpu", entry=_StripProvider(model)
        )
        assert derive_model_provider(model.backends) == legacy, model.id
