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
#          box.sh uninstall [--purge [--yes]]
#          box.sh rebuild
#          box.sh status | start | stop
#          box.sh logs [journalctl args...]
#          box.sh exec -- <command...>
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
#
# Overrides an operator may need:
#   HAL0_BOX_SELFTEST_IMAGE     image for the nested network self-test
#   HAL0_BOX_SELFTEST_URL       plain-HTTP URL the self-test fetches

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
# Root-owned launcher state: the verified tree (rebuild and the shim run from
# it), the installer args, and the firewall rules this launcher added.
STATE_DIR="${HOST_ROOT}/var/lib/hal0-box"
STATE_TREE="${STATE_DIR}/tree"
# Written when this launcher creates the box user ("<user>:<uid>"). Together
# with the user's comment it proves hal0 owns the account, so a pre-existing
# hal0box user is never adopted, rebuilt into or purged.
OWNER_FILE="${STATE_DIR}/owner"
# The image build context (root-owned, readable by the box user).
BUILD_DIR="${STATE_DIR}/build"
BOX_GECOS="hal0 box"
LOG_DIR="${HOST_ROOT}/var/log"
BUILD_LOG="${LOG_DIR}/hal0-box-build.log"
SELFTEST_LOG="${LOG_DIR}/hal0-box-selftest.log"
INSTALL_LOG="${LOG_DIR}/hal0-box-install.log"
DROPIN="${HOST_ROOT}/etc/systemd/system/user@.service.d/hal0-box.conf"
BOX_HOME="${HOST_ROOT}/home/${BOX_USER}"
RUN_USER_DIR="${HOST_ROOT}/run/user"
BOX_UNIT="${BOX_NAME}.service"
# Where the verified tree is copied inside the box.
BOX_SRC=/usr/local/src/hal0
# Nested network self-test (design decision 2): a container inside the box
# fetches a plain-HTTP connectivity endpoint. Pinned by index digest.
SELFTEST_IMAGE="${HAL0_BOX_SELFTEST_IMAGE:-docker.io/library/busybox:1.37@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec}"
SELFTEST_URL="${HAL0_BOX_SELFTEST_URL:-http://connectivity-check.ubuntu.com/}"
PODMAN_MIN_VERSION=4.9
# Extra `podman build` flags; `rebuild` sets --no-cache.
BOX_BUILD_ARGS=()
# State lives on volumes and the rootfs is disposable (decision 9): a rebuild
# is a new image plus the same installer, which never clobbers config.
BOX_VOLUMES=(
    "hal0-box-etc:/etc/hal0"
    "hal0-box-var:/var/lib/hal0"
    "hal0-box-containers:/var/lib/containers"
)

