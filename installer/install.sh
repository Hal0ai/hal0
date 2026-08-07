#!/usr/bin/env bash
# hal0 installer — idempotent, non-interactive.
#
# Usage:
#   sudo bash install.sh             # standard install at /usr/lib/hal0
#   bash install.sh --dev            # local-only install under $PWD/.hal0ai
#   sudo bash install.sh --no-start  # set up everything but don't start units
#
# Env overrides:
#   HAL0_PREFIX        installation root (default /usr/lib/hal0)
#   HAL0_PORT          API port (default 8080)
#   HAL0_PYTHON        python interpreter (default python3)
#   HAL0_NO_PROBE=1    skip the hardware probe at the end
#   HAL0_TOOLBOX_IMAGE_VULKAN, HAL0_TOOLBOX_IMAGE_ROCM, ...
#                      override per-backend container image refs

set -euo pipefail
IFS=$'\n\t'

# A hardened root umask (0027/0077 from a CIS/STIG host, or a login shell
# that sets `umask 077`) leaks into everything this script creates — the
# venv, the FHS code tree, /etc/hal0, /var/lib/hal0 — leaving it 0700 and
# breaking the non-root `hal0` CLI (PermissionError reading /usr/lib/hal0,
# /etc/hal0/slots, /var/lib/hal0/{registry,models}). Chmod-patching one
# path at a time (as the /etc/hal0 fix below already does) doesn't scale;
# normalize to the conventional 022 for the whole install body instead.
# Restored at the very end of the script — this process's umask never
# escapes to the caller's shell anyway, but symmetry is cheap.
_HAL0_ORIG_UMASK="$(umask)"
umask 022

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
# Empty → prompt on an interactive terminal, else default to <var-lib>/models.
# The chosen path is written to hal0.toml as [models].store (+ the deprecated
# pull_root) and also auto-included in [models].roots so it's scanned at startup.
MODELS_DIR="${HAL0_MODELS_DIR:-}"
# Set when --models-dir / HAL0_MODELS_DIR supplied the value: an EXPLICIT
# choice is never re-asked at the TTY prompt below.
MODELS_DIR_EXPLICIT=0
[[ -n "${MODELS_DIR}" ]] && MODELS_DIR_EXPLICIT=1
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
                      Omit it and the installer asks on an interactive terminal;
                      a piped (\`curl … | bash\`) or headless run takes the default.

Interactive prompts (models dir, HuggingFace token) only run when stdin is a
terminal. Set HAL0_NONINTERACTIVE=1 to force the flag/env values everywhere.
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
PY="${HAL0_PYTHON:-python3}"

# ==========================================================================
# SECURITY — LAN-ONLY BIND. READ BEFORE CHANGING.
# --------------------------------------------------------------------------
# hal0-api binds 0.0.0.0:8080, i.e. EVERY network interface. This is a
# deliberate zero-config choice so the dashboard is reachable from any
# device on the *local network* without setup. It is NOT hardened for the
# public internet: hal0 ships NO TLS, NO edge auth, NO rate limiting on
# this bind.
#
#   * SAFE:   a trusted LAN / VPN / private subnet behind a firewall.
#   * UNSAFE: exposing 0.0.0.0:8080 directly to the internet (port-forward,
#             cloud VM with a public IP, DMZ). Anyone who can reach the
#             port can drive the API.
#
# To expose hal0 externally, put a reverse proxy (Traefik / nginx /
# Cloudflare Tunnel) in FRONT of it for TLS + auth — see docs/operate/tls.md
# — and firewall :8080 off from the public interface. Do NOT flip this to a
# loopback-only bind: 127.0.0.1 breaks the zero-config LAN dashboard.
# ==========================================================================
API_BIND_HOST="0.0.0.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${DEV_MODE}" -eq 1 ]]; then
    # Dev: editable checkout, everything under one prefix. `hal0 update`
    # hard-refuses in this mode (the updater detects the editable install via
    # PEP 610 metadata) — run `git pull && pip install -e .` to update.
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

# Resolve pull destination: explicit flag / env wins, then the FHS default.
# On an interactive terminal the operator gets a chance to change it (see the
# "Operator input" block after the pre-flight gates — it must run AFTER the
# sudo re-exec, or the answer would be lost and the question asked twice).
# Always absolute — relative paths under sudo would land in /root or wherever
# the install was launched.
DEFAULT_MODELS_DIR="${VAR_DIR}/models"
if [[ -z "${MODELS_DIR}" ]]; then
    MODELS_DIR="${DEFAULT_MODELS_DIR}"
fi
if [[ "${MODELS_DIR}" != /* ]]; then
    die "--models-dir must be an absolute path (got: ${MODELS_DIR})"
fi

# Step total. Kept here so editors who add or remove a ui_step bump the
# visible counter in the same diff. Verify with:
#   grep -c '^ui_step ' installer/install.sh
# (it drifted to 13-vs-14 before v1.0 — tests/install/test_ui_step_total.py
# now asserts the two agree).
UI_STEP_TOTAL=16

trap 'err "install failed at line ${LINENO} during: ${CURRENT_STEP:-pre-init}"
    case "${CURRENT_STEP}" in
        "Pre-flight checks")
            warn "Recovery: free space under ${VAR_DIR:-/var/lib/hal0} (need ≥20 GB),"
            warn "         or stop the process holding the port and rerun. hal0 own"
            warn "         units (hal0-api / hal0-openwebui) are exempted automatically,"
            warn "         so this is a foreign process on the port; find it with"
            warn "         ss -ltnp sport = :PORT . If it is actually a stuck"
            warn "         hal0-api / hal0-openwebui the exemption did not recognize, try"
            warn "         systemctl stop hal0-api hal0-openwebui and rerun. Set"
            warn "         HAL0_PORT=<other> to bind a different API port; OpenWebUI"
            warn "         :3001 is hardcoded in the systemd unit." ;;
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
        exec sudo -E HAL0_PORT="${HAL0_PORT}" HAL0_PYTHON="${PY}" \
            HAL0_PREFIX="${HAL0_PREFIX:-}" HAL0_NO_PROBE="${HAL0_NO_PROBE:-}" \
            bash "$0" "$@"
    else
        die "must run as root (sudo bash install.sh)"
    fi
fi

info "system: $(uname -srm)"

# ── Bootstrap-prereq parity (#1098) ─────────────────────────────────────────
# bootstrap.sh (the curl|bash one-liner) hard-requires a Linux host plus
# curl/tar/sha256sum in its own preflight() before it ever fetches the
# release tarball (installer/bootstrap.sh). install.sh itself leans on all
# three later in this same run (preflight_network's curl probe, the
# rsync-fallback tar copy below, the FLM .deb sha256 check) — but a direct
# `sudo bash install.sh`, with no bootstrap in front, never checked for
# them up front. Run the same check bootstrap.sh does so a minimal host
# missing one of them fails here with an actionable message instead of a
# bare "command not found" deep in the install.
preflight_bootstrap_prereqs || die "missing base prereqs — see above (installer/bootstrap.sh's one-liner preflight requires the same tools)"

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
# below hal0's floor (pyproject.toml requires-python >=3.12; it logs an
# `err`/`warn` itself). A below-floor interpreter is NOT survivable — pip
# install "${REPO_ROOT}" always fails on it — so unlike a merely-missing
# python (which just needs installing per the hint below), we actively try
# to resolve or auto-install a compatible interpreter (mirrors the
# Hindsight venv's resolve_hindsight_python) instead of limping ahead only
# to die minutes later, deep inside "pip install", on Debian 12 (python3
# 3.11) / Ubuntu 22.04 (python3 3.10) — every stock LTS whose system
# python3 predates 3.12.
if ! preflight_python; then
    if ! command -v "${PY}" >/dev/null 2>&1; then
        die "python interpreter '${PY}' not found — install with: $(python_venv_hint)"
    fi
    if resolved_py="$(HAL0_PY_AUTOINSTALL=1 resolve_main_python)" && [[ -n "${resolved_py}" ]]; then
        info "using ${resolved_py} for the main hal0 venv (default ${PY} is below hal0's 3.12 floor)"
        PY="${resolved_py}"
        export HAL0_PYTHON="${PY}"
    else
        die "python '${PY}' is below hal0's floor (pyproject.toml requires-python >=3.12) and no compatible interpreter could be found or installed.
  install one manually, e.g.: $(pkg_install_cmd python3.12 python3.12-venv 2>/dev/null || echo 'install python3.12'), then re-run with HAL0_PYTHON=python3.12"
    fi
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

# ── GPU / NPU device gate (WS-B, #1104) ─────────────────────────────────────
# preflight_gpu detects GPU/NPU device visibility and, inside an LXC, whether
# the render node's gid maps to a real group. In `hal0 doctor` it is
# advisory-only; run here in GATE mode (HAL0_GPU_GATE=1) it returns a code we
# smart-block on, so a box with broken Proxmox passthrough never installs
# "successfully" and then silently runs every slot CPU-only:
#   HAL0_GPU_RC_BROKEN_GID → devices visible but the render gid maps to no group
#     inside this container (the #1 broken-install shape). HARD STOP with the
#     dev0 remedy preflight_gpu just printed; the operator fixes the host dev0
#     line and re-runs.
#   HAL0_GPU_RC_NO_DEVICE  → no GPU devices inside an LXC. Allow an EXPLICIT
#     CPU-only opt-in: HAL0_ALLOW_CPU_ONLY=1, or a y/N confirm on a real TTY;
#     otherwise stop with the passthrough remedy.
#   0 → GPU present + wired, or a genuine bare-metal CPU box: proceed silently.
# Skipped in dev mode (no system slots there). This block is self-contained so
# later install.sh edits merge around it cleanly.
_confirm_cpu_only() {
    # y/N confirm read from the controlling terminal, so it works even when
    # stdin is the piped install script (`curl … | bash`). Default No; any
    # read failure (no TTY) also means No.
    local reply=""
    printf '%s' "Continue with a CPU-only install anyway? [y/N] " >/dev/tty 2>/dev/null || return 1
    IFS= read -r reply </dev/tty 2>/dev/null || return 1
    [[ "${reply}" =~ ^[Yy]([Ee][Ss])?$ ]]
}

# ── Interactive-input gate (v1.0) ───────────────────────────────────────────
# The installer is the single user-facing entry point, so the two answers it
# genuinely needs from a human — where models live, and a HuggingFace read
# token — are asked HERE rather than in a post-install wizard.
#
# `_interactive` is the ONE predicate that decides whether anything is asked.
# It tests **stdin**, not /dev/tty, and that distinction is load-bearing:
#
#   sudo bash install.sh            stdin = the terminal      → interactive
#   curl -fsSL … | sudo bash        stdin = the script pipe   → NON-interactive
#   ssh host 'sudo bash install.sh' stdin = no tty at all     → NON-interactive
#
# bootstrap.sh documents that same contract (it forwards stdin so a downloaded
# `sudo bash install.sh` can prompt, while `curl | bash` "falls back to
# defaults"), and scripts/fresh-test-ct.sh drives the install over a
# tty-less ssh. Both therefore stay fully unattended, which is the
# requirement: an install that blocks on a prompt in CI is a broken install.
# HAL0_NONINTERACTIVE=1 forces the headless path even on a terminal.
#
# The GPU-gate confirm above deliberately keeps its own /dev/tty behaviour —
# it is a "your box is misconfigured, are you sure?" safety stop, not an
# input prompt, and it already defaults to the safe answer.
_interactive() {
    [[ "${HAL0_NONINTERACTIVE:-0}" != "1" ]] && [[ -t 0 ]] && [[ -r /dev/tty ]]
}

# Read one line from the controlling terminal, echoing `prompt` first and
# falling back to `default` on an empty answer or any read failure. Result
# lands in the caller-named variable (nameref) so the value never travels
# through a subshell — `$(...)` would swallow the prompt written to /dev/tty
# on some terminals and cannot be used with `read -s`.
#   _tty_read <outvar> <prompt> [default] [silent]
_tty_read() {
    local -n _out="$1"
    local prompt="$2" default="${3:-}" silent="${4:-0}" reply=""
    if [[ -n "${default}" && "${silent}" != "1" ]]; then
        printf '%s [%s]: ' "${prompt}" "${default}" >/dev/tty 2>/dev/null || { _out="${default}"; return 0; }
    else
        printf '%s: ' "${prompt}" >/dev/tty 2>/dev/null || { _out="${default}"; return 0; }
    fi
    if [[ "${silent}" == "1" ]]; then
        IFS= read -rs reply </dev/tty 2>/dev/null || reply=""
        printf '\n' >/dev/tty 2>/dev/null || true
    else
        IFS= read -r reply </dev/tty 2>/dev/null || reply=""
    fi
    _out="${reply:-${default}}"
}

if [[ "${DEV_MODE}" -eq 0 ]]; then
    gpu_rc=0
    HAL0_GPU_GATE=1 preflight_gpu || gpu_rc=$?
    if (( gpu_rc == HAL0_GPU_RC_BROKEN_GID )); then
        err "GPU passthrough is broken: the render device is visible but its gid does not map to the render group in this container (no group, or the wrong one)."
        err "Every GPU slot would silently fall back to CPU. Apply the dev0/gid fix shown above on the Proxmox host, then re-run install.sh."
        exit 1
    elif (( gpu_rc == HAL0_GPU_RC_NO_DEVICE )); then
        if [[ "${HAL0_ALLOW_CPU_ONLY:-0}" == "1" ]]; then
            warn "No GPU devices inside this container — proceeding CPU-only (HAL0_ALLOW_CPU_ONLY=1)."
        elif [[ -r /dev/tty ]] && _confirm_cpu_only; then
            warn "Proceeding with a CPU-only install (confirmed at the prompt)."
        else
            err "No GPU devices inside this container. Forward them from the Proxmox host (remedy above), then re-run install.sh."
            err "To install CPU-only anyway, re-run with HAL0_ALLOW_CPU_ONLY=1."
            exit 1
        fi
    fi
fi

# ── Operator input: model store + HuggingFace token ─────────────────────────
# Asked once, here — after the sudo re-exec (so the answers survive) and
# before preflight_disk measures MODELS_DIR (so it measures the REAL choice).
# Both questions are pre-filled from the flag/env value, so hitting Enter is
# always the documented default and a headless run behaves exactly as before.
#
# The HF token is only GATHERED here; validation (`hf auth whoami`) and the
# 0600 persist still happen in the "Configuration" step below, which is the
# first point where the venv's `hf` console script exists.
HF_TOKEN_VAL="${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-}}"
# Pre-declared because `_tty_read` writes through a nameref, which shellcheck
# cannot follow (SC2154) — and an unset target would trip `set -u` if the
# helper ever grew an early return.
_md_answer=""
_hf_answer=""
if _interactive; then
    if [[ "${MODELS_DIR_EXPLICIT}" -eq 1 ]]; then
        info "model store fixed by --models-dir / HAL0_MODELS_DIR — not asking"
    else
        while true; do
            _tty_read _md_answer "Where should downloaded models live?" "${MODELS_DIR}"
            if [[ "${_md_answer}" == /* ]]; then
                MODELS_DIR="${_md_answer}"
                break
            fi
            warn "that must be an absolute path (starts with /) — models are pulled as root, so a relative path would land in an unpredictable directory"
        done
    fi
    # Token prompt: silent (never echoed, never logged). An existing env token
    # is offered as "keep" rather than redisplayed — printing a secret back at
    # the operator is exactly what the 0600 secrets file exists to avoid.
    if [[ -n "${HF_TOKEN_VAL}" ]]; then
        info "HuggingFace token: taken from the environment (HF_TOKEN / HUGGING_FACE_HUB_TOKEN)"
    else
        printf '\n' >/dev/tty 2>/dev/null || true
        info "A HuggingFace read token lets hal0 pull gated repos. Open models need none — leave it blank to skip."
        _tty_read _hf_answer "HuggingFace token (input hidden, Enter to skip)" "" 1
        HF_TOKEN_VAL="${_hf_answer}"
        unset _hf_answer
    fi
fi
if [[ "${MODELS_DIR}" != /* ]]; then
    die "model store must be an absolute path (got: ${MODELS_DIR})"
fi
info "Pull destination: ${MODELS_DIR}"

# The Hindsight memory engine (installed much later) builds its own venv and
# pulls litellm, which gates out Python 3.14 via requires-python metadata.
# Resolve the interpreter for that venv NOW — auto-installing a compatible
# 3.11-3.13 when the default python is 3.14+ and the package manager can supply
# one — so the memory-engine step builds cleanly instead of dumping a wall of
# pip resolver errors mid-install. Sets HINDSIGHT_PY / HINDSIGHT_PY_FALLBACK,
# consumed in the Hindsight block below. Never fatal (the metadata-gate bypass
# is the backstop). Skipped in dev mode and when HAL0_SKIP_HINDSIGHT=1.
if [[ "${DEV_MODE}" -eq 0 && "${HAL0_SKIP_HINDSIGHT:-0}" -ne 1 ]]; then
    HAL0_HINDSIGHT_AUTOINSTALL=1 resolve_hindsight_python
fi

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
    # Model-store disk check (#1098): the probe above only measures
    # VAR_DIR's own mount (venv, container images, config, registry). Model
    # weights land at MODELS_DIR, which is frequently a *different* mount
    # (--models-dir=/data/models, a mounted NAS/NVMe) that the VAR_DIR probe
    # never touches — a box could sail through pre-flight with 20 GB free on
    # / while the actual model store is nearly full. Measure it too, but
    # non-fatal (warn only, per the Q4 posture in
    # docs/archive/handoffs/installer-setup-plan-2026-07-05.md): no model has been picked
    # yet at install time, so an undersized store shouldn't hard-block the
    # rest of the platform gate the way a genuinely full root disk should —
    # the pull gate re-validates before any download lands.
    preflight_disk "${HAL0_MODELS_DISK_MIN_GB:-20}" "${MODELS_DIR}" \
        || warn "model store ${MODELS_DIR} is low on free space — model pulls may fail until freed"
    # Container-runtime graphroot disk check: same cross-mount blind spot as
    # the model-store check above, but for image storage. The VAR_DIR probe
    # only measures VAR_DIR's own mount; multi-GB toolbox runners +
    # OpenWebUI + ComfyUI images land in the runtime's graphroot
    # (/var/lib/containers for podman, /var/lib/docker for docker), which is
    # frequently a separate mount when an operator relocates var-dir or
    # deliberately puts container storage on its own volume. Without this, a
    # box passes pre-flight with "20 GB free" and then image pulls fill the
    # container store and fail. Non-fatal (warn only) — same posture as the
    # model-store check.
    if command -v podman >/dev/null 2>&1; then
        HAL0_GRAPHROOT="$(podman info --format '{{.Store.GraphRoot}}' 2>/dev/null || echo /var/lib/containers)"
        preflight_disk "${HAL0_CONTAINER_DISK_MIN_GB:-20}" "${HAL0_GRAPHROOT}" \
            || warn "container image store ${HAL0_GRAPHROOT} is low on free space — image pulls may fail until freed"
    elif command -v docker >/dev/null 2>&1; then
        preflight_disk "${HAL0_CONTAINER_DISK_MIN_GB:-20}" /var/lib/docker \
            || warn "container image store /var/lib/docker is low on free space — image pulls may fail until freed"
    fi
    preflight_ports "${HAL0_PORT}" 3001       || pf_rc=$?
    if (( pf_rc != 0 )); then
        false
    fi
fi

# ── hal0 system user (early, #1098) ─────────────────────────────────────────
# A dedicated `hal0` system user/group runs the non-root hal0 services:
# hal0-agent@<id> (the Hermes runner), hermes-gateway, and the shared
# hindsight-api memory engine. It also owns the HF cache under
# ${VAR_DIR}/.cache so agent-side HuggingFace pulls work without
# escalating. Slot inference itself runs in podman containers supervised
# by hal0-slot@<name>.service — no daemon user needed there.
#
# Created here, immediately after pre-flight and BEFORE any filesystem
# mutation (was previously created much later, after directories/units/
# config were already written — docs/archive/handoffs/installer-setup-plan-2026-07-05.md
# Q1), so the render/video group membership below is settled before any
# later step that depends on group membership or on the user existing
# (the FLM cache / HF cache / STATE.md ownership work further down).
ui_step "System user"

if [[ "${DEV_MODE}" -eq 1 ]]; then
    # Dev installs never create system users or touch systemd.
    info "dev mode — skipping hal0 system user creation"
else
    # hal0 system user/group. System user (UID < 1000), no login shell,
    # home at ${VAR_DIR} so any stray `~`-relative writes from agent
    # processes land somewhere sane. ${VAR_DIR} itself doesn't need to
    # exist yet — useradd only records the home path (no -m flag).
    # Idempotent via `getent`.
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

# O13: the runtime state trees (slots/, registry/, models/) are born root:root
# from the mkdir above, but hal0-api runs User=hal0 and must create
# slots/<id>/state.json, write the registry, and write pulled model files
# there. Left root:root, every slot degrades to `error` on a fresh box, and
# default-store pulls fail with PermissionError (r5-sync-assessment §6.2).
# The `doctor perms --fix` backstop (Service start) also heals these via
# their OwnershipStore rows (src/hal0/install/perms.py), but chown here so
# they're born correct before the daemon's first touch. Prod-only + hal0-gated:
# the service user doesn't exist in dev mode. ${VAR_DIR}/models is the
# OwnershipStore row's FHS-default target — chowned explicitly (not
# ${MODELS_DIR}, which may point off-tree via --models-dir/HAL0_MODELS_DIR;
# an external store's ownership is out of scope here).
if [[ "${DEV_MODE}" -eq 0 ]] && getent passwd hal0 >/dev/null 2>&1; then
    chown hal0:hal0 "${VAR_DIR}/slots" "${VAR_DIR}/registry" "${VAR_DIR}/models" 2>/dev/null || true
    chmod 2775 "${VAR_DIR}/slots" "${VAR_DIR}/registry" "${VAR_DIR}/models" 2>/dev/null || true
fi

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

# Seed hal0.toml's [models].store (single source of truth: pulls, scans,
# and slot container mounts all resolve through it) when the operator
# picked a non-default directory, so the API and CLI both read the same
# value from the canonical config without an extra dashboard step. The
# deprecated pull_root is written too so a downgrade still reads the same
# path. Idempotent: a previous run with the same value is a no-op; a
# different value overwrites (operator just re-ran the installer with a
# new path). Historically only pull_root was seeded and NOT added to the
# scan roots — models pulled to a custom dir were never registered and
# slots died with "gguf ... No such file or directory".
HAL0_TOML="${ETC_DIR}/hal0.toml"
if [[ "${MODELS_DIR}" != "/var/lib/hal0/models" ]]; then
    if ! grep -qE "^\\s*store\\s*=\\s*\"${MODELS_DIR//\//\\/}\"" "${HAL0_TOML}" 2>/dev/null; then
        if [[ -f "${HAL0_TOML}" ]] && grep -q "^\\[models\\]" "${HAL0_TOML}"; then
            # [models] table exists — patch store + pull_root in place (or
            # append under the existing table). Cheap regex pass; no toml
            # parser so we accept the limitation that nested tables under
            # [models.xxx] aren't supported (the schema has none).
            python3 - "${HAL0_TOML}" "${MODELS_DIR}" <<'PYEOF'
import sys, re, pathlib
path = pathlib.Path(sys.argv[1])
new_root = sys.argv[2]
text = path.read_text(encoding="utf-8")
# Replace existing store/pull_root inside [models], else append before the
# next [section]. The section body is "every following line that does not
# START with '['" — NOT `[^\[]*`, which the old patcher used and which
# stopped at the first '[' of a list value like roots = ["/x"], splicing
# the table in half and corrupting the TOML.
m = re.search(r"^\[models\][ \t]*\n(?:(?!\[).*\n?)*", text, flags=re.MULTILINE)
if m:
    block = m.group(0)
    for key in ("store", "pull_root"):
        if re.search(rf"^\s*{key}\s*=", block, flags=re.MULTILINE):
            block = re.sub(rf"^\s*{key}\s*=.*$",
                           f'{key} = "{new_root}"',
                           block, count=1, flags=re.MULTILINE)
        else:
            block = block.rstrip() + f'\n{key} = "{new_root}"\n'
    if not block.endswith("\n"):
        block += "\n"
    text = text[:m.start()] + block + text[m.end():]
else:
    text = text.rstrip() + f'\n\n[models]\nstore = "{new_root}"\npull_root = "{new_root}"\n'
path.write_text(text, encoding="utf-8")
PYEOF
        else
            mkdir -p "${ETC_DIR}"
            printf '\n[models]\nstore = "%s"\npull_root = "%s"\n' "${MODELS_DIR}" "${MODELS_DIR}" >> "${HAL0_TOML}"
        fi
        info "wrote [models].store (+pull_root) → ${HAL0_TOML}"
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
    # Same-version --source git re-run: pip sees the version satisfied and
    # SKIPS, leaving OLD code in the venv (halo143 finding, 2026-07-19). The
    # refresh must gate on tree contents, not the version string — force the
    # hal0 code reinstall; deps were just resolved by the line above so
    # --no-deps keeps this fast and offline-safe.
    ui_spinner_run "Refreshing hal0 code in venv" \
        "${PIP}" install --force-reinstall --no-deps "${REPO_ROOT}"
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

ui_step "Node.js toolchain"

UI_DIR="${REPO_ROOT}/ui"
UI_DIST="${UI_DIR}/dist"
_ui_has_build_inputs=0
[[ -f "${UI_DIR}/package.json" ]] && _ui_has_build_inputs=1
_ui_prebuilt_release=0
if [[ "${_ui_has_build_inputs}" -eq 0 && -s "${UI_DIST}/index.html" ]]; then
    _ui_prebuilt_release=1
fi

# Node/npm is a dependency only when this tree contains the dashboard npm
# project. Signed release trees intentionally ship a verified ui/dist without
# ui/package.json; provisioning Node there cannot rebuild anything and would be
# misleading work. Source/git installs retain the existing best-effort setup.
if [[ "${_ui_has_build_inputs}" -eq 0 ]]; then
    info "dashboard npm project not shipped — Node.js toolchain not required"
elif [[ "${DEV_MODE}" -eq 1 ]]; then
    info "dev mode — skipping Node.js auto-provisioning (install manually if exercising the dashboard build)"
elif HAL0_NODE_AUTOINSTALL=1 resolve_node; then
    info "node: $(node -v 2>/dev/null || echo present) (>= ${NODE_MIN_MAJOR} LTS)"
else
    warn "could not provision Node.js ${NODE_MIN_MAJOR}+ LTS — dashboard UI build will be skipped until Node is installed"
    warn "  install manually: https://nodejs.org/en/download (or your distro's nodejs/NodeSource package), then re-run install.sh"
fi

ui_step "Dashboard UI"

# Staleness gate for source trees (same class as the venv same-version trap):
# "dist exists" is NOT "dist is current". Stamp a local build with the ui/
# tree hash and skip only on an exact match; no git / no stamp → rebuild.
_ui_tree_hash=""
if [[ "${_ui_has_build_inputs}" -eq 1 ]] && command -v git >/dev/null 2>&1; then
    _ui_tree_hash="$(git -C "${REPO_ROOT}" rev-parse HEAD:ui 2>/dev/null || true)"
fi
_ui_dist_current=0
if [[ -f "${UI_DIST}/index.html" && -n "${_ui_tree_hash}" \
      && -f "${UI_DIST}/.hal0-build-stamp" \
      && "$(cat "${UI_DIST}/.hal0-build-stamp" 2>/dev/null)" == "${_ui_tree_hash}" ]]; then
    _ui_dist_current=1
fi

# A distribution-only tree has no source freshness signal or npm project by
# design. Its prebuilt bundle is part of the already-verified release artifact,
# so reuse a non-empty index explicitly and never attempt an impossible rebuild.
if [[ "${_ui_prebuilt_release}" -eq 1 ]]; then
    info "using release's prebuilt ui/dist — npm rebuild not required"
elif [[ "${_ui_has_build_inputs}" -eq 0 ]]; then
    warn "no valid prebuilt ui/dist/index.html; dashboard cannot rebuild without ui/package.json"
    warn "dashboard at :${HAL0_PORT}/ will return 404 until a complete release tree is installed"
elif [[ "${_ui_dist_current}" -eq 1 ]]; then
    info "ui/dist already built for this ui/ tree (${_ui_tree_hash:0:12}) — left alone"
elif command -v npm >/dev/null 2>&1; then
    # ui/package.json pins vite ^6.0.3 + @tailwindcss/vite ^4.2.2, both of
    # which need a modern Node; `command -v npm` alone doesn't catch a Node
    # older than that (Debian 11 / older-Ubuntu apt nodejs, a stale nvm
    # default) — it fails deep inside esbuild/oxide with a cryptic version
    # error instead of a clear message. Gate on the major version and take
    # the same soft-skip path as the npm-absent branch below rather than
    # attempting a build that's certain to fail.
    _ui_node_ver="" _ui_node_major=0
    if command -v node >/dev/null 2>&1; then
        _ui_node_ver="$(node -v 2>/dev/null || true)"
        [[ "${_ui_node_ver}" =~ ^v([0-9]+) ]] && _ui_node_major="${BASH_REMATCH[1]}"
    fi
    if (( _ui_node_major < 20 )); then
        warn "node ${_ui_node_ver:-not found} is too old to build the dashboard (need Node >=20 LTS) — skipping"
        warn "  install Node 20 LTS, then: cd ${UI_DIR} && npm install && npm run build"
    else
        # Two phases — install can dominate first-boot time, build is
        # steady. Wrap each so the user sees what npm is doing instead of
        # staring at a blank line for several minutes.
        #
        # Non-fatal: a registry flake, an OOM'd `vite build` on a small
        # LXC, or a peer-dep error here used to trip the ERR trap and abort
        # the WHOLE install — after the venv, hal0 wheel, config, and
        # (partially) systemd units were already written — even though the
        # API itself doesn't need the built UI (`_mount_dashboard`
        # degrades to "no dashboard" when dist is absent). Degrade to the
        # same soft warning as the npm-absent branch below instead.
        if ui_spinner_run "Installing dashboard npm packages" \
                bash -c "cd '${UI_DIR}' && npm install --no-audit --no-fund" \
            && ui_spinner_run "Building dashboard (npm run build)" \
                bash -c "cd '${UI_DIR}' && npm run build"; then
            [[ -n "${_ui_tree_hash}" ]] && printf '%s\n' "${_ui_tree_hash}" > "${UI_DIST}/.hal0-build-stamp"
            info "wrote ${UI_DIST}"
        else
            warn "dashboard build failed — the API still serves; the UI at :${HAL0_PORT}/ will 404 until you build it"
            warn "  scroll up for the real npm error; retry later: cd ${UI_DIR} && npm install && npm run build"
        fi
    fi
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
# Network coherence (WS-C): derive HAL0_BIND_HOST + HAL0_HOSTNAME +
# HAL0_ALLOWED_ORIGINS from ONE bind choice so the hal0-api unit, `hal0
# serve`, mDNS, and the chat-proxy WS origin gate all agree. The unit's
# ExecStart no longer passes --host — it reads HAL0_BIND_HOST from this
# file, the same var `hal0 serve` honours (main.py). HAL0_ALLOWED_ORIGINS
# is seeded with the hostname + every LAN IP + localhost on the API port
# so whatever URL /api/config/urls advertises passes the WS gate (no 4403
# mismatch). All derivation lives in src/hal0/install/network.py (single
# source), invoked via the just-installed venv; a failure degrades to a
# bare bind var rather than aborting the install.
NETWORK_ENV_LINES="$(
    HAL0_BIND_HOST="${API_BIND_HOST}" \
    HAL0_PORT="${HAL0_PORT}" \
    HAL0_LAN_IPS="$(hostname -I 2>/dev/null || true)" \
    "${VENV_DIR}/bin/python" -c 'from hal0.install.network import main; raise SystemExit(main())' 2>/dev/null
)" || NETWORK_ENV_LINES="HAL0_BIND_HOST=${API_BIND_HOST}"
if [[ -z "${NETWORK_ENV_LINES}" ]]; then
    NETWORK_ENV_LINES="HAL0_BIND_HOST=${API_BIND_HOST}"
fi

# The network block below is rewritten on EVERY run (not just when api.env is
# first created) — it used to be write-once (guarded by `[[ ! -f api.env ]]`),
# but the hal0-api unit's ExecStart was later changed to read HAL0_BIND_HOST
# from this file instead of a --host flag, and that unit IS rewritten every
# run. A box installed before that change (~v0.9.3) that later re-runs
# install.sh to repair/upgrade would never gain HAL0_BIND_HOST at all — the
# unit falls back to the CLI's 127.0.0.1 default, silently killing LAN/
# dashboard access with no error anywhere. Delimited markers let us refresh
# just this block on re-runs while leaving hand-edited lines elsewhere in the
# file (HF_TOKEN, toolbox image overrides, etc.) untouched.
NETWORK_BEGIN_MARKER="# BEGIN hal0-network"
NETWORK_END_MARKER="# END hal0-network"
NETWORK_BLOCK="$(cat <<NETBLOCK
${NETWORK_BEGIN_MARKER}
# Network shape — derived from one bind choice (WS-C). HAL0_BIND_HOST is
# read by BOTH this file's consumer (\`hal0 serve\`) and the hal0-api unit;
# HAL0_HOSTNAME feeds mDNS; HAL0_ALLOWED_ORIGINS gates the chat-proxy WS
# upgrade. This block is rewritten on every install.sh run — it's the only
# way an existing install picks up a changed bind host / LAN IP set. Hand
# edits inside the markers will be overwritten on the next run; edit
# elsewhere in this file (or hal0.toml) to persist a change.
${NETWORK_ENV_LINES}
${NETWORK_END_MARKER}
NETBLOCK
)"

