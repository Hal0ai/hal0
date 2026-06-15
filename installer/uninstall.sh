#!/usr/bin/env bash
# hal0 uninstaller
#
# Usage:
#   sudo bash uninstall.sh             # conservative: stop+remove code/units/
#                                      #   venvs/binaries/containers; KEEP
#                                      #   /etc/hal0 + /var/lib/hal0 (models)
#   sudo bash uninstall.sh --purge     # clean slate: ALSO delete /etc/hal0,
#                                      #   /var/lib/hal0, the hal0 user+group,
#                                      #   podman images and the fastflowlm .deb
#   sudo bash uninstall.sh --keep-data # legacy alias for the conservative default
#   sudo bash uninstall.sh --force     # skip the --purge DELETE confirmation
#        bash uninstall.sh --dev       # remove dev-mode tree under $PWD/.hal0ai
#   HAL0_FORCE=1 sudo bash uninstall.sh # equivalent to --force
#
# What the CONSERVATIVE default removes (system mode): the hal0-api /
# hal0-openwebui / hal0-slot@ / hal0-agent@ / hermes-gateway /
# hindsight-api units (+ drop-in dirs), every hal0 podman CONTAINER
# (openwebui, hal0-slot-*, comfyui), the FHS code tree + shared venv
# (/usr/lib/hal0), the install PREFIX (/opt/hal0), the per-agent venvs
# (/var/lib/hal0/venvs/*), the Hindsight engine venv, and the
# /usr/local/bin/{hal0,hal0-agent,hermes} shims. It deliberately KEEPS
# /etc/hal0 (config) and /var/lib/hal0 (models, registry, OpenWebUI
# state) so a re-install reuses them.
#
# --purge (== --clean-slate) ALSO removes: /etc/hal0, /var/lib/hal0,
# the hal0 system user AND group, all hal0/toolbox podman IMAGES, and
# the fastflowlm .deb — a true clean slate for fresh test installs.
#
# Every step is best-effort and continue-on-error: the uninstaller NEVER
# aborts mid-teardown on an already-gone target or a failing step. It
# tallies soft failures and reports them at the end (non-zero exit only
# if something could not be torn down).
#
# What --purge does NOT remove (documented, deliberate): host apt
# packages other than fastflowlm (libxrt-npu2, ffmpeg, boost, podman
# itself) — these are general-purpose system libraries that other
# software may depend on, so removing them is out of scope. podman/
# docker themselves are left installed.
#
# Env overrides:
#   HAL0_PREFIX        installation root (default /opt/hal0; --dev defaults
#                      to $PWD/.hal0ai). When set, --dev path layout is used
#                      so the uninstall mirrors the matching install.sh run.
#   HAL0_PATH_LINK     PATH symlink to remove (default /usr/local/bin/hal0;
#                      ignored in --dev mode)

# NOTE on shell options: we deliberately do NOT use `set -e` (errexit) here.
# An uninstaller's whole job is to tear down a possibly-partial install where
# many targets are legitimately already gone, services already stopped, or
# external tools (systemctl/podman/userdel/apt) return non-zero for benign
# reasons. Under `set -e` + a fatal ERR trap, the FIRST such non-zero aborted
# the entire teardown — this is the root cause of the "v0.4 uninstall threw
# lots of errors and left state behind" report: one early failure (e.g.
# `systemctl disable` of an absent unit, or `podman rm` of a running
# container) killed the run before it reached the data/user/image cleanup.
# Instead we keep `-u` (catch unset vars) and `pipefail`, make every
# destructive step best-effort, and tally soft failures for a final report.
set -uo pipefail
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

# ── Soft-failure tally ────────────────────────────────────────────────────────
# Every teardown step that COULD fail records a soft failure instead of
# aborting. The count drives the final exit code so CI / scripted callers can
# still tell a fully-clean teardown from a partial one, without ever leaving
# state behind because of an early error.
SOFT_FAILURES=0
soft_fail() { warn "$*"; SOFT_FAILURES=$((SOFT_FAILURES + 1)); }

# rm_path <path> — best-effort recursive remove with a uniform log line.
# Reports "removed" / "not present" / soft-fails (never aborts).
rm_path() {
    local target="$1"
    if [[ -e "$target" || -L "$target" ]]; then
        if rm -rf "$target" 2>/dev/null; then
            info "Removed ${target}"
        else
            soft_fail "Could not remove ${target}"
        fi
    else
        info "${target} not present"
    fi
}