log() { printf '[hal0-box %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { log "WARNING: $*"; }
die() { log "ERROR: $*"; exit 1; }

# Run a command as the box user, in its home, with its user manager's
# runtime dir. The memlock raise is inherited by podman (decision 6).
as_box() {
    local u
    u="$(id -u "${BOX_USER}")"
    (
        cd "${BOX_HOME}" 2>/dev/null || cd /
        ulimit -Hl unlimited 2>/dev/null || true
        ulimit -Sl unlimited 2>/dev/null || true
        exec runuser -u "${BOX_USER}" -- env \
            HOME="${BOX_HOME}" \
            XDG_RUNTIME_DIR="${RUN_USER_DIR}/${u}" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=${RUN_USER_DIR}/${u}/bus" \
            "$@"
    )
}

box_user_exists() { id -u "${BOX_USER}" >/dev/null 2>&1; }

# True when the box user exists and this launcher created it: the owner
# marker names its current uid and its comment is the one useradd was given.
box_user_is_ours() {
    local u gecos
    u="$(id -u "${BOX_USER}" 2>/dev/null)" || return 1
    gecos="$(getent passwd "${BOX_USER}" 2>/dev/null | cut -d: -f5)" || gecos=""
    [[ "${gecos}" == "${BOX_GECOS}" && -f "${OWNER_FILE}" \
        && "$(cat "${OWNER_FILE}" 2>/dev/null)" == "${BOX_USER}:${u}" ]]
}

box_foreign_user_message() {
    printf '%s' "a ${BOX_USER} user already exists on this host and hal0 did not create it (no ${OWNER_FILE#"${HOST_ROOT}"} marker for its uid, or its comment is not \"${BOX_GECOS}\"). The box would take over that account and a purge would delete it. Remove or rename that user, then re-run."
}

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
    if box_user_exists && ! box_user_is_ours; then
        log "ERROR: $(box_foreign_user_message)"
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
    local u gid vol gidmap=() volumes=()
    u="$(id -u "${BOX_USER}" 2>/dev/null || echo '<uid>')"
    for gid in "${BOX_DEVICE_GIDS[@]+"${BOX_DEVICE_GIDS[@]}"}"; do
        gidmap+=(--gidmap "+g${gid}:@${gid}")
    done
    for vol in "${BOX_VOLUMES[@]}"; do
        volumes+=(--volume "${vol}")
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
        "${volumes[@]}"
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


# ── host setup ────────────────────────────────────────────────────────────
# Keep a root-owned copy of the verified tree: the shim and `rebuild` run from
# it after bootstrap's temp dir is gone. Root runs the launcher and the
# AppArmor helper from this copy, so the box user must not be able to write it.
box_stage_tree() {
    local tree="$1"; shift
    local src dst
    mkdir -p "${STATE_DIR}"
    chmod 0755 "${STATE_DIR}"
    src="$(cd "${tree}" && pwd -P)"
    dst="$(cd "${STATE_DIR}" && pwd -P)/tree"
    if [[ "${src}" != "${dst}" ]]; then
        rm -rf "${STATE_TREE}.new"
        cp -a "${src}/." "${STATE_TREE}.new"
        rm -rf "${STATE_TREE}"
        mv "${STATE_TREE}.new" "${STATE_TREE}"
    fi
    chown -R 0:0 "${STATE_TREE}"
    chmod -R go-w,go+rX "${STATE_TREE}"
    # The installer args, so `rebuild` re-runs the installer the same way.
    : > "${STATE_DIR}/install-args"
    [[ $# -eq 0 ]] || printf '%s\0' "$@" > "${STATE_DIR}/install-args"
    # install.sh refuses an unverified tree. Remember that bootstrap verified
    # this one, so a rebuild from the kept copy passes the same gate.
    if [[ "${HAL0_BOOTSTRAP_VERIFIED:-0}" == 1 ]]; then
        : > "${STATE_DIR}/verified"
    elif [[ "${src}" != "${dst}" ]]; then
        rm -f "${STATE_DIR}/verified"
    fi
    rm -f "${STATE_DIR}/installed"
}

box_host_packages() {
    local missing=() cmd
    for cmd in podman pasta newuidmap newgidmap python3; do
        command -v "${cmd}" >/dev/null 2>&1 || missing+=("${cmd}")
    done
    [[ "${#missing[@]}" -eq 0 ]] && return 0
    log "installing host packages (missing: ${missing[*]})"
    if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get -qq update \
            && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y podman uidmap passt python3 >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        dnf -q -y install podman passt shadow-utils python3
    elif command -v pacman >/dev/null 2>&1; then
        pacman -S --needed --noconfirm podman passt shadow python
    elif command -v zypper >/dev/null 2>&1; then
        zypper -q -n install podman passt shadow python3
    else
        die "missing ${missing[*]} and no known package manager; install podman (4.9 or newer), passt, uidmap/shadow and python3, then re-run"
    fi || die "host package install failed; install podman, passt, uidmap and python3 by hand, then re-run"
}

# Disable the guest AppArmor profiles that block rootless podman inside an
# unprivileged LXC (the #3234 family), before the first rootless podman call.
# The helper only touches profiles when /sys/kernel/security/apparmor/.ns_level
# is above 0, so it is a no-op on bare metal. It is run as a bare script, the
# form installer/lib/preflight.sh uses, because hal0 is not installed yet.
# A missing helper means the launcher and the tree disagree; fail rather than
# guess, because skipping it surfaces later as an opaque podman error.
box_nested_apparmor() {
    local helper="${STATE_TREE}/src/hal0/agents/containers_apparmor.py"
    [[ -f "${helper}" ]] \
        || die "the release tree has no src/hal0/agents/containers_apparmor.py, which the box launcher needs; this tree does not match the launcher"
    python3 "${helper}" --nested-box \
        || die "containers_apparmor.py --nested-box failed; rootless podman would be blocked by the guest's AppArmor profiles"
}

box_check_podman() {
    local v
    v="$(podman --version 2>/dev/null | awk '{print $NF}')" || v=""
    [[ -n "${v}" ]] || die "podman is not runnable on this host"
    if [[ "$(printf '%s\n%s\n' "${PODMAN_MIN_VERSION}" "${v}" | sort -V | head -1)" != "${PODMAN_MIN_VERSION}" ]]; then
        die "podman ${v} is too old; the box needs podman ${PODMAN_MIN_VERSION} or newer"
    fi
    log "podman ${v}"
}

# The box user, its sub-IDs, the user@.service drop-in and a lingering user
# manager, so the box starts at boot with nobody logged in.
box_user_setup() {
    local u want
    if ! box_user_exists; then
        useradd -m -d "${BOX_HOME}" -s /usr/sbin/nologin -c "${BOX_GECOS}" "${BOX_USER}" \
            || die "could not create the ${BOX_USER} user"
        mkdir -p "${STATE_DIR}"
        printf '%s:%s\n' "${BOX_USER}" "$(id -u "${BOX_USER}")" > "${OWNER_FILE}"
        log "created user ${BOX_USER}"
    fi
    box_user_is_ours || die "$(box_foreign_user_message)"
    u="$(id -u "${BOX_USER}")"
    box_subids
    want="$(printf '[Service]\nLimitMEMLOCK=infinity\nDelegate=cpu cpuset io memory pids\n')"
    mkdir -p "$(dirname "${DROPIN}")"
    if [[ ! -f "${DROPIN}" || "$(cat "${DROPIN}")" != "${want}" ]]; then
        printf '%s\n' "${want}" > "${DROPIN}"
        systemctl daemon-reload
        loginctl enable-linger "${BOX_USER}"
        systemctl restart "user@${u}.service"
    else
        loginctl enable-linger "${BOX_USER}"
        systemctl start "user@${u}.service"
    fi
    local _
    for _ in $(seq 1 30); do
        systemctl is-active --quiet "user@${u}.service" && [[ -S "${RUN_USER_DIR}/${u}/bus" ]] && break
        sleep 1
    done
    systemctl is-active --quiet "user@${u}.service" \
        || die "the ${BOX_USER} user manager (user@${u}.service) did not start; see: journalctl -u user@${u}.service"
}

# Rootless podman clamps --ulimit memlock to the hard limit of the shell that
# runs `podman create` (decision 6), so raise it here and fail if we cannot.
box_raise_memlock() {
    ulimit -Hl unlimited 2>/dev/null || true
    ulimit -Sl unlimited 2>/dev/null || true
    [[ "$(ulimit -Hl)" == unlimited ]] && return 0
    if [[ "${HAL0_BOX_ALLOW_LOW_MEMLOCK:-0}" == 1 ]]; then
        warn "memlock hard limit is $(ulimit -Hl), not unlimited (allowed by HAL0_BOX_ALLOW_LOW_MEMLOCK)"
        return 0
    fi
    die "could not raise the memlock hard limit to unlimited; GPU runners need it. Run as real root, not inside a limited shell."
}

# The build context lives in the root-owned state dir, never in the box
# user's home: root writing there would follow any symlink the box user
# planted (to /etc/shadow, say). The box user only needs to read it.
box_build_image() {
    local dir="${BUILD_DIR}" groups="${BOX_DEVICE_GROUPS[*]:-}"
    rm -rf "${dir}"
    mkdir -p "${dir}"
    chmod 0755 "${STATE_DIR}" "${dir}"
    box_render_containerfile "${dir}"
    chmod 0644 "${dir}/Containerfile"
    mkdir -p "${LOG_DIR}"
    log "building the box image (log: ${BUILD_LOG#"${HOST_ROOT}"})"
    as_box podman build "$@" --build-arg "HAL0_BOX_DEVICE_GROUPS=${groups}" -t "${BOX_IMAGE}" "${dir}" \
        > "${BUILD_LOG}" 2>&1 \
        || die "box image build failed, see ${BUILD_LOG#"${HOST_ROOT}"}"
}

# Written by the box user, so root never writes through a path in a home the
# box user controls.
box_write_unit() {
    local podman_bin dir="${BOX_HOME}/.config/systemd/user"
    podman_bin="$(command -v podman)"
    as_box mkdir -p "${dir}" || die "could not create ${dir#"${HOST_ROOT}"} as ${BOX_USER}"
    # A persistent unit, not quadlet: quadlet's --rm --replace would destroy
    # the box on stop (decision 7).
    as_box tee "${dir}/${BOX_UNIT}" >/dev/null <<EOF || die "could not write the ${BOX_UNIT} user unit as ${BOX_USER}"
[Unit]
Description=hal0 in a rootless podman box
Wants=network-online.target
After=network-online.target

[Service]
Environment=PODMAN_SYSTEMD_UNIT=%n
LimitMEMLOCK=infinity
Type=forking
PIDFile=%t/${BOX_NAME}.pid
ExecStart=${podman_bin} start ${BOX_NAME}
ExecStop=${podman_bin} stop -t 60 ${BOX_NAME}
TimeoutStopSec=90
Restart=on-failure

[Install]
WantedBy=default.target
EOF
}

# Removes an existing box container (state on volumes stays), then creates
# and starts a fresh one. A re-run after any failure converges here.
box_create_and_start() {
    if as_box podman container exists "${BOX_NAME}"; then
        log "replacing the existing ${BOX_NAME} container"
        as_box systemctl --user stop "${BOX_UNIT}" >/dev/null 2>&1 || true
        as_box podman rm -f "${BOX_NAME}" >/dev/null \
            || die "could not remove the old ${BOX_NAME} container"
    fi
    local vol
    for vol in "${BOX_VOLUMES[@]}"; do
        vol="${vol%%:*}"
        as_box podman volume exists "${vol}" \
            || as_box podman volume create "${vol}" >/dev/null \
            || die "could not create the ${vol} volume"
    done
    box_create_argv
    as_box "${BOX_CREATE_ARGV[@]}" >/dev/null || die "podman create failed"
    box_write_unit
    as_box systemctl --user daemon-reload
    as_box systemctl --user enable "${BOX_UNIT}" >/dev/null 2>&1 || true
    as_box systemctl --user restart "${BOX_UNIT}" \
        || die "the ${BOX_UNIT} user unit did not start; see: sudo hal0 box logs, or journalctl --user -u ${BOX_UNIT} as ${BOX_USER}"
}

box_wait_systemd() {
    local state="" _
    for _ in $(seq 1 60); do
        state="$(as_box podman exec "${BOX_NAME}" systemctl is-system-running 2>/dev/null | tail -1)" || true
        case "${state}" in
            running) break ;;
            degraded) warn "the box's systemd is degraded (podman exec ${BOX_NAME} systemctl --failed shows why)"; break ;;
        esac
        sleep 2
    done
    case "${state}" in
        running|degraded) log "box systemd: ${state}" ;;
        *) die "the box's systemd did not come up (state: ${state:-unknown})" ;;
    esac
}

