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

# Re-runnable pre-flight checks (preflight_systemd / preflight_python /
# preflight_docker / preflight_disk / preflight_ports / preflight_all).
# Sourcing only loads the functions — the installer dispatches the
# subset it cares about below. `hal0 doctor` shells the same file in
# executable mode to run preflight_all post-install.
# shellcheck source=lib/preflight.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/preflight.sh"

# Poll `systemctl is-active` for up to `timeout` seconds. Returns 0 the
# moment the unit reports active, 1 on timeout. Use instead of a flat
# `sleep N; is-active` so slow first boots (OpenWebUI pulling images,
# Lemonade backend init) don't get falsely flagged as failures.
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

# ── v0.1.x state detection ─────────────────────────────────────────────────
# v0.2 is a breaking change: slot architecture, model layout, and runtime have
# all changed (see lemonade-adoption-plan §9). The installer refuses to run
# over a v0.1.x state — there is no migration path beyond the registry, and
# the only safe option is back-up + wipe.
#
# Detection criterion (plan §9):
#   /etc/hal0/slots/*.toml exists  AND  /var/lib/hal0/lemonade/config.json absent.
#
# Both conditions matter: the slots dir is the v0.1.x fingerprint, and the
# absence of the Lemonade config is what tells us this isn't a previously
# completed v0.2 install with leftover v0.1.x slot files (PR-9 will retire
# /etc/hal0/slots/ but until then a partial v0.2 box could still have files
# there).
#
# Runs BEFORE the DEV_MODE check on purpose: dev mode does NOT bypass the
# refusal. Escape hatch is HAL0_SKIP_V01_DETECT=1 (CI + worktree tests).
# Idempotent — only checks two paths, no side effects.
if [[ "${HAL0_SKIP_V01_DETECT:-0}" != "1" ]]; then
    v01_slots_found=0
    if compgen -G "/etc/hal0/slots/*.toml" >/dev/null 2>&1; then
        v01_slots_found=1
    fi
    if [[ "${v01_slots_found}" -eq 1 && ! -f "/var/lib/hal0/lemonade/config.json" ]]; then
        cat <<'V01_EOF' >&2
hal0 v0.1.x detected. v0.2 is a breaking change — slot architecture, model layout,
and runtime have all changed. The installer will not overwrite a v0.1.x state.

To preserve your configuration:
  sudo tar czf hal0-v0.1-backup-$(date +%F).tar.gz /etc/hal0 /var/lib/hal0/registry

To wipe v0.1.x and start fresh:
  sudo systemctl stop 'hal0-slot@*' hal0-api
  sudo systemctl disable 'hal0-slot@*' hal0-api
  sudo rm -rf /etc/hal0 /var/lib/hal0 /opt/hal0
  # then re-run this installer

Or read the v0.2 migration notes: https://hal0.dev/docs/v0.2-upgrade
V01_EOF
        exit 1
    fi
    unset v01_slots_found
fi

# Banner first — before any info/warn so the brand greets the user
# rather than hiding behind a "Dev mode …" line.
ui_banner

HAL0_PORT="${HAL0_PORT:-8080}"
HAL0_USER="${HAL0_USER:-root}"
PY="${HAL0_PYTHON:-python3}"

# API binds 0.0.0.0:8080 unconditionally. TLS is upstream's job — see
# the comment on TLS posture near the flag parser.
API_BIND_HOST="0.0.0.0"

if [[ "${DEV_MODE}" -eq 1 ]]; then
    PREFIX="${HAL0_PREFIX:-${PWD}/.hal0ai}"
    ETC_DIR="${PREFIX}/etc/hal0"
    VAR_DIR="${PREFIX}/var/lib/hal0"
    UNIT_DIR="${PREFIX}/etc/systemd/system"
    info "Dev mode — all paths under ${PREFIX}"
else
    PREFIX="${HAL0_PREFIX:-/opt/hal0}"
    ETC_DIR="/etc/hal0"
    VAR_DIR="/var/lib/hal0"
    UNIT_DIR="/etc/systemd/system"
fi
VENV_DIR="${PREFIX}/.venv"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
UI_STEP_TOTAL=10                                    # +1 for "Lemonade daemon" (PR-5)

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
        "Lemonade daemon")
            warn "Recovery: check /opt/lemonade/ ownership + free space under /opt"
            warn "         (embeddable tarball is ~200 MB extracted)."
            warn "         Set HAL0_SKIP_LEMONADE_SHA=1 if the placeholder SHA-256"
            warn "         is blocking on a fresh upstream tarball." ;;
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
        die "python interpreter '${PY}' not found — install with 'apt install python3 python3-venv'"
    fi
    # Version warning already printed; keep going.
fi

# preflight_docker is soft (always returns 0 with a warning when
# Docker is absent), so we don't need to guard the call.
preflight_docker

# Disk + port-collision checks only matter for the live install — dev
# mode lays files under $PWD/.hal0ai and never binds 8080/3001. We
# aggregate both check results (so the operator sees *both* failures
# in one run instead of fixing disk → rerun → discover port) and then
# trip a bare `false` so the ERR trap fires with the contextual
# "Pre-flight checks" recovery hint above.
if [[ "${DEV_MODE}" -eq 0 ]]; then
    pf_rc=0
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

# Production install ships the source tree into ${PREFIX} so `pip install
# --editable` ends up pointing at a persistent location, not the temp dir
# the bootstrap unpacks into (which gets cleaned on exit — and on a
# tmpfs /tmp, doesn't survive a reboot). Dev installs skip this: their
# REPO_ROOT is the operator's git checkout and we want pip's editable
# link aimed there so source edits flow without a reinstall.
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

# Refresh pip + install hal0 in editable mode pointing at this checkout.
# ui_spinner_run drops the >/dev/null — the spinner shows the live tail
# of pip's output, and on failure replays the last 50 lines on stderr.
ui_spinner_run "Upgrading pip / setuptools / wheel" \
    "${PIP}" install --upgrade pip setuptools wheel
