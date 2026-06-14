# FLM Universal Install + Toolbox Hardening — Design

**Date:** 2026-06-14
**Status:** Draft (awaiting review)
**Author:** Claude (brainstorming session)
**Related:** [NPU Hardware-Usage Dashboard Pane](./2026-06-14-npu-hardware-usage-dashboard-design.md) (Spec B — depends on this)

---

## 1. Why this exists

FLM (FastFlowLM, the NPU LLM path) is **the trickiest, least portable part of the hal0 setup**. The recurring failure mode: a user on a non-Ubuntu distro (Debian, in the motivating case) cannot get FLM installed at all. This spec makes the FLM runtime **install easily and behave uniformly across distros**, and — as a rider — fixes the missing library that currently prevents NPU telemetry (`xrt-smi`) from running, which Spec B depends on.

This is the priority workstream. The dashboard pane (Spec B) is the occasion that surfaced it; broad, reliable FLM install is the higher-value outcome.

### The two Debian blockers (root-caused)

A user's friend could not install FLM on Debian. Research identified **two independent, real traps**, both Ubuntu-centricity bugs in the *bare-metal* path:

1. **The PPA trap.** FLM/Lemonade's Linux recipe says `add-apt-repository ppa:lemonade-team/stable` to get `libxrt-npu2` + `amdxdna-dkms`. That is a **Launchpad PPA — Ubuntu suites only** (Noble/Questing/Resolute). On Debian, apt looks for a `trixie` Release file that was never built → `E: The repository '… Release' does not have a Release file`. (https://launchpad.net/~lemonade-team/+archive/ubuntu/stable)
2. **The ffmpeg ABI trap.** FLM links `libav*` (ffmpeg) at runtime. The **Ubuntu 24.04** `.deb` depends on **ffmpeg-6 → `libavcodec60`**. **Debian 13 (trixie) ships ffmpeg-7 → `libavcodec61`** and has no `libavcodec60`, so the Ubuntu `.deb` is uninstallable: `fastflowlm depends on libavcodec60; however: Package libavcodec60 is not installed`. (https://packages.debian.org/trixie/amd64/ffmpeg)

FLM already partly solved (2) upstream by shipping **per-distro `.deb`s** including `fastflowlm_<ver>_debian13_amd64.deb` (ffmpeg-7). The friend almost certainly grabbed the Ubuntu-24.04 `.deb`. (1) remains: the surrounding driver tooling is still Ubuntu-only, so Debian users must avoid the PPA.

### The telemetry-blocker rider

The current published image `ghcr.io/hal0ai/hal0-toolbox-flm:v1` ships `xrt-smi` but it **cannot run**: it dies with `error while loading shared libraries: libboost_filesystem.so.1.83.0`. XRT's CMake links **both** `boost_filesystem` and `boost_program_options` (Xilinx/XRT `boostUtil.cmake`), but the image installs only `libboost-program-options1.83.0` (`packaging/toolbox/flm.Dockerfile:180`). XRT does not declare its boost dependency (Xilinx/XRT #6295), so it was missed. This one missing lib is why Spec B's column map can't read the NPU today.

---

## 2. Goals / Non-goals

### Goals
- **G1.** Container-first FLM install works identically on Ubuntu 24.04, Debian 13, Ubuntu 25.10/26.04, Arch, Fedora — the host requirement reduces to "recent kernel + firmware + `/dev/accel`."
- **G2.** `xrt-smi examine` runs cleanly inside the image (no `unwrapped` + `LD_LIBRARY_PATH` hack), enabling Spec B telemetry.
- **G3.** Bare-metal install (for users who refuse containers) picks the **distro-matched** `.deb` and **never** touches the Ubuntu-only PPA on non-Ubuntu.
- **G4.** A host **preflight/doctor** check that diagnoses the real prerequisites with cross-distro remediation, replacing trial-and-error.
- **G5.** Correct the stale kernel-version claim in docs/manifest.

### Non-goals
- Shipping the kernel `amdxdna` driver or NPU firmware in the container (they are host-side, by definition — containers share the host kernel).
- Supporting XDNA1 (Phoenix/Hawk Point) — FLM is XDNA2-only.
- Bundling AMD's login-gated prebuilt XRT debs (cannot be fetched anonymously in CI).
- Replacing FLM or changing the NPU serving architecture (FLM trio stays as-is).

---

## 3. Current state (sourced facts)

| Aspect | Current | Source |
|---|---|---|
| Image recipe | Retired from tree; last at `packaging/toolbox/flm.Dockerfile` @ `c71f04d9` | repo |
| Image pinned in | `manifest.json:18`, `src/hal0/providers/flm.py:46`, `src/hal0/capabilities/catalog.py:95` | repo |
| Base | `ubuntu:24.04` (boost 1.83, ffmpeg 6 era) | `flm.Dockerfile` |
| XRT | built from source, `amd/xdna-driver` `XDNA_REF=main` (unpinned) | `flm.Dockerfile:54-101` |
| FLM (image) | built from source, `FastFlowLM` `FLM_REF=main` | `flm.Dockerfile:103-167` |
| FLM (installer/bare-metal) | `.deb` v0.9.43 `ubuntu24.04`, SHA pinned | `installer/install.sh:755-760` |
| ffmpeg | 6 — hardcoded `libavformat60 libavcodec60 libswscale7` | `flm.Dockerfile:170-194` |
| Missing lib | `libboost-filesystem1.83.0` (only `-program-options` installed) → `xrt-smi` broken | `flm.Dockerfile:180` |
| `xrt-smi`/telemetry wiring | none anywhere in repo | repo grep |
| Kernel claim | `manifest.json:20` "kernel ≥ 6.11" — **wrong** | repo |

### Reference facts
- `amdxdna` mainlined in **Linux 6.14**; out-of-tree `amd/xdna-driver` DKMS targets **≥6.10**. (https://www.phoronix.com/news/Ryzen-AI-NPU6-Linux-6.14, https://docs.kernel.org/accel/amdxdna/)
- NPU firmware lives host-side in `linux-firmware` at `/usr/lib/firmware/amdnpu/`; FLM needs **≥1.1.0.0**.
- Current Strix-Halo pairing: **xdna-driver 2.21.75 ↔ XRT 2.21.75** (Ryzen AI Software 1.7.1). (https://ryzenai.docs.amd.com/en/latest/linux.html)
- FLM latest **v0.9.43** (2026-05-26); per-distro `.deb`s: `ubuntu24.04`, `ubuntu25.10`, `ubuntu26.04`, `debian13`. (https://github.com/FastFlowLM/FastFlowLM/releases)
- Lemonade dropped its standalone `.deb` in v10.0.1 → Ubuntu-only PPA; Debian = "build from source"/Docker. The official Lemonade Docker image is **CPU/ROCm-GPU only, no NPU** — NPU-in-a-container needs the FLM-specific image. (https://lemonade-server.ai/docs/guide/install/)
- Linux **7.1** adds NPU power + utilization telemetry via `DRM_IOCTL_AMDXDNA_GET_INFO` (`npu_tops_curr`, power) — relevant to Spec B's future, not this spec. CT105 is on 7.0.6.
- Host gotchas: never `amd_iommu=off` (NPU vanishes; use `amd_iommu=pt`); `memlock` unlimited; in-tree module supports an older firmware protocol than DKMS — match them.

---

## 4. Design

### 4.1 Thesis: container-first is the universal install

The host kernel driver + firmware *must* live host-side regardless of distro. Everything else (XRT userspace, ffmpeg, FLM, boost) can be sealed in the image. So the robust cross-distro story is: **run FLM in hal0's container; require only a sane host kernel + firmware.** hal0 already does this — this spec hardens it and makes the host side honest.

### 4.2 Toolbox image rebuild (`hal0-toolbox-flm`)

Restore/refresh `packaging/toolbox/flm.Dockerfile` and republish. Changes grouped by purpose:

**Group 1 — enable `xrt-smi` telemetry (unblocks Spec B):**
- Add runtime deps `libboost-filesystem1.83.0 libboost-system1.83.0` to stage-3 apt line.
- Bake XRT env so `xrt-smi` runs cleanly: `ENV XILINX_XRT=/opt/xilinx/xrt`, extend `PATH`/`LD_LIBRARY_PATH`, add `PYTHONPATH=/opt/xilinx/xrt/python` (the vars `setup.sh` exports).
- Build-time smoke: `RUN xrt-smi --version` (no NPU needed) so a missing-lib regression fails the build, not runtime.

**Group 2 — cross-distro robustness:**
- Keep **XRT source-build** as the default (only fully-anonymous path; AMD prebuilt debs are login-gated).
- For the **bare-metal `.deb` fallback only**, parameterize `ARG FLM_DEB_DISTRO=ubuntu24.04` and fetch the matching asset; never cross a `.deb` onto a mismatched base. The container itself can keep building FLM from source against the base's own `libav*-dev` so the ffmpeg soname is implicit (no hardcoded `libavcodec60`).
- Drop any reliance on the Lemonade PPA (the source-build already avoids it — keep it that way; document the prohibition for Debian).

**Group 3 — version hygiene:**
- Pin `ARG XDNA_REF=2.21.75` and matching XRT (no more `main`).
- Bump FLM pin to v0.9.43 (verify latest at build); keep the dual pin in sync (`installer/install.sh` `FLM_DEB_VERSION` + lemonade `backend_versions.json`).

### 4.3 Host preflight / `doctor`

Extend the installer/`hal0 doctor` path with an **NPU readiness check** that reports pass/fail + remediation per item:

| Check | Pass condition | Remediation (cross-distro) |
|---|---|---|
| Kernel `amdxdna` | `/dev/accel/accel0` exists, driver bound | kernel ≥6.14 (in-tree) or `amdxdna-dkms` (Ubuntu: PPA; **Debian: debian-backports**; Arch: AUR) |
| Firmware | `/usr/lib/firmware/amdnpu/` present, ≥1.1.0.0 | update `linux-firmware` |
| IOMMU | not `amd_iommu=off` | set `amd_iommu=pt` |
| memlock | unlimited (or container `--ulimit memlock=-1:-1`) | raise limit |
| Device passthrough | container can open `/dev/accel/accel0` | (hal0 ContainerSpec already maps it) |

**Debian-specific:** the doctor must explicitly *not* suggest the PPA on Debian, and point to debian-backports or a ≥7.0 kernel.

### 4.4 Doc corrections
- `manifest.json:20`: kernel ≥6.11 → **"≥6.14 in-tree, ≥6.10 via amdxdna-dkms"**.
- `src/hal0/providers/flm.py:16` comment: keep accurate to the rebuilt image (base, XRT/FLM versions).

---

## 5. What changes (file map)

- `packaging/toolbox/flm.Dockerfile` — restore + Group 1/2/3 edits.
- `manifest.json` — image tag bump (`:v2`), kernel-version correction.
- `src/hal0/providers/flm.py`, `src/hal0/capabilities/catalog.py` — image tag references.
- `installer/install.sh` — distro-matched `.deb` selection for bare-metal; FLM version pin.
- Host preflight: wherever `hal0 doctor`/preflight lives (TBD during planning) — add NPU readiness checks.
- Docs: Proxmox/LXC install guide + README NPU section — container-first guidance, no-PPA-on-Debian.

---

## 6. Risks / caveats
- **boost SONAME lock:** XRT links against the base's boost (Ubuntu 24.04 = 1.83.0, matches the new lib). A future base rebase (Ubuntu 26.04) moves boost → re-pin.
- **Per-distro fan-out:** base ⇄ FLM `.deb` ⇄ XRT boost soname must move together. ARG-driven base, default `ubuntu24.04`.
- **Unverified `.deb` Depends line:** the exact ffmpeg soname in each FLM `.deb` is inferred (per-distro asset names + Arch's `ffmpeg` dep + distro ABIs), not read from the control file. **Verify with `dpkg-deb -I fastflowlm_*_debian13_amd64.deb` before committing the rebuild.**
- **In-tree vs DKMS firmware protocol mismatch:** forcing 1.1 firmware on a stock kernel can make the NPU vanish — doctor should detect/warn.

---

## 7. Testing
- **Image build:** `xrt-smi --version` build smoke passes; image boots; `flm validate` OK.
- **Telemetry:** in a running FLM container, `xrt-smi examine -r aie-partitions -f JSON` returns valid JSON (loaded slot → non-empty HW Contexts; idle → empty) — the contract Spec B consumes.
- **Cross-distro host matrix:** validate container run on Ubuntu 24.04 (CT105 baseline) and at least one Debian 13 host; confirm `/dev/accel0` passthrough + inference.
- **Doctor:** unit-test each check's pass/fail + remediation string; manual run on a deliberately-misconfigured host (iommu off, old firmware).
- **Bare-metal fallback:** dry-run the distro-matched `.deb` selection logic per distro.

---

## 8. Open questions (resolve in planning)
- Where exactly does host preflight live today, and is `hal0 doctor` the right home for the NPU checks?
- Source-build FLM in-image vs distro-matched `.deb` in-image — pick one as the container default (lean: source-build for implicit ffmpeg soname).
- Image tag scheme (`:v2` vs date tag) and republish/CI flow for the toolbox image now that the recipe was retired from the tree.
