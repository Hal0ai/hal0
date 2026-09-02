# hal0 on Proxmox VE

One-line helper that creates a privileged Ubuntu 26.04 LXC, forwards
whatever GPU/NPU device nodes the host has, and runs the standard hal0
bootstrap inside it. This is the production hal0 container shape — see the
[Proxmox guide](../../docs/getting-started/proxmox.mdx) for the manual
recipe it automates and for the host-side driver/GTT prerequisites.

## Quick start

On a Proxmox VE host as `root`:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"
```

That will:

1. Probe the host for `/dev/dri/renderD*`, `/dev/dri/amdgpu`, `/dev/kfd` and
   `/dev/accel/accel*`
2. Pick the next free CTID and download the Ubuntu 26.04 LXC template if missing
3. Print the resolved plan (CTID, hostname, cores, RAM, disk, network, devices)
4. Wait 5s so you can ctrl-C if anything looks wrong
5. Create the LXC privileged, with `nesting/keyctl/fuse/mknod`, every probed
   device as `devN`, the matching `lxc.cgroup2.devices.allow` rules,
   `lxc.prlimit.memlock: unlimited`, and `lxc.apparmor.profile: unconfined`
6. Start it, wait for network, and verify the forwarded devices are actually
   visible inside the container before installing anything
7. Install `curl` if the template lacks it (the one package that cannot be
   fetched by the thing it fetches), then pipe `https://hal0.dev/install.sh`
   into `bash`. Everything else the guest installs itself — bootstrap pulls
   tar/jq/python3 and a pinned `cosign`, the installer adds podman and the
   Python venv stdlib
8. Print the dashboard URL (`http://<lxc-ip>:8080`)

## Interactive prompts

Pass `--advanced` to open whiptail dialogs for every parameter:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)" -- --advanced
```

## Env-var overrides

Every parameter has an env-var override. Useful when running headless
or templating multiple hosts.

| Variable                  | Default                                  | Notes |
| ------------------------- | ---------------------------------------- | ----- |
| `CTID`                    | `pvesh get /cluster/nextid`              | LXC ID |
| `HOSTNAME`                | `hal0`                                   | |
| `CORES`                   | `4`                                      | |
| `RAM_MB`                  | `8192`                                   | |
| `SWAP_MB`                 | `1024`                                   | |
| `DISK_GB`                 | `20`                                     | |
| `STORAGE`                 | `local-lvm` if present, else first rootdir pool | |
| `BRIDGE`                  | first `vmbr*` in `/etc/network/interfaces` | |
| `NET_CONFIG`              | `name=eth0,bridge=$BRIDGE,ip=dhcp`       | full `--net0` arg if you need static IP / VLAN |
| `IP_CIDR` / `GATEWAY`     | *(empty → DHCP)*                         | static IPv4, e.g. `10.0.1.90/24` + `10.0.1.1` |
| `NAMESERVER`              | *(pve default)*                          | space-separated resolvers |
| `SEARCHDOMAIN`            | *(pve default)*                          | |
| `TIMEZONE`                | *(pve default)*                          | |
| `TAGS`                    | `hal0`                                   | semicolon-separated pve tags |
| `ONBOOT`                  | `1`                                      | start with the host |
| `STARTUP`                 | *(empty)*                                | pve startup order, e.g. `order=3` |
| `MOUNTS`                  | *(empty)*                                | `;`-separated `--mpN` values, e.g. `/mnt/ai-models,mp=/mnt/ai-models,backup=0` |
| `OS_TYPE` / `OS_VERSION`  | `ubuntu` / `26.04`                       | |
| `TEMPLATE_STORAGE`        | `local`                                  | where to keep the template |
| `UNPRIVILEGED`            | `0`                                      | `1` for the narrower unprivileged container |
| `FEATURES`                | `nesting=1,keyctl=1,fuse=1,mknod=1`      | podman-in-LXC needs these |
| `GPU_PASSTHROUGH`         | `auto`                                   | `1` = require a device, `0` = CPU-only container |
| `PASSWORD`                | *(empty)*                                | blank = no root password; use `pct enter` |
| `SSH_AUTHORIZED_KEYS`     | *(empty)*                                | path to a public-key file on the pve host |
| `HAL0_CHANNEL`            | `stable`                                 | passes through to the hal0 bootstrap |
| `RUN_BOOTSTRAP`           | `1`                                      | `0` creates the container and stops there |

Example — pinned CTID, static IP, model store bind-mounted:

```bash
CTID=210 \
HOSTNAME=hal0-test \
IP_CIDR=192.0.2.50/24 GATEWAY=192.0.2.1 \
MOUNTS='/mnt/ai-models,mp=/mnt/ai-models,backup=0' \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"
```

Example — the old vanilla shape (unprivileged, no passthrough):

```bash
UNPRIVILEGED=1 GPU_PASSTHROUGH=0 OS_TYPE=debian OS_VERSION=13 \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"
```

## What the script does NOT do

- **No host-side driver or GTT setup.** `amdgpu`/`amdxdna` and the GTT
  window are the host kernel's business; see the drivers guide. The script
  forwards the nodes that already exist and fails loudly when they don't
  appear inside the container.
- **No reverse proxy / TLS.** The dashboard listens on `0.0.0.0:8080`
  with no auth (per ADR-0012). Treat the LXC as LAN-only.
- **No backup / migration.** Standard `pct` / Proxmox tooling applies.

## Test plan

Manual; needs a Proxmox VE host since `pct` only works there:

1. On a fresh pve, `bash -c "$(curl … hal0.sh)"` — should land at a hal0 dashboard.
2. On a GPU host, `pct config <CTID>` should show `dev0…devN` plus the
   cgroup/memlock/apparmor lines, and `hal0 doctor` inside the CT should see
   the GPU (and the NPU where present).
3. On a GPU-less host, the run should warn once and finish CPU-only.
4. `--advanced` — should open whiptail prompts and honour them.
5. `CTID=… STORAGE=… bash hal0.sh` non-interactive — should silently respect overrides.
6. Re-running `apply_raw_config` against an existing CTID must not duplicate
   any `lxc.*` line.
7. `pct destroy <CTID>` — clean teardown leaves no leaked storage.
