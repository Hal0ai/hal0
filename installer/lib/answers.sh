#!/usr/bin/env bash
# installer/lib/answers.sh
#
# Purpose: The one place answers enter the install. install.sh calls
#          hal0_collect_answers once, between the detection half of the
#          pre-flight step and everything that changes the host.
# Expects: Sourced by install.sh. Nothing here prompts or changes the host.
# Provides: hal0_apply_answers, hal0_collect_answers, hal0_save_answers.
# Env:     HAL0_ANSWERS_FILE  path of an answers document (read here only).
#          HAL0_FACTS_FILE    where the facts file goes; exported by
#                             hal0_collect_answers with its default filled in.
# Sets:    HAL0_ANSWERS_APPLIED=1 when a document was applied, else 0. The
#          legacy prompts in install.sh skip themselves when it is 1.
#          HAL0_SAVED_ANSWERS_FILE: where the applied document is kept, in
#          root's own directory (exported by hal0_collect_answers).
#
# Variables that hold document text are unset here before their first
# assignment: a name inherited from the environment with the export attribute
# would otherwise pass the document to every child process.
unset HAL0_ANSWERS_SAVED HAL0_ANSWERS_APPLIED HAL0_ANSWERS_EXPORTED _HAL0_ANSWERS_PY

# Where the reader lives, and the names it may emit. Assigned unconditionally
# (never taken from the environment); tests override them after sourcing.
_HAL0_ANSWERS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HAL0_ANSWERS_READER="${_HAL0_ANSWERS_LIB_DIR}/answers_reader.py"
# The allow-list. installer/lib/answers_reader.py holds the other copy
# (ALLOWED_NAMES); tests/installer/test_answers_apply.py keeps them equal.
_HAL0_ANSWER_NAMES=(
    HAL0_NONINTERACTIVE
    HAL0_MODELS_DIR
    HAL0_FLM_MODELS_DIR
    HAL0_SKIP_OPENWEBUI
    HAL0_SKIP_HERMES
    HAL0_HERMES_TERMINAL
    HAL0_INSTALL_PI
    HAL0_SKIP_PI
    HAL0_SKIP_HINDSIGHT
    HAL0_IMAGE_MODELS
    HAL0_SKIP_BRAIN_MODEL
    HAL0_BRAIN_MODEL
    HAL0_PULL_AGENT_MODEL
    HAL0_AGENT_MODEL
    HAL0_ALLOW_CPU_ONLY
    HAL0_PORT
    HAL0_HOSTNAME
    HAL0_PUBLIC_URL
    HAL0_ALLOWED_ORIGINS
    HAL0_ADMIN_KEY_MODE
    HAL0_ENABLE_BENCH_TIMER
    HAL0_NPU_SETUP
)

# Print the interpreter that reads a document: ${PY} if it runs and is Python
# 3.10 or newer (the reader's floor), else a python3 on PATH that is. Fails
# (prints nothing) when there is none. The installer uses this, before anything
# changes, to know whether a document can be checked without installing Python.
hal0_answers_python() {
    local candidate
    for candidate in "${PY:-}" "$(command -v python3 2>/dev/null || true)"; do
        [[ -n "${candidate}" ]] || continue
        if "${candidate}" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
            >/dev/null 2>&1; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    return 1
}

# Sets store_arg (the caller's local array) to `--default-store ${VAR_DIR}/models`
# when VAR_DIR is known, else to nothing (the reader then assumes the standard
# /var/lib/hal0/models). The reader's "inside the default store" rules follow
# what the installer will create, so a --dev prefix works. Never taken from the
# document.
_hal0_default_store_arg() {
    store_arg=()
    if [[ -n "${VAR_DIR:-}" ]]; then
        store_arg=(--default-store "${VAR_DIR}/models")
    fi
}

