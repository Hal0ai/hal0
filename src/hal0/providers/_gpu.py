"""Shared helpers for GPU device + group exposure to provider containers.

Lives here so each provider (llama-server, moonshine, kokoro, flm, …) gets
the same numeric-GID treatment for ``docker run --group-add``: the toolbox
images ship with a stock ``ubuntu:24.04`` ``/etc/group`` that has no
``render``/``video`` entries, so passing the names fails fast inside the
container ("unable to find group ..."). The kernel only checks integers
when gating access to ``/dev/dri/renderD128`` etc., so resolve to host
GIDs once and pass them through.
"""

from __future__ import annotations

import os
import stat

import structlog

log = structlog.get_logger(__name__)


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


def resolve_gpu_group_ids() -> list[int]:
    """Return numeric GIDs for the host's GPU access groups.

    Falls back to the Linux convention (render=993, video=44) on systems
    without ``grp`` (Windows hosts). Skips groups that don't exist on the
    host rather than guessing — passing a non-existent GID to docker is a
    silent permissions hole.
    """
    gids: list[int] = []
    try:
        import grp

        for name, _fallback in (("render", 993), ("video", 44)):
            try:
                gids.append(grp.getgrnam(name).gr_gid)
            except KeyError:
                log.debug("provider.gpu_group_missing", group=name)
    except ImportError:
        gids = [993, 44]
    return gids


__all__ = ["resolve_gpu_device_paths", "resolve_gpu_group_ids"]
