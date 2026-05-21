#!/usr/bin/env bash
# hal0 release-check — pre-tag ritual.
#
# Runs every prerequisite gate before a tag is cut.  This is the last
# safety net between "main looks good" and `git tag`.
#
# Usage:
#   bash scripts/release-check.sh [--channel stable|nightly] [--tag vX.Y.Z]
#
# Gates (in order):
#   1.  Backend tests green (pytest)
#   2.  UI build clean (npm run build)
#   3.  Lint clean (ruff + shellcheck if present)
#   4.  Toolbox image manifest pinned (manifest.json digests non-empty)
#   5.  Release-gate report present, fresh (≤24h), all-pass
#   6.  Working tree clean, proposed tag doesn't exist
#   7.  pyproject.toml version matches the proposed tag

set -euo pipefail
IFS=$'\n\t'

# ── Colour helpers ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
    BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; YELLOW=''; GREEN=''; BOLD=''; RESET=''
fi

info()  { printf "${GREEN}✔${RESET}  %s\n" "$*"; }
warn()  { printf "${YELLOW}!${RESET}  %s\n" "$*"; }
fail()  { printf "${RED}✗${RESET}  %s\n" "$*" >&2; FAILURES=$(( FAILURES + 1 )); }
step()  { printf "\n${BOLD}── %s${RESET}\n" "$*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNEL="${HAL0_CHANNEL:-stable}"
PROPOSED_TAG=""
FAILURES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --channel=*) CHANNEL="${1#--channel=}"; shift ;;
        --channel)   shift; CHANNEL="$1"; shift ;;
        --tag=*)     PROPOSED_TAG="${1#--tag=}"; shift ;;
        --tag)       shift; PROPOSED_TAG="$1"; shift ;;
        *)           warn "unknown arg: $1 (ignored)"; shift ;;
    esac
done

# ── 1. Backend tests ──────────────────────────────────────────────────────────
step "1. Backend tests"

if command -v pytest &>/dev/null; then
    # Unit tier only — tier β + γ run elsewhere (the integration workflow
    # and `make release-test` respectively).
    if pytest "${REPO_ROOT}/tests/" -q -m "not integration" 2>&1; then
        info "pytest (-m 'not integration'): green"
    else
        fail "pytest: test failures — fix before release"
    fi
else
    fail "pytest not installed — required for release-check"
fi

# ── 2. UI build ───────────────────────────────────────────────────────────────
step "2. UI build"

if [[ -d "${REPO_ROOT}/ui" ]]; then
    if command -v npm &>/dev/null; then
        ( cd "${REPO_ROOT}/ui" && npm ci --silent && npm run build --silent ) \
            && info "ui: npm run build succeeded" \
            || fail "ui build failed"
    else
        warn "npm not installed — skipping UI build check"
    fi
else
    warn "no ui/ directory — skipping"
fi

# ── 3. Lint ───────────────────────────────────────────────────────────────────
step "3. Lint"

if command -v ruff &>/dev/null; then
    if ruff check "${REPO_ROOT}/src/" "${REPO_ROOT}/tests/" 2>&1; then
        info "ruff: clean"
    else
        fail "ruff found lint errors"
    fi
else
    warn "ruff not installed — skipping Python lint (pip install ruff)"
fi

if command -v shellcheck &>/dev/null; then
    SC_ERRORS=0
    for SCRIPT in \
        "${REPO_ROOT}/installer/install.sh" \
        "${REPO_ROOT}/installer/uninstall.sh" \
        "${REPO_ROOT}/scripts/dev-bootstrap.sh" \
        "${REPO_ROOT}/scripts/release-check.sh" \
        "${REPO_ROOT}/scripts/release-test.sh"
    do
        if [[ -f "${SCRIPT}" ]]; then
            if shellcheck "${SCRIPT}" 2>&1; then
                info "shellcheck OK: $(basename "${SCRIPT}")"
            else
                fail "shellcheck: errors in $(basename "${SCRIPT}")"
                SC_ERRORS=$(( SC_ERRORS + 1 ))
            fi
        fi
    done
    [[ "${SC_ERRORS}" -eq 0 ]] && info "All shell scripts clean"
else
    warn "shellcheck not installed — skipping shell lint"
fi