ui_spinner_run "Installing hal0 from ${REPO_ROOT}" \
    "${PIP}" install -e "${REPO_ROOT}"

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

API_ENV="${ETC_DIR}/api.env"
if [[ ! -f "${API_ENV}" ]]; then
    cat > "${API_ENV}" <<EOF
HAL0_PORT=${HAL0_PORT}
HAL0_LOG_LEVEL=info
# Uncomment to pin specific toolbox images:
# HAL0_TOOLBOX_IMAGE_VULKAN=ghcr.io/hal0ai/hal0-toolbox-vulkan:v1
# HAL0_TOOLBOX_IMAGE_ROCM=ghcr.io/hal0ai/hal0-toolbox-rocm:v1
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
WorkingDirectory=${PREFIX}
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

# Note: hal0-slot@.service template removed in PR-9 (v0.2 retires per-modality
# toolbox containers — see docs/internal/adr/0008-lemonade-adoption.md §2 and
# docs/internal/lemonade-adoption-plan-2026-05-22.md §10.1). Lemonade owns the
# process lifecycle in v0.2; the SlotManager dispatch rewrite lands in PR-10.

OPENWEBUI_UNIT_SRC="${REPO_ROOT}/packaging/systemd/hal0-openwebui.service"
OPENWEBUI_UNIT_DST="${UNIT_DIR}/hal0-openwebui.service"
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
# The unit also has ExecStartPre=docker pull (idempotent), so a missed
# background pull never breaks correctness — only first-boot latency.
if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 ]] && command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    # Background the actual pull, but spin briefly so the user sees we
    # kicked it off. The hal0-openwebui unit also has ExecStartPre=docker
    # pull (idempotent), so missing this background pull only costs first
    # -boot latency, not correctness.
    (docker pull ghcr.io/open-webui/open-webui:main >/dev/null 2>&1 || true) &
    disown
    ui_spinner_run "Pulling ghcr.io/open-webui/open-webui:main in background" sleep 3
fi

ui_step "Hardware probe"

if [[ -z "${HAL0_NO_PROBE:-}" ]]; then
    HAL0_HOME_FOR_PROBE=""
    if [[ "${DEV_MODE}" -eq 1 ]]; then
        HAL0_HOME_FOR_PROBE="${PREFIX}"
    fi
    # Inline Python: probe → write hardware.json → emit 4 hardware cards
    # → (if no slots/primary.toml exists yet) render one from
    # recommend_primary_slot() so the operator has a sensible default
    # waiting after `hal0 model pull <id>`. PRIMARY_TOML is exported so
    # the heredoc doesn't need to know the dev-mode prefix.
    PRIMARY_TOML="${ETC_DIR}/slots/primary.toml" \
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

# Pre-populate slots/primary.toml if absent. Idempotent: never overwrite
# an operator-edited file. Disabled by default — they pull a model and
# flip enabled = true when ready.
target = Path(os.environ["PRIMARY_TOML"])
if target.exists():
    print(f"  {target} exists — left alone")
else:
    rec = recommend_primary_slot(info)
    meta = rec.pop("_meta", {})
    import tomli_w  # hal0 install dep, always available here
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# hal0 primary slot — recommended for this hardware.\n"
        "# Created by install.sh on first install. Edit freely; the\n"
        "# installer will not overwrite this file on subsequent runs.\n"
        "#\n"
        f"# Backend rationale: {meta.get('rationale_backend', '')}\n"
        f"# Model rationale:   {meta.get('rationale_model', '')}\n"
        f"# Memory budget:     ~{meta.get('vram_budget_gb', '?')} GB\n"
        "#\n"
        "# Next: `hal0 model pull " + rec['model']['default'] + "`\n"
        "#       then flip enabled = true and `systemctl start hal0-slot@primary`\n"
        "\n"
    )
    target.write_text(header + tomli_w.dumps(rec))
    print(f"  wrote {target}  (backend={rec['backend']} model={rec['model']['default']})")
PY
else
    warn "skipping probe (HAL0_NO_PROBE=1)"
fi

# ── Lemonade system prerequisites (PR-4) ──────────────────────────────────
# Install the system-level packages Lemonade + FLM need BEFORE PR-5 drops
# the lemond binary, config.json, and systemd unit. Three pieces:
#
#   1. Lemonade PPA (ppa:lemonade-team/stable) — provides libxrt-npu2,
#      the AMDXDNA NPU runtime FLM dlopen()s at start. Without it,
#      `flm serve` fails with "cannot open shared object libxrt_coreutil.so.2"
#      and the NPU stays unreachable. PPA add is an apt key + sources.list
#      drop; idempotent via add-apt-repository's built-in dedup.
#
#   2. FLM transitive runtime libs (apt) —
#        * libxrt-npu2                   NPU runtime (from PPA)
#        * libavformat60/libavcodec60/   ffmpeg6 — FLM's audio transcribe
#          libavutil58/libswscale7/        path (Whisper-V3-Turbo on NPU)
#          libswresample4
#        * libboost-program-options1.83.0  FLM CLI flag parsing
#        * libfftw3-single3              FLM signal processing
#
#   3. FastFlowLM .deb v0.9.42 — pinned URL + SHA-256, fetched from upstream
#      releases. Verified BEFORE dpkg install; fail-soft if unreachable
#      (NPU-less hal0 still ships — FLM trio gates on `flm validate`).
#
# Refs: ADR-0008 (Lemonade adoption), ADR-0009 (FLM trio NPU packing),
#       lemonade-adoption-plan §3 (service topology) + §11 (PR-4 scope),
#       memory `hal0_lemonade_flm_npu_install` (the manual install recipe
#       this section automates).
ui_step "Lemonade system prerequisites"

