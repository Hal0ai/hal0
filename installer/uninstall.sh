#!/usr/bin/env bash
# hal0 uninstaller
#
# Usage:
#   sudo bash uninstall.sh             # stops services, prompts before deleting data
#   sudo bash uninstall.sh --keep-data # leaves config/state intact
#   sudo bash uninstall.sh --force     # skip confirmation prompt
#        bash uninstall.sh --dev       # remove dev-mode tree under $PWD/.hal0ai
#   HAL0_FORCE=1 sudo bash uninstall.sh # equivalent to --force
#
# What it removes (system mode): the hal0-api / hal0-openwebui /
# hal0-slot@ / hal0-agent@ / hermes-gateway units (+ their drop-in
# dirs), the install PREFIX (/opt/hal0), the hal0 system user AND group,
# and the fastflowlm .deb. Config + state (/etc/hal0, /var/lib/hal0) go
# too unless --keep-data. Idempotent — never hard-fails on an
# already-gone target.
#
# Legacy Lemonade cleanup: pre-Phase-E installs shipped a hal0-lemonade
# daemon (/opt/lemonade bundle + ppa:lemonade-team/stable apt source).
# The labelled "legacy Lemonade cleanup" blocks below keep removing that
# state from upgraded boxes; they are no-ops on fresh installs.
#
# Env overrides:
#   HAL0_PREFIX        installation root (default /opt/hal0; --dev defaults
#                      to $PWD/.hal0ai). When set, --dev path layout is used
#                      so the uninstall mirrors the matching install.sh run.
#   HAL0_LEMONADE_PREFIX  legacy Lemonade runtime root to remove (default
#                      /opt/lemonade; ignored in --dev mode)
#   HAL0_PATH_LINK     PATH symlink to remove (default /usr/local/bin/hal0;
#                      ignored in --dev mode)

set -euo pipefail
IFS=$'\n\t'

# ── Colour helpers ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
    BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; YELLOW=''; GREEN=''; BOLD=''; RESET=''
fi

info()  { printf "${GREEN}✔${RESET}  %s\n" "$*"; }
warn()  { printf "${YELLOW}!${RESET}  %s\n" "$*" >&2; }
error() { printf "${RED}✗${RESET}  %s\n" "$*" >&2; }
step()  { printf "\n${BOLD}── %s${RESET}\n" "$*"; }
die()   { error "$*"; exit 1; }

# ── Parse flags ───────────────────────────────────────────────────────────────
KEEP_DATA=0
DEV_MODE=0
HAL0_FORCE="${HAL0_FORCE:-0}"

for arg in "$@"; do
    case "$arg" in
        --keep-data) KEEP_DATA=1 ;;
        --force)     HAL0_FORCE=1 ;;
        --dev)       DEV_MODE=1 ;;
        --help|-h)
            cat <<'EOF'
Usage: uninstall.sh [--keep-data] [--force] [--dev]
  --keep-data  preserve config + state directories
  --force      skip data-deletion confirmation prompt
  --dev        remove the dev-mode tree under $PWD/.hal0ai (or $HAL0_PREFIX);
               no systemd / system-user / PATH-symlink operations performed.
               Required to undo `install.sh --dev` without clobbering a host
               install.
  HAL0_FORCE=1 env var is equivalent to --force
  HAL0_PREFIX  override install root (default /opt/hal0, or $PWD/.hal0ai
               when --dev). Path layout always mirrors install.sh.
EOF
            exit 0
            ;;
        *) warn "Unknown flag: $arg (ignored)" ;;
    esac
done

# ── Compute paths (mirrors install.sh:89-100) ─────────────────────────────────
if [[ "${DEV_MODE}" -eq 1 ]]; then
    PREFIX="${HAL0_PREFIX:-${PWD}/.hal0ai}"
    ETC_DIR="${PREFIX}/etc/hal0"
    VAR_DIR="${PREFIX}/var/lib/hal0"
    UNIT_DIR="${PREFIX}/etc/systemd/system"
    LIB_DIR="${PREFIX}/usr/lib/hal0"
    info "Dev mode — all paths under ${PREFIX}"
else
    PREFIX="${HAL0_PREFIX:-/opt/hal0}"
    ETC_DIR="/etc/hal0"
    VAR_DIR="/var/lib/hal0"
    UNIT_DIR="/etc/systemd/system"
    LIB_DIR="/usr/lib/hal0"
