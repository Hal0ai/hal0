#!/usr/bin/env bash
# installer/lib/answers.sh
#
# Purpose: The one place answers enter the install. install.sh calls
#          hal0_collect_answers once, between the detection half of the
#          pre-flight step and everything that changes the host.
# Expects: Sourced by install.sh. Nothing here prompts or changes the host,
#          except the setup screens (hal0_run_wizard), which ask on the
#          terminal and write only into a root-only work directory.
# Provides: hal0_apply_answers, hal0_collect_answers, hal0_save_answers,
#          hal0_wizard_present, hal0_run_wizard, hal0_answers_forget,
#          hal0_answers_unset_doc_flm.
# Env:     HAL0_ANSWERS_FILE  path of an answers document (read here only).
#          HAL0_FACTS_FILE    where the facts file goes; exported by
#                             hal0_collect_answers with its default filled in.
# Sets:    HAL0_ANSWERS_APPLIED=1 when a document was applied, else 0. The
#          legacy prompts in install.sh skip themselves when it is 1.
#          HAL0_SAVED_ANSWERS_FILE: where the applied document is kept, in
#          root's own directory (exported by hal0_collect_answers).
#          HAL0_WIZARD_RAN=1 when the setup screens wrote the applied document;
#          HAL0_ANSWERS_ORIGIN is the origin it was applied with.
#
# Variables that hold document text are unset here before their first
# assignment: a name inherited from the environment with the export attribute
# would otherwise pass the document to every child process. The same goes for
# what the setup screens hand back (the token, and the two reasons a model
# store counts as the operator's own choice): they are set only by
# hal0_run_wizard in this process, never taken from the environment.
unset HAL0_ANSWERS_SAVED HAL0_ANSWERS_APPLIED HAL0_ANSWERS_EXPORTED _HAL0_ANSWERS_PY \
    HAL0_WIZARD_RAN HAL0_ANSWERS_ORIGIN _HAL0_WIZARD_CALLED _HAL0_WIZARD_HF_TOKEN \
    _HAL0_WIZARD_CHOSEN_STORE _HAL0_SAVED_STORE _HAL0_WIZARD_ENTRY_PATH \
    _HAL0_WIZARD_CATALOG_PATH _HAL0_WIZARD_MODELS_PATH

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

