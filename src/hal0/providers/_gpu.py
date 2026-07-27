"""Shared helpers for GPU device + group exposure to provider containers.

Lives here so each provider (llama-server, moonshine, kokoro, flm, …) gets
the same numeric-GID treatment for ``docker run --group-add``: the toolbox
images ship with a stock ``ubuntu:24.04`` ``/etc/group`` that has no
``render``/``video`` entries, so passing the names fails fast inside the
container ("unable to find group ..."). The kernel only checks integers
when gating access to ``/dev/dri/renderD128`` etc., so resolve to host
GIDs once and pass them through.

Vendor split (GPU generalization wave):

* AMD path — ``/dev/kfd`` + explicit ``/dev/dri`` nodes passed as plain
  ``--device=<path>`` entries plus the render/video GIDs above.
* NVIDIA path — CDI (Container Device Interface) via the
  nvidia-container-toolkit: ``--device nvidia.com/gpu=all`` (or
  ``nvidia.com/gpu=<n>`` when a slot pins one GPU). CDI names are NOT
  filesystem paths: no existence filtering, no ``--group-add`` — the CDI
  spec injects the device nodes, libraries, and permissions itself.

Which path applies is decided from the slot's declared device/profile
(``gpu-cuda`` device or a profile with ``backend="cuda"``) — never by
probing the host at spec-build time.
"""

from __future__ import annotations

import json
import os
import stat

import structlog

log = structlog.get_logger(__name__)

# Linux-convention GID fallbacks (Strix Halo LXC values; also the historical
# hal0 defaults). Used only as the LAST resort in resolve_gpu_group_ids —
# see the fallback chain documented there.
_GPU_GROUP_FALLBACK_GIDS: dict[str, int] = {"render": 993, "video": 44}


def resolve_gpu_device_paths(
    kfd_path: str = "/dev/kfd",
    dri_dir: str = "/dev/dri",
) -> list[str]:
    """Return explicit GPU device-node paths to pass via ``--device=``.

    Docker recurses a ``--device=/dev/dri`` *directory* and adds every node
    under it; podman does not, and errors ``no devices found in /dev/dri`` on
    hosts whose /dev/dri holds non-standard nodes (e.g. an ``amdgpu`` node and
    no ``card0``). So we enumerate the actual character devices and pass each
    one explicitly — which is correct for docker too.

    Includes ``kfd_path`` when it exists, then every character device directly
    under ``dri_dir`` (sorted). Subdirectories (``by-path``) and regular files
    are skipped.

    Falls back to the legacy directory paths ``["/dev/kfd", "/dev/dri"]`` when
    neither exists (CI / no-GPU dev box) so unit rendering stays deterministic
    off-GPU; no container actually runs there.
    """
    paths: list[str] = []
    if os.path.exists(kfd_path):
        paths.append(kfd_path)
    try:
        entries = sorted(os.listdir(dri_dir))
    except OSError:
        entries = []
    for name in entries:
        node = os.path.join(dri_dir, name)
        try:
            if stat.S_ISCHR(os.stat(node).st_mode):
                paths.append(node)
        except OSError:
            continue
    if not paths:
        return ["/dev/kfd", "/dev/dri"]
    return paths


def _device_node_for_group(name: str, node_paths: list[str]) -> str | None:
    """Pick the discovered device node that gates a GPU access group.

    ``render`` nodes are named ``renderD*`` (e.g. ``renderD128``); ``video``
    nodes are the older KMS/master nodes, named ``card*`` (occasionally
    ``video*``). Returns the first (sorted-order) match from
    :func:`resolve_gpu_device_paths`'s output, or ``None`` when no node of
    that kind was discovered (CI/no-GPU box, or the bare-directory fallback
    path, which never matches these prefixes).
    """
    prefixes = ("renderD",) if name == "render" else ("card", "video")
    for path in node_paths:
        if os.path.basename(path).startswith(prefixes):
            return path
    return None


def _probed_gpu_group_gids() -> dict[str, int]:
    """GIDs `hal0 probe` recorded in hardware.json (``gpu_group_gids``).

    Raw-JSON read (no pydantic) so this stays cheap on every spec build and
    never raises: a missing / unparseable / pre-wave hardware.json simply
    yields ``{}`` and the caller moves on to the next fallback.
    """
    try:
        from hal0.config import paths as _paths

        raw = json.loads(_paths.hardware_json().read_text())
        table = raw.get("gpu_group_gids")
        if not isinstance(table, dict):
            return {}
        return {str(k): int(v) for k, v in table.items()}
    except Exception:
        return {}


