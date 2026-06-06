#!/usr/bin/env bash
# Provision the hal0 fresh-install test template (#407).
#
# Run ONCE inside a fresh Ubuntu 24.04 LXC (CT 200) before `pct template 200`.
# Idempotent. Bakes everything a `fresh-test-ct.sh` clone needs:
#   - the `halo` operator user (NOPASSWD sudo) + the provisioning SSH key
#   - the packages install.sh's bootstrap assumes (curl/ca-certs/jq/git/rsync)
#   - sshd enabled
#   - apparmor PURGED so docker-in-unconfined-LXC works during install
#     (see memory dreamserver_ct108_eval — docker-default profile can't load
#     in an apparmor-unconfined LXC; removing apparmor makes containers run
#     unconfined with no per-container --security-opt)
#   - the per-boot readiness oneshot (hal0-test-ready.service)
#
# Usage (from the Proxmox host):
#   pct push 200 ./provision.sh /root/provision.sh
#   pct push 200 ./hal0-test-ready.service /root/hal0-test-ready.service
#   pct push 200 <pubkey-file> /root/hal0-test.pub
#   pct exec 200 -- bash /root/provision.sh
set -euo pipefail

PUB_FILE="${HAL0_TEST_PUBKEY_FILE:-/root/hal0-test.pub}"
UNIT_SRC="${HAL0_TEST_UNIT_FILE:-/root/hal0-test-ready.service}"
export DEBIAN_FRONTEND=noninteractive

echo "[provision] apt update + base packages"
apt-get update -qq
# Purge apparmor BEFORE docker is ever installed by install.sh — required for
# docker to run containers inside an apparmor-unconfined LXC.
apt-get purge -y -qq apparmor 2>/dev/null || true
# python3-venv is a hard install.sh prerequisite (preflight_venv, #497) — the
# Ubuntu base image ships python3 without ensurepip. Bake it in so the clone
# mirrors a host where the operator has met the documented prerequisites.
apt-get install -y -qq curl ca-certificates jq git rsync htop openssh-server sudo \
  python3-venv python3-pip

echo "[provision] halo user + NOPASSWD sudo + ssh key"
id halo >/dev/null 2>&1 || useradd -m -s /bin/bash halo
printf 'halo ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/halo-nopasswd
chmod 440 /etc/sudoers.d/halo-nopasswd
if [[ -f "${PUB_FILE}" ]]; then
    install -d -m 700 -o halo -g halo /home/halo/.ssh
    install -m 600 -o halo -g halo "${PUB_FILE}" /home/halo/.ssh/authorized_keys
    install -d -m 700 /root/.ssh
    cat "${PUB_FILE}" > /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
else
    echo "[provision] WARNING: ${PUB_FILE} not found — no SSH key installed"
fi

echo "[provision] readiness oneshot + sshd enable"
if [[ -f "${UNIT_SRC}" ]]; then
    install -m 644 "${UNIT_SRC}" /etc/systemd/system/hal0-test-ready.service
fi
systemctl enable ssh.service hal0-test-ready.service >/dev/null 2>&1 || true

# Clean apt only. Do NOT empty /etc/machine-id: in an LXC that makes
# systemd-machine-id-commit.service hang on the next boot (it blocks remounting
# /etc/machine-id), which leaves an unkillable D-state process pinning the ZFS
# subvol so the clone can't be destroyed. Clones get unique MACs/IPs from DHCP
# regardless, so a shared machine-id is harmless for throwaway test boxes.
apt-get clean

echo "[provision] done — stop the CT and run: pct template <vmid>"