# Decision 2: a nested container makes an outbound TCP connection through the
# box's pasta. Old passt builds stall here, and the installer would otherwise
# hang much later on its first image pull.
box_self_test() {
    log "self-test: outbound connection from a container nested in the box"
    if ! as_box podman exec "${BOX_NAME}" timeout 300 \
        podman run --rm --pull=missing "${SELFTEST_IMAGE}" \
        wget -q -T 30 -O /dev/null "${SELFTEST_URL}" > "${SELFTEST_LOG}" 2>&1; then
        log "ERROR: a container nested in the box could not reach ${SELFTEST_URL}."
        log "  With working host networking this is the known stall of older passt/pasta"
        log "  builds (2024-02 and earlier) under nested containers. Install a newer passt"
        log "  package on the host (2026-01 or later), then re-run this install."
        log "  Details: ${SELFTEST_LOG#"${HOST_ROOT}"}"
        exit 1
    fi
}

# Every HAL0_* setting the operator gave bootstrap reaches install.sh, except
# the launcher's own and bootstrap's temp-dir hand-off, which is ours to clean.
box_installer_env() {
    local v
    BOX_INSTALLER_ENV=()
    while IFS= read -r v; do
        case "${v}" in
            HAL0_BOX_*|HAL0_BOOTSTRAP_WORK|HAL0_BOOTSTRAP_KEEP_TMP|HAL0_BOOTSTRAP_VERIFIED) continue ;;
            HAL0_*) BOX_INSTALLER_ENV+=(-e "${v}=${!v}") ;;
        esac
    done < <(compgen -e)
    if [[ -f "${STATE_DIR}/verified" ]]; then
        BOX_INSTALLER_ENV+=(-e HAL0_BOOTSTRAP_VERIFIED=1)
    fi
}