# ── 4. Toolbox image manifest ─────────────────────────────────────────────────
step "4. Toolbox image manifest"

# Authoritative manifest is repo-root manifest.json (Team A patches it
# post-build in toolbox.yml).  The legacy src/hal0/manifest.json shape is
# checked as a soft warning.
MANIFEST="${REPO_ROOT}/manifest.json"
if [[ -f "${MANIFEST}" ]]; then
    info "manifest.json found at repo root"
    # Every entry under toolbox_images must have a non-null `digest`.
    if python3 - "${MANIFEST}" <<'PY'
import json, sys
m = json.loads(open(sys.argv[1]).read())
images = m.get("toolbox_images", {})
if not images:
    sys.exit("manifest.json has no toolbox_images entry")
missing = [name for name, e in images.items() if not e.get("digest")]
if missing:
    sys.exit("missing digests for: " + ", ".join(missing))
print("all", len(images), "toolbox images pinned")
PY
    then
        info "all toolbox image digests pinned"
    else
        fail "manifest.json has unpinned toolbox image(s) — Team A must run the toolbox workflow on main"
    fi
else
    fail "manifest.json not found at repo root"
fi

# ── 5. Release-gate report freshness ──────────────────────────────────────────
step "5. Release-gate report (tier γ)"

REPORT="${REPO_ROOT}/tests/release-gate-report.json"
if [[ -f "${REPORT}" ]]; then
    if python3 - "${REPORT}" <<'PY'
import json, sys, time
report = json.loads(open(sys.argv[1]).read())
generated = report.get("generated", 0)
age_s = time.time() - generated
if generated <= 0 or age_s > 24 * 3600:
    sys.exit(f"report is stale (age={age_s/3600:.1f}h) — re-run `make release-test`")
summary = report.get("summary", {})
if summary.get("fail", 0):
    sys.exit(f"release-test has {summary['fail']} failed row(s)")
print(f"release-test fresh (age={age_s/3600:.1f}h), {summary.get('pass', 0)} pass, "
      f"{summary.get('skip', 0)} skip, {summary.get('deferred', 0)} deferred")
PY
    then
        info "release-gate report fresh and clean"
    else
        fail "release-gate report is stale or has failures — run 'make release-test'"
    fi
else
    fail "tests/release-gate-report.json not found — run 'make release-test'"
fi

# ── 6. Git working tree + proposed tag ───────────────────────────────────────
step "6. Git state"

cd "${REPO_ROOT}"
if [[ -z "$(git status --porcelain)" ]]; then
    info "working tree clean"
else
    fail "working tree is dirty — commit or stash before tagging"
fi

if [[ -n "${PROPOSED_TAG}" ]]; then
    if git rev-parse "${PROPOSED_TAG}" >/dev/null 2>&1; then
        fail "tag '${PROPOSED_TAG}' already exists"
    else
        info "tag '${PROPOSED_TAG}' is available"
    fi
else
    warn "no --tag provided — skipping tag-exists check"
fi

# ── 7. pyproject.toml version ↔ proposed tag ─────────────────────────────────
step "7. Version ↔ tag agreement"

PYPROJ_VERSION="$(python3 - <<'PY'
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
print(tomllib.loads(open("pyproject.toml","rb").read().decode()).get("project", {}).get("version", ""))
PY
)"
info "pyproject.toml version: ${PYPROJ_VERSION:-<unknown>}"

if [[ -n "${PROPOSED_TAG}" ]]; then
    # Strip leading "v" if present so `v0.1.0` and `0.1.0` both match.
    NORMALISED_TAG="${PROPOSED_TAG#v}"
    if [[ "${PYPROJ_VERSION}" == "${NORMALISED_TAG}" ]]; then
        info "version matches proposed tag"
    else
        fail "pyproject.toml version '${PYPROJ_VERSION}' does not match tag '${PROPOSED_TAG}'"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
printf "\n"
if [[ "${FAILURES}" -eq 0 ]]; then
    printf "${GREEN}${BOLD}Release check passed${RESET} (channel: %s)\n\n" "${CHANNEL}"
    exit 0
else
    printf "${RED}${BOLD}Release check FAILED${RESET} — %d gate(s) failed.\n\n" "${FAILURES}"
    exit 1
fi
