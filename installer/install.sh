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
# Caddy provisioning TLS) don't get falsely flagged as failures.
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
# TLS posture (per ADR-0001 Child B):
#   default — install Caddy as a dumb TLS terminator + reverse proxy on
#             :443 in front of the API on 127.0.0.1:8080. Auth (password
#             + session cookies + Bearer) lives entirely in FastAPI.
#   --no-tls — skip Caddy. FastAPI binds 0.0.0.0:8080 directly. Right
#             for hosts behind an existing reverse proxy (Traefik etc.)
#             or for trusted-LAN dev boxes that don't need TLS.
NO_TLS=0
# Pull destination for `hal0 model pull` and the dashboard's pull buttons.
# Empty → ask interactively when stdin is a tty, default to <var-lib>/models
# otherwise. The chosen path is written to hal0.toml as [models].pull_root
# and also auto-included in [models].roots so it's scanned at startup.
MODELS_DIR="${HAL0_MODELS_DIR:-}"
for arg in "$@"; do
    case "$arg" in
        --dev) DEV_MODE=1 ;;
        --no-start) NO_START=1 ;;
        --no-tls) NO_TLS=1 ;;
        --models-dir=*) MODELS_DIR="${arg#--models-dir=}" ;;
        --help|-h)
            cat <<EOF
Usage: install.sh [--dev] [--no-start] [--no-tls] [--models-dir=PATH]
  --dev               install under \$PWD/.hal0ai/, no systemd setup
  --no-start          set up everything but don't enable/start the API
  --no-tls            skip the Caddy reverse proxy; bind FastAPI on
                      0.0.0.0:8080 directly. No TLS, no edge proxy. Auth
                      (password + tokens) still works — set it up in the
                      first-run wizard or via the dashboard's Settings panel.
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

# --dev implies --no-tls — there's no system Caddy install in a dev tree
# and the prefix-relative unit paths won't match Caddy's expectations
# anyway. Warn the operator if they passed --no-tls redundantly.
if [[ "${DEV_MODE}" -eq 1 ]]; then
    if [[ "${NO_TLS}" -eq 0 ]]; then
        info "--dev implies --no-tls (no system Caddy install in dev tree)"
    fi
    NO_TLS=1
fi

# Banner first — before any info/warn so the brand greets the user
# rather than hiding behind a "Dev mode …" line.
ui_banner

HAL0_PORT="${HAL0_PORT:-8080}"
HAL0_USER="${HAL0_USER:-root}"
PY="${HAL0_PYTHON:-python3}"

# API bind host:
#   --no-tls         → 0.0.0.0 (the API is the front door)
#   default (Caddy)  → 127.0.0.1 (Caddy on :443 is the front door; the
#                                 API is loopback-only so it can't be
#                                 reached around the TLS terminator)
# DEV_MODE forces NO_TLS=1 above, so dev installs bind 0.0.0.0 too —
# matches the prior behaviour where dev installs were directly reachable
# on the LAN for testing.
if [[ "${NO_TLS}" -eq 1 ]]; then
    API_BIND_HOST="0.0.0.0"
else
    API_BIND_HOST="127.0.0.1"
fi

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

# Step total — base 8, +1 for the optional TLS / Caddy setup. Kept
# here so editors who add or remove a ui_step bump the visible counter
# in the same diff.
UI_STEP_TOTAL=8
if [[ "${NO_TLS}" -eq 0 ]]; then
    UI_STEP_TOTAL=$((UI_STEP_TOTAL + 1))           # "TLS (Caddy reverse proxy)"
