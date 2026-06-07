# hal0 fresh-install test template (CT 200)

The golden Proxmox LXC template `fresh-test-ct.sh` clones to run a clean-box
install → smoke → uninstall cycle (#407). This directory holds the artifacts
to **rebuild** the template; the harness itself is `scripts/fresh-test-ct.sh`.

## What the template is

A vanilla **Ubuntu 24.04** LXC (VMID **200**, `hal0-test-template`) with:

- the **CT-105-equivalent Strix Halo passthrough** (privileged; `dev0`–`dev3`
  for renderD128/amdgpu/kfd/accel0; `lxc.cgroup2.devices.allow` for
  226/234/261/10:200; `lxc.prlimit.memlock: unlimited`; `lxc.apparmor.profile:
  unconfined`) so the installer's hardware probe sees the iGPU/NPU
- the `halo` operator user (NOPASSWD sudo) + the provisioning SSH key
- `curl ca-certificates jq git rsync htop openssh-server sudo`
- **apparmor purged** — docker-in-unconfined-LXC can't load `docker-default`
  otherwise (see memory `dreamserver_ct108_eval`)
- `hal0-test-ready.service` — a per-boot oneshot that writes
  `/tmp/hal0-test-ready` so the harness knows a clone is up
- LAN nameserver (`192.0.2.1 192.0.2.2`) — the pve host's resolv.conf points at
  a Tailscale resolver (`100.100.100.100`) that does **not** work inside the CT

No `mp0` model mount: bind mounts can't be templated, and the install/uninstall
smoke doesn't warm models. `fresh-test-ct.sh --with-models` adds it per-clone.

## Rebuild from scratch

```bash
# on the Proxmox host (pve), as root:
pct create 200 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname hal0-test-template --cores 4 --memory 8192 --swap 2048 \
  --rootfs local-zfs:32 --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --ostype ubuntu --features nesting=1,fuse=1,keyctl=1,mknod=1 \
  --unprivileged 0 --onboot 0
# append the passthrough block (dev0-3 + cgroup allows + memlock + apparmor)
# — copy the lines from /etc/pve/lxc/105.conf (minus the mp* mounts)
pct set 200 --nameserver "192.0.2.1 192.0.2.2" --searchdomain thinmint.dev
pct start 200
# provision (this dir): pct push provision.sh + hal0-test-ready.service + a
# pubkey file, then `pct exec 200 -- bash /root/provision.sh`
pct stop 200          # if it hangs on /dev/kfd: pkill -9 -f 'lxc-start -F -n 200'
pct template 200
```

## Refreshing the golden image

Periodically rebuild so clones start from a current base:

1. Clone to a scratch vmid, `apt update && apt -y upgrade`, re-run `provision.sh`.
2. Re-`pct template`. Or just rebuild from scratch (above) — it's ~2 minutes.

Refresh the base LXC template image with `pveam update && pveam download local
ubuntu-24.04-standard_<new>_amd64.tar.zst` when a newer point release lands.

## CT-105 contention caveat

CT 200 clones share the host kernel + iGPU/NPU cgroup rights with the live
CT 105 (`hal0`). The install/uninstall smoke does **not** warm a model, so there
is no GPU contention. If you add model-loading steps (`--with-models` + a slot
load), run them when CT 105 is idle to avoid a brief device-contention window.