# Pinned FLM .deb — bump in lockstep with ADR-0009 / lemonade-adoption-plan
# §5. v0.9.43 revalidated 2026-06-03 (LXC 105, Strix Halo NPU passthrough):
# flm validate ok (NPU FW 1.1.2.65), embed-gemma-300m-FLM → 768-dim,
# gemma3-1b-FLM chat ok. NOTE: 0.9.43 tightened CLI arg parsing — it now
# rejects a flag passed twice. lemond auto-injects the requested model's
# mode flag (e.g. --embed 1 for an embedding model), so flm.args must NOT
# repeat it. Hence flm.args = "--asr 1" below (was "--asr 1 --embed 1",
# which produced a duplicate --embed and crashed flm-server on 0.9.43).
FLM_DEB_VERSION="0.9.43"
FLM_DEB_URL="https://github.com/FastFlowLM/FastFlowLM/releases/download/v${FLM_DEB_VERSION}/fastflowlm_${FLM_DEB_VERSION}_ubuntu24.04_amd64.deb"
# SHA-256 of the upstream ubuntu24.04 artefact at v0.9.43 (verified on
# download 2026-06-03). If upstream rebuilds under the same tag this will
# drift; bump in lockstep with FLM_DEB_VERSION.
FLM_DEB_SHA256="4173fa82f0043a4ff14cf7b84c7d24188fac4ac64346942601b7d2b915308479"

if [[ "${DEV_MODE}" -eq 1 ]]; then
    # Dev installs don't touch the host's apt or third-party package
    # sources — devs install once manually (see installer/README.md).
    # We still log what *would* have happened so the dev knows the gap
    # exists for production installs.
    info "dev mode — skipping Lemonade prereqs (apt PPA + libxrt-npu2 + ffmpeg6 + boost1.83 + fftw3 + FLM .deb v${FLM_DEB_VERSION})"
    info "          install manually if exercising NPU paths: see installer/README.md"
elif ! command -v apt-get >/dev/null 2>&1; then
    # Non-Debian host (e.g., the maintainer's CachyOS dev box). FLM
    # upstream only ships .deb + Windows .msi as of v0.9.42; we cannot
    # auto-install on pacman/dnf/zypper. Surface the gap, keep going —
    # GPU paths still work without FLM.
    warn "apt-get not found — skipping Lemonade NPU prereqs (FLM .deb is Ubuntu-only upstream)"
    warn "  GPU paths (Vulkan/ROCm) still work; NPU paths will be unavailable until FLM is installed manually"
else
    # 1. PPA. `add-apt-repository -y` is idempotent — re-adding an
    #    existing PPA is a no-op + warning, not a failure. We DO surface
    #    what's about to happen so the operator isn't surprised by a
    #    third-party apt source landing on their host.
    info "adding ppa:lemonade-team/stable (provides libxrt-npu2 — NPU runtime)"
    if ! command -v add-apt-repository >/dev/null 2>&1; then
        ui_spinner_run "Installing software-properties-common (for add-apt-repository)" \
            apt-get install -y software-properties-common
    fi
    # `add-apt-repository -y` prints to stderr on re-add; wrap so a
    # double-run looks clean. Failure here IS fatal — without the PPA
    # libxrt-npu2 is unavailable and the NPU surface won't work.
    ui_spinner_run "Adding ppa:lemonade-team/stable" \
        add-apt-repository -y ppa:lemonade-team/stable
    ui_spinner_run "apt-get update (refresh PPA index)" \
        apt-get update -qq

    # 2. Runtime libs. apt is naturally idempotent — already-installed
    #    packages are a no-op. Listed explicitly (not via a metapackage)
    #    so a future libavformat ABI bump is a visible single-line edit
    #    rather than a silent metapackage drift.
    ui_spinner_run "Installing FLM runtime libs (libxrt-npu2 + ffmpeg6 + boost1.83 + fftw3)" \
        apt-get install -y \
            libxrt-npu2 \
            libavformat60 libavcodec60 libavutil58 libswscale7 libswresample4 \
            libboost-program-options1.83.0 \
            libfftw3-single3

    # 3. FLM .deb. Fail-soft: if upstream is unreachable or the SHA-256
    #    doesn't match, warn + skip. NPU paths gate on `flm validate`
    #    succeeding later — GPU-only hal0 still ships fine.
    FLM_DEB_TMP="/tmp/fastflowlm_${FLM_DEB_VERSION}.deb"
    NEED_FLM_INSTALL=1
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
            # SHA-256 verify BEFORE dpkg installs it. A real pin is
            # required here in a follow-up — for the POC the placeholder
            # is all-zeroes and `--lemonade-skip-flm-sha` (env var
            # HAL0_SKIP_FLM_SHA=1) bypasses the check so CI can land
            # the section without blocking on the lookup. Operators
            # who set the env explicitly accept the trust trade.
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

