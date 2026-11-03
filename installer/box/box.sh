#!/usr/bin/env bash
# installer/box/box.sh
#
# Purpose: install and run hal0 inside one rootless podman "box" (#3332).
#          Opt-in through `bootstrap.sh --box`, which verifies the signed
#          release tarball exactly as for a bare-metal install and then runs
#            box.sh install <unpacked-tree> [install.sh args...]
#          from that verified tree.
#
# Usage:   box.sh install <tree> [install.sh args...]
#          box.sh preflight
#
# The box is the container `hal0-box`, owned by the dedicated host user
# `hal0box`, built from the image `localhost/hal0-box:local`. Design,
# decisions and test evidence: issue #3332.
#
# Fixed decisions this file enforces (do not loosen without the design doc):
#   - Network is exactly `pasta:-t,8080,-t,3001,-t,8188`. Never -p (hides the
#     client IP on podman 4.9), never slirp4netns (removed in podman 6), never
#     -t auto (reserves every host loopback port).
#   - Never --privileged. Capabilities, label, /proc/sys unmask and an
#     explicit device list instead, so seccomp stays and host disks stay hidden.
#   - Device access is by group mapping (--gidmap +g<gid>:@<gid>), never by
#     making box root the device owner.
#
# Test seams (unit tests cannot touch the real host):
#   HAL0_BOX_HOST_ROOT          prefix for every host path written or read
#                               (/etc, /home, /run/user, /var, /usr/local,
#                               /sys/fs/cgroup). Empty in production.
#   HAL0_BOX_DEV_ROOT           where device nodes are discovered (default /dev).
#                               When set, plain files stand in for char devices.
#   HAL0_BOX_UID_MAP_FILE       stands in for /proc/self/uid_map.
#   HAL0_BOX_DRY_RUN=1          `install` prints the podman create argv and stops.
#   HAL0_BOX_ALLOW_LOW_MEMLOCK=1  do not fail when the memlock hard limit
#                               cannot be raised (an unprivileged test).

set -euo pipefail

BOX_USER=hal0box
BOX_NAME=hal0-box
BOX_IMAGE=localhost/hal0-box:local
BOX_PORTS=(8080 3001 8188)
BOX_NETWORK="pasta:-t,8080,-t,3001,-t,8188"
# hal0's uid and gid inside the box, pinned by Containerfile.in.
BOX_HAL0_ID=970
# Base image, pinned by the multi-arch index digest of ubuntu:26.04.
# Bump deliberately: `skopeo inspect --format '{{.Digest}}' docker://docker.io/library/ubuntu:26.04`.
BOX_BASE_IMAGE="docker.io/library/ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c"

BOX_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_ROOT="${HAL0_BOX_HOST_ROOT:-}"
DEV_ROOT="${HAL0_BOX_DEV_ROOT:-/dev}"
UID_MAP_FILE="${HAL0_BOX_UID_MAP_FILE:-/proc/self/uid_map}"
SUBUID_FILE="${HOST_ROOT}/etc/subuid"
SUBGID_FILE="${HOST_ROOT}/etc/subgid"
CGROUP_DIR="${HOST_ROOT}/sys/fs/cgroup"
SHIM_PATH="${HOST_ROOT}/usr/local/bin/hal0"
SHIM_MARKER="hal0-box-shim"