fi

# ── Root check (system mode only) ─────────────────────────────────────────────
if [[ "${DEV_MODE}" -eq 0 && "$(id -u)" -ne 0 ]]; then
    if command -v sudo &>/dev/null; then
        exec sudo bash "$0" "$@"
    else
        die "Must run as root or have sudo available."
    fi
fi

trap 'error "Uninstall failed at line ${LINENO}."; exit 1' ERR

# ── Stop + disable units (system mode only) ───────────────────────────────────
if [[ "${DEV_MODE}" -eq 0 ]]; then
    step "Stopping services"

    # Static units the installer writes, plus legacy units kept for
    # old-install cleanup: hal0-caddy (pre-v0.3 auth/Caddy removal) and
    # hal0-lemonade (── legacy Lemonade cleanup (pre-Phase-E installs) ──).
    UNITS=(hal0-api hal0-openwebui hal0-caddy hal0-lemonade \
           hal0-agent@hermes hermes-gateway)

    # Discover any running slot instances
    while IFS= read -r UNIT; do
        UNITS+=("${UNIT%.service}")
    done < <(systemctl list-units --type=service --all --no-legend 2>/dev/null \
        | awk '{print $1}' | grep '^hal0-slot@' || true)

    # Discover any other hal0-agent@ instances (besides hermes, added above)
    # so a box that bootstrapped extra agents tears them all down.
    while IFS= read -r UNIT; do
        [[ "${UNIT}" == "hal0-agent@hermes.service" ]] && continue
        UNITS+=("${UNIT%.service}")
    done < <(systemctl list-units --type=service --all --no-legend 2>/dev/null \
        | awk '{print $1}' | grep '^hal0-agent@' || true)

    for UNIT in "${UNITS[@]}"; do
        if systemctl is-active "${UNIT}" &>/dev/null 2>&1; then
            if systemctl stop "${UNIT}"; then
                info "Stopped ${UNIT}"
            else
                warn "Could not stop ${UNIT} (may already be stopped)"
            fi
        else
            info "${UNIT} not running"
        fi
        if systemctl is-enabled "${UNIT}" &>/dev/null 2>&1; then
            if systemctl disable "${UNIT}"; then
                info "Disabled ${UNIT}"
            else
                warn "Could not disable ${UNIT}"
            fi
        fi
    done

    # Stop OpenWebUI container explicitly in case docker didn't get the memo
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^hal0-openwebui$'; then
        docker stop hal0-openwebui &>/dev/null || true
        info "Stopped Docker container hal0-openwebui"
    fi
else
    step "Skipping systemd / docker stop (dev mode)"
fi