# ── Lemonade daemon bootstrap (PR-5) ──────────────────────────────────────
# Drops the lemond binary, baseline config.json with the mandatory
# `--threads N` guard, and the hal0-lemonade.service systemd unit. After
# PR-4 (system prereqs) and before PR-6 (server_models.json), so the
# resources/ directory the next step writes into exists. Three pieces:
#
#   1. Embeddable tarball (lemond + lemonade CLI + resources/) extracted
#      to /opt/lemonade/. AMD ships this from
#      github.com/lemonade-sdk/lemonade/releases — version-pinned, sha256
#      verified, --strip-components=1 so /opt/lemonade/{lemond,lemonade,
#      resources}/ land flat. Idempotent via a marker file
#      (/opt/lemonade/.installed-version) — re-running with the same
#      LEMONADE_VERSION skips download + extract.
#
#   2. /var/lib/hal0/lemonade/config.json — written atomically every run
#      (tempfile + mv), so a config bump propagates without a manual
#      delete. Locked baseline from lemonade-adoption-plan-2026-05-22 §3:
#      config_version=1, port 13305 loopback, max_loaded_models=4,
#      rocm_channel=stable, llamacpp.args="--parallel 1 --threads N",
#      flm.args="--asr 1 --embed 1", kokoro.cpu_bin=builtin. The
#      --threads N value is the load-bearing guard against the multi-
#      model CPU-oversubscription deadlock from spike #2 (memory
#      `hal0_lemonade_threads_deadlock`): with 2+ concurrent child
#      llama-servers each spawning all cores, Vulkan dispatch starves
#      and inference hangs at 30 s timeouts / load avg 25. Formula:
#      N = max(2, (nproc - 2) / 4). The "-2" leaves headroom for
#      hal0-api + system; "/4" splits across the typical four-process
#      capability rollup (primary + embed + rerank + voice).
#
#   3. /etc/systemd/system/hal0-lemonade.service — Type=simple, runs as
#      a dedicated `hal0` system user (per ADR-0008 §1: loopback-only
#      internal runtime). LimitMEMLOCK=infinity for FLM/NPU; CPUQuota=80%
#      leaves 20% for hal0-api + system. Enabled here; the Service start
#      block at the end of install.sh starts it alongside hal0-api.
#
# Refs: ADR-0008 §1 (single lemond, loopback), §3 (per-type LRU), §4
#       (mandatory --threads N); lemonade-adoption-plan §3 (service
#       topology + verbatim config.json + unit baseline), §11 PR-5,
#       §12.2 (port 13305), §12.3 (version pinning); memory
#       `hal0_lemonade_threads_deadlock` (non-negotiable operational
#       constraint).
ui_step "Lemonade daemon"

# Pinned Lemonade embeddable tarball — bump in lockstep with hal0
# releases. v10.6.0 is the build the v0.2 spike #2 validated against
# on 2026-05-22.
LEMONADE_VERSION="v10.6.0"
LEMONADE_VERSION_BARE="${LEMONADE_VERSION#v}"
LEMONADE_TARBALL="lemonade-embeddable-${LEMONADE_VERSION_BARE}-ubuntu-x64.tar.gz"
LEMONADE_URL="https://github.com/lemonade-sdk/lemonade/releases/download/${LEMONADE_VERSION}/${LEMONADE_TARBALL}"
# SHA-256 of the upstream artefact at v10.6.0. Placeholder pre-pin; gate
# release on populating with the real checksum. HAL0_SKIP_LEMONADE_SHA=1
# lets CI / dev installs proceed against the placeholder explicitly.
LEMONADE_SHA256="0000000000000000000000000000000000000000000000000000000000000000"

LEMONADE_PREFIX="/opt/lemonade"
LEMONADE_CACHE_DIR="${VAR_DIR}/lemonade"
LEMONADE_CONFIG_JSON="${LEMONADE_CACHE_DIR}/config.json"
LEMONADE_UNIT="${UNIT_DIR}/hal0-lemonade.service"
LEMONADE_MARKER="${LEMONADE_PREFIX}/.installed-version"

# Compute --threads N at install time. See memory
# `hal0_lemonade_threads_deadlock` + ADR-0008 §4: this flag is the
# difference between the Vulkan deadlock spike and a happy 4-concurrent
# Phase B.3 run. NEVER skip it.
#
# Formula: N = max(2, (nproc - 2) / 4). The "/4" assumes the typical
# v0.2 capability rollup (primary + embed + rerank + voice = 4
# concurrent child llama-servers). The "-2" leaves headroom for the
# hal0-api process + system. The min-of-2 is a hard floor for hosts
# small enough that the formula would otherwise underflow (a 4-core box
# computes (4 - 2) / 4 = 0; we bump to 2 so llama-server has at least a
# producer/consumer pair).
#
# If nproc is unavailable or returns a non-positive integer (broken
# containers, exotic minimal hosts), we default to 2 + warn rather than
# omit the flag entirely. Per the deadlock memory, an omitted --threads
# is the failure mode we are guarding against.
if command -v nproc >/dev/null 2>&1; then
    LEMONADE_CORES="$(nproc 2>/dev/null || echo 0)"
else
    LEMONADE_CORES=0
fi
if ! [[ "${LEMONADE_CORES}" =~ ^[0-9]+$ ]] || (( LEMONADE_CORES < 1 )); then
    warn "nproc unavailable or returned '${LEMONADE_CORES}' — defaulting --threads 2"
    LEMONADE_THREADS=2
else
    LEMONADE_THREADS=$(( (LEMONADE_CORES - 2) / 4 ))
    if (( LEMONADE_THREADS < 2 )); then
        LEMONADE_THREADS=2
    fi
fi
info "Lemonade --threads ${LEMONADE_THREADS} (nproc=${LEMONADE_CORES}, formula=max(2,(n-2)/4))"

if [[ "${DEV_MODE}" -eq 1 ]]; then
    # Dev installs don't touch /opt/lemonade, /var/lib/hal0/lemonade, or
    # systemd. Surface what the production install would do so the dev
    # knows the gap exists; the rest of v0.2 wiring (PR-6 server_models,
    # capability dispatch) still exercises in dev mode against a manually
    # started lemond.
    info "dev mode — skipping Lemonade daemon bootstrap"
    info "          would install: tarball ${LEMONADE_TARBALL} → ${LEMONADE_PREFIX}"
    info "          would write:   ${LEMONADE_CONFIG_JSON} (threads ${LEMONADE_THREADS})"
    info "          would enable:  ${LEMONADE_UNIT}"