box_run_installer() {
    log "copying the verified tree into the box"
    as_box podman exec "${BOX_NAME}" rm -rf "${BOX_SRC}"
    as_box podman exec "${BOX_NAME}" mkdir -p "${BOX_SRC}"
    as_box podman cp "${STATE_TREE}/." "${BOX_NAME}:${BOX_SRC}" \
        || die "could not copy the release tree into the box"
    box_installer_env
    log "running the hal0 installer inside the box (log: ${INSTALL_LOG#"${HOST_ROOT}"})"
    as_box podman exec "${BOX_INSTALLER_ENV[@]+"${BOX_INSTALLER_ENV[@]}"}" "${BOX_NAME}" \
        bash "${BOX_SRC}/installer/install.sh" "$@" 2>&1 | tee "${INSTALL_LOG}" \
        || die "the hal0 installer failed inside the box, see ${INSTALL_LOG#"${HOST_ROOT}"}"
}

# Host-local HTTP differs by podman version, so health is checked inside.
box_wait_health() {
    local _
    for _ in $(seq 1 60); do
        as_box podman exec "${BOX_NAME}" curl -fsS -m 4 -o /dev/null "http://127.0.0.1:${BOX_PORTS[0]}/api/health" \
            >/dev/null 2>&1 && return 0
        sleep 5
    done
    die "hal0 did not answer /api/health inside the box within 5 minutes"
}

