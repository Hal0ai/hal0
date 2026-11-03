# ADR-0051: hal0 can install inside one rootless podman box, as an opt-in

## Status

**ACCEPTED (decided 2026-11-02, recorded 2026-11-03).** Ships as an opt-in in
v1.8.0 (`install.sh --box`). Bare metal stays the default. This record amends
decisions 1 and 2 of ADR-0050. Tracking issue: #3332.

## Context

ADR-0050 chose an unprivileged LXC system container as hal0's one wrapper and
ruled out an OCI image of the control plane. Its reasons were that hal0 is a
container orchestrator, and that an image would need systemd and nested podman,
or the host's container socket, which is root on the host.

Testing on 2026-11-02 and 2026-11-03 showed that the first reason does not rule
out a rootless podman container. A systemd container run by an ordinary host
user can hold the unchanged installer, nested podman for the slots, and the
GPU and NPU device nodes, and it needs no socket from the host.

Inference through the box on a Strix Halo machine, inside an unprivileged LXC,
with the same runner and model (qwen3.5-0.8b):

| Shape | ROCm | Vulkan |
|---|---|---|
| Rootful, as hal0 runs today | 199 tok/s | 225 tok/s |
| One rootless layer | 198 tok/s | 225 tok/s |
| Nested in the box | 198 tok/s | 225 tok/s |

On NVIDIA (CachyOS, podman 6) a model ran at 194 tok/s both in one container
and nested. The unchanged installer inside the box on the AMD machine finished
with doctor at 46 pass, 3 warn, 0 fail. `brain` served on ROCm at 67 tok/s and a
Vulkan `utility` slot at 179 tok/s, both reached from another LAN machine, and
`flm validate` passed as the hal0 user with a chat model on the NPU at 39.6
tok/s. `flm-embed` did not load ("container stopped") and was not traced.

On a VM the install, a reboot with nobody logged in, an in-place update from
stable to nightly, and the uninstall all passed, and the uninstall left nothing
behind. On a true bare-metal CachyOS desktop (CPU-only, podman 6, Docker
installed, ordinary user) the installer passed in 16 min 41 s with doctor at 45
pass, 4 warn, 0 fail, and a rebuild on the same volumes passed in 251 s with the
models and a marker file intact.

Containment was probed as box root and from a nested `--privileged` container.
Writes to `core_pattern`, `uevent_helper` and host sysctls, sysrq, `devtmpfs`,
`mknod`, `/dev/mem` and module loading were all denied, and host settings did
not change.

Network modes (nested TCP through pasta):

| Host | Podman | passt | Result |
|---|---|---|---|
| Ubuntu 24.04, kernel 6.8 | 4.9.3 | 2024-02 | Pass |
| Ubuntu 26.04, kernel 7.0 | 5.7 | 2026-01 | Pass |
| CachyOS, kernel 7.1 | 6.0.2 | 2026-07 | Pass |
| Ubuntu 26.04 LXC | 5.7 | 2026-01 | Pass |
| Ubuntu 24.04 LXC | 4.9.3 | 2024-02 | **Stalls** |
| Same 24.04 LXC, 2026-01 passt | 4.9.3 | 2026-01 | Pass |

What hal0 sees as the client address depends on the port handler. Default `-p`
on podman 4.9 hides a LAN client behind a fixed internal address, which breaks audit logs
and rate limits. Explicit pasta ports show the real LAN address on podman 4.9
and 5.7. With an admin key set, a LAN client got `401` on admin routes and hal0
logged its real address. Download throughput was about 110 MB/s in pasta, the
same as the host on a gigabit link.

## Decision

1. hal0 offers a second install shape, opt-in: `install.sh --box` runs the
   unchanged installer inside one rootless podman container, the box. Bare
   metal, Proxmox and Incus installs are unchanged and stay supported. This
   amends ADR-0050 decision 1 (no OCI image) for this one shape: the box is a
   systemd container built locally on the operator's host from a pinned base
   image, not a published image or compose file, and the slots inside it are
   still containers hal0 orchestrates with nested podman.
