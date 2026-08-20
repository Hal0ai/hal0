"""Boot-time convergence of the ROCm compute node's group (#1953).

``/dev/kfd`` is recreated on every boot — by udev on bare metal, by container
start from the host's ``dev`` entry on an LXC — so any ownership fix applied at
install or update time is undone by the next reboot unless the host entry
itself carries ``gid=``. This module is the ``ExecStart`` of
``hal0-gpu-perms.service``, which re-applies it once per boot before
``hal0.target``.

Why a service and not a ``tmpfiles.d`` / ``udev`` rule: both of those would
have to name a gid, and a *baked* gid is precisely the bug this line of work
exists to remove. The kernel gates on the integer, and the integer is not
portable across hosts — on a halo143-class box ``renderD128`` is owned by a gid
whose ``/etc/group`` name is ``clock`` while ``render`` resolves elsewhere.
Re-running the converge lets the target gid be *re-derived from the render
node* on each boot, so a box whose passthrough changes is still correct.

Scope, deliberately small. Post-#1953 no hal0-user code path opens ``/dev/kfd``
— the slot-load guard asks about the root runner, and the bench harness only
resolves device PATHS to hand to podman. So this does not fix a live outage; it
keeps the invariant true rather than leaving it resting on "nothing happens to
need it right now", which is a fact that can change silently. A mismatched gid
costs hal0-user probes and diagnostics their GPU visibility, not inference.

Never fails the boot. Every outcome — converged, already fine, nothing to do,
refused by an unprivileged LXC — exits 0. A GPU-permissions tidy-up must not be
able to wedge ``hal0.target``.
"""

from __future__ import annotations

import sys

import structlog

log = structlog.get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Converge ``/dev/kfd``'s group, log the outcome, and always succeed."""
    from hal0.providers._gpu import converge_kfd_device_group, host_is_amd_gpu

    if not host_is_amd_gpu():
        log.info("gpu_perms.skipped", reason="amdgpu is not bound on this host")
        return 0
    try:
        status, detail = converge_kfd_device_group()
    except Exception as exc:  # pragma: no cover - defensive; converge catches its own
        log.warning("gpu_perms.failed", error=str(exc))
        return 0
    if status == "changed":
        log.warning("gpu_perms.converged", detail=detail)
    elif status == "failed":
        # Unprivileged LXC: the node's ownership is host-mapped and chown is
        # EPERM. The remedy is a gid= on the host's dev entry, which the detail
        # string spells out. Not a boot failure.
        log.warning("gpu_perms.refused", detail=detail)
    else:
        log.info("gpu_perms.noop", status=status, detail=detail)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