# An active ufw or firewalld would block LAN clients. Open exactly the
# published ports and record each rule this launcher added, so uninstall
# removes those and never a rule the operator had before.
box_firewall_open() {
    local rec="${STATE_DIR}/firewall" p rule
    touch "${rec}"
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | head -1 | grep -q '^Status: active'; then
        for p in "${BOX_PORTS[@]}"; do
            rule="ufw ${p}/tcp"
            grep -qxF "${rule}" "${rec}" && continue
            if ufw status 2>/dev/null | awk '{print $1, $2}' | grep -qxF "${p}/tcp ALLOW"; then
                continue
            fi
            ufw allow "${p}/tcp" comment "${BOX_NAME}" >/dev/null && printf '%s\n' "${rule}" >> "${rec}"
        done
        log "ufw is active: allowed ${BOX_PORTS[*]}/tcp"
    elif command -v firewall-cmd >/dev/null 2>&1 && [[ "$(firewall-cmd --state 2>/dev/null)" == running ]]; then
        for p in "${BOX_PORTS[@]}"; do
            rule="firewalld ${p}/tcp"
            grep -qxF "${rule}" "${rec}" && continue
            firewall-cmd -q --permanent --query-port="${p}/tcp" && continue
            firewall-cmd -q --permanent --add-port="${p}/tcp" \
                && firewall-cmd -q --add-port="${p}/tcp" \
                && printf '%s\n' "${rule}" >> "${rec}"
        done
        log "firewalld is running: allowed ${BOX_PORTS[*]}/tcp"
    fi
}

box_firewall_close() {
    local rec="${STATE_DIR}/firewall" kind port
    [[ -f "${rec}" ]] || return 0
    while read -r kind port; do
        case "${kind}" in
            ufw) ufw delete allow "${port}" >/dev/null 2>&1 || true ;;
            firewalld)
                firewall-cmd -q --permanent --remove-port="${port}" >/dev/null 2>&1 || true
                firewall-cmd -q --remove-port="${port}" >/dev/null 2>&1 || true
                ;;
        esac
    done < "${rec}"
}

box_install_shim() {
    local text
    text="$(cat "${BOX_SELF_DIR}/hal0-shim")"
    mkdir -p "$(dirname "${SHIM_PATH}")"
    printf '%s\n' "${text//@BOX_SH@/${STATE_TREE}/installer/box/box.sh}" > "${SHIM_PATH}.new"
    chmod 0755 "${SHIM_PATH}.new"
    mv "${SHIM_PATH}.new" "${SHIM_PATH}"
}