2. The box is an additional wrapper beside the unprivileged LXC. This amends
   ADR-0050 decision 2, which named the LXC as the one wrapper. The LXC guides
   remain.
3. The network is `pasta` with an explicit port list on every host:
   `--network pasta:-t,8080,-t,3001,-t,8188`. slirp4netns is removed in podman
   6. `-p` hides the client on podman 4.9. `-t auto` also reserves every
   loopback port on the host, which can collide with host services.
4. A self-test runs at creation. A container nested in the box makes an
   outbound TCP connection, and the launcher fails with a remedy if it stalls.
   This catches the 2024-02 passt case without a version table.
5. Tight flags, never `--privileged`: `--cap-add=all`, `label=disable`,
   `unmask=/proc/sys` and an explicit device list. The seccomp filter stays and
   host disks stay hidden. Nested netavark needs the writable `/proc/sys`, and
   kernel-wide sysctls stay denied.
6. Devices are reached by group mapping (`--gidmap +g<gid>:@<gid>`), never by
   making box root the node's owner. Slots and hal0 run as the hal0 user, which
   joins the group. Box root is not a member but can still open the node: it
   holds CAP_SETGID in the box's user namespace and the gid is mapped, so it
   can take the group. The device boundary is the box as a whole, that is the
   `hal0box` host user, and only nodes in a dedicated device group are mapped
   (a privileged host group such as `disk` or `shadow` is refused). A root-owned device node (gid 0) is skipped
   with a message that names the udev rule the host needs.
7. A narrow ID map is supported. Inside a 65536-wide LXC map the box uses a
   narrow range, and 65534 is mapped through so apt and PAM work.
8. Limits are fixed at creation. Rootless podman clamps `--ulimit memlock` to
   the hard limit of the shell running `podman create`, so the launcher raises
   it first and sets `LimitMEMLOCK=infinity` on `user@.service` and the box
   unit, and `DefaultLimitMEMLOCK=infinity` inside.
9. The box is a persistent user unit that runs `podman start` on a created
   container. A quadlet would generate `--rm --replace` and destroy the box on
   stop.
10. hal0 is pinned to uid and gid 970 in the image, so a rebuilt box keeps file
    ownership. Root linger is preset in the image, or Open WebUI's network
    namespace is stranded.
11. State lives on three volumes (`/etc/hal0`, `/var/lib/hal0`,
    `/var/lib/containers`) and the root filesystem is disposable.
    `unattended-upgrades` applies security fixes to the box's OS, and
    `hal0 box rebuild` replaces it for larger changes. A rebuild re-runs the
    installer from the tree saved at install time. After an in-box
    `hal0 update`, a rebuild therefore reinstalls the older version.
12. The launcher ships inside the signed tarball. `bootstrap.sh` verifies it as
    it does for any install, then runs `installer/box/box.sh` from the verified
    tree and keeps a root-owned copy of that tree. The existing trust chain is
    unchanged.
13. Host commands go through a thin shim at `/usr/local/bin/hal0`, which runs as
    root, raises the memlock limit and calls `podman exec` as the box user.
    Host-local HTTP is never relied on, because the client address it shows
    differs by podman version.
14. PAM inside the box does not depend on the host. Inside an LXC on the
    7.0-pve kernel the guest's enforcing `unix-chkpwd` AppArmor profile denied
    the box's binary by path. The image sets `common-account` to `pam_permit`,
    which is safe in a single-purpose box with no interactive accounts.
15. Nested AppArmor remediation stays guarded. In a fresh unprivileged LXC the
    guest's profiles block rootless podman outright (the #3234 family).
    `src/hal0/agents/containers_apparmor.py --nested-box` disables them only
    when `.ns_level > 0`, never on bare metal, and its list includes
    `usr.bin.pasta` and `usr.bin.passt`. The launcher records each profile the
    helper newly disabled, and uninstall re-enables exactly those, never a
    profile the operator had disabled before.
