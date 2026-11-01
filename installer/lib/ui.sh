#!/usr/bin/env bash
# installer/lib/ui.sh
#
# Purpose: Shared terminal UI for the hal0 installer family — banner, the
#          "(n/N) Title" step counter with per-step time estimates and
#          measured durations, a spinner for long-running commands, a
#          boxed summary, and the info/warn/err/die log helpers.
# Expects: Source from a sibling script with
#          `source "$(dirname "${BASH_SOURCE[0]}")/lib/ui.sh"`. Honors
#          HAL0_PLAIN=1 (disable banner/colors/box/spinner glyphs),
#          NO_COLOR=1 (colors only, https://no-color.org), UI_STEP_TOTAL
#          (caller sets before the first ui_step call), HAL0_VERSION
#          (override the detected version string).
# Provides: ui_banner; ui_step TITLE [ESTIMATE]; ui_step_finalize (closes
#          out the last step's measured duration); ui_spinner_run desc
#          cmd...; ui_box "Title" "line1" ...; info/warn/err/die.
#          Exported for back-compat with existing scripts: RED YEL GRN BLU
#          AMBER BOLD DIM RST, UI_STEP_NUM (count of ui_step calls so far),
#          UI_STEP_DURATIONS (assoc array, title -> elapsed seconds),
#          CURRENT_STEP (most recent ui_step title, read by the ERR trap),
#          UI_PLAIN (1 if degraded mode is active).
# Modder notes:
#   Change the CRT-free hal0 theme (banner, box glyphs, spinner style) here.
#   ui_step's estimate is advisory text only — it is never used for timing
#   logic; UI_STEP_DURATIONS is the actual measured source of truth and is
#   what --summary-json reports.

# shellcheck shell=bash

# ── Mode + colors ───────────────────────────────────────────────────────────
_ui_init_colors() {
    UI_PLAIN="${HAL0_PLAIN:-0}"
    if [[ -n "${NO_COLOR:-}" ]]; then UI_PLAIN=1; fi
    if ! [[ -t 1 ]]; then UI_PLAIN=1; fi

    if [[ "${UI_PLAIN}" == "1" ]]; then
        RED=; YEL=; GRN=; BLU=; AMBER=
        BOLD=; DIM=; RST=
        # Ascii fallbacks
        UI_GLYPH_OK="OK"
        UI_GLYPH_WARN="!!"
        UI_GLYPH_ERR="XX"
        UI_GLYPH_BOX_TL="+"; UI_GLYPH_BOX_TR="+"
        UI_GLYPH_BOX_BL="+"; UI_GLYPH_BOX_BR="+"
        UI_GLYPH_BOX_H="-";  UI_GLYPH_BOX_V="|"
        # shellcheck disable=SC2034  # consumed by ui_spinner_run
        UI_SPINNER_GLYPHS=("-" "\\" "|" "/")
    else
        RED=$'\033[0;31m'
        YEL=$'\033[1;33m'
        GRN=$'\033[0;32m'
        BLU=$'\033[0;36m'
        # Sodium amber #FFB000 — hal0 brand accent.
        AMBER=$'\033[38;5;214m'
        BOLD=$'\033[1m'
        DIM=$'\033[2m'
        RST=$'\033[0m'
        UI_GLYPH_OK="✔"
        UI_GLYPH_WARN="!"
        UI_GLYPH_ERR="✗"
        UI_GLYPH_BOX_TL="┌"; UI_GLYPH_BOX_TR="┐"
        UI_GLYPH_BOX_BL="└"; UI_GLYPH_BOX_BR="┘"
        UI_GLYPH_BOX_H="─";  UI_GLYPH_BOX_V="│"
        # shellcheck disable=SC2034
        UI_SPINNER_GLYPHS=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
    fi
}
_ui_init_colors

# ── Internal helpers ────────────────────────────────────────────────────────

# Strip ANSI SGR sequences so we can compute on-screen widths.
_ui_strip_ansi() {
    # shellcheck disable=SC1003  # backslash inside char class is literal in sed
    printf '%s' "$1" | sed -E $'s/\x1b\\[[0-9;]*[a-zA-Z]//g'
}