if [[ ! -f "${API_ENV}" ]]; then
    cat > "${API_ENV}" <<EOF
HAL0_PORT=${HAL0_PORT}
HAL0_LOG_LEVEL=info
HAL0_UI_DIST=${HAL0_UI_DIST_VAL}
${NETWORK_BLOCK}
# Memory subsystem (Hindsight engine + /mcp/memory + the Agent → Memory tab)
# is ENABLED by default via [memory].enabled=true in hal0.toml — no env var
# needed here (HAL0_MEMORY_ENABLED was removed; use 'hal0 memory enable' /
# 'hal0 memory disable' to toggle it, or hand-edit hal0.toml). Needs the
# shared hindsight-api daemon (installer/systemd/hindsight-api.service). If
# the daemon is unreachable, hal0 degrades to the in-memory pgvector
# provider (ADR-0023; the cognee engine was removed).
# HF_TOKEN — HuggingFace token for gated / large model pulls. Easiest path:
# set it in the dashboard (Settings -> Secrets -> HuggingFace token) for a live,
# no-restart update. If HF_TOKEN/HUGGING_FACE_HUB_TOKEN was present in the
# installer's own environment it was already gathered and persisted to a
# root-only secrets/ EnvironmentFile below. Uncommenting below also works and
# is no longer unsafe (api.env is 0600 since #1466), but the dashboard path
# applies the token live with no restart; prefer it.
# \`systemctl restart hal0-api\` either way.
# HF_TOKEN=
# HAL0_TOOLBOX_IMAGE_VULKAN / HAL0_TOOLBOX_IMAGE_ROCM — optional overrides for
# the per-backend container image refs used by providers/llama_server.py.
# Unset = use the image pinned in the provider at release time.
EOF
    # api.env is the EnvironmentFile that carries provider tokens, operator
    # secrets and (after a rotation) HAL0_ADMIN_KEY/HAL0_CLIENT_KEY. Owner-only
    # from the first byte — it was 0644 here and on the refresh branch below,
    # so every install and every re-run published live tokens to any local
    # account (#1466). Must match hal0.config.paths.API_ENV_MODE, which the
    # dashboard writer, the key rotation and the perms engine all read.
    chmod 0600 "${API_ENV}"
    info "wrote ${API_ENV} (0600)"
