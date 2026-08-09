#!/usr/bin/env bash
# hal0 — Hermes provisioning prerequisites.
#
# `hal0 agent install hermes` provisions Hermes into a hal0-managed venv
# at /var/lib/hal0/venvs/hermes. That needs an OS toolchain a clean box
# may lack: a python3 that can `python3 -m venv` (Debian/Ubuntu split this
# into the separate python3-venv package — the classic clean-Ubuntu trap),
# python3-pip, pipx, and git. This script ensures all five are present,
# using lib/distro.sh for cross-distro package naming.
#
# git is required because the provisioner's `pip install
# git+https://github.com/NousResearch/hermes-agent.git@<rev>` shells out to
# `git clone`; a stock minimal distro image doesn't ship it (#1726, #1727
# review). This script is the SHARED choke point for both provisioning
# entry points — install.sh's inline `hal0 agent install hermes` call and
# the standalone/deferred `hal0 agent install hermes` an operator runs
# manually (e.g. after HAL0_SKIP_HERMES=1, or per the remediation hint on
# a failed inline provision) — both go through
# hal0.cli.agent_commands._install_hermes, which shells out to this exact
# script (see installer/install.sh's own preflight_git gate, which is
# fast-path defense-in-depth only; this script is what actually covers
# both call sites).
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

# Hermes is deliberately pinned to Python 3.12. The hal0 runtime has its
# separate Python policy; do not use python3, python3.11, or python3.13 here.
PY="$(command -v python3.12 || command -v python3 || true)"

# ── Probe: is the toolchain already complete? ────────────────────────────────
have_venv() { [ -n "${PY}" ] && "${PY}" -c 'import venv, ensurepip' >/dev/null 2>&1; }
have_pip() { [ -n "${PY}" ] && "${PY}" -m pip --version >/dev/null 2>&1; }
have_pipx() { command -v pipx >/dev/null 2>&1; }
have_uv() { command -v uv >/dev/null 2>&1; }
# `command -v git` only proves a `git` file resolves on PATH, not that it
# runs (a non-executable file — permissions drift, a half-finished package
# install — still resolves). `git --version` is the real functional probe
# (#1727 review).
have_git() { git --version >/dev/null 2>&1; }

# hermes-agent wheels pin `requires-python >=3.11,<3.14`. On a box whose only
# interpreter is 3.14+ (e.g. Ubuntu 26.04, whose apt has no python3.12), pip
# filters out every hermes-agent wheel and the provision fails. The provisioner
# (hermes_provision._provision_python_via_uv) recovers by downloading a managed
# 3.12 with `uv python install` — but only if `uv` is on PATH. So on a
# 3.14-only box, uv is a REQUIRED prerequisite; where a system 3.11-3.13 exists
# it's unnecessary.
have_system_hermes_py() {
    command -v python3.12 >/dev/null 2>&1
}

# Install uv to a PATH location (pipx → /usr/local/bin) so the provisioner's
# managed-interpreter fallback (hermes_provision._provision_python_via_uv) can
# find it via `command -v uv`. Idempotent; requires pipx. Returns non-zero if uv
# still isn't on PATH afterwards.
ensure_uv() {
    have_uv && { info "uv already present ($(command -v uv))"; return 0; }
    have_pipx || { warn "pipx unavailable — cannot install uv for the managed-Python fallback"; return 1; }
    info "installing uv (managed-Python fallback for hermes-agent on 3.14+ boxes) via pipx → /usr/local/bin"
    PIPX_HOME="${PIPX_HOME:-/opt/pipx}" PIPX_BIN_DIR=/usr/local/bin pipx install uv >/dev/null 2>&1 || true
    have_uv
}

# Ensure the hermes-agent interpreter path is satisfiable before declaring the
# toolchain complete: hermes-agent wheels require Python <3.14, so a 3.14-only
# box needs uv on PATH (the provisioner then fetches a managed 3.12/3.13).
ensure_interpreter_path() {
    if have_system_hermes_py; then
        info "system Python 3.12 present — uv fallback not required"
    elif ensure_uv; then
        info "uv ready ($(command -v uv)) — provisioner will fetch a managed Python <3.14"
    else
        die "this box has only Python 3.14+ (hermes-agent wheels require <3.14) and
uv could not be installed for the managed-interpreter fallback. Install uv
(https://docs.astral.sh/uv/) or a python3.12, then re-run \`hal0 agent install hermes\`."
    fi
}

# Toolchain is "complete" only when the interpreter fallback is also satisfiable:
# venv+pip+pipx+git AND (a system Python in range OR uv for the managed fallback).
if have_venv && have_pip && have_pipx && have_git && { have_system_hermes_py || have_uv; }; then
    info "toolchain already present (python venv + pip + pipx + git + interpreter path) — nothing to do"
    exit 0
fi

# ── Resolve per-family package names ─────────────────────────────────────────
# Names differ by ecosystem, not distro. venv: bundled everywhere except
# Debian/Ubuntu. pipx: `python-pipx` on Arch, `pipx` elsewhere it's packaged.
# git: package named `git` on every family below.
family="$(distro_family || true)"
case "${family}" in
    debian) pkgs=(python3 python3-venv python3-pip pipx git) ;;
    fedora) pkgs=(python3 python3-pip pipx git) ;;
    arch) pkgs=(python python-pip python-pipx git) ;;
    suse) pkgs=(python3 python3-pip python3-pipx git) ;;
    alpine) pkgs=(python3 py3-pip pipx git) ;;
    *)
        die "unrecognised package manager — install Python 3.11+ (with the venv
stdlib module), pip, pipx, and git manually, then re-run \`hal0 agent install hermes\`."
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
PY="$(command -v python3.12 || command -v python3 || true)"
have_venv || die "python venv module still missing after install — check ${PY:-python3} and \`$(python_venv_hint)\`"
have_pip || warn "python pip still not importable — bootstrap will bootstrap it via ensurepip"
have_pipx || warn "pipx not on PATH after install — Hermes still installs into the managed venv; pipx is optional tooling"
# git is load-bearing, not optional — `pip install git+...` (the very next
# phase of provisioning) hard-requires it, so a still-missing/unusable git
# here must die loudly with a remediation line rather than warn and let the
# pip clone fail with the original opaque error three steps later (#1726,
# #1727 review).
have_git || die "git still missing or not runnable after install — install it manually
(e.g. \`$(pkg_install_cmd git 2>/dev/null || echo 'install git via your distro package manager')\`),
then re-run \`hal0 agent install hermes\`."

# hermes-agent's Python-version floor: ensure a usable interpreter path (system
# 3.11-3.13, or uv for the managed fallback on a 3.14-only box).
ensure_interpreter_path

info "toolchain ready"
exit 0