16. Honest signals inside the box. mDNS reports itself unavailable instead of
    "announced", because multicast does not cross pasta. Memory budgeting reads
    the host's memory, not the container marker's guess. When the host is
    itself a container (an LXC), the box would see the physical host's memory
    instead of the LXC's limit, so the launcher creates it with `--memory` set
    to the LXC's `MemTotal`, and hal0 reads that cgroup limit. `hal0 doctor` gains a
    `box` row, "Box boundary", that warns when a slot publishes on a
    non-loopback host or the API port moved off 8080, since only 8080, 3001 and
    8188 are forwarded.
17. The launcher opens the three ports on an active `ufw` or `firewalld` and
    removes exactly those rules on uninstall. The bare-metal installer has no
    firewall handling and is unchanged.

## What the box does not protect

- **Kernel bugs reachable from a user namespace.** The probes above cover
  permissions. A kernel flaw reachable from inside a user namespace is the same
  class of exposure as an unprivileged LXC, and the box does not remove it.
- **The credential chain inside the box.** `SECURITY.md` records that the hal0
  account is root-equivalent by design and that an agent made to run a command
  can reach every credential the box holds. A box contains that to the box. It
  does not close it. The agent user split (#3134) is still needed and keeps its
  v1.9.0 target. No guide may present the box as closing the chain.

## Consequences

- There is still no published image. The operator's host builds the box image
  locally from a digest-pinned base, and the build needs network access.
- `hal0.local` does not work for a box, and the forwarded port list is fixed at
  creation. Both are listed in the guide.
- The host keeps the kernel, `amdgpu`, `amdxdna`, firmware and the GTT kernel
  argument. Nothing in a box can move those.
- The launcher is a second install path to maintain. Phase 3 (below) removes the
  duplication only if the box becomes the default.

## Not tested

Stated here so the opt-in is not read as more than it is.

- A true bare-metal AMD host. AMD was tested inside an LXC, and plain hosts were
  tested in VMs and on NVIDIA and CPU-only.
- Converting an existing install, and a rebuild across versions on a GPU host.
- ComfyUI's `img` slot, halogen, and the Hermes terminal tool inside the box.
- Client identity on podman 6.
- WSL.

## Alternatives considered

- **Keep the LXC as the only wrapper (ADR-0050 as written).** It needs LXC
  tooling and, on Proxmox, a container the operator creates. The box needs one
  command on an ordinary host.
- **A published OCI image or compose file.** Still rejected for the reasons in
  ADR-0050. The box is built on the host, from the signed tarball.
- **slirp4netns or `-p`.** Rejected under decision 3.
- **`--privileged`.** Rejected under decision 5.
- **A quadlet.** Rejected under decision 9.

## Roadmap

| Phase | Delivers | Gate to next phase |
|---|---|---|
| 1, v1.8.0 | `--box` opt-in on bare metal and LXC. Bare metal stays the default. | Live matrix passes, including a true bare-metal AMD host if one is available |
| 2 | A converter for existing installs, fleet commands through the shim, and a Proxmox script that installs the box. | A copy of a real install converts and rolls back cleanly |
| 3 | The box becomes the default, then the bare-metal path and its docs are removed. | One release with the default flipped and no blockers |

## Relationship to other records

- **ADR-0050.** Decisions 1 and 2 are amended here. Its statement that a wrapper
  protects the host and not the credentials inside it still stands.
- **ADR-0002 and ADR-0024.** The agent user split they describe is still the
  isolation inside the box.

## References

- `docs/getting-started/box.mdx`, `installer/box/box.sh`,
  `installer/box/hal0-shim`, `installer/box/Containerfile.in`,
  `src/hal0/system/box.py`, `src/hal0/agents/containers_apparmor.py`.
- `SECURITY.md`, "Bundled agent trust boundary".
- #3332, #3134, #3234, #3327, #3328, #3333.