# bootstrap.sh's EXIT trap died at its exec into us, so its temp dir is ours
# to remove after a successful install, as install.sh does on bare metal.
box_cleanup_bootstrap_work() {
    local work="${HAL0_BOOTSTRAP_WORK:-}"
    [[ -n "${work}" && "${HAL0_BOOTSTRAP_KEEP_TMP:-0}" != 1 ]] || return 0
    [[ -d "${work}" && "$(basename "${work}")" == hal0-install-* ]] || return 0
    rm -rf "${work}"
}

_box_install_failed() {
    local rc=$?
    [[ "${rc}" == 0 ]] && return 0
    log "the box install did not finish. Re-run the same install command to retry (state on the box volumes is kept),"
    log "or remove the box with: sudo bash ${STATE_TREE#"${HOST_ROOT}"}/installer/box/box.sh uninstall (add --purge to also delete its data)"
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
    if [[ "${HAL0_BOX_DRY_RUN:-0}" == 1 ]]; then
        box_subids
        box_create_argv
        printf '%s\n' "--- podman create argv ---"
        printf '%s\n' "${BOX_CREATE_ARGV[@]}"
        return 0
    fi
    box_stage_tree "${tree}" "$@"
    box_install_flow "$@"
}

# Everything after preflight. Expects STATE_TREE to hold the tree to install.
box_install_flow() {
    local t0="${SECONDS}" ip
    trap _box_install_failed EXIT
    box_host_packages
    box_nested_apparmor
    box_check_podman
    box_user_setup
    box_raise_memlock
    box_build_image "${BOX_BUILD_ARGS[@]+"${BOX_BUILD_ARGS[@]}"}"
    box_create_and_start
    box_wait_systemd
    box_self_test
    box_run_installer "$@"
    box_wait_health
    box_firewall_open
    box_install_shim
    date -u +%Y-%m-%dT%H:%M:%SZ > "${STATE_DIR}/installed"
    trap - EXIT
    box_cleanup_bootstrap_work
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    log "done in $((SECONDS - t0))s. Dashboard: http://${ip:-<this-host>}:${BOX_PORTS[0]}"
    log "host commands run through the box: sudo hal0 <command>; box lifecycle: sudo hal0 box status|start|stop|logs"
}

uninstall_usage() {
    cat <<EOF
usage: hal0 uninstall [--purge [--yes]]
       box.sh uninstall [--purge [--yes]]

Removes the hal0 box: its container and image, its user unit, the host
hal0 shim, the ${BOX_USER} user's linger, the systemd drop-in, and the
firewall rules and AppArmor changes the launcher made.

Keeps the box's data: the ${BOX_USER} user, its sub-ID ranges (the volumes'
file ownership depends on them) and the state volumes $(_box_volume_names)
(config, models, history). Installing again reuses them.

  --purge        also delete the data: the volumes and the rest of the box
                 user's podman store, the ${BOX_USER} user and its home, and
                 its sub-ID ranges. Asks you to type DELETE.
  --yes, -y      skip the DELETE prompt (required when not on a terminal).
                 --force, -f and HAL0_FORCE=1 do the same.
  -h, --help     show this help
EOF
}

_box_volume_names() {
    local vol out=()
    for vol in "${BOX_VOLUMES[@]}"; do out+=("${vol%%:*}"); done
    printf '%s' "${out[*]}"
}

# --purge deletes every model and all box data, so it needs a typed DELETE on
# a terminal, or --yes when there is none to ask on.
box_confirm_purge() {
    local answer=""
    [[ -t 0 ]] || die "refusing to --purge without a terminal to confirm on: it deletes every model and all hal0 data in the box. Re-run with --purge --yes to do it non-interactively."
    printf 'This deletes the %s user, the box volumes and every model and all hal0 data in them.\nType DELETE to confirm: ' "${BOX_USER}"
    read -r answer || answer=""
    [[ "${answer}" == DELETE ]] || die "not confirmed; nothing was changed"
}

# Ends every process of the box user, with a bounded wait. userdel refuses a
# user with live processes, so a survivor stops the purge here, before any
# sub-ID or state is touched.
box_terminate_user() {
    local _
    loginctl terminate-user "${BOX_USER}" 2>/dev/null || true
    for _ in $(seq 1 20); do
        pgrep -u "${BOX_USER}" >/dev/null 2>&1 || return 0
        pkill -KILL -u "${BOX_USER}" 2>/dev/null || true
        sleep 0.5
    done
    pgrep -u "${BOX_USER}" >/dev/null 2>&1 || return 0
    die "processes of ${BOX_USER} are still running after SIGKILL (see: pgrep -a -u ${BOX_USER}). The user, its sub-ID ranges and the launcher state were kept. Stop them and re-run: sudo bash ${STATE_TREE#"${HOST_ROOT}"}/installer/box/box.sh uninstall --purge"
}