else
    # Idempotent refresh (see comment above NETWORK_BLOCK): strip any
    # existing marker-delimited block and append a fresh one, atomically.
    # Everything outside the markers survives untouched.
    API_ENV_TMP="$(mktemp "${API_ENV}.XXXXXX")"
    awk -v begin="${NETWORK_BEGIN_MARKER}" -v end="${NETWORK_END_MARKER}" '
        $0 == begin { skip = 1; next }
        $0 == end   { skip = 0; next }
        !skip       { print }
    ' "${API_ENV}" > "${API_ENV_TMP}"
    printf '%s\n' "${NETWORK_BLOCK}" >> "${API_ENV_TMP}"
    # 0600 on the tmp file so the mode lands with the rename. #1375 made this
    # refresh run on EVERY re-run over an existing api.env, so the old
    # `chmod 0644` here re-published every secret in the file on any upgrade
    # or repair — it undid the dashboard writer and the key rotation both.
    chmod 0600 "${API_ENV_TMP}"
    mv -f "${API_ENV_TMP}" "${API_ENV}"
    info "refreshed network vars in ${API_ENV} (0600)"
fi

# ── HF_TOKEN validate + persist (WS-D, #1106) ───────────────────────────────
# HF_TOKEN_VAL was GATHERED in the "Operator input" block near the top: the
# env (HF_TOKEN, falling back to HUGGING_FACE_HUB_TOKEN — the same precedence
# the in-process apply path and the /api/install/* + /api/models pull routes
# use, #1094) pre-fills a TTY prompt, and a headless
# `HF_TOKEN=hf_xxx sudo -E bash install.sh` run supplies it directly. Empty is
# a clean, silent skip — public model pulls need no token.
#
# Only validation + persistence happen here, because this is the first point
# where the venv's `hf` console script exists to validate against.
SECRETS_DIR="${VAR_DIR}/secrets"
HF_SECRETS_ENV="${SECRETS_DIR}/hal0-api.env"
if [[ -n "${HF_TOKEN_VAL}" ]]; then
    # Optional `hf whoami` validation — warns on a bad/expired token but
    # NEVER hard-fails the install (acceptance criterion). Guarded on the
    # venv's `hf` console script (shipped by the huggingface-hub dependency,
    # already installed by the "Python environment" step above) existing.
    HF_CLI="${VENV_DIR}/bin/hf"
    if [[ -x "${HF_CLI}" ]]; then
        if HF_TOKEN="${HF_TOKEN_VAL}" "${HF_CLI}" auth whoami >/dev/null 2>&1; then
            info "HuggingFace token validated (hf auth whoami)"
        else
            warn "hf auth whoami could not validate the HuggingFace token — continuing anyway (gated/large model pulls may fail until it's fixed)"
        fi
    fi

    # Persist: root:root 0600, under secrets/ — NOT api.env (0644). Re-running
    # install.sh with HF_TOKEN set rewrites this file (an explicit env var is
    # an explicit rotate signal); with no token set, any existing file here is
    # left untouched — never deleted just because the env var was omitted on
    # a later run.
    mkdir -p "${SECRETS_DIR}"
    HF_SECRETS_TMP="$(mktemp "${HF_SECRETS_ENV}.XXXXXX")"
    cat > "${HF_SECRETS_TMP}" <<EOF
# HuggingFace token for gated / large model pulls — gathered at install time
# from HF_TOKEN / HUGGING_FACE_HUB_TOKEN. Root-only (0600); loaded by
# hal0-api.service as an EnvironmentFile (see the "Systemd units" step).
# Rotate by re-running install.sh with a new HF_TOKEN in env, or:
#   sudo install -m 0600 -o root -g root /dev/stdin ${HF_SECRETS_ENV} <<<"HF_TOKEN=..."
#   sudo systemctl restart hal0-api
HF_TOKEN=${HF_TOKEN_VAL}
EOF
    chown root:root "${HF_SECRETS_TMP}" 2>/dev/null || true
    chmod 0600 "${HF_SECRETS_TMP}"
    mv -f "${HF_SECRETS_TMP}" "${HF_SECRETS_ENV}"
    info "wrote ${HF_SECRETS_ENV} (0600 root:root — not ${API_ENV})"
else
    info "no HuggingFace token supplied — skipping (open-model pulls need none)"
    info "  add one later in the dashboard's Settings -> Secrets tab, or re-run install.sh with HF_TOKEN=hf_… in the environment"
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
# OpenWebUI prewire env. Rendered via the just-installed venv so the
# defaults live in exactly one place (src/hal0/openwebui/env_writer.py).
# In dev mode we point HAL0_HOME at the prefix so the file lands under
# the dev tree alongside the rest of the config.
HAL0_HOME_FOR_OWUI=""
if [[ "${DEV_MODE}" -eq 1 ]]; then
    HAL0_HOME_FOR_OWUI="${PREFIX}"
fi
# Two knobs reach the prewire (#1515). Both were previously unreachable: the
# only documented path was a `overrides=` parameter no caller ever passed, and
# the HAL0_AUTH_ENABLED flag threaded here was dead — env_writer stopped
# reading it in the auth-removal sweep, so the comment promising it "flips
# OpenWebUI prewire defaults to single-sign-on" had been false for releases.
#
#   HAL0_BIND_HOST                  the box's one bind choice (same value that
#                                   seeds api.env above), rendered into
#                                   HAL0_OWUI_BIND_HOST for the unit's
#                                   `podman run -p`. The chat UI now follows
#                                   the operator off the LAN instead of
#                                   publishing on 0.0.0.0 regardless.
#   HAL0_OWUI_TRUSTED_EMAIL_HEADER  pass-through from the installer's own env
#                                   for operators fronting hal0 with a proxy
#                                   that injects a trusted email header.
#
# This call is also no longer a clobber (#1514): main() merges, so an operator
# who hand-edited openwebui.env keeps every value across repair and upgrade
# runs while still receiving newly shipped defaults.
if HAL0_HOME="${HAL0_HOME_FOR_OWUI}" \
    HAL0_BIND_HOST="${API_BIND_HOST}" \
    HAL0_OWUI_TRUSTED_EMAIL_HEADER="${HAL0_OWUI_TRUSTED_EMAIL_HEADER:-}" \
    "${VENV_DIR}/bin/python" -c \
    'from hal0.openwebui.env_writer import main; main()'; then
    info "wrote ${ETC_DIR}/openwebui.env (bind ${API_BIND_HOST}:3001)"
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
# hindsight-api ordering (#1613): a cold engine start outlasts hal0-api's
# boot probe, degrading memory to the pgvector fallback. After= narrows the
# boot-time window (the runtime self-heal re-probe covers the rest); both
# directives are no-ops on a box without the engine unit.
After=network-online.target hindsight-api.service
Wants=network-online.target hindsight-api.service

[Service]
Type=simple
User=hal0
Group=hal0
# hal0-api writes /etc/hal0/* + /var/lib/hal0/* directly — those trees are
# hal0:hal0 2775/setgid (src/hal0/install/perms.py, P3-perms). Privileged IO
# (systemd unit writes, daemon-reload, slot start/stop/restart) routes through
# \`sudo -n /usr/lib/hal0/bin/hal0-systemctl\` — the one narrow seam (see the
# hal0-systemctl wrapper install below). UMask is the default 0022: no
# group-writable kludge needed, the setgid dirs already cover group access.
WorkingDirectory=${API_WORKDIR}
EnvironmentFile=${API_ENV}
# Shared Hermes interpreter policy, persisted by \`hal0 agent install hermes\`.
# Optional so core hal0 services remain installable when Hermes is skipped/degraded.
EnvironmentFile=-${ETC_DIR}/hermes-python.env
# Optional (leading \`-\`): the HF_TOKEN secrets file (WS-D, #1106) — absent
# on a fresh box with no token gathered at install time, and a missing
# EnvironmentFile= target must not block the unit from starting.
EnvironmentFile=-${HF_SECRETS_ENV}
# No --host here (WS-C): the bind host comes from HAL0_BIND_HOST in the
# EnvironmentFile above — the SAME var \`hal0 serve\` reads — so the unit
# and the CLI can never disagree on the bind address.
ExecStart=${HAL0_BIN} serve --port \${HAL0_PORT}
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hal0-api

[Install]
WantedBy=multi-user.target
EOF
info "wrote ${API_UNIT}"

# P3-perms: hal0-api now ships User=hal0 directly (no drop-in needed — the
# unit above IS the hardened posture). Clean up the OLD drop-in dir a
# pre-P3-perms hardened-perms attempt may have left behind, so upgrading such
# a box doesn't carry stale (and now meaningless) unit fragments.
API_DROPIN_DST_DIR="${UNIT_DIR}/hal0-api.service.d"
rm -f "${API_DROPIN_DST_DIR}/20-run-as-hal0.conf" 2>/dev/null || true
rmdir "${API_DROPIN_DST_DIR}" 2>/dev/null || true

# hal0.target (r5-sync-assessment §6.1, launch-blocker #1): every rendered
# per-slot Quadlet declares `[Install] WantedBy=hal0.target`
# (src/hal0/providers/container.py) but nothing ever shipped the target
# itself — slots silently stayed down after every reboot. Ship + enable it
# unconditionally (System mode only; harmless idempotent re-write on
# upgrade) so multi-user.target pulls it in, which in turn starts every
# enabled hal0-slot@ unit.
TARGET_UNIT_SRC="${REPO_ROOT}/installer/systemd/hal0.target"
TARGET_UNIT_DST="${UNIT_DIR}/hal0.target"
if [[ -f "${TARGET_UNIT_SRC}" ]]; then
    cp "${TARGET_UNIT_SRC}" "${TARGET_UNIT_DST}"
    info "wrote ${TARGET_UNIT_DST}"
else
    warn "${TARGET_UNIT_SRC} not found — hal0.target not installed; slots will not autostart after reboot"
fi

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

# podman<->Docker FORWARD reconciliation unit. Only meaningful when Docker is
# co-installed with podman: Docker flips the iptables FORWARD policy to DROP and
# clobbers the implicit ACCEPT podman relies on, which makes podman-published
# ports (OpenWebUI :3001) unreachable off-host. Laid down unconditionally;
# enabled below only when docker is present. The unit self-guards (no-op without
# iptables/podman0/Docker), so shipping it on a docker-free box is harmless.
PODMAN_FWD_UNIT_SRC="${REPO_ROOT}/packaging/systemd/hal0-podman-forward.service"
PODMAN_FWD_UNIT_DST="${UNIT_DIR}/hal0-podman-forward.service"
if [[ -f "${PODMAN_FWD_UNIT_SRC}" ]]; then
    cp "${PODMAN_FWD_UNIT_SRC}" "${PODMAN_FWD_UNIT_DST}"
    info "wrote ${PODMAN_FWD_UNIT_DST}"
fi

