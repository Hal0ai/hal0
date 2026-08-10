#!/usr/bin/env bash
# installer/lib/ui.sh — shared UI helpers for the hal0 installer family.
#
# Source from a sibling script with:
#     source "$(dirname "${BASH_SOURCE[0]}")/lib/ui.sh"
#
# Public API
#   ui_banner                    — 5-line ASCII banner with version
#   ui_step "Title"              — incremental "── (n/N) Title ──"
#   ui_spinner_run desc cmd...   — run cmd in background, foreground spinner
#   ui_box "Title" "line1" ...   — Unicode box around a multi-line block
#   info / warn / err / die      — log helpers (signatures unchanged)
#
# Globals honoured
#   HAL0_PLAIN=1                 — disable banner/colors/box/spinner glyphs
#   NO_COLOR=1                   — disable colors only (https://no-color.org)
#   UI_STEP_TOTAL                — total step count (caller sets before first ui_step)
#   HAL0_VERSION                 — override the version string
#
# Globals exported (for back-compat with existing scripts)
#   RED YEL GRN BLU AMBER BOLD DIM RST
#   UI_STEP_NUM                  — count of ui_step calls so far
#   CURRENT_STEP                 — most recent ui_step title (useful in traps)
#   UI_PLAIN                     — 1 if degraded mode is active

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
UI_STEP_NUM=${UI_STEP_NUM:-0}
CURRENT_STEP=""

ui_step() {
    UI_STEP_NUM=$((UI_STEP_NUM + 1))
    CURRENT_STEP="$*"
    local total="${UI_STEP_TOTAL:-?}"
    local title="$*"

    if [[ "${UI_PLAIN}" == "1" ]]; then
        printf '\n== (%s/%s) %s\n' "$UI_STEP_NUM" "$total" "$title"
        return 0
    fi

    local cols pad fill label
    cols="$(_ui_cols)"
    label="── (${UI_STEP_NUM}/${total}) ${title} "
    pad=$(( cols - ${#label} ))
    [[ $pad -lt 3 ]] && pad=3
    fill="$(_ui_repeat "${UI_GLYPH_BOX_H}" "$pad")"
    printf '\n%s%s%s%s%s\n' "${BOLD}${AMBER}" "$label" "${RST}${AMBER}" "$fill" "${RST}"
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