# Apply the answers document at PATH; ORIGIN is `file` or `wizard`.
# Runs the reader ONCE (${PY} -I, standard library only), then exports what it
# printed. All-or-nothing: every line is checked against the allow-list and
# collected first; nothing is exported unless every line passed. Values are
# data: they only ever reach `export "NAME=value"` for a vetted NAME.
# The reader's last line is `#saved <base64>`, the saved form of the same
# document from the same read; it is kept (not exported) in HAL0_ANSWERS_SAVED
# for hal0_save_answers, so the copy kept can never differ from what was
# applied. A missing, second or malformed `#saved` line, or any line after it,
# fails the apply.
# Returns 0 on success, the reader's status (2) with its messages on stderr
# when it rejects the document, or 1 for anything the reader should not print.
hal0_apply_answers() {
    local path="${1:-}" origin="${2:-file}"
    # Names exported by this call (empty until it succeeds); install.sh reads it
    # to tell a document's value from an inherited environment variable.
    HAL0_ANSWERS_EXPORTED=()
    if [[ -z "${path}" ]]; then
        echo "error: no answers document given" >&2
        return 1
    fi
    case "${origin}" in
        file | wizard) ;;
        *)
            echo "error: unknown answers origin: ${origin}" >&2
            return 1
            ;;
    esac
    _HAL0_ANSWERS_PY="$(hal0_answers_python)" || _HAL0_ANSWERS_PY=""
    if [[ -z "${_HAL0_ANSWERS_PY}" ]]; then
        echo "error: no Python 3.10 or newer available to read the answers document" >&2
        return 1
    fi

    local output rc=0
    local -a store_arg=()
    _hal0_default_store_arg
    output="$("${_HAL0_ANSWERS_PY}" -I "${_HAL0_ANSWERS_READER}" --origin "${origin}" \
        "${store_arg[@]+"${store_arg[@]}"}" -- "${path}")" || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
        echo "Stopped: the answers document was not accepted." >&2
        return "${rc}"
    fi

    local -a names=() values=()
    local line name value known allowed saved="" saved_seen=0
    # A local keeps the export attribute of a same-named variable in the
    # environment; these hold document text and must not reach child processes.
    export -n output line name value names values saved
    # IFS='=' read drops one trailing '=' from the last field (a path ending in
    # '=' would lose it), so read whole lines and split at the first '='.
    while IFS= read -r line; do
        if [[ "${saved_seen}" -eq 1 ]]; then
            echo "error: the answers reader printed a line after the saved form" >&2
            return 1
        fi
        if [[ "${line}" == "#saved "* ]]; then
            saved="${line#"#saved "}"
            saved_seen=1
            continue
        fi
        if [[ "${line}" != *=* ]]; then
            echo "error: the answers reader produced a malformed line" >&2
            return 1
        fi
        name="${line%%=*}"
        value="${line#*=}"
        known=0
        for allowed in "${_HAL0_ANSWER_NAMES[@]}"; do
            if [[ "${name}" == "${allowed}" ]]; then
                known=1
                break
            fi
        done
        if [[ "${known}" -ne 1 ]]; then
            echo "error: the answers reader produced a variable that is not allowed: ${name//[^A-Za-z0-9_]/?}" >&2
            return 1
        fi
        if [[ "${name}" == "HAL0_HERMES_TERMINAL" && "${origin}" != "wizard" ]]; then
            echo "error: only the install wizard may turn the Hermes terminal tool on" >&2
            return 1
        fi
        names+=("${name}")
        values+=("${value}")
    done <<<"${output}"
    # The saved form: standard base64 on one line, and it must decode.
    if [[ "${saved_seen}" -ne 1 || -z "${saved}" \
        || "${saved}" == *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=]* ]] \
        || ! printf '%s' "${saved}" | base64 -d >/dev/null 2>&1; then
        echo "error: the answers reader did not print a usable saved form" >&2
        return 1
    fi

    local i
    HAL0_ANSWERS_EXPORTED=()
    for i in "${!names[@]}"; do
        export "${names[i]}=${values[i]}"
        HAL0_ANSWERS_EXPORTED+=("${names[i]}")
    done
    HAL0_ANSWERS_SAVED="${saved}"
    # shellcheck disable=SC2034 # read by install.sh, which sources this file
    HAL0_ANSWERS_APPLIED=1
    return 0
}