# Legacy slot-unit cleanup (R3 quadlet migration). Pre-quadlet installs
# shipped a static hal0-slot@.service template (+ per-slot drop-ins). Static
# units in /etc/systemd/system SHADOW podman's generator units, so a leftover
# template breaks every quadlet slot with "Failed to load environment files"
# (halo150 Phase-2 finding: the generator accepted hal0-slot@qtest.container
# and produced the service, but the docker-era template won the name).
# Convergent: remove if present, no-op otherwise.
LEGACY_SLOT_UNIT="${UNIT_DIR}/hal0-slot@.service"
if [[ -f "${LEGACY_SLOT_UNIT}" ]]; then
    rm -f "${LEGACY_SLOT_UNIT}"
    info "removed legacy static ${LEGACY_SLOT_UNIT} (shadowed quadlet generator units)"
fi
for LEGACY_SLOT_DROPIN in "${UNIT_DIR}"/hal0-slot@*.service.d; do
    if [[ -d "${LEGACY_SLOT_DROPIN}" ]]; then
        rm -rf "${LEGACY_SLOT_DROPIN}"
        info "removed legacy slot drop-in ${LEGACY_SLOT_DROPIN}"
    fi
done

# Stale hal0-agent@ drop-in cleanup (RATIFIED 2026-07-18, halo150 O3). systemd
# merges EVERY *.conf under hal0-agent@<id>.service.d/, so a drop-in an OLD
# installer left behind still applies after the current template overwrites
# override.conf. A stale `ConfigurationDirectory=` fragment brick-loops the unit
# with status=241/CONFIGURATION_DIRECTORY (the current template ships no such
# directive). Convergent: remove non-shipped fragments carrying that directive,
# report each, no-op on a clean box. `override.conf` (the shipped drop-in) is
# rewritten from the template below and never removed here.
for AGENT_DROPIN_DIR in "${UNIT_DIR}"/hal0-agent@*.service.d; do
    [[ -d "${AGENT_DROPIN_DIR}" ]] || continue
    for AGENT_DROPIN in "${AGENT_DROPIN_DIR}"/*.conf; do
        [[ -f "${AGENT_DROPIN}" ]] || continue
        [[ "$(basename "${AGENT_DROPIN}")" == "override.conf" ]] && continue
        if grep -q "ConfigurationDirectory=" "${AGENT_DROPIN}" 2>/dev/null; then
            rm -f "${AGENT_DROPIN}"
            info "removed stale agent drop-in ${AGENT_DROPIN} (241/CONFIGURATION_DIRECTORY class)"
        fi
    done
done

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

    # run-as-hal0 guard: the hermes wrapper sources this at the absolute path
    # ${LIB_DIR}/guards/run-as-hal0.sh and re-execs as the hal0 service user
    # when launched as root, preventing the root-clobber regression (#843).
    GUARD_SRC="${REPO_ROOT}/installer/lib/run-as-hal0.sh"
    if [[ -f "${GUARD_SRC}" ]]; then
        install -d "${LIB_DIR}/guards"
        install -m 0755 "${GUARD_SRC}" "${LIB_DIR}/guards/run-as-hal0.sh"
        info "wrote ${LIB_DIR}/guards/run-as-hal0.sh"
    else
        warn "${GUARD_SRC} not found — run-as-hal0 guard not installed"
    fi

    # Slot privilege seam removed: hal0-api runs as root and writes per-slot
    # units / runs systemctl directly (the ContainerProvider's former euid
    # routing through hal0-slotctl is gone). Remove any stale helper + sudoers
    # grant left by an older hardened-perms install.
    rm -f "${LIB_DIR}/bin/hal0-slotctl" 2>/dev/null || true
    if [[ "${DEV_MODE}" -eq 0 ]]; then
        rm -f /etc/sudoers.d/hal0-slotctl 2>/dev/null || true
    fi

    # Privileged seam #2 (D hardened-perms): hal0-agentenv writes the per-agent
    # .env files that the hardened model pins root:root — the secrets vault
    # (/var/lib/hal0/secrets/agents/<agent>.env) and the driver env
    # (/etc/hal0/agents/<agent>.env). When hal0-api runs unprivileged it cannot
    # write those root-owned dirs, so the hermes provisioner delegates the two
    # writes here. Inert under the default root install (the provisioner branches
    # on euid). Lands at the absolute /usr/lib/hal0/bin path the provider's
    # _HAL0_AGENTENV default hardcodes (dev mode shadows it under PREFIX).
    AGENTENV_SRC="${REPO_ROOT}/installer/wrappers/hal0-agentenv"
    if [[ -f "${AGENTENV_SRC}" ]]; then
        install -d "${LIB_DIR}/bin"
        install -m 0755 "${AGENTENV_SRC}" "${LIB_DIR}/bin/hal0-agentenv"
        info "wrote ${LIB_DIR}/bin/hal0-agentenv"
    else
        warn "${AGENTENV_SRC} not found — agent-env seam helper not installed"
    fi

    # sudoers grant for the agent-env seam. Real installs only; visudo-validate
    # before activating so a malformed drop-in can never wedge sudo for the box.
    if [[ "${DEV_MODE}" -eq 0 ]]; then
        AGENTENV_SUDOERS_SRC="${REPO_ROOT}/packaging/sudoers/hal0-agentenv"
        AGENTENV_SUDOERS_DST="/etc/sudoers.d/hal0-agentenv"
        if [[ -f "${AGENTENV_SUDOERS_SRC}" ]]; then
            if visudo -cf "${AGENTENV_SUDOERS_SRC}" >/dev/null 2>&1; then
                install -m 0440 "${AGENTENV_SUDOERS_SRC}" "${AGENTENV_SUDOERS_DST}"
                info "wrote ${AGENTENV_SUDOERS_DST}"
            else
                warn "${AGENTENV_SUDOERS_SRC} failed visudo check — agent-env sudoers grant not installed"
            fi
        else
            warn "${AGENTENV_SUDOERS_SRC} not found — agent-env sudoers grant not installed"
        fi
    fi

    # Privileged seam #3 (D hardened-perms): hal0-benchctl runs the GPU benchmark
    # harness. Benchmarking is a rootful container op (needs /dev/kfd + the images
    # in root's podman store), but the agent runs unprivileged, so it delegates the
    # run/aggregate ops here. The seam validates every argument (model path under
    # the model dir, backend + llama-bench flag whitelist) and is the entire
    # privileged surface — no shell, no arbitrary args. Lands at the absolute
    # /usr/lib/hal0/bin path the seam's callers hardcode.
    BENCHCTL_SRC="${REPO_ROOT}/installer/wrappers/hal0-benchctl"
    if [[ -f "${BENCHCTL_SRC}" ]]; then
        install -d "${LIB_DIR}/bin"
        install -m 0755 "${BENCHCTL_SRC}" "${LIB_DIR}/bin/hal0-benchctl"
        info "wrote ${LIB_DIR}/bin/hal0-benchctl"
    else
        warn "${BENCHCTL_SRC} not found — benchmark seam helper not installed"
    fi

    # GPU benchmark harness driven by the seam. Root-owned and off any shared
    # mount so the unprivileged agent cannot tamper with a script that runs as
    # root. Results live under VAR_DIR (agent-readable); the seam chowns them back
    # to hal0 after each run.
    BENCH_SRC="${REPO_ROOT}/installer/bench"
    if [[ -d "${BENCH_SRC}" ]]; then
        install -d "${LIB_DIR}/bench"
        install -m 0644 "${BENCH_SRC}/config.sh"                "${LIB_DIR}/bench/config.sh"
        install -m 0755 "${BENCH_SRC}/run_benchmarks.sh"        "${LIB_DIR}/bench/run_benchmarks.sh"
        install -m 0755 "${BENCH_SRC}/generate_results_json.py" "${LIB_DIR}/bench/generate_results_json.py"
        # Profile-matrix orchestrator (seam-driven Tier A) + server-level A/B
        # harness (Tier B: MTP/spec, cache-reuse, embed/rerank — hits hal0-api
        # + slot ports as the hal0 user, no sudo needed).
        install -m 0755 "${BENCH_SRC}/profile-matrix.sh"        "${LIB_DIR}/bench/profile-matrix.sh"
        install -m 0755 "${BENCH_SRC}/server_ab.py"             "${LIB_DIR}/bench/server_ab.py"
        install -m 0644 "${BENCH_SRC}/README.md"                "${LIB_DIR}/bench/README.md"
        install -d "${VAR_DIR}/benchmarks" "${VAR_DIR}/benchmarks/runs" "${VAR_DIR}/benchmarks/logs" "${VAR_DIR}/benchmarks/server-ab"
        # P3-perms: benchmarks/ (+ runs/, logs/, server-ab/) is now a declared
        # OwnershipStore row (hal0:hal0 2775) — the `doctor perms --fix`
        # backstop before "Service start" applies it; no explicit chown needed.
        info "wrote ${LIB_DIR}/bench + ${VAR_DIR}/benchmarks"

        # hal0.bench v2 (design 2026-07-05): suite seeds + politeness window are
        # OPERATOR-OWNED under etc — install only if absent (never clobber
        # operator edits; the shipped copies in installer/bench/ are the seeds).
        install -d "${ETC_DIR}/bench/suites"
        for toml in "${BENCH_SRC}/suites"/*.toml; do
            [[ -f "${toml}" ]] || continue
            dst="${ETC_DIR}/bench/suites/$(basename "${toml}")"
            [[ -f "${dst}" ]] || install -m 0644 "${toml}" "${dst}"
        done
        if [[ -f "${BENCH_SRC}/window.toml" && ! -f "${ETC_DIR}/bench/window.toml" ]]; then
            install -m 0644 "${BENCH_SRC}/window.toml" "${ETC_DIR}/bench/window.toml"
        fi
        # Result store (append-only records.jsonl + derived bench.db + artifacts).
        # Prefix-relative under --dev so a dev install never touches the host
        # (matches every other path in this block; HAL0_BENCH_STATE points the
        # engine at it).
        if [[ "${DEV_MODE}" -eq 1 ]]; then
            BENCH_STATE_DIR="${PREFIX}/var/lib/hal0-bench"
        else
            BENCH_STATE_DIR="/var/lib/hal0-bench"
        fi
        install -d "${BENCH_STATE_DIR}" "${BENCH_STATE_DIR}/artifacts"
        chown -R hal0:hal0 "${BENCH_STATE_DIR}" 2>/dev/null || true
        # Units: weekly scheduled session (timer→oneshot) + the run-queue worker
        # (long-running, inert until Started from the dashboard). The shipped
        # units hardcode the default FHS venv and the engine defaults its
        # state root to /var/lib/hal0-bench; on a non-default prefix
        # (HAL0_PREFIX override or --dev), rewrite ExecStart and inject
        # Environment=HAL0_BENCH_STATE so the units actually run the prefix's
        # binary against the prefix's state dir. Rewritten in python (not
        # sed) so a prefix containing sed metacharacters can't corrupt the
        # substitution.
        for _bench_unit in hal0-bench.service hal0-bench.timer hal0-bench-worker.service; do
            install -m 0644 "${REPO_ROOT}/installer/systemd/${_bench_unit}" "${UNIT_DIR}/${_bench_unit}"
        done
        if [[ "${VENV_DIR}" != "/usr/lib/hal0/venv" || "${BENCH_STATE_DIR}" != "/var/lib/hal0-bench" ]]; then
            "${PY}" - "${UNIT_DIR}" "${VENV_DIR}" "${BENCH_STATE_DIR}" <<'PYEOF'
import sys
from pathlib import Path

unit_dir, venv, state = sys.argv[1], sys.argv[2], sys.argv[3]
for name in ("hal0-bench.service", "hal0-bench.timer", "hal0-bench-worker.service"):
    p = Path(unit_dir) / name
    text = p.read_text(encoding="utf-8").replace("/usr/lib/hal0/venv", venv)
    if name.endswith(".service") and state != "/var/lib/hal0-bench":
        text = text.replace(
            "[Service]", f"[Service]\nEnvironment=HAL0_BENCH_STATE={state}", 1
        )
    p.write_text(text, encoding="utf-8")
PYEOF
        fi
        if [[ "${DEV_MODE}" -eq 0 ]]; then
            systemctl daemon-reload 2>/dev/null || true
            systemctl enable --now hal0-bench-worker.service >/dev/null 2>&1 || true
            systemctl enable --now hal0-bench.timer >/dev/null 2>&1 || true
        fi
        info "wrote ${ETC_DIR}/bench + ${BENCH_STATE_DIR} + bench units"
    else
        warn "${BENCH_SRC} not found — benchmark harness not installed"
    fi

    # sudoers grant for the benchmark seam. Real installs only; visudo-validate
    # before activating so a malformed drop-in can never wedge sudo for the box.
    if [[ "${DEV_MODE}" -eq 0 ]]; then
        BENCHCTL_SUDOERS_SRC="${REPO_ROOT}/packaging/sudoers/hal0-benchctl"
        BENCHCTL_SUDOERS_DST="/etc/sudoers.d/hal0-benchctl"
        if [[ -f "${BENCHCTL_SUDOERS_SRC}" ]]; then
            if visudo -cf "${BENCHCTL_SUDOERS_SRC}" >/dev/null 2>&1; then
                install -m 0440 "${BENCHCTL_SUDOERS_SRC}" "${BENCHCTL_SUDOERS_DST}"
                info "wrote ${BENCHCTL_SUDOERS_DST}"
            else
                warn "${BENCHCTL_SUDOERS_SRC} failed visudo check — benchmark sudoers grant not installed"
            fi
        else
            warn "${BENCHCTL_SUDOERS_SRC} not found — benchmark sudoers grant not installed"
        fi
    fi

    # Privileged seam #4 (P3-perms): hal0-systemctl covers the genuinely-root
    # ops the now-unprivileged hal0-api (User=hal0 above) still needs — writing
    # per-slot systemd units, daemon-reload, and start/stop/restart of a slot
    # unit (+ restarting hal0-api itself on self-update). Narrow + validated
    # (no shell, no wildcards, literal slot-id regex) — see the wrapper source.
    #
    # #1465: this seam is LOAD-BEARING — without it every slot start, unit write
    # and daemon-reload fails, and (pre-fix) a warn here still produced a green
    # success box and a green `hal0 doctor`. A real install now dies instead of
    # shipping a box that cannot run a single slot. Dev installs keep warning:
    # they run non-root, have no sudoers to write, and the seam is a passthrough.
    SYSTEMCTL_SRC="${REPO_ROOT}/installer/wrappers/hal0-systemctl"
    if [[ -f "${SYSTEMCTL_SRC}" ]]; then
        install -d "${LIB_DIR}/bin"
        install -m 0755 "${SYSTEMCTL_SRC}" "${LIB_DIR}/bin/hal0-systemctl"
        info "wrote ${LIB_DIR}/bin/hal0-systemctl"
    elif [[ "${DEV_MODE}" -eq 0 ]]; then
        die "${SYSTEMCTL_SRC} not found — the slot-lifecycle seam cannot be installed; hal0 would be unable to start any slot"
    else
        warn "${SYSTEMCTL_SRC} not found — systemctl seam helper not installed"
    fi

    # sudoers grant for the systemctl seam. Real installs only; visudo-validate
    # before activating so a malformed drop-in can never wedge sudo for the box.
    if [[ "${DEV_MODE}" -eq 0 ]]; then
        command -v visudo >/dev/null 2>&1 \
            || die "visudo not found — hal0 requires the 'sudo' package (every privileged op routes through a sudo seam); install it and re-run"
        SYSTEMCTL_SUDOERS_SRC="${REPO_ROOT}/packaging/sudoers/hal0-systemctl"
        SYSTEMCTL_SUDOERS_DST="/etc/sudoers.d/hal0-systemctl"
        if [[ -f "${SYSTEMCTL_SUDOERS_SRC}" ]]; then
            if visudo -cf "${SYSTEMCTL_SUDOERS_SRC}" >/dev/null 2>&1; then
                install -m 0440 "${SYSTEMCTL_SUDOERS_SRC}" "${SYSTEMCTL_SUDOERS_DST}"
                info "wrote ${SYSTEMCTL_SUDOERS_DST}"
            else
                die "${SYSTEMCTL_SUDOERS_SRC} failed visudo check — refusing to continue; without this grant every slot start, unit write and daemon-reload fails"
            fi
        else
            die "${SYSTEMCTL_SUDOERS_SRC} not found — refusing to continue; without this grant every slot start, unit write and daemon-reload fails"
        fi
    fi

    # Privileged seam #6 (#1464): hal0-update covers the genuinely-root
    # self-update ops. /usr/lib/hal0 is root:root 0755 and never
    # service-writable (src/hal0/install/perms.py), so the unprivileged
    # hal0-api cannot stage a release, swap the `current` symlink, or re-pip
    # the venv — self-update was structurally impossible on the shipped
    # posture. Same narrow shape as hal0-systemctl: validated argv, no shell,
    # no wildcards; `stage` verifies the release cosign-side AS ROOT so the
    # grant can never be used to pip-install an unverified tree.
    UPDATE_SRC="${REPO_ROOT}/installer/wrappers/hal0-update"
    if [[ -f "${UPDATE_SRC}" ]]; then
        install -d "${LIB_DIR}/bin"
        install -m 0755 "${UPDATE_SRC}" "${LIB_DIR}/bin/hal0-update"
        info "wrote ${LIB_DIR}/bin/hal0-update"
    elif [[ "${DEV_MODE}" -eq 0 ]]; then
        die "${UPDATE_SRC} not found — the self-update seam cannot be installed; this box could never take an update"
    else
        warn "${UPDATE_SRC} not found — self-update seam helper not installed"
    fi

    if [[ "${DEV_MODE}" -eq 0 ]]; then
        UPDATE_SUDOERS_SRC="${REPO_ROOT}/packaging/sudoers/hal0-update"
        UPDATE_SUDOERS_DST="/etc/sudoers.d/hal0-update"
        if [[ -f "${UPDATE_SUDOERS_SRC}" ]]; then
            if visudo -cf "${UPDATE_SUDOERS_SRC}" >/dev/null 2>&1; then
                install -m 0440 "${UPDATE_SUDOERS_SRC}" "${UPDATE_SUDOERS_DST}"
                info "wrote ${UPDATE_SUDOERS_DST}"
            else
                die "${UPDATE_SUDOERS_SRC} failed visudo check — refusing to continue; without this grant this box could never take an update"
            fi
        else
            die "${UPDATE_SUDOERS_SRC} not found — refusing to continue; without this grant this box could never take an update"
        fi
    fi

    # Privileged seam #5 (O12): hal0-podman-ro covers READ-ONLY podman
    # introspection (image presence today) against ROOT's podman store — the
    # store slots actually populate via Quadlet, NOT hal0-api's own rootless
    # store. Narrow + hardcoded (no shell, no wildcards, no operator-supplied
    # podman flags) — see the wrapper source.
    PODMAN_RO_SRC="${REPO_ROOT}/installer/wrappers/hal0-podman-ro"
    if [[ -f "${PODMAN_RO_SRC}" ]]; then
        install -d "${LIB_DIR}/bin"
        install -m 0755 "${PODMAN_RO_SRC}" "${LIB_DIR}/bin/hal0-podman-ro"
        info "wrote ${LIB_DIR}/bin/hal0-podman-ro"
    else
        warn "${PODMAN_RO_SRC} not found — podman introspection seam helper not installed"
    fi

    # sudoers grant for the podman introspection seam. Real installs only;
    # visudo-validate before activating so a malformed drop-in can never wedge
    # sudo for the box.
    if [[ "${DEV_MODE}" -eq 0 ]]; then
        PODMAN_RO_SUDOERS_SRC="${REPO_ROOT}/packaging/sudoers/hal0-podman-ro"
        PODMAN_RO_SUDOERS_DST="/etc/sudoers.d/hal0-podman-ro"
        if [[ -f "${PODMAN_RO_SUDOERS_SRC}" ]]; then
            if visudo -cf "${PODMAN_RO_SUDOERS_SRC}" >/dev/null 2>&1; then
                install -m 0440 "${PODMAN_RO_SUDOERS_SRC}" "${PODMAN_RO_SUDOERS_DST}"
                info "wrote ${PODMAN_RO_SUDOERS_DST}"
            else
                warn "${PODMAN_RO_SUDOERS_SRC} failed visudo check — podman introspection sudoers grant not installed"
            fi
        else
            warn "${PODMAN_RO_SUDOERS_SRC} not found — podman introspection sudoers grant not installed"
        fi
    fi
else
    warn "${AGENT_UNIT_SRC} not found — hal0-agent@ template not installed"
fi

if [[ "${DEV_MODE}" -eq 0 ]]; then
    systemctl daemon-reload
    info "systemctl daemon-reload"

    # #1465: post-install assertion. Everything above installs the seams
    # best-effort *per file*; this proves the result end-to-end — wrapper
    # present + root:root 0755, sudoers drop-in present + root 0440, and
    # `sudo -n <seam> <probe>` actually working AS the hal0 user. Without it a
    # box whose grant silently failed to install still printed a success box
    # and still reported green from every `hal0 doctor` surface, while every
    # slot start and every update failed undiagnosably.
    preflight_seams "${LIB_DIR}/bin" /etc/sudoers.d \
        || die "privileged seam verification failed (see above) — fix the grants and re-run 'sudo bash install.sh'"
fi

# AppArmor preflight for podman on unconfined LXC (RATIFIED 2026-07-18,
# halo150 R4). A privileged LXC with `apparmor.profile: unconfined` cannot load
# podman's default AppArmor profile, so `podman run` dies with exit 243
# ("install profile containers-default apparmor") and NO slot ever starts. The
# convergent fix writes `[containers] apparmor_profile="unconfined"` to
# /etc/containers/containers.conf and retries — detected from the podman SMOKE
# FAILURE (not OS/LXC sniffing), idempotent, unit-tested against recorded fakes
# (see src/hal0/agents/containers_apparmor.py). Runs the shared, tested Python
# module via the FHS venv so bash and Python never diverge.
if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 ]] && command -v podman >/dev/null 2>&1 \
    && [[ -x "${VENV_DIR}/bin/python" ]]; then
    if "${VENV_DIR}/bin/python" -m hal0.agents.containers_apparmor; then
        :
    else
        warn "podman apparmor preflight could not resolve the profile-load failure — slots may not start; see the detail above"
    fi
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

ui_step "Container slot seeds"

# ── Container slot seeds (A10) ────────────────────────────────────────────
# Pre-populate /etc/hal0/slots/{flm,tts,rerank,utility,img,agent,brain,…}.toml
# if absent (the loop below is the single source of truth). Idempotent: never
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
#
# ORDERING IS LOAD-BEARING (v1.0). This loop MUST run BEFORE the first-run
# scaffold pass below. It used to run after it, and the two then fought:
# the scaffold wrote a generic model-less `agent`/`brain` toml (with a
# derived profile like plain "vulkan"), and this loop's `[[ -f ]]` guard
# then said "exists — left alone" and silently discarded the CURATED seeds.
# Net effect on every fresh box: agent.toml never got `profile =
# "chadrock-moe"` and brain.toml never got `profile = "brain"` or its
# `[model].default`. Seeding first inverts it correctly with no special
# case — `build_auto_selections` already skips any slot whose config exists
# (`existing_slots`, setup_command.py), so the scaffold pass sees the
# curated files and leaves them alone, while still scaffolding the slots
# that have NO static seed (e.g. `vision`).
# slot_name_exists — "does a slot with this NAME already exist, in ANY keying
# layout?". The obvious `[[ -f "${ETC_DIR}/slots/<name>.toml" ]]` test is only
# correct on a NAME-keyed box. After `hal0 slot migrate-id-keying` the same slot
# lives at `<id>.toml` carrying `name = "<name>"`, so the filename test misses it
# and the seed loop below re-seeds all ten curated names as name-keyed
# DUPLICATES of slots that already exist (#1421/#1422). That chains: duplicate
# `GET /api/slots` rows, and then `migrate_slot_id_keying` — which walks
# `sorted(glob("*.toml"))`, skips `<id>.toml` as already-migrated, and later
# reaches the stale `<name>.toml` — rewrites the LIVE `<id>.toml` from the seed
# content. A configured slot silently reverts to a seed.
#
# Cheap, layout-agnostic answer: filename first, then the `name =` field of
# every slot TOML in the directory.
slot_name_exists() {
    local want="$1" dir="${ETC_DIR}/slots" f
    [[ -f "${dir}/${want}.toml" ]] && return 0
    [[ -d "${dir}" ]] || return 1
    for f in "${dir}"/*.toml; do
        [[ -f "${f}" ]] || continue
        if grep -qE "^[[:space:]]*name[[:space:]]*=[[:space:]]*[\"']${want}[\"'][[:space:]]*\$" "${f}"; then
            return 0
        fi
    done
    return 1
}

SEEDED_NEW_SLOTS=()
SEEDED_EXISTING_SLOTS=0
for seed_slot in flm tts rerank utility img agent brain qwen3tts coder embed; do
    SLOT_TOML="${ETC_DIR}/slots/${seed_slot}.toml"
    SLOT_SRC="${REPO_ROOT}/installer/etc-hal0/slots/${seed_slot}.toml"
    if slot_name_exists "${seed_slot}"; then
        info "${seed_slot} slot: already present — left alone"
        SEEDED_EXISTING_SLOTS=$((SEEDED_EXISTING_SLOTS + 1))
    else
        [[ -f "${SLOT_SRC}" ]] \
            || die "installer bundle incomplete: ${SLOT_SRC} missing (installer/etc-hal0/ should ship with every release tree)"
        mkdir -p "${ETC_DIR}/slots"
        cp "${SLOT_SRC}" "${SLOT_TOML}"
        chmod 0644 "${SLOT_TOML}"
        info "seeded ${seed_slot} slot → ${SLOT_TOML}"
        SEEDED_NEW_SLOTS+=("${seed_slot}")
    fi
done

# EXPECTED, NOT A BUG: the loop above closes gaps, so upgrading a box that
# predates a curated slot ADDS it (most often `utility`, added after the
# original seed set). A parallel boot-time closer does the same thing —
# hal0.install.static_seeds.seed_static_slots, wired into hal0-api startup — so
# the slot also appears after a plain service restart with no installer run.
# Called out explicitly here so an upgrade test does not misreport a new tile on
# the dashboard as a regression.
if (( SEEDED_EXISTING_SLOTS > 0 )) && (( ${#SEEDED_NEW_SLOTS[@]} > 0 )); then
    info "note: ${#SEEDED_NEW_SLOTS[@]} curated slot(s) added to this existing install: ${SEEDED_NEW_SLOTS[*]}"
    info "      that is intentional gap-closing (this release ships slots your install predates),"
    info "      not a stray slot — each stays inert until you bind a model to it."
fi

ui_step "Hardware probe"

if [[ "${HAL0_SKIP_SETUP:-0}" == "1" || "${HAL0_NO_PROBE:-0}" == "1" ]]; then
    info "Skipping first-run seeding (HAL0_SKIP_SETUP/HAL0_NO_PROBE set)."
else
    info "First-run seeding (sentinel + wiring + the capability slots the curated seeds above don't cover; no model picks, no downloads)"
    # `hal0 setup` is an INTERNAL entry point (hidden from `hal0 --help`) that
    # this installer drives — there is no user-facing first-run wizard in v1.0.
    #
    # --auto: non-interactive first-run seeding. It scaffolds the capability +
    # NPU slot STRUCTURE (chat/embed/rerank/stt/tts/vision, device+profile+port)
    # with NO model picks — every slot's model is left unset for the operator to
    # choose later in the dashboard. Pick-free: slots yes, models no. It skips
    # every slot the curated seed loop above already wrote (see the ordering
    # note there). --no-pull keeps the path download-free regardless (redundant
    # with modelless scaffolds, but belt-and-suspenders). --no-extensions:
    # OpenWebUI + Hermes are installed by the dedicated stages below, not here.
    # (Pass --no-slots to seed truly zero slots instead.)
    # Build argv as an array so --storage-dir and its value stay TWO separate
    # tokens. The old `${MODELS_DIR:+--storage-dir "${MODELS_DIR}"}` collapsed
    # into a single arg ("--storage-dir /mnt/ai-models") that typer rejected
    # with "No such option", silently skipping --auto seeding on --models-dir
    # installs.
    _setup_args=(--auto --no-pull --no-extensions)
    [[ -n "${MODELS_DIR}" ]] && _setup_args+=(--storage-dir "${MODELS_DIR}")
    "${HAL0_BIN}" setup "${_setup_args[@]}" \
        || warn "first-run seeding failed — slots can still be created from the dashboard; see the output above"
fi

# ── hal0 brain model (v1.0) ─────────────────────────────────────────────────
# The brain is the platform steward: the dashboard's sidebar chat targets the
# virtual model `hal0/brain`, so unlike every other slot it must WORK out of
# the box rather than ship as a grey model-less tile. Pull its weights here.
#
# Fail-soft by construction (ruling: never hard-fail an install over an
# optional model pull) — no network, no disk, an unsupported device or a
# missing venv all end in a warning and a model-less brain slot, exactly like
# the absent-HF_TOKEN posture above. The heredoc mirrors the three updater
# migrations below: bare invocation, `|| warn`, never `set -e`-fatal.
#
# HAL0_SKIP_BRAIN_MODEL=1 skips it entirely; HAL0_BRAIN_MODEL=<curated-id>
# forces a specific quant instead of the hardware-derived pick.
#
# The AGENT anchor offer below shares this step (no extra ui_step banner, so
# UI_STEP_TOTAL is unchanged) because it is the second half of one story: the
# brain pull makes steward CHAT work; the agent pull is what makes steward
# TOOL CALLS work.
ui_step "Steward + agent models"

if [[ "${DEV_MODE}" -eq 1 ]]; then
    info "dev mode — skipping the brain model pull (no system writes)"
elif [[ "${HAL0_SKIP_BRAIN_MODEL:-0}" == "1" || "${HAL0_SKIP_SETUP:-0}" == "1" ]]; then
    info "Skipping the brain model pull (HAL0_SKIP_BRAIN_MODEL/HAL0_SKIP_SETUP set)."
else
    HF_TOKEN="${HF_TOKEN_VAL}" HAL0_BRAIN_MODEL="${HAL0_BRAIN_MODEL:-}" \
        "${VENV_DIR}/bin/python" -m hal0.install.brain_model \
        || warn "brain model pull did not complete — the brain slot stays model-less and the install continues; retry later with 'hal0 model pull <id>' or from the dashboard"
fi

# ── agent anchor model — OPT-IN, size disclosed, default SKIP ───────────────
# Measured on a GPU box: the 1B brain model does NOT emit tool calls. Given the
# `hal0-function-xml` contract and an explicit tool request it reasons and
# returns an empty `content` with no function block — exactly what
# installer/etc-hal0/slots/brain.toml predicts. Tool turns are therefore routed
# to `[brain_chat] tool_model`, whose code default is "hal0/agent"
# (src/hal0/config/schema.py). But agent.toml ships model-less on purpose
# (#1369: model-presence is the activation signal), so on a fresh box brain
# tool calling is DEAD until someone binds an agent model.
#
# That is what this offer fixes — and the shape of the offer is the whole
# point. Unlike the brain pull (unconditional, ~1-2 GB) the anchor is 15-31 GB,
# so:
#
#   * it is ASKED, never assumed, and the exact GB figure is printed first;
#   * the default answer is NO (bare Enter skips);
#   * it sits behind `_interactive`, so `curl | bash`, a tty-less ssh install
#     and HAL0_NONINTERACTIVE=1 all skip WITHOUT BLOCKING — a prompt that can
#     hang CI is a broken install;
#   * declining, skipping, or failing all leave the slot model-less, print
#     what that costs, and let the install SUCCEED (ruling 7).
#
# The size string comes from `python -m hal0.install.agent_model --plan`, which
# reads it off the curated row. Bash never hardcodes a GB number, so the figure
# the operator consents to is the figure that gets downloaded.
#
# HAL0_PULL_AGENT_MODEL=1 opts in unattended (automation that wants the bytes);
# =0 forces the skip even on a terminal. HAL0_AGENT_MODEL=<curated-id> forces a
# specific rung of the ladder instead of the hardware-derived pick.
_agent_plan=""
_agent_id=""
_agent_desc=""
_agent_answer=""
_agent_wanted=0

if [[ "${DEV_MODE}" -eq 1 ]]; then
    :  # dev mode writes nothing to the system store — no offer, no notice
elif [[ "${HAL0_SKIP_SETUP:-0}" == "1" || "${HAL0_PULL_AGENT_MODEL:-}" == "0" ]]; then
    info "Skipping the agent model (HAL0_SKIP_SETUP/HAL0_PULL_AGENT_MODEL=0 set)."
    info "brain chat works, but TOOL CALLS need an agent model bound to the agent slot ([brain_chat] tool_model, default hal0/agent)."
else
    # `|| true`: a plan that cannot be computed is simply no offer. Never fatal.
    _agent_plan="$(HAL0_AGENT_MODEL="${HAL0_AGENT_MODEL:-}" \
        "${VENV_DIR}/bin/python" -m hal0.install.agent_model --plan 2>/dev/null || true)"
    _agent_plan="${_agent_plan%%$'\n'*}"
    if [[ -z "${_agent_plan}" || "${_agent_plan}" != *$'\t'* ]]; then
        info "No agent model fits this box (a ROCm/Vulkan device and ~15 GB of pool are the floor) — not offering one."
    else
        _agent_id="${_agent_plan%%$'\t'*}"
        _agent_desc="${_agent_plan#*$'\t'}"
        if [[ "${HAL0_PULL_AGENT_MODEL:-}" == "1" ]]; then
            info "Agent model opt-in via HAL0_PULL_AGENT_MODEL=1: ${_agent_desc}"
            _agent_wanted=1
        elif _interactive; then
            printf '\n' >/dev/tty 2>/dev/null || true
            info "The brain steward can chat now, but it cannot CALL TOOLS on its own — it routes tool turns to the agent slot, which has no model yet."
            info "Optional download: ${_agent_desc}"
            _tty_read _agent_answer "Download the agent model now? [y/N]" "n"
            case "${_agent_answer}" in
                [Yy]|[Yy][Ee][Ss]) _agent_wanted=1 ;;
                *) _agent_wanted=0 ;;
            esac
            unset _agent_answer
        else
            info "Non-interactive install — not pulling the ${_agent_desc%% —*} agent model (set HAL0_PULL_AGENT_MODEL=1 to opt in)."
        fi
    fi

    if [[ "${_agent_wanted}" -eq 1 ]]; then
        if HF_TOKEN="${HF_TOKEN_VAL}" HAL0_AGENT_MODEL="${_agent_id}" \
            "${VENV_DIR}/bin/python" -m hal0.install.agent_model; then
            :
        else
            warn "agent model pull did not complete — the agent slot stays model-less and the install continues"
            _agent_wanted=0
        fi
    fi
    if [[ "${_agent_wanted}" -ne 1 ]]; then
        info "brain chat works, but TOOL CALLS need an agent model: the steward routes tool turns to [brain_chat] tool_model (default hal0/agent) and the agent slot has no model bound."
        info "Assign one from the dashboard, or 'hal0 model pull <id> && hal0 slot load agent', whenever you like."
    fi
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

# Pinned FLM .deb — bump in lockstep with ADR-0009 and the toolbox image
# tag in manifest.json (both 0.9.44 as of v0.9.6). NOTE (since 0.9.43):
# FLM rejects a CLI flag passed twice, so FLMProvider.container_spec must
# never repeat a mode flag (--asr/--embed) the model already implies.
FLM_DEB_VERSION="0.9.44"
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
        ubuntu24.04) echo "ce51f73da998e7b3b3ec21851a4450087f05d9a446108cc2d18a5355872c2800" ;;
        ubuntu25.10) echo "b58ca7875d5e462ff53d84145987f81a4855a577b72efedde389768fcd93ad15" ;;
        ubuntu26.04) echo "5eeb7fffc62f44260d1d562749bde56e1e7ade7940c3764765cf8933bac67ac3" ;;
        debian13)    echo "2a56c2d4447642968ce1698252d983e2ffc4f2169071307a295cf681b54cf9af" ;;
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

    # The FastFlowLM .deb hard-depends on libxrt2 + libxrt-npu2 (AMDXDNA NPU
    # runtime), which ship from the lemonade-team PPA — NOT Ubuntu's own repos.
    # Without the PPA a fresh box hits `fastflowlm : Depends: libxrt-npu2 but it
    # is not installable` and skips FLM entirely. Add the PPA (Ubuntu only — it
    # builds for `noble`; the FLM container bundles its own runtime so this is
    # only for the host `flm validate` probe) before the libxrt install.
    # Best-effort + idempotent: a failure here just disables host NPU probing;
    # GPU/CPU hal0 is unaffected.
    if [[ "$(distro_id)" == "ubuntu" ]] \
        && ! apt-cache policy libxrt-npu2 2>/dev/null | grep -q lemonade-team; then
        if command -v add-apt-repository >/dev/null 2>&1 \
            || apt-get install -y software-properties-common >/dev/null 2>&1; then
            if add-apt-repository -y ppa:lemonade-team/stable >/dev/null 2>&1; then
                apt-get update -qq >/dev/null 2>&1 || true
                info "added lemonade-team PPA (libxrt-npu2 / AMDXDNA NPU runtime)"
            else
                warn "could not add lemonade-team PPA — host NPU libs (libxrt-npu2) may be unavailable"
            fi
        fi
    fi

    # libxrt-npu2 — best-effort. Resolved from the lemonade-team PPA added above
    # (or a pre-existing vendor repo on upgraded boxes). The FLM container image
    # bundles its own runtime, so a miss here only disables the HOST `flm
    # validate` probe, not the npu slot itself.
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
                warn "FLM_DEB_SHA256 is the placeholder — pin the real checksum in _flm_sha_for_suffix before release"
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

# ── config-convergence migrations ─────────────────────────────────────────────
# Every step below is an upgrade migration over /etc/hal0. None of them is
# required for hal0 to start, and every one of them can meet a config file that
# an older hal0 wrote and today's validators reject.
#
# This file runs under `set -euo pipefail` (:17), so a bare heredoc here aborts
# the WHOLE install on any raise — before start_or_restart_api is ever reached.
# ensure_seed_profiles raises ConfigParseError on a profiles.toml that fails the
# current extra="forbid" ProfileConfig, which is exactly the pre-v1.0 shape this
# block exists to fix: the migration that repairs an old box was the one most
# likely to brick its upgrade.
#
# Updater.commit() already wraps the identical calls in try/except-and-warn.
# hal0_migration_step gives install.sh the same posture: report the failure,
# keep going, let the service start. `if ! cmd` also suppresses `set -e` for the
# call itself.
hal0_migration_step() {
    # $1 = human label for the warning; python source arrives on stdin.
    local label="$1"
    if "${VENV_DIR}/bin/python" -; then
        return 0
    fi
    warn "${label}: migration step failed — continuing with the install"
    warn "  this is non-fatal; hal0-api still starts. Re-run 'sudo bash install.sh'"
    warn "  after fixing the reported problem, or check 'journalctl -u hal0-api'."
    return 0
}

# ── profile catalog: one-shot v1.0 reset ──────────────────────────────────────
# v1.0 makes profiles tuning-only. A pre-v1.0 /etc/hal0/profiles.toml can hold
# shapes the v1.0 loader no longer accepts, so converging such a box means
# deleting the file — the built-in catalog is virtual (overlaid from code on
# every load), so the reseed is free.
#
# Gated on hal0.toml's [meta] schema_version, so it fires exactly once per box
# and never again. A timestamped backup is written to /var/lib/hal0/backups/
# before anything is removed. The prompt is asked ONLY when operator-authored
# profiles would actually be lost; a fresh install (no profiles.toml) and a
# `curl | bash` run therefore stay fully non-interactive. Set
# HAL0_RESET_PROFILES=1 to pre-approve it in a script. MUST run before the
# schema migration below: the reset's gate IS meta.schema_version, and letting
# the migration stamp it forward first would make the gate unreadable on the
# very box it exists for.
PROFILES_TOML="${ETC_DIR}/profiles.toml"
if [[ "${DEV_MODE}" -eq 1 ]]; then
    info "dev mode — skipping profile catalog reset (no system writes)"
else
    # Export so the heredoc's python child sees it (sudo strips a bare prefix).
    export HAL0_RESET_PROFILES="${HAL0_RESET_PROFILES:-}"
    hal0_migration_step "profile catalog reset" <<'PYEOF'
import os

from hal0.updater.updater import profile_reset_status, reset_profile_catalog

status = profile_reset_status()
approved = None

if not status["due"]:
    print(f"  profile catalog already at the v1.0 shape ({status['reason']})")
elif status["unreadable"]:
    # No prompt: there is no recoverable operator content to weigh, and the file
    # is actively breaking this box. Say so anyway — a silent delete of a file
    # the operator believes holds their work is indistinguishable from a bug.
    print("  /etc/hal0/profiles.toml does NOT parse, so its whole contents — including")
    print("  any profiles you authored — are removed by the v1.0 reset. The original")
    print("  bytes are copied to /var/lib/hal0/backups/ first; that copy is the only")
    print("  way to recover them.")
elif status["needs_consent"]:
    names = ", ".join(status["custom_profiles"])
    if os.environ.get("HAL0_RESET_PROFILES") == "1":
        approved = True
        print(f"  HAL0_RESET_PROFILES=1 — resetting; custom profiles removed: {names}")
    else:
        # stdin is this heredoc, so a prompt has to read the controlling
        # terminal directly (same reason as _confirm_cpu_only above). No tty
        # (curl | bash, cron, CI) means no consent: leave them alone.
        print("  hal0 v1.0 makes profiles tuning-only; /etc/hal0/profiles.toml is reset once.")
        print(f"  These operator-authored profiles would be deleted: {names}")
        # #1411: a pre-existing custom profile whose stored flags include hardware
        # flags (-dev, --threads, -ngl) is rejected by the v1.0 hardware screen on
        # PUT, so it cannot be edited or repaired through the UI. Those profiles
        # are in the list above and are deleted like any other. An operator who
        # has been fighting an un-editable profile must not discover that the
        # upgrade quietly resolved it by destruction.
        print("  This includes profiles the UI currently refuses to save (their stored")
        print("  flags contain hardware flags v1.0 no longer accepts) — they are deleted,")
        print("  not repaired.")
        print("  A timestamped copy is written to /var/lib/hal0/backups/ first; it is the")
        print("  only way to get any of them back.")
        try:
            with open("/dev/tty", "r+") as tty:
                tty.write("Reset the profile catalog now? [Y/n] ")
                tty.flush()
                approved = (tty.readline().strip().lower() or "y") in ("y", "yes")
        except OSError:
            approved = None
            print("  no terminal available — keeping them; re-run with HAL0_RESET_PROFILES=1.")

if status["due"]:
    result = reset_profile_catalog(approved=approved)
    if result["performed"]:
        print(f"  profile catalog reset (backup: {result['backup']})")
    else:
        print(f"  profile catalog NOT reset ({result['outcome']}) — still due on the next run")
PYEOF
fi

# ── post-activation migrations (schema + seed-profile/mtp/image-pin/extra-args) ─
# One shared sequence with Updater.commit()'s self-update path (GH #1475):
# hal0.toml schema migrations, profiles.toml virtual-seed pruning (seeds live
# in code and are overlaid on every load — an older install that materialised
# them into profiles.toml froze stale definitions; this prunes them so the
# code definition wins again), stale mtp=true slot-override clearing (a
# force-on pointing at a model with no MTP heads crashes llama-server at load
# once the unit re-renders under post-separation code), stale runner-image
# pin retagging, and defaults.extra_args sanitizing. Every pass after the
# schema migration is independently best-effort — a single pass failing never
# aborts the others. Previously this block ran only the seed-profile and
# stale-MTP passes directly, so a box upgraded by re-running install.sh (the
# documented repair/upgrade path) kept a stale meta.schema_version, stale
# runner-image pins, and unsanitised defaults.extra_args that `hal0 update`
# would have fixed — two boxes on the same version with different on-disk
# state. Operator edits and non-seed profiles are always left untouched.
#
# Ran through hal0_migration_step (not a bare heredoc): the schema migration
# used to be able to abort this whole script under set -euo pipefail before
# start_or_restart_api was ever reached, on exactly the pre-v1.0 boxes this
# repair path exists for. The ceiling mirrors Updater.commit(): re-derive
# whether the profile-catalog reset above is still outstanding (its own gate
# is meta.schema_version, re-read fresh here) and cap the migration target
# below the watermark so an outright decline doesn't silently consume the
# one-shot.
if [[ "${DEV_MODE}" -eq 1 ]]; then
    info "dev mode — skipping post-activation migrations (no system writes)"
else
    info "running post-activation migrations (schema + seed-profile/mtp/image-pin/extra-args)"
    hal0_migration_step "post-activation migrations" <<'PYEOF'
from hal0.config.migrations.v2 import PROFILE_CATALOG_SCHEMA_VERSION
from hal0.updater.updater import run_post_activation_migrations
from hal0.updater.updater import profile_reset_status

status = profile_reset_status()
reset_outstanding = bool(status["due"])
ceiling = PROFILE_CATALOG_SCHEMA_VERSION - 1 if reset_outstanding else None
source, target = run_post_activation_migrations(ceiling=ceiling)
if target != source:
    print(f"  hal0.toml schema migrated {source} -> {target}")
else:
    print(f"  hal0.toml schema already at {target}")
print("  seed-profile / stale-MTP / runner-image / extra-args cleanup passes ran (see log for any per-item detail)")
PYEOF
fi

# ── SlotConfig.enabled sweep (#1369) ──────────────────────────────────────────
# `enabled` is gone — a non-empty [model].default IS the activation signal — and
# `enabled = false` alongside a bound model would silently switch a deliberately
# disabled slot back ON. The identical idempotent sweep runs at hal0-api boot,
# but only into `journalctl -u hal0-api`, which nobody watching an install ever
# sees. Run it here so the result lands in the install transcript.
if [[ "${DEV_MODE}" -eq 1 ]]; then
    info "dev mode — skipping slot 'enabled' sweep (no system writes)"
else
    hal0_migration_step "slot 'enabled' key sweep" <<'PYEOF'
from hal0.updater.updater import sweep_slot_enabled_keys
swept = sweep_slot_enabled_keys()
if swept:
    print(f"  swept the removed 'enabled' key off {len(swept)} slot(s): {', '.join(swept)}")
    print("    (a slot that was 'enabled = false' WITH a bound model is now model-less,")
    print("     which is how 'off' is expressed in v1.0 — re-bind a model to turn it on)")
else:
    print("  no slot carries the removed 'enabled' key")
PYEOF
fi

# Stale former-default runner-image pins and hal0-managed flags in model
# defaults.extra_args (parity with Updater.commit() steps 7d/7e) are handled
# by the consolidated "post-activation migrations" call above — it runs
# retag_stale_slot_images and sanitize_model_extra_args itself, so a second
# call here would just repeat the same passes.

# ── hal0-api service start / restart ──────────────────────────────────────────
# Deliberately NOT the same policy as the slot units below. A slot is a running
# inference workload: bouncing it costs a model reload, so new argv is allowed
# to apply on its next start. hal0-api is the thing we just replaced, and it
# imports its code once at startup — leaving it running means the install did
# not take effect at all.
#
# ``systemctl enable --now`` alone is NOT enough for that: ``--now`` starts a
# stopped unit but is a no-op on an active one. So an upgrade over a live box
# swapped the venv and ``current`` symlink, printed its success banner, exited
# 0, and left the OLD process serving. Observed on halo 2026-07-27: ``hal0
# --version`` said 1.0.0a2 while ``/api/health`` said 1.0.0 for 15 minutes,
# until a manual restart. Enable for boot either way; restart only when
# something is already running.
start_or_restart_api() {
    systemctl enable hal0-api >/dev/null 2>&1 || true
    if systemctl is-active --quiet hal0-api 2>/dev/null; then
        info "hal0-api is already running — restarting onto the newly installed code"
        systemctl restart hal0-api
    else
        systemctl enable --now hal0-api
    fi
}

# ── slot-unit re-render ───────────────────────────────────────────────────────
# Slot units bake the launch argv at load time; without this, systemctl
# restarts and reboots keep running PRE-update flags until an operator does a
# hal0-level slot restart. Rewrite existing units through the just-installed
# code + one daemon-reload — running services are NOT bounced; new argv
# applies on each slot's next start.
if [[ "${DEV_MODE}" -eq 1 ]]; then
    info "dev mode — skipping slot-unit re-render (no system writes)"
else
    hal0_migration_step "slot-unit re-render" <<'PYEOF'
from hal0.updater.updater import rerender_slot_units
n = rerender_slot_units()
if n:
    print(f"  re-rendered {n} slot unit(s) through the new code (services not restarted)")
else:
    print("  all slot units already match the new code")
PYEOF
fi

# ── convergence verdict ───────────────────────────────────────────────────────
# Same report `hal0 update` prints, from the same code, so both entry points
# tell an operator the same story about the same box.
#
# The three spec-hw-slot-ownership / spec-flags-ownership folds are deliberately
# NOT run here. `hal0 slot migrate-hw --apply` refuses outright while hal0-api
# or any hal0-slot@* unit is active, because rewriting slot TOMLs under a live
# runtime split-brains it — and on an upgrade the old hal0-api is still serving
# at this point in the script. slot_flags_fold can also legitimately REFUSE a
# model when two slots disagree, which needs a human. So: detect (write-free
# planners), name what is outstanding, print the exact command, and let the
# operator run it in a real window.
if [[ "${DEV_MODE}" -eq 0 ]]; then
    hal0_migration_step "convergence check" <<'PYEOF'
from hal0.updater.updater import convergence_report

report = convergence_report()
pending = report["ownership_migrations"]["pending"]
if report["converged"]:
    print("  on-disk config is fully converged to the v1.0 shape")
else:
    if report["profile_reset"]["due"]:
        print("  ! profile catalog reset is still outstanding "
              "(re-run this installer on a terminal, or with HAL0_RESET_PROFILES=1)")
    if pending:
        print("  ! this box is still on the pre-v1.0 slot/model shape. hal0 works, but the")
        print("    per-slot launch tune, NGL/runner pins and mtp/vision/reasoning settings")
        print("    below are still where the OLD schema put them and are no longer read at")
        print("    launch. Stop hal0 (systemctl stop 'hal0-slot@*' hal0-api), then run:")
        for key in pending:
            entry = report["ownership_migrations"]["detail"][key]
            print(f"      {entry['command']}")
            if entry.get("error"):
                print(f"        needs manual resolution first: {entry['error']}")
            for line in entry["lines"][:5]:
                print(f"        {line}")
            if len(entry["lines"]) > 5:
                print(f"        … and {len(entry['lines']) - 5} more")
        print("    Each is dry-run without --apply, and backs up the slot config + registry")
        print("    DB before any write.")
PYEOF
fi

# ── ComfyUI model share ───────────────────────────────────────────────────────
# The docker comfy-up/down/logs/postinstall.sh scripts have been retired; the
# podman img slot (hal0-slot@img.service) is the sole lifecycle owner of :8188
# (#984, also resolves #874).  We still create the model-share subdirectories
# and seed the optional helper assets (custom nodes, extra_model_paths.yaml)
# because the img slot bind-mounts the same paths.
ui_step "ComfyUI model share"

COMFYUI_CUSTOM_NODES_SRC="${REPO_ROOT}/installer/comfyui/custom_nodes"
COMFYUI_MODELS_ROOT="/mnt/ai-models/comfyui"

if [[ "${DEV_MODE}" -eq 1 ]]; then
    info "dev mode — skipping ComfyUI model-share setup (no system writes)"
elif [[ "${HAL0_SKIP_COMFYUI:-0}" == "1" ]]; then
    info "HAL0_SKIP_COMFYUI=1 — skipping ComfyUI model-share setup"
else
    # Create the model-share subdirs bind-mounted by the img slot container.
    # COMFYUI_MODELS_ROOT is frequently an NFS / shared mount (e.g.
    # /mnt/ai-models forwarded into an LXC) whose squashed or foreign owner
    # DENIES chmod — so `install -d`/`install -m` blow up with "Operation not
    # permitted" and abort the whole install. ComfyUI is optional (the img
    # slot only needs the dirs to EXIST), so use plain mkdir -p (no mode
    # change) and treat perms/copy failures as warnings, never fatal. Operators
    # who don't want ComfyUI at all can set HAL0_SKIP_COMFYUI=1.
    _comfy_ok=1
    for _subdir in models output input user custom_nodes; do
        mkdir -p "${COMFYUI_MODELS_ROOT}/${_subdir}" 2>/dev/null || _comfy_ok=0
    done
    if [[ "${_comfy_ok}" -eq 1 ]]; then
        info "ensured ${COMFYUI_MODELS_ROOT}/{models,output,input,user,custom_nodes}"
    else
        warn "could not create some ${COMFYUI_MODELS_ROOT} subdirs (read-only or NFS-squashed perms?) — ComfyUI img slot may be degraded; set HAL0_SKIP_COMFYUI=1 to silence"
    fi

    if [[ -d "${COMFYUI_CUSTOM_NODES_SRC}" ]]; then
        if cp "${COMFYUI_CUSTOM_NODES_SRC}"/*.py "${COMFYUI_MODELS_ROOT}/custom_nodes/" 2>/dev/null; then
            info "wrote ComfyUI custom nodes → ${COMFYUI_MODELS_ROOT}/custom_nodes/"
        else
            warn "could not write ComfyUI custom nodes to ${COMFYUI_MODELS_ROOT}/custom_nodes/ (perms) — skipped"
        fi
    else
        warn "${COMFYUI_CUSTOM_NODES_SRC} not found — ComfyUI custom nodes not installed"
    fi

    # Place extra_model_paths.yaml if not already present (operator may have a
    # customised copy — never overwrite).
    _EXTRA_PATHS_SRC="${REPO_ROOT}/installer/comfyui/extra_model_paths.yaml"
    _EXTRA_PATHS_DST="${COMFYUI_MODELS_ROOT}/extra_model_paths.yaml"
    if [[ -f "${_EXTRA_PATHS_DST}" ]]; then
        info "${_EXTRA_PATHS_DST} exists — left alone"
    elif [[ -f "${_EXTRA_PATHS_SRC}" ]]; then
        if cp "${_EXTRA_PATHS_SRC}" "${_EXTRA_PATHS_DST}" 2>/dev/null; then
            info "wrote ${_EXTRA_PATHS_DST}"
        else
            warn "could not write ${_EXTRA_PATHS_DST} (perms) — create manually"
        fi
    else
        warn "${_EXTRA_PATHS_SRC} not found — extra_model_paths.yaml not placed (create manually)"
    fi
fi

# ── hal0 system user: device/dir-dependent follow-up (#1098) ───────────────
# The user/group + render/video membership themselves are created MUCH
# earlier now (see "── hal0 system user (early, #1098) ──" right after
# pre-flight, before any filesystem mutation). What's left here needs
# directories that only exist from this point on (VAR_DIR's tree, created
# in "Filesystem layout" above) — the hal0 user's HF cache, the FLM (NPU)
# cache, and the shared STATE.md.
if [[ "${DEV_MODE}" -eq 1 ]]; then
    : # dev mode never created the hal0 user above; nothing to follow up.
else
    # FLM (NPU) model cache. The npu slot bind-mounts this dir into the FLM
    # container, which runs as uid 1000 — NOT the host hal0 uid (a system
    # uid < 1000). If the dir is missing at container start podman fails
    # with exit 125 (statfs ... no such file or directory); if it's owned
    # hal0:hal0 only, the container gets Permission denied creating model
    # subdirs on `flm pull`. uid 1000 + hal0 group + setgid 2775 satisfies
    # both writers (container via owner, host flm probe/pull via group).
    # Honours HAL0_FLM_MODELS_DIR / [models].flm_store relocations; created
    # whenever an XDNA NPU node is present (harmless otherwise).
    if [[ -e /dev/accel/accel0 ]]; then
        FLM_CACHE_DIR="${HAL0_FLM_MODELS_DIR:-${VAR_DIR}/.config/flm/models}"
        mkdir -p "${FLM_CACHE_DIR}"
        chown 1000:hal0 "${FLM_CACHE_DIR}" 2>/dev/null || chown hal0:hal0 "${FLM_CACHE_DIR}" || true
        chmod 2775 "${FLM_CACHE_DIR}" || true
        info "FLM model cache: ${FLM_CACHE_DIR} (container-uid writable, setgid hal0)"
    fi

    # HuggingFace hub cache (#275 bug 4). The hal0 user's HOME is
    # ${VAR_DIR} (per useradd above), so HF's default cache lands at
    # ${VAR_DIR}/.cache/huggingface/hub. Pre-create the leaf dir + give
    # hal0 ownership of the cache tree (NOT the whole VAR_DIR — slot
    # state.json + registry are written by hal0-api, which runs as root)
    # so hal0-user processes (agents) can download HF assets without a
    # PermissionError on first use.
    mkdir -p "${VAR_DIR}/.cache/huggingface/hub"
    chown -R hal0:hal0 "${VAR_DIR}/.cache"

    # Shared STATE.md (#766). The hermes agent runs as hal0 and its
    # render-context (re)writes ${VAR_DIR}/STATE.md — the live snapshot the
    # Claude session-start hook cats — via a tmp+rename that needs *directory*
    # write on ${VAR_DIR}.
    #
    # P3-perms: ${VAR_DIR} itself (owner hal0, setgid 2775) and STATE.md are
    # now declared rows in src/hal0/install/perms.py's OwnershipStore — the
    # explicit chgrp/chmod/chown dance this comment used to describe (kept
    # ${VAR_DIR}'s OWNER at root because hal0-api used to write slot
    # state.json/registry AS ROOT) is redundant now that hal0-api runs
    # User=hal0 (below) and the `doctor perms --fix` backstop (before "Service
    # start") applies the table before the daemon's first start. Just create
    # the file; ownership lands via the table.
    touch "${VAR_DIR}/STATE.md"

    # Seed the agent config + secret dirs (root-owned; the agent driver reads
    # agents/, and systemd reads the secrets/ EnvironmentFile as root). The hermes
    # provisioner also creates these, but seeding them here keeps them present
    # before provisioning.
    #
    # NOTE: the former HAL0_USER!=root "hardened-perms flip" — which chowned the
    # whole /etc/hal0 + /var/lib/hal0 tree to an unprivileged service user so a
    # dropped hal0-api could rewrite config — was removed. hal0-api runs as root,
    # so it writes config/state, applies updates, and restarts services directly.
    mkdir -p "${ETC_DIR}/agents" "${VAR_DIR}/secrets/agents"

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
        # P3-perms: no chown needed — these manifests are READ-ONLY content
        # (the bundle picker only reads them); root:root 0644 is world-readable,
        # same posture as /etc/hal0/agents/ + secrets/ staying root:root
        # elsewhere in the table (read-only surfaces don't need service
        # ownership).
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
    # P3-perms: skills/ is a declared OwnershipStore row (hal0:hal0 2775) —
    # the `doctor perms --fix` backstop before "Service start" applies it.
    info "skill drop-in: ${SKILLS_DROPIN} (drop a folder here to add a skill; editable)"
else
    info "dev mode — skipping system skill install (/usr/share/hal0/skills)"
fi

ui_step "Service start"

# P3-perms migration backstop: hal0-api ships User=hal0 (above), so /etc/hal0
# + /var/lib/hal0 must already be hal0-owned (2775/setgid) BEFORE the unit's
# first start, or the daemon can't read/write its own config on boot. The
# config-seed steps earlier in this script mostly write as root (see the
# per-block P3-perms notes above); this one-shot `doctor perms --fix --force`
# (root-gated, atomic w/ rollback — src/hal0/install/perms.py) reconciles the
# WHOLE declared table against disk right before the daemon needs it, both on
# a fresh install and on an upgrade of a pre-P3-perms box. Non-fatal: don't
# let a hiccup here abort an otherwise-good install — `hal0 doctor perms`
# surfaces any residual drift afterward.
if [[ "${DEV_MODE}" -eq 0 ]]; then
    if "${HAL0_BIN}" doctor perms --fix --force; then
        info "ownership table applied (P3-perms) — /etc/hal0 + /var/lib/hal0 are hal0-owned"
    else
        warn "'${HAL0_BIN} doctor perms --fix' reported drift/errors — re-run 'sudo ${HAL0_BIN} doctor perms --fix' after install"
    fi
fi

if [[ "${DEV_MODE}" -eq 1 || "${NO_START}" -eq 1 ]]; then
    warn "not starting services automatically (dev / --no-start)."
    warn "  start manually: ${HAL0_BIN} serve --host ${API_BIND_HOST} --port ${HAL0_PORT}"
else
    start_or_restart_api
    if wait_active hal0-api 15; then
        info "hal0-api is running"
    else
        warn "hal0-api failed to start; check 'journalctl -u hal0-api -n 40'"
    fi

    # hal0.target (§6.1 above): enable so multi-user.target pulls it in at
    # boot; `--now` also starts it right away, which pulls in whichever
    # hal0-slot@ units are ALREADY enabled (WantedBy=hal0.target) on this
    # box — harmless no-op on a fresh install with no slots yet.
    if [[ -f "${TARGET_UNIT_DST}" ]]; then
        if systemctl enable --now hal0.target; then
            info "hal0.target enabled — slots will autostart after reboot"
        else
            warn "'systemctl enable --now hal0.target' failed; slots will not autostart after reboot — check 'systemctl status hal0.target'"
        fi
    fi

    # ── Memory engine (Hindsight) ─────────────────────────────────────────────
    # Stand up the local hindsight-api daemon (the shared memory brain) and seed
    # the global shared bank + the hermes private bank. The unit ships in
    # installer/systemd/ but was never installed before, so a fresh box had
    # [memory].enabled=true (hal0.toml default) pointing at a dead engine. The daemon
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
            # Interpreter + gate decision were made up front by
            # resolve_hindsight_python (preflight). HINDSIGHT_PY is a 3.11-3.13
            # when one was found/installed; otherwise the default ${PY} with
            # HINDSIGHT_PY_FALLBACK=1 (litellm's requires-python gate must be
            # bypassed). Default to ${PY} if the resolver didn't run.
            hs_py="${HINDSIGHT_PY:-${PY}}"
            hs_fallback="${HINDSIGHT_PY_FALLBACK:-0}"
            "${hs_py}" -m venv "${HS_DIR}/.venv"
            hs_pip="${HS_DIR}/.venv/bin/pip"
            "${hs_pip}" install --upgrade pip wheel -q 2>/dev/null || true

            # On a Python litellm rejects by metadata (3.14+ with no compatible
            # interpreter available) skip the doomed first attempt — it spews a
            # wall of "Could not find a version" resolver output — and bypass the
            # gate directly. litellm runs fine on 3.14 (classifier dropped over a
            # since-resolved fastuuid wheel gap; BerriAI/litellm#26343); the
            # /health poll below is the real gate on whether the engine came up.
            hs_installed=0
            if [[ "${hs_fallback}" -ne 1 ]]; then
                if "${hs_pip}" install "hindsight-api==0.8.4" -q; then
                    hs_installed=1
                else
                    hs_pyver="$("${HS_DIR}/.venv/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo '?')"
                    warn "hindsight-api install failed on Python ${hs_pyver}; retrying past the requires-python gate"
                fi
            fi
            if [[ "${hs_installed}" -ne 1 ]]; then
                [[ "${hs_fallback}" -eq 1 ]] && \
                    info "installing hindsight-api with --ignore-requires-python (no Python 3.11-3.13 available; litellm's 3.14 gate is metadata-only)"
                if "${hs_pip}" install --ignore-requires-python "hindsight-api==0.8.4" -q; then
                    hs_installed=1
                else
                    warn "hindsight-api install failed — memory engine will be unavailable"
                    warn "  install a Python 3.11-3.13 and re-run, or set HAL0_HINDSIGHT_PYTHON=python3.12"
                fi
            fi
        else
            info "hindsight-api venv already present — skipping pip install"
        fi
        # The unit runs as hal0 with HOME=${HS_DIR}; hand it the whole tree.
        chown -R hal0:hal0 "${VAR_DIR}/memory" 2>/dev/null || true
        if [[ -x "${HS_DIR}/.venv/bin/hindsight-api" ]]; then
            install -m644 "${HINDSIGHT_UNIT_SRC}" /etc/systemd/system/hindsight-api.service
            # The unit ships HINDSIGHT_API_LLM_API_KEY=hal0-local-noauth — fine
            # while the box has no keys, but once KB-1 auth is on, every
            # extraction/reflect call to /v1 401s and retains fail silently
            # (#1543's engine-side sibling). Hand the engine a real client-tier
            # key via the unit's EnvironmentFile whenever one exists;
            # `hal0 auth rotate` keeps the file fresh afterward.
            # `|| true` on every grep: on an auth-off box these patterns match
            # nothing and grep's exit 1 would abort the whole install under
            # the script's set -e/pipefail (bit a live fresh-install test).
            hs_key=""
            if [[ -f /etc/hal0/api.env ]]; then
                hs_key="$(grep -oP '(?<=^HAL0_CLIENT_KEY=).*' /etc/hal0/api.env 2>/dev/null | head -1 || true)"
                if [[ -z "${hs_key}" ]]; then
                    hs_key="$(grep -oP '(?<=^HAL0_ADMIN_KEY=).*' /etc/hal0/api.env 2>/dev/null | head -1 || true)"
                fi
            fi
            if [[ -n "${hs_key}" ]]; then
                ( umask 027 && printf 'HINDSIGHT_API_LLM_API_KEY=%s\n' "${hs_key}" \
                    > /etc/hal0/hindsight-llm.env )
                chown root:hal0 /etc/hal0/hindsight-llm.env 2>/dev/null || true
            fi
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
                # `shared` = the global cross-agent brain; `private:hermes`
                # = hermes' private store (the server derives it from the agent-id
                # via PRIVATE_PREFIX="private:", so the seed name must match exactly).
                # Other private/project banks lazy-create.
                _seed_bank() {
                    curl -fsS -m 20 -X POST \
                        "http://127.0.0.1:${HAL0_PORT}/api/memory/banks/$1/import" \
                        -H "Content-Type: application/json" -H "X-hal0-Agent: installer" \
                        -d "$2" >/dev/null 2>&1
                }
                if _seed_bank shared '{"version":"1","bank":{"retain_mission":"Extract technical decisions and rationale, gotchas and fixes, PRs and status changes, conventions, commands, endpoints, flags, incidents and resolutions, and cross-session coordination facts. Ignore routine edits, transient state, secrets, and anything already in git.","enable_observations":true,"disposition_skepticism":4,"disposition_literalism":4,"disposition_empathy":1}}' \
                    && _seed_bank private:hermes '{"version":"1","bank":{"retain_mission":"This agent private working notes, scratch decisions, and per-task state. Never store shared facts here.","disposition_skepticism":4,"disposition_literalism":4,"disposition_empathy":2}}'; then
                    info "seeded memory banks: shared (global) + private:hermes"
                else
                    warn "memory bank seeding incomplete — banks also lazy-create on first write"
                fi
            else
                warn "hindsight-api not healthy yet; check 'journalctl -u hindsight-api -n 40'"
            fi
        fi
    fi

    # Escape hatch: HAL0_SKIP_OPENWEBUI=1 for operators who don't want the
    # bundled chat UI — same "skip now, install later" contract as
    # HAL0_SKIP_HERMES above. `hal0 app install openwebui` (issue #1102 / Q9)
    # runs the identical enable+guard logic below, so skipping here is
    # lossless.
    if [[ -f "${OPENWEBUI_UNIT_DST}" ]]; then
        if [[ "${HAL0_SKIP_OPENWEBUI:-0}" -eq 1 ]]; then
            info "skipping OpenWebUI (HAL0_SKIP_OPENWEBUI=1) — run '${HAL0_BIN} app install openwebui' later"
        # OpenWebUI runs as a podman container (ExecStart=podman run …) — the
        # same runtime as the slots, so the preflight that installed podman
        # already satisfies it. Without a usable runtime the unit would
        # restart-loop with status=203/EXEC, so guard the enable anyway — the
        # dashboard/API are unaffected and the built-in chat works without it.
        elif command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
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

    # podman<->Docker FORWARD reconciliation. Only needed when Docker is
    # co-installed alongside podman: Docker sets iptables FORWARD policy to DROP
    # and rewrites the chain on every start, which drops external traffic to
    # podman-published ports (OpenWebUI :3001) — reachable from the host but not
    # from a reverse proxy / LAN client. The hal0-podman-forward unit re-adds an
    # ACCEPT for the podman0 bridge into DOCKER-USER at boot and on each docker
    # restart. The unit self-guards, so enabling it is safe; we gate on docker
    # actually being present to avoid shipping a firewall unit on podman-only boxes.
    if [[ -f "${PODMAN_FWD_UNIT_DST}" ]] && command -v docker >/dev/null 2>&1; then
        if systemctl enable --now hal0-podman-forward >/dev/null 2>&1; then
            info "hal0-podman-forward enabled (keeps podman ports reachable alongside Docker)"
        else
            warn "hal0-podman-forward did not enable; check 'journalctl -u hal0-podman-forward'"
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
    #
    # §7.4 privilege drop: `agent install hermes` runs here as root, but the CLI
    # itself drops the BOOTSTRAP step to the hal0 user (cli/_provision_hermes) —
    # a root-only prelude installs the /usr/local/bin wrapper + ensures the
    # setgid hal0-owned skeleton, then it re-execs `agent bootstrap hermes` as
    # hal0 so config.yaml / personas / runtime.json / provision.json are all born
    # hal0:hal0. Root:root artifacts (seed TOML, driver env, gateway drop-in) go
    # through the hal0-agentenv / hal0-systemctl sudo seams. The remaining root
    # orchestration here (toolchain prereqs, unit enable, gateway install) stays
    # as root. The setgid /var/lib/hal0 + /etc/hal0 skeleton is also applied by
    # `doctor perms --fix` above, before this block.

    # §7.4 root prelude — /usr/local/bin/hermes (+ the hal0-hermes back-compat
    # symlink) is genuinely root-only install infra. Lay it down HERE, in the
    # installer's root context, BEFORE `agent install hermes` runs, so that once
    # that call drops to the unprivileged hal0 user (§7.4) the provisioner finds
    # the wrapper already present and skips it (hermes_provision._copy_wrapper is
    # euid-aware). Installing it in the prelude keeps the sudo seam from ever
    # needing to widen to /usr/local/bin. Capture safety (mirrors _copy_wrapper):
    # a pre-existing FOREIGN hermes (no HAL0_AGENT_ID marker) is backed up to
    # .pre-hal0 before overwrite. Real installs only — dev installs run non-root
    # and the provisioner's euid branch handles the skip.
    if [[ "${DEV_MODE}" -eq 0 && -f "${AGENT_UNIT_DST}" ]]; then
        HERMES_WRAPPER_SRC="${REPO_ROOT}/installer/wrappers/hermes"
        if [[ -f "${HERMES_WRAPPER_SRC}" ]]; then
            if [[ -e /usr/local/bin/hermes ]] && ! grep -q "HAL0_AGENT_ID" /usr/local/bin/hermes 2>/dev/null; then
                cp -p /usr/local/bin/hermes /usr/local/bin/hermes.pre-hal0 2>/dev/null || true
            fi
            install -m 0755 -o root -g root "${HERMES_WRAPPER_SRC}" /usr/local/bin/hermes
            ln -sfn /usr/local/bin/hermes /usr/local/bin/hal0-hermes
            info "wrote /usr/local/bin/hermes (+ hal0-hermes symlink) — §7.4 root prelude"
        else
            warn "${HERMES_WRAPPER_SRC} not found — hermes wrapper not pre-installed"
        fi
    fi

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
        #
        # `hermes gateway install` on the systemd path PROMPTS interactively
        # ("Start the gateway now…?" / "…on boot?") with no flag to bypass.
        # The installer's contract is non-interactive (see DEBIAN_FRONTEND
        # above), so we feed it </dev/null: a TTY-less read hits EOF and
        # hermes falls back to its built-in defaults (install + enable on
        # boot + start now). Without this, two failure modes appear:
        #   - on a real TTY the install BLOCKS on the prompt;
        #   - under a launcher that *closes* fd 0 (some headless/CI runners),
        #     hermes' input() raises `RuntimeError: lost sys.stdin` — which it
        #     does NOT catch — so the install aborts AFTER printing the prompt
        #     but BEFORE writing the unit file. That is what produced the
        #     "Unit file hermes-gateway.service does not exist" error below.
        # Redirecting from /dev/null turns that crash into a clean EOF.
        GATEWAY_UNIT_DST="${UNIT_DIR}/hermes-gateway.service"
        info "installing system-scope hermes gateway (User=hal0)"
        # m1 fix: this script runs as root, so a bare `env -u HERMES_HOME
        # hermes ...` here resolves `~/.hermes` to /root/.hermes — the exact
        # "split-brain" root-owned tree `hal0 doctor perms` flags as Hermes
        # ownership drift (check_hermes_ownership's stray_home check; see
        # installer/lib/run-as-hal0.sh's docstring, which names this same
        # `hermes gateway install --system` checks os.geteuid() == 0
        # internally and refuses when invoked by a non-root user. The
        # `--run-as-user hal0` flag tells the hermes CLI which runtime
        # user to write into the systemd unit — we do NOT drop to that
        # user here. Run the install command AS ROOT directly (the
        # #843 safeguard for /root/.hermes is satisfied by the
        # $HOME=/var/lib/hal0 override and the fact that 'pip install'
        # was already done as hal0 earlier in the provisioning block).
        env -u HERMES_HOME HOME=/var/lib/hal0 \
            /var/lib/hal0/venvs/hermes/bin/hermes gateway install --system --run-as-user hal0 </dev/null \
            || warn "hermes gateway install failed — Telegram/Discord bridge unavailable; continuing"
        # Only enable/start if hermes actually laid down the unit. If the
        # install genuinely failed the file is absent; `systemctl enable` would
        # otherwise emit a scary "Unit file … does not exist" error and trip
        # the ERR trap. Skip honestly with a warning instead.
        if [[ -f "${GATEWAY_UNIT_DST}" ]]; then
            systemctl daemon-reload
            systemctl enable --now hermes-gateway.service \
                || warn "hermes-gateway enable returned non-zero — continuing (check 'journalctl -u hermes-gateway -n 40')"
            if wait_active hermes-gateway.service 20; then
                info "hermes-gateway is running (Telegram/Discord)"
            else
                warn "hermes-gateway not yet active; check 'journalctl -u hermes-gateway -n 40'"
            fi
        else
            warn "hermes-gateway unit not installed (${GATEWAY_UNIT_DST} missing) — Telegram/Discord bridge unavailable"
            warn "  retry with 'HERMES_HOME= /var/lib/hal0/venvs/hermes/bin/hermes gateway install --system --run-as-user hal0 </dev/null'"
        fi
    elif [[ -f "${AGENT_UNIT_DST}" ]]; then
        # No venv after the provision block: it was skipped (HAL0_SKIP_HERMES=1)
        # or failed. The warnings above already explain; this is the summary line.
        info "hal0-agent@hermes not enabled — provision with '${HAL0_BIN} agent install hermes'"
    fi

fi

# Real LAN IPv4 addresses — filtered to skip container / virtual bridge
# interfaces (podman/cni/docker/veth/flannel/virbr/...). Without this filter,
# `hostname -I` leaks noise like the podman 10.88.0.1 gateway into the "Reach
# hal0 at" list and can even pick it as the primary HOST. Falls back to
# `hostname -I` (then localhost) when iproute2 is unavailable.
lan_ipv4s() {
    if command -v ip >/dev/null 2>&1; then
        ip -o -4 addr show scope global 2>/dev/null | awk '
            $2 ~ /^(lo|podman|cni|docker|veth|br-|flannel|kube|virbr|tailscale)/ { next }
            { sub(/\/.*/, "", $4); print $4 }'
    else
        hostname -I 2>/dev/null | tr ' ' '\n'
    fi
}