# ── Parse flags ───────────────────────────────────────────────────────────────
# Default is now CONSERVATIVE: keep /etc/hal0 + /var/lib/hal0 (models) so a
# re-install reuses them. --purge (== --clean-slate) flips PURGE=1 to wipe
# config, data, the system user/group, and podman images for a true reset.
# --keep-data is retained as an explicit alias of the conservative default
# (back-compat for old runbooks / the `hal0 uninstall --keep-data` wrapper).
PURGE=0
DEV_MODE=0
HAL0_FORCE="${HAL0_FORCE:-0}"

for arg in "$@"; do
    case "$arg" in
        --purge|--clean-slate) PURGE=1 ;;
        --keep-data) PURGE=0 ;;   # explicit conservative (now the default)
        --force)     HAL0_FORCE=1 ;;
        --dev)       DEV_MODE=1 ;;
        --help|-h)
            cat <<'EOF'
Usage: uninstall.sh [--purge|--clean-slate] [--keep-data] [--force] [--dev]

  (default)      Conservative teardown: stop services, remove code/units/
                 venvs/binaries and every hal0 podman container, but KEEP
                 /etc/hal0 (config) and /var/lib/hal0 (models, registry,
                 OpenWebUI state) so a re-install reuses them.
  --purge        Clean slate: everything the default removes, PLUS /etc/hal0,
  --clean-slate    /var/lib/hal0, the hal0 system user+group, all hal0/toolbox
                 podman images, and the fastflowlm .deb. Prompts for DELETE
                 confirmation unless --force / HAL0_FORCE=1.
  --keep-data    Explicit conservative mode (alias of the default).
  --force        Skip the --purge DELETE confirmation prompt.
  --dev          Remove the dev-mode tree under $PWD/.hal0ai (or $HAL0_PREFIX);
                 no systemd / system-user / PATH-symlink operations performed.
                 Required to undo `install.sh --dev` without clobbering a host
                 install. (--purge under --dev removes the whole dev tree.)

  HAL0_FORCE=1   env var is equivalent to --force
  HAL0_PREFIX    override install root (default /opt/hal0, or $PWD/.hal0ai
                 when --dev). Path layout always mirrors install.sh.

Every step is best-effort: the uninstaller never aborts mid-teardown on an
already-gone target. It reports any soft failures and exits non-zero only if
something could not be removed.
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

# Intentionally NO fatal ERR trap. Earlier versions ran
#   trap 'error "Uninstall failed at line ${LINENO}."; exit 1' ERR
# under `set -e`, so the first benign non-zero (a missing unit, an
# already-stopped container) aborted the whole teardown and left state
# behind. Teardown steps below are all best-effort (see soft_fail/rm_path).

# ── Stop + disable units (system mode only) ───────────────────────────────────
if [[ "${DEV_MODE}" -eq 0 ]]; then
    step "Stopping services"

    # Static units the installer writes, plus two legacy units kept for
    # old-install cleanup: hal0-caddy (pre-v0.3 auth/Caddy removal) and
    # hal0-lemonade (pre lemonade-removal epic). The current installer
    # writes neither, but an install that predates those changes still
    # has the unit on disk — and a stale hal0-lemonade restart-loops with
    # 203/EXEC once its placeholder binary is gone, so tear it down too.
    UNITS=(hal0-api hal0-openwebui hal0-caddy hal0-lemonade \
           hindsight-api hal0-agent@hermes hermes-gateway)

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

    # Stop + remove every hal0-created container. The unit stops above SHOULD
    # have taken the slot/openwebui containers down (ExecStopPost=podman rm -f),
    # but a half-installed box can have orphaned containers whose units never
    # got the memo. Match hal0's container naming:
    #   - hal0-openwebui                (OpenWebUI)
    #   - hal0-slot-<slot>              (inference slots, base.py:327)
    #   - the ComfyUI image-gen container
    # Check both podman (current) and docker (docker-era installs) on one pass.
    # `rm -f` stops-then-removes; absent containers are skipped silently.
    for _rt in podman docker; do
        command -v "${_rt}" >/dev/null 2>&1 || continue
        while IFS= read -r _cname; do
            [[ -n "${_cname}" ]] || continue
            case "${_cname}" in
                hal0-openwebui|hal0-slot-*|*comfyui*)
                    if "${_rt}" rm -f "${_cname}" &>/dev/null; then
                        info "Removed ${_rt} container ${_cname}"
                    else
                        soft_fail "Could not remove ${_rt} container ${_cname}"
                    fi
                    ;;
            esac
        done < <("${_rt}" ps -a --format '{{.Names}}' 2>/dev/null || true)
    done
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