else
    # 1. hal0 system user/group. The unit runs lemond as `hal0` (per
    #    plan §3 + ADR-0008 §1 "internal runtime, never exposed
    #    off-box"). The user owns /opt/lemonade and /var/lib/hal0/lemonade
    #    so lemond can write runtime state (user_models.json, logs).
    #    System user (UID < 1000), no login shell, home at ${VAR_DIR}
    #    so any stray `~`-relative lemond writes land somewhere sane.
    #    Idempotent via `getent passwd`.
    if ! getent group hal0 >/dev/null 2>&1; then
        groupadd --system hal0
        info "created group hal0"
    fi
    if ! getent passwd hal0 >/dev/null 2>&1; then
        useradd --system --gid hal0 --home-dir "${VAR_DIR}" \
            --shell /usr/sbin/nologin \
            --comment "hal0 Lemonade daemon" \
            hal0
        info "created user hal0 (system, no login)"
    fi

    # GPU device access (issue #420). lemond's ROCm/Vulkan backends need
    # the `hal0` user in `render` (for /dev/kfd + /dev/dri/renderD*) and
    # `video` (for /dev/dri/card*/amdgpu). Without it ROCm reports "no
    # ROCm-capable device is detected" and silently falls back to CPU.
    # Idempotent; only adds groups that actually exist on the host (a
    # non-GPU box / CI runner simply has neither).
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

    # 2. Embeddable tarball. Idempotent: a marker file pinned to
    #    LEMONADE_VERSION lets re-runs skip the multi-hundred-MB
    #    download when the binary on disk already matches. Operators
    #    who want to force a re-extract can `rm /opt/lemonade/.installed-version`.
    NEED_LEMONADE_EXTRACT=1
    if [[ -x "${LEMONADE_PREFIX}/lemond" && -f "${LEMONADE_MARKER}" ]]; then
        INSTALLED_VERSION="$(cat "${LEMONADE_MARKER}" 2>/dev/null || true)"
        if [[ "${INSTALLED_VERSION}" == "${LEMONADE_VERSION}" ]]; then
            info "Lemonade ${LEMONADE_VERSION} already extracted at ${LEMONADE_PREFIX} — skipping download"
            NEED_LEMONADE_EXTRACT=0
        else
            info "Lemonade marker reports ${INSTALLED_VERSION:-unknown}, target ${LEMONADE_VERSION} — re-extracting"
        fi
    fi

    if [[ "${NEED_LEMONADE_EXTRACT}" -eq 1 ]]; then
        LEMONADE_TARBALL_TMP="/tmp/${LEMONADE_TARBALL}"
        # `curl -fsSL` — fail on HTTP error, silent, follow redirects.
        # Tarball lands in /tmp so a re-run doesn't keep a stale copy in
        # the install tree.
        if ! curl -fsSL -o "${LEMONADE_TARBALL_TMP}" "${LEMONADE_URL}"; then
            warn "Lemonade tarball download failed (${LEMONADE_URL})"
            warn "  Lemonade-backed slots will be unavailable until ${LEMONADE_PREFIX}/lemond exists"
            NEED_LEMONADE_EXTRACT=0
        fi
    fi

    if [[ "${NEED_LEMONADE_EXTRACT}" -eq 1 ]]; then
        # SHA-256 verify BEFORE extracting. Same shape as PR-4's FLM .deb
        # check: placeholder all-zeroes pin is non-fatal when
        # HAL0_SKIP_LEMONADE_SHA=1, otherwise refuse + skip. Real
        # checksum mismatch is always fatal (refuse + skip — never
        # extract a tarball we don't trust).
        ACTUAL_SHA="$(sha256sum "${LEMONADE_TARBALL_TMP}" | awk '{print $1}')"
        if [[ "${LEMONADE_SHA256}" == "0000000000000000000000000000000000000000000000000000000000000000" ]]; then
            warn "LEMONADE_SHA256 is the placeholder — pin the real checksum in install.sh before v0.2 ships"
            warn "  observed: ${ACTUAL_SHA}"
            if [[ "${HAL0_SKIP_LEMONADE_SHA:-0}" != "1" ]]; then
                warn "  skipping Lemonade install (set HAL0_SKIP_LEMONADE_SHA=1 to accept the placeholder)"
                rm -f "${LEMONADE_TARBALL_TMP}"
                NEED_LEMONADE_EXTRACT=0
            fi
        elif [[ "${ACTUAL_SHA}" != "${LEMONADE_SHA256}" ]]; then
            warn "Lemonade tarball SHA-256 mismatch — refusing to extract"
            warn "  expected: ${LEMONADE_SHA256}"
            warn "  observed: ${ACTUAL_SHA}"
            rm -f "${LEMONADE_TARBALL_TMP}"
            NEED_LEMONADE_EXTRACT=0
        fi
    fi

    if [[ "${NEED_LEMONADE_EXTRACT}" -eq 1 ]]; then
        # The upstream tarball has a single top-level
        # `lemonade-embeddable-<VER>/` directory; --strip-components=1
        # lands {lemond, lemonade, LICENSE, resources}/ flat in
        # /opt/lemonade/.
        mkdir -p "${LEMONADE_PREFIX}"
        if ui_spinner_run "Extracting ${LEMONADE_TARBALL} → ${LEMONADE_PREFIX}" \
            tar -xzf "${LEMONADE_TARBALL_TMP}" -C "${LEMONADE_PREFIX}" --strip-components=1; then
            rm -f "${LEMONADE_TARBALL_TMP}"
            printf '%s\n' "${LEMONADE_VERSION}" > "${LEMONADE_MARKER}"
            info "extracted Lemonade ${LEMONADE_VERSION} → ${LEMONADE_PREFIX}"
        else
            warn "tar extract failed — Lemonade-backed slots will be unavailable"
            rm -f "${LEMONADE_TARBALL_TMP}"
        fi
    fi

    # Ownership: lemond reads from /opt/lemonade/{resources,bin} and may
    # update entries under resources/server_models.json on backend
    # install. Owned by hal0:hal0 so the daemon can write its own state
    # without escalating. Always runs (idempotent + cheap), so a
    # download-skipped re-install still corrects perms after, e.g., an
    # operator manually extracted as root.
    if [[ -d "${LEMONADE_PREFIX}" ]]; then
        chown -R hal0:hal0 "${LEMONADE_PREFIX}"
    fi

    # 2b. Pin llama.cpp backend builds (ADR-0023, issue #438). Lemonade
    #     bundles a *frozen* default in resources/backend_versions.json
    #     (10.6.0 ships vulkan=b9253 / rocm-stable=b9247). b9253 predates
    #     the qwen3next Vulkan kernels → our primary coding model runs an
    #     unoptimised fallback (~4× slower gen). lemond restores whatever
    #     matches this pin on every model load, so a hand-swapped binary
    #     does NOT stick — the pin is the only durable lever. We pin BOTH
    #     backends to the same official ggml-org build (b9496 ships both
    #     ubuntu-vulkan-x64 and ubuntu-rocm-7.2-x64 assets) so they share
    #     a build era and stay comparable. lemond downloads the matching
    #     binary lazily on first load of each backend. rocm-nightly is
    #     left alone (needs kernel ≥6.18.4; 64 GB alloc cap — TheRock
    #     #4645). Bump LLAMACPP_PIN as ggml-org advances + re-run install.
    LLAMACPP_PIN="b9496"
    LEMONADE_BACKEND_VERSIONS="${LEMONADE_PREFIX}/resources/backend_versions.json"
    if [[ -f "${LEMONADE_BACKEND_VERSIONS}" ]]; then
        if "${PY}" - "${LEMONADE_BACKEND_VERSIONS}" "${LLAMACPP_PIN}" <<'PYPIN'