HOST="$(lan_ipv4s | head -1)"
[[ -z "${HOST}" ]] && HOST="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[[ -z "${HOST}" ]] && HOST=localhost

# ── Reachability discovery ─────────────────────────────────────────────────
# Build a list of "label\turl" pairs covering every interface the user
# might browse from. Always-tab-separated so the renderer can split.
# Failures are silent: a missing tailscale binary just means no
# Tailscale entry; nothing in this block can fail the installer.
REACH_LINES=()

DASHBOARD_URL="http://${HOST}:${HAL0_PORT}/"
# Real LAN IPv4s (container/bridge interfaces filtered out — see lan_ipv4s).
for ip in $(lan_ipv4s); do
    REACH_LINES+=("LAN"$'\t'"http://${ip}:${HAL0_PORT}/")
done
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
    "$(printf '  %sOpen the dashboard%s   assign models to the slots this install created' "${BOLD}" "${RST}")"
    "$(printf '  %shal0 model pull <id>%s download a model (browse with %shal0 model list%s)' "${BOLD}" "${RST}" "${BOLD}" "${RST}")"
    "$(printf '  %shal0 status%s         system + slot + memory summary' "${BOLD}" "${RST}")"
    "$(printf '  %shal0 slot list%s      inspect configured slots' "${BOLD}" "${RST}")"
    "$(printf '  %shal0 update%s         check for + apply updates' "${BOLD}" "${RST}")"
    ""
    "$(printf '%sDocs %shttps://github.com/Hal0ai/hal0%s  ·  %sLogs %sjournalctl -fu hal0-api%s' "${DIM}" "${BLU}" "${RST}" "${DIM}" "${BLU}" "${RST}")"
)

ui_box "hal0 is ready" "${SUMMARY_LINES[@]}"

# ── No Stage-2 handoff (v1.0) ───────────────────────────────────────────────
# There used to be a "Launch the guided hal0 setup now? [Y/n]" prompt here
# (issue #1112's Stage-2). It is deliberately GONE: install.sh is the single
# user-facing entry point, and every answer that wizard collected is now
# either asked by this script (model store + HF token, in the "Operator input"
# block) or already done by it (slot scaffolding, extensions, NPU probe).
# Offering it again sent operators through a five-question flow that only
# re-derived what had just been derived. What is genuinely left after an
# install is picking models per slot, which is a dashboard job — see the
# "Next steps" box above. Do NOT reintroduce a post-install CLI wizard here.

# Restore the caller's umask (see the save near the top of the file).
umask "${_HAL0_ORIG_UMASK}"