# The hal0-caddy + hal0-lemonade entries are legacy: not written by the
# current installer, swept so old boxes come clean (pre-v0.3 auth/Caddy
# removal; pre lemonade-removal epic).
for UNIT_FILE in \
    "${UNIT_DIR}/hal0-api.service" \
    "${UNIT_DIR}/hal0-openwebui.service" \
    "${UNIT_DIR}/hal0-caddy.service" \
    "${UNIT_DIR}/hal0-lemonade.service" \
    "${UNIT_DIR}/hindsight-api.service" \
    "${UNIT_DIR}/hal0-slot@.service" \
    "${UNIT_DIR}/hal0-agent@.service" \
    "${UNIT_DIR}/hermes-gateway.service"
do
    rm_path "${UNIT_FILE}"
done

# Per-instance slot/agent drop-in dirs the orchestrator renders at runtime
# (hal0-slot@<name>.service.d/, hal0-agent@<name>.service.d/) plus the named
# drop-ins the installer ships. Glob-sweep so extra agents/slots come clean.
for DROPIN_DIR in \
    "${UNIT_DIR}"/hal0-slot@*.service.d \
    "${UNIT_DIR}"/hal0-agent@*.service.d \
    "${UNIT_DIR}/hermes-gateway.service.d"
do
    [[ -d "${DROPIN_DIR}" ]] && rm_path "${DROPIN_DIR}"
done

# Stale enablement symlinks under multi-user.target.wants/ — `systemctl
# disable` above should have removed them, but a half-installed box can have
# a dangling symlink whose target unit was never written. Sweep defensively.
for WANTS_LINK in \
    "${UNIT_DIR}/multi-user.target.wants"/hal0-*.service \
    "${UNIT_DIR}/multi-user.target.wants"/hindsight-api.service \
    "${UNIT_DIR}/multi-user.target.wants"/hermes-gateway.service
do
    [[ -L "${WANTS_LINK}" ]] && rm_path "${WANTS_LINK}"
done

if [[ "${DEV_MODE}" -eq 0 ]]; then
    systemctl daemon-reload 2>/dev/null \
        && info "systemctl daemon-reload done" \
        || soft_fail "systemctl daemon-reload failed"
fi

# ── Remove code + venvs ───────────────────────────────────────────────────────
step "Removing code"

# /usr/lib/hal0 — FHS code tree (hal0-<version>/), the `current` symlink, the
# shared venv (venv/), and the Hermes hooks (hermes-hooks/). Removing the whole
# dir gets all of them in one shot (#495). The `[[ -L .../current ]]` test
# catches the editable→FHS layout where `current` is a symlink into a
# versioned dir even if LIB_DIR itself isn't a plain dir.
if [[ -d "${LIB_DIR}" || -L "${LIB_DIR}/current" ]]; then
    rm_path "${LIB_DIR}"
else
    info "${LIB_DIR} not present"
fi

# The install PREFIX (default /opt/hal0). install.sh rsyncs the source
# tree + builds the venv here; before #508 the uninstaller only removed
# LIB_DIR and left this behind. In --dev mode PREFIX is handled by the
# dev-tree cleanup block lower down (it lives under $PWD/.hal0ai), so we
# only sweep it here for a system install.
if [[ "${DEV_MODE}" -eq 0 ]]; then
    rm_path "${PREFIX}"
fi

# Bundled agent-skills SHIP dir (/usr/share/hal0/skills, install.sh:1063).
# This is read-only source the hermes provision symlink-mirrors into
# /etc/hal0/agent-skills. It lives OUTSIDE /etc + /var, so neither the data
# sweep nor the code sweep above touches it — a leftover here re-seeds stale
# skills into the next install's mirror. Always removed (it's pure code).
if [[ "${DEV_MODE}" -eq 0 ]]; then
    rm_path "/usr/share/hal0/skills"
    # Drop the now-empty parent so /usr/share/hal0 doesn't linger.
    [[ -d /usr/share/hal0 ]] && rmdir /usr/share/hal0 2>/dev/null \
        && info "Removed /usr/share/hal0"
fi

# Per-agent + Hindsight venvs live UNDER ${VAR_DIR}, but they are pure
# executable code (toolchain-built), not user data. The conservative default
# keeps ${VAR_DIR} for the models/registry/OpenWebUI state — so we MUST remove
# the venvs explicitly here, otherwise a stale venv (wrong Python ABI after a
# distro upgrade, half-provisioned hermes) collides with the next install's
# `hal0 agent install hermes` / hindsight bootstrap. --purge wipes all of
# ${VAR_DIR} below anyway, so this is a no-op-then-redundant in that mode.
#   /var/lib/hal0/venvs/<agent>   — hermes (+ any other agent) managed venvs
#   /var/lib/hal0/memory/hindsight/.venv — Hindsight engine venv (pg0/hf-cache
#       under that dir are engine STATE; keep them in conservative mode so the
#       memory banks survive a re-install, drop them under --purge with VAR_DIR)
rm_path "${VAR_DIR}/venvs"
rm_path "${VAR_DIR}/memory/hindsight/.venv"

