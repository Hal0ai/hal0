# hal0 on Proxmox VE

A helper script that creates an unprivileged Ubuntu 26.04 LXC, forwards the
host's GPU into it, and runs the hal0 installer inside. It is written in the
[community-scripts](https://community-scripts.org) form and built on their
engine, so its menus, defaults files and `var_*` settings work the way they
do for every script on that site. See the
[Proxmox guide](../../docs/getting-started/proxmox.mdx) for the manual recipe
and the host-side driver prerequisites.

## Quick start

On a Proxmox VE host as `root`:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/ct/hal0.sh)"
```

The script asks for Default or Advanced install, creates the container,
forwards the GPU nodes it finds (`/dev/dri/renderD*`, `/dev/dri/card*`,
`/dev/kfd`) with the container's own `render` and `video` group ids, asks
you to confirm running hal0's installer, and prints the dashboard address
(`http://<container-ip>:8080`) when it finishes.

To update hal0 later, run `update` inside the container, or `hal0 update`.

## The files

| File | Runs | What it does |
| --- | --- | --- |
| `ct/hal0.sh` | on the Proxmox host | Declares the container (4 cores, 8 GB RAM, 40 GB disk, unprivileged, nesting, keyctl, fuse, mknod, GPU) and the update command |
| `install/hal0-install.sh` | inside the container | Confirms, then runs hal0's signed installer from `https://hal0.dev/install.sh` |
| `json/hal0.json` | nowhere | Metadata for the community-scripts website |
| `hal0-npu-passthrough` | on the Proxmox host | Forwards the NPU and lifts the memlock limit (below) |
| `hal0-fabric-clock`, `hal0-fabric-clock.service` | on the Proxmox host | GPU fabric-clock guard (below) |

## Settings

Container settings are the engine's standard `var_*` variables. Set them in
the environment, pick them in the Advanced install, or save them as defaults:

```bash
var_ctid=210 var_hostname=hal0 var_ram=16384 var_disk=100 \
var_container_storage=local-zfs var_net=192.0.2.50/24 var_gateway=192.0.2.1 \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/ct/hal0.sh)"
```

hal0's own settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `var_hal0_channel` | `stable` | Release channel: `stable`, `preview` or `nightly` |
| `var_hal0_memory` | `yes` | Install the memory engine (Hindsight) |
| `var_hal0_openwebui` | `yes` | Install Open WebUI, the chat on port 3001 |
| `var_hal0_agents` | `yes` | Install the bundled agents |
| `var_hal0_image_models` | `no` | Download the image model recommended for the box's memory (several GB) |
| `var_gpu` | `yes` | Forward the host's GPU. `no` builds a CPU-only container |
| `var_unprivileged` | `1` | `0` builds a privileged container (see below) |
| `var_external_installer` | *(ask)* | `yes` skips the confirmation before hal0's installer runs |

A fully unattended run sets `mode=default var_external_installer=yes`. On a
host with more than one storage pool that can hold containers, also set
`var_container_storage` (and `var_template_storage` if more than one pool can
hold templates): without them the engine opens its storage dialog, and with
no terminal to answer it the run ends with "User exited script" before
anything is created.

```bash
mode=default var_external_installer=yes \
var_container_storage=local-zfs var_template_storage=local \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/ct/hal0.sh)"
```

## Unprivileged by default

The container is unprivileged: its root is an ordinary unprivileged user on
the host. This is the shape [ADR-0050](../../docs/adr/0050-no-oci-appliance-unprivileged-lxc-is-the-wrapper.md)
names as hal0's one wrapper.