# Read version from the repo's pyproject.toml. ui.sh lives at
# installer/lib/ui.sh, so the repo root is two directories up.
_ui_read_version() {
    local here root pyproject
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    root="$(cd "${here}/../.." 2>/dev/null && pwd)"
    pyproject="${root}/pyproject.toml"
    if [[ -f "${pyproject}" ]]; then
        awk -F'"' '/^version = /{print $2; exit}' "${pyproject}"
    else
        echo "0.0.0"
    fi
}

# Cached terminal width (recomputed on each call — cheap, and survives resize).
_ui_cols() {
    local cols="${COLUMNS:-}"
    if [[ -z "$cols" ]] && command -v tput >/dev/null 2>&1; then
        cols="$(tput cols 2>/dev/null || true)"
    fi
    [[ -z "$cols" || "$cols" -lt 40 ]] && cols=78
    echo "$cols"
}

# Repeat a single character N times.
_ui_repeat() {
    local ch="$1" n="$2" out=""
    local i
    for ((i = 0; i < n; i++)); do out+="$ch"; done
    printf '%s' "$out"
}

# ── Log helpers (preserve signatures) ───────────────────────────────────────
# UI_WARN_COUNT / UI_ERR_COUNT: bumped by every warn()/err() call so a caller
# (preflight_all's summary line, #1796) can tell "printed warnings along the
# way" from "clean run" even on a soft check whose own return code stays 0 by
# design — counting return codes alone silently drops those warnings from the
# final verdict.
UI_WARN_COUNT=${UI_WARN_COUNT:-0}
UI_ERR_COUNT=${UI_ERR_COUNT:-0}
info()  { printf '%s%s%s  %s\n' "${GRN}" "${UI_GLYPH_OK}"   "${RST}" "$*"; }
warn()  { UI_WARN_COUNT=$((UI_WARN_COUNT + 1)); printf '%s%s%s  %s\n' "${YEL}" "${UI_GLYPH_WARN}" "${RST}" "$*" >&2; }
err()   { UI_ERR_COUNT=$((UI_ERR_COUNT + 1)); printf '%s%s%s  %s\n' "${RED}" "${UI_GLYPH_ERR}"  "${RST}" "$*" >&2; }
die()   { err "$*"; exit 1; }

# ── ui_banner ───────────────────────────────────────────────────────────────
ui_banner() {
    local version="${HAL0_VERSION:-$(_ui_read_version)}"

    if [[ "${UI_PLAIN}" == "1" ]]; then
        printf '\n'
        printf '   hal0 v%s\n' "$version"
        printf '   Local AI inference, native to your hardware.\n\n'
        return 0
    fi

    local A="${AMBER}" R="${RST}" D="${DIM}"
    printf '\n'
    printf '   %s██╗  ██╗ █████╗ ██╗      ██████╗ %s\n' "$A" "$R"
    printf '   %s██║  ██║██╔══██╗██║     ██╔═████╗%s\n' "$A" "$R"
    printf '   %s███████║███████║██║     ██║██╔██║%s\n' "$A" "$R"
    printf '   %s██╔══██║██╔══██║██║     ████╔╝██║%s\n' "$A" "$R"
    printf '   %s██║  ██║██║  ██║███████╗╚██████╔╝%s   %sv%s%s\n' "$A" "$R" "$D" "$version" "$R"
    printf '   %s╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝ %s\n' "$A" "$R"
    printf '   %sLocal AI inference, native to your hardware.%s\n\n' "$D" "$R"
}

# ── ui_step ─────────────────────────────────────────────────────────────────
# Caller sets UI_STEP_TOTAL before the first ui_step call. If unset, we
# print "?" so a missing-config bug is visible rather than silently absent.
#
# ui_step TITLE [ESTIMATE]
#   ESTIMATE is an optional honest range ("~10-30s", "~1-5 min") printed
#   next to the title so an operator watching a quiet step (a pip install,
#   a multi-GB model pull) knows whether to expect seconds or minutes.
#   Omit it for a step with no meaningful wait.
#
# Measures wall-clock time PER STEP (via bash's $SECONDS, zero subprocess
# cost) into UI_STEP_DURATIONS, keyed by title — install.sh's
# `ui_step_finalize` (call once, after the last step's work is done) closes
# out the final step's duration so --summary-json can report real numbers
# instead of only estimates. Durations are best-effort bookkeeping, never
# consulted for control flow, so a caller that never touches
# UI_STEP_DURATIONS pays nothing extra beyond the array writes.
UI_STEP_NUM=${UI_STEP_NUM:-0}
CURRENT_STEP=""
declare -A UI_STEP_DURATIONS 2>/dev/null || true
_UI_STEP_LAST_TITLE=""
_UI_STEP_LAST_START=0

