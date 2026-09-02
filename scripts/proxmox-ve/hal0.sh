#!/usr/bin/env bash
#
# hal0 Proxmox VE installer — creates a privileged Ubuntu 26.04 LXC, wires up
# GPU/NPU passthrough when the host has any, and runs the standard hal0
# bootstrap inside it.
#
# Run on a Proxmox VE host (NOT inside a container):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"
#
# Pass --advanced to open whiptail prompts for every parameter; otherwise
# values come from env vars (see README) with sensible defaults applied
# silently. Every default is printed before container creation, with a
# 5s grace window to ctrl-C and re-run with overrides.
#
# Defaults are the production hal0 shape:
#
#   * PRIVILEGED container. hal0 runs rootful podman slots that open the
#     GPU compute node directly; an unprivileged container maps device
#     ownership through the host's idmap, which costs hal0-user probes
#     their GPU visibility and makes /dev/kfd gid repair impossible from
#     inside (#1953). Set UNPRIVILEGED=1 for a CPU-only/Vulkan container.
#     A privileged container's root is effectively host root — that is the
#     trade this default makes, deliberately, for hardware access.
#   * Ubuntu 26.04. The FastFlowLM .deb and the toolbox images track it,
#     and it ships a Python new enough for hal0's venv without backports.
#   * nesting/keyctl/fuse/mknod features — podman-in-LXC needs them.
#   * GPU/NPU passthrough (GPU_PASSTHROUGH=auto): every AMD compute/render
#     node the HOST actually has is forwarded, with matching cgroup2 device
#     rules, unlimited memlock and (privileged only) an unconfined apparmor
#     profile. Nothing to hand-edit in /etc/pve/lxc/<CTID>.conf afterwards.
#
# Everything the guest needs (tar, jq, python3, python3-venv, podman,
# cosign) is installed by the hal0 bootstrap/installer itself. The single
# exception this script stages is curl — the tool that downloads the
# bootstrap cannot be downloaded by the bootstrap.

set -euo pipefail
IFS=$'\n\t'

# ── output helpers (community-scripts visual parity) ──────────────────────
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    YW=$'\033[33m'; BL=$'\033[1;34m'; RD=$'\033[01;31m'
    GN=$'\033[1;92m'; CL=$'\033[m'; BFR='\\r\\033[K'
else
    YW=""; BL=""; RD=""; GN=""; CL=""; BFR=""
fi
CM=" ✓ "
CROSS=" ✗ "
HOLD=" "

msg_info()  { printf "%b%s %s%s..." "${BFR}" "${HOLD}" "${YW}" "$*${CL}"; }
msg_ok()    { printf "%b%s%s %s%s\n" "${BFR}" "${CM}" "${GN}" "$*" "${CL}"; }
msg_warn()  { printf "%b%s %s%s\n" "${BFR}" "${HOLD}" "${YW}" "$*${CL}" >&2; }
msg_error() { printf "%b%s%s %s%s\n" "${BFR}" "${CROSS}" "${RD}" "$*" "${CL}" >&2; }
die()       { msg_error "$*"; exit 1; }

