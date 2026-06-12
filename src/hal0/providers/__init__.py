"""hal0.providers — Inference backend abstraction layer.

Each Provider is a stateless class that knows how to build the environment
file, start command, and ContainerSpec for one backend type.  The Provider
ABC is the contract between SlotManager and the concrete backends.

Live providers:
    ContainerProvider    — podman container per slot (the sole slot-lifecycle
                           backend; systemd unit per slot, loopback upstream)
    LlamaServerProvider  — llama.cpp argv/env derivation (Vulkan default,
                           ROCm opt-in) consumed by container profiles
    FLMProvider          — AMD NPU via host FLM (optional, Strix Halo only)
    ComfyUIProvider      — image-gen pipeline (driven directly by api/routes/v1.py)

Dispatch model (container-only):
    SlotManager dispatches every slot through ``ContainerProvider``.
    The prior ``MoonshineProvider`` + ``KokoroProvider`` self-managed paths
    were vestigial and removed in PR-10 (#620).

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
from hal0.providers.llama_server import LlamaServerProvider

# Provider name → singleton instance.  Providers are stateless (per the
# ABC contract), so one instance per process is enough.
#
# ContainerProvider runs every slot (podman + systemd units).
# ``ComfyUIProvider`` and ``FLMProvider`` remain for non-SlotManager callers.
# ``MoonshineProvider`` and ``KokoroProvider`` were removed in #620.
_PROVIDERS: dict[str, Provider] = {
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


__all__ = [
    "ComfyUIProvider",
    "ContainerProvider",
    "ContainerSpec",
    "FLMProvider",
    "LlamaServerProvider",
    "Provider",
    "container_provider",
    "get_provider",
]
