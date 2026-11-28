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

# Apply the answers document at PATH; ORIGIN is `file` or `wizard`.
# Runs the reader (${PY} -I, standard library only), then exports what it
# printed. All-or-nothing: every line is checked against the allow-list and
# collected first; nothing is exported unless every line passed. Values are
# data: they only ever reach `export "NAME=value"` for a vetted NAME.
# Returns 0 on success, the reader's status (2) with its messages on stderr
# when it rejects the document, or 1 for a name off the list.
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
    if [[ -z "${PY:-}" ]]; then
        echo "error: no Python available to read the answers document" >&2
        return 1
    fi

    local output rc=0
    output="$("${PY}" -I "${_HAL0_ANSWERS_READER}" --origin "${origin}" -- "${path}")" || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
        echo "Stopped: the answers document was not accepted." >&2
        return "${rc}"
    fi

    local -a names=() values=()
    local line name value known allowed
    # IFS='=' read drops one trailing '=' from the last field (a path ending in
    # '=' would lose it), so read whole lines and split at the first '='.
    while IFS= read -r line; do
        [[ -n "${line}" ]] || continue
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

    local i
    HAL0_ANSWERS_EXPORTED=()
    for i in "${!names[@]}"; do
        export "${names[i]}=${values[i]}"
        HAL0_ANSWERS_EXPORTED+=("${names[i]}")
    done
    # shellcheck disable=SC2034 # read by install.sh, which sources this file
    HAL0_ANSWERS_APPLIED=1
    _hal0_prepare_saved_answers "${path}"
    return 0
}

hal0_collect_answers() {
    HAL0_ANSWERS_APPLIED=0
    HAL0_ANSWERS_SAVED=""
    HAL0_ANSWERS_EXPORTED=()
    export HAL0_FACTS_FILE="${HAL0_FACTS_FILE:-/run/hal0-install/facts.json}"
    local path="${HAL0_ANSWERS_FILE:-}"
    [[ -n "${path}" ]] || return 0
    hal0_apply_answers "${path}" file || return $?
    # shellcheck disable=SC2034 # read by install.sh, which sources this file
    HAL0_ANSWERS_APPLIED=1
    return 0
}

# Keep a copy of the applied document at ${ETC_DIR}/install-answers.json so a
# later run can start from the same choices. The saved form is produced once,
# at apply time (_hal0_prepare_saved_answers), so the file written at the end
# is what was applied even if the document changed or went away meanwhile. The
# reader emits it without the huggingface section, without
# apps.hermes.terminal_tool and without unknown keys; `nofollow write` creates
# it root:root 0600 (random O_EXCL temp file, fchmod before rename), so it is
# never wider than 0600. The install has already succeeded: nothing here may
# fail it.
HAL0_ANSWERS_SAVED=""

_hal0_answers_warn() {
    if declare -F warn >/dev/null 2>&1; then warn "$*"; else echo "warning: $*" >&2; fi
}

# Produce the saved form of the document at PATH into HAL0_ANSWERS_SAVED.
# In this reader mode stdout carries the JSON on success and stderr only an
# error (no warnings: apply already printed them), so merging them is safe.
# On failure: one warning, nothing saved at the end. Always returns 0.
_hal0_prepare_saved_answers() {
    local path="${1:-}" out rc=0
    HAL0_ANSWERS_SAVED=""
    out="$("${PY}" -I "${_HAL0_ANSWERS_READER}" --emit-saved "${path}" 2>&1)" || rc=$?
    if [[ "${rc}" -ne 0 || -z "${out}" ]]; then
        _hal0_answers_warn "could not prepare a copy of the answers document to keep: ${out%%$'\n'*}"
        return 0
    fi
    HAL0_ANSWERS_SAVED="${out}"
    return 0
}

# Write the copy prepared at apply time. Does not read the document again.
hal0_save_answers() {
    [[ "${HAL0_ANSWERS_APPLIED:-0}" == "1" ]] || return 0
    [[ -n "${HAL0_ANSWERS_SAVED:-}" ]] || return 0
    if [[ -z "${ETC_DIR:-}" ]]; then
        _hal0_answers_warn "could not keep the answers document (no configuration directory)"
        return 0
    fi
    if ! printf '%s\n' "${HAL0_ANSWERS_SAVED}" \
        | nofollow write --owner root:root --mode 0600 --require-trusted-dir "${ETC_DIR}/install-answers.json"; then
        _hal0_answers_warn "could not write ${ETC_DIR}/install-answers.json; any earlier saved copy was left as it was"
    fi
    return 0
}