header() {
    cat <<'EOF'
    __          __ ____
   / /_  ____ _/ // __ \
  / __ \/ __ `/ // / / /
 / / / / /_/ / // /_/ /
/_/ /_/\__,_/_/ \____/   open-source home AI inference

EOF
}

# ── preflight ─────────────────────────────────────────────────────────────
require_pve() {
    [[ "$(uname -s)" == "Linux" ]] || die "this script must run on a Proxmox VE host (Linux)"
    [[ $EUID -eq 0 ]] || die "must run as root on the Proxmox VE host"
    command -v pveversion >/dev/null 2>&1 || die "pveversion not found — is this a Proxmox VE host?"
    command -v pct >/dev/null 2>&1 || die "pct not found — Proxmox container tools missing"
}

# ── defaults (override via env) ───────────────────────────────────────────
default_ctid() {
    if command -v pvesh >/dev/null 2>&1; then
        pvesh get /cluster/nextid 2>/dev/null || echo 200
    else
        echo 200
    fi
}

default_storage() {
    # Prefer local-lvm if present, fall back to first content=rootdir storage
    if pvesm status -content rootdir 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx local-lvm; then
        echo local-lvm
    else
        pvesm status -content rootdir 2>/dev/null | awk 'NR>1 {print $1; exit}'
    fi
}

default_bridge() {
    grep -E '^iface vmbr[0-9]+' /etc/network/interfaces 2>/dev/null \
        | awk '{print $2; exit}' || echo vmbr0
}

CTID="${CTID:-$(default_ctid)}"
HOSTNAME="${HOSTNAME:-hal0}"
CORES="${CORES:-4}"
RAM_MB="${RAM_MB:-8192}"
SWAP_MB="${SWAP_MB:-1024}"
DISK_GB="${DISK_GB:-20}"
STORAGE="${STORAGE:-$(default_storage)}"
BRIDGE="${BRIDGE:-$(default_bridge)}"
# Static addressing without hand-writing NET_CONFIG: set IP_CIDR (+GATEWAY).
IP_CIDR="${IP_CIDR:-}"
GATEWAY="${GATEWAY:-}"
if [[ -n "${IP_CIDR}" ]]; then
    NET_CONFIG="${NET_CONFIG:-name=eth0,bridge=${BRIDGE},ip=${IP_CIDR}${GATEWAY:+,gw=${GATEWAY}}}"
else
    NET_CONFIG="${NET_CONFIG:-name=eth0,bridge=${BRIDGE},ip=dhcp}"
fi
NAMESERVER="${NAMESERVER:-}"
SEARCHDOMAIN="${SEARCHDOMAIN:-}"
TIMEZONE="${TIMEZONE:-}"
TAGS="${TAGS:-hal0}"
ONBOOT="${ONBOOT:-1}"
STARTUP="${STARTUP:-}"
# MOUNTS: newline- or semicolon-separated `--mpN` values, e.g.
#   MOUNTS='/mnt/ai-models,mp=/mnt/ai-models,backup=0;/srv/data,mp=/mnt/data'
MOUNTS="${MOUNTS:-}"
OS_TYPE="${OS_TYPE:-ubuntu}"
OS_VERSION="${OS_VERSION:-26.04}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
# Privileged by default — see the header comment for why. UNPRIVILEGED=1
# still produces the old vanilla container.
UNPRIVILEGED="${UNPRIVILEGED:-0}"
FEATURES="${FEATURES:-nesting=1,keyctl=1,fuse=1,mknod=1}"
# auto = forward whatever the host actually has; 1 = same but fail if the
# host has no GPU/NPU node at all; 0 = skip passthrough entirely.
GPU_PASSTHROUGH="${GPU_PASSTHROUGH:-auto}"
PASSWORD="${PASSWORD:-}"  # empty = no root password set; use `pct enter` from host
SSH_AUTHORIZED_KEYS="${SSH_AUTHORIZED_KEYS:-}"
HAL0_CHANNEL="${HAL0_CHANNEL:-stable}"
RUN_BOOTSTRAP="${RUN_BOOTSTRAP:-1}"

# ── advanced (whiptail) prompts ───────────────────────────────────────────
ADVANCED=0
for arg in "$@"; do
    case "$arg" in
        --advanced) ADVANCED=1 ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "unknown argument: $arg (try --help)" ;;
    esac
done

prompt_advanced() {
    command -v whiptail >/dev/null 2>&1 || die "whiptail not installed (apt install -y whiptail) — re-run without --advanced or install whiptail"

    CTID=$(whiptail --inputbox "Container ID (CTID)" 8 60 "${CTID}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
    HOSTNAME=$(whiptail --inputbox "Hostname" 8 60 "${HOSTNAME}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
    CORES=$(whiptail --inputbox "CPU cores" 8 60 "${CORES}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
    RAM_MB=$(whiptail --inputbox "RAM (MB)" 8 60 "${RAM_MB}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
    DISK_GB=$(whiptail --inputbox "Disk (GB)" 8 60 "${DISK_GB}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
    STORAGE=$(whiptail --inputbox "Storage pool" 8 60 "${STORAGE}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
    BRIDGE=$(whiptail --inputbox "Network bridge" 8 60 "${BRIDGE}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
    IP_CIDR=$(whiptail --inputbox "IPv4 CIDR (blank = DHCP)" 8 60 "${IP_CIDR}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
    if [[ -n "${IP_CIDR}" ]]; then
        GATEWAY=$(whiptail --inputbox "Gateway" 8 60 "${GATEWAY}" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1
        NET_CONFIG="name=eth0,bridge=${BRIDGE},ip=${IP_CIDR}${GATEWAY:+,gw=${GATEWAY}}"
    else
        NET_CONFIG="name=eth0,bridge=${BRIDGE},ip=dhcp"
    fi
    PASSWORD=$(whiptail --passwordbox "Root password (blank = none, use pct enter)" 8 60 "" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1

    if whiptail --yesno "Use a PRIVILEGED container? (recommended — required for GPU/NPU passthrough)" 8 70 --title "hal0 LXC"; then
        UNPRIVILEGED=0
    else
        UNPRIVILEGED=1
    fi

    if whiptail --yesno "Forward the host's GPU/NPU devices into the container?" 8 70 --title "hal0 LXC"; then
        GPU_PASSTHROUGH=auto
    else
        GPU_PASSTHROUGH=0
    fi
}

[[ $ADVANCED -eq 1 ]] && prompt_advanced

# ── host GPU/NPU discovery ────────────────────────────────────────────────
# Everything below reads the HOST's device nodes. Nothing here can be done
# from inside the container (install.sh detects the result and reports it,
# but a guest cannot forward its own devices) — which is exactly why this
# script, not the installer, owns passthrough.
#
# Collected into two arrays:
#   GPU_DEV_ARGS   — `--devN` values for pct create
#   GPU_CGROUP_MAJ — device majors needing an lxc.cgroup2.devices.allow rule
GPU_DEV_ARGS=()
GPU_CGROUP_MAJ=()

_dev_major() {
    # stat prints the major in hex; cgroup rules want decimal.
    local hex
    hex="$(stat -c '%t' "$1" 2>/dev/null)" || return 1
    [[ -n "${hex}" ]] || return 1
    printf '%d\n' "$((16#${hex}))"
}

_add_gpu_device() {
    local path="$1" with_gid="${2:-1}" gid=""
    [[ -e "${path}" ]] || return 1
    if [[ "${with_gid}" == "1" ]]; then
        gid="$(stat -c '%g' "${path}" 2>/dev/null || true)"
    fi
    # gid= makes the node readable by that group INSIDE the container. On a
    # privileged container host gid == container gid, so this is the same
    # number; on an unprivileged one pct maps it. Root-owned nodes (gid 0,
    # e.g. /dev/kfd on stock kernels) are forwarded without a gid so hal0's
    # own repair path (#1953) can align them.
    if [[ -n "${gid}" && "${gid}" != "0" ]]; then
        GPU_DEV_ARGS+=("${path},gid=${gid}")
    else
        GPU_DEV_ARGS+=("${path}")
    fi
    local maj
    if maj="$(_dev_major "${path}")"; then
        GPU_CGROUP_MAJ+=("${maj}")
    fi
    return 0
}

discover_gpu_devices() {
    [[ "${GPU_PASSTHROUGH}" != "0" ]] || { msg_warn "GPU passthrough disabled (GPU_PASSTHROUGH=0) — CPU-only container"; return 0; }

    msg_info "probing host for GPU/NPU devices"
    local node
    # Render nodes first: hal0 derives the render gid from these.
    for node in /dev/dri/renderD*; do
        [[ -e "${node}" ]] && _add_gpu_device "${node}" 1
    done
    # Strix Halo exposes /dev/dri/amdgpu (no card0); forward it when present.
    [[ -e /dev/dri/amdgpu ]] && _add_gpu_device /dev/dri/amdgpu 1
    # ROCm compute node.
    [[ -e /dev/kfd ]] && _add_gpu_device /dev/kfd 1
    # XDNA/NPU accelerators (FastFlowLM lane).
    for node in /dev/accel/accel*; do
        [[ -e "${node}" ]] && _add_gpu_device "${node}" 1
    done

    if [[ ${#GPU_DEV_ARGS[@]} -eq 0 ]]; then
        if [[ "${GPU_PASSTHROUGH}" == "1" ]]; then
            die "GPU_PASSTHROUGH=1 but this host exposes no /dev/dri, /dev/kfd or /dev/accel node"
        fi
        msg_warn "no GPU/NPU devices on this host — creating a CPU-only container"
        return 0
    fi
    msg_ok "host devices: ${GPU_DEV_ARGS[*]}"
}

# ── locate or download the LXC template ───────────────────────────────────
locate_template() {
    msg_info "locating ${OS_TYPE}-${OS_VERSION} template"
    local tmpl
    tmpl=$(pveam list "${TEMPLATE_STORAGE}" 2>/dev/null | awk '/'"${OS_TYPE}"'-'"${OS_VERSION}"'/ {print $1; exit}') || true
    if [[ -z "${tmpl:-}" ]]; then
        msg_info "downloading ${OS_TYPE}-${OS_VERSION} template"
        pveam update >/dev/null
        local available
        available=$(pveam available | awk '$1=="system" && $2 ~ /^'"${OS_TYPE}"'-'"${OS_VERSION}"'-standard.*amd64.tar.zst$/ {print $2; exit}')
        [[ -n "${available}" ]] || die "no ${OS_TYPE}-${OS_VERSION} template available from pveam"
        pveam download "${TEMPLATE_STORAGE}" "${available}" >/dev/null
        tmpl=$(pveam list "${TEMPLATE_STORAGE}" 2>/dev/null | awk '/'"${OS_TYPE}"'-'"${OS_VERSION}"'/ {print $1; exit}')
    fi
    [[ -n "${tmpl}" ]] || die "could not locate or download template"
    msg_ok "template: ${tmpl}"
    TEMPLATE="${tmpl}"
}

# ── confirm + countdown ───────────────────────────────────────────────────
print_plan() {
    local privlabel="privileged"
    [[ "${UNPRIVILEGED}" == "1" ]] && privlabel="unprivileged"
    printf "\n%shal0 LXC plan%s\n"  "${BL}" "${CL}"
    printf "  CTID         %s\n" "${CTID}"
    printf "  Hostname     %s\n" "${HOSTNAME}"
    printf "  OS           %s %s (%s)\n" "${OS_TYPE}" "${OS_VERSION}" "${privlabel}"
    printf "  Features     %s\n" "${FEATURES}"
    printf "  Cores/RAM    %s / %sMB\n" "${CORES}" "${RAM_MB}"
    printf "  Disk         %sGB on %s\n" "${DISK_GB}" "${STORAGE}"
    printf "  Network      %s\n" "${NET_CONFIG}"
    if [[ ${#GPU_DEV_ARGS[@]} -gt 0 ]]; then
        printf "  Devices      %s\n" "${GPU_DEV_ARGS[*]}"
    else
        printf "  Devices      none (CPU-only)\n"
    fi
    [[ -n "${MOUNTS}" ]] && printf "  Mounts       %s\n" "${MOUNTS//$'\n'/ }"
    printf "  hal0 channel %s\n" "${HAL0_CHANNEL}"
    if [[ "${UNPRIVILEGED}" != "1" ]]; then
        printf "\n  %sPrivileged container: its root is effectively host root.%s\n" "${YW}" "${CL}"
    fi
    printf "\n  Ctrl-C within 5s to abort.\n\n"
    sleep 5
}

# ── create LXC ────────────────────────────────────────────────────────────
create_lxc() {
    msg_info "creating LXC ${CTID}"

    local args=(
        --hostname "${HOSTNAME}"
        --cores "${CORES}"
        --memory "${RAM_MB}"
        --swap "${SWAP_MB}"
        --rootfs "${STORAGE}:${DISK_GB}"
        --net0 "${NET_CONFIG}"
        --ostype "${OS_TYPE}"
        --unprivileged "${UNPRIVILEGED}"
        --features "${FEATURES}"
        --onboot "${ONBOOT}"
        --tags "${TAGS}"
    )

    [[ -n "${NAMESERVER}" ]]   && args+=(--nameserver "${NAMESERVER}")
    [[ -n "${SEARCHDOMAIN}" ]] && args+=(--searchdomain "${SEARCHDOMAIN}")
    [[ -n "${TIMEZONE}" ]]     && args+=(--timezone "${TIMEZONE}")
    [[ -n "${STARTUP}" ]]      && args+=(--startup "${STARTUP}")

    # Devices go on `pct create` itself so the container never boots once
    # without them (a first boot without /dev/kfd makes hal0 seed CPU slots).
    local i=0 dev
    for dev in ${GPU_DEV_ARGS[@]+"${GPU_DEV_ARGS[@]}"}; do
        args+=(--dev"${i}" "${dev}")
        i=$((i + 1))
    done

    local m
    i=0
    if [[ -n "${MOUNTS}" ]]; then
        while IFS= read -r m; do
            [[ -n "${m}" ]] || continue
            args+=(--mp"${i}" "${m}")
            i=$((i + 1))
        done < <(printf '%s\n' "${MOUNTS//;/$'\n'}")
    fi

    if [[ -n "${PASSWORD}" ]]; then
        args+=(--password "${PASSWORD}")
    fi
    if [[ -n "${SSH_AUTHORIZED_KEYS}" && -f "${SSH_AUTHORIZED_KEYS}" ]]; then
        args+=(--ssh-public-keys "${SSH_AUTHORIZED_KEYS}")
    fi

    pct create "${CTID}" "${TEMPLATE}" "${args[@]}" >/dev/null \
        || die "pct create failed"

    msg_ok "LXC ${CTID} created"
}

# ── raw lxc.* config pct has no flag for ──────────────────────────────────
# cgroup2 device rules, memlock and the apparmor profile can only be
# expressed as raw lxc keys in /etc/pve/lxc/<CTID>.conf. Idempotent: each
# line is appended only if it is not already there, so re-running the
# script against an existing CTID does not duplicate rules.
apply_raw_config() {
    local conf="/etc/pve/lxc/${CTID}.conf"
    [[ -f "${conf}" ]] || die "expected ${conf} after pct create"

    local -a lines=()
    if [[ ${#GPU_CGROUP_MAJ[@]} -gt 0 ]]; then
        local maj
        for maj in $(printf '%s\n' "${GPU_CGROUP_MAJ[@]}" | sort -un); do
            lines+=("lxc.cgroup2.devices.allow: c ${maj}:* rwm")
        done
        # memlock: ROCm pins large host buffers; the default 64K limit makes
        # allocation fail inside a container long before host RAM runs out.
        lines+=("lxc.prlimit.memlock: unlimited")
    fi
    if [[ "${UNPRIVILEGED}" != "1" && ${#GPU_DEV_ARGS[@]} -gt 0 ]]; then
        # Privileged + GPU: the stock lxc-container-default-cgns profile
        # blocks the mounts podman needs for rootful slots (#1563 covers the
        # in-guest half of this).
        lines+=("lxc.apparmor.profile: unconfined")
    fi

    [[ ${#lines[@]} -gt 0 ]] || return 0

    msg_info "applying raw lxc config (${#lines[@]} lines)"
    local line
    for line in "${lines[@]}"; do
        grep -qxF "${line}" "${conf}" || printf '%s\n' "${line}" >> "${conf}"
    done
    msg_ok "raw lxc config applied"
}

start_lxc() {
    msg_info "starting LXC ${CTID}"
    pct start "${CTID}" >/dev/null || die "pct start failed"
    msg_ok "LXC ${CTID} started"
}

# ── wait for network ──────────────────────────────────────────────────────
wait_for_net() {
    msg_info "waiting for network in LXC"
    for _ in $(seq 1 30); do
        if pct exec "${CTID}" -- bash -c 'getent hosts github.com >/dev/null 2>&1'; then
            msg_ok "LXC network up"
            return
        fi
        sleep 2
    done
    die "LXC network did not come up in 60s"
}

# ── verify passthrough landed ─────────────────────────────────────────────
# Cheap, honest gate: if devices were forwarded, they must be visible in the
# guest before the bootstrap runs. Catching it here beats discovering it as
# CPU-seeded slots after a successful-looking install.
verify_devices() {
    [[ ${#GPU_DEV_ARGS[@]} -gt 0 ]] || return 0
    msg_info "verifying devices inside the LXC"
    local missing=""
    local dev path
    for dev in "${GPU_DEV_ARGS[@]}"; do
        path="${dev%%,*}"
        pct exec "${CTID}" -- test -e "${path}" || missing="${missing} ${path}"
    done
    if [[ -n "${missing}" ]]; then
        msg_error "forwarded but not visible in the container:${missing}"
        die "check /etc/pve/lxc/${CTID}.conf, then: pct stop ${CTID} && pct start ${CTID}"
    fi
    msg_ok "devices visible in LXC"
}

# ── install hal0 ──────────────────────────────────────────────────────────
# No package pre-staging beyond a fetcher: bootstrap.sh installs its own
# dependencies (curl/tar/jq/python3 via the guest package manager),
# install.sh installs podman and the python venv stdlib, and bootstrap
# fetches a pinned, sha256-verified cosign. The one thing the guest cannot
# install for us is the tool used to DOWNLOAD the bootstrap — stock LXC
# templates ship without curl — so that single package is staged here.
ensure_fetcher() {
    if pct exec "${CTID}" -- bash -c 'command -v curl >/dev/null 2>&1'; then
        return 0
    fi
    msg_info "installing curl in the LXC (needed to fetch the bootstrap)"
    pct exec "${CTID}" -- bash -c '
        set -e
        export DEBIAN_FRONTEND=noninteractive
        if command -v apt-get >/dev/null 2>&1; then
            apt-get -o DPkg::Lock::Timeout=120 update -qq
            apt-get -o DPkg::Lock::Timeout=120 install -y -qq curl ca-certificates
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y curl ca-certificates
        elif command -v apk >/dev/null 2>&1; then
            apk add curl ca-certificates
        else
            echo "no supported package manager to install curl" >&2
            exit 1
        fi
    ' || die "could not install curl inside the LXC"
    msg_ok "curl available in the LXC"
}

install_hal0() {
    [[ "${RUN_BOOTSTRAP}" == "1" ]] || { msg_warn "RUN_BOOTSTRAP=0 — skipping hal0 install"; return 0; }
    msg_info "running hal0 bootstrap (channel: ${HAL0_CHANNEL})"
    pct exec "${CTID}" -- bash -c "
        set -e
        export HAL0_CHANNEL='${HAL0_CHANNEL}'
        curl -fsSL https://hal0.dev/install.sh | bash
    " || die "hal0 bootstrap failed"
    msg_ok "hal0 installed"
}

# ── final status ──────────────────────────────────────────────────────────
print_access() {
    local ip
    ip=$(pct exec "${CTID}" -- bash -c "ip -4 -o addr show dev eth0 | awk '{print \$4}' | cut -d/ -f1" 2>/dev/null || true)
    [[ -n "${ip}" ]] || ip="<DHCP-pending>"

    printf "\n%shal0 ready%s\n" "${GN}" "${CL}"
    printf "  Dashboard   %shttp://%s:8080%s\n" "${BL}" "${ip}" "${CL}"
    printf "  Enter LXC   %spct enter %s%s\n"  "${BL}" "${CTID}" "${CL}"
    printf "  CLI         %spct exec %s -- hal0 --help%s\n" "${BL}" "${CTID}" "${CL}"
    printf "  Update      %spct exec %s -- hal0 update%s\n" "${BL}" "${CTID}" "${CL}"
    printf "\n"
}

# ── main ──────────────────────────────────────────────────────────────────
main() {
    header
    require_pve
    discover_gpu_devices
    locate_template
    print_plan
    create_lxc
    apply_raw_config
    start_lxc
    wait_for_net
    verify_devices
    ensure_fetcher
    install_hal0
    print_access
}

main "$@"