# Deletes the box user and its home. A user that is still there afterwards
# is a failed purge, never a silent success.
box_delete_user() {
    local out rc=0
    out="$(userdel -r "${BOX_USER}" 2>&1)" || rc=$?
    if box_user_exists; then
        die "userdel -r ${BOX_USER} failed (exit ${rc}): ${out:-no output}. The user, its sub-ID ranges and the launcher state were kept; fix the cause and re-run: sudo bash ${STATE_TREE#"${HOST_ROOT}"}/installer/box/box.sh uninstall --purge"
    fi
    [[ "${rc}" == 0 ]] || warn "userdel -r ${BOX_USER} exited ${rc} (${out:-no output}); the user is gone, but check that ${BOX_HOME#"${HOST_ROOT}"} was removed"
}

# The launcher's per-install files in the state dir. A plain uninstall drops
# these but keeps the saved tree, so the data it keeps can still be purged
# later with the launcher in it.
BOX_STATE_INSTALL_FILES=(installed install-args verified firewall)

cmd_uninstall() {
    local purge=0 yes=0 arg u f kept=0 foreign=0
    [[ "${HAL0_FORCE:-0}" == 1 ]] && yes=1
    for arg in "$@"; do
        case "${arg}" in
            --purge) purge=1 ;;
            --yes|-y|--force|-f) yes=1 ;;
            -h|--help) uninstall_usage; return 0 ;;
            *) log "ERROR: unknown uninstall option: ${arg}"; uninstall_usage >&2; exit 2 ;;
        esac
    done
    box_require_root
    if box_user_exists && ! box_user_is_ours; then
        [[ "${purge}" == 0 ]] || die "refusing to purge: $(box_foreign_user_message)"
        foreign=1
    fi
    [[ "${purge}" == 0 || "${yes}" == 1 ]] || box_confirm_purge
    if [[ "${purge}" == 1 ]]; then
        log "removing the hal0 box, its user and all hal0 data in it"
    else
        log "removing the hal0 box; its data (the ${BOX_USER} user and the state volumes) is kept"
    fi
    if [[ "${foreign}" == 1 ]]; then
        warn "$(box_foreign_user_message) Leaving that user, its processes and its sub-ID ranges alone."
    elif box_user_exists; then
        u="$(id -u "${BOX_USER}")"
        as_box systemctl --user disable --now "${BOX_UNIT}" >/dev/null 2>&1 || true
        as_box podman rm -f "${BOX_NAME}" >/dev/null 2>&1 || true
        as_box podman image rm -f "${BOX_IMAGE}" >/dev/null 2>&1 || true
        as_box rm -f "${BOX_HOME}/.config/systemd/user/${BOX_UNIT}" 2>/dev/null || true
        as_box systemctl --user daemon-reload >/dev/null 2>&1 || true
        if [[ "${purge}" == 1 ]]; then
            # Only ever as the box user: as root this would wipe the host's
            # own containers and images.
            as_box podman system reset --force >/dev/null 2>&1 || true
        fi
        loginctl disable-linger "${BOX_USER}" 2>/dev/null || true
        systemctl stop "user@${u}.service" 2>/dev/null || true
        if [[ "${purge}" == 1 ]]; then
            box_terminate_user
            box_delete_user
        else
            kept=1
        fi
    else
        log "user ${BOX_USER} is already gone; cleaning what remains"
    fi
    box_firewall_close
    if [[ -f "${DROPIN}" ]]; then
        rm -f "${DROPIN}"
        rmdir "$(dirname "${DROPIN}")" 2>/dev/null || true
        systemctl daemon-reload 2>/dev/null || true
    fi
    if [[ -f "${SHIM_PATH}" ]] && grep -q "${SHIM_MARKER}" "${SHIM_PATH}"; then
        rm -f "${SHIM_PATH}"
    fi
    rm -f "${BUILD_LOG}" "${SELFTEST_LOG}" "${INSTALL_LOG}"
    if [[ "${kept}" == 1 ]]; then
        for f in "${BOX_STATE_INSTALL_FILES[@]}"; do
            rm -f "${STATE_DIR:?}/${f}"
        done
        log "removed the box. Kept: the ${BOX_USER} user, its sub-ID ranges and the volumes $(_box_volume_names)."
        log "Installing again reuses them. To delete them too: sudo bash ${STATE_TREE#"${HOST_ROOT}"}/installer/box/box.sh uninstall --purge"
        return 0
    fi
    if [[ "${foreign}" == 0 ]]; then
        for f in "${SUBUID_FILE}" "${SUBGID_FILE}"; do
            [[ -f "${f}" ]] && _box_write_subids "${f}"
        done
    fi
    rm -rf "${STATE_DIR}"
    log "removed"
}