log() { printf '[hal0-box %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { log "WARNING: $*"; }
die() { log "ERROR: $*"; exit 1; }

box_require_root() {
    [[ "$(id -u)" == 0 ]] || die "box.sh must run as root (it creates the ${BOX_USER} user and maps device groups)"
}

# A char device in production. Under the HAL0_BOX_DEV_ROOT seam a plain file
# stands in, because an unprivileged test cannot mknod.
_box_is_dev() {
    [[ -c "$1" ]] && return 0
    [[ -n "${HAL0_BOX_DEV_ROOT:-}" && -e "$1" ]]
}

# ── devices ───────────────────────────────────────────────────────────────
# Fills:
#   BOX_DEVICE_ARGS   --device <host>:<box> pairs for GPU and NPU nodes
#   BOX_DEVICE_GIDS   unique non-zero host gids of those nodes
#   BOX_DEVICE_GROUPS "<gid>:<name>" specs for the image build
# Returns 1 (after logging why) when a node's gid collides with hal0's pinned
# 970: mapping it would make the device group and hal0's own group one gid.
box_discover_devices() {
    BOX_DEVICE_ARGS=()
    BOX_DEVICE_GIDS=()
    BOX_DEVICE_GROUPS=()
    local node rel gid name bad=0
    shopt -s nullglob
    for node in "${DEV_ROOT}/kfd" "${DEV_ROOT}"/dri/renderD* "${DEV_ROOT}"/accel/accel*; do
        _box_is_dev "${node}" || continue
        rel="${node#"${DEV_ROOT}"/}"
        gid="$(stat -c %g "${node}")"
        if [[ "${gid}" == 0 ]]; then
            warn "skipping /dev/${rel}: it is owned by root (gid 0), so the box cannot reach it and hal0 would fall back to CPU. Add a host udev rule giving it a device group, for example: KERNEL==\"kfd\", GROUP=\"render\", MODE=\"0660\" in /etc/udev/rules.d/70-hal0-kfd.rules, then: udevadm control --reload && udevadm trigger"
            continue
        fi
        if [[ "${gid}" == "${BOX_HAL0_ID}" ]]; then
            log "ERROR: /dev/${rel} is owned by gid ${gid}, which is the gid hal0 is pinned to inside the box. Change the device group on the host (a udev rule) to a different gid and re-run."
            bad=1
            continue
        fi
        BOX_DEVICE_ARGS+=(--device "${node}:/dev/${rel}")
        if [[ " ${BOX_DEVICE_GIDS[*]:-} " != *" ${gid} "* ]]; then
            BOX_DEVICE_GIDS+=("${gid}")
            name="$(getent group "${gid}" 2>/dev/null | cut -d: -f1)" || name=""
            [[ "${name}" =~ ^[a-z_][a-z0-9_-]*$ ]] || name="hal0dev${gid}"
            BOX_DEVICE_GROUPS+=("${gid}:${name}")
        fi
    done
    shopt -u nullglob
    return "${bad}"
}

# ── ports ─────────────────────────────────────────────────────────────────
# Prints each busy published port. A listener owned by pasta while the box
# user exists is our own running box (a re-run), not a conflict.
box_busy_ports() {
    local p listeners
    command -v ss >/dev/null 2>&1 || { warn "ss not found, cannot check ports ${BOX_PORTS[*]}"; return 0; }
    listeners="$(ss -Hltnp 2>/dev/null || true)"
    for p in "${BOX_PORTS[@]}"; do
        awk -v p="${p}" -v own="$(id -u "${BOX_USER}" >/dev/null 2>&1 && echo 1)" '
            $4 ~ (":" p "$") { if (own == "1" && $0 ~ /\("pasta/) next; found = 1 }
            END { exit !found }' <<<"${listeners}" && printf '%s\n' "${p}"
    done
    return 0
}

# ── preflight ─────────────────────────────────────────────────────────────
box_preflight() {
    local fail=0 busy
    [[ -f "${CGROUP_DIR}/cgroup.controllers" ]] \
        || { log "ERROR: cgroup v2 (unified hierarchy) is required for rootless podman with systemd; this host mounts cgroup v1 at /sys/fs/cgroup."; fail=1; }
    _box_is_dev "${DEV_ROOT}/net/tun" \
        || { log "ERROR: /dev/net/tun is missing; pasta needs it. On a Proxmox LXC add it on the host with: pct set <id> --dev<N> /dev/net/tun, then restart the container."; fail=1; }
    _box_is_dev "${DEV_ROOT}/fuse" \
        || { log "ERROR: /dev/fuse is missing; the box's container storage needs it. On a Proxmox LXC add it on the host with: pct set <id> --dev<N> /dev/fuse (or features: fuse=1), then restart the container."; fail=1; }
    if [[ -e "${SHIM_PATH}" ]] && ! grep -q "${SHIM_MARKER}" "${SHIM_PATH}" 2>/dev/null; then
        log "ERROR: /usr/local/bin/hal0 already exists and is not the box shim, so hal0 looks installed on this host already. Remove that install first (sudo bash installer/uninstall.sh --purge from its release tree), or install without --box."
        fail=1
    fi
    box_discover_devices || fail=1
    if [[ "${#BOX_DEVICE_ARGS[@]}" -eq 0 ]]; then
        log "no GPU or NPU device nodes found; the box will be CPU-only"
    else
        log "devices: ${BOX_DEVICE_ARGS[*]}"
    fi
    busy="$(box_busy_ports)"
    if [[ -n "${busy}" ]]; then
        log "ERROR: port(s) $(tr '\n' ' ' <<<"${busy}")already in use on this host. The box publishes ${BOX_PORTS[*]}; stop whatever listens there (ss -ltnp) and re-run."
        fail=1
    fi
    [[ "${fail}" == 0 ]] || return 1
    log "preflight passed"
}

# ── sub-IDs ───────────────────────────────────────────────────────────────
# Width of this machine's own uid map. A host that is itself a container
# (unprivileged LXC) has a narrow map, typically 65536.
box_uid_map_width() {
    awk 'NR==1{print $3}' "${UID_MAP_FILE}" 2>/dev/null || echo 4294967295
}

box_is_narrow() {
    local w
    w="$(box_uid_map_width)"
    [[ -n "${w}" && "${w}" -lt 1000000 ]]
}

# First free start after every range any other user already holds, so an
# existing allocation is never overlapped.
_box_next_start() {
    cat "${SUBUID_FILE}" "${SUBGID_FILE}" 2>/dev/null \
        | awk -F: -v u="${BOX_USER}" -v m=100000 '
            $1 != u && $3 + 0 > 1 && $2 + $3 > m { m = $2 + $3 }
            END { print m }'
}

# Rewrite the box user's lines in one sub-ID file to exactly the wanted set.
# A wide range the user already holds is kept, so a re-run never remaps
# existing box files.
_box_write_subids() {
    local file="$1"; shift
    local tmp="${file}.hal0-box.tmp"
    [[ -f "${file}" ]] || : > "${file}"
    { grep -v "^${BOX_USER}:" "${file}" || true; printf '%s\n' "$@"; } > "${tmp}"
    cat "${tmp}" > "${file}"
    rm -f "${tmp}"
}

# Writes /etc/subuid and /etc/subgid for the box user and fills BOX_IDMAP_ARGS.
#   narrow map: 20000:40000 plus 65534:1, with 65534 mapped straight through
#               (without it apt and PAM fail inside the box)
#   wide map:   one 65536 range, reusing the user's existing one if present
# Each device gid gets a <user>:<gid>:1 line in /etc/subgid.
box_subids() {
    local uid_lines=() gid_lines=() existing start gid
    BOX_IDMAP_ARGS=()
    if box_is_narrow; then
        uid_lines=("${BOX_USER}:20000:40000" "${BOX_USER}:65534:1")
        gid_lines=("${uid_lines[@]}")
        BOX_IDMAP_ARGS=(--uidmap "+u65534:@65534" --gidmap "+g65534:@65534")
        log "narrow ID map ($(box_uid_map_width)): the box gets sub-IDs 20000:40000 and 65534:1"
    else
        existing="$(awk -F: -v u="${BOX_USER}" '$1 == u && $3 >= 65536 { print; exit }' "${SUBUID_FILE}" 2>/dev/null || true)"
        if [[ -z "${existing}" || "${existing}" == "${BOX_USER}:20000:40000" ]]; then
            start="$(_box_next_start)"
            existing="${BOX_USER}:${start}:65536"
        fi
        uid_lines=("${existing}")
        gid_lines=("${existing}")
    fi
    for gid in "${BOX_DEVICE_GIDS[@]+"${BOX_DEVICE_GIDS[@]}"}"; do
        gid_lines+=("${BOX_USER}:${gid}:1")
    done
    _box_write_subids "${SUBUID_FILE}" "${uid_lines[@]}"
    _box_write_subids "${SUBGID_FILE}" "${gid_lines[@]}"
}

# ── create argv ───────────────────────────────────────────────────────────
# Fills BOX_CREATE_ARGV. Needs box_discover_devices and box_subids first.
box_create_argv() {
    local u gid gidmap=()
    u="$(id -u "${BOX_USER}" 2>/dev/null || echo '<uid>')"
    for gid in "${BOX_DEVICE_GIDS[@]+"${BOX_DEVICE_GIDS[@]}"}"; do
        gidmap+=(--gidmap "+g${gid}:@${gid}")
    done
    BOX_CREATE_ARGV=(
        podman create
        --name "${BOX_NAME}"
        --hostname "$(hostname)"
        --systemd=always
        --cap-add=all
        --security-opt label=disable
        --security-opt unmask=/proc/sys
        --device "${DEV_ROOT}/fuse:/dev/fuse"
        --device "${DEV_ROOT}/net/tun:/dev/net/tun"
        "${BOX_DEVICE_ARGS[@]+"${BOX_DEVICE_ARGS[@]}"}"
        "${gidmap[@]+"${gidmap[@]}"}"
        "${BOX_IDMAP_ARGS[@]+"${BOX_IDMAP_ARGS[@]}"}"
        --network "${BOX_NETWORK}"
        --ulimit memlock=-1:-1
        --conmon-pidfile "${HOST_ROOT}/run/user/${u}/${BOX_NAME}.pid"
        "${BOX_IMAGE}"
    )
}

# ── image ─────────────────────────────────────────────────────────────────
# Renders Containerfile.in into <dir>/Containerfile with the pinned base.
box_render_containerfile() {
    local dir="$1" text
    text="$(cat "${BOX_SELF_DIR}/Containerfile.in")"
    mkdir -p "${dir}"
    printf '%s\n' "${text//@BASE_IMAGE@/${BOX_BASE_IMAGE}}" > "${dir}/Containerfile"
}

# ── commands ──────────────────────────────────────────────────────────────
cmd_preflight() {
    box_require_root
    box_preflight
}

cmd_install() {
    local tree="${1:-}"
    [[ -n "${tree}" && -d "${tree}" ]] || die "usage: box.sh install <unpacked-tree> [install.sh args...]"
    shift
    box_require_root
    box_preflight || die "preflight failed; nothing was changed"
    box_subids
    box_create_argv
    if [[ "${HAL0_BOX_DRY_RUN:-0}" == 1 ]]; then
        printf '%s\n' "--- podman create argv ---"
        printf '%s\n' "${BOX_CREATE_ARGV[@]}"
        return 0
    fi
    die "box install is not implemented yet"
}

usage() {
    cat <<EOF
usage: box.sh <command>
  install <tree> [install.sh args...]   install hal0 in a rootless podman box
  preflight                             check this host can run the box
EOF
}

main() {
    local cmd="${1:-}"
    [[ $# -gt 0 ]] && shift
    case "${cmd}" in
        install) cmd_install "$@" ;;
        preflight) cmd_preflight ;;
        -h|--help|help) usage ;;
        *) usage >&2; exit 2 ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
