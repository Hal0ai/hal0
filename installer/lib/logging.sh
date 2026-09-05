#!/usr/bin/env bash
# installer/lib/logging.sh
#
# Purpose: Tee every line the installer prints — narrator output plus every
#          spawned command's stdout/stderr — to a durable on-disk log, so a
#          failed or partial install leaves forensic evidence behind instead
#          of living only in a terminal scrollback nobody saved.
# Expects: Sourced once, early in install.sh, before any output the caller
#          wants captured. No dependency on lib/ui.sh (this file works
#          standalone), though install.sh sources it right after ui.sh by
#          convention so the banner itself is captured too.
# Provides: hal0_install_log_path()  — pure: prints the path this run would
#             use, without creating anything.
#           hal0_install_log_init()  — picks the path, creates its directory,
#             and re-points the CALLING SHELL's stdout/stderr through
#             `tee -a` so every subsequent line (including output from
#             sourced libs, subshells, and third-party tools like pip/npm/
#             apt-get) lands in the log. Exports HAL0_INSTALL_LOG.
# Modder notes:
#   exec-based teeing (not a per-`echo` wrapper) so nothing in the rest of
#   the ~4,000-line install.sh has to change to be captured. Root gets
#   /var/log/hal0/install-<ts>.log (FHS: persistent host logs); anything
#   without root (a `--dev` run, or `bash install.sh` without sudo) falls
#   back to /tmp/hal0-install-<ts>.log since /var/log/hal0 is root-owned.
#   A read-only /tmp (rare, but seen on some hardened containers) degrades
#   to no log rather than aborting the install over forensics — check
#   `HAL0_INSTALL_LOG` for emptiness before relying on it.

# shellcheck shell=bash

[[ -n "${_HAL0_LOGGING_SH_LOADED:-}" ]] && return 0
_HAL0_LOGGING_SH_LOADED=1

hal0_install_log_path() {
    local ts
    ts="$(date -u +%Y%m%d-%H%M%S)"
    if [[ "$(id -u)" -eq 0 ]]; then
        printf '/var/log/hal0/install-%s.log\n' "$ts"
    else
        printf '/tmp/hal0-install-%s.log\n' "$ts"
    fi
}

# hal0_install_log_init — idempotent (a second call is a no-op once
# HAL0_INSTALL_LOG is set), so install.sh can call it unconditionally even
# if a future refactor sources this file more than once.
hal0_install_log_init() {
    [[ -n "${HAL0_INSTALL_LOG:-}" ]] && return 0

    local path
    path="$(hal0_install_log_path)"
    if ! mkdir -p "$(dirname "$path")" 2>/dev/null || ! : >"$path" 2>/dev/null; then
        # Root's /var/log/hal0 couldn't be created/written (read-only FS,
        # unusual SELinux policy, ...) — fall back to /tmp before giving up
        # on a log entirely.
        path="/tmp/hal0-install-$(date -u +%Y%m%d-%H%M%S).log"
        if ! : >"$path" 2>/dev/null; then
            HAL0_INSTALL_LOG=""
            export HAL0_INSTALL_LOG
            return 1
        fi
    fi
    chmod 0644 "$path" 2>/dev/null || true

    HAL0_INSTALL_LOG="$path"
    export HAL0_INSTALL_LOG
    # Duplicate every line of stdout/stderr into the log while still showing
    # it on the terminal. Two independent `tee`s (rather than one merged
    # stream) keep stdout/stderr on their original fds for anything
    # downstream that distinguishes them (e.g. `2>/dev/null` callers).
    exec > >(tee -a "$path") 2> >(tee -a "$path" >&2)
}
