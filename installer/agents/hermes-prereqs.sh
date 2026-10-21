#!/usr/bin/env bash
# hal0 — Hermes provisioning prerequisites.
#
# `hal0 agent install hermes` builds Hermes as a release under
# /var/lib/hal0/hermes (ADR-0032): a git mirror of the pinned commit plus a
# venv that uv creates. uv ships in hal0's own venv (pyproject.toml), and it
# fetches the managed Python 3.14 the Hermes venv runs on, so this script no
# longer installs uv. It ensures the rest of the OS toolchain a clean box may
# lack: python3 with its venv module (the classic clean-Ubuntu trap),
# python3-pip, pipx, and git, using lib/distro.sh for cross-distro naming.
#
# git is load-bearing: the builder fetches the pinned commit into its mirror
# and checks it out with `git worktree add`; a stock minimal distro image
# doesn't ship it (#1726, #1727 review). This script is the SHARED choke point
# for both provisioning entry points — install.sh's inline
# `hal0 agent install hermes` call and the standalone/deferred one an operator
# runs manually (e.g. after HAL0_SKIP_HERMES=1) — both go through
# hal0.cli.agent_commands._install_hermes, which shells out to this exact
# script (installer/install.sh's own preflight_git gate is fast-path
# defense-in-depth only).
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

PY="$(command -v python3 || true)"

# ── Probe: is the toolchain already complete? ────────────────────────────────
have_venv() { [ -n "${PY}" ] && "${PY}" -c 'import venv, ensurepip' >/dev/null 2>&1; }
have_pip() { [ -n "${PY}" ] && "${PY}" -m pip --version >/dev/null 2>&1; }
have_pipx() { command -v pipx >/dev/null 2>&1; }
# `command -v git` only proves a `git` file resolves on PATH, not that it
# runs (a non-executable file — permissions drift, a half-finished package
# install — still resolves). `git --version` is the real functional probe
# (#1727 review).
have_git() { git --version >/dev/null 2>&1; }

if have_venv && have_pip && have_pipx && have_git; then
    info "toolchain already present (python venv + pip + pipx + git) — nothing to do"
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
# -o DPkg::Lock::Timeout=120: this script runs well after boot, so
# unattended-upgrades commonly still holds the dpkg/apt-get lock (#1584) —
# poll for it instead of dying on the first hit.
if [ "${family}" = "debian" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        apt-get -o DPkg::Lock::Timeout=120 update -qq || warn "apt-get update failed — install may still succeed from cache"
    else
        sudo apt-get -o DPkg::Lock::Timeout=120 update -qq || warn "apt-get update failed — install may still succeed from cache"
    fi
fi

info "installing toolchain: ${pkgs[*]}"
info "  ${cmd}"
if ! eval "${cmd}"; then
    die "toolchain install failed. Run it yourself and retry:
  $(pkg_install_cmd "${pkgs[@]}")"
fi

# ── Verify ───────────────────────────────────────────────────────────────────
PY="$(command -v python3 || true)"
have_venv || die "python venv module still missing after install — check ${PY:-python3} and \`$(python_venv_hint)\`"
have_pip || warn "python pip still not importable — bootstrap will bootstrap it via ensurepip"
have_pipx || warn "pipx not on PATH after install — Hermes still installs into the managed venv; pipx is optional tooling"
# git is load-bearing, not optional — the release builder's very first step
# fetches the pinned commit into its git mirror, so a still-missing/unusable
# git here must die loudly with a remediation line rather than warn and let
# the build fail three steps later (#1726, #1727 review).
have_git || die "git still missing or not runnable after install — install it manually
(e.g. \`$(pkg_install_cmd git 2>/dev/null || echo 'install git via your distro package manager')\`),
then re-run \`hal0 agent install hermes\`."

info "toolchain ready"
exit 0
