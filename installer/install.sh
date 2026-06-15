#!/usr/bin/env bash
# hal0 installer — idempotent, non-interactive.
#
# Usage:
#   sudo bash install.sh             # standard install at /opt/hal0
#   bash install.sh --dev            # local-only install under $PWD/.hal0ai
#   sudo bash install.sh --no-start  # set up everything but don't start units
#
# Env overrides:
#   HAL0_PREFIX        installation root (default /opt/hal0)
#   HAL0_PORT          API port (default 8080)
#   HAL0_USER          system user (default root — slot template uses root
#                      because the container is the real sandbox boundary)
#   HAL0_PYTHON        python interpreter (default python3)
#   HAL0_NO_PROBE=1    skip the hardware probe at the end
#   HAL0_TOOLBOX_IMAGE_VULKAN, HAL0_TOOLBOX_IMAGE_ROCM, ...
#                      override per-backend container image refs

set -euo pipefail
IFS=$'\n\t'

# Shared UI helpers — banner, step counter, spinner, boxed summary, plus
# info / warn / err / die. ui_step maintains CURRENT_STEP for the ERR
# trap below. Honors HAL0_PLAIN=1 and NO_COLOR=1 for non-fancy terms.
# shellcheck source=lib/ui.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/ui.sh"

# Distro / package-manager detection (distro_id / pkg_mgr / pkg_install_cmd /
# python_venv_hint). One place knows "what distro is this and how do I name an
# install command" so the apt-centric assumptions below degrade into honest,
# distro-correct messages on Fedora/Arch/openSUSE/Alpine instead of "apt not
# found". Sourced before preflight.sh, which re-sources it (guarded no-op).
# shellcheck source=lib/distro.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/distro.sh"

# Re-runnable pre-flight checks (preflight_systemd / preflight_python /
# preflight_container_runtime / preflight_disk / preflight_ports / preflight_all).
# Sourcing only loads the functions — the installer dispatches the
# subset it cares about below. `hal0 doctor` shells the same file in
# executable mode to run preflight_all post-install.
# shellcheck source=lib/preflight.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/preflight.sh"

# Non-interactive apt for every apt-get call in this installer (FLM
# runtime libs, FLM .deb) — without this a debconf prompt can hang a
# tty install or fail a CI/non-tty run. Only meaningful on apt hosts;
# guarded so it isn't exported as dead state on Fedora/Arch/etc.
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
fi

# Poll `systemctl is-active` for up to `timeout` seconds. Returns 0 the
# moment the unit reports active, 1 on timeout. Use instead of a flat
# `sleep N; is-active` so slow first boots (OpenWebUI pulling images,
# slot container image pulls) don't get falsely flagged as failures.
wait_active() {
    local unit="$1"
    # `local` evaluates all RHS *before* any name binds, so a one-liner
    # `local timeout="${2:-15}" deadline=$((SECONDS+timeout))` would
    # reference an unset `timeout` under `set -u`. Split deliberately.
    local timeout="${2:-15}"
    local deadline=$((SECONDS+timeout))
    while (( SECONDS < deadline )); do
        systemctl is-active --quiet "${unit}" && return 0
        sleep 0.5
    done
    return 1
}

DEV_MODE=0
NO_START=0
# ROCmFP4 + MTP note: the old `--rocmfp4` power pack (a host-side fork
# binary wired into the retired daemon runtime) is gone. FP4/MTP now
# ships as container profiles (`rocm` / `rocm-mtp` in
# installer/etc-hal0/profiles.toml) — the fork llama-server is baked
# into the rocm-7.2.4-rocmfp4-server toolbox image and selected per
# slot via `profile = "..."`.
# TLS posture: hal0-api binds 0.0.0.0:8080 directly. TLS termination,
# DNS, and any per-host certs are the responsibility of an upstream
# reverse proxy (Traefik, nginx, Cloudflare Tunnel) — hal0 does not ship
# an edge terminator. See docs/operate/tls.md for example proxies.
# Pull destination for `hal0 model pull` and the dashboard's pull buttons.
# Empty → ask interactively when stdin is a tty, default to <var-lib>/models
# otherwise. The chosen path is written to hal0.toml as [models].pull_root
# and also auto-included in [models].roots so it's scanned at startup.
MODELS_DIR="${HAL0_MODELS_DIR:-}"
for arg in "$@"; do
    case "$arg" in
        --dev) DEV_MODE=1 ;;
        --no-start) NO_START=1 ;;
        --models-dir=*) MODELS_DIR="${arg#--models-dir=}" ;;
        --help|-h)
            cat <<EOF
Usage: install.sh [--dev] [--no-start] [--models-dir=PATH]
  --dev               install under \$PWD/.hal0ai/, no systemd setup
  --no-start          set up everything but don't enable/start the API
  --models-dir=PATH   absolute path where HuggingFace pulls land
                      (default: /var/lib/hal0/models — or \$PWD/.hal0ai/var/lib/hal0/models
                      under --dev). Can also be set with HAL0_MODELS_DIR=PATH.
                      Asks interactively if running on a tty and not provided.
EOF
            exit 0
            ;;
        *) warn "unknown flag: ${arg} (ignored)" ;;
    esac
done

# Banner first — before any info/warn so the brand greets the user
# rather than hiding behind a "Dev mode …" line.
ui_banner

HAL0_PORT="${HAL0_PORT:-8080}"
HAL0_USER="${HAL0_USER:-root}"
PY="${HAL0_PYTHON:-python3}"

# API binds 0.0.0.0:8080 unconditionally. TLS is upstream's job — see
# the comment on TLS posture near the flag parser.
API_BIND_HOST="0.0.0.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${DEV_MODE}" -eq 1 ]]; then
    # Dev: editable checkout, everything under one prefix. Updater.apply()
    # hard-refuses in this mode — run `git pull && pip install -e .` to update.
    PREFIX="${HAL0_PREFIX:-${PWD}/.hal0ai}"
    ETC_DIR="${PREFIX}/etc/hal0"
    VAR_DIR="${PREFIX}/var/lib/hal0"
    UNIT_DIR="${PREFIX}/etc/systemd/system"
    VENV_DIR="${PREFIX}/.venv"
    CURRENT_LINK=""
    info "Dev mode — all paths under ${PREFIX}"
else
    # Prod FHS (#495): code lives in a versioned dir with a `current`
    # symlink; the venv is shared at ${FHS_ROOT}/venv so it survives
    # `hal0 update`'s atomic symlink swaps (the updater re-pips `current`
    # into this venv on apply).
    HAL0_FHS_ROOT="${HAL0_PREFIX:-/usr/lib/hal0}"
    VERSION="$(grep -m1 '^version' "${REPO_ROOT}/pyproject.toml" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/')"
    [[ -n "${VERSION}" ]] || VERSION="0.0.0"
    PREFIX="${HAL0_FHS_ROOT}/hal0-${VERSION}"
    CURRENT_LINK="${HAL0_FHS_ROOT}/current"
    ETC_DIR="/etc/hal0"
    VAR_DIR="/var/lib/hal0"
    UNIT_DIR="/etc/systemd/system"
    VENV_DIR="${HAL0_FHS_ROOT}/venv"
    info "FHS layout — code ${PREFIX}, current → ${CURRENT_LINK}, venv ${VENV_DIR}"
fi

# ── Release verification gate ──────────────────────────────────────────────
# Refuse to run as root against an UNVERIFIED release tree. The signed
# install path (`curl -fsSL https://hal0.dev/install.sh | sudo bash`) runs
# through bootstrap.sh, which sha256 + cosign-verifies the release tarball
# and exports HAL0_BOOTSTRAP_VERIFIED=1 before exec'ing us. A git checkout
# is trusted (you cloned it from your own remote) and --dev installs are
# local. Any other path — e.g. someone who downloaded a random tarball and
# ran `sudo bash install.sh` — would execute arbitrary code as root, so it
# must opt in explicitly with HAL0_INSTALL_SKIP_VERIFY=1.
if [[ "${DEV_MODE}" -eq 0 \
      && "${HAL0_BOOTSTRAP_VERIFIED:-0}" != "1" \
      && ! -d "${REPO_ROOT}/.git" ]]; then
    if [[ "${HAL0_INSTALL_SKIP_VERIFY:-0}" == "1" ]]; then
        warn "HAL0_INSTALL_SKIP_VERIFY=1 — installing from an UNVERIFIED source (no cosign check)"
    else
        die "Refusing to install from an unverified release tree.

  This tree did NOT come through the signed installer (no cosign
  verification, and it is not a git checkout). Installing it would run
  arbitrary code as root.

  Use the signed one-liner instead:
      curl -fsSL https://hal0.dev/install.sh | sudo bash

  Or, if you trust THIS source and accept the risk:
      HAL0_INSTALL_SKIP_VERIFY=1 sudo bash installer/install.sh"
    fi
fi

# Heads-up if a legacy editable /opt/hal0 install is present (pre-#495).
# This run installs the FHS layout under ${HAL0_FHS_ROOT} and rewrites the
# systemd units to the shared venv, so the old tree is orphaned. We do NOT
# auto-delete it (it may be a CT-105-style working checkout) — uninstall.sh
# cleans /opt/hal0 if the operator later wants it gone.
if [[ "${DEV_MODE}" -eq 0 && -e "/opt/hal0/.venv" && "${HAL0_FHS_ROOT}" != "/opt/hal0" ]]; then
    warn "legacy install at /opt/hal0 detected — superseded by the FHS layout at ${HAL0_FHS_ROOT}"
    warn "  the old tree is now orphaned; remove it with 'sudo bash installer/uninstall.sh' or 'sudo rm -rf /opt/hal0' once you've confirmed the new install works"
fi

# Resolve pull destination: explicit flag / env wins, then an interactive
# prompt (only when attached to a tty), then the FHS default. Always
# absolute — relative paths under sudo would land in /root or wherever
# the install was launched, which is never what the operator wanted.
DEFAULT_MODELS_DIR="${VAR_DIR}/models"
if [[ -z "${MODELS_DIR}" ]]; then
    if [[ -t 0 && -t 1 ]]; then
        printf '\n  Model pull directory (where downloaded .gguf/.safetensors land)\n'
        printf '  [%s]: ' "${DEFAULT_MODELS_DIR}"
        read -r MODELS_DIR_INPUT || MODELS_DIR_INPUT=""
        MODELS_DIR="${MODELS_DIR_INPUT:-${DEFAULT_MODELS_DIR}}"
    else
        MODELS_DIR="${DEFAULT_MODELS_DIR}"
    fi
