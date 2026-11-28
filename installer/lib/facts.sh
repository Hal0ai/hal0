#!/usr/bin/env bash
# installer/lib/facts.sh
#
# Purpose: Gather detection-only facts about this machine and write them to a
#          JSON file a terminal wizard reads (facts file, version 1).
# Expects: Sourced by install.sh after lib/distro.sh and lib/preflight.sh, which
#          provide hal0_lxc_kind, preflight_hindsight_python, distro_id,
#          pkg_mgr and _os_release_field. The caller runs preflight_gpu once
#          (HAL0_GPU_GATE=1) and passes its return code to hal0_facts_gather.
#          Plain bash and coreutils only: this runs before any Python exists.
# Provides: hal0_json_string, hal0_gpu_pool_gb, hal0_install_state,
#          hal0_facts_gather, hal0_facts_write.
# Safety:  Every call here only reads. Nothing installs a package, pulls an
#          image, or changes a device or group. The functions of preflight.sh
#          that can do so (resolve_*_python, preflight_venv, preflight_git,
#          preflight_container_runtime, resolve_node, the container smoke test)
#          are never called.
# Test seams (read only here): HAL0_FACTS_SYS_ROOT (prefix for /sys),
#          HAL0_FACTS_VAR_DIR (default /var/lib/hal0), HAL0_FACTS_FHS_ROOT
#          (directory holding the `current` link; default ${HAL0_PREFIX:-/usr/lib/hal0}).

# Print VALUE as a JSON string literal. Control characters are dropped, then
# backslash and double quote are escaped.
hal0_json_string() {
    local v
    v="$(printf '%s' "${1-}" | LC_ALL=C tr -d '\000-\037\177')"
    v="${v//\\/\\\\}"
    v="${v//\"/\\\"}"
    printf '"%s"' "${v}"
}

# Largest of mem_info_vram_total and mem_info_gtt_total (bytes) over every
# card's device directory, in GiB to one decimal. Prints nothing when none is
# readable. Same rule as src/hal0/hardware/probe.py.
hal0_gpu_pool_gb() {
    local drm="${HAL0_FACTS_SYS_ROOT:-}/sys/class/drm"
    local best=0 f v
    for f in "${drm}"/card*/device/mem_info_vram_total "${drm}"/card*/device/mem_info_gtt_total; do
        [[ -r "${f}" ]] || continue
        v=""
        read -r v < "${f}" 2>/dev/null || true
        [[ "${v}" =~ ^[0-9]{1,15}$ ]] || continue
        v=$((10#${v}))
        ((v > best)) && best="${v}"
    done
    ((best > 0)) || return 0
    local tenths=$(((best * 10 + (1 << 29)) >> 30))
    printf '%d.%d\n' $((tenths / 10)) $((tenths % 10))
}

_hal0_facts_fhs_root() { printf '%s' "${HAL0_FACTS_FHS_ROOT:-${HAL0_PREFIX:-/usr/lib/hal0}}"; }

# fresh | upgrade | unfinished.
hal0_install_state() {
    if [[ -e "${HAL0_FACTS_VAR_DIR:-/var/lib/hal0}/.install-in-progress" ]]; then
        printf 'unfinished\n'
        return 0
    fi
    local cur
    cur="$(_hal0_facts_fhs_root)/current"
    if [[ -e "${cur}" || -L "${cur}" ]]; then
        printf 'upgrade\n'
    else
        printf 'fresh\n'
    fi
}

# Contents of current/VERSION (first line), or nothing.
_hal0_install_version() {
    local f v=""
    f="$(_hal0_facts_fhs_root)/current/VERSION"
    [[ -r "${f}" ]] || return 0
    read -r v < "${f}" 2>/dev/null || true
    printf '%s' "${v}"
}

# Set FACT_* from detection. $1 is the return code of preflight_gpu, already run.
hal0_facts_gather() {
    local gpu_rc="${1:-0}"

    FACT_DISTRO_ID="$(distro_id 2>/dev/null || true)"
    FACT_DISTRO_ID="${FACT_DISTRO_ID:-unknown}"
    FACT_DISTRO_RELEASE="$(_os_release_field VERSION_ID 2>/dev/null || true)"
    FACT_PKG_MGR="$(pkg_mgr 2>/dev/null || true)"

    local kind
    kind="$(hal0_lxc_kind 2>/dev/null || true)"
    case "${kind}" in
        none | box | lxc-privileged | lxc-unprivileged) FACT_CONTAINER="${kind}" ;;
        *) FACT_CONTAINER="other" ;;
    esac

    FACT_PODMAN_PRESENT=false
    FACT_PODMAN_RUNS=false
    if command -v podman >/dev/null 2>&1; then
        FACT_PODMAN_PRESENT=true
        podman info >/dev/null 2>&1 && FACT_PODMAN_RUNS=true
    fi

    case "${gpu_rc}" in
        3) FACT_GPU_OUTCOME=stop; FACT_GPU_REASON=render_gid_unmapped ;;
        4) FACT_GPU_OUTCOME=cpu_only_needs_consent; FACT_GPU_REASON=no_gpu_device ;;
        5) FACT_GPU_OUTCOME=cpu_only_needs_consent; FACT_GPU_REASON=no_kfd ;;
        6) FACT_GPU_OUTCOME=usable; FACT_GPU_REASON=kfd_group_mismatch ;;
        *) FACT_GPU_OUTCOME=usable; FACT_GPU_REASON="" ;;
    esac

    FACT_GPU_VENDOR=""
    FACT_GPU_POOL_GB=""
    if [[ -d "${HAL0_FACTS_SYS_ROOT:-}/sys/module/amdgpu" ]]; then
        FACT_GPU_VENDOR=amd
        FACT_GPU_POOL_GB="$(hal0_gpu_pool_gb)"
    fi

    # NPU: node present and /sys still has the device (preflight_gpu's stale test).
    FACT_NPU_DEVICE=false
    local node="${HAL0_NPU_ACCEL_NODE:-/dev/accel/accel0}"
    local sysacc="${HAL0_NPU_SYS_ACCEL:-/sys/class/accel/accel0}"
    [[ -e "${node}" && -d "${sysacc}/device" ]] && FACT_NPU_DEVICE=true
    FACT_NPU_MEMLOCK_OK=false
    [[ "$(ulimit -Hl 2>/dev/null)" == unlimited ]] && FACT_NPU_MEMLOCK_OK=true
    # The NPU step installs the host FastFlowLM .deb on apt hosts that are
    # Ubuntu 24.04 or newer or Debian (install.sh NPU prerequisites step).
    FACT_NPU_FLM_BUILD=false
    if [[ "${FACT_PKG_MGR}" == apt-get ]]; then
        case "${FACT_DISTRO_ID}" in
            debian) FACT_NPU_FLM_BUILD=true ;;
            ubuntu)
                [[ -n "${FACT_DISTRO_RELEASE}" ]] \
                    && [[ "$(printf '%s\n%s\n' 24.04 "${FACT_DISTRO_RELEASE}" | sort -V | head -1)" == 24.04 ]] \
                    && FACT_NPU_FLM_BUILD=true
                ;;
        esac
    fi

    FACT_INSTALL_STATE="$(hal0_install_state)"
    FACT_INSTALL_VERSION=""
    [[ "${FACT_INSTALL_STATE}" == upgrade ]] && FACT_INSTALL_VERSION="$(_hal0_install_version)"

    FACT_MEMORY_PYTHON=false
    # Detection only: the wrapper never installs; a skip request must not read as "usable".
    (HAL0_SKIP_HINDSIGHT=0 preflight_hindsight_python) >/dev/null 2>&1 && FACT_MEMORY_PYTHON=true
    return 0
}