import json, sys
path, pin = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
lc = cfg.setdefault("llamacpp", {})
changed = False
for key in ("vulkan", "rocm-stable"):
    if lc.get(key) != pin:
        lc[key] = pin
        changed = True
if changed:
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
print("pinned" if changed else "already-pinned",
      "vulkan/rocm-stable =", pin)
PYPIN
        then
            chown hal0:hal0 "${LEMONADE_BACKEND_VERSIONS}"
            info "pinned llamacpp vulkan + rocm-stable → ${LLAMACPP_PIN} (${LEMONADE_BACKEND_VERSIONS})"
        else
            warn "failed to pin backend_versions.json — Lemonade will use its bundled (slower) default builds"
        fi
    else
        warn "${LEMONADE_BACKEND_VERSIONS} not present — skipping backend pin (Lemonade extract may have failed)"
    fi

    # HuggingFace hub cache for /v1/pull downloads (#275 bug 4). The hal0
    # user's HOME is ${VAR_DIR} (per useradd above), so HF's default
    # cache lands at ${VAR_DIR}/.cache/huggingface/hub. Without this,
    # the first POST /v1/pull fails with "Permission denied" because
    # ${VAR_DIR} is created by this script as root and never reassigned
    # to hal0. Pre-create the leaf dir + give hal0 ownership of the
    # cache tree (NOT the whole VAR_DIR — slot state.json + registry
    # are written by hal0-api which runs as ${HAL0_USER}, default root).
    mkdir -p "${VAR_DIR}/.cache/huggingface/hub"
    chown -R hal0:hal0 "${VAR_DIR}/.cache"

    # 3. Cache dir + config.json. Atomic write (tempfile in the same
    #    directory + mv) so a crash mid-write doesn't leave lemond
    #    parsing a half-written JSON file on next start. Overwrite on
    #    every install run — a baseline bump (port move, new key, etc.)
    #    should propagate without the operator having to delete the file.
    #
    #    llamacpp.args carries --no-mmap deliberately. Models live on a
    #    ZFS dataset and the iGPU is UMA, so mmap loading is doubly bad:
    #    (1) ZFS + mmap page-faults bypass ARC prefetch and double-buffer
    #        → cold loads crawl (~200 MB/s vs ~1.6 GB/s with read()), and
    #    (2) on a UMA iGPU the mmap'd file sits in the page cache AND the
    #        GTT GPU buffer simultaneously → a 47 GB model needs ~94 GB
    #        RAM and OOM-crashes the host. --no-mmap reads into a transient
    #        buffer, uploads to GTT, frees it → ~1× RAM + fast sequential.
    mkdir -p "${LEMONADE_CACHE_DIR}"
    chown hal0:hal0 "${LEMONADE_CACHE_DIR}"
    LEMONADE_CONFIG_TMP="$(mktemp "${LEMONADE_CONFIG_JSON}.XXXXXX")"
    cat > "${LEMONADE_CONFIG_TMP}" <<JSON
{
  "config_version": 1,
  "host": "127.0.0.1",
  "port": 13305,
  "ctx_size": 4096,
  "max_loaded_models": 8,
  "extra_models_dir": "/var/lib/hal0/models",
  "global_timeout": 900,
  "no_broadcast": true,
  "log_level": "info",
  "rocm_channel": "stable",
  "llamacpp": {
    "args": "--parallel 1 -fa on --threads ${LEMONADE_THREADS} --no-mmap",
    "backend": "vulkan",
    "prefer_system": false
  },
  "flm":        { "args": "--asr 1" },
  "kokoro":     { "cpu_bin": "builtin" },
  "whispercpp": { "backend": "vulkan" },
  "sdcpp":      { "backend": "rocm", "steps": 20, "cfg_scale": 7.0, "width": 512, "height": 512 }
}
JSON
    chown hal0:hal0 "${LEMONADE_CONFIG_TMP}"
    chmod 0644 "${LEMONADE_CONFIG_TMP}"
    mv "${LEMONADE_CONFIG_TMP}" "${LEMONADE_CONFIG_JSON}"
    info "wrote ${LEMONADE_CONFIG_JSON} (threads=${LEMONADE_THREADS})"

    # 4. Systemd unit — verbatim from lemonade-adoption-plan §3. Always
    #    rewritten so a unit bump propagates without manual `rm`. The
    #    ExecStop curl lets lemond drain in-flight requests cleanly;
    #    on hosts where /usr/bin/curl doesn't exist systemd will log
    #    the failure but `Restart=on-failure` still kicks in correctly.
    cat > "${LEMONADE_UNIT}" <<UNIT
