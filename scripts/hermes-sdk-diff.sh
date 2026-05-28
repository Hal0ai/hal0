#!/usr/bin/env bash
# hal0 v0.3 — Upstream Hermes drift detector (ADR-0018).
#
# Owner: v0.3 integration team.
# Triggered by: .github/workflows/hermes-sdk-diff.yml (weekly + dispatch),
#               and operators running locally to preview drift.
#
# Reads the pin recorded in pyproject.toml's [tool.hal0.upstream-hermes]
# table, clones upstream Hermes at HEAD, diffs the tracked surfaces
# against the pinned commit, and prints a markdown summary suitable for
# a GitHub issue body.
#
# Exit codes:
#   0 — no drift in tracked files (or --dry-run / --bump --no-fetch path).
#   1 — drift detected on at least one tracked file.
#   2 — operational error (missing pin, clone failure, bad arguments).
#
# Operator entry points:
#   scripts/hermes-sdk-diff.sh                # full diff to stdout
#   scripts/hermes-sdk-diff.sh --dry-run      # parse pin + print plan
#   scripts/hermes-sdk-diff.sh --bump <sha>   # rewrite pin in-place
#
# Environment overrides:
#   HAL0_HERMES_DIFF_WORKDIR  — clone target (default: mktemp -d).
#   HAL0_HERMES_DIFF_KEEPDIR  — set non-empty to keep the clone after.

set -euo pipefail

# ── repo root resolution ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYPROJECT="${REPO_ROOT}/pyproject.toml"

# ── argument parsing ───────────────────────────────────────────────────
MODE="diff"
BUMP_SHA=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            MODE="dry-run"
            shift
            ;;
        --bump)
            MODE="bump"
            BUMP_SHA="${2:-}"
            if [[ -z "${BUMP_SHA}" ]]; then
                echo "error: --bump requires a commit sha" >&2
                exit 2
            fi
            shift 2
            ;;
        -h|--help)
            sed -n '2,30p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "error: unknown argument '$1'" >&2
            exit 2
            ;;
    esac
done

# ── pin parser ─────────────────────────────────────────────────────────
# Read [tool.hal0.upstream-hermes] via the python tomllib stdlib. Keeps
# the script free of TOML parsing in bash and matches what the workflow
# does in its setup step.
read_pin_field() {
    local field="$1"
    python3 - "$PYPROJECT" "$field" <<'PY'
import sys
import tomllib

path, field = sys.argv[1], sys.argv[2]
with open(path, "rb") as fh:
    data = tomllib.load(fh)
section = data.get("tool", {}).get("hal0", {}).get("upstream-hermes", {})
value = section.get(field)
if value is None:
    sys.stderr.write(f"error: pyproject.toml is missing [tool.hal0.upstream-hermes].{field}\n")
    sys.exit(2)
if isinstance(value, list):
    print("\n".join(value))
else:
    print(value)
PY
}

REPO_URL="$(read_pin_field repo)"
PINNED_COMMIT="$(read_pin_field commit)"
mapfile -t TRACKED_FILES < <(read_pin_field tracked_files)

# ── mode: dry-run ──────────────────────────────────────────────────────
if [[ "${MODE}" == "dry-run" ]]; then
    cat <<EOF