ui_step() {
    local title="$1" estimate="${2:-}"

    if [[ -n "${_UI_STEP_LAST_TITLE}" ]]; then
        UI_STEP_DURATIONS["${_UI_STEP_LAST_TITLE}"]=$((SECONDS - _UI_STEP_LAST_START))
    fi
    _UI_STEP_LAST_TITLE="$title"
    _UI_STEP_LAST_START="$SECONDS"

    UI_STEP_NUM=$((UI_STEP_NUM + 1))
    CURRENT_STEP="$title"
    local total="${UI_STEP_TOTAL:-?}"
    local suffix=""
    [[ -n "$estimate" ]] && suffix="  ${DIM}(EST. TIME: ${estimate})${RST}"

    if [[ "${UI_PLAIN}" == "1" ]]; then
        local plain_suffix=""
        [[ -n "$estimate" ]] && plain_suffix="  (EST. TIME: ${estimate})"
        printf '\n== (%s/%s) %s%s\n' "$UI_STEP_NUM" "$total" "$title" "$plain_suffix"
        return 0
    fi

    local cols pad fill label
    cols="$(_ui_cols)"
    label="── (${UI_STEP_NUM}/${total}) ${title} "
    pad=$(( cols - ${#label} ))
    [[ $pad -lt 3 ]] && pad=3
    fill="$(_ui_repeat "${UI_GLYPH_BOX_H}" "$pad")"
    printf '\n%s%s%s%s%s%s\n' "${BOLD}${AMBER}" "$label" "${RST}${AMBER}" "$fill" "${RST}" "$suffix"
}

# ui_step_finalize — close out the LAST step's duration. Call once, after
# the final step's work completes and before reading UI_STEP_DURATIONS
# (the success box, --summary-json) — otherwise the last entry is missing
# since ui_step only closes out the *previous* step when the *next* one
# starts.
ui_step_finalize() {
    if [[ -n "${_UI_STEP_LAST_TITLE}" ]]; then
        UI_STEP_DURATIONS["${_UI_STEP_LAST_TITLE}"]=$((SECONDS - _UI_STEP_LAST_START))
        _UI_STEP_LAST_TITLE=""
    fi
}

# ── ui_spinner_run ──────────────────────────────────────────────────────────
# Run a command in the background, draw a single-line spinner foreground.
# - In plain / non-TTY mode, just runs the command and tees output through.
# - On success: prints "✔ desc (Ns)".
# - On failure: prints "✗ desc (exit N after Ms)" and replays captured output
#   on stderr so the user still sees the error.
#
# Usage:
#   ui_spinner_run "Installing hal0" pip install -e /path/to/repo
ui_spinner_run() {
    local desc="$1"; shift
    if [[ $# -eq 0 ]]; then
        err "ui_spinner_run: no command supplied for '$desc'"
        return 2
    fi

    local start ts elapsed rc=0

    if [[ "${UI_PLAIN}" == "1" ]]; then
        printf '   %s ...\n' "$desc"
        start="$(date +%s)"
        if "$@"; then
            elapsed=$(( $(date +%s) - start ))
            info "$desc (${elapsed}s)"
        else
            rc=$?
            elapsed=$(( $(date +%s) - start ))
            err "$desc — failed (exit $rc after ${elapsed}s)"
        fi
        return $rc
    fi

    local tmp pid glyph idx tail_line cols room
    tmp="$(mktemp -t hal0-spin.XXXXXX)"
    start="$(date +%s)"

    # Disable the ERR trap inside the subshell-launched command so its
    # non-zero exit doesn't bypass our `wait` capture.
    ( "$@" >"$tmp" 2>&1 ) &
    pid=$!

    # Hide cursor while the spinner runs. We restore it on every return
    # path below (success, failure, and through the SIGINT trap).
    printf '\033[?25l'
    # shellcheck disable=SC2064  # we want $pid expanded now, not later
    trap "printf '\033[?25h'; kill ${pid} 2>/dev/null; exit 130" INT

    idx=0
    while kill -0 "$pid" 2>/dev/null; do
        glyph="${UI_SPINNER_GLYPHS[$(( idx % ${#UI_SPINNER_GLYPHS[@]} ))]}"
        ts="$(date +%s)"
        elapsed=$(( ts - start ))
        cols="$(_ui_cols)"
        # Reserve room: glyph + space + desc + "  Ns  " + tail line
        room=$(( cols - ${#desc} - 12 ))
        [[ $room -lt 10 ]] && room=10
        tail_line="$(tail -n 1 "$tmp" 2>/dev/null | tr -d '\r\n' | cut -c1-"$room")"
        printf '\r\033[K%s%s%s %s  %s%ds%s  %s%s%s' \
            "${AMBER}" "$glyph" "${RST}" \
            "$desc" \
            "${DIM}" "$elapsed" "${RST}" \
            "${DIM}" "$tail_line" "${RST}"
        sleep 0.1
        idx=$(( idx + 1 ))
    done

    wait "$pid" || rc=$?
    elapsed=$(( $(date +%s) - start ))

    # Restore cursor and clear the SIGINT trap.
    printf '\033[?25h'
    trap - INT
    printf '\r\033[K'
    if [[ $rc -eq 0 ]]; then
        printf '%s%s%s  %s  %s(%ds)%s\n' \
            "${GRN}" "${UI_GLYPH_OK}" "${RST}" \
            "$desc" \
            "${DIM}" "$elapsed" "${RST}"
    else
        printf '%s%s%s  %s  %s(exit %d after %ds)%s\n' \
            "${RED}" "${UI_GLYPH_ERR}" "${RST}" \
            "$desc" \
            "${DIM}" "$rc" "$elapsed" "${RST}" >&2
        printf '%s── captured output ──%s\n' "${DIM}" "${RST}" >&2
        if [[ -s "$tmp" ]]; then
            tail -n 50 "$tmp" >&2
        fi
        printf '%s──%s\n' "${DIM}" "${RST}" >&2
    fi
    rm -f "$tmp"
    return $rc
}

# ── ui_box ──────────────────────────────────────────────────────────────────
# Render an amber-bordered box around a list of lines. Lines may include
# ANSI codes — width math strips them first.
#
# Usage:
#   ui_box "hal0 is ready" \
#       "Dashboard   http://localhost:8080" \
#       "CLI         /opt/hal0/.venv/bin/hal0"
ui_box() {
    local title="$1"; shift
    local line plain len cols inner
    cols="$(_ui_cols)"
    [[ $cols -gt 80 ]] && cols=80

    if [[ "${UI_PLAIN}" == "1" ]]; then
        printf '\n=== %s ===\n' "$title"
        for line in "$@"; do
            printf '  %s\n' "$line"
        done
        printf '\n'
        return 0
    fi

    # Inner width = max(title + 4, longest line + 4), capped at cols-2.
    local maxlen=${#title}
    for line in "$@"; do
        plain="$(_ui_strip_ansi "$line")"
        len=${#plain}
        [[ $len -gt $maxlen ]] && maxlen=$len
    done
    inner=$(( maxlen + 4 ))
    (( inner > cols - 2 )) && inner=$(( cols - 2 ))
    (( inner < 30 )) && inner=30

    # Top edge: ┌─ TITLE ──...─┐
    local title_used=$(( ${#title} + 4 ))    # "─ TITLE "
    local top_dashes=$(( inner - title_used ))
    [[ $top_dashes -lt 1 ]] && top_dashes=1
    local top_fill bottom_fill
    top_fill="$(_ui_repeat "${UI_GLYPH_BOX_H}" "$top_dashes")"
    bottom_fill="$(_ui_repeat "${UI_GLYPH_BOX_H}" "$inner")"

    printf '\n%s%s%s %s%s%s%s %s%s\n' \
        "${AMBER}" "${UI_GLYPH_BOX_TL}${UI_GLYPH_BOX_H}" "${RST}" \
        "${BOLD}" "$title" "${RST}" \
        "${AMBER}" "${top_fill}${UI_GLYPH_BOX_TR}" "${RST}"

    for line in "$@"; do
        plain="$(_ui_strip_ansi "$line")"
        len=${#plain}
        local pad=$(( inner - len - 2 ))
        [[ $pad -lt 0 ]] && pad=0
        local sp
        sp="$(_ui_repeat " " "$pad")"
        printf '%s%s%s %s%s %s%s%s\n' \
            "${AMBER}" "${UI_GLYPH_BOX_V}" "${RST}" \
            "$line" "$sp" \
            "${AMBER}" "${UI_GLYPH_BOX_V}" "${RST}"
    done

    printf '%s%s%s%s%s\n\n' \
        "${AMBER}" "${UI_GLYPH_BOX_BL}" \
        "${bottom_fill}" \
        "${UI_GLYPH_BOX_BR}" "${RST}"
}

# ── Component picker ────────────────────────────────────────────────────────
# Interactive checklist for the optional install components.
#
# Usage:
#   rows=( $'id\tLabel\tsummary\ton|off\tunavailable-reason' ... )
#   declare -A picked=()
#   ui_component_picker rows picked      # picked[id] = on | off | unavailable
#
# An empty reason means the row is available; a non-empty reason greys the row
# out, it never toggles, and its result is "unavailable". Input comes from
# ${UI_TTY_IN:-/dev/tty}, output goes to ${UI_TTY_OUT:-/dev/tty}. Always
# returns 0; a failed read (EOF) accepts the current state.
#
# Layout: the rows are shown read-only first, then the preset line
# "a) defaults  b) bare inference  p) pick  [a]"; "p" opens the checklist
# below. Every rendered row is clipped to the terminal width so the in-place
# redraw (cursor up N lines) never meets a wrapped line. The width comes from
# `stty size` on the input tty, else 80; UI_TTY_COLS overrides it (for tests).
#
# The cursor path needs a terminal that answers `tput cuu1`; with UI_PLAIN=1,
# TERM=dumb or an unset TERM the picker uses the numbered plain path instead.
#
# The helpers below read the picker's locals through bash dynamic scope
# (_up_* variables), so only ui_component_picker may call them.

# Terminal width for clipping: UI_TTY_COLS, else `stty size` on fd 4, else 80.
_ui_picker_width() {
    local _up_sz _up_w=""
    if [[ "${UI_TTY_COLS:-}" =~ ^[0-9]+$ ]]; then
        _up_w="$UI_TTY_COLS"
    else
        _up_sz="$(stty size <&4 2>/dev/null)" || _up_sz=""
        _up_w="${_up_sz##* }"
    fi
    [[ "$_up_w" =~ ^[0-9]+$ ]] || _up_w=80
    (( _up_w >= 20 )) || _up_w=20
    _up_cols="$_up_w"
}

# Print the rows (and, when $1 is 1, a hint line) to fd 3, each clipped to one
# physical line. Sets _up_lines to the number of lines written.
_ui_picker_render() {
    local _up_i _up_mark _up_clear _up_lead _up_plen _up_avail
    local _up_head _up_full _up_hd _up_sm _up_hint
    _up_lines=0
    for _up_i in "${!_up_ids[@]}"; do
        if [[ "$_up_mode" == "plain" ]]; then
            _up_clear=""
            printf -v _up_lead '  %d) ' "$((_up_i + 1))"
            _up_plen=${#_up_lead}
        else
            _up_clear=$'\033[2K\r'
            if (( _up_i == _up_pos )); then
                _up_lead=" ${AMBER}>${RST} "
            else
                _up_lead="   "
            fi
            _up_plen=3
        fi
        _up_avail=$((_up_cols - 1 - _up_plen))
        if [[ -n "${_up_reason[$_up_i]}" ]]; then
            _up_full="[-] ${_up_labels[$_up_i]}  (${_up_reason[$_up_i]})"
            printf '%s%s%s%s%s\n' "$_up_clear" "$_up_lead" "${DIM}" "${_up_full:0:_up_avail}" "${RST}" >&3
        else
            if [[ "${_up_cur[$_up_i]}" == "on" ]]; then _up_mark="x"; else _up_mark=" "; fi
            _up_head="[${_up_mark}] ${_up_labels[$_up_i]}"
            _up_full="${_up_head}  ${_up_sums[$_up_i]}"
            _up_full="${_up_full:0:_up_avail}"
            _up_hd="${_up_full:0:${#_up_head}}"
            _up_sm="${_up_full:${#_up_head}}"
            printf '%s%s%s%s%s%s\n' "$_up_clear" "$_up_lead" "$_up_hd" "${DIM}" "$_up_sm" "${RST}" >&3
        fi
        _up_lines=$((_up_lines + 1))
    done
    if [[ "${1:-0}" == "1" ]]; then
        if [[ "$_up_mode" == "plain" ]]; then
            _up_hint="number toggles, a) defaults  b) bare  d) done"
            printf '  %s%s%s\n' "${DIM}" "${_up_hint:0:_up_cols-3}" "${RST}" >&3
        else
            _up_hint="j/k or arrows move, space toggles, a) defaults  b) bare, Enter accepts"
            printf '\033[2K\r  %s%s%s\n' "${DIM}" "${_up_hint:0:_up_cols-3}" "${RST}" >&3
        fi
        _up_lines=$((_up_lines + 1))
    fi
}

# Toggle row index $1; unavailable rows are left alone.
_ui_picker_toggle() {
    local _up_t="$1"
    if [[ -n "${_up_reason[$_up_t]}" ]]; then return 0; fi
    if [[ "${_up_cur[$_up_t]}" == "on" ]]; then _up_cur[_up_t]="off"; else _up_cur[_up_t]="on"; fi
}

# Apply preset $1: "a" restores the row defaults, "b" turns every row off.
_ui_picker_preset() {
    local _up_p
    for _up_p in "${!_up_ids[@]}"; do
        if [[ -n "${_up_reason[$_up_p]}" ]]; then continue; fi
        if [[ "$1" == "a" ]]; then _up_cur[_up_p]="${_up_def[$_up_p]}"; else _up_cur[_up_p]="off"; fi
    done
}

# Move the cursor back up $1 lines.
_ui_picker_up() {
    if (( $1 > 0 )); then tput cuu "$1" >&3 2>/dev/null || true; fi
}

# Read one key from fd 4 into _up_key; returns 1 on EOF or read failure.
# A whole escape sequence is consumed (CSI up to its final byte @..~, SS3 as
# one byte) and reported as UP, DOWN or IGNORE, so no tail bytes are left in
# the queue to be parsed as keys.
_ui_picker_readkey() {
    local _up_b _up_seq="" _up_k=0 _up_o
    IFS= read -rsn1 _up_b <&4 || return 1
    if [[ "$_up_b" != $'\033' ]]; then _up_key="$_up_b"; return 0; fi
    _up_key="IGNORE"
    IFS= read -rsn1 -t 0.1 _up_b <&4 || return 0
    case "$_up_b" in
        "[")
            _up_seq="["
            while (( _up_k < 32 )); do
                _up_k=$((_up_k + 1))
                IFS= read -rsn1 -t 0.1 _up_b <&4 || break
                _up_seq+="$_up_b"
                if [[ -n "$_up_b" ]]; then
                    printf -v _up_o '%d' "'$_up_b"
                    if (( _up_o >= 64 && _up_o <= 126 )); then break; fi
                fi
            done
            ;;
        O)
            if IFS= read -rsn1 -t 0.1 _up_b <&4; then _up_seq="O$_up_b"; fi
            ;;
    esac
    case "$_up_seq" in
        "[A"|"OA") _up_key="UP" ;;
        "[B"|"OB") _up_key="DOWN" ;;
    esac
    return 0
}

ui_component_picker() {
    local -n _up_rows_ref="$1"
    local -n _up_result_ref="$2"
    local -a _up_ids=() _up_labels=() _up_sums=() _up_def=() _up_reason=() _up_cur=()
    local _up_mode="cursor" _up_lines=0 _up_pos=0 _up_cols=80 _up_key=""
    local _up_row _up_id _up_label _up_summary _up_state _up_rsn _up_i _up_n
    local _up_choice="" _up_action="" _up_line=""

    for _up_row in "${_up_rows_ref[@]}"; do
        IFS=$'\t' read -r _up_id _up_label _up_summary _up_state _up_rsn <<<"$_up_row"
        _up_ids+=("$_up_id"); _up_labels+=("$_up_label"); _up_sums+=("$_up_summary")
        _up_def+=("$_up_state"); _up_reason+=("$_up_rsn"); _up_cur+=("$_up_state")
    done
    _up_n=${#_up_ids[@]}

    if [[ "${UI_PLAIN:-0}" == "1" ]] || ! tput cuu1 >/dev/null 2>&1; then
        _up_mode="plain"
    fi

    { exec 4<"${UI_TTY_IN:-/dev/tty}"; } 2>/dev/null || exec 4</dev/null
    { exec 3>"${UI_TTY_OUT:-/dev/tty}"; } 2>/dev/null || exec 3>/dev/null
    _ui_picker_width

    printf '\n%sWhich components should be installed?%s\n' "${BOLD}" "${RST}" >&3
    _up_pos=-1
    _ui_picker_render 0
    _up_pos=0

    # Preset line: loop until a recognised answer; EOF accepts the defaults.
    if [[ "$_up_mode" == "plain" ]]; then
        while :; do
            printf '  a) defaults  b) bare inference  p) pick  [a]: ' >&3
            if ! IFS= read -r _up_choice <&4; then
                printf '\n' >&3
                _up_action="a"
                break
            fi
            printf '\n' >&3
            _up_choice="${_up_choice//[[:space:]]/}"
            case "${_up_choice,,}" in
                ""|a|all|defaults) _up_action="a"; break ;;
                b|bare) _up_action="b"; break ;;
                p|pick) _up_action="p"; break ;;
            esac
        done
    else
        printf '  a) defaults  b) bare inference  p) pick  [a]: ' >&3
        while :; do
            if ! _ui_picker_readkey; then _up_action="a"; break; fi
            case "$_up_key" in
                ""|$'\r'|$'\n'|a|A) _up_action="a"; break ;;
                b|B) _up_action="b"; break ;;
                p|P) _up_action="p"; break ;;
            esac
        done
        printf '%s\n' "${_up_action}" >&3
    fi

    case "$_up_action" in
        b) _ui_picker_preset b ;;
        p)
            if [[ "$_up_mode" == "plain" ]]; then
                while :; do
                    _ui_picker_render 1
                    printf '  > ' >&3
                    IFS= read -r _up_line <&4 || break
                    printf '\n' >&3
                    _up_line="${_up_line//[[:space:]]/}"
                    case "$_up_line" in
                        ""|d|D) break ;;
                        a|A) _ui_picker_preset a ;;
                        b|B) _ui_picker_preset b ;;
                        *[!0-9]*|?????*) ;;
                        *)
                            if (( 10#$_up_line >= 1 && 10#$_up_line <= _up_n )); then
                                _ui_picker_toggle "$((10#$_up_line - 1))"
                            fi
                            ;;
                    esac
                done
            else
                _ui_picker_render 1
                while :; do
                    _ui_picker_readkey || break
                    case "$_up_key" in
                        ""|$'\r'|$'\n') break ;;
                        a|A) _ui_picker_preset a ;;
                        b|B) _ui_picker_preset b ;;
                        j|DOWN) if (( _up_pos < _up_n - 1 )); then _up_pos=$((_up_pos + 1)); fi ;;
                        k|UP) if (( _up_pos > 0 )); then _up_pos=$((_up_pos - 1)); fi ;;
                        " ") _ui_picker_toggle "$_up_pos" ;;
                    esac
                    _ui_picker_up "$_up_lines"
                    _ui_picker_render 1
                done
            fi
            ;;
    esac

    for _up_i in "${!_up_ids[@]}"; do
        if [[ -n "${_up_reason[$_up_i]}" ]]; then
            _up_result_ref["${_up_ids[$_up_i]}"]="unavailable"
        else
            _up_result_ref["${_up_ids[$_up_i]}"]="${_up_cur[$_up_i]}"
        fi
    done
    exec 3>&- 4<&-
    return 0
}