[Unit]
Description=hal0 Lemonade backend (lemond)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${LEMONADE_PREFIX}/lemond ${LEMONADE_CACHE_DIR}
ExecStop=/usr/bin/curl -s -X POST http://127.0.0.1:13305/internal/shutdown
Restart=on-failure
RestartSec=5s
User=hal0
Group=hal0
LimitMEMLOCK=infinity
CPUQuota=80%

[Install]
WantedBy=multi-user.target
UNIT
    info "wrote ${LEMONADE_UNIT}"

    # 4b. /dev/kfd group-access drop-in (issue #420). The GPU compute
    #     node resets to root:root 0660 on every boot — no udev rule
    #     fires for the host-passed node inside the LXC — so the `hal0`
    #     user loses access after a reboot even though it's in `render`.
    #     ROCm then can't see the iGPU and lemond silently runs on CPU
    #     (a 40B load blows past the hal0-api proxy 120s read timeout →
    #     chat 502s). This ExecStartPre re-chgrp's the node to `render`
    #     before lemond starts, every boot. The leading `+` runs it as
    #     root despite User=hal0; the `-` makes it non-fatal so non-GPU
    #     hosts (no /dev/kfd) still start lemond. Mirrors the
    #     whisper-patchelf drop-in. Always rewritten so a bump propagates.
    LEMONADE_DROPIN_DIR="${UNIT_DIR}/hal0-lemonade.service.d"
    mkdir -p "${LEMONADE_DROPIN_DIR}"
    cat > "${LEMONADE_DROPIN_DIR}/kfd-perms.conf" <<'DROPIN'
[Service]
ExecStartPre=+-/usr/bin/chgrp render /dev/kfd
ExecStartPre=+-/usr/bin/chmod 0660 /dev/kfd
DROPIN
    info "wrote ${LEMONADE_DROPIN_DIR}/kfd-perms.conf"

    # 4c. Vulkan ICD pin (ADR-0023). gfx1151 (Strix Halo) runs the open
    #     Mesa RADV driver markedly faster than AMD's closed AMDVLK for
    #     llama.cpp Vulkan. Both ICDs are typically installed; without
    #     this, loader order picks AMDVLK on some images. Pinning RADV is
    #     the load-bearing half of the qwen3next Vulkan perf path (the
    #     other half is the b9496 backend pin set earlier, step 2b).
    #     Harmless on ROCm-only loads. Always rewritten so a bump propagates.
    cat > "${LEMONADE_DROPIN_DIR}/20-vulkan-radv.conf" <<'DROPIN'
[Service]
Environment=AMD_VULKAN_ICD=RADV
DROPIN
    info "wrote ${LEMONADE_DROPIN_DIR}/20-vulkan-radv.conf"

    # daemon-reload is idempotent — always safe to call. enable (no
    # --now) so the unit auto-starts on boot; the Service start block at
    # the end of install.sh handles the first start in lockstep with
    # hal0-api / hal0-openwebui.
    systemctl daemon-reload
    if [[ -x "${LEMONADE_PREFIX}/lemond" ]]; then
        systemctl enable hal0-lemonade.service >/dev/null 2>&1 || \
            warn "systemctl enable hal0-lemonade failed — check 'systemctl status hal0-lemonade'"
    else
        warn "${LEMONADE_PREFIX}/lemond missing — leaving hal0-lemonade.service disabled"
    fi
fi

# ── Lemonade server_models.json generation (issue #141) ───────────────────
# Convert hal0's registry.toml into the curated catalog Lemonade Server
# loads from ``resources/server_models.json``. Must run BEFORE
# ``systemctl enable --now lemond`` so the daemon picks up our entries on
# first start (Lemonade re-reads the file on probe, so a later sync
# does not strictly require a restart).
#
# Guarded by the presence of the Lemonade resources directory: installs
# that have not yet bundled Lemonade (issue #140 lands the tarball) are
# skipped silently. Once #140 lands, this block becomes the canonical
# install-time wiring without further changes here.
#
# Idempotent: re-running install.sh overwrites server_models.json via
# atomic tempfile + rename. See ADR-0006 §4 + ``src/hal0/lemonade/server_models_gen.py``.
ui_step "Lemonade server_models.json"

LEMONADE_RESOURCES="/opt/lemonade/resources"
LEMONADE_SERVER_MODELS="${LEMONADE_RESOURCES}/server_models.json"
REGISTRY_TOML="${VAR_DIR}/registry/registry.toml"

if [[ "${DEV_MODE}" -eq 0 && -d "${LEMONADE_RESOURCES}" ]]; then
    if [[ ! -f "${REGISTRY_TOML}" ]]; then
        warn "registry.toml not found at ${REGISTRY_TOML}; writing empty server_models.json"
    fi
    if "${VENV_DIR}/bin/python" -m hal0.lemonade.server_models_gen \
        --registry "${REGISTRY_TOML}" \
        --output "${LEMONADE_SERVER_MODELS}"; then
        info "wrote ${LEMONADE_SERVER_MODELS}"
    else
        warn "server_models_gen failed; Lemonade will fall back to its bundled catalog"
    fi
else
    info "skipping (Lemonade resources dir ${LEMONADE_RESOURCES} not present yet)"
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

ui_step "Service start"

if [[ "${DEV_MODE}" -eq 1 || "${NO_START}" -eq 1 ]]; then
    warn "not starting services automatically (dev / --no-start)."
    warn "  start manually: ${HAL0_BIN} serve --host ${API_BIND_HOST} --port ${HAL0_PORT}"