# JSON string, or null when empty.
_hal0_facts_str_or_null() {
    if [[ -n "${1-}" ]]; then hal0_json_string "$1"; else printf 'null'; fi
}

# Write the version 1 facts file from FACT_*, atomically, mode 0644.
hal0_facts_write() {
    local path="$1" dir tmp
    dir="$(dirname "${path}")"
    if [[ ! -d "${dir}" ]]; then
        mkdir -p "${dir}" 2>/dev/null && chmod 0755 "${dir}" || return 1
    fi
    tmp="$(mktemp "${dir}/.facts.XXXXXX")" || return 1
    local pool=null
    [[ -n "${FACT_GPU_POOL_GB:-}" ]] && pool="${FACT_GPU_POOL_GB}"
    {
        printf '{\n'
        printf '  "version": 1,\n'
        printf '  "distro": {"id": %s, "release": %s, "package_manager": %s},\n' \
            "$(hal0_json_string "${FACT_DISTRO_ID:-unknown}")" \
            "$(_hal0_facts_str_or_null "${FACT_DISTRO_RELEASE:-}")" \
            "$(_hal0_facts_str_or_null "${FACT_PKG_MGR:-}")"
        printf '  "container": "%s",\n' "${FACT_CONTAINER:-other}"
        printf '  "podman": {"present": %s, "runs": %s},\n' \
            "${FACT_PODMAN_PRESENT:-false}" "${FACT_PODMAN_RUNS:-false}"
        printf '  "gpu": {"outcome": "%s", "reason": %s, "vendor": %s, "pool_gb": %s},\n' \
            "${FACT_GPU_OUTCOME:-usable}" \
            "$(_hal0_facts_str_or_null "${FACT_GPU_REASON:-}")" \
            "$(_hal0_facts_str_or_null "${FACT_GPU_VENDOR:-}")" \
            "${pool}"
        printf '  "npu": {"device": %s, "memlock_ok": %s, "flm_build": %s},\n' \
            "${FACT_NPU_DEVICE:-false}" "${FACT_NPU_MEMLOCK_OK:-false}" "${FACT_NPU_FLM_BUILD:-false}"
        printf '  "install": {"state": "%s", "version": %s},\n' \
            "${FACT_INSTALL_STATE:-fresh}" \
            "$(_hal0_facts_str_or_null "${FACT_INSTALL_VERSION:-}")"
        printf '  "memory_python": %s\n' "${FACT_MEMORY_PYTHON:-false}"
        printf '}\n'
    } > "${tmp}" || { rm -f "${tmp}"; return 1; }
    chmod 0644 "${tmp}" || { rm -f "${tmp}"; return 1; }
    mv -f "${tmp}" "${path}" || { rm -f "${tmp}"; return 1; }
    return 0
}