fi

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
        "TLS (Caddy reverse proxy)")
            warn "Recovery: install caddy by hand and re-run,"
            warn "         or rerun with --no-tls to skip the reverse proxy entirely." ;;
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
            HAL0_PUBLIC_HOST="${HAL0_PUBLIC_HOST:-}" HAL0_TLS_EMAIL="${HAL0_TLS_EMAIL:-}" \
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
# Auth is **on by default** as of v1.0 (security review §36). The wizard
# captures the owner password on first visit; until then, only the wizard
# claim paths (/api/install/*, /api/auth/password) are reachable without
# credentials. Set HAL0_AUTH_DISABLED=1 to opt back into the pre-v1
# trusted-LAN open posture — only safe on single-user dev boxes.
# HAL0_AUTH_DISABLED=1
# Uncomment to pin specific toolbox images:
# HAL0_TOOLBOX_IMAGE_VULKAN=ghcr.io/hal0ai/hal0-toolbox-vulkan:v1
# HAL0_TOOLBOX_IMAGE_ROCM=ghcr.io/hal0ai/hal0-toolbox-rocm:v1
EOF
    info "wrote ${API_ENV}"
fi

# ── First-run claim lockfile ─────────────────────────────────────────
# Per security review §36 (auth-on-by-default) + §28 (first-run claim
# race), drop a one-time-password lockfile that scopes the
# unauthenticated wizard window to operators who can read this file on
# disk. The middleware honours the lockfile only while the owner
# password is unset; once the wizard finishes, the file is deleted.
#
# The OTP is a UUID hex (128 bits, plenty for a one-shot bootstrap).
# Owner = the API service user (root in the v1 install layout — see
# HAL0_USER above). Mode 0600 so neighbours on the host cannot read
# the value out of band. The summary box surfaces it once at the end
# of the install for the operator to type into the wizard's first
# prompt.
FIRST_RUN_LOCK="${VAR_DIR}/.first-run.lock"
FIRST_RUN_OTP=""
if [[ ! -f "${FIRST_RUN_LOCK}" ]]; then
    # Mint a UUID OTP. ``uuidgen`` lives in util-linux on every host
    # we ship to; falling back to ``/proc/sys/kernel/random/uuid``
    # keeps minimal containers (no util-linux) working.
    if command -v uuidgen >/dev/null 2>&1; then
        FIRST_RUN_OTP="$(uuidgen | tr -d '-' | tr 'A-Z' 'a-z')"
    elif [[ -r /proc/sys/kernel/random/uuid ]]; then
        FIRST_RUN_OTP="$(tr -d '-' </proc/sys/kernel/random/uuid)"
    else
        FIRST_RUN_OTP="$(${PY} -c 'import uuid; print(uuid.uuid4().hex)')"
    fi
    # Write atomically: temp file in the same directory + rename, so a
    # crash mid-write doesn't leave a half-empty lockfile with the wrong
    # mode. ``install -m 0600`` would also set ownership, but we just
    # need mode here — the service user owns ${VAR_DIR} already.
    LOCK_TMP="$(mktemp "${FIRST_RUN_LOCK}.XXXXXX")"
    printf 'otp=%s\n' "${FIRST_RUN_OTP}" > "${LOCK_TMP}"
    chmod 0600 "${LOCK_TMP}"
    mv "${LOCK_TMP}" "${FIRST_RUN_LOCK}"
    info "wrote ${FIRST_RUN_LOCK} (first-run claim OTP, mode 0600)"
else
    # Idempotent rerun: keep the existing OTP in place so a re-run
    # doesn't invalidate a token the operator already noted from a
    # prior install attempt. Parse out the value to redisplay in the
    # summary box.
    FIRST_RUN_OTP="$(awk -F= '$1=="otp"{print $2; exit}' "${FIRST_RUN_LOCK}" 2>/dev/null || true)"
    info "${FIRST_RUN_LOCK} already exists — reusing existing OTP"
fi

UPSTREAMS_TOML="${ETC_DIR}/upstreams.toml"
if [[ ! -f "${UPSTREAMS_TOML}" ]]; then
    cat > "${UPSTREAMS_TOML}" <<EOF
# External LLM upstreams — populated via the WebUI Providers tab,
# 'hal0 config edit' here, or directly with the API.
EOF
    info "wrote ${UPSTREAMS_TOML}"
