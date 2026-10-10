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
# Provides: hal0_install_log_path()  — pure: prints the name template this
#             run would use, without creating anything. The XXXXXX is
#             replaced by random hex at creation.
#           hal0_excl_open(prefix, suffix) — create and open a fresh
#             prefix<random>suffix in one O_CREAT|O_EXCL step, mode 0600.
#             Sets HAL0_EXCL_FD (an open write fd) and HAL0_EXCL_PATH.
#           hal0_install_log_init()  — creates the log and re-points the
#             CALLING SHELL's stdout/stderr through `tee -a` so every
#             subsequent line (including output from sourced libs,
#             subshells, and third-party tools like pip/npm/apt-get) lands
#             in the log. Exports HAL0_INSTALL_LOG (the path, for display)
#             and HAL0_INSTALL_LOG_FD (the open fd, for writing/reading).
# Modder notes:
#   exec-based teeing (not a per-`echo` wrapper) so nothing in the rest of
#   the ~4,000-line install.sh has to change to be captured. Root gets
#   /var/log/hal0/install-<ts>-<random>.log (FHS: persistent host logs);
#   anything without root (a `--dev` run, or `bash install.sh` without
#   sudo), and a root run whose /var/log/hal0 is unwritable, gets
#   /tmp/hal0-install-<random>.log.
#
#   Never a guessable name, and never reopened by name (#2407). /tmp is
#   world-writable, and /var/log/hal0 is owned by the hal0 service user
#   (src/hal0/install/perms.py pins it hal0:hal0 0755), so in both places
#   another uid can plant a symlink at a predictable name or swap a file
#   between its creation and a later open. A plain `>` follows that symlink
#   and a root install would clobber its target. hal0_excl_open creates the
#   file with bash noclobber (O_CREAT|O_EXCL for a new name) under a random
#   name, checks the fd is a regular file that IS that name (noclobber still
#   opens an existing symlink to a FIFO or device), and keeps the fd, so
#   nothing after creation goes back through the name. Anything that reports the log path must read
#   HAL0_INSTALL_LOG, never rebuild the name; anything that reads the log
#   should prefer /dev/fd/$HAL0_INSTALL_LOG_FD.
#
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
        printf '/var/log/hal0/install-%s-XXXXXX.log\n' "$ts"
    else
        printf '/tmp/hal0-install-XXXXXX.log\n'
    fi
}

# hal0_excl_open PREFIX SUFFIX — see the header. Returns 1 (HAL0_EXCL_FD and
# HAL0_EXCL_PATH empty) when no file could be created; HAL0_EXCL_ERR is then
# "nodevfd" when /dev/fd (i.e. /proc) is unusable, so the fd can neither be
# verified nor handed to other processes. Bash noclobber opens a name that
# does not exist with O_CREAT|O_EXCL. A name that already exists as a
# regular file, or as a symlink to one (or a dangling symlink), is refused.
# A symlink to a FIFO or a device is NOT refused — noclobber opens it — so
# after the open the fd must be a regular file whose dev:ino is the name's
# own (stat without -L, so a symlink never matches); anything else is
# closed and another random name tried. With 48 random bits per name such a
# plant needs a lucky guess anyway; the check makes it harmless.
hal0_excl_open() {
    local prefix="$1" suffix="${2:-}"
    local - rand name old_umask fd fd_id name_id _
    HAL0_EXCL_FD=""
    HAL0_EXCL_PATH=""
    HAL0_EXCL_ERR=""
    set -C
    old_umask="$(umask)"
    umask 077
    for _ in 1 2 3 4 5 6 7 8; do
        rand="$(od -An -N6 -tx1 /dev/urandom 2>/dev/null)" || break
        rand="${rand//[^0-9a-f]/}"
        [[ ${#rand} -eq 12 ]] || break
        name="${prefix}${rand}${suffix}"
        { exec {fd}>"$name"; } 2>/dev/null || continue
        if [[ ! -e "/dev/fd/${fd}" ]]; then
            exec {fd}>&-
            rm -f -- "$name" 2>/dev/null || true
            HAL0_EXCL_ERR="nodevfd"
            break
        fi
        fd_id="$(stat -L -c '%d:%i' "/dev/fd/${fd}" 2>/dev/null)" || fd_id=""
        name_id="$(stat -c '%d:%i' "$name" 2>/dev/null)" || name_id=""
        if [[ -f "/dev/fd/${fd}" && -n "$fd_id" && "$fd_id" == "$name_id" ]]; then
            HAL0_EXCL_FD="$fd"
            HAL0_EXCL_PATH="$name"
            break
        fi
        exec {fd}>&-
    done
    umask "$old_umask"
    [[ -n "$HAL0_EXCL_FD" ]]
}

# hal0_install_log_init — idempotent (a second call is a no-op once
# HAL0_INSTALL_LOG is set), so install.sh can call it unconditionally even
# if a future refactor sources this file more than once.
hal0_install_log_init() {
    [[ -n "${HAL0_INSTALL_LOG:-}" ]] && return 0

    # The log tees installer output unredacted, so it is owner-only (#2361):
    # born 0600 under `umask 077` inside hal0_excl_open, which restores the
    # caller's umask for the rest of the install. `hal0 doctor bundle` run
    # as a non-root user therefore can't read it and skips it.
    local tmpl prefix suffix opened=1
    if [[ "$(id -u)" -eq 0 ]]; then
        tmpl="$(hal0_install_log_path)"
        if [[ "$tmpl" == *XXXXXX* ]]; then
            prefix="${tmpl%%XXXXXX*}"
            suffix="${tmpl#*XXXXXX}"
        else
            prefix="${tmpl%.log}-"
            suffix=".log"
        fi
        # /var/log/hal0 may be uncreatable/unwritable (read-only FS, unusual
        # SELinux policy, ...) — fall back to /tmp before giving up on a log.
        if mkdir -p "$(dirname "$tmpl")" 2>/dev/null; then
            hal0_excl_open "$prefix" "$suffix" && opened=0
        fi
    fi
    if [[ $opened -ne 0 ]] && ! hal0_excl_open /tmp/hal0-install- .log; then
        if [[ "${HAL0_EXCL_ERR:-}" == "nodevfd" ]]; then
            # The log is written through /dev/fd/N; without /proc there is no
            # way to do that safely, so say so instead of logging nothing.
            printf 'WARN  install log disabled: /dev/fd is unavailable (is /proc mounted?)\n' >&2
        fi
        HAL0_INSTALL_LOG=""
        export HAL0_INSTALL_LOG
        return 1
    fi

    HAL0_INSTALL_LOG="$HAL0_EXCL_PATH"
    HAL0_INSTALL_LOG_FD="$HAL0_EXCL_FD"
    export HAL0_INSTALL_LOG HAL0_INSTALL_LOG_FD
    # Duplicate every line of stdout/stderr into the log while still showing
    # it on the terminal. Two independent `tee`s (rather than one merged
    # stream) keep stdout/stderr on their original fds for anything
    # downstream that distinguishes them (e.g. `2>/dev/null` callers).
    # /dev/fd/N reopens the inode the fd already holds, not the name.
    exec > >(tee -a "/dev/fd/${HAL0_INSTALL_LOG_FD}") \
        2> >(tee -a "/dev/fd/${HAL0_INSTALL_LOG_FD}" >&2)
}
