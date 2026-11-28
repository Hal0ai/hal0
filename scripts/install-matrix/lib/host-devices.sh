#!/usr/bin/env bash
# Runs ON the Proxmox host: pve_run 'bash -s -- gpu+npu' < host-devices.sh
# Prints `dev <path>[,gid=<n>]` and `major <n>` lines for the nodes to forward
# into a container. Same discovery rules as scripts/proxmox-ve/hal0.sh
# (_add_gpu_device, discover_gpu_devices): render nodes, /dev/kfd and, with
# gpu+npu, /dev/accel/accel*; symlinks skipped; gid= only for a non-zero group.
# The duplication is deliberate; hal0.sh is not edited from here.
#
# HAL0_MATRIX_DEV_ROOT prefixes the paths read (test hook). When it is set the
# tests cannot create character devices without root, so regular files count.
set -euo pipefail

mode="${1:-}"
case "$mode" in
  gpu | gpu+npu) ;;
  *)
    echo "usage: host-devices.sh gpu|gpu+npu" >&2
    exit 2
    ;;
esac

root="${HAL0_MATRIX_DEV_ROOT:-}"
seen_majors=""

is_node() {
  [[ ! -L "$1" ]] || return 1
  if [[ -n "$root" ]]; then [[ -f "$1" ]]; else [[ -c "$1" ]]; fi
}

emit() {
  local path="$1" real gid hex maj
  real="${root}${path}"
  is_node "$real" || return 0
  gid="$(stat -c '%g' "$real" 2>/dev/null || true)"
  if [[ -n "$gid" && "$gid" != "0" ]]; then
    echo "dev ${path},gid=${gid}"
  else
    echo "dev ${path}"
  fi
  hex="$(stat -c '%t' "$real" 2>/dev/null || true)"
  if [[ -n "$hex" ]]; then
    maj="$((16#${hex}))"
    # Major 0 is not a real device; never emit a rule for it.
    if [[ "$maj" != "0" && " ${seen_majors} " != *" ${maj} "* ]]; then
      seen_majors+=" ${maj}"
      echo "major ${maj}"
    fi
  fi
  return 0
}

shopt -s nullglob
for node in "${root}"/dev/dri/renderD*; do
  emit "${node#"$root"}"
done
emit /dev/kfd
if [[ "$mode" == "gpu+npu" ]]; then
  for node in "${root}"/dev/accel/accel*; do
    emit "${node#"$root"}"
  done
fi
