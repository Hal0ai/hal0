---
title: Hardware overview
description: The four hardware tiers hal0 targets (Strix Halo, AMD discrete, NVIDIA, CPU-only) and how to pick.
sidebar:
  order: 1
---

hal0 runs on four classes of homelab hardware. The
[one-line installer](/docs/getting-started/install/) probes the box,
picks a backend, and writes `/etc/hal0/hardware.json`. This page is
for figuring out which tier you're on before you commit, and which
deployment shape (LXC, bare metal, VM with passthrough) is sane for it.

## The tiers

| Tier | Hardware | Status | Path |
|---|---|---|---|
| **First-class** | AMD Strix Halo (Ryzen AI Max+ 395, Radeon 8060S, XDNA NPU, 64–128 GB unified) | Reference platform | Vulkan llama.cpp on the iGPU, FLM on the NPU (NPU pending) |
| **Supported** | AMD discrete (RX 7900 XT/XTX, Radeon Pro) | Vulkan today, ROCm queued | Vulkan llama.cpp; ROCm toolbox pending |
| **Supported** | NVIDIA discrete (RTX 3080 / 4080 / 4090 / 5090) | Vulkan today, CUDA queued | Vulkan llama.cpp; CUDA toolbox pending |
| **Fallback** | CPU-only x86_64 (no GPU) | CI smoke target | Vulkan-CPU (lavapipe) |

Linux + systemd is required for every tier.

## Deployment shapes

hal0 is happy in any of these. Pick what fits your homelab:

- **Privileged LXC with device passthrough.** The canonical
  small-footprint install on a Proxmox node. AppArmor unconfined,
  `dev0`–`dev3` + cgroup allows for the render nodes and the XDNA
  accelerator. hal0 sits next to your other tenants, sees the GPU/NPU
  passthrough, and shows up in the dashboard's memory bar as one
  segment of the host's unified pool. CPU-only deployments can stay
  unprivileged.
- **Bare-metal Linux.** Fine. No virtualisation overhead, no carveout
  to negotiate. The dashboard's "PVE host" segment falls back to the
  LXC-only view.
- **VM with PCIe passthrough.** Works for discrete GPUs. Plan on CPU
  pinning and an IOMMU group that doesn't bring half your PCH along
  with the card.

The Strix Halo iGPU + XDNA NPU passthrough recipe is privileged LXC +
AppArmor unconfined + `dev0`–`dev3` + the cgroup allows for `/dev/kfd`,
`/dev/dri/*`, `/dev/accel/*`. AMD discrete + ROCm in an LXC needs the
same `/dev/kfd` + `/dev/dri/*` device entries; NVIDIA wants the nvidia
container toolkit on the host plus the matching device cgroup allows.

## How to pick

- **You're shopping for a dedicated homelab box.** Get a 128 GB
  [Strix Halo](/docs/hardware/strix-halo/) machine. Unified memory is
  the whole point: you run the big models that discrete cards can't,
  in a quiet SFF chassis that idles low enough to live on 24/7.
- **You already have a high-end NVIDIA card.** Use it. The
  [NVIDIA](/docs/hardware/nvidia/) page covers what works today (Vulkan)
  and what's queued (CUDA toolbox). A 4090 or 5090 outpaces the iGPU
  on small chat models; you trade headroom for throughput.
- **You already have an AMD discrete card.** Same story.
  [AMD discrete](/docs/hardware/amd-discrete/): Vulkan today, ROCm
  toolbox queued.
- **You have a CPU-only box and want to try hal0.**
  [CPU-only](/docs/hardware/cpu-only/) walks through the fallback path.
  Smoke-test it; don't expect to chat through it all day.

## Where the perf numbers come from

Every measured number on this site comes from the **Strix Halo
reference deployment** (Ryzen AI Max+ 395 + Vulkan llama.cpp). See
the [Strix Halo page](/docs/hardware/strix-halo/#measured-performance)
for the verbatim table. Numbers from other hardware tiers will be
added as they're measured at publish quality.
