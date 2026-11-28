#!/usr/bin/env bash
# hal0 -- shell lint with a ratchet.
#
# Runs `shellcheck -x -f gcc` over the installer's shell files and compares the
# findings, per file and per SC code, with scripts/shellcheck-baseline.txt. It
# fails only when a file gains findings, so the count can fall but never rise.
#
# Usage: scripts/lint-shell.sh [--update-baseline]
#
# Checked files: installer/**/*.sh, scripts/install-matrix/**/*.sh,
# scripts/fresh-test-ct.sh and scripts/proxmox-ve/*.sh.
#
# Baseline lines are `<repo-relative path>:<SC code>:<count>`, sorted in the C
# locale. The first lines are comments, one of them naming the shellcheck
# version the counts came from. The script works from any directory: it finds
# the repository root from its own location. A different shellcheck version
# than the baseline's only prints a note; the comparison still runs.
#
# Files under scripts/install-matrix/ may never have a baseline entry.
#
# Exit codes:
#   0  no file gained findings (a reminder is printed when a count fell)
#   1  a file gained findings, or install-matrix has a finding or baseline entry
#   2  bad arguments
#   3  shellcheck is not installed
#   4  shellcheck itself failed (not just reported findings)

set -euo pipefail
export LC_ALL=C

update=0
case "${1:-}" in
    "") ;;
    --update-baseline) update=1 ;;
    *) echo "usage: scripts/lint-shell.sh [--update-baseline]" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo "usage: scripts/lint-shell.sh [--update-baseline]" >&2; exit 2; }

if ! command -v shellcheck >/dev/null 2>&1; then
    echo "lint-shell: shellcheck is not installed (apt install shellcheck)" >&2
    exit 3
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
baseline="$root/scripts/shellcheck-baseline.txt"
cd "$root"

version="$(shellcheck --version | sed -n 's/^version: *//p' | head -n 1)"
version="${version:-unknown}"

files=()
for dir in installer scripts/install-matrix; do
    [ -d "$dir" ] || continue
    while IFS= read -r f; do files+=("$f"); done < <(find "$dir" -type f -name '*.sh' | sort)
done
[ -f scripts/fresh-test-ct.sh ] && files+=(scripts/fresh-test-ct.sh)
if [ -d scripts/proxmox-ve ]; then
    while IFS= read -r f; do files+=("$f"); done < <(find scripts/proxmox-ve -maxdepth 1 -type f -name '*.sh' | sort)
fi
[ "${#files[@]}" -gt 0 ] || { echo "lint-shell: no shell files found under $root" >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -r -- "$work"' EXIT

rc=0
shellcheck -x -f gcc "${files[@]}" >"$work/raw" 2>"$work/err" || rc=$?
if [ "$rc" -gt 1 ]; then
    echo "lint-shell: shellcheck failed (exit $rc)" >&2
    cat "$work/err" >&2
    exit 4
fi

# <path> TAB <code> TAB <full line>, path made repository-relative.
awk -v root="$root/" '
    /^[^:]+:[0-9]+:[0-9]+: .*\[SC[0-9]+\]$/ {
        path = $0; sub(/:[0-9]+:[0-9]+: .*$/, "", path)
        code = $0; sub(/^.*\[/, "", code); sub(/\]$/, "", code)
        if (index(path, root) == 1) path = substr(path, length(root) + 1)
        sub(/^\.\//, "", path)
        line = $0
        if (index(line, root) == 1) line = substr(line, length(root) + 1)
        sub(/^\.\//, "", line)
        print path "\t" code "\t" line
    }' "$work/raw" >"$work/found.tsv"

awk -F '\t' '{ n[$1 ":" $2]++ } END { for (k in n) print k ":" n[k] }' "$work/found.tsv" \
    | sort >"$work/current.txt"

if [ "$update" -eq 1 ]; then
    if grep -q '^scripts/install-matrix/' "$work/current.txt"; then
        echo "lint-shell: scripts/install-matrix/ has findings; fix them, they may not be baselined:" >&2
        grep '^scripts/install-matrix/' "$work/current.txt" >&2
        exit 1
    fi
    {
        echo "# shellcheck $version"
        echo "# Findings per file and SC code; regenerate with: scripts/lint-shell.sh --update-baseline"
        echo "# The count may fall but never rise. scripts/install-matrix/ never appears here."
        cat "$work/current.txt"
    } >"$baseline"
    echo "lint-shell: wrote $(grep -c . "$work/current.txt") entries to scripts/shellcheck-baseline.txt (shellcheck $version)"
    exit 0
fi

[ -f "$baseline" ] || { echo "lint-shell: $baseline is missing; run --update-baseline" >&2; exit 2; }

grep -v -e '^#' -e '^[[:space:]]*$' "$baseline" | sort >"$work/base.txt" || true

base_version="$(sed -n 's/^# shellcheck //p' "$baseline" | head -n 1)"
if [ -n "$base_version" ] && [ "$base_version" != "$version" ]; then
    echo "lint-shell: note: baseline was made with shellcheck $base_version, this is $version; counts may differ" >&2
fi

status=0
if grep -q '^scripts/install-matrix/' "$work/base.txt"; then
    echo "lint-shell: the baseline has an entry under scripts/install-matrix/, which is not allowed:" >&2
    grep '^scripts/install-matrix/' "$work/base.txt" >&2
    status=1
fi

# Entries that rose, and entries that fell. Key is path:code, value the count.
awk -F ':' -v up="$work/up.txt" -v down="$work/down.txt" '
    NR == FNR { c = $NF; k = $0; sub(/:[0-9]+$/, "", k); base[k] = c; next }
    { c = $NF; k = $0; sub(/:[0-9]+$/, "", k); cur[k] = c }
    END {
        for (k in cur) {
            b = (k in base) ? base[k] : 0
            if (cur[k] + 0 > b + 0) print k ":" b ":" cur[k] > up
        }
        for (k in base) {
            c = (k in cur) ? cur[k] : 0
            if (c + 0 < base[k] + 0) print k ":" base[k] ":" c > down
        }
    }' "$work/base.txt" "$work/current.txt"
: >>"$work/up.txt"
: >>"$work/down.txt"

# install-matrix files tolerate nothing, baseline or not.
if grep -q '^scripts/install-matrix/' "$work/current.txt"; then
    grep '^scripts/install-matrix/' "$work/current.txt" | while IFS= read -r e; do
        k="${e%:*}"
        echo "$k:0:${e##*:}"
    done >>"$work/up.txt"
fi

if [ -s "$work/up.txt" ]; then
    echo "lint-shell: new shellcheck findings (path:code, baseline -> now):"
    sort -u "$work/up.txt" | while IFS= read -r e; do
        now="${e##*:}"; rest="${e%:*}"; was="${rest##*:}"; key="${rest%:*}"
        echo "  $key  $was -> $now"
        path="${key%:*}"; code="${key##*:}"
        awk -F '\t' -v p="$path" -v c="$code" '$1 == p && $2 == c { print "    " $3 }' "$work/found.tsv"
    done
    status=1
fi

if [ "$status" -ne 0 ]; then
    echo "lint-shell: fix the findings above; the baseline may only go down" >&2
    exit 1
fi

if [ -s "$work/down.txt" ]; then
    echo "lint-shell: $(grep -c . "$work/down.txt") baseline entries have fewer findings now; run scripts/lint-shell.sh --update-baseline to lock the gain in"
else
    echo "lint-shell: ok (no file gained findings)"
fi
exit 0