fi

# ── TLS / Caddy reverse proxy (default unless --no-tls) ──────────────
#
# Per ADR-0001 Child B, Caddy is now a dumb TLS terminator + reverse
# proxy — no basic_auth at the edge, no PUBLIC_PATHS allowlist, no
# admin credential prompts. Auth (password + session cookies + Bearer
# tokens) lives entirely in FastAPI; the wizard owns first-run
# credential capture.
#
# Two paths from here:
#   NO_TLS=1 — skip this whole block. API binds 0.0.0.0:8080 directly.
#   default  — install Caddy, render the minimal Caddyfile, write the
#              systemd unit. Caddy listens on :443, proxies to the API
#              on 127.0.0.1:8080. Auth (if any) is whatever the
#              operator sets up via the wizard.
#
# Done BEFORE the openwebui env writer so trusted-header keys are baked
# in on the first render rather than requiring a second pass.
HAL0_AUTH_ENABLED_FOR_RENDER="0"
if [[ "${NO_TLS}" -eq 0 ]]; then
    ui_step "TLS (Caddy reverse proxy)"

    # Caddy install — Debian/Ubuntu via apt, Arch/CachyOS via pacman.
    # Anything else falls through with a manual-install hint.
    if ! command -v caddy >/dev/null; then
        if command -v apt-get >/dev/null; then
            info "installing caddy via apt"
            apt-get update -qq
            # The official caddy package on Debian needs a third-party
            # repo; for a polished installer we pin to the cloudsmith
            # mirror in a v0.3 follow-up. For the POC, surface the
            # missing-binary path so an operator can apt install caddy
            # by hand and re-run.
            APT_ERR="$(mktemp)"
            if ! apt-get install -y caddy 2>"${APT_ERR}"; then
                warn "apt failed to install caddy:"
                sed 's/^/    /' "${APT_ERR}" >&2
                warn "Install per https://caddyserver.com/docs/install#debian-ubuntu-raspbian and re-run."
            fi
            rm -f "${APT_ERR}"
        elif command -v pacman >/dev/null; then
            info "installing caddy via pacman"
            pacman -S --noconfirm caddy
        else
            warn "no recognised package manager for caddy; install it from https://caddyserver.com/docs/install and re-run"
        fi
    fi
    if ! command -v caddy >/dev/null; then
        die "caddy binary not on PATH after install attempt — see warnings above (or rerun with --no-tls)"
    fi
    info "caddy: $(caddy version 2>/dev/null | head -n1 || echo unknown)"

    # Public host + TLS posture. HAL0_PUBLIC_HOST defaults to hal0.local
    # for the mDNS case (Caddy's internal CA mints a self-signed cert).
    # For a real DNS-resolvable host the operator sets HAL0_PUBLIC_HOST
    # + HAL0_TLS_EMAIL and Caddy runs ACME against Let's Encrypt.
    HAL0_PUBLIC_HOST="${HAL0_PUBLIC_HOST:-hal0.local}"
    HAL0_TLS_EMAIL="${HAL0_TLS_EMAIL:-admin@${HAL0_PUBLIC_HOST}}"

    # Decide the `tls` directive value. ``internal`` triggers Caddy's
    # private CA path (right for *.local + IP literals); anything else
    # is treated as an ACME contact email. We auto-detect *.local here
    # so a fresh ``sudo bash install.sh`` does the right thing without
    # the operator having to know that ACME-on-.local fails noisily.
    if [[ "${HAL0_PUBLIC_HOST}" == *.local || "${HAL0_PUBLIC_HOST}" == "localhost" ]]; then
        HAL0_TLS_DIRECTIVE="internal"
    else
        HAL0_TLS_DIRECTIVE="${HAL0_TLS_EMAIL}"
    fi

    # Render the Caddyfile from the template. Python so the renderer
    # doesn't depend on envsubst / coreutils on minimal hosts; the
    # template only ships {$KEY} / {$KEY:default} placeholders that the
    # renderer expands at install time.
    CADDY_TEMPLATE="${REPO_ROOT}/packaging/caddy/Caddyfile.template"
    CADDY_TARGET="${ETC_DIR}/Caddyfile"
    if [[ ! -f "${CADDY_TEMPLATE}" ]]; then
        die "Caddyfile template missing at ${CADDY_TEMPLATE}"
    fi
    HAL0_PUBLIC_HOST="${HAL0_PUBLIC_HOST}" \
    HAL0_TLS_EMAIL="${HAL0_TLS_EMAIL}" \
    HAL0_TLS_DIRECTIVE="${HAL0_TLS_DIRECTIVE}" \
    "${PY}" - "${CADDY_TEMPLATE}" "${CADDY_TARGET}" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src).read()
