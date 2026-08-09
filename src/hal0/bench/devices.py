"""GPU device-node resolution for the benchmark harness (issue #1303).

Why this module exists
----------------------
The installed llama-bench harness (``installer/bench/config.sh``) used to
hardcode its podman passthrough flags::

    --device=/dev/kfd
    --device=/dev/dri/amdgpu      # <- not a kernel-conventional node name
    --device=/dev/dri/renderD128
    --group-add=993 --group-add=44

That is wrong three ways: ``/dev/dri/amdgpu`` only exists on a couple of
distro/driver combinations (a stock LXC exposes ``card1`` + ``renderD128``,
and every cell fails with ``stat /dev/dri/amdgpu: no such file or
directory``); the GIDs are host-specific; and the AMD-only shape cannot
express the NVIDIA (CDI) or CPU-only hardware tiers that the v1.0 baseline
matrix has to cover.

This module is the ONE resolver both paths share. It does **not** probe the
host itself — it delegates to the production slot-container helpers in
:mod:`hal0.providers._gpu` (:func:`~hal0.providers._gpu.resolve_gpu_device_paths`,
:func:`~hal0.providers._gpu.resolve_gpu_group_ids`,
:func:`~hal0.providers._gpu.nvidia_cdi_devices`) plus the ``hal0 probe``
snapshot in ``hardware.json``, so a benchmark container sees exactly what a
production slot container sees.

Resolution order
----------------
1. **Explicit environment override** — ``HAL0_BENCH_GPU_DEVICES`` (a full
   colon/comma-separated node list) or the per-node
   ``HAL0_BENCH_KFD_DEVICE`` / ``HAL0_BENCH_CARD_DEVICE`` /
   ``HAL0_BENCH_RENDER_DEVICE``. Kept for unusual passthrough layouts and
   for recovery/CI. Overrides are validated STRICTLY: a bad path raises
   :class:`BenchDeviceError` listing what was checked, rather than silently
   falling back (the whole bug being fixed here was a silent wrong default).
2. **Hardware tier from the probe** — ``hardware.json``'s primary GPU vendor.
   ``nvidia`` selects the CDI path (``nvidia.com/gpu=all``), which is a device
   *name*, never a filesystem path, and takes no ``--group-add``.
3. **Live discovery via the shared resolver** — the AMD/unknown path:
   ``/dev/kfd`` plus every character device under ``/dev/dri``, exactly as
   :func:`hal0.providers._gpu.resolve_gpu_device_paths` enumerates them for a
   slot container.
4. **CPU tier** — nothing resolved, so nothing is passed. A CPU-tier
   benchmark run must never demand a DRI node; it gets an empty device list,
   no group IDs, and no error.

Test/recovery seams: ``HAL0_BENCH_KFD_PATH`` and ``HAL0_BENCH_DRI_DIR``
relocate the two discovery roots (used by the unit tests so they need no real
GPU), and ``HAL0_BENCH_TIER`` pins the tier (``cpu`` / ``amd`` / ``nvidia``).

Shell surface: ``python -m hal0.bench.devices --format env`` emits a
``KEY=VALUE`` block the harness parses without ``eval`` — see
``installer/bench/config.sh``. ``hal0 bench devices`` is the operator view.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field

from hal0.errors import Hal0Error

__all__ = [
    "TIER_AMD",
    "TIER_CPU",
    "TIER_NVIDIA",
    "BenchDeviceError",
    "BenchDeviceSpec",
    "main",
    "resolve_bench_devices",
]

# ── tiers ─────────────────────────────────────────────────────────────────────
#: AMD path — explicit /dev/kfd + /dev/dri nodes plus render/video GIDs.
TIER_AMD = "amd"
#: NVIDIA path — CDI names injected by the nvidia-container-toolkit.
TIER_NVIDIA = "nvidia"
#: CPU path — no GPU passthrough at all (and no DRI node required).
TIER_CPU = "cpu"

# ── env names ─────────────────────────────────────────────────────────────────
ENV_DEVICES = "HAL0_BENCH_GPU_DEVICES"
ENV_KFD = "HAL0_BENCH_KFD_DEVICE"
ENV_CARD = "HAL0_BENCH_CARD_DEVICE"
ENV_RENDER = "HAL0_BENCH_RENDER_DEVICE"
ENV_GROUPS = "HAL0_BENCH_GPU_GROUPS"
ENV_TIER = "HAL0_BENCH_TIER"
ENV_KFD_PATH = "HAL0_BENCH_KFD_PATH"
ENV_DRI_DIR = "HAL0_BENCH_DRI_DIR"

_OVERRIDE_ENV = (ENV_DEVICES, ENV_KFD, ENV_CARD, ENV_RENDER)

#: Node basenames a benchmark container may be handed. Anything with a path
#: separator, ``..``, or shell-significant characters is rejected outright.
_NODE_NAME_RE = re.compile(r"^[A-Za-z0-9_.:+-]+$")
#: Extra device root that is always allowed alongside the (relocatable) DRI
#: dir and KFD path — the XDNA NPU nodes.
_ACCEL_DIR = "/dev/accel"


class BenchDeviceError(Hal0Error):
    """A benchmark device setting could not be resolved into a usable node."""

    code = "bench.device_unresolvable"
    status = 400


@dataclass(frozen=True)
class BenchDeviceSpec:
    """The GPU passthrough a benchmark container should be launched with.

    ``devices`` holds ``--device=`` VALUES: filesystem node paths on the AMD
    tier, CDI names (``nvidia.com/gpu=all``) on the NVIDIA tier, and nothing
    at all on the CPU tier. ``group_ids`` is only ever populated on the AMD
    tier (CDI injects its own permissions).
    """

    tier: str
    devices: tuple[str, ...] = ()
    group_ids: tuple[int, ...] = ()
    source: str = "none"
    gpu_label: str = ""
    checked: tuple[str, ...] = field(default=(), repr=False)

    @property
    def card_node(self) -> str:
        """The DRM card/KMS node, or "" — used for sysfs telemetry lookups."""
        return _first_node(self.devices, ("card", "video"))

    @property
    def render_node(self) -> str:
        """The DRM render node (``renderD*``), or ""."""
        return _first_node(self.devices, ("renderD",))

    def podman_flags(self) -> list[str]:
        """The exact ``--device=`` / ``--group-add=`` argv for this spec.

        Empty on the CPU tier — the benchmark still runs, just without GPU
        passthrough.
        """
        flags = [f"--device={d}" for d in self.devices]
        flags += [f"--group-add={g}" for g in self.group_ids]
        return flags

    @property
    def run_flags(self) -> list[str]:
        """Alias for :meth:`podman_flags` under the name
        :func:`hal0.bench.harness.compose_podman_argv` spreads directly into a
        ``podman run`` argv — a pure accessor, no resolution logic here."""
        return self.podman_flags()

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "devices": list(self.devices),
            "group_ids": list(self.group_ids),
            "source": self.source,
            "gpu_label": self.gpu_label,
            "card_node": self.card_node,
            "render_node": self.render_node,
            "podman_flags": self.podman_flags(),
        }

    def to_env_block(self) -> str:
        """``KEY=VALUE`` lines for shell consumers (parsed, never ``eval``ed).

        One ``BENCH_RUN_FLAG=`` line per podman argument keeps the shell side
        free of quoting/word-splitting hazards in a root-run script.
        """
        lines = [
            f"BENCH_TIER={self.tier}",
            f"BENCH_DEVICE_SOURCE={self.source}",
            f"BENCH_GPU_LABEL={self.gpu_label}",
            f"BENCH_CARD_NODE={self.card_node}",
            f"BENCH_RENDER_NODE={self.render_node}",
        ]
        lines += [f"BENCH_RUN_FLAG={flag}" for flag in self.podman_flags()]
        return "\n".join(lines)


def _first_node(devices: tuple[str, ...], prefixes: tuple[str, ...]) -> str:
    for dev in devices:
        # CDI entries (``nvidia.com/gpu=all``) are names, not paths — skip.
        if "=" in dev:
            continue
        if os.path.basename(dev).startswith(prefixes):
            return dev
    return ""


def _is_char_device(path: str) -> bool:
    try:
        return stat.S_ISCHR(os.stat(path).st_mode)
    except OSError:
        return False


def _node_allowed(node: str, kfd_path: str, dri_dir: str) -> bool:
    """True when ``node`` is inside one of the permitted device roots.

    The roots are the resolved KFD path, the DRI directory, and
    ``/dev/accel`` — i.e. ``/dev/kfd``, ``/dev/dri/<name>``,
    ``/dev/accel/<name>`` on a real host. They are parameters rather than a
    baked-in ``/dev/...`` regex so the ``HAL0_BENCH_KFD_PATH`` /
    ``HAL0_BENCH_DRI_DIR`` seams (tests, unusual passthrough layouts) stay
    covered by the SAME check instead of bypassing it. The privileged
    ``hal0-benchctl``/``config.sh`` seam mirrors this in shell.
    """
    if ".." in node:
        return False
    if node == kfd_path:
        return True
    for root in (dri_dir.rstrip("/"), _ACCEL_DIR):
        prefix = f"{root}/"
        if node.startswith(prefix):
            return bool(_NODE_NAME_RE.match(node[len(prefix) :]))
    return False


def _validate_override(
    path: str, env_name: str, checked: list[str], kfd_path: str, dri_dir: str
) -> str:
    """Validate one operator-supplied node path, or raise with the details."""
    node = path.strip()
    checked.append(node)
    if not _node_allowed(node, kfd_path, dri_dir):
        raise BenchDeviceError(
            f"{env_name}={node!r} is not an allowed GPU device node "
            f"(expected {kfd_path}, {dri_dir}/<node>, or {_ACCEL_DIR}/<node>)",
            {"env": env_name, "path": node, "checked": checked},
        )
    if not os.path.exists(node):
        raise BenchDeviceError(
            f"{env_name}={node!r} does not exist — checked: {', '.join(checked)}",
            {"env": env_name, "path": node, "checked": checked},
        )
    if not _is_char_device(node):
        raise BenchDeviceError(
            f"{env_name}={node!r} is not a character device — "
            "podman --device needs a real device node",
            {"env": env_name, "path": node, "checked": checked},
        )
    return node


def _override_devices(
    env: Mapping[str, str], kfd_path: str, dri_dir: str
) -> tuple[list[str], list[str]] | None:
    """Devices from the explicit env overrides, or None when none are set."""
    if not any(env.get(name, "").strip() for name in _OVERRIDE_ENV):
        return None
    checked: list[str] = []
    devices: list[str] = []
    raw_list = env.get(ENV_DEVICES, "").strip()
    if raw_list:
        for token in re.split(r"[,:\s]+", raw_list):
            if token:
                devices.append(_validate_override(token, ENV_DEVICES, checked, kfd_path, dri_dir))
    for name in (ENV_KFD, ENV_CARD, ENV_RENDER):
        raw = env.get(name, "").strip()
        if raw:
            node = _validate_override(raw, name, checked, kfd_path, dri_dir)
            if node not in devices:
                devices.append(node)
    return devices, checked


def _override_group_ids(env: Mapping[str, str]) -> list[int] | None:
    raw = env.get(ENV_GROUPS, "").strip()
    if not raw:
        return None
    out: list[int] = []
    for token in re.split(r"[,:\s]+", raw):
        if not token:
            continue
        try:
            gid = int(token)
        except ValueError as exc:
            raise BenchDeviceError(
                f"{ENV_GROUPS}={raw!r} must be a comma-separated list of numeric GIDs",
                {"env": ENV_GROUPS, "value": raw},
            ) from exc
        if gid not in out:
            out.append(gid)
    return out


def _probe_snapshot() -> dict[str, object]:
    """The ``hal0 probe`` hardware.json payload, or ``{}``.

    Raw-JSON read (no pydantic) for the same reason
    :func:`hal0.providers._gpu._probed_gpu_group_gids` does it: a missing or
    stale snapshot must degrade to "no opinion", never raise.
    """
    try:
        from hal0.config import paths as _paths

        raw = json.loads(_paths.hardware_json().read_text())
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _probed_gpu(snapshot: Mapping[str, object]) -> dict[str, object]:
    gpus = snapshot.get("gpus")
    if isinstance(gpus, list):
        for entry in gpus:
            if isinstance(entry, dict) and (entry.get("vendor") or entry.get("name")):
                return entry
    gpu = snapshot.get("gpu")
    return gpu if isinstance(gpu, dict) else {}


def resolve_bench_devices(
    env: Mapping[str, str] | None = None,
    *,
    kfd_path: str | None = None,
    dri_dir: str | None = None,
) -> BenchDeviceSpec:
    """Resolve the GPU passthrough for a benchmark container.

    See the module docstring for the precedence chain. Never guesses a node
    name: when nothing resolves, the result is the CPU tier (empty device
    list), not ``/dev/dri/amdgpu``.

    Raises :class:`BenchDeviceError` only for *explicit* operator settings
    that cannot be honoured — discovery failures are not errors, they are the
    CPU tier.
    """
    env = os.environ if env is None else env
    kfd_path = kfd_path or env.get(ENV_KFD_PATH, "").strip() or "/dev/kfd"
    dri_dir = dri_dir or env.get(ENV_DRI_DIR, "").strip() or "/dev/dri"

    snapshot = _probe_snapshot()
    gpu = _probed_gpu(snapshot)
    label = str(gpu.get("name") or "").strip()
    vendor = str(gpu.get("vendor") or "").strip().lower()
    tier_override = env.get(ENV_TIER, "").strip().lower()
    if tier_override and tier_override not in (TIER_AMD, TIER_NVIDIA, TIER_CPU):
        raise BenchDeviceError(
            f"{ENV_TIER}={tier_override!r} is not a known tier (amd|nvidia|cpu)",
            {"env": ENV_TIER, "value": tier_override},
        )

    # 1. explicit operator override — strictly validated, wins over everything
    #    except an explicit CPU-tier pin (which means "run without a GPU").
    override = _override_devices(env, kfd_path, dri_dir)
    if tier_override == TIER_CPU:
        return BenchDeviceSpec(tier=TIER_CPU, source="env", gpu_label=label)
    if override is not None:
        devices, checked = override
        # Explicit node paths always mean the path-passthrough (AMD-shaped)
        # lane: CDI never takes filesystem paths.
        tier = TIER_AMD
        group_ids = _override_group_ids(env)
        if group_ids is None:
            from hal0.providers._gpu import resolve_gpu_group_ids

            group_ids = resolve_gpu_group_ids(devices)
        return BenchDeviceSpec(
            tier=tier,
            devices=tuple(devices),
            group_ids=tuple(group_ids),
            source="env",
            gpu_label=label,
            checked=tuple(checked),
        )

    # 2. NVIDIA tier — CDI names, no paths to stat, no --group-add.
    if tier_override == TIER_NVIDIA or (not tier_override and vendor == TIER_NVIDIA):
        from hal0.providers._gpu import nvidia_cdi_devices

        return BenchDeviceSpec(
            tier=TIER_NVIDIA,
            devices=tuple(nvidia_cdi_devices()),
            source="probe",
            gpu_label=label,
        )

    # 3. AMD / unknown-vendor tier — the SAME enumeration a slot container
    #    gets. resolve_gpu_device_paths degrades to the bare ``/dev/kfd`` +
    #    ``/dev/dri`` DIRECTORIES on a no-GPU box; the char-device filter drops
    #    those, which is what makes step 4 (CPU tier) reachable.
    from hal0.providers._gpu import resolve_gpu_device_paths, resolve_gpu_group_ids

    candidates = resolve_gpu_device_paths(kfd_path=kfd_path, dri_dir=dri_dir)
    devices = [p for p in candidates if _node_allowed(p, kfd_path, dri_dir) and _is_char_device(p)]

    # 4. CPU tier — nothing to pass through, and that is not an error.
    if not devices:
        if tier_override == TIER_AMD:
            raise BenchDeviceError(
                f"{ENV_TIER}=amd but no GPU device nodes were found — "
                f"checked: {kfd_path}, {dri_dir}/*",
                {"checked": [kfd_path, f"{dri_dir}/*"]},
            )
        return BenchDeviceSpec(
            tier=TIER_CPU,
            source="none",
            gpu_label=label,
            checked=(kfd_path, f"{dri_dir}/*"),
        )

    group_ids = _override_group_ids(env)
    if group_ids is None:
        group_ids = resolve_gpu_group_ids(devices)
    return BenchDeviceSpec(
        tier=TIER_AMD,
        devices=tuple(devices),
        group_ids=tuple(group_ids),
        source="discovery",
        gpu_label=label,
        checked=(kfd_path, f"{dri_dir}/*"),
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def render(spec: BenchDeviceSpec, fmt: str) -> str:
    """Render a resolved spec in one of the three output formats."""
    if fmt == "env":
        return spec.to_env_block()
    if fmt == "json":
        return json.dumps(spec.to_dict(), indent=2, sort_keys=True)
    if fmt == "flags":
        return "\n".join(spec.podman_flags())
    lines = [
        f"tier    : {spec.tier}",
        f"source  : {spec.source}",
        f"gpu     : {spec.gpu_label or '(unknown)'}",
        f"devices : {', '.join(spec.devices) or '(none — CPU tier)'}",
        f"groups  : {', '.join(str(g) for g in spec.group_ids) or '(none)'}",
        f"podman  : {' '.join(spec.podman_flags()) or '(no GPU passthrough)'}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """``python -m hal0.bench.devices [--format env|json|flags|text]``."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="hal0-bench-devices",
        description="Resolve the GPU device nodes a benchmark container should use.",
    )
    ap.add_argument(
        "--format",
        default="text",
        choices=("text", "env", "json", "flags"),
        help="output shape (default: text; the harness uses env)",
    )
    args = ap.parse_args(argv)
    try:
        spec = resolve_bench_devices()
    except BenchDeviceError as exc:
        print(f"hal0 bench devices: {exc}", flush=True, file=sys.stderr)
        return 2
    out = render(spec, args.format)
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