else
    # Lemonade FIRST — hal0-api may resolve capability state from
    # /v1/health on its own boot, and the lemond start is the slowest
    # of the three (ROCm/Vulkan backend init). Soft on failure: hal0-api
    # still serves the dashboard and surfaces the dead-lemond banner
    # rather than the installer aborting.
    if [[ -f "${UNIT_DIR}/hal0-lemonade.service" ]]; then
        systemctl enable --now hal0-lemonade
        if wait_active hal0-lemonade 30; then
            info "hal0-lemonade is running (loopback :13305)"

            # Pre-warm the default (Vulkan) backend binary. lemond pulls
            # the build matching the b9496 pin lazily on first model load
            # — without this, the user's first chat after install blocks
            # on a ~33 MB download + extract. Eagerly fetching it here
            # (only ~33 MB; ROCm + its 4.9 GB therock libs stay lazy,
            # pulled only if a ROCm slot is selected) makes the first
            # load instant. Fail-soft: a download miss just defers the
            # pull to first load, exactly as before. systemd-active does
            # not mean the REST API is listening yet, so poll health
            # first. Skipped if curl is unavailable.
            if command -v curl >/dev/null 2>&1; then
                LEMONADE_READY=0
                for _ in $(seq 1 20); do
                    if curl -fsS -m 2 http://127.0.0.1:13305/api/v1/health >/dev/null 2>&1; then
                        LEMONADE_READY=1
                        break
                    fi
                    sleep 1
                done
                if [[ "${LEMONADE_READY}" -eq 1 ]]; then
                    if ui_spinner_run "Pre-warming Vulkan backend (${LLAMACPP_PIN:-pinned} build)" \
                        curl -fsS -m 300 -X POST http://127.0.0.1:13305/api/v1/install \
                            -H 'Content-Type: application/json' \
                            -d '{"recipe":"llamacpp","backend":"vulkan"}'; then
                        info "Vulkan backend ready (first model load won't wait on a download)"
                    else
                        warn "Vulkan pre-warm failed — lemond will pull the binary on first load instead"
                    fi
                else
                    warn "lemond REST not ready in time — skipping pre-warm (binary pulls on first load)"
                fi
            fi
        else
            warn "hal0-lemonade not yet active; check 'journalctl -u hal0-lemonade -n 60'"
        fi
    fi

    systemctl enable --now hal0-api
    if wait_active hal0-api 15; then
        info "hal0-api is running"
    else
        warn "hal0-api failed to start; check 'journalctl -u hal0-api -n 40'"
    fi

    if [[ -f "${OPENWEBUI_UNIT_DST}" ]]; then
        systemctl enable --now hal0-openwebui
        # OpenWebUI can take a moment to come up while it pulls the
        # image / initialises its sqlite db. Don't fail the installer
        # on a slow first boot; just surface the status.
        if wait_active hal0-openwebui 30; then
            info "hal0-openwebui is running (chat at :3001)"
        else
            warn "hal0-openwebui not yet active; check 'journalctl -u hal0-openwebui -n 40'"
        fi
    fi

    # hal0-agent@hermes — gated on the hermes venv existing. PR-3 owns the
    # actual `hal0 agent bootstrap hermes` invocation; until that lands,
    # this branch is a no-op on fresh installs (correct — there's no
    # agent to run yet). On upgrade installs where the venv is already
    # present from a previous bootstrap, this enables the unit so the
    # agent comes back up after the upgrade.
    if [[ -f "${AGENT_UNIT_DST}" && -x "/var/lib/hal0/venvs/hermes/bin/hermes" ]]; then
        systemctl enable --now hal0-agent@hermes.service
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
        env -u HERMES_HOME /var/lib/hal0/venvs/hermes/bin/hermes gateway install --system --run-as-user hal0
        systemctl daemon-reload
        systemctl enable --now hermes-gateway.service
        if wait_active hermes-gateway.service 20; then
            info "hermes-gateway is running (Telegram/Discord)"
        else
            warn "hermes-gateway not yet active; check 'journalctl -u hermes-gateway -n 40'"
        fi
    elif [[ -f "${AGENT_UNIT_DST}" ]]; then
        info "hal0-agent@hermes not enabled — run 'hal0 agent bootstrap hermes' first"
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
    if curl -sf --max-time 3 "${HELLO_BASE}/api/health" >/dev/null 2>&1; then
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

# --dev mode caveat — systemd units were written into the dev tree, not
# /etc/systemd/system, so the host's systemctl does not see them. The
# `hal0 slot create && hal0 slot load` flow will fail with "Unit
# hal0-slot@<name>.service not found" until the operator either runs a
# real (sudo) install or links the dev units into the system search
# path. Documented at installer/README.md ("--dev mode limitations").
# Tracked as harness finding #6 / task #24.
if [[ "${DEV_MODE}" -eq 1 ]]; then
    printf '\n'
    warn "--dev mode: systemd units written to ${UNIT_DIR}"
    warn "            (not /etc/systemd/system — host systemctl can't see them)"
    warn ""
    warn "  Consequence: 'hal0 slot load' will fail with"
    warn "    'Unit hal0-slot@<name>.service not found'"
    warn "  because the host's systemctl only consults /etc/systemd/system"
    warn "  and /usr/lib/systemd/system."
    warn ""
    warn "  Workarounds:"
    warn "    (a) sudo bash installer/install.sh        # real install at /opt/hal0"
    warn "    (b) sudo systemctl link ${UNIT_DIR}/hal0-slot@.service"
    warn "        sudo systemctl daemon-reload          # then slot load works"
    warn ""
    warn "  See installer/README.md ('--dev mode limitations') for details."
fi

ui_box "hal0 is ready" "${SUMMARY_LINES[@]}"