# ── Bundled agents (Phase 8, ADR-0004 §6) ─────────────────────────────────────
# Iterates /etc/hal0/agents/*.toml, calls each agent's uninstall
# companion script (dropped by installer/agents/<name>.sh during
# install), then removes the per-agent data dirs. Runs BEFORE the
# code/data sweep below because the companion scripts live under
# ${VAR_DIR}/agents/<name>/ — wiping that first would orphan the
# upstream packages (npm/cargo/hermes-agent).
uninstall_agents() {
    local AGENTS_ETC="${ETC_DIR}/agents"
    local AGENTS_VAR="${VAR_DIR}/agents"
    if [[ ! -d "${AGENTS_ETC}" ]] && [[ ! -d "${AGENTS_VAR}" ]]; then
        return 0
    fi
    step "Bundled agents"
    if [[ -d "${AGENTS_ETC}" ]]; then
        for AGENT_TOML in "${AGENTS_ETC}"/*.toml; do
            [[ -e "${AGENT_TOML}" ]] || continue
            local AGENT_NAME
            AGENT_NAME="$(basename "${AGENT_TOML}" .toml)"
            local COMPANION="${AGENTS_VAR}/${AGENT_NAME}/uninstall.sh"
            if [[ -x "${COMPANION}" ]]; then
                if bash "${COMPANION}"; then
                    info "Ran uninstall companion for ${AGENT_NAME}"
                else
                    warn "Companion uninstall for ${AGENT_NAME} returned non-zero (continuing)"
                fi
            else
                info "No uninstall companion for ${AGENT_NAME} (already removed?)"
            fi
        done
    fi
    if [[ -d "${AGENTS_VAR}" ]]; then
        rm -rf "${AGENTS_VAR}"
        info "Removed ${AGENTS_VAR}"
    fi
    if [[ -d "${AGENTS_ETC}" ]]; then
        rm -rf "${AGENTS_ETC}"
        info "Removed ${AGENTS_ETC}"
    fi
}

uninstall_agents

# ── Remove unit files ─────────────────────────────────────────────────────────
step "Removing systemd units"

# hal0-caddy + hal0-lemonade entries are legacy: not written by the
# current installer, swept so upgraded boxes come clean.
# (hal0-lemonade: ── legacy Lemonade cleanup (pre-Phase-E installs) ──)
for UNIT_FILE in \
    "${UNIT_DIR}/hal0-api.service" \
    "${UNIT_DIR}/hal0-openwebui.service" \
    "${UNIT_DIR}/hal0-caddy.service" \
    "${UNIT_DIR}/hal0-lemonade.service" \
    "${UNIT_DIR}/hal0-agent@.service" \
    "${UNIT_DIR}/hermes-gateway.service"
do
    if [[ -f "${UNIT_FILE}" ]]; then
        rm -f "${UNIT_FILE}"
        info "Removed ${UNIT_FILE}"
    else
        info "${UNIT_FILE} not present"
    fi
done

# Drop-in directories the installer creates alongside the units:
#   hal0-agent@hermes.service.d/  — override.conf (hermes-specific env)
#   hermes-gateway.service.d/     — 10-hal0-secrets.conf (bootstrap secrets)
#   hal0-lemonade.service.d/      — kfd-perms.conf + 20-vulkan-radv.conf
#     (── legacy Lemonade cleanup (pre-Phase-E installs) ──)
for DROPIN_DIR in \
    "${UNIT_DIR}/hal0-lemonade.service.d" \
    "${UNIT_DIR}/hal0-agent@hermes.service.d" \
    "${UNIT_DIR}/hermes-gateway.service.d"
do
    if [[ -d "${DROPIN_DIR}" ]]; then
        rm -rf "${DROPIN_DIR}"
        info "Removed ${DROPIN_DIR}"
    fi
done

if [[ "${DEV_MODE}" -eq 0 ]]; then
    systemctl daemon-reload
    info "systemctl daemon-reload done"
fi

# ── Remove code ───────────────────────────────────────────────────────────────
step "Removing code"

# /usr/lib/hal0 — Hermes hooks + any FHS-migrated tree (#495). The
# `[[ -L .../current ]]` test catches the editable→FHS layout where
# `current` is a symlink into a versioned dir.
if [[ -d "${LIB_DIR}" ]] || [[ -L "${LIB_DIR}/current" ]]; then
    rm -rf "${LIB_DIR}"
    info "Removed ${LIB_DIR}"
else
    info "${LIB_DIR} not present"
fi

# The install PREFIX (default /opt/hal0). install.sh rsyncs the source
# tree + builds the venv here; before #508 the uninstaller only removed
# LIB_DIR and left this behind. In --dev mode PREFIX is handled by the
# dev-tree cleanup block lower down (it lives under $PWD/.hal0ai), so we
# only sweep it here for a system install.
if [[ "${DEV_MODE}" -eq 0 ]]; then
    if [[ -d "${PREFIX}" ]]; then
        rm -rf "${PREFIX}"
        info "Removed ${PREFIX}"
    else
        info "${PREFIX} not present"
    fi
fi

# ── legacy Lemonade cleanup (pre-Phase-E installs): /opt/lemonade ─────────────
# The current installer never writes this tree; pre-Phase-E installs
# extracted the Lemonade embeddable bundle (lemond + lemonade CLI +
# resources/, hundreds of MB plus lazily-pulled llama.cpp / ROCm backend
# binaries) here. Expensive to re-fetch, so we gate removal behind its
# own confirmation unless --force / HAL0_FORCE=1. System mode only. The
# legacy cache dir under ${VAR_DIR}/lemonade rides along with the
# data-dir removal below (respecting --keep-data).
LEMONADE_PREFIX="${HAL0_LEMONADE_PREFIX:-/opt/lemonade}"
if [[ "${DEV_MODE}" -eq 0 && -d "${LEMONADE_PREFIX}" ]]; then
    step "Legacy Lemonade runtime"
    if [[ -d "${LEMONADE_PREFIX}" ]]; then
        REMOVE_LEMONADE=1
        if [[ "${HAL0_FORCE}" -ne 1 ]]; then
            printf '\n%b%bLemonade runtime%b at %s is expensive to re-download\n' \
                "${YELLOW}" "${BOLD}" "${RESET}" "${LEMONADE_PREFIX}"
            printf '(the embeddable tarball + backend binaries are hundreds of MB).\n'
            printf 'Remove it? Type %byes%b to delete, anything else to keep: ' \
                "${BOLD}" "${RESET}"
            read -r LEMONADE_CONFIRM || LEMONADE_CONFIRM=""
            if [[ "${LEMONADE_CONFIRM}" != "yes" ]]; then
                REMOVE_LEMONADE=0
            fi
        fi
        if [[ "${REMOVE_LEMONADE}" -eq 1 ]]; then
            rm -rf "${LEMONADE_PREFIX}"
            info "Removed ${LEMONADE_PREFIX}"
        else
            warn "Keeping ${LEMONADE_PREFIX} (re-run with --force to remove)"
        fi
    else
        info "${LEMONADE_PREFIX} not present"
    fi
fi

# ── PATH symlink (system mode only) ───────────────────────────────────────────
if [[ "${DEV_MODE}" -eq 0 ]]; then
    HAL0_PATH_LINK="${HAL0_PATH_LINK:-/usr/local/bin/hal0}"
    if [[ -L "${HAL0_PATH_LINK}" ]] || [[ -f "${HAL0_PATH_LINK}" ]]; then
        rm -f "${HAL0_PATH_LINK}"
        info "Removed ${HAL0_PATH_LINK}"
    else
        info "${HAL0_PATH_LINK} not present"
    fi
    # hal0-agent shim symlink (created alongside hal0 by install.sh)
    HAL0_AGENT_LINK="$(dirname "${HAL0_PATH_LINK}")/hal0-agent"
    if [[ -L "${HAL0_AGENT_LINK}" ]] || [[ -f "${HAL0_AGENT_LINK}" ]]; then
        rm -f "${HAL0_AGENT_LINK}"
        info "Removed ${HAL0_AGENT_LINK}"
    fi
fi

# ── Data dirs ─────────────────────────────────────────────────────────────────
step "Data directories"

if [[ "${KEEP_DATA}" -eq 1 ]]; then
    warn "Keeping data dirs (--keep-data):"
    warn "  ${ETC_DIR}      — config"
    warn "  ${VAR_DIR}  — models, registry, openwebui state"
    warn "Re-run install.sh to restore services using this data."
else
    # Confirmation unless forced
    if [[ "${HAL0_FORCE}" -ne 1 ]]; then
        # %b interprets backslash escapes in the substituted strings —
        # the BOLD/RED/RESET helpers are stored as literal `\033[...m`
        # sequences (line 23), so %s would print them verbatim instead of
        # invoking the terminal's SGR. Same gotcha bit the WARNING + the
        # "Type DELETE" prompt; both now use %b.
        printf '\n%b%bWARNING:%b This will delete:\n' "${RED}" "${BOLD}" "${RESET}"
        printf '  %s      (config + slot definitions)\n' "${ETC_DIR}"
        printf '  %s  (models, registry, openwebui state)\n\n' "${VAR_DIR}"
        printf 'Type %bDELETE%b to confirm, or Ctrl-C to cancel: ' "${BOLD}" "${RESET}"
        read -r CONFIRM
        if [[ "${CONFIRM}" != "DELETE" ]]; then
            warn "Aborted — data preserved."
            exit 0
        fi
    fi

    for DATA_DIR in "${ETC_DIR}" "${VAR_DIR}"; do
        if [[ -d "${DATA_DIR}" ]]; then
            rm -rf "${DATA_DIR}"
            info "Removed ${DATA_DIR}"
        else
            info "${DATA_DIR} not present"
        fi
    done
fi

# ── First-run claim lockfile ──────────────────────────────────────────────────
# Always cleaned up — even on --keep-data — because the lockfile only has
# meaning during the first-run claim window, and a leftover OTP after an
# uninstall is a credential lying around with no semantics. Belt-and-braces
# even though the file lives under ${VAR_DIR} (which we just rm -rf'd
# when --keep-data was NOT passed).
FIRST_RUN_LOCK="${VAR_DIR}/.first-run.lock"
if [[ -f "${FIRST_RUN_LOCK}" ]]; then
    rm -f "${FIRST_RUN_LOCK}"
    info "Removed ${FIRST_RUN_LOCK}"
fi

# ── Dev-mode tree cleanup ─────────────────────────────────────────────────────
# After removing the FHS-mirror subdirs, drop the now-empty PREFIX itself so
# `install.sh --dev` can be re-run cleanly. Only do this if PREFIX is otherwise
# empty (don't blow away unrelated user files that happened to share the dir).
if [[ "${DEV_MODE}" -eq 1 && -d "${PREFIX}" ]]; then
    # Remove obvious leftover dirs first
    for D in "${PREFIX}/etc" "${PREFIX}/var" "${PREFIX}/usr" "${PREFIX}/.venv"; do
        [[ -d "${D}" ]] && rm -rf "${D}" && info "Removed ${D}"
    done
    if [[ -z "$(ls -A "${PREFIX}" 2>/dev/null)" ]]; then
        rmdir "${PREFIX}"
        info "Removed empty ${PREFIX}"
    else
        warn "${PREFIX} not empty — leaving in place"
    fi
fi

# ── System user + group (system mode only) ────────────────────────────────────
if [[ "${DEV_MODE}" -eq 0 ]]; then
    step "System user + group"

    if id hal0 &>/dev/null 2>&1; then
        if userdel hal0; then
            info "Removed system user hal0"
        else
            warn "Could not remove hal0 user"
        fi
    else
        info "System user hal0 not present"
    fi

    # userdel removes the user but NOT the matching primary group (which
    # install.sh creates separately via `groupadd --system hal0`). Remove
    # it explicitly. groupdel refuses if the group is still the primary
    # group of a remaining user, or if it's gone — both are fine to ignore
    # here (the user was just deleted, and a missing group is the goal).
    if getent group hal0 &>/dev/null 2>&1; then
        if groupdel hal0 2>/dev/null; then
            info "Removed system group hal0"
        else
            warn "Could not remove group hal0 (still in use or already gone)"
        fi
    else
        info "System group hal0 not present"
    fi
fi

# ── FLM .deb (apt hosts only) ─────────────────────────────────────────────────
# install.sh installs the `fastflowlm` .deb on Debian/Ubuntu hosts (NPU
# host probe + device sanity). Reverse it. Guarded by `command -v apt-get`
# so non-apt hosts skip cleanly, and every step is fail-soft — a leftover
# package must never abort the uninstall. dev mode never touched apt, so
# it skips this entirely.
if [[ "${DEV_MODE}" -eq 0 ]] && command -v apt-get &>/dev/null 2>&1; then
    step "FLM package"

    if dpkg-query -W -f='${Status}' fastflowlm 2>/dev/null | grep -q "install ok installed"; then
        if apt-get remove -y fastflowlm &>/dev/null; then
            info "Removed fastflowlm package"
        else
            warn "Could not remove fastflowlm package (continuing)"
        fi
    else
        info "fastflowlm package not installed"
    fi

    # ── legacy Lemonade cleanup (pre-Phase-E installs): apt PPA ──────────────
    # Pre-Phase-E installs added ppa:lemonade-team/stable for libxrt-npu2.
    # The current installer never adds it; remove it from upgraded boxes.
    # Cheap existence guard: only call add-apt-repository when a matching
    # sources entry is actually present.
    if compgen -G "/etc/apt/sources.list.d/*lemonade*" >/dev/null 2>&1; then
        if command -v add-apt-repository &>/dev/null 2>&1; then
            if add-apt-repository --remove -y ppa:lemonade-team/stable &>/dev/null; then
                info "Removed legacy ppa:lemonade-team/stable"
            else
                warn "Could not remove legacy ppa:lemonade-team/stable (may already be gone)"
            fi
        else
            warn "add-apt-repository not present — leaving the legacy Lemonade PPA in place"
        fi
    else
        info "no legacy Lemonade PPA present"
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
printf '\n%s%shal0 uninstalled.%s\n' "${GREEN}" "${BOLD}" "${RESET}"
if [[ "${KEEP_DATA}" -eq 1 ]]; then
    printf '  Config + data preserved in %s and %s.\n\n' "${ETC_DIR}" "${VAR_DIR}"
fi
