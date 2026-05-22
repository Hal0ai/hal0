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
# Env overrides:
#   HAL0_PREFIX        installation root (default /opt/hal0; --dev defaults
#                      to $PWD/.hal0ai). When set, --dev path layout is used
#                      so the uninstall mirrors the matching install.sh run.
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

    UNITS=(hal0-api hal0-openwebui hal0-caddy)

    # Discover any running slot instances
    while IFS= read -r UNIT; do
        UNITS+=("${UNIT%.service}")
    done < <(systemctl list-units --type=service --all --no-legend 2>/dev/null \
        | awk '{print $1}' | grep '^hal0-slot@' || true)

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

for UNIT_FILE in \
    "${UNIT_DIR}/hal0-api.service" \
    "${UNIT_DIR}/hal0-openwebui.service" \
    "${UNIT_DIR}/hal0-caddy.service" \
    "${UNIT_DIR}/hal0-slot@.service"
do
    if [[ -f "${UNIT_FILE}" ]]; then
        rm -f "${UNIT_FILE}"
        info "Removed ${UNIT_FILE}"
    else
        info "${UNIT_FILE} not present"
    fi
done

if [[ "${DEV_MODE}" -eq 0 ]]; then
    systemctl daemon-reload
    info "systemctl daemon-reload done"
fi

# ── Remove code ───────────────────────────────────────────────────────────────
step "Removing ${LIB_DIR}"

if [[ -d "${LIB_DIR}" ]] || [[ -L "${LIB_DIR}/current" ]]; then
    rm -rf "${LIB_DIR}"
    info "Removed ${LIB_DIR}"
else
    info "${LIB_DIR} not present"
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

# ── System user (system mode only) ────────────────────────────────────────────
if [[ "${DEV_MODE}" -eq 0 ]]; then
    step "System user"

    if id hal0 &>/dev/null 2>&1; then
        if userdel hal0; then
            info "Removed system user hal0"
        else
            warn "Could not remove hal0 user"
        fi
    else
        info "System user hal0 not present"
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
printf '\n%s%shal0 uninstalled.%s\n' "${GREEN}" "${BOLD}" "${RESET}"
if [[ "${KEEP_DATA}" -eq 1 ]]; then
    printf '  Config + data preserved in %s and %s.\n\n' "${ETC_DIR}" "${VAR_DIR}"
fi