fi
if [[ "${MODELS_DIR}" != /* ]]; then
    die "--models-dir must be an absolute path (got: ${MODELS_DIR})"
fi
info "Pull destination: ${MODELS_DIR}"

# Step total. Kept here so editors who add or remove a ui_step bump the
# visible counter in the same diff.
UI_STEP_TOTAL=12

trap 'err "install failed at line ${LINENO} during: ${CURRENT_STEP:-pre-init}"
    case "${CURRENT_STEP}" in
        "Pre-flight checks")
            warn "Recovery: free space under ${VAR_DIR:-/var/lib/hal0} (need ≥20 GB),"
            warn "         or stop the process holding the port and rerun."
            warn "         Set HAL0_PORT=<other> to bind a different API port;"
            warn "         OpenWebUI :3001 is hardcoded in the systemd unit." ;;
        "Python environment")
            warn "Recovery: scroll up to the pip output for the real error."
            warn "         Retry with HAL0_PYTHON=python3.12 sudo bash install.sh" ;;
        "Service start")
            warn "Recovery: journalctl -u hal0-api -n 60" ;;
        "Hardware probe")
            warn "Recovery: rerun with HAL0_NO_PROBE=1 and file an issue with"
            warn "         /etc/hal0/hardware.json (if present) attached." ;;
    esac
    exit 1' ERR

ui_step "Pre-flight checks"

if [[ "${DEV_MODE}" -eq 0 && "$(id -u)" -ne 0 ]]; then
    if command -v sudo >/dev/null; then
        warn "Re-exec under sudo"
        exec sudo -E HAL0_PORT="${HAL0_PORT}" HAL0_USER="${HAL0_USER}" HAL0_PYTHON="${PY}" \
            HAL0_PREFIX="${HAL0_PREFIX:-}" HAL0_NO_PROBE="${HAL0_NO_PROBE:-}" \
            bash "$0" "$@"
    else
        die "must run as root (sudo bash install.sh)"
    fi
fi

info "system: $(uname -srm)"

# Architecture is a hard requirement in every mode — all shipped binaries
# (FastFlowLM .deb, toolbox container images) are amd64-only.
preflight_arch || die "hal0 requires an x86_64 host (see the message above)"

# Single up-front connectivity probe (soft) so a network/proxy problem
# surfaces once with guidance instead of as N download failures later.
preflight_network

# Systemd is hard-required outside dev mode; preflight_systemd just
# reports presence, so we wrap it in the dev-mode skip and turn its
# non-zero return into a die().
if [[ "${DEV_MODE}" -eq 0 ]]; then
    preflight_systemd || die "systemd not found — hal0 v1 requires systemctl on PATH"
fi

# preflight_python returns 1 when python is missing OR the version is
# outside 3.11–3.14 (it logs an `err` / `warn` itself). The installer
# only treats *missing* python as fatal — a wrong-version warning is OK
# because pip may still work. We disambiguate by re-checking PATH.
if ! preflight_python; then
    if ! command -v "${PY}" >/dev/null 2>&1; then
        die "python interpreter '${PY}' not found — install with: $(python_venv_hint)"
    fi
    # Version warning already printed; keep going.
fi

# `python3 -m venv` capability is a hard requirement — the install always
# creates a venv. HAL0_VENV_REQUIRED=1 flips preflight_venv into
# install-the-venv-stdlib-or-fail mode (the common clean-Debian/Ubuntu
# "python3 present, python3-venv missing" case auto-installs via the
# detected package manager), mirroring preflight_container_runtime above.
HAL0_VENV_REQUIRED=1 preflight_venv \
    || die "python venv module missing and could not be installed — $(python_venv_hint), then re-run install.sh"

# Every inference slot runs in a container, so a container runtime is a hard
# requirement. HAL0_CONTAINER_REQUIRED=1 flips preflight_container_runtime into
# install-podman-or-fail mode (podman auto-installed via the detected package
# manager; hard-fail with the exact one-liner otherwise). Without this a fresh
# box finishes "successfully" but every slot sits in error "no container runtime
# found". `hal0 doctor` leaves the flag unset and stays soft/report-only.
HAL0_CONTAINER_REQUIRED=1 preflight_container_runtime \
    || die "no container runtime — install podman (see above), then re-run install.sh"

# Disk + port-collision checks only matter for the live install — dev
# mode lays files under $PWD/.hal0ai and never binds 8080/3001. We
# aggregate both check results (so the operator sees *both* failures
# in one run instead of fixing disk → rerun → discover port) and then
# trip a bare `false` so the ERR trap fires with the contextual
# "Pre-flight checks" recovery hint above.
if [[ "${DEV_MODE}" -eq 0 ]]; then
    pf_rc=0
    preflight_writable "${PREFIX}" /usr/lib/hal0 "${ETC_DIR}" "${UNIT_DIR}" \
        "${VAR_DIR}" /usr/local/bin || pf_rc=$?
    preflight_disk 20 "${VAR_DIR}"            || pf_rc=$?
    preflight_ports "${HAL0_PORT}" 3001       || pf_rc=$?
    if (( pf_rc != 0 )); then
        false
    fi
fi

ui_step "Filesystem layout"

mkdir -p \
    "${PREFIX}" \
    "${ETC_DIR}/slots" \
    "${MODELS_DIR}" \
    "${VAR_DIR}/registry" \
    "${VAR_DIR}/slots" \
    "${VAR_DIR}/openwebui" \
    "${VAR_DIR}/cache" \
    "${UNIT_DIR}"
info "directories under ${PREFIX}, ${ETC_DIR}, ${VAR_DIR} (pulls → ${MODELS_DIR})"

# Production (FHS, #495) ships the source tree into the versioned dir
# ${PREFIX} (=${FHS_ROOT}/hal0-<version>) and points `current` at it, so
# `hal0 update` can atomically swap `current` to a new versioned tree.
# The shared venv at ${FHS_ROOT}/venv pip-installs hal0 (non-editable)
# from this tree; the updater re-pips the swapped-in tree on apply. Dev
# installs skip the copy: REPO_ROOT is the operator's git checkout and we
# want pip's editable link aimed there so source edits flow without a
# reinstall.
if [[ "${DEV_MODE}" -eq 0 && "${REPO_ROOT}" != "${PREFIX}" ]]; then
    if command -v rsync >/dev/null 2>&1; then
        ui_spinner_run "Copying source to ${PREFIX}" \
            rsync -a --delete \
                --exclude='.venv/' \
                --exclude='.git/' \
                --exclude='__pycache__/' \
                --exclude='*.pyc' \
                --exclude='node_modules/' \
                --exclude='.pytest_cache/' \
                --exclude='.ruff_cache/' \
                "${REPO_ROOT}/" "${PREFIX}/"
    else
        # rsync isn't strictly a prereq; tar-pipe falls back cleanly.
        (cd "${REPO_ROOT}" && tar --exclude='.venv' --exclude='.git' \
            --exclude='__pycache__' --exclude='*.pyc' \
            --exclude='node_modules' --exclude='.pytest_cache' \
            --exclude='.ruff_cache' -cf - .) \
            | (cd "${PREFIX}" && tar -xf -)
        info "copied source → ${PREFIX} (tar fallback)"
    fi
    REPO_ROOT="${PREFIX}"
fi

# Point the `current` symlink at this release's versioned tree (prod only).
# Atomic swap so a concurrent reader never sees a missing link: write a
# temp symlink then rename over the old one. This is the same target the
# updater swaps on `hal0 update`.
if [[ "${DEV_MODE}" -eq 0 && -n "${CURRENT_LINK}" ]]; then
    ln -sfn "${PREFIX}" "${CURRENT_LINK}.tmp.$$"
    mv -T "${CURRENT_LINK}.tmp.$$" "${CURRENT_LINK}"
    info "current → ${PREFIX}"
fi

# Seed hal0.toml's [models].pull_root when the operator picked a non-default
# directory, so the API and CLI both read the same value from the
# canonical config without an extra dashboard step. Idempotent: a
# previous run with the same value is a no-op; a different value
# overwrites (operator just re-ran the installer with a new path).
HAL0_TOML="${ETC_DIR}/hal0.toml"
if [[ "${MODELS_DIR}" != "/var/lib/hal0/models" ]]; then
    if ! grep -qE "^\\s*pull_root\\s*=\\s*\"${MODELS_DIR//\//\\/}\"" "${HAL0_TOML}" 2>/dev/null; then
        if [[ -f "${HAL0_TOML}" ]] && grep -q "^\\[models\\]" "${HAL0_TOML}"; then
            # [models] table exists — patch pull_root in place (or append
            # under the existing table). Cheap awk pass; no toml parser
            # so we accept the limitation that nested tables under
            # [models.xxx] aren't supported (the schema has none).
            python3 - "${HAL0_TOML}" "${MODELS_DIR}" <<'PYEOF'
import sys, re, pathlib
path = pathlib.Path(sys.argv[1])
new_root = sys.argv[2]
text = path.read_text(encoding="utf-8")
# Replace existing pull_root inside [models], else append before the next [section]
m = re.search(r"^\[models\][^\[]*", text, flags=re.MULTILINE)
if m:
    block = m.group(0)
    if re.search(r"^\s*pull_root\s*=", block, flags=re.MULTILINE):
        new_block = re.sub(r"^\s*pull_root\s*=.*$",
                           f'pull_root = "{new_root}"',
                           block, count=1, flags=re.MULTILINE)
    else:
        new_block = block.rstrip() + f'\npull_root = "{new_root}"\n\n'
    text = text[:m.start()] + new_block + text[m.end():]
else:
    text = text.rstrip() + f'\n\n[models]\npull_root = "{new_root}"\n'
path.write_text(text, encoding="utf-8")
PYEOF
        else
            mkdir -p "${ETC_DIR}"
            printf '\n[models]\npull_root = "%s"\n' "${MODELS_DIR}" >> "${HAL0_TOML}"
        fi
        info "wrote [models].pull_root → ${HAL0_TOML}"
    fi
fi

ui_step "Python environment"

if [[ ! -d "${VENV_DIR}" ]]; then
    "${PY}" -m venv "${VENV_DIR}"
    info "created venv at ${VENV_DIR}"
fi
PIP="${VENV_DIR}/bin/pip"
HAL0_BIN="${VENV_DIR}/bin/hal0"
# The `hal0-agent` console script (pyproject [project.scripts]) is the
# stable entry point the `hal0-agent@.service` unit ExecStart's. pip
# installs it alongside `hal0` in the venv.
HAL0_AGENT_BIN="${VENV_DIR}/bin/hal0-agent"

# Refresh pip, then install hal0. Prod (FHS) installs NON-editable from the
# versioned tree so the venv owns its own copy of the code and `hal0 update`
# can re-pip a swapped-in tree (#495). Dev installs editable so the
# operator's source edits flow without a reinstall.
# ui_spinner_run drops the >/dev/null — the spinner shows the live tail
# of pip's output, and on failure replays the last 50 lines on stderr.
ui_spinner_run "Upgrading pip / setuptools / wheel" \
    "${PIP}" install --upgrade pip setuptools wheel
if [[ "${DEV_MODE}" -eq 1 ]]; then
    ui_spinner_run "Installing hal0 (editable) from ${REPO_ROOT}" \
        "${PIP}" install -e "${REPO_ROOT}"
else
    ui_spinner_run "Installing hal0 from ${REPO_ROOT}" \
        "${PIP}" install "${REPO_ROOT}"
fi

if [[ ! -x "${HAL0_BIN}" ]]; then
    die "hal0 binary not produced at ${HAL0_BIN} — check pip install output"
fi
info "hal0 cli: ${HAL0_BIN}"

# Symlink onto PATH so `hal0` works in any new shell. Skip in --dev (dev tree
# stays self-contained); /usr/local/bin is on default PATH for bash/zsh/fish
# and survives upgrades because it points at the venv shim, not a copy.
if [[ "${DEV_MODE}" -eq 0 ]]; then
    HAL0_PATH_LINK="${HAL0_PATH_LINK:-/usr/local/bin/hal0}"
    if ln -sfn "${HAL0_BIN}" "${HAL0_PATH_LINK}" 2>/dev/null; then
        info "linked ${HAL0_PATH_LINK} → ${HAL0_BIN}"
    else
        warn "could not link ${HAL0_PATH_LINK} (check permissions); add ${VENV_DIR}/bin to PATH manually"
    fi
    # Also link `hal0-agent` — the `hal0-agent@.service` unit ExecStart's
    # `/usr/local/bin/hal0-agent`, so without this symlink the agent units
    # fail with status=203/EXEC the moment an operator runs
    # `hal0 agent bootstrap hermes`. Derive the link dir from HAL0_PATH_LINK
    # so a relocated `hal0` keeps `hal0-agent` beside it.
    HAL0_AGENT_LINK="$(dirname "${HAL0_PATH_LINK}")/hal0-agent"
    if [[ -x "${HAL0_AGENT_BIN}" ]]; then
        if ln -sfn "${HAL0_AGENT_BIN}" "${HAL0_AGENT_LINK}" 2>/dev/null; then
            info "linked ${HAL0_AGENT_LINK} → ${HAL0_AGENT_BIN}"
        else
            warn "could not link ${HAL0_AGENT_LINK} (check permissions); agent units need it on PATH"
        fi
    else
        warn "hal0-agent shim not found at ${HAL0_AGENT_BIN} — agent units will fail until it is linked"
    fi
fi

ui_step "Dashboard UI"

UI_DIR="${REPO_ROOT}/ui"
UI_DIST="${UI_DIR}/dist"
if [[ -f "${UI_DIST}/index.html" ]]; then
    info "ui/dist already built — left alone"
elif command -v npm >/dev/null 2>&1; then
    # Two phases — install can dominate first-boot time, build is steady.
    # Wrap each so the user sees what npm is doing instead of staring at
    # a blank line for several minutes.
    ui_spinner_run "Installing dashboard npm packages" \
        bash -c "cd '${UI_DIR}' && npm install --no-audit --no-fund"
    ui_spinner_run "Building dashboard (npm run build)" \
        bash -c "cd '${UI_DIR}' && npm run build"
    info "wrote ${UI_DIST}"
else
    warn "npm not found — dashboard at :${HAL0_PORT}/ will return 404 until you build the UI"
    warn "  install Node 20 LTS, then: cd ${UI_DIR} && npm install && npm run build"
fi

ui_step "Configuration"

HAL0_TOML="${ETC_DIR}/hal0.toml"
if [[ ! -f "${HAL0_TOML}" ]]; then
    cat > "${HAL0_TOML}" <<TOML
# hal0 configuration — created by install.sh ($(date -uIseconds))
# Edit with: hal0 config edit
# Validate:  hal0 config validate

[meta]
schema_version = 1

[slots]
port_range_start = 8081
port_range_end = 8099

[dispatcher]
prefetch_timeout_s = 8.0
prefetch_parallel_cap = 4

[telemetry]
enabled = false
TOML
    info "wrote ${HAL0_TOML}"
else
    info "${HAL0_TOML} exists — left alone"
fi
# Make the config world-readable. It's not a secret (no tokens, no
# passwords — those live in tokens.toml + auth.toml which stay 0600),
# and `hal0 config show` from a non-root shell needs to read it.
# Same goes for /etc/hal0 itself — without this an install run with
# a tightened root umask leaves /etc/hal0 at 0700 and every non-root
# CLI command 500s with PermissionError. Idempotent on re-runs.
chmod 0755 "${ETC_DIR}" 2>/dev/null || true
chmod 0644 "${HAL0_TOML}" 2>/dev/null || true

# Pin the dashboard's built assets. Prod installs hal0 NON-editable, so the
# package's __file__ lives in the venv site-packages and the walk-up that
# finds ui/dist in a checkout no longer reaches it — point HAL0_UI_DIST at
# the `current` tree's ui/dist (follows atomic update swaps). Dev points at
# the editable checkout's build.
if [[ -n "${CURRENT_LINK}" ]]; then
    HAL0_UI_DIST_VAL="${CURRENT_LINK}/ui/dist"
else
    HAL0_UI_DIST_VAL="${UI_DIST}"
fi

API_ENV="${ETC_DIR}/api.env"
if [[ ! -f "${API_ENV}" ]]; then
    cat > "${API_ENV}" <<EOF
HAL0_PORT=${HAL0_PORT}
HAL0_LOG_LEVEL=info
HAL0_UI_DIST=${HAL0_UI_DIST_VAL}
# Memory subsystem (Hindsight engine + /mcp/memory + the Agent → Memory tab)
# is ENABLED by default as of v0.5 (brain re-enablement). Comment out to ship
# with memory dark. Needs the shared hindsight-api daemon (installer/systemd/
# hindsight-api.service); set [memory] engine = "cognee" to fall back.
HAL0_MEMORY_ENABLED=1
# HF_TOKEN — HuggingFace token for gated / large model pulls. Easiest path:
# set it in the dashboard (Settings -> Secrets -> HuggingFace token) for a live,
# no-restart update. Or uncomment below and \`systemctl restart hal0-api\`.
# HF_TOKEN=
# HAL0_TOOLBOX_IMAGE_VULKAN / HAL0_TOOLBOX_IMAGE_ROCM — optional overrides for
# the per-backend container image refs used by providers/llama_server.py.
# Unset = use the image pinned in the provider at release time.
EOF
    info "wrote ${API_ENV}"
fi

UPSTREAMS_TOML="${ETC_DIR}/upstreams.toml"
if [[ ! -f "${UPSTREAMS_TOML}" ]]; then
    cat > "${UPSTREAMS_TOML}" <<EOF
# External LLM upstreams — populated via the WebUI Providers tab,
# 'hal0 config edit' here, or directly with the API.
EOF
    info "wrote ${UPSTREAMS_TOML}"
fi

# TLS termination is upstream's job — hal0 no longer ships an edge
# proxy. The API binds 0.0.0.0:8080 directly and any TLS / certs are
# handled by Traefik / nginx / Cloudflare Tunnel in front of it. See
# docs/operate/tls.md for example proxy configs.
# HAL0_AUTH_ENABLED_FOR_RENDER kept for OWUI prewire compatibility — set
# to "0" until the auth-removal sweep collapses that flag entirely.
HAL0_AUTH_ENABLED_FOR_RENDER="0"

# OpenWebUI prewire env. Rendered via the just-installed venv so the
# defaults live in exactly one place (src/hal0/openwebui/env_writer.py).
# In dev mode we point HAL0_HOME at the prefix so the file lands under
# the dev tree alongside the rest of the config.
HAL0_HOME_FOR_OWUI=""
if [[ "${DEV_MODE}" -eq 1 ]]; then
    HAL0_HOME_FOR_OWUI="${PREFIX}"
fi
# HAL0_AUTH_ENABLED in the calling env flips OpenWebUI prewire defaults
# to single-sign-on (WEBUI_AUTH=True + WEBUI_AUTH_TRUSTED_EMAIL_HEADER).
if HAL0_HOME="${HAL0_HOME_FOR_OWUI}" HAL0_AUTH_ENABLED="${HAL0_AUTH_ENABLED_FOR_RENDER}" \
    "${VENV_DIR}/bin/python" -c \
    'from hal0.openwebui.env_writer import main; main()'; then
    info "wrote ${ETC_DIR}/openwebui.env"
else
    warn "failed to write openwebui.env — OpenWebUI may not start"
fi

ui_step "Systemd units"

# WorkingDirectory follows `current` in prod so a `hal0 update` symlink swap
# moves it to the new tree without rewriting the unit; dev uses the checkout.
API_WORKDIR="${CURRENT_LINK:-${PREFIX}}"
API_UNIT="${UNIT_DIR}/hal0-api.service"
cat > "${API_UNIT}" <<EOF
[Unit]
Description=hal0 API daemon
Documentation=https://github.com/hal0ai/hal0
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${HAL0_USER}
WorkingDirectory=${API_WORKDIR}
EnvironmentFile=${API_ENV}
ExecStart=${HAL0_BIN} serve --host ${API_BIND_HOST} --port \${HAL0_PORT}
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hal0-api

[Install]
WantedBy=multi-user.target
EOF
info "wrote ${API_UNIT}"

OPENWEBUI_UNIT_SRC="${REPO_ROOT}/packaging/systemd/hal0-openwebui.service"
OPENWEBUI_UNIT_DST="${UNIT_DIR}/hal0-openwebui.service"
# pin per release (#79) — single source of truth for the OpenWebUI image
# pulled by the background job below and referenced by the systemd unit.
# Bump the sha256 digest here on each hal0 release; update the matching
# digest in packaging/systemd/hal0-openwebui.service at the same time.
# IMPORTANT: this MUST be the multi-arch *manifest list* (index) digest, NOT a
# per-arch sub-manifest — a sub-manifest digest pins one architecture on every
# host (the prior arm64 pin died on amd64 with "exec ... Exec format error").
# Verify with: podman manifest inspect <ref> (mediaType ...manifest.list...).
OPENWEBUI_IMAGE="ghcr.io/open-webui/open-webui@sha256:7f1b0a1a50cfbac23da3b16f96bc968fd757b26dc9e54e93813d61768ea9184e"
if [[ -f "${OPENWEBUI_UNIT_SRC}" ]]; then
    cp "${OPENWEBUI_UNIT_SRC}" "${OPENWEBUI_UNIT_DST}"
    info "wrote ${OPENWEBUI_UNIT_DST}"
else
    warn "${OPENWEBUI_UNIT_SRC} not found — OpenWebUI unit not installed"
fi

# hal0-agent@ template + hermes drop-in (v0.3 PR-5). The template is the
# generic per-agent runner; the drop-in pins hermes-specific env.
# Lay them down whether or not bootstrap has been run — the shim's
# `cmd_serve` bails cleanly when the venv isn't there yet, and the
# `systemctl enable --now` for the hermes instance is gated on the
# venv existing (see "Service start" block below).
AGENT_UNIT_SRC="${REPO_ROOT}/installer/systemd/hal0-agent@.service"
AGENT_UNIT_DST="${UNIT_DIR}/hal0-agent@.service"
if [[ -f "${AGENT_UNIT_SRC}" ]]; then
    cp "${AGENT_UNIT_SRC}" "${AGENT_UNIT_DST}"
    info "wrote ${AGENT_UNIT_DST}"

    AGENT_OVERRIDE_SRC="${REPO_ROOT}/installer/systemd/hal0-agent@hermes.service.d/override.conf"
    AGENT_OVERRIDE_DST_DIR="${UNIT_DIR}/hal0-agent@hermes.service.d"
    if [[ -f "${AGENT_OVERRIDE_SRC}" ]]; then
        mkdir -p "${AGENT_OVERRIDE_DST_DIR}"
        cp "${AGENT_OVERRIDE_SRC}" "${AGENT_OVERRIDE_DST_DIR}/override.conf"
        info "wrote ${AGENT_OVERRIDE_DST_DIR}/override.conf"
    fi

    # Session-start hook: inject-system-state.sh cats /var/lib/hal0/STATE.md into
    # every new Hermes session (referenced by config.yaml.j2's
    # hooks.on_session_start). MUST land at the absolute /usr/lib/hal0 path
    # the config hardcodes (dev mode shadows it under PREFIX).
    if [[ "${DEV_MODE}" -eq 1 ]]; then
        LIB_DIR="${PREFIX}/usr/lib/hal0"
    else
        LIB_DIR="/usr/lib/hal0"
    fi
    HOOK_SRC="${REPO_ROOT}/installer/agents/hermes/hooks/inject-system-state.sh"
    if [[ -f "${HOOK_SRC}" ]]; then
        install -d "${LIB_DIR}/hermes-hooks"
        install -m 0755 "${HOOK_SRC}" "${LIB_DIR}/hermes-hooks/inject-system-state.sh"
        info "wrote ${LIB_DIR}/hermes-hooks/inject-system-state.sh"
    else
        warn "${HOOK_SRC} not found — Hermes session-state hook not installed"
    fi
else
    warn "${AGENT_UNIT_SRC} not found — hal0-agent@ template not installed"
fi

if [[ "${DEV_MODE}" -eq 0 ]]; then
    systemctl daemon-reload
    info "systemctl daemon-reload"
fi

# Kick off a background pull of the OpenWebUI image so the unit start
# below isn't blocked by a multi-hundred-MB download on first install.
# The unit also has ExecStartPre=podman pull (idempotent), so a missed
# background pull never breaks correctness — only first-boot latency.
if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 ]] && command -v podman >/dev/null 2>&1; then
    # Background the actual pull, but spin briefly so the user sees we
    # kicked it off. The hal0-openwebui unit also has ExecStartPre=podman
    # pull (idempotent), so missing this background pull only costs first
    # -boot latency, not correctness.
    (podman pull "${OPENWEBUI_IMAGE}" >/dev/null 2>&1 || true) &
    disown
    ui_spinner_run "Pulling ${OPENWEBUI_IMAGE} in background" sleep 3
fi

ui_step "Hardware probe"

if [[ -z "${HAL0_NO_PROBE:-}" ]]; then
    HAL0_HOME_FOR_PROBE=""
    if [[ "${DEV_MODE}" -eq 1 ]]; then
        HAL0_HOME_FOR_PROBE="${PREFIX}"
    fi
    # Inline Python: probe → write hardware.json → emit 4 hardware cards
    # → (if no slots/chat.toml exists yet) render one from
    # recommend_primary_slot() so the operator has a sensible default
    # waiting after `hal0 model pull <id>`. CHAT_TOML is exported so
    # the heredoc doesn't need to know the dev-mode prefix.
    CHAT_TOML="${ETC_DIR}/slots/chat.toml" \
    HAL0_HOME="${HAL0_HOME_FOR_PROBE}" "${VENV_DIR}/bin/python" - <<'PY'
import os
from pathlib import Path

from hal0.hardware.probe import HardwareProbe, format_cards
from hal0.hardware.recommend import recommend_primary_slot

p = HardwareProbe()
info = p.probe()
out = p.write(info)
print(f"  wrote {out}")
for line in format_cards(info):
    print(line)

# Pre-populate slots/chat.toml if absent. Idempotent: never overwrite
# an operator-edited file. Disabled by default — they pull a model and
# flip enabled = true when ready.
target = Path(os.environ["CHAT_TOML"])
if target.exists():
    print(f"  {target} exists — left alone")
else:
    rec = recommend_primary_slot(info)
    meta = rec.pop("_meta", {})
    import tomli_w  # hal0 install dep, always available here
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# hal0 chat slot — recommended for this hardware.\n"
        "# Created by install.sh on first install. Edit freely; the\n"
        "# installer will not overwrite this file on subsequent runs.\n"
        "#\n"
        f"# Backend rationale: {meta.get('rationale_backend', '')}\n"
        f"# Model rationale:   {meta.get('rationale_model', '')}\n"
        f"# Memory budget:     ~{meta.get('vram_budget_gb', '?')} GB\n"
        "#\n"
        "# Next: `hal0 model pull " + rec['model']['default'] + "`\n"
        "#       then flip enabled = true and `systemctl start hal0-slot@chat`\n"
        "\n"
    )
    target.write_text(header + tomli_w.dumps(rec))
    print(f"  wrote {target}  (backend={rec['backend']} model={rec['model']['default']})")
PY
else
    warn "skipping probe (HAL0_NO_PROBE=1)"
fi

# ── NPU prerequisites (FastFlowLM) ─────────────────────────────────────────
# The npu container slot runs FLM inside the hal0-toolbox-flm image, but a
# HOST FLM install is still required for the device-sanity probe
# (`flm validate`) and model cache management. Three pieces:
#
#   1. libxrt-npu2 — the AMDXDNA NPU runtime the host `flm` binary
#      dlopen()s at start. Best-effort from the host's configured apt
#      sources (hal0 no longer adds a third-party apt source for it);
#      missing libxrt only degrades the host probe — the container image
#      bundles its own runtime.
#
#   2. FLM transitive runtime libs (ffmpeg for the audio-transcribe path,
#      libboost-program-options for CLI parsing, libfftw3 for signal
#      processing). NOT hand-installed — the per-distro .deb (3.) declares the
#      exact versions for its release, and `apt-get install ./deb` pulls them
#      from the host's repos. (Hardcoding the ffmpeg6 SONAMEs was the old bug:
#      they don't exist on ffmpeg7/8 hosts like Ubuntu 25.10+/26.04.)
#
#   3. FastFlowLM .deb — pinned URL + SHA-256, fetched from upstream
#      releases. Verified BEFORE dpkg install; fail-soft if unreachable
#      (NPU-less hal0 still ships — FLM trio gates on `flm validate`).
#
# Refs: ADR-0009 (FLM trio NPU packing).
ui_step "NPU prerequisites (FastFlowLM)"

# Pinned FLM .deb — bump in lockstep with ADR-0009. v0.9.43 revalidated
# 2026-06-03 (LXC 105, Strix Halo NPU passthrough): flm validate ok
# (NPU FW 1.1.2.65), embed-gemma-300m-FLM → 768-dim, gemma3-1b-FLM chat
# ok. NOTE: 0.9.43 tightened CLI arg parsing — it rejects a flag passed
# twice, so FLMProvider.container_spec must never repeat a mode flag
# (--asr/--embed) the model already implies.
FLM_DEB_VERSION="0.9.43"
# Upstream ships a SEPARATE .deb per distro, each built against that release's
# ffmpeg/boost ABI: ubuntu24.04 (ffmpeg6/boost1.83), ubuntu25.10 + ubuntu26.04
# (ffmpeg7/8 / boost1.90), and debian13. Pick the artefact matching THIS host
# so `apt-get install ./deb` resolves the .deb's ffmpeg/boost/fftw deps from the
# host's own repos — no hardcoded SONAME list, and newer Ubuntu is first-class.
# (Older installers pinned ONLY the ubuntu24.04 build and then had to SKIP host
# FastFlowLM on every ffmpeg>=7 distro — even though the matching build existed.)
# SHA-256 pinned per artefact, verified on download 2026-06-15; if upstream
# rebuilds under the same tag these drift — bump in lockstep with FLM_DEB_VERSION.
_flm_sha_for_suffix() {
    case "$1" in
        ubuntu24.04) echo "4173fa82f0043a4ff14cf7b84c7d24188fac4ac64346942601b7d2b915308479" ;;
        ubuntu25.10) echo "7ff4d9a621c94aaa8bf783c05759dcd40ca43bdb5d07c31d4ccf04946dda0b69" ;;
        ubuntu26.04) echo "20ab2ba4f338837be2aabdf463d1369ffe56ad7f7c6a3eacd112630d983aa357" ;;
        debian13)    echo "acad5a520165956016bdadcb4983538f24f3ada9d1e5ac591c5e9ba11c0e22d1" ;;
    esac
}
# Resolve host distro -> .deb suffix. For Ubuntu, pick the HIGHEST shipped build
# whose version is <= the host's (sort -V), so a future 26.10/27.04 still uses
# the ffmpeg-newest ubuntu26.04 artefact rather than falling back to ffmpeg6.
# Empty suffix => no upstream build for this host (handled as an honest skip).
FLM_DEB_SUFFIX=""
case "$(distro_id)" in
    ubuntu)
        # `|| true`: _os_release_field returns 1 on a missing field, which would
        # abort under `set -e`; an empty version just falls through to no match.
        _flm_host_ver="$(_os_release_field VERSION_ID 2>/dev/null || true)"
        for _cand in 24.04 25.10 26.04; do
            if [[ "$(printf '%s\n%s\n' "${_cand}" "${_flm_host_ver:-0}" | sort -V | head -1)" == "${_cand}" ]]; then
                FLM_DEB_SUFFIX="ubuntu${_cand}"
            fi
        done
        ;;
    debian) FLM_DEB_SUFFIX="debian13" ;;
esac
FLM_DEB_SHA256="$(_flm_sha_for_suffix "${FLM_DEB_SUFFIX}")"
FLM_DEB_URL="https://github.com/FastFlowLM/FastFlowLM/releases/download/v${FLM_DEB_VERSION}/fastflowlm_${FLM_DEB_VERSION}_${FLM_DEB_SUFFIX}_amd64.deb"

if [[ "${DEV_MODE}" -eq 1 ]]; then
    # Dev installs don't touch the host's apt or third-party package
    # sources — devs install once manually (see installer/README.md).
    # We still log what *would* have happened so the dev knows the gap
    # exists for production installs.
    info "dev mode — skipping NPU prereqs (libxrt-npu2 + per-distro FastFlowLM .deb v${FLM_DEB_VERSION} + its ffmpeg/boost/fftw deps)"
    info "          install manually if exercising NPU paths: see installer/README.md"
elif ! command -v apt-get >/dev/null 2>&1; then
    # Non-Debian host (Fedora, Arch/CachyOS, openSUSE…). FastFlowLM upstream
    # ships only an Ubuntu .deb + a Windows .msi — there is no dnf/pacman/
    # zypper artefact and the libxrt-npu2 runtime is Debian-packaged too — so
    # the NPU prereqs genuinely can't be auto-installed here. This is an
    # upstream packaging limit, not a hal0 one: GPU (Vulkan/ROCm) and CPU
    # paths are fully supported on this distro; only the NPU/FLM trio waits
    # on a manual FastFlowLM install. Surface it honestly and keep going.
    warn "$(distro_pretty): skipping NPU prereqs — FastFlowLM ships an Ubuntu .deb only (upstream)"
    warn "  GPU (Vulkan/ROCm) + CPU paths work normally; NPU/FLM slots stay disabled until you install"
    warn "  FastFlowLM ${FLM_DEB_VERSION} manually (see installer/README.md). 'flm validate' gates the NPU trio."
else
    ui_spinner_run "apt-get update (refresh package index)" \
        apt-get update -qq

    # FLM host-support gate. Upstream now ships a per-distro .deb (ubuntu24.04 /
    # ubuntu25.10 / ubuntu26.04 / debian13), each pinned against that release's
    # ffmpeg/boost ABI — so the host probe works on ffmpeg6/7/8 alike. The
    # resolution above set FLM_DEB_SUFFIX iff a matching build exists; when it
    # didn't, skip host-FLM with ONE honest line. The npu CONTAINER slot bundles
    # its own runtime, so NPU inference is unaffected — only the host `flm
    # validate` probe is disabled.
    if [[ -n "${FLM_DEB_SUFFIX}" ]]; then
        FLM_HOST_LIBS_OK=1
    else
        FLM_HOST_LIBS_OK=0
        warn "$(distro_pretty): no matching upstream FastFlowLM .deb (builds: ubuntu 24.04/25.10/26.04, debian 13)"
        warn "  skipping host FLM .deb. The npu container slot bundles its own runtime, so NPU inference is unaffected"
        warn "  (only the host 'flm validate' probe is disabled). Install FastFlowLM manually if upstream ships a build for this host."
    fi

    # libxrt-npu2 — best-effort. Available only when the host's apt sources
    # carry an XRT NPU build (e.g. a pre-existing vendor repo on upgraded
    # boxes). The FLM container image bundles its own runtime, so a miss here
    # only disables the HOST `flm validate` probe, not the npu slot itself.
    if apt-get install -y libxrt-npu2 >/dev/null 2>&1; then
        info "libxrt-npu2 installed (AMDXDNA NPU runtime for the host flm probe)"
    else
        warn "libxrt-npu2 not available from configured apt sources — host 'flm validate' may fail"
        warn "  the npu container slot bundles its own XRT runtime and is unaffected"
    fi

    # NB: the FLM ffmpeg/boost/fftw runtime libs are NOT pre-installed by hand
    # anymore — `apt-get install ./fastflowlm_*.deb` (below) pulls the exact
    # versions THIS .deb declares from the host's repos. That's why the build
    # must match the host distro: it's what makes the dep resolution clean on
    # ffmpeg7/8 hosts instead of demanding the ffmpeg6 SONAMEs that don't exist.

    # 3. FLM .deb. Fail-soft: if upstream is unreachable or the SHA-256
    #    doesn't match, warn + skip. NPU paths gate on `flm validate`
    #    succeeding later — GPU-only hal0 still ships fine.
    FLM_DEB_TMP="/tmp/fastflowlm_${FLM_DEB_VERSION}.deb"
    # 0 when the host-FLM gate above found no upstream .deb for this distro —
    # skip download+install entirely (no noisy exit).
    NEED_FLM_INSTALL="${FLM_HOST_LIBS_OK}"
    if command -v dpkg-query >/dev/null 2>&1 && \
       dpkg-query -W -f='${Version}\n' fastflowlm 2>/dev/null | grep -qx "${FLM_DEB_VERSION}"; then
        info "fastflowlm ${FLM_DEB_VERSION} already installed — skipping download"
        NEED_FLM_INSTALL=0
    fi

    if [[ "${NEED_FLM_INSTALL}" -eq 1 ]]; then
        # `curl -fsSL` — fail on HTTP error, silent, follow redirects.
        # Download to /tmp so a re-run doesn't keep a stale copy in the
        # install tree. -o to a deterministic path so the SHA-256 check
        # below can find it.
        if curl -fsSL -o "${FLM_DEB_TMP}" "${FLM_DEB_URL}"; then
            # SHA-256 verify BEFORE dpkg installs it. An all-zeroes
            # placeholder pin means the digest was never looked up;
            # HAL0_SKIP_FLM_SHA=1 bypasses the check for that case only.
            # Operators who set the env explicitly accept the trust trade.
            ACTUAL_SHA="$(sha256sum "${FLM_DEB_TMP}" | awk '{print $1}')"
            if [[ "${FLM_DEB_SHA256}" == "0000000000000000000000000000000000000000000000000000000000000000" ]]; then
                warn "FLM_DEB_SHA256 is the placeholder — pin the real checksum in install.sh before v0.2 ships"
                warn "  observed: ${ACTUAL_SHA}"
                if [[ "${HAL0_SKIP_FLM_SHA:-0}" != "1" ]]; then
                    warn "  skipping FLM install (set HAL0_SKIP_FLM_SHA=1 to accept the placeholder)"
                    rm -f "${FLM_DEB_TMP}"
                    NEED_FLM_INSTALL=0
                fi
            elif [[ "${ACTUAL_SHA}" != "${FLM_DEB_SHA256}" ]]; then
                warn "FLM .deb SHA-256 mismatch — refusing to install"
                warn "  expected: ${FLM_DEB_SHA256}"
                warn "  observed: ${ACTUAL_SHA}"
                rm -f "${FLM_DEB_TMP}"
                NEED_FLM_INSTALL=0
            fi
        else
            warn "FLM .deb download failed (${FLM_DEB_URL})"
            warn "  NPU paths will be unavailable until you install FastFlowLM ${FLM_DEB_VERSION} manually"
            NEED_FLM_INSTALL=0
        fi
    fi

    if [[ "${NEED_FLM_INSTALL}" -eq 1 ]]; then
        # `apt-get install -y /path/to.deb` pulls transitive deps from
        # apt (cleaner than `dpkg -i` + manual `apt-get -f install`).
        if ui_spinner_run "Installing FastFlowLM ${FLM_DEB_VERSION}" \
            apt-get install -y "${FLM_DEB_TMP}"; then
            rm -f "${FLM_DEB_TMP}"
            # Smoke-test the binary. `flm validate` returns 0 when the
            # NPU runtime is reachable AND the binary is wired up — it's
            # the upstream-recommended health check. Soft on failure:
            # missing NPU hardware (e.g., installing on a non-Strix-Halo
            # host) is a perfectly valid configuration.
            if command -v flm >/dev/null 2>&1; then
                if flm validate >/dev/null 2>&1; then
                    info "flm validate ok — NPU runtime reachable"
                else
                    warn "flm validate failed — NPU hardware may be absent or libxrt-npu2 mismatched"
                    warn "  GPU paths still work; NPU slots will stay disabled until 'flm validate' passes"
                fi
            else
                warn "flm not on PATH after .deb install — check /var/log/apt/term.log"
            fi
        else
            warn "FastFlowLM ${FLM_DEB_VERSION} install failed — NPU paths will be unavailable"
            rm -f "${FLM_DEB_TMP}"
        fi
    fi
fi

# ── Container slot seeds (A10) ────────────────────────────────────────────
# Pre-populate /etc/hal0/slots/{npu,tts}.toml if absent. Idempotent: never
# overwrite an operator-edited file. Each slot is seeded unconditionally so
# the dashboard can show its tile on any hal0 install; each gates on its own
# runtime validation at load time. runtime=container + profile=<X> routes
# to ContainerProvider (podman).
#
# Single source of truth: seeds are COPIED from the repo tree
# (installer/etc-hal0/slots/<name>.toml — same files the schema tests
# validate), never duplicated inline. Present in every install flow:
# the release tarball ships the whole installer/ dir (release.yml
# `cp -a installer "${STAGE}/"`), git checkouts carry it, and the prod
# rsync to ${PREFIX} (which REPO_ROOT is re-pointed at) has no exclude
# that touches installer/.
for seed_slot in npu tts rerank utility img; do
    SLOT_TOML="${ETC_DIR}/slots/${seed_slot}.toml"
    SLOT_SRC="${REPO_ROOT}/installer/etc-hal0/slots/${seed_slot}.toml"
    if [[ -f "${SLOT_TOML}" ]]; then
        info "${seed_slot} slot: ${SLOT_TOML} exists — left alone"
    else
        [[ -f "${SLOT_SRC}" ]] \
            || die "installer bundle incomplete: ${SLOT_SRC} missing (installer/etc-hal0/ should ship with every release tree)"
        mkdir -p "${ETC_DIR}/slots"
        cp "${SLOT_SRC}" "${SLOT_TOML}"
        chmod 0644 "${SLOT_TOML}"
        info "seeded ${seed_slot} slot → ${SLOT_TOML}"
    fi
done

# ── hal0 system user ────────────────────────────────────────────────────────
# A dedicated `hal0` system user/group runs the non-root hal0 services:
# hal0-agent@<id> (the Hermes runner), hermes-gateway, and the shared
# hindsight-api memory engine. It also owns the HF cache under
# ${VAR_DIR}/.cache so agent-side HuggingFace pulls work without
# escalating. Slot inference itself runs in podman containers supervised
# by hal0-slot@<name>.service — no daemon user needed there.
ui_step "System user"

if [[ "${DEV_MODE}" -eq 1 ]]; then
    # Dev installs never create system users or touch systemd.
    info "dev mode — skipping hal0 system user creation"
else
    # 1. hal0 system user/group. System user (UID < 1000), no login
    #    shell, home at ${VAR_DIR} so any stray `~`-relative writes from
    #    agent processes land somewhere sane. Idempotent via `getent`.
    if ! getent group hal0 >/dev/null 2>&1; then
        groupadd --system hal0
        info "created group hal0"
    fi
    if ! getent passwd hal0 >/dev/null 2>&1; then
        useradd --system --gid hal0 --home-dir "${VAR_DIR}" \
            --shell /usr/sbin/nologin \
            --comment "hal0 service user" \
            hal0
        info "created user hal0 (system, no login)"
    fi

    # GPU device access (issue #420). Keeps hal0-user processes (agents,
    # diagnostics) able to read /dev/kfd + /dev/dri/renderD* when they
    # probe the GPU. Slot containers get their devices from podman
    # directly and don't depend on this. Idempotent; only adds groups
    # that actually exist on the host (a non-GPU box / CI runner simply
    # has neither).
    KFD_GROUPS=""
    for _g in render video; do
        if getent group "${_g}" >/dev/null 2>&1; then
            KFD_GROUPS="${KFD_GROUPS:+${KFD_GROUPS},}${_g}"
        fi
    done
    if [[ -n "${KFD_GROUPS}" ]]; then
        usermod -aG "${KFD_GROUPS}" hal0
        info "added hal0 to groups: ${KFD_GROUPS}"
    fi

    # HuggingFace hub cache (#275 bug 4). The hal0 user's HOME is
    # ${VAR_DIR} (per useradd above), so HF's default cache lands at
    # ${VAR_DIR}/.cache/huggingface/hub. Pre-create the leaf dir + give
    # hal0 ownership of the cache tree (NOT the whole VAR_DIR — slot
    # state.json + registry are written by hal0-api which runs as
    # ${HAL0_USER}, default root) so hal0-user processes can download
    # HF assets without a PermissionError on first use.
    mkdir -p "${VAR_DIR}/.cache/huggingface/hub"
    chown -R hal0:hal0 "${VAR_DIR}/.cache"

    # Shared STATE.md (#766). The hermes agent runs as hal0 and its
    # render-context (re)writes ${VAR_DIR}/STATE.md — the live snapshot the
    # Claude session-start hook cats — via a tmp+rename that needs *directory*
    # write on ${VAR_DIR}. Grant the hal0 group write on the top dir
    # (setgid so new entries inherit group hal0). Ownership stays root, so
    # root-owned slots/registry/models are untouched; this preserves the
    # ".cache NOT the whole VAR_DIR" posture above.
    #
    # NOT sticky (#766 follow-up): render runs as root during provisioning
    # (creating a root-owned STATE.md) but as hal0 at runtime — the hal0
    # rename-over of a root-owned STATE.md needs plain directory write, which
    # the sticky bit would deny (it'd require owning the existing file). The
    # group-write grant is what systemd's `ReadWritePaths=/var/lib/hal0`
    # already assumes, so this just makes the filesystem agree.
    chgrp hal0 "${VAR_DIR}"
    chmod 2775 "${VAR_DIR}"
    touch "${VAR_DIR}/STATE.md"
    chown hal0:hal0 "${VAR_DIR}/STATE.md"

fi

# ── Bundle picker manifests (ADR-0010 / PR-17) ────────────────────────────
# Ship the five first-run bundle manifests (hal0-Lite / hal0-Default /
# hal0-Pro / hal0-Max + LMX-Omni-52B-Halo) into the runtime collections
# directory. The bundle picker UI on first dashboard load reads from
# /var/lib/hal0/models/collections/omni/ — without this copy, the API
# falls back to the in-tree dev manifests, which only exist on a source
# checkout, not in a packaged install.
#
# Idempotent: re-running install.sh overwrites each manifest. Manifests
# are tiny (a few KB) and the copy is fast, so we don't bother with
# content hashing.
ui_step "Bundle picker manifests"

BUNDLES_SRC="${REPO_ROOT}/installer/manifests/omni"
BUNDLES_DST="${VAR_DIR}/models/collections/omni"

if [[ -d "${BUNDLES_SRC}" ]]; then
    mkdir -p "${BUNDLES_DST}"
    if cp -f "${BUNDLES_SRC}"/*.json "${BUNDLES_DST}/" 2>/dev/null; then
        chown -R hal0:hal0 "${VAR_DIR}/models/collections" 2>/dev/null || true
        info "installed bundle manifests → ${BUNDLES_DST}"
    else
        warn "failed to copy bundle manifests from ${BUNDLES_SRC}"
    fi
else
    warn "bundle manifest source ${BUNDLES_SRC} not found; picker will fall back to in-tree defaults"
fi

# ── Bundled agent skills (drop-in skill library) ──────────────────────────
# Ship hal0's own agent skills to /usr/share/hal0/skills (read-only source).
# The hermes provision's context_link phase (_mirror_bundled_skills) symlinks
# each one into /etc/hal0/agent-skills, which the rendered config.yaml lists in
# skills.external_dirs — so a fresh agent comes up with the bundled skills
# already loaded. Also create a writable drop-in dir at /var/lib/hal0/skills
# (also on external_dirs): new skills install just by dropping a folder in, and
# editing is a plain file edit. This must run BEFORE the hermes provision in
# "Service start" so the mirror finds the shipped source. Idempotent.
ui_step "Bundled agent skills"

SKILLS_SRC="${REPO_ROOT}/installer/agent-skills"
SKILLS_SHIP="/usr/share/hal0/skills"
SKILLS_DROPIN="${VAR_DIR}/skills"
AGENT_SKILLS_MIRROR="${ETC_DIR}/agent-skills"

if [[ "${DEV_MODE}" -eq 0 ]]; then
    mkdir -p "${SKILLS_SHIP}" "${AGENT_SKILLS_MIRROR}" "${SKILLS_DROPIN}"
    if [[ -d "${SKILLS_SRC}" ]] && compgen -G "${SKILLS_SRC}/*" >/dev/null; then
        cp -rf "${SKILLS_SRC}"/* "${SKILLS_SHIP}/"
        info "shipped $(find "${SKILLS_SRC}" -mindepth 1 -maxdepth 1 -type d | wc -l) hal0 skill(s) → ${SKILLS_SHIP}"
    else
        info "no bundled skills at ${SKILLS_SRC} — drop-in dirs still created"
    fi
    # Writable drop-in (agent runs as hal0): add/edit skills at runtime here.
    chown -R hal0:hal0 "${SKILLS_DROPIN}" 2>/dev/null || true
    info "skill drop-in: ${SKILLS_DROPIN} (drop a folder here to add a skill; editable)"
else
    info "dev mode — skipping system skill install (/usr/share/hal0/skills)"
fi

ui_step "Service start"

if [[ "${DEV_MODE}" -eq 1 || "${NO_START}" -eq 1 ]]; then
    warn "not starting services automatically (dev / --no-start)."
    warn "  start manually: ${HAL0_BIN} serve --host ${API_BIND_HOST} --port ${HAL0_PORT}"
else
    systemctl enable --now hal0-api
    if wait_active hal0-api 15; then
        info "hal0-api is running"
    else
        warn "hal0-api failed to start; check 'journalctl -u hal0-api -n 40'"
    fi

    # ── Memory engine (Hindsight) ─────────────────────────────────────────────
    # Stand up the local hindsight-api daemon (the shared memory brain) and seed
    # the global shared bank + the hermes private bank. The unit ships in
    # installer/systemd/ but was never installed before, so a fresh box had
    # HAL0_MEMORY_ENABLED=1 (api.env above) pointing at a dead engine. The daemon
    # runs in its own venv at ${VAR_DIR}/memory/hindsight/.venv (pinned to the
    # version CT105 runs) with an embedded postgres + local BGE/MiniLM models;
    # its extraction/reflection LLM is hal0/utility on :8080 (used lazily — the
    # unit sets HINDSIGHT_API_SKIP_LLM_VERIFICATION so it binds without a loaded
    # model). Escape hatch: HAL0_SKIP_HINDSIGHT=1.
    HS_DIR="${VAR_DIR}/memory/hindsight"
    HINDSIGHT_UNIT_SRC="${REPO_ROOT}/installer/systemd/hindsight-api.service"
    if [[ "${HAL0_SKIP_HINDSIGHT:-0}" -ne 1 && -f "${HINDSIGHT_UNIT_SRC}" ]]; then
        info "setting up Hindsight memory engine (venv + daemon) — this can take a few minutes…"
        mkdir -p "${HS_DIR}/hf-cache" "${HS_DIR}/.cache"
        if [[ ! -x "${HS_DIR}/.venv/bin/hindsight-api" ]]; then
            "${PY}" -m venv "${HS_DIR}/.venv"
            hs_pip="${HS_DIR}/.venv/bin/pip"
            "${hs_pip}" install --upgrade pip wheel -q 2>/dev/null || true
            if ! "${hs_pip}" install "hindsight-api==0.7.2" -q; then
                # Newer distros (Ubuntu 25.10+/26.04) ship Python 3.14, which
                # litellm>=1.83.14 (a hindsight-api dep) gates out via its
                # requires-python metadata — even though the pinned stack runs
                # fine on 3.14 (litellm dropped the 3.14 classifier over a
                # since-resolved fastuuid wheel gap; BerriAI/litellm#26343).
                # Retry past the metadata gate; the hindsight-api /health poll
                # below is the real gate on whether the engine actually came up.
                hs_pyver="$("${HS_DIR}/.venv/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo '?')"
                warn "hindsight-api hit a requires-python gate on Python ${hs_pyver}; retrying with --ignore-requires-python"
                if ! "${hs_pip}" install --ignore-requires-python "hindsight-api==0.7.2" -q; then
                    warn "hindsight-api install failed — memory engine will be unavailable"
                fi
            fi
        else
            info "hindsight-api venv already present — skipping pip install"
        fi
        # The unit runs as hal0 with HOME=${HS_DIR}; hand it the whole tree.
        chown -R hal0:hal0 "${VAR_DIR}/memory" 2>/dev/null || true
        if [[ -x "${HS_DIR}/.venv/bin/hindsight-api" ]]; then
            install -m644 "${HINDSIGHT_UNIT_SRC}" /etc/systemd/system/hindsight-api.service
            systemctl daemon-reload
            systemctl enable --now hindsight-api
            # First boot: embedded pg0 init + local embed/rerank model load can
            # take ~30-60s. Skip-LLM-verification means it binds without a model.
            hs_up=0
            for _ in $(seq 1 40); do
                if curl -fsS "http://127.0.0.1:9177/health" >/dev/null 2>&1; then hs_up=1; break; fi
                sleep 3
            done
            if [[ "${hs_up}" -eq 1 ]]; then
                info "hindsight-api is running (memory engine on 127.0.0.1:9177)"
                # Seed banks through hal0-api (idempotent import, config-by-field):
                # `shared` = the global cross-agent brain; `private__hermes-agent`
                # = hermes' private store. Other private/project banks lazy-create.
                _seed_bank() {
                    curl -fsS -m 20 -X POST \
                        "http://127.0.0.1:${HAL0_PORT}/api/memory/banks/$1/import" \
                        -H "Content-Type: application/json" -H "X-hal0-Agent: installer" \
                        -d "$2" >/dev/null 2>&1
                }
                if _seed_bank shared '{"version":"1","bank":{"retain_mission":"Extract technical decisions and rationale, gotchas and fixes, PRs and status changes, conventions, commands, endpoints, flags, incidents and resolutions, and cross-session coordination facts. Ignore routine edits, transient state, secrets, and anything already in git.","enable_observations":true,"disposition_skepticism":4,"disposition_literalism":4,"disposition_empathy":1}}' \
                    && _seed_bank private__hermes-agent '{"version":"1","bank":{"retain_mission":"This agent private working notes, scratch decisions, and per-task state. Never store shared facts here.","disposition_skepticism":4,"disposition_literalism":4,"disposition_empathy":2}}'; then
                    info "seeded memory banks: shared (global) + private__hermes-agent"
                else
                    warn "memory bank seeding incomplete — banks also lazy-create on first write"
                fi
            else
                warn "hindsight-api not healthy yet; check 'journalctl -u hindsight-api -n 40'"
            fi
        fi
    fi

    if [[ -f "${OPENWEBUI_UNIT_DST}" ]]; then
        # OpenWebUI runs as a podman container (ExecStart=podman run …) — the
        # same runtime as the slots, so the preflight that installed podman
        # already satisfies it. Without a usable runtime the unit would
        # restart-loop with status=203/EXEC, so guard the enable anyway — the
        # dashboard/API are unaffected and the built-in chat works without it.
        if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
            systemctl enable --now hal0-openwebui
            # OpenWebUI can take a moment to come up while it pulls the
            # image / initialises its sqlite db. Don't fail the installer
            # on a slow first boot; just surface the status.
            if wait_active hal0-openwebui 30; then
                info "hal0-openwebui is running (chat at :3001)"
            else
                warn "hal0-openwebui not yet active; check 'journalctl -u hal0-openwebui -n 40'"
            fi
        else
            # A prior install on a box that has since lost its runtime (or an
            # upgrade where openwebui was enabled) may have left the unit
            # restart-looping with 203/EXEC. Actively quiesce it so the
            # status reflects reality (inactive, not failed/looping).
            systemctl disable --now hal0-openwebui >/dev/null 2>&1 || true
            systemctl reset-failed hal0-openwebui >/dev/null 2>&1 || true
            info "hal0-openwebui not started — no usable container runtime"
            info "  install podman, then: systemctl enable --now hal0-openwebui  (chat at :3001)"
        fi
    fi

    # hal0-agent@hermes — provision Hermes end-to-end on a FRESH install so the
    # box comes up with a fully-configured agent (config.yaml + MCP wiring +
    # personas + skills + install artifacts) WITHOUT a manual bootstrap step.
    # `hal0 agent install hermes` runs the toolchain (python·venv·pip·pipx)
    # then the full bootstrap pipeline in the foreground, chowns the
    # provisioned trees to the hal0 runtime user, and enables the unit. It is
    # multi-minute (pip-installs hermes-agent), so it streams into the install
    # log. Escape hatch: HAL0_SKIP_HERMES=1 for operators who don't want the
    # bundled agent. On UPGRADE installs the venv already exists, so the
    # provision is skipped and the block below just (re)enables the unit.
    # hal0-api is already up at this point (enabled + wait_active above), which
    # the bootstrap preflight requires.
    if [[ -f "${AGENT_UNIT_DST}" && ! -x "/var/lib/hal0/venvs/hermes/bin/hermes" ]]; then
        if [[ "${HAL0_SKIP_HERMES:-0}" -eq 1 ]]; then
            info "skipping hermes provisioning (HAL0_SKIP_HERMES=1) — run '${HAL0_BIN} agent install hermes' later"
        else
            info "provisioning Hermes agent (toolchain + bootstrap) — this can take a few minutes…"
            if "${HAL0_BIN}" agent install hermes; then
                info "hermes provisioned — config.yaml + MCP servers + skills wired"
            else
                warn "hermes provisioning failed — run '${HAL0_BIN} agent install hermes' manually"
                warn "  diagnose with '${HAL0_BIN} agent log hermes' / '${HAL0_BIN} agent status hermes'"
            fi
        fi
    fi

    # Enable the unit + gateway for both fresh (just-provisioned) and upgrade
    # installs. `hal0 agent install hermes` already enables the agent unit, so
    # the enable here is idempotent; it also covers the upgrade path where no
    # provision ran, plus the system-scope gateway (which the provision does
    # not install).
    if [[ -f "${AGENT_UNIT_DST}" && -x "/var/lib/hal0/venvs/hermes/bin/hermes" ]]; then
        # Non-fatal: a hermes start hiccup must NOT abort an otherwise-good
        # install (hal0-api + chat are already up). Under `set -e` a failed
        # `enable --now` (e.g. the unit tripped StartLimitBurst) would fire the
        # ERR trap; `|| warn` downgrades it to the wait_active warning below.
        systemctl enable --now hal0-agent@hermes.service \
            || warn "hal0-agent@hermes enable returned non-zero — continuing (check 'journalctl -u hal0-agent@hermes -n 40')"
        if wait_active hal0-agent@hermes.service 20; then
            info "hal0-agent@hermes is running (chat at 127.0.0.1:9119, proxied by hal0-api)"
        else
            warn "hal0-agent@hermes not yet active; check 'journalctl -u hal0-agent@hermes -n 40'"
        fi
        # Gateway (Telegram/Discord) also runs as a SYSTEM service under
        # the hal0 user — same posture as the dashboard above. The
        # bootstrap provisioner has already written the secrets drop-in
        # (/etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf);
        # hermes_cli lays down the main unit here. daemon-reload picks up
        # the drop-in BEFORE first start so platforms connect on boot.
        # HERMES_HOME is unset so the generator bakes the hal0 default
        # (~/.hermes), not a value inherited from the installer env.
        info "installing system-scope hermes gateway (User=hal0)"
        env -u HERMES_HOME /var/lib/hal0/venvs/hermes/bin/hermes gateway install --system --run-as-user hal0 \
            || warn "hermes gateway install failed — Telegram/Discord bridge unavailable; continuing"
        systemctl daemon-reload
        systemctl enable --now hermes-gateway.service \
            || warn "hermes-gateway enable returned non-zero — continuing (check 'journalctl -u hermes-gateway -n 40')"
        if wait_active hermes-gateway.service 20; then
            info "hermes-gateway is running (Telegram/Discord)"
        else
            warn "hermes-gateway not yet active; check 'journalctl -u hermes-gateway -n 40'"
        fi
    elif [[ -f "${AGENT_UNIT_DST}" ]]; then
        # No venv after the provision block: it was skipped (HAL0_SKIP_HERMES=1)
        # or failed. The warnings above already explain; this is the summary line.
        info "hal0-agent@hermes not enabled — provision with '${HAL0_BIN} agent install hermes'"
    fi

fi

HOST="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[[ -z "${HOST}" ]] && HOST=localhost

# ── Reachability discovery ─────────────────────────────────────────────────
# Build a list of "label\turl" pairs covering every interface the user
# might browse from. Always-tab-separated so the renderer can split.
# Failures are silent: a missing tailscale binary just means no
# Tailscale entry; nothing in this block can fail the installer.
REACH_LINES=()

DASHBOARD_URL="http://${HOST}:${HAL0_PORT}/"
# All IPv4 addresses on this host. `hostname -I` already excludes
# loopback.
if command -v hostname >/dev/null 2>&1; then
    for ip in $(hostname -I 2>/dev/null); do
        REACH_LINES+=("LAN"$'\t'"http://${ip}:${HAL0_PORT}/")
    done
fi
# Tailscale — show whichever tailnet IPs are present. Never fatal.
if command -v tailscale >/dev/null 2>&1; then
    for ts in $(tailscale ip -4 2>/dev/null) $(tailscale ip -6 2>/dev/null); do
        # Bracket IPv6 for URL grammar.
        if [[ "$ts" == *:* ]]; then
            REACH_LINES+=("Tailscale"$'\t'"http://[${ts}]:${HAL0_PORT}/")
        else
            REACH_LINES+=("Tailscale"$'\t'"http://${ts}:${HAL0_PORT}/")
        fi
    done
fi
# Up to 2 globally-routable IPv6 addresses — useful for direct LAN
# access on dual-stack networks. `ip` is in iproute2; on non-Linux
# exotic minimal containers it may be missing — silent skip.
if command -v ip >/dev/null 2>&1; then
    v6_addrs=$(ip -6 addr show scope global 2>/dev/null | awk '/inet6/{print $2}' | cut -d/ -f1 | head -2)
    for v6 in $v6_addrs; do
        REACH_LINES+=("IPv6"$'\t'"http://[${v6}]:${HAL0_PORT}/")
    done
fi

# ── Live hello prompt ──────────────────────────────────────────────────────
# Fires only when ALL of the following are true:
#   * not --dev
#   * not --no-start
#   * HAL0_NO_HELLO is unset
#   * the hal0 API is responding to /api/health
#   * at least one model is already pulled (so we don't silently spend
#     5+ minutes downloading on first install — cheap operators can
#     `hal0 model pull qwen3-4b` and re-run the installer to see the
#     greeting fire)
# Every step is wrapped so failure logs a single info line and moves on.
HELLO_RESULT=""
if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 && -z "${HAL0_NO_HELLO:-}" ]]; then
    HELLO_BASE="http://127.0.0.1:${HAL0_PORT}"
    # `wait_active hal0-api` only proves systemd marked the unit active —
    # uvicorn may still be importing the app and not yet bound to the
    # port. A single probe here races that cold start and prints a false
    # "API not responding"; poll /api/health for a few seconds first.
    HELLO_API_UP=0
    for _ in $(seq 1 15); do
        if curl -sf --max-time 2 "${HELLO_BASE}/api/health" >/dev/null 2>&1; then
            HELLO_API_UP=1
            break
        fi
        sleep 1
    done
    if [[ "${HELLO_API_UP}" -eq 1 ]]; then
        # Find a pulled model. `hal0 model list --installed` returns one
        # id per line. We pick the first; on a fresh install this is
        # empty and we skip with a hint.
        FIRST_MODEL="$("${HAL0_BIN}" model list --installed 2>/dev/null | awk 'NR==1{print $1}' || true)"
        if [[ -z "${FIRST_MODEL}" || "${FIRST_MODEL}" == "ID" ]]; then
            HELLO_RESULT="skipped: no model pulled — try '${HAL0_BIN} model pull qwen3-4b' then rerun installer"
        else
            HELLO_SLOT="hello"
            # Best-effort slot create + load. Errors don't stop us.
            "${HAL0_BIN}" slot create "${HELLO_SLOT}" --backend cpu --provider llama-server >/dev/null 2>&1 || true
            "${HAL0_BIN}" model assign "${FIRST_MODEL}" --slot "${HELLO_SLOT}" >/dev/null 2>&1 || true
            "${HAL0_BIN}" slot load "${HELLO_SLOT}" >/dev/null 2>&1 || true

            # Wait up to 30 s for the slot to report ready.
            HELLO_DEADLINE=$((SECONDS + 30))
            HELLO_READY=0
            while (( SECONDS < HELLO_DEADLINE )); do
                if curl -sf --max-time 2 "${HELLO_BASE}/api/slots/${HELLO_SLOT}" 2>/dev/null \
                   | grep -q '"state":"ready"'; then
                    HELLO_READY=1
                    break
                fi
                sleep 1
            done

            if [[ "${HELLO_READY}" -eq 1 ]]; then
                # Stream the greeting. python -c parses SSE and echoes
                # content deltas in real time. 20 s ceiling on the
                # whole exchange so a sluggish backend doesn't stall
                # the installer.
                printf '\n   %shal0 says:%s\n\n   %s' "${BOLD}" "${RST}" "${AMBER}"
                set +e
                timeout 20 curl -sN -X POST \
                    -H 'Content-Type: application/json' \
                    -d "{\"model\":\"${HELLO_SLOT}\",\"stream\":true,\"messages\":[{\"role\":\"system\",\"content\":\"You are hal0, just installed on a new machine. Greet the user in one short friendly sentence.\"},{\"role\":\"user\",\"content\":\"Say hi.\"}]}" \
                    "${HELLO_BASE}/v1/chat/completions" 2>/dev/null \
                | "${VENV_DIR}/bin/python" -c '
import json, sys
for raw in sys.stdin:
    raw = raw.strip()
    if not raw.startswith("data: "):
        continue
    payload = raw[6:]
    if payload == "[DONE]":
        break
    try:
        delta = json.loads(payload)["choices"][0]["delta"].get("content", "")
    except Exception:
        continue
    if delta:
        sys.stdout.write(delta)
        sys.stdout.flush()
'
                rc=$?
                set -e
                printf '%s\n\n' "${RST}"
                if [[ $rc -ne 0 ]]; then
                    HELLO_RESULT="hello stream errored (exit ${rc}) — slot stayed up; try the dashboard"
                else
                    HELLO_RESULT="ok"
                fi
            else
                HELLO_RESULT="skipped: slot ${HELLO_SLOT} not ready within 30s — check 'journalctl -u hal0-slot@${HELLO_SLOT}'"
            fi
        fi
    else
        HELLO_RESULT="skipped: API not responding at ${HELLO_BASE}/api/health"
    fi
fi
if [[ -n "${HELLO_RESULT}" && "${HELLO_RESULT}" != "ok" ]]; then
    info "live hello ${HELLO_RESULT}"
fi

# ── QR code ────────────────────────────────────────────────────────────────
# Render a QR for DASHBOARD_URL above the summary box if qrencode is on
# PATH. Skipped in --dev / --no-start (no daemon listening, so the URL
# would 404). HAL0_NO_QR=1 forces skip on headless runs; missing
# qrencode binary is a silent soft-skip — it's documented as optional.
if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 \
      && -z "${HAL0_NO_QR:-}" ]] \
   && command -v qrencode >/dev/null 2>&1; then
    printf '\n   %sScan to open:%s  %s%s%s\n\n' "${DIM}" "${RST}" "${BLU}" "${DASHBOARD_URL}" "${RST}"
    qrencode -t ANSIUTF8 -m 2 "${DASHBOARD_URL}" 2>/dev/null | sed 's/^/   /' || true
fi

# Build the summary lines into an array, then hand off to ui_box. Lines
# are pre-padded so the column layout reads cleanly inside the box.
SUMMARY_LINES=(
    "$(printf 'CLI         %s%s%s' "${BLU}" "${HAL0_BIN}" "${RST}")"
    "$(printf 'Config      %s%s%s' "${BLU}" "${ETC_DIR}" "${RST}")"
    "$(printf 'Data        %s%s%s' "${BLU}" "${VAR_DIR}" "${RST}")"
)
if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 ]]; then
    # hal0-api binds 0.0.0.0:8080. TLS / DNS is whatever upstream proxy
    # you put in front. Auth (password + tokens) still works — set it
    # up in the first-run wizard or via the dashboard Settings panel.
    SUMMARY_LINES+=(
        "$(printf 'Dashboard   %shttp://%s:%s%s' "${BLU}" "${HOST}" "${HAL0_PORT}" "${RST}")"
        "$(printf 'Chat        %shttp://%s:3001%s' "${BLU}" "${HOST}" "${RST}")"
        "$(printf 'TLS         %supstream-only (front with Traefik / nginx / Cloudflare Tunnel)%s' "${DIM}" "${RST}")"
        "$(printf 'Auth        %sopen on the trusted LAN — front with a reverse proxy if exposed%s' "${DIM}" "${RST}")"
        "$(printf 'Logs        %sjournalctl -fu hal0-api%s' "${DIM}" "${RST}")"
    )
fi

# Reachability list — only shown when we have entries beyond the
# already-printed Dashboard line, and only outside --dev / --no-start.
if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 && ${#REACH_LINES[@]} -gt 0 ]]; then
    SUMMARY_LINES+=(
        ""
        "$(printf '%sReach hal0 at:%s' "${BOLD}" "${RST}")"
    )
    for entry in "${REACH_LINES[@]}"; do
        label="${entry%%$'\t'*}"
        url="${entry##*$'\t'}"
        SUMMARY_LINES+=("$(printf '  %s%-12s%s %s%s%s' "${DIM}" "${label}" "${RST}" "${BLU}" "${url}" "${RST}")")
    done
fi

SUMMARY_LINES+=(
    ""
    "$(printf '%sNext steps:%s' "${BOLD}" "${RST}")"
    "$(printf '  %shal0 status%s         system + slot summary' "${BOLD}" "${RST}")"
    "$(printf '  %shal0 slot list%s      list configured slots' "${BOLD}" "${RST}")"
    "$(printf '  %shal0 model list%s     list known models' "${BOLD}" "${RST}")"
    "$(printf '  %shal0 config show%s    inspect %s/hal0.toml' "${BOLD}" "${RST}" "${ETC_DIR}")"
)

ui_box "hal0 is ready" "${SUMMARY_LINES[@]}"