# _hal0_answers_path_ok VALUE: VALUE is an absolute path made only of the ASCII
# characters the reader allows in a folder path (letters, digits, space and
# _ . + @ , : ~ = / -). A backstop behind the reader's own check: a folder path
# reaches root's mkdir and the install log, so nothing else may pass. The set
# is spelled out (no ranges) and matched in the C locale, so no other byte or
# letter can fall inside it.
_hal0_answers_path_ok() {
    local LC_ALL=C
    [[ "${1:-}" == /* \
        && "${1}" != *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.+@,:~=/\ -]* ]]
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
# fails the apply; so does a name printed twice, or a folder path
# (HAL0_MODELS_DIR, HAL0_FLM_MODELS_DIR) outside _hal0_answers_path_ok.
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
    local saved=""
    # A local keeps the export attribute of a same-named variable in the
    # environment; these hold document text and must not reach child processes.
    export -n output names values saved
    _hal0_answers_parse "${origin}" "${output}" || return 1

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

# _hal0_answers_parse ORIGIN OUTPUT: check what the reader printed, line by
# line, and fill the CALLER's arrays `names` and `values` and its string `saved`
# (the base64 saved form). Nothing is exported here. Every rule of
# hal0_apply_answers applies: a name off the allow-list, a name printed twice, a
# folder path outside _hal0_answers_path_ok, HAL0_HERMES_TERMINAL for any
# origin but `wizard`, a missing, second or malformed `#saved` line, or any line
# after it, fails the parse (return 1, one line on stderr). Shared by the apply
# and by the read of the root-only saved copy (_hal0_saved_store), so the two
# can never disagree on what a line means.
_hal0_answers_parse() {
    local origin="${1:-file}" output="${2:-}"
    local line name value known allowed saved_seen=0
    export -n output line name value
    names=() values=() saved=""
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
        for allowed in "${names[@]+"${names[@]}"}"; do
            if [[ "${name}" == "${allowed}" ]]; then
                echo "error: the answers reader printed ${name} twice" >&2
                return 1
            fi
        done
        if [[ "${name}" == "HAL0_MODELS_DIR" || "${name}" == "HAL0_FLM_MODELS_DIR" ]] \
            && ! _hal0_answers_path_ok "${value}"; then
            echo "error: the answers reader printed a ${name} that is not a plain absolute path" >&2
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
    return 0
}

# hal0_answers_forget NAME: NAME no longer counts as set by the document (it is
# removed from HAL0_ANSWERS_EXPORTED; its value is left as it is).
hal0_answers_forget() {
    local name="${1:-}" kept
    local -a rest=()
    for kept in "${HAL0_ANSWERS_EXPORTED[@]+"${HAL0_ANSWERS_EXPORTED[@]}"}"; do
        [[ "${kept}" == "${name}" ]] || rest+=("${kept}")
    done
    HAL0_ANSWERS_EXPORTED=("${rest[@]+"${rest[@]}"}")
}

# hal0_answers_unset_doc_flm: a document's NPU folder was refused or skipped.
# Later root steps (`hal0 doctor perms --fix` reads HAL0_FLM_MODELS_DIR) must
# not act on it, so the variable goes, and with it the record that the document
# set it (finding C6 of the security review).
hal0_answers_unset_doc_flm() {
    unset HAL0_FLM_MODELS_DIR
    hal0_answers_forget HAL0_FLM_MODELS_DIR
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
    # The setup screens, when this release tree has them. They return 0 with
    # nothing applied when it does not, and today's path below runs unchanged.
    hal0_run_wizard || return $?
    [[ "${HAL0_WIZARD_RAN:-0}" == "1" ]] && return 0
    local path="${HAL0_ANSWERS_FILE:-}"
    [[ -n "${path}" ]] || return 0
    hal0_apply_answers "${path}" file || return $?
    # shellcheck disable=SC2034 # read by install.sh, which sources this file
    HAL0_ANSWERS_APPLIED=1
    # shellcheck disable=SC2034 # read by install.sh and the tests
    HAL0_ANSWERS_ORIGIN="file"
    return 0
}

# ── The setup screens (the wizard) ──────────────────────────────────────────
# The wizard is a separate program (src/hal0/install/wizard, with its question
# catalog installer/manifests/setup-catalog.json). It asks on the terminal,
# merges the flags, the variables, the saved answers and a given document into
# one answers document, and writes it into a work directory only root can use.
# The installer then applies that document like any other: the reader checks it
# in full and nothing the wizard wrote is ever sourced or evaluated. The
# command line, the exit codes and the three side files follow the seam
# contract agreed with the wizard's authors.

# _hal0_wizard_paths: where the wizard and its catalog are in this release
# tree (REPO_ROOT, set by install.sh before the flag loop). HAL0_WIZARD_ENTRY
# and HAL0_WIZARD_CATALOG are test seams, honoured only when the effective uid
# is not 0: a real install runs as root and never takes the program it runs, or
# the questions it asks, from the environment.
_hal0_wizard_paths() {
    _HAL0_WIZARD_ENTRY_PATH="${REPO_ROOT:-}/src/hal0/install/wizard/__main__.py"
    _HAL0_WIZARD_CATALOG_PATH="${REPO_ROOT:-}/installer/manifests/setup-catalog.json"
    _HAL0_WIZARD_MODELS_PATH="${REPO_ROOT:-}/installer/manifests/setup-models.json"
    if [[ "${EUID}" -ne 0 ]]; then
        if [[ -n "${HAL0_WIZARD_ENTRY:-}" ]]; then
            _HAL0_WIZARD_ENTRY_PATH="${HAL0_WIZARD_ENTRY}"
        fi
        if [[ -n "${HAL0_WIZARD_CATALOG:-}" ]]; then
            _HAL0_WIZARD_CATALOG_PATH="${HAL0_WIZARD_CATALOG}"
        fi
    fi
}

# hal0_wizard_present: true when this release tree has the wizard (both its
# entry point and its catalog). When it is false the installer behaves exactly
# as it did before the wizard existed.
hal0_wizard_present() {
    _hal0_wizard_paths
    [[ -n "${REPO_ROOT:-}" || "${EUID}" -ne 0 ]] || return 1
    [[ -f "${_HAL0_WIZARD_ENTRY_PATH}" && -f "${_HAL0_WIZARD_CATALOG_PATH}" ]]
}

# _hal0_wizard_lstat PATH: look at PATH itself (stat without -L is lstat). Sets
# _HAL0_WL_KIND (missing, dir, file, link, other), _HAL0_WL_UID and _HAL0_WL_PERM
# (octal). Returns 1 when PATH exists but cannot be inspected.
_HAL0_WL_KIND="" _HAL0_WL_UID="" _HAL0_WL_PERM=""
_hal0_wizard_lstat() {
    local raw mode kind
    _HAL0_WL_KIND=missing _HAL0_WL_UID="" _HAL0_WL_PERM=""
    [[ -e "${1}" || -L "${1}" ]] || return 0
    raw="$(stat -c '%f %u' -- "${1}" 2>/dev/null)" || return 1
    [[ "${raw}" =~ ^([0-9a-fA-F]{1,8})\ ([0-9]{1,10})$ ]] || return 1
    mode=$(( 16#${BASH_REMATCH[1]} ))
    _HAL0_WL_UID="${BASH_REMATCH[2]}"
    _HAL0_WL_PERM="$(printf '%o' $(( mode & 8#7777 )))"
    kind=$(( mode & 8#170000 ))
    case "${kind}" in
        $(( 8#040000 ))) _HAL0_WL_KIND=dir ;;
        $(( 8#100000 ))) _HAL0_WL_KIND="file" ;;
        $(( 8#120000 ))) _HAL0_WL_KIND="link" ;;
        *) _HAL0_WL_KIND=other ;;
    esac
}

# _hal0_wizard_own_file PATH: PATH is a regular file (not a link) this process
# owns. The work directory is 0700 and this process's alone, so nothing else
# can have put a file there; this only rules out a mistake.
_hal0_wizard_own_file() {
    _hal0_wizard_lstat "${1}" && [[ "${_HAL0_WL_KIND}" == file && "${_HAL0_WL_UID}" == "${EUID}" ]]
}

_hal0_answers_err() {
    if declare -F err >/dev/null 2>&1; then err "$*"; else echo "error: $*" >&2; fi
}

# _hal0_wizard_workdir: print a fresh work directory for one run of the wizard,
# made with mktemp -d (0700, this process's own) under ${PREFIX}/run/hal0-install
# for --dev and /run/hal0-install otherwise, the directory the facts file lives
# in. That base must be a real directory this process owns that no group and
# no other user can write, or the run stops: nobody else may be able to rename
# or replace the work directory while the wizard writes into it.
_hal0_wizard_workdir() {
    local base dir
    if [[ "${DEV_MODE:-0}" -eq 1 ]]; then
        base="${PREFIX:-}/run/hal0-install"
    else
        base="/run/hal0-install"
    fi
    if [[ ! -e "${base}" && ! -L "${base}" ]]; then
        mkdir -p -- "${base}" 2>/dev/null && chmod 0755 -- "${base}" 2>/dev/null || return 1
    fi
    _hal0_wizard_lstat "${base}" || return 1
    [[ "${_HAL0_WL_KIND}" == dir && "${_HAL0_WL_UID}" == "${EUID}" ]] || return 1
    (( (8#${_HAL0_WL_PERM} & 8#022) == 0 )) || return 1
    dir="$(mktemp -d "${base}/wizard.XXXXXXXXXX" 2>/dev/null)" || return 1
    _hal0_wizard_lstat "${dir}" || { rm -rf -- "${dir}"; return 1; }
    if [[ "${_HAL0_WL_KIND}" != dir || "${_HAL0_WL_UID}" != "${EUID}" || "${_HAL0_WL_PERM}" != 700 ]]; then
        rm -rf -- "${dir}"
        return 1
    fi
    printf '%s\n' "${dir}"
}

# Copy the saved answers into the work directory, or fail. Exit 0 copied, 1 no
# saved copy, 3 one that root alone could not have written. The helper's own
# walk (src/hal0/security/nofollow.py, open_dir with require_trusted_dir) opens
# every folder from / without following a link below a folder someone other
# than root could change, and requires the folder holding the file to be
# root's with no group or other write bit. The file itself is opened
# O_NOFOLLOW on that folder's descriptor and must be a regular file owned by
# root, mode 0600, with one name. What the wizard then reads is this copy, made
# O_EXCL in root's own work directory, so the hal0 account (which owns
# /var/lib/hal0 and can rename what is in it) cannot swap the file between this
# check and the wizard's read. "Root" is the set the helper trusts: uid 0, or
# the uid running a non-root --dev install.
_HAL0_SAVED_COPY_PY='
import importlib.util, os, stat, sys
spec = importlib.util.spec_from_file_location("hal0_nofollow", sys.argv[1])
nf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nf)
src, dest = sys.argv[2], sys.argv[3]
parent, name = os.path.split(src)
try:
    dfd = nf.open_dir(parent, require_trusted_dir=True)
except FileNotFoundError:
    sys.exit(1)
except OSError:
    sys.exit(3)
try:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC,
                     dir_fd=dfd)
    except FileNotFoundError:
        sys.exit(1)
    except OSError:
        sys.exit(3)
finally:
    os.close(dfd)
limit = 1 << 20
with os.fdopen(fd, "rb") as fh:
    st = os.fstat(fh.fileno())
    if (not stat.S_ISREG(st.st_mode) or st.st_uid not in nf._trusted_uids()
            or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
        sys.exit(3)
    data = fh.read(limit + 1)
if len(data) > limit:
    sys.exit(3)
out = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
with os.fdopen(out, "wb") as fh:
    fh.write(data)
'

# _hal0_saved_answers_copy DEST: see _HAL0_SAVED_COPY_PY. Warns once when a
# saved copy exists but is not one root alone wrote; prints nothing when there
# is none. Returns 0 only when DEST now holds the copy.
_hal0_saved_answers_copy() {
    local dest="${1}" file="${HAL0_SAVED_ANSWERS_FILE:-}" rc=0
    [[ -n "${file}" ]] || return 1
    [[ -e "${file}" || -L "${file}" ]] || return 1
    "${PY}" -I -c "${_HAL0_SAVED_COPY_PY}" "${REPO_ROOT:-}/src/hal0/security/nofollow.py" \
        "${file}" "${dest}" </dev/null >/dev/null 2>&1 || rc=$?
    case "${rc}" in
        0) return 0 ;;
        1) return 1 ;;
    esac
    rm -f -- "${dest}"
    _hal0_answers_warn "the saved answers ${file} are not a file only root wrote (root-owned, mode 0600, in a folder only root can change); the setup screens start without them"
    return 1
}

# _hal0_saved_store COPY: set _HAL0_SAVED_STORE to the model store the root-only
# saved answers name, read through the reader (never parsed here) and picked
# from its output with the same parser the apply uses. Empty when the copy
# names none or the reader refuses it.
_hal0_saved_store() {
    local copy="${1}" output="" rc=0 i
    local -a store_arg=() names=() values=()
    local saved=""
    export -n output names values saved
    _HAL0_SAVED_STORE=""
    _hal0_default_store_arg
    output="$("${PY}" -I "${_HAL0_ANSWERS_READER}" --origin file \
        "${store_arg[@]+"${store_arg[@]}"}" -- "${copy}" 2>/dev/null)" || rc=$?
    [[ "${rc}" -eq 0 && -n "${output}" ]] || return 0
    _hal0_answers_parse file "${output}" 2>/dev/null || return 0
    for i in "${!names[@]}"; do
        if [[ "${names[i]}" == "HAL0_MODELS_DIR" ]]; then
            _HAL0_SAVED_STORE="${values[i]}"
        fi
    done
    return 0
}

# _hal0_wizard_take_token FILE: the Hugging Face token the person typed on the
# screens. One line, read with `read` (never sourced or evaluated): accepted only
# as `HF_TOKEN=<letters, digits, _ and ->` and nothing else in the file. It goes
# to _HAL0_WIZARD_HF_TOKEN, not exported; install.sh hands it to HF_TOKEN_VAL,
# the variable the legacy prompt fills. Anything else: one warning that never
# shows the file's content, and no token. The file is removed either way.
_hal0_wizard_take_token() {
    local file="${1}" tname="" tvalue="" textra="" first=0 more=1 LC_ALL=C
    export -n tname tvalue textra
    _HAL0_WIZARD_HF_TOKEN=""
    if [[ ! -e "${file}" && ! -L "${file}" ]]; then
        return 0
    fi
    if _hal0_wizard_own_file "${file}"; then
        {
            IFS='=' read -r tname tvalue || first=$?
            IFS= read -r textra || more=$?
        } <"${file}" 2>/dev/null
        if [[ "${first}" -eq 0 && "${more}" -ne 0 && -z "${textra}" \
            && "${tname}" == "HF_TOKEN" && "${tvalue}" =~ ^[A-Za-z0-9_-]+$ ]]; then
            _HAL0_WIZARD_HF_TOKEN="${tvalue}"
        fi
    fi
    rm -f -- "${file}"
    if [[ -z "${_HAL0_WIZARD_HF_TOKEN}" ]]; then
        _hal0_answers_warn "the Hugging Face token from the setup screens was not in the expected form; it was not used (set HF_TOKEN when running the installer, or add it later in the dashboard)"
    fi
    unset tname tvalue textra
    return 0
}

# hal0_run_wizard: run the setup screens when this release tree has them, then
# apply the document they wrote. Returns 0 with nothing done when the tree has
# no wizard. Otherwise returns 0 with the document applied (HAL0_WIZARD_RAN=1,
# HAL0_ANSWERS_APPLIED=1), or the installer's exit code for the wizard's:
#   2 bad input or usage, 11 no GPU and no consent, 70 internal error -> 1
#   10 the person quit, nothing changed -> 10
#   130 interrupted -> 130
#   any other code, an exit 0 with no document, or a refused document -> 1
# The wizard prints its own reasons. Nothing it wrote outlives this call: the
# work directory, and in it the document, the token file and the chosen-folder
# file, is removed on every path.
hal0_run_wizard() {
    unset _HAL0_WIZARD_HF_TOKEN _HAL0_WIZARD_CHOSEN_STORE _HAL0_SAVED_STORE
    HAL0_WIZARD_RAN=0
    _HAL0_WIZARD_CALLED=0
    hal0_wizard_present || return 0
    _HAL0_WIZARD_CALLED=1
    if ! "${PY:-python3}" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
        </dev/null >/dev/null 2>&1; then
        _hal0_answers_err "the setup screens need Python 3.12 or newer, and ${PY:-python3} is not one"
        return 1
    fi
    local dir rc=0
    if ! dir="$(_hal0_wizard_workdir)" || [[ -z "${dir}" ]]; then
        _hal0_answers_err "could not make a work directory for the setup screens that only root can use"
        return 1
    fi
    if declare -F hal0_tmp_track >/dev/null 2>&1; then hal0_tmp_track "${dir}"; fi
    _hal0_wizard_call "${dir}" || rc=$?
    rm -rf -- "${dir}"
    if declare -F hal0_tmp_remove >/dev/null 2>&1; then hal0_tmp_remove "${dir}"; fi
    return "${rc}"
}

# _hal0_wizard_call DIR: the body of hal0_run_wizard, in the work directory DIR.
_hal0_wizard_call() {
    local dir="${1}" rc=0 headless=0 origin=wizard arg chosen="" line=""
    local out="${dir}/answers.json" secret="${dir}/hf-token.env"
    local chosen_out="${dir}/chosen-folder" saved_copy="${dir}/saved.json"
    local -a cmd=()
    export -n line chosen
    cmd=("${PY}" -I "${_HAL0_WIZARD_ENTRY_PATH}"
        --catalog "${_HAL0_WIZARD_CATALOG_PATH}"
        --models "${_HAL0_WIZARD_MODELS_PATH}")
    if [[ -n "${HAL0_FACTS_FILE:-}" && -f "${HAL0_FACTS_FILE}" ]]; then
        cmd+=(--facts "${HAL0_FACTS_FILE}")
    fi
    cmd+=(--out "${out}" --secret-out "${secret}" --chosen-folder-out "${chosen_out}")
    if [[ -n "${VERSION:-}" ]]; then
        cmd+=(--hal0-version "${VERSION}")
    fi
    # The saved answers: only root's own copy, handed over as a copy (above).
    # The box's existing store is read from the same copy.
    if _hal0_saved_answers_copy "${saved_copy}"; then
        cmd+=(--saved "${saved_copy}")
        _hal0_saved_store "${saved_copy}"
    fi
    # A document the person gave is always merged headless: a document from
    # someone else must never be walked through with Enter on pre-filled
    # screens, and it must never earn the chosen-folder signal.
    if [[ -n "${HAL0_ANSWERS_FILE:-}" ]]; then
        cmd+=(--answers "${HAL0_ANSWERS_FILE}")
        headless=1
    fi
    if [[ "${HAL0_NONINTERACTIVE:-0}" == "1" ]]; then
        headless=1
    fi
    if [[ "${headless}" -eq 1 ]]; then
        cmd+=(--headless)
    fi
    if [[ "${_HAL0_RECONFIGURE:-0}" == "1" ]]; then
        cmd+=(--reconfigure)
    fi
    cmd+=(--)
    # The installer's own flags, in order, as the person gave them. Only the
    # ones the wizard knows; --answers=PATH went before the `--` above and
    # --reconfigure is the installer's, not a flag the wizard merges.
    for arg in "${_HAL0_INSTALL_ARGS[@]+"${_HAL0_INSTALL_ARGS[@]}"}"; do
        case "${arg}" in
            --no-owui | --no-comfyui | --no-hermes | --no-pi | --no-agents | --no-memory \
                | --no-brain-model | --bare | --bare-inference | --models-dir=* | --dev \
                | --no-start | --summary-json=* | --help | -h)
                cmd+=("${arg}")
                ;;
        esac
    done

    # Standard input is not the wizard's: it asks on the terminal device, and a
    # `curl | bash` install still has the script on standard input.
    "${cmd[@]}" </dev/null || rc=$?
    case "${rc}" in
        0) ;;
        10 | 130) return "${rc}" ;;
        2 | 11 | 70) return 1 ;;
        *)
            _hal0_answers_err "the setup screens stopped with status ${rc}"
            return 1
            ;;
    esac
    if ! _hal0_wizard_own_file "${out}"; then
        _hal0_answers_err "the setup screens finished but wrote no answers document"
        return 1
    fi
    _hal0_wizard_take_token "${secret}"
    # The folder the person changed on the screens in this run. A headless run
    # never shows a screen, so a file there is ignored.
    if [[ "${headless}" -eq 0 ]] && _hal0_wizard_own_file "${chosen_out}"; then
        IFS= read -r line <"${chosen_out}" 2>/dev/null || line=""
        chosen="${line}"
    fi
    rm -f -- "${chosen_out}"
    # A document merged from a given file had no screens: what it says about
    # the Hermes terminal tool came from that file (or from HAL0_HERMES_TERMINAL
    # in the environment, which install.sh honours on its own), so it is applied
    # as a file. Only a run that could show screens has origin `wizard`.
    if [[ -n "${HAL0_ANSWERS_FILE:-}" ]]; then
        origin="file"
    fi
    if ! hal0_apply_answers "${out}" "${origin}"; then
        unset _HAL0_WIZARD_HF_TOKEN
        return 1
    fi
    # The chosen-folder signal counts only for the very text the reader
    # exported as the document's model store.
    local name
    for name in "${HAL0_ANSWERS_EXPORTED[@]+"${HAL0_ANSWERS_EXPORTED[@]}"}"; do
        if [[ "${name}" == "HAL0_MODELS_DIR" && -n "${chosen}" && "${chosen}" == "${HAL0_MODELS_DIR:-}" ]]; then
            _HAL0_WIZARD_CHOSEN_STORE="${chosen}"
        fi
    done
    HAL0_WIZARD_RAN=1
    # shellcheck disable=SC2034 # read by install.sh and the tests
    HAL0_ANSWERS_ORIGIN="${origin}"
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
