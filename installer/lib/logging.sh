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
#             use, without creating anything (for a non-root run, the mktemp
#             template, since that name is only chosen at creation).
#           hal0_install_log_init()  — picks the path, creates its directory,
#             and re-points the CALLING SHELL's stdout/stderr through
#             `tee -a` so every subsequent line (including output from
#             sourced libs, subshells, and third-party tools like pip/npm/
#             apt-get) lands in the log. Exports HAL0_INSTALL_LOG.
# Modder notes:
#   exec-based teeing (not a per-`echo` wrapper) so nothing in the rest of
#   the ~4,000-line install.sh has to change to be captured. Root gets
#   /var/log/hal0/install-<ts>.log (FHS: persistent host logs); anything
#   without root (a `--dev` run, or `bash install.sh` without sudo), and a
#   root run whose /var/log/hal0 is unwritable, falls back to a mktemp name,
#   /tmp/hal0-install-XXXXXX.log. Never a timestamped /tmp name (#2407): a
#   plain `>` redirect follows a symlink a local user planted at a guessable
#   name, so a root install could clobber any file through it. mktemp opens
#   with O_EXCL under a random name, born 0600. Anything that reports the
#   log path must read HAL0_INSTALL_LOG, never rebuild the name.
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
        # Not a real path: the mktemp template the non-root log is made
        # from (see _hal0_install_log_mktemp). The name is random on purpose.
        printf '/tmp/hal0-install-XXXXXX.log\n'
    fi
}

# _hal0_install_log_mktemp — create a fresh owner-only log in /tmp and print
# its path (#2407). mktemp opens with O_CREAT|O_EXCL, so it never follows a
# symlink or reuses a file someone else pre-created, and the random name
# can't be planted in advance. GNU and uutils mktemp keep the ".log" suffix
# after the X's; a mktemp that rejects a suffix (busybox) gets the
# suffix-less template instead.
_hal0_install_log_mktemp() {
    (umask 077 && mktemp /tmp/hal0-install-XXXXXX.log 2>/dev/null) \
        || (umask 077 && mktemp /tmp/hal0-install-XXXXXX 2>/dev/null)
}

# hal0_install_log_init — idempotent (a second call is a no-op once
# HAL0_INSTALL_LOG is set), so install.sh can call it unconditionally even
# if a future refactor sources this file more than once.
hal0_install_log_init() {
    [[ -n "${HAL0_INSTALL_LOG:-}" ]] && return 0

    local path=""
    # The log tees installer output unredacted, so it is owner-only (#2361):
    # born 0600 under `umask 077` (in a subshell, so the rest of the install
    # keeps the caller's umask), then chmod'ed in case a same-second rerun
    # appends to a file an older build created 0644. `hal0 doctor bundle`
    # run as a non-root user therefore can't read it and skips it.
    #
    # Only root uses the timestamped FHS path: /var/log/hal0 is root-owned,
    # so nobody else can plant a symlink in it. Everything else, including
    # root's fallback, goes through mktemp in /tmp (#2407).
    if [[ "$(id -u)" -eq 0 ]]; then
        path="$(hal0_install_log_path)"
        if ! mkdir -p "$(dirname "$path")" 2>/dev/null \
            || ! (umask 077 && : >"$path") 2>/dev/null; then
            # /var/log/hal0 couldn't be created/written (read-only FS,
            # unusual SELinux policy, ...) — fall back to /tmp before giving
            # up on a log entirely.
            path=""
        fi
    fi
    if [[ -z "$path" ]]; then
        path="$(_hal0_install_log_mktemp)" || path=""
        if [[ -z "$path" ]]; then
            HAL0_INSTALL_LOG=""
            export HAL0_INSTALL_LOG
            return 1
        fi
    fi
    chmod 0600 "$path" 2>/dev/null || true

    HAL0_INSTALL_LOG="$path"
    export HAL0_INSTALL_LOG
    # Duplicate every line of stdout/stderr into the log while still showing
    # it on the terminal. Two independent `tee`s (rather than one merged
    # stream) keep stdout/stderr on their original fds for anything
    # downstream that distinguishes them (e.g. `2>/dev/null` callers).
    exec > >(tee -a "$path") 2> >(tee -a "$path" >&2)
}
