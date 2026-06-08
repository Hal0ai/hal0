"""hal0.providers — Inference backend abstraction layer.

Each Provider is a stateless class that knows how to build the environment
file, start command, and ContainerSpec for one backend type.  The Provider
ABC is the contract between SlotManager and the concrete backends.

Live providers (v0.2+):
    LlamaServerProvider  — llama.cpp (Vulkan default, ROCm opt-in)
    FLMProvider          — AMD NPU (optional, Strix Halo only)
    LemonadeProvider     — Lemonade gateway (sole slot-lifecycle backend)
    ComfyUIProvider      — image-gen pipeline (driven directly by api/routes/v1.py)
    ContainerProvider    — podman container per slot (P1 tracer bullet, issue #655)

Dispatch model (v0.2, ADR-0008 + P1 hybrid):
    SlotManager dispatches through ``LemonadeProvider`` for lemond slots.
    Slots with ``profile`` set (or ``runtime="container"``) dispatch through
    ``ContainerProvider`` (systemd podman unit per slot, loopback upstream).
    The prior ``MoonshineProvider`` + ``KokoroProvider`` self-managed paths were
    vestigial (lemond now owns STT/TTS) and were removed in PR-10 (#620).

Live exceptions (callers that bypass SlotManager dispatch):
    - ``api/routes/v1.py``  → ``ComfyUIProvider.infer()`` for image-gen
    - ``api/routes/hardware.py`` → ``FLMProvider.flm_served_models()`` for NPU footprint
    - ``registry/pull.py``  → ``FLMProvider._probe_flm_catalog()`` for FLM model resolution

See PLAN.md §1, §3 and ARCHITECTURE.md §Key boundaries.
"""

from __future__ import annotations

from hal0.providers.base import ContainerSpec, Provider
from hal0.providers.comfyui import ComfyUIProvider
from hal0.providers.container import ContainerProvider, container_provider
from hal0.providers.flm import FLMProvider
from hal0.providers.lemonade import LemonadeProvider
from hal0.providers.llama_server import LlamaServerProvider

# Provider name → singleton instance.  Providers are stateless (per the
# ABC contract), so one instance per process is enough.
#
# v0.2 (ADR-0008 §1/§2): Lemonade is the sole inference backend for lemond
# slots.  ContainerProvider handles container slots (P1 tracer, issue #655).
# ``ComfyUIProvider`` and ``FLMProvider`` remain for non-SlotManager callers.
# ``MoonshineProvider`` and ``KokoroProvider`` were removed in #620.
_PROVIDERS: dict[str, Provider] = {
    "lemonade": LemonadeProvider(),
    "container": ContainerProvider(),
    "llama-server": LlamaServerProvider(),
    "flm": FLMProvider(),
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
    "ContainerProvider",
    "ContainerSpec",
    "FLMProvider",
    "LemonadeProvider",
    "LlamaServerProvider",
    "Provider",
    "container_provider",
    "get_provider",
    "lemonade_provider",
]