# Caddy's own ${VAR:default} placeholder syntax is what the template
# uses inside its config — preserve those by only substituting the
# specific keys we know about. Anything else is passed through verbatim
# so Caddy's runtime substitution still works.
keys = ("HAL0_PUBLIC_HOST", "HAL0_TLS_EMAIL", "HAL0_TLS_DIRECTIVE")
for k in keys:
    val = os.environ.get(k, "")
    text = text.replace("{$" + k + "}", val)
    # Match {$KEY:default} regardless of default value.
    while True:
        marker = "{$" + k + ":"
        i = text.find(marker)
        if i < 0:
            break
        j = text.find("}", i)
        if j < 0:
            break
        text = text[:i] + val + text[j+1:]
open(dst, "w").write(text)
# 0644 so the unprivileged 'caddy' user can read the rendered file.
# Nothing in this template is sensitive post-ADR-0001 (no password
# hashes, no secrets — just hostname + ACME email).
os.chmod(dst, 0o644)
print(f"  rendered {dst}")
PY
    info "wrote ${CADDY_TARGET}"

    # systemd unit drop-in.
    CADDY_UNIT_SRC="${REPO_ROOT}/packaging/systemd/hal0-caddy.service"
    CADDY_UNIT_DST="${UNIT_DIR}/hal0-caddy.service"
    if [[ -f "${CADDY_UNIT_SRC}" ]]; then
        cp "${CADDY_UNIT_SRC}" "${CADDY_UNIT_DST}"
        info "wrote ${CADDY_UNIT_DST}"
    else
        warn "${CADDY_UNIT_SRC} not found — Caddy unit not installed"
    fi

    # Avahi (best-effort).
    if command -v avahi-daemon >/dev/null && [[ -d /etc/avahi/services ]]; then
        cp "${REPO_ROOT}/packaging/avahi/hal0.service" /etc/avahi/services/hal0.service
        info "wrote /etc/avahi/services/hal0.service (mDNS announcing ${HAL0_PUBLIC_HOST})"
    else
        warn "avahi-daemon not present; add an /etc/hosts entry on clients: '<this-host-ip> ${HAL0_PUBLIC_HOST}'"
    fi

    # Auth is on by default at the API level (v1.0 security review §36)
    # — we still flip OpenWebUI's prewire to use the Caddy SSO header
    # because that's a Caddy-fronted concern. For the API itself the
    # env var is the legacy switch; the new HAL0_AUTH_DISABLED escape
    # hatch is documented in the api.env template instead.
    HAL0_AUTH_ENABLED_FOR_RENDER="1"
fi

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

SLOT_TEMPLATE_SRC="${REPO_ROOT}/packaging/systemd/hal0-slot@.service"
SLOT_TEMPLATE_DST="${UNIT_DIR}/hal0-slot@.service"
if [[ -f "${SLOT_TEMPLATE_SRC}" ]]; then
    cp "${SLOT_TEMPLATE_SRC}" "${SLOT_TEMPLATE_DST}"
    info "wrote ${SLOT_TEMPLATE_DST}"
