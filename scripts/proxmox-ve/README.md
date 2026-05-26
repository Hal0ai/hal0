# hal0 on Proxmox VE

One-line helper that creates an unprivileged Debian 13 LXC and runs the
standard hal0 bootstrap inside it. Aimed at homelab Proxmox hosts that
just want to try hal0 — see the [Strix Halo passthrough recipe](../../docs/internal/)
for the privileged-LXC + iGPU/NPU setup that powers production hal0.

## Quick start

On a Proxmox VE host as `root`:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"
```

That will:

1. Pick the next free CTID and download the Debian 13 LXC template if missing
2. Print the resolved plan (CTID, hostname, cores, RAM, disk, network)
3. Wait 5s so you can ctrl-C if anything looks wrong
4. Create + start the LXC, install `curl ca-certificates tar jq python3 sudo`,
   install `cosign` (for release signature verification), and pipe
   `https://hal0.dev/install.sh` into `bash`
5. Print the dashboard URL (`http://<lxc-ip>:8080`)

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
| `OS_TYPE` / `OS_VERSION`  | `debian` / `13`                          | |
| `TEMPLATE_STORAGE`        | `local`                                  | where to keep the template |
| `UNPRIVILEGED`            | `1`                                      | `0` for privileged container |
| `PASSWORD`                | *(empty)*                                | blank = no root password; use `pct enter` |
| `SSH_AUTHORIZED_KEYS`     | *(empty)*                                | path to a public-key file on the pve host |
| `HAL0_CHANNEL`            | `stable`                                 | passes through to the hal0 bootstrap |
| `INSTALL_COSIGN`          | `1`                                      | `0` skips cosign install (bootstrap will then refuse to verify) |
| `COSIGN_VERSION`          | `v3.0.0`                                 | cosign 3.x required for keyless verify-blob |

Example — pinned CTID, static IP, no cosign:

```bash
CTID=210 \
HOSTNAME=hal0-test \
NET_CONFIG="name=eth0,bridge=vmbr0,ip=10.0.1.150/24,gw=10.0.1.1" \
INSTALL_COSIGN=0 \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"
```

## What the script does NOT do

- **No GPU/NPU passthrough.** This is a vanilla unprivileged LXC for
  CPU/Vulkan inference. For hardware-accelerated hal0 on AMD Strix Halo
  see the privileged-LXC + apparmor unconfined + `dev0`–`dev3` recipe
  in the main hal0 docs.
- **No reverse proxy / TLS.** The dashboard listens on `0.0.0.0:8080`
  with no auth (per ADR-0012). Treat the LXC as LAN-only.
- **No backup / migration.** Standard `pct` / Proxmox tooling applies.

## Test plan

Manual; needs a Proxmox VE host since `pct` only works there:

1. On a fresh pve, `bash -c "$(curl … hal0.sh)"` — should land at a hal0 dashboard.
2. `--advanced` — should open whiptail prompts and honour them.
3. `CTID=… STORAGE=… bash hal0.sh` non-interactive — should silently respect overrides.
4. `pct destroy <CTID>` — clean teardown leaves no leaked storage.
