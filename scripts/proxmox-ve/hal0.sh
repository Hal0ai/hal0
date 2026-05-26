#!/usr/bin/env bash
#
# hal0 Proxmox VE installer — creates an unprivileged Debian 13 LXC and
# runs the standard hal0 bootstrap inside it.
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
# Hardware-agnostic. For Strix Halo iGPU/NPU passthrough see the
# privileged-LXC recipe at https://github.com/Hal0ai/hal0 — this script
# intentionally targets the generic homelab case.

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
NET_CONFIG="${NET_CONFIG:-name=eth0,bridge=${BRIDGE},ip=dhcp}"
OS_TYPE="${OS_TYPE:-debian}"
OS_VERSION="${OS_VERSION:-13}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
UNPRIVILEGED="${UNPRIVILEGED:-1}"
PASSWORD="${PASSWORD:-}"  # empty = no root password set; use `pct enter` from host
SSH_AUTHORIZED_KEYS="${SSH_AUTHORIZED_KEYS:-}"
HAL0_CHANNEL="${HAL0_CHANNEL:-stable}"
INSTALL_COSIGN="${INSTALL_COSIGN:-1}"
COSIGN_VERSION="${COSIGN_VERSION:-v3.0.0}"

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
    NET_CONFIG="name=eth0,bridge=${BRIDGE},ip=dhcp"
    PASSWORD=$(whiptail --passwordbox "Root password (blank = none, use pct enter)" 8 60 "" --title "hal0 LXC" 3>&1 1>&2 2>&3) || exit 1

    if whiptail --yesno "Use unprivileged container? (recommended)" 8 60 --title "hal0 LXC"; then
        UNPRIVILEGED=1
    else
        UNPRIVILEGED=0
    fi
}

[[ $ADVANCED -eq 1 ]] && prompt_advanced

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
    printf "\n%shal0 LXC plan%s\n"  "${BL}" "${CL}"
    printf "  CTID         %s\n" "${CTID}"
    printf "  Hostname     %s\n" "${HOSTNAME}"
    printf "  OS           %s %s (unprivileged=%s)\n" "${OS_TYPE}" "${OS_VERSION}" "${UNPRIVILEGED}"
    printf "  Cores/RAM    %s / %sMB\n" "${CORES}" "${RAM_MB}"
    printf "  Disk         %sGB on %s\n" "${DISK_GB}" "${STORAGE}"
    printf "  Network      %s\n" "${NET_CONFIG}"
    printf "  hal0 channel %s\n" "${HAL0_CHANNEL}"
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
        --features "nesting=1"
        --onboot 1
        --start 1
    )

    if [[ -n "${PASSWORD}" ]]; then
        args+=(--password "${PASSWORD}")
    fi
    if [[ -n "${SSH_AUTHORIZED_KEYS}" && -f "${SSH_AUTHORIZED_KEYS}" ]]; then
        args+=(--ssh-public-keys "${SSH_AUTHORIZED_KEYS}")
    fi

    pct create "${CTID}" "${TEMPLATE}" "${args[@]}" >/dev/null \
        || die "pct create failed"

    msg_ok "LXC ${CTID} created and started"
}

# ── wait for network ──────────────────────────────────────────────────────
wait_for_net() {
    msg_info "waiting for network in LXC"
    for _ in $(seq 1 30); do
        if pct exec "${CTID}" -- bash -c 'getent hosts deb.debian.org >/dev/null 2>&1'; then
            msg_ok "LXC network up"
            return
        fi
        sleep 2
    done
    die "LXC network did not come up in 60s"
}

# ── install deps + cosign + hal0 ──────────────────────────────────────────
install_inside_lxc() {
    msg_info "installing base packages (curl, tar, jq, python3, ca-certificates)"
    pct exec "${CTID}" -- bash -c '
        set -e
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq curl ca-certificates tar jq python3 sudo >/dev/null
    ' || die "apt install failed"
    msg_ok "base packages installed"

    if [[ "${INSTALL_COSIGN}" == "1" ]]; then
        msg_info "installing cosign ${COSIGN_VERSION}"
        pct exec "${CTID}" -- bash -c "
            set -e
            curl -fsSL -o /usr/local/bin/cosign \
                'https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-amd64'
            chmod +x /usr/local/bin/cosign
        " || die "cosign install failed"
        msg_ok "cosign ${COSIGN_VERSION} installed"
    fi

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
    locate_template
    print_plan
    create_lxc
    wait_for_net
    install_inside_lxc
    print_access
}

main "$@"