else
    warn "${SLOT_TEMPLATE_SRC} not found — slot template not installed"
fi

OPENWEBUI_UNIT_SRC="${REPO_ROOT}/packaging/systemd/hal0-openwebui.service"
OPENWEBUI_UNIT_DST="${UNIT_DIR}/hal0-openwebui.service"
if [[ -f "${OPENWEBUI_UNIT_SRC}" ]]; then
    cp "${OPENWEBUI_UNIT_SRC}" "${OPENWEBUI_UNIT_DST}"
    info "wrote ${OPENWEBUI_UNIT_DST}"
else
    warn "${OPENWEBUI_UNIT_SRC} not found — OpenWebUI unit not installed"
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

    # Default (TLS) install: bring up Caddy in front. Per ADR-0001 Child B
    # this is a dumb TLS terminator + reverse proxy — no edge auth, no
    # round-trip self-test against a credential (because there is no
    # credential at the edge anymore). The wizard captures a password
    # post-install; until then the server is open on the LAN, matching
    # the trusted-LAN default documented in the ADR.
    if [[ "${NO_TLS}" -eq 0 && -f "${UNIT_DIR}/hal0-caddy.service" ]]; then
        systemctl restart hal0-api
        systemctl restart hal0-openwebui || true
        systemctl enable --now hal0-caddy
        if wait_active hal0-caddy 15; then
            info "hal0-caddy is running (https://${HAL0_PUBLIC_HOST:-hal0.local}/)"
        else
            warn "hal0-caddy not yet active; check 'journalctl -u hal0-caddy -n 60'"
        fi
        # Reload avahi so the freshly-dropped service file is announced
        # (best-effort — failing reload doesn't break anything).
        if command -v systemctl >/dev/null && systemctl is-active --quiet avahi-daemon; then
            systemctl reload avahi-daemon || true
        fi
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

if [[ "${NO_TLS}" -eq 0 ]]; then
    HOSTNAME_FOR_REACH="${HAL0_PUBLIC_HOST:-hal0.local}"
    DASHBOARD_URL="https://${HOSTNAME_FOR_REACH}/"
    REACH_LINES+=("mDNS"$'\t'"https://${HOSTNAME_FOR_REACH}/")
