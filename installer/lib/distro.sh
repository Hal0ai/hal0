#!/usr/bin/env bash
# installer/lib/distro.sh — distro / package-manager detection.
#
# Sourced by install.sh, lib/preflight.sh, and `hal0 doctor` so the one
# place that knows "what distro is this and how do I name an install
# command" lives here instead of being string-matched in three files.
#
# All functions are pure: they read /etc/os-release and probe PATH, echo
# a result, and return 0/1. None of them mutate the caller's environment
# (os-release is sourced in a subshell so its ID/NAME/VERSION vars never
# leak in and clobber installer state).
#
# Sourcing this file twice is a no-op (guard below), so install.sh can
# source it explicitly and preflight.sh can source it again for its
# standalone `hal0 doctor` path without double-defining anything.
#
# Public API:
#   distro_id            — /etc/os-release ID            (fedora, ubuntu, arch…)
#   distro_id_like       — /etc/os-release ID_LIKE       (e.g. "debian", "arch")
#   distro_pretty        — human name (PRETTY_NAME → NAME → ID → "this host")
#   pkg_mgr              — host package manager command   (apt-get|dnf|yum|
#                          zypper|pacman|apk) or non-zero if none recognised
#   distro_family        — ecosystem bucket              (debian|fedora|suse|
#                          arch|alpine) used to pick package names
#   pkg_install_cmd PKG… — the full one-liner an operator runs to install
#                          PKG… with the detected manager (incl. sudo)
#   python_venv_hint     — install one-liner for a `python3 -m venv`-capable
#                          Python on this distro (Debian splits out
#                          python3-venv; most others bundle it)

[[ -n "${_HAL0_DISTRO_SH_LOADED:-}" ]] && return 0
_HAL0_DISTRO_SH_LOADED=1

# Read a single field from /etc/os-release without leaking os-release's
# own variables (ID/NAME/VERSION/…) into the caller. Subshell-source +
# indirect-expand the requested field. Returns 1 if os-release is absent
# or the field is unset/empty.
_os_release_field() {
    local field="$1"
    [[ -r /etc/os-release ]] || return 1
    local value
    # shellcheck disable=SC1091  # os-release is host config, not a project file
    value="$(. /etc/os-release 2>/dev/null && printf '%s' "${!field-}")"
    [[ -n "${value}" ]] || return 1
    printf '%s\n' "${value}"
}

distro_id() { _os_release_field ID; }

distro_id_like() { _os_release_field ID_LIKE; }

distro_pretty() {
    _os_release_field PRETTY_NAME \
        || _os_release_field NAME \
        || distro_id \
        || printf '%s\n' "this host"
}

# Package manager by command presence — more reliable than os-release for
# derivatives (CachyOS reports ID=cachyos but ships pacman; PikaOS reports
# its own ID but ships apt). First match in preference order wins.
pkg_mgr() {
    local m
    for m in apt-get dnf yum zypper pacman apk; do
        if command -v "${m}" >/dev/null 2>&1; then
            printf '%s\n' "${m}"
            return 0
        fi
    done
    return 1
}

# Ecosystem bucket — derived from the concrete package manager so package
# *names* (which differ by ecosystem, not by distro) can be selected.
distro_family() {
    case "$(pkg_mgr)" in
        apt-get) printf 'debian\n' ;;
        dnf | yum) printf 'fedora\n' ;;
        zypper) printf 'suse\n' ;;
        pacman) printf 'arch\n' ;;
        apk) printf 'alpine\n' ;;
        *) return 1 ;;
    esac
}

# The install one-liner for the detected package manager, e.g.
#   pkg_install_cmd python3 python3-venv
#     → "sudo apt-get install -y python3 python3-venv"   (Debian)
#     → "sudo dnf install -y python3 python3-venv"       (Fedora)
#     → "sudo pacman -S --noconfirm python3 python3-venv"(Arch)
# Returns 1 (and echoes nothing) when no package manager is recognised so
# callers can fall back to a generic message.
pkg_install_cmd() {
    local pm
    pm="$(pkg_mgr)" || return 1
    case "${pm}" in
        apt-get) printf 'sudo apt-get install -y %s\n' "$*" ;;
        dnf) printf 'sudo dnf install -y %s\n' "$*" ;;
        yum) printf 'sudo yum install -y %s\n' "$*" ;;
        zypper) printf 'sudo zypper install -y %s\n' "$*" ;;
        pacman) printf 'sudo pacman -S --noconfirm %s\n' "$*" ;;
        apk) printf 'sudo apk add %s\n' "$*" ;;
    esac
}

# Install one-liner for a Python that can do `python3 -m venv`. Debian and
# Ubuntu split the stdlib venv into a separate python3-venv package (the
# classic "python3 present, ensurepip explodes" trap); Fedora, Arch,
# openSUSE and Alpine bundle it with the base interpreter.
python_venv_hint() {
    case "$(distro_family)" in
        debian) pkg_install_cmd python3 python3-venv ;;
        fedora) pkg_install_cmd python3 ;;
        arch) pkg_install_cmd python ;;
        suse) pkg_install_cmd python3 python3-pip ;;
        alpine) pkg_install_cmd python3 ;;
        *) printf '%s\n' "install Python 3.11+ (with the venv stdlib module) from your distribution" ;;
    esac
}