def resolve_gpu_group_ids(node_paths: list[str] | None = None) -> list[int]:
    """Return numeric GIDs for the host's GPU access groups (render, video).

    ``node_paths`` lets a caller that has ALREADY resolved its device nodes
    (the benchmark harness — ``hal0.bench.devices``, which may be pointed at
    an operator-overridden node set) reuse them instead of re-enumerating
    ``/dev/dri``. Default ``None`` keeps the historical behaviour: resolve
    the host's nodes here.

    Fallback chain, PER GROUP, most-authoritative source first:

      1. the OWNING gid of the actual device node (``os.stat(node).st_gid``
         on the ``renderD*`` node for ``render``, the ``card*``/``video*``
         node for ``video``) — this is what the kernel actually gates on,
         so it is correct even when the host's group NAME for that gid
         differs from "render"/"video" (e.g. a halo143-class host where
         ``renderD128`` is owned by gid 993 but gid 993's /etc/group name
         is "clock", not "render" — ``grp.getgrnam("render")`` there
         resolves a DIFFERENT, wrong gid and the container ends up unable
         to read the device on any non-root-owner slot);
      2. live ``grp.getgrnam`` against the running host's /etc/group, used
         only when the device node is absent (CI/no-GPU box) — still
         better than nothing when the name happens to line up;
      3. the probe-time record in hardware.json (``gpu_group_gids``, written
         by ``hal0 probe``) — covers deployments where the API process runs
         in a context whose /etc/group lacks the entries the host actually
         uses for /dev/dri (e.g. minimal containers/chroots);
      4. the Linux-convention constants (render=993, video=44) — last resort
         so unit rendering stays deterministic on hosts with neither source
         (also the sole path on platforms without the ``grp`` module).

    Duplicate GIDs (render and video mapping to the same id) are collapsed,
    order preserved.
    """
    probed = _probed_gpu_group_gids()
    if node_paths is None:
        node_paths = resolve_gpu_device_paths()
    gids: list[int] = []
    try:
        import grp

        for name, fallback in _GPU_GROUP_FALLBACK_GIDS.items():
            node = _device_node_for_group(name, node_paths)
            if node is not None:
                try:
                    gids.append(os.stat(node).st_gid)
                    continue
                except OSError:
                    log.debug("provider.gpu_group_node_stat_failed", group=name, node=node)
            try:
                gids.append(grp.getgrnam(name).gr_gid)
                continue
            except KeyError:
                log.debug("provider.gpu_group_missing", group=name)
            recorded = probed.get(name)
            gids.append(recorded if recorded is not None else fallback)
    except ImportError:
        # No grp module (non-POSIX host): device node, then probe, then constants.
        for name, fallback in _GPU_GROUP_FALLBACK_GIDS.items():
            node = _device_node_for_group(name, node_paths)
            if node is not None:
                try:
                    gids.append(os.stat(node).st_gid)
                    continue
                except OSError:
                    log.debug("provider.gpu_group_node_stat_failed", group=name, node=node)
            gids.append(probed.get(name, fallback))
    # De-dup while preserving order (render/video can share a GID).
    seen: set[int] = set()
    out: list[int] = []
    for g in gids:
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out


# ── NVIDIA / CDI ─────────────────────────────────────────────────────────────


def is_nvidia_gpu_device(device: str | None, profile_backend: str | None = None) -> bool:
    """True when a slot's declared device/profile selects the NVIDIA path.

    Decided from configuration only (``device == "gpu-cuda"`` or the profile's
    ``backend == "cuda"``) — deliberately NOT from probing the host at
    spec-build time, so unit rendering is deterministic and previewable.
    """
    if (device or "").strip().lower() == "gpu-cuda":
        return True
    return (profile_backend or "").strip().lower() == "cuda"


def nvidia_cdi_devices(gpu_index: int | None = None) -> list[str]:
    """CDI device names for NVIDIA GPU passthrough.

    ``--device nvidia.com/gpu=all`` maps every GPU; a non-None ``gpu_index``
    maps exactly that GPU (``nvidia.com/gpu=<n>``). These are CDI names, not
    paths: callers must NOT existence-filter them or attach ``--group-add``
    GIDs — the CDI spec (generated by ``nvidia-ctk cdi generate``) injects
    nodes, libraries, and permissions itself.
    """
    if gpu_index is not None and int(gpu_index) >= 0:
        return [f"nvidia.com/gpu={int(gpu_index)}"]
    return ["nvidia.com/gpu=all"]


# ── multi-GPU pinning (SlotConfig.gpu_index) ─────────────────────────────────


def gpu_visibility_env(device: str | None, gpu_index: int | None) -> dict[str, str]:
    """Visibility env a pinned slot needs, keyed by device family.

    Returns ``{}`` when ``gpu_index`` is None (no pinning — unchanged
    behaviour) or the device is not a GPU family. Callers merge the result
    UNDER ``[server].env`` so an operator's explicit env always wins::

        env = {**gpu_visibility_env(device, idx), **server_env}

    Per family:

    * ``gpu-rocm``   → ``HIP_VISIBLE_DEVICES`` + ``ROCR_VISIBLE_DEVICES``
      (HIP runtime and ROCr each honour their own variable).
    * ``gpu-vulkan`` → ``GGML_VK_VISIBLE_DEVICES`` (llama.cpp's Vulkan
      backend device filter).
    * ``gpu-cuda``   → ``CUDA_VISIBLE_DEVICES=0``: the CDI mapping
      (``nvidia.com/gpu=<n>``) already exposes only the pinned GPU, which
      appears as ordinal 0 inside the container.
    """
    if gpu_index is None:
        return {}
    idx = str(int(gpu_index))
    d = (device or "").strip().lower()
    if d == "gpu-rocm":
        return {"HIP_VISIBLE_DEVICES": idx, "ROCR_VISIBLE_DEVICES": idx}
    if d == "gpu-vulkan":
        return {"GGML_VK_VISIBLE_DEVICES": idx}
    if d == "gpu-cuda":
        return {"CUDA_VISIBLE_DEVICES": "0"}
    return {}


__all__ = [
    "gpu_visibility_env",
    "is_nvidia_gpu_device",
    "nvidia_cdi_devices",
    "resolve_gpu_device_paths",
    "resolve_gpu_group_ids",
]
