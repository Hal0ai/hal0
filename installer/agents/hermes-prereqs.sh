#!/usr/bin/env bash
# hal0 — Hermes provisioning prerequisites.
#
# `hal0 agent install hermes` provisions Hermes into a hal0-managed venv
# at /var/lib/hal0/venvs/hermes. That needs an OS toolchain a clean box
# may lack: a python3 that can `python3 -m venv` (Debian/Ubuntu split this
# into the separate python3-venv package — the classic clean-Ubuntu trap),
# python3-pip, and pipx. This script ensures all four are present, using
# lib/distro.sh for cross-distro package naming.
#
# Idempotent: probes first and installs nothing when the toolchain is
# already complete. Cross-distro via the same package-manager detection
# install.sh uses (#764). Exit non-zero (with a copy-pasteable hint) when
# it can't install — the caller surfaces it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/distro.sh
source "${SCRIPT_DIR}/../lib/distro.sh"

info() { printf '[hermes-prereqs] %s\n' "$*"; }
warn() { printf '[hermes-prereqs] WARN: %s\n' "$*" >&2; }
die() { printf '[hermes-prereqs] ERROR: %s\n' "$*" >&2; exit 1; }

# Pick the python interpreter the bootstrap will use. Prefer an explicit
# python3.11+ but fall back to python3 (preflight enforces the >=3.11 floor).
PY="$(command -v python3.12 || command -v python3.11 || command -v python3 || true)"

# ── Probe: is the toolchain already complete? ────────────────────────────────
have_venv() { [ -n "${PY}" ] && "${PY}" -c 'import venv, ensurepip' >/dev/null 2>&1; }
have_pip() { [ -n "${PY}" ] && "${PY}" -m pip --version >/dev/null 2>&1; }
have_pipx() { command -v pipx >/dev/null 2>&1; }

if have_venv && have_pip && have_pipx; then
    info "toolchain already present (python venv + pip + pipx) — nothing to do"
    exit 0
fi

# ── Resolve per-family package names ─────────────────────────────────────────
# Names differ by ecosystem, not distro. venv: bundled everywhere except
# Debian/Ubuntu. pipx: `python-pipx` on Arch, `pipx` elsewhere it's packaged.
family="$(distro_family || true)"
case "${family}" in
    debian) pkgs=(python3 python3-venv python3-pip pipx) ;;
    fedora) pkgs=(python3 python3-pip pipx) ;;
    arch) pkgs=(python python-pip python-pipx) ;;
    suse) pkgs=(python3 python3-pip python3-pipx) ;;
    alpine) pkgs=(python3 py3-pip pipx) ;;
    *)
        die "unrecognised package manager — install Python 3.11+ (with the venv
stdlib module), pip, and pipx manually, then re-run \`hal0 agent install hermes\`."
        ;;
esac

# ── Install ──────────────────────────────────────────────────────────────────
# pkg_install_cmd emits a `sudo …` one-liner. Strip the sudo when we're
# already root (clean containers often lack sudo entirely).
cmd="$(pkg_install_cmd "${pkgs[@]}")" || die "could not build install command for ${family}"
if [ "$(id -u)" -eq 0 ]; then
    cmd="${cmd#sudo }"
fi

# Debian needs an index refresh before a first install on a fresh image.
if [ "${family}" = "debian" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        apt-get update -qq || warn "apt-get update failed — install may still succeed from cache"
    else
        sudo apt-get update -qq || warn "apt-get update failed — install may still succeed from cache"
    fi
fi

info "installing toolchain: ${pkgs[*]}"
info "  ${cmd}"
if ! eval "${cmd}"; then
    die "toolchain install failed. Run it yourself and retry:
  $(pkg_install_cmd "${pkgs[@]}")"
fi

# ── Verify ───────────────────────────────────────────────────────────────────
PY="$(command -v python3.12 || command -v python3.11 || command -v python3 || true)"
have_venv || die "python venv module still missing after install — check ${PY:-python3} and \`$(python_venv_hint)\`"
have_pip || warn "python pip still not importable — bootstrap will bootstrap it via ensurepip"
have_pipx || warn "pipx not on PATH after install — Hermes still installs into the managed venv; pipx is optional tooling"

info "toolchain ready"
exit 0
