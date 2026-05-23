"""hal0.providers — Inference backend abstraction layer.

Each Provider is a stateless class that knows how to build the environment
file, start command, and ContainerSpec for one backend type.  The Provider
ABC is the contract between SlotManager and the concrete backends.

v1 providers:
    LlamaServerProvider  — llama.cpp (Vulkan default, ROCm opt-in)
    FLMProvider          — AMD NPU (optional, Strix Halo only)
    MoonshineProvider    — Moonshine STT (CPU/Vulkan)
    KokoroProvider       — Kokoro TTS (CPU/Vulkan)

Port targets: haloai lib/providers/ (base.py, llama_server.py, flm.py).
Moonshine + Kokoro are new wrappers around haloai voice servers.
See PLAN.md §1, §3 and ARCHITECTURE.md §Key boundaries.
"""

from __future__ import annotations

from hal0.providers.base import ContainerSpec, Provider
from hal0.providers.comfyui import ComfyUIProvider
from hal0.providers.flm import FLMProvider
from hal0.providers.kokoro import KokoroProvider
from hal0.providers.lemonade import LemonadeProvider
from hal0.providers.llama_server import LlamaServerProvider
from hal0.providers.moonshine import MoonshineProvider

# Provider name → singleton instance.  Providers are stateless (per the
# ABC contract), so one instance per process is enough.  SlotManager
# itself stays provider-agnostic; the unit-template renderer (which is
# logically "provider-systemd glue") is the only caller of get_provider.
#
# v0.2 (ADR-0008 §2): Lemonade is the only runtime — slots no longer
# spawn per-modality toolbox containers. ``LemonadeProvider`` is the
# operational provider for every slot when ``HAL0_BACKEND=lemonade``;
# the others survive in this table because PR-9 only retired the
# Dockerfiles + systemd template, not the Python provider classes (per
# the anti-scope in PR-8's brief — PR-10 owns their removal). The
# v0.1.x toolbox path remains intact for any caller still on
# ``HAL0_BACKEND`` ≠ ``lemonade``.
_PROVIDERS: dict[str, Provider] = {
    "lemonade": LemonadeProvider(),
    "llama-server": LlamaServerProvider(),
    "flm": FLMProvider(),
    "moonshine": MoonshineProvider(),
    "kokoro": KokoroProvider(),
    "comfyui": ComfyUIProvider(),
}


def get_provider(name: str) -> Provider:
    """Return the singleton Provider for ``name``.

    Raises:
        KeyError: If no provider is registered for that name. The slot
            config schema rejects unknown providers at load time, so this
            should only fire on internal misuse.
    """
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise KeyError(f"no provider registered for {name!r}; known: {sorted(_PROVIDERS)}") from exc


def lemonade_provider() -> LemonadeProvider:
    """Return the process-wide ``LemonadeProvider`` singleton.

    Convenience accessor for callers that know they want the Lemonade
    provider specifically (SlotManager's v0.2 dispatch branch is the
    canonical caller). Equivalent to ``get_provider("lemonade")``
    cast to ``LemonadeProvider``; kept as a typed helper so callers
    don't have to cast.
    """
    return _PROVIDERS["lemonade"]  # type: ignore[return-value]


__all__ = [
    "ComfyUIProvider",
    "ContainerSpec",
    "FLMProvider",
    "KokoroProvider",
    "LemonadeProvider",
    "LlamaServerProvider",
    "MoonshineProvider",
    "Provider",
    "get_provider",
    "lemonade_provider",
]