# ── PATH symlinks / CLI shims (system mode only) ──────────────────────────────
if [[ "${DEV_MODE}" -eq 0 ]]; then
    HAL0_PATH_LINK="${HAL0_PATH_LINK:-/usr/local/bin/hal0}"
    BIN_DIR="$(dirname "${HAL0_PATH_LINK}")"
    # hal0 (main CLI), hal0-agent (agent shim), hermes (hermes_provision.py
    # installs /usr/local/bin/hermes as the hermes-agent CLI shim — never
    # removed before this fix, so it shadowed a fresh provision's shim).
    for SHIM in "${HAL0_PATH_LINK}" "${BIN_DIR}/hal0-agent" "${BIN_DIR}/hermes"; do
        if [[ -L "${SHIM}" || -f "${SHIM}" ]]; then
            rm_path "${SHIM}"
        fi
    done
fi

# ── Data dirs ─────────────────────────────────────────────────────────────────
step "Data directories"

if [[ "${PURGE}" -eq 0 ]]; then
    # Conservative default — keep config + state so a re-install reuses them.
    warn "Keeping data dirs (conservative default — pass --purge to wipe):"
    warn "  ${ETC_DIR}      — config"
    warn "  ${VAR_DIR}  — models, registry, openwebui state, memory banks"
    warn "Re-run install.sh to restore services using this data."
else
    # --purge: confirmation unless forced
    if [[ "${HAL0_FORCE}" -ne 1 ]]; then
        # %b interprets backslash escapes in the substituted strings —
        # the BOLD/RED/RESET helpers are stored as literal `\033[...m`
        # sequences, so %s would print them verbatim instead of invoking
        # the terminal's SGR. Same gotcha bit the WARNING + the
        # "Type DELETE" prompt; both use %b.
        printf '\n%b%bWARNING:%b --purge will delete:\n' "${RED}" "${BOLD}" "${RESET}"
        printf '  %s      (config + slot definitions)\n' "${ETC_DIR}"
        printf '  %s  (models, registry, openwebui state, memory banks)\n\n' "${VAR_DIR}"
        printf 'Type %bDELETE%b to confirm, or Ctrl-C to cancel: ' "${BOLD}" "${RESET}"
        read -r CONFIRM
        if [[ "${CONFIRM}" != "DELETE" ]]; then
            warn "Aborted — data preserved (code/services already removed)."
            CONFIRM=""
            PURGE=0   # fall through to the conservative summary
        fi
    fi

    if [[ "${PURGE}" -eq 1 ]]; then
        for DATA_DIR in "${ETC_DIR}" "${VAR_DIR}"; do
            rm_path "${DATA_DIR}"
        done
    fi
fi

# ── First-run claim lockfile ──────────────────────────────────────────────────
# Always cleaned up — even in the conservative (keep-data) default — because
# the lockfile only has meaning during the first-run claim window, and a
# leftover OTP after an uninstall is a credential lying around with no
# semantics. Belt-and-braces even though the file lives under ${VAR_DIR}
# (which --purge rm -rf's above).
FIRST_RUN_LOCK="${VAR_DIR}/.first-run.lock"
if [[ -f "${FIRST_RUN_LOCK}" ]]; then
    rm_path "${FIRST_RUN_LOCK}"
fi

# ── Dev-mode tree cleanup ─────────────────────────────────────────────────────
# After removing the FHS-mirror subdirs, drop the now-empty PREFIX itself so
# `install.sh --dev` can be re-run cleanly. Only do this if PREFIX is otherwise
# empty (don't blow away unrelated user files that happened to share the dir).
if [[ "${DEV_MODE}" -eq 1 && -d "${PREFIX}" ]]; then
    # Remove obvious leftover dirs first. In conservative mode the dev tree's
    # var/ (models, memory) is kept; --purge removes it too.
    DEV_SUBDIRS=("${PREFIX}/usr" "${PREFIX}/.venv")
    [[ "${PURGE}" -eq 1 ]] && DEV_SUBDIRS+=("${PREFIX}/etc" "${PREFIX}/var")
    for D in "${DEV_SUBDIRS[@]}"; do
        [[ -d "${D}" ]] && rm_path "${D}"
    done
    if [[ -z "$(ls -A "${PREFIX}" 2>/dev/null)" ]]; then
        rmdir "${PREFIX}" 2>/dev/null && info "Removed empty ${PREFIX}"
    else
        warn "${PREFIX} not empty — leaving in place"
    fi