# A new image and container on the same volumes, then the installer again
# with the args of the original install. Picks up base OS changes that
# unattended-upgrades does not cover, and repairs a damaged rootfs.
cmd_rebuild() {
    box_require_root
    [[ -d "${STATE_TREE}" ]] && box_user_exists \
        || die "the hal0 box is not installed; nothing to rebuild"
    local args=()
    if [[ -f "${STATE_DIR}/install-args" ]]; then
        mapfile -d '' -t args < "${STATE_DIR}/install-args"
    fi
    log "rebuilding the box; the state volumes are kept"
    # Stop first so the published ports are free for preflight.
    as_box systemctl --user stop "${BOX_UNIT}" >/dev/null 2>&1 || true
    box_preflight || die "preflight failed; the box is stopped. Fix the above and re-run: sudo hal0 box rebuild"
    box_stage_tree "${STATE_TREE}" "${args[@]+"${args[@]}"}"
    BOX_BUILD_ARGS=(--no-cache)
    box_install_flow "${args[@]+"${args[@]}"}"
}

cmd_status() {
    box_require_root
    box_user_exists || { log "the hal0 box is not installed"; return 3; }
    local state
    as_box systemctl --user --no-pager status "${BOX_UNIT}" || true
    state="$(as_box podman exec "${BOX_NAME}" systemctl is-system-running 2>/dev/null | tail -1)" || true
    log "box systemd: ${state:-not running}"
    if [[ -f "${STATE_DIR}/installed" ]]; then
        log "install: complete ($(cat "${STATE_DIR}/installed"))"
    else
        log "install: incomplete. Re-run the install, or: sudo hal0 box rebuild"
        return 1
    fi
}

cmd_unit() {
    box_require_root
    box_user_exists || die "the hal0 box is not installed"
    as_box systemctl --user "$1" "${BOX_UNIT}"
}

cmd_logs() {
    box_require_root
    box_user_exists || die "the hal0 box is not installed"
    [[ $# -gt 0 ]] || set -- -n 200
    as_box podman exec "${BOX_NAME}" journalctl --no-pager "$@"
}

cmd_exec() {
    [[ "${1:-}" == -- ]] && shift
    [[ $# -gt 0 ]] || die "usage: box.sh exec -- <command...>"
    box_require_root
    box_user_exists || die "the hal0 box is not installed"
    local flags=(-i)
    [[ -t 0 && -t 1 ]] && flags+=(-t)
    as_box podman exec "${flags[@]}" "${BOX_NAME}" "$@"
}

usage() {
    cat <<EOF
usage: box.sh <command>
  install <tree> [install.sh args...]   install hal0 in a rootless podman box
  preflight                             check this host can run the box
  uninstall [--purge [--yes]]           remove the box; --purge also deletes its user and data
  rebuild                               new image and container, same volumes
  status | start | stop                 the box's user unit
  logs [journalctl args...]             the box's journal
  exec -- <command...>                  run a command inside the box
EOF
}

main() {
    local cmd="${1:-}"
    [[ $# -gt 0 ]] && shift
    case "${cmd}" in
        install) cmd_install "$@" ;;
        preflight) cmd_preflight ;;
        uninstall) cmd_uninstall "$@" ;;
        rebuild) cmd_rebuild ;;
        status) cmd_status ;;
        start|stop) cmd_unit "${cmd}" ;;
        logs) cmd_logs "$@" ;;
        exec) cmd_exec "$@" ;;
        -h|--help|help) usage ;;
        *) usage >&2; exit 2 ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