One thing an unprivileged container cannot do is repair a device node whose
group is wrong, because the node's ownership comes from the host. If
`hal0 doctor all` reports a GPU node with the wrong group, fix the `devN:`
entry in `/etc/pve/lxc/<CTID>.conf` on the host (`gid=` must be the
container's `render` group id, from `getent group render` inside it) and
restart the container.

`var_unprivileged=0` builds a privileged container, where hal0 can repair
the node itself. A privileged container's root is effectively host root, and
the engine goes further for this case: it allows the container every device
on the host (`lxc.cgroup2.devices.allow: a`) and clears its capability drop
list (`lxc.cap.drop:`). It writes no AppArmor line, so add
`lxc.apparmor.profile: unconfined` by hand if slots fail to start (see the
[Proxmox guide](../../docs/getting-started/proxmox.mdx)). A privileged build
by this script has not been tested with hal0.

## What the engine does besides building the container

- **It runs code from a moving branch.** `ct/hal0.sh` loads the
  community-scripts engine (about 35 files) from the `main` branch of
  `community-scripts/core`, as root on the host and inside the container,
  on the first run and again on every `update`. Nothing is pinned; this is
  the form every script on that site has.
- **It asks about diagnostics.** On a host's first run the engine asks
  whether to send diagnostics to the community-scripts API: script name,
  install method, success or failure, CPU core count, RAM and disk size, OS
  type and version, and Proxmox version. The answer is kept in
  `/usr/local/community-scripts/diagnostics`; answer no, or set
  `DIAGNOSTICS=no` in that file, to send nothing. hal0 itself sends nothing.
- **It prints two `curl: (22) ... 404` lines** before its banner. The engine
  looks for an ASCII header for `hal0` in its own repository, where there is
  none until the script is submitted there. The lines are harmless.

## NPU and memlock

The engine forwards GPU nodes only. For the AMD XDNA NPU (FastFlowLM and the
ggml-xdna runner), and for the raised memlock limit it needs, run this on the
Proxmox host after the container exists:

```bash
curl -fsSLO https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0-npu-passthrough
bash hal0-npu-passthrough --dry-run <CTID>   # shows what it would change
bash hal0-npu-passthrough <CTID>
```

It adds a `devN` entry for each `/dev/accel/accel*` node, owned by the
container's `render` group, adds `lxc.prlimit.memlock: unlimited`, and
restarts the container. Running it again changes nothing. Then, inside the
container:

```bash
flm validate
curl -fsSL https://hal0.dev/install.sh | HAL0_NONINTERACTIVE=1 bash
```

The second command runs the hal0 installer again over the existing install.
It keeps your configuration, and it is what records the NPU check in hal0's
hardware record: the installer is the only step that writes it, and at first
install the NPU was not there yet.

## What the script does not do

- **No host-side driver or GTT setup.** `amdgpu`, `amdxdna` and the GTT
  window are the host kernel's business; see the drivers guide. The script
  forwards the nodes that already exist.
- **No reverse proxy or TLS.** The dashboard listens on `0.0.0.0:8080` with
  no login by default. Keep the container on a trusted LAN.
- **No DNS repair.** The container inherits the host's DNS servers. If the
  host resolves through something a container cannot reach (a Tailscale or
  other VPN resolver), the build still succeeds, because the engine injects
  public DNS while it runs, and name resolution then fails after the first
  restart: updates stop and the memory engine cannot start. Set `var_ns`
  (for example `var_ns=1.1.1.1`) on such a host, or later run
  `pct set <CTID> -nameserver 1.1.1.1`.
- **No model store.** Models land on the container's disk. Bind-mount a
  host directory with `pct set <CTID> -mp0 ...` to keep them elsewhere.

## GPU fabric-clock guard (NPU + GPU together on Strix Halo)

On Strix Halo (gfx1151 iGPU + XDNA2 NPU), running an NPU slot (`flm`) and a
GPU slot (Vulkan/ROCm/HIP) at the same time can hang the box or corrupt NPU
results if the GPU's fabric clock (fclk) changes DPM level mid-flight
(upstream ROCm/TheRock#8709; see [ADR-0054](../../docs/adr/0054-gpu-fabric-clock-guard.md)).
The fix is to pin the fabric clock at its top DPM level — which can only be
done on the **Proxmox host**, not inside the LXC (the sysfs controls are
guest-readable but not guest-writable).

Install on the Proxmox host, as root, from this directory:

```bash
install -m 755 hal0-fabric-clock /usr/local/sbin/
install -m 644 hal0-fabric-clock.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hal0-fabric-clock.service
```

This holds the clock at boot and after every suspend/hibernate/hybrid-sleep
(a resume can hand it back to the driver). At boot the unit waits up to two
minutes for the GPU driver to expose its clock controls (`hold --wait 120`),
since it can start before amdgpu has finished probing. Check it any time with
`hal0-fabric-clock status`, or hold it for one session without installing the
unit with `hal0-fabric-clock hold`. Cost: about 1 W of idle power while held.
Inside the guest, `hal0 doctor all`'s `fabric_clock` row reads the same sysfs
files (read-only) and warns with these same commands if an NPU slot and a
GPU slot are both configured and the clock is not held; `[slots].fabric_clock_guard`
(default `refuse`) blocks a conflicting load until it is.

Undo: `systemctl disable --now hal0-fabric-clock.service`, then
`hal0-fabric-clock release` (or just reboot the host).


## Submitting to community-scripts

The three files are kept in the form their repository takes. Before a pull
request to `community-scripts/DevScripts`:

- change `_CS_DEFAULT_URL` in `ct/hal0.sh` to their repository, and the
  header comment of both scripts to their copyright and MIT licence lines;
- their project asks for 1,000 GitHub stars and a project at least six
  months old, so check the current count first;
- their rules allow a script that hands off to an upstream installer only
  behind `confirm_external_installer`, which this one uses.

## Test plan

Manual; needs a Proxmox VE host.

1. Default install on a GPU host: lands at the dashboard, `pct config <CTID>`
   shows `unprivileged: 1` and the `devN` entries, `hal0 doctor all` inside
   has no FAIL row, and a slot serves on ROCm and on Vulkan.
2. `hal0-npu-passthrough <CTID>`, then `flm validate` passes inside.
3. `var_gpu=no`: finishes as a CPU-only container.
4. `update` inside the container runs `hal0 update` and exits 0, twice.
5. `mode=default var_external_installer=yes` with `var_ctid` and, on a host
   with several pools, `var_container_storage` and `var_template_storage`
   set: no prompt.
6. `pct destroy <CTID>` leaves no leaked storage.