else
    DASHBOARD_URL="http://${HOST}:${HAL0_PORT}/"
    # All IPv4 addresses on this host. `hostname -I` already excludes
    # loopback. Add an avahi mDNS entry only if we wrote the service file.
    if command -v hostname >/dev/null 2>&1; then
        for ip in $(hostname -I 2>/dev/null); do
            REACH_LINES+=("LAN"$'\t'"http://${ip}:${HAL0_PORT}/")
        done
    fi
    if [[ -f /etc/avahi/services/hal0.service ]]; then
        REACH_LINES+=("mDNS"$'\t'"http://${HAL0_PUBLIC_HOST:-hal0.local}:${HAL0_PORT}/")
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
    if [[ "${NO_TLS}" -eq 0 ]]; then
        # TLS-default install: Caddy fronts both services, dashboard
        # lives on https://<public-host>/. Auth is **on by default**
        # (v1.0 security review §36); the wizard captures the owner
        # password using the one-time OTP printed below.
        HOSTNAME_FOR_DISPLAY="${HAL0_PUBLIC_HOST:-hal0.local}"
        SUMMARY_LINES+=(
            "$(printf 'Dashboard   %shttps://%s/%s' "${BLU}" "${HOSTNAME_FOR_DISPLAY}" "${RST}")"
            "$(printf 'Chat        %shttp://%s:3001%s' "${BLU}" "${HOST}" "${RST}")"
            "$(printf 'Auth        %slocked — finish first-run wizard to set a password%s' "${DIM}" "${RST}")"
            "$(printf 'Logs        %sjournalctl -fu hal0-caddy hal0-api hal0-openwebui%s' "${DIM}" "${RST}")"
        )
    else
        # --no-tls: API binds 0.0.0.0:8080 directly. No TLS, no edge
        # proxy. Auth is still on by default; only the wizard claim
        # paths are reachable until the operator sets a password.
        SUMMARY_LINES+=(
            "$(printf 'Dashboard   %shttp://%s:%s%s' "${BLU}" "${HOST}" "${HAL0_PORT}" "${RST}")"
            "$(printf 'Chat        %shttp://%s:3001%s' "${BLU}" "${HOST}" "${RST}")"
            "$(printf 'TLS         %soff (--no-tls) — no TLS, no edge proxy%s' "${DIM}" "${RST}")"
            "$(printf 'Auth        %slocked — finish first-run wizard to set a password%s' "${DIM}" "${RST}")"
            "$(printf 'Logs        %sjournalctl -fu hal0-api%s' "${DIM}" "${RST}")"
        )
    fi

    # First-run claim OTP. The wizard's "Set a password" step asks for
    # this verbatim; it proves the operator can read root-owned files
    # on the host and prevents a LAN attacker from racing in to claim
    # ownership before the legitimate operator opens the dashboard.
    # The lockfile is auto-deleted once the password is set.
    if [[ -n "${FIRST_RUN_OTP:-}" ]]; then
        SUMMARY_LINES+=(
            ""
            "$(printf '%sFirst-run OTP:%s' "${BOLD}" "${RST}")"
            "$(printf '  %s%s%s' "${AMBER}" "${FIRST_RUN_OTP}" "${RST}")"
            "$(printf '  %spaste this into the wizard to claim ownership.%s' "${DIM}" "${RST}")"
            "$(printf '  %slockfile: %s%s' "${DIM}" "${FIRST_RUN_LOCK}" "${RST}")"
        )
    fi
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

# First-run OTP banner (FINDINGS §28). The API mints a one-time token
# at ``${VAR_DIR}/.first-run.lock`` whenever the owner password is
# unset. Operators reaching the dashboard from a non-loopback host
# (browser on a laptop hitting hal0.local) need to paste this token
# into the wizard's password step. Loopback callers (a curl on the
# host itself) bypass the gate, so a local install with `curl
# http://127.0.0.1:8080/api/auth/password ...` still works without
# the token.
#
# The lockfile may not exist yet (the API is still starting; the file
# is created in the lifespan hook). We poll briefly and surface the
# token when it lands, otherwise fall back to a "find it at <path>"
# hint so the operator knows where to look.
if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 ]]; then
    FIRST_RUN_LOCK="${VAR_DIR}/.first-run.lock"
    FIRST_RUN_OTP=""
    for _attempt in 1 2 3 4 5; do
        if [[ -r "${FIRST_RUN_LOCK}" ]]; then
            # Lockfile is JSON {"otp": "...", "created_at": N}. Use
            # grep + sed rather than depending on jq being installed —
            # the installer cannot assume jq is on the host.
            FIRST_RUN_OTP="$(sed -nE 's/.*"otp"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "${FIRST_RUN_LOCK}" 2>/dev/null || true)"
            [[ -n "${FIRST_RUN_OTP}" ]] && break
        fi
        sleep 1
    done

    SUMMARY_LINES+=(
        ""
        "$(printf '%sFirst-run setup token:%s' "${BOLD}" "${RST}")"
    )
    if [[ -n "${FIRST_RUN_OTP}" ]]; then
        SUMMARY_LINES+=(
            "$(printf '  %s%s%s' "${BLU}" "${FIRST_RUN_OTP}" "${RST}")"
            "$(printf '  %sPaste into the wizard password step. Loopback callers (curl on this host) bypass it.%s' "${DIM}" "${RST}")"
        )
    else
        SUMMARY_LINES+=(
            "$(printf '  %s(API still starting — read it from %s once available)%s' "${DIM}" "${FIRST_RUN_LOCK}" "${RST}")"
        )
    fi
fi

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