fi

# ── podman/docker images (clean-slate only) ───────────────────────────────────
# install.sh + the providers pull a stack of toolbox/serving images
# (open-webui, hal0-toolbox-{vulkan,rocm,flm,kokoro}, comfyui). These are large
# (multi-GB) and survive a code uninstall. The conservative default LEAVES them
# (a re-install reuses the pulled layers — faster, no re-download). --purge
# removes them for a true clean slate. Best-effort per image.
if [[ "${DEV_MODE}" -eq 0 && "${PURGE}" -eq 1 ]]; then
    step "Container images (--purge)"
    for _rt in podman docker; do
        command -v "${_rt}" >/dev/null 2>&1 || continue
        # Match hal0's images by repository substring so tag/digest pins and
        # local dev builds (hal0-toolbox-*:dev) are all caught.
        while IFS= read -r _img; do
            [[ -n "${_img}" ]] || continue
            case "${_img}" in
                *open-webui*|*openwebui*|*hal0-toolbox*|ghcr.io/hal0ai/*|*amd-strix-halo-comfyui*|*kyuz0/*comfyui*)
                    if "${_rt}" rmi -f "${_img}" &>/dev/null; then
                        info "Removed ${_rt} image ${_img}"
                    else
                        soft_fail "Could not remove ${_rt} image ${_img}"
                    fi
                    ;;
            esac
        done < <("${_rt}" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null || true)
    done
fi

# ── System user + group (clean-slate only) ────────────────────────────────────
# The hal0 system user owns ${VAR_DIR} content. The conservative default keeps
# ${VAR_DIR}, so it MUST keep the user too (else the kept data is left orphaned
# with a numeric uid that a later user could reuse). --purge removes both.
if [[ "${DEV_MODE}" -eq 0 && "${PURGE}" -eq 1 ]]; then
    step "System user + group"

    if id hal0 &>/dev/null 2>&1; then
        if userdel hal0 2>/dev/null; then
            info "Removed system user hal0"
        else
            soft_fail "Could not remove hal0 user"
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

# ── FLM .deb (clean-slate, apt hosts only) ────────────────────────────────────
# install.sh installs the `fastflowlm` .deb on Debian/Ubuntu hosts (NPU
# host probe + device sanity). Reverse it under --purge only — it's a hal0-
# specific package, but on a shared box a user may have grown to depend on it,
# so the conservative default leaves it. Guarded by `command -v apt-get`
# so non-apt hosts skip cleanly, and every step is fail-soft.
# NOTE: other host packages install.sh may touch (libxrt-npu2, ffmpeg, boost,
# podman) are general-purpose system libraries and are deliberately NOT removed.
if [[ "${DEV_MODE}" -eq 0 && "${PURGE}" -eq 1 ]] && command -v apt-get &>/dev/null 2>&1; then
    step "FLM package (--purge)"

    if dpkg-query -W -f='${Status}' fastflowlm 2>/dev/null | grep -q "install ok installed"; then
        if apt-get remove -y fastflowlm &>/dev/null; then
            info "Removed fastflowlm package"
        else
            soft_fail "Could not remove fastflowlm package"
        fi
    else
        info "fastflowlm package not installed"
    fi

fi

# ── Done ──────────────────────────────────────────────────────────────────────
if [[ "${SOFT_FAILURES}" -gt 0 ]]; then
    printf '\n%s%shal0 uninstalled with %d soft failure(s).%s\n' \
        "${YELLOW}" "${BOLD}" "${SOFT_FAILURES}" "${RESET}"
    warn "Some targets could not be removed (see warnings above) — re-run to retry."
else
    printf '\n%s%shal0 uninstalled.%s\n' "${GREEN}" "${BOLD}" "${RESET}"
fi
if [[ "${PURGE}" -eq 0 && "${DEV_MODE}" -eq 0 ]]; then
    printf '  Config + data preserved in %s and %s.\n' "${ETC_DIR}" "${VAR_DIR}"
    printf '  Run %ssudo bash uninstall.sh --purge%s for a full clean slate.\n\n' \
        "${BOLD}" "${RESET}"
fi

# Non-zero exit when something could not be torn down, so CI / test loops can
# tell a clean teardown from a partial one — WITHOUT ever having aborted early.
[[ "${SOFT_FAILURES}" -eq 0 ]] || exit 1
exit 0
