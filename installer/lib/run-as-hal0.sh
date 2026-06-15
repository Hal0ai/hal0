# shellcheck shell=sh
# run-as-hal0.sh — sourced privilege-drop guard for hal0-managed wrappers.
#
# hal0_ensure_runas <user> <cmd> [args...]
#
#   When the caller is root, RE-EXEC <cmd> as <user> with that user's HOME and
#   a sanitized env (HERMES_HOME stripped), so a hal0-managed process never runs
#   as root. Running as root would resolve `~/.hermes` to /root/.hermes (a
#   split-brain state tree the hal0 service never reads) and create root:root
#   files the hal0 user later can't read — the "root-clobber regression" (#843).
#
#   * non-root caller      -> return 0; the caller proceeds with its own perms.
#   * root + HAL0_ALLOW_ROOT (1/true/yes) -> return 0; deliberate root debug.
#   * root, no <user>       -> return 0; nothing to drop to.
#   * root                  -> exec <cmd...> as <user> (this process is replaced).
#   * root, no dropper tool -> return non-zero + a refusal on stderr; we never
#                              proceed silently as root.
#
#   Privilege-drop tool preference: runuser (util-linux, sets HOME via passwd)
#   -> setpriv --init-groups -> sudo -H. The command is always wrapped in
#   `env HOME=<home> -u HERMES_HOME` so HOME is correct and any inherited
#   HERMES_HOME is dropped regardless of which tool is used.
#
#   HAL0_RUNAS_TEST_UID overrides the detected euid — TEST ONLY (we can't become
#   root in CI). Never set it in production.

hal0_ensure_runas() {
    _hru_user="${1:-}"
    [ -n "$_hru_user" ] || return 0
    shift
    [ "$#" -gt 0 ] || return 0

    _hru_uid="${HAL0_RUNAS_TEST_UID:-$(id -u 2>/dev/null || echo 0)}"
    [ "$_hru_uid" = "0" ] || return 0

    case "${HAL0_ALLOW_ROOT:-}" in
        1 | true | yes | TRUE | YES) return 0 ;;
    esac

    id "$_hru_user" >/dev/null 2>&1 || return 0

    _hru_home="$(getent passwd "$_hru_user" 2>/dev/null | cut -d: -f6)"
    [ -n "$_hru_home" ] || _hru_home="/var/lib/hal0"

    if command -v runuser >/dev/null 2>&1; then
        exec runuser -u "$_hru_user" -- env "HOME=$_hru_home" -u HERMES_HOME "$@"
    elif command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid "$_hru_user" --regid "$_hru_user" --init-groups -- \
            env "HOME=$_hru_home" -u HERMES_HOME "$@"
    elif command -v sudo >/dev/null 2>&1; then
        exec sudo -H -u "$_hru_user" -- env -u HERMES_HOME "$@"
    fi

    printf 'hal0: refusing to run as root and cannot drop privileges to %s ' "$_hru_user" >&2
    printf '(no runuser/setpriv/sudo found). Set HAL0_ALLOW_ROOT=1 to override.\n' >&2
    return 1
}
