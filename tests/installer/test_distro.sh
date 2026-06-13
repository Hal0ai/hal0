#!/usr/bin/env bash
# Unit tests for installer/lib/distro.sh — the pure detection helpers.
#
# Deliberately distro-agnostic: every assertion checks an invariant that
# holds on any supported host, so the same file passes on the maintainer's
# CachyOS/pacman box AND inside the Fedora validation container. Run with:
#   bash tests/installer/test_distro.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../installer/lib/distro.sh disable=SC1091
source "${HERE}/../../installer/lib/distro.sh"

FAIL=0
ok()  { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; FAIL=1; }
# want DESC ARGS… — ARGS… is a `test`/`[` expression (string equality and
# the -n/-z unary tests; no glob — use a literal `case` for patterns).
want() { local d="$1"; shift; if test "$@"; then ok "${d}"; else bad "${d}"; fi; }

pm="$(pkg_mgr || true)"
fam="$(distro_family || true)"
ic="$(pkg_install_cmd cowsay)"
vh="$(python_venv_hint)"
echo "distro.sh on: $(distro_pretty)  (id=$(distro_id || echo ?), pm=${pm:-none}, family=${fam:-?})"

# os-release is readable on every supported target.
want "distro_id is non-empty"     -n "$(distro_id)"
want "distro_pretty is non-empty" -n "$(distro_pretty)"

# A recognised package manager must be found, and it must be one we know.
want "pkg_mgr found a manager" -n "${pm}"
case "${pm}" in
    apt-get | dnf | yum | zypper | pacman | apk) ok "pkg_mgr is a known manager" ;;
    *) bad "pkg_mgr is a known manager (${pm})" ;;
esac

# Family must resolve and stay consistent with the detected manager.
want "distro_family resolved" -n "${fam}"
case "${pm}" in
    apt-get) want "apt-get → debian" "${fam}" = debian ;;
    dnf | yum) want "dnf/yum  → fedora" "${fam}" = fedora ;;
    zypper) want "zypper   → suse" "${fam}" = suse ;;
    pacman) want "pacman   → arch" "${fam}" = arch ;;
    apk) want "apk      → alpine" "${fam}" = alpine ;;
esac

# pkg_install_cmd: sudo-prefixed, names the manager, ends with the package.
case "${ic}" in "sudo "*) ok "install cmd starts with sudo" ;; *) bad "install cmd starts with sudo (${ic})" ;; esac
case "${ic}" in *cowsay) ok "install cmd ends with package" ;; *) bad "install cmd ends with package (${ic})" ;; esac
case "${ic}" in *"${pm}"*) ok "install cmd names the manager" ;; *) bad "install cmd names the manager (${ic})" ;; esac

# python_venv_hint mentions python and is a runnable install one-liner.
case "${vh}" in *python*) ok "venv hint mentions python" ;; *) bad "venv hint mentions python (${vh})" ;; esac
case "${vh}" in "sudo "*) ok "venv hint is a sudo install cmd" ;; *) bad "venv hint is a sudo install cmd (${vh})" ;; esac
# Debian is the only family that must name the split python3-venv package.
if [[ "${fam}" == debian ]]; then
    case "${vh}" in *python3-venv*) ok "debian venv hint adds python3-venv" ;; *) bad "debian venv hint adds python3-venv (${vh})" ;; esac
fi

# Sourcing twice is a no-op (guard holds).
# shellcheck source=../../installer/lib/distro.sh disable=SC1091
source "${HERE}/../../installer/lib/distro.sh"
want "double-source guard holds" "${_HAL0_DISTRO_SH_LOADED}" = 1

# os-release vars must NOT have leaked into this shell.
want "ID did not leak from os-release"   -z "${ID:-}"
want "NAME did not leak from os-release" -z "${NAME:-}"

echo
if [[ "${FAIL}" -eq 0 ]]; then echo "PASS"; exit 0; else echo "FAIL"; exit 1; fi