hal0_collect_answers() {
    unset HAL0_ANSWERS_APPLIED HAL0_ANSWERS_SAVED HAL0_ANSWERS_EXPORTED
    HAL0_ANSWERS_APPLIED=0
    HAL0_ANSWERS_SAVED=""
    HAL0_ANSWERS_EXPORTED=()
    export HAL0_FACTS_FILE="${HAL0_FACTS_FILE:-/run/hal0-install/facts.json}"
    # Never taken from the environment: where root keeps the applied document.
    # The directory is the one the installer already keeps root-only (the
    # Hugging Face secrets file lives in it); /etc/hal0 cannot be used, the
    # hal0 account owns it after the permissions step.
    if [[ -n "${VAR_DIR:-}" ]]; then
        export HAL0_SAVED_ANSWERS_FILE="${VAR_DIR}/secrets/install-answers.json"
    else
        unset HAL0_SAVED_ANSWERS_FILE
    fi
    local path="${HAL0_ANSWERS_FILE:-}"
    [[ -n "${path}" ]] || return 0
    hal0_apply_answers "${path}" file || return $?
    # shellcheck disable=SC2034 # read by install.sh, which sources this file
    HAL0_ANSWERS_APPLIED=1
    return 0
}

# Keep a copy of the applied document at ${HAL0_SAVED_ANSWERS_FILE}
# (${VAR_DIR}/secrets/install-answers.json, SECRETS_DIR in install.sh) so a
# later run can start from the same choices. The saved form comes from the
# apply run itself (its `#saved` line, kept base64 in HAL0_ANSWERS_SAVED), so
# the file written at the end is what was applied even if the document changed
# or went away meanwhile. The reader emits it without the huggingface section,
# without apps.hermes.terminal_tool and without unknown keys; `nofollow write`
# creates it root:root 0600 (random O_EXCL temp file, fchmod before rename), so
# it is never wider than 0600. The install has already succeeded: nothing here
# may fail it.
HAL0_ANSWERS_SAVED=""

_hal0_answers_warn() {
    if declare -F warn >/dev/null 2>&1; then warn "$*"; else echo "warning: $*" >&2; fi
}

# Write the copy taken at apply time. Does not read the document again.
# The directory is root-only (0700, root-owned: `nofollow dir --expect root`
# refuses one the hal0 account owns or swapped in), made here if it does not
# exist yet (--dev installs create it only for a Hugging Face token). The file
# is written with --require-trusted-dir, so a directory that is not trusted
# is refused rather than written into.
hal0_save_answers() {
    [[ "${HAL0_ANSWERS_APPLIED:-0}" == "1" ]] || return 0
    [[ -n "${HAL0_ANSWERS_SAVED:-}" ]] || return 0
    local file="${HAL0_SAVED_ANSWERS_FILE:-}"
    if [[ -z "${file}" ]]; then
        _hal0_answers_warn "could not keep the answers document (no state directory)"
        return 0
    fi
    if ! nofollow dir --create --owner root:root --mode 0700 --expect root "${file%/*}"; then
        _hal0_answers_warn "could not keep the answers document: ${file%/*} is not a root-owned directory"
        return 0
    fi
    # The payload was checked to decode when it was applied.
    if ! printf '%s' "${HAL0_ANSWERS_SAVED}" | base64 -d \
        | nofollow write --owner root:root --mode 0600 --require-trusted-dir "${file}"; then
        _hal0_answers_warn "could not write ${file}; any earlier saved copy was left as it was"
    fi
    return 0
}