hermes-sdk-diff plan (dry-run; no clone)
  repo:    ${REPO_URL}
  pinned:  ${PINNED_COMMIT}
  tracked: ${#TRACKED_FILES[@]} file(s)
EOF
    for f in "${TRACKED_FILES[@]}"; do
        echo "    - ${f}"
    done
    exit 0
fi

# ── mode: bump ─────────────────────────────────────────────────────────
if [[ "${MODE}" == "bump" ]]; then
    # Rewrite `commit = "..."` and `date = "..."` inside the
    # [tool.hal0.upstream-hermes] block in-place. Uses python rather
    # than sed so we never accidentally rewrite a same-shaped line in
    # an unrelated section.
    python3 - "$PYPROJECT" "$BUMP_SHA" <<'PY'
import re
import sys
from datetime import date

path, new_sha = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as fh:
    text = fh.read()

# Match the [tool.hal0.upstream-hermes] block up to the next top-level
# section header (line starting with `[`) or EOF. Negative-lookahead is
# used per-line so `tracked_files = [` arrays inside the block don't
# terminate the match.
block_re = re.compile(
    r"\[tool\.hal0\.upstream-hermes\]\n(?:(?!^\[)[\s\S])*",
    re.MULTILINE,
)
m = block_re.search(text)
if not m:
    sys.stderr.write("error: [tool.hal0.upstream-hermes] block not found\n")
    sys.exit(2)
block_text = m.group(0)

new_block = re.sub(
    r'(commit\s*=\s*)"[0-9a-fA-F]+"',
    f'\\1"{new_sha}"',
    block_text,
    count=1,
)
new_block = re.sub(
    r'(date\s*=\s*)"[0-9-]+"',
    f'\\1"{date.today().isoformat()}"',
    new_block,
    count=1,
)
if new_block == block_text:
    sys.stderr.write("error: failed to rewrite commit/date — pin shape changed?\n")
    sys.exit(2)

with open(path, "w", encoding="utf-8") as fh:
    fh.write(text[: m.start()] + new_block + text[m.end():])
print(f"bumped commit -> {new_sha}")
PY
    exit 0
fi

# ── mode: diff (default) ───────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
    echo "error: git not on PATH" >&2
    exit 2
fi

WORKDIR="${HAL0_HERMES_DIFF_WORKDIR:-$(mktemp -d -t hermes-sdk-diff.XXXXXX)}"
cleanup() {
    if [[ -z "${HAL0_HERMES_DIFF_KEEPDIR:-}" && -z "${HAL0_HERMES_DIFF_WORKDIR:-}" ]]; then
        rm -rf "${WORKDIR}"
    fi
}
trap cleanup EXIT

CLONE_DIR="${WORKDIR}/hermes-upstream"
if [[ ! -d "${CLONE_DIR}/.git" ]]; then
    # Sparse + filter clone keeps the workdir small. We need full
    # history only for the pinned commit; everything else is a single
    # snapshot at HEAD.
    git clone --filter=blob:none --no-checkout "${REPO_URL}" "${CLONE_DIR}" >/dev/null 2>&1 || {
        echo "error: failed to clone ${REPO_URL}" >&2
        exit 2
    }
fi

cd "${CLONE_DIR}"
git sparse-checkout init --cone >/dev/null 2>&1 || true

# Resolve HEAD + ensure the pinned commit is fetched.
HEAD_SHA="$(git rev-parse origin/HEAD 2>/dev/null || git rev-parse HEAD)"
if ! git cat-file -e "${PINNED_COMMIT}^{commit}" 2>/dev/null; then
    # Pinned commit not in the default fetch — pull it explicitly.
    git fetch --depth=1 origin "${PINNED_COMMIT}" >/dev/null 2>&1 || {
        echo "error: failed to fetch pinned commit ${PINNED_COMMIT}" >&2
        exit 2
    }
fi

HEAD_SHORT="${HEAD_SHA:0:8}"
PINNED_SHORT="${PINNED_COMMIT:0:8}"

# Render markdown summary; track whether any file showed drift.
drift=0
{
    echo "## Hermes upstream drift report"
    echo
    echo "- Pinned: \`${PINNED_SHORT}\`"
    echo "- Upstream HEAD: \`${HEAD_SHORT}\`"
    echo "- Repo: ${REPO_URL}"
    echo
} >&1

for file in "${TRACKED_FILES[@]}"; do
    diff_out="$(git diff "${PINNED_COMMIT}..${HEAD_SHA}" -- "${file}" 2>/dev/null || true)"
    if [[ -z "${diff_out}" ]]; then
        echo "### \`${file}\` — unchanged"
        echo
        continue
    fi
    drift=1
    {
        echo "### \`${file}\` — DRIFT"
        echo
        echo '<details><summary>diff</summary>'
        echo
        echo '```diff'
        echo "${diff_out}"
        echo '```'
        echo
        echo '</details>'
        echo
    } >&1
done

if [[ "${drift}" -eq 0 ]]; then
    echo "_No tracked surfaces changed between ${PINNED_SHORT} and ${HEAD_SHORT}._"
    exit 0
fi

exit 1
