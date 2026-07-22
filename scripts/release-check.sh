#!/usr/bin/env bash
# hal0 release-check — pre-tag ritual.
#
# Runs every prerequisite gate before a tag is cut.  This is the last
# safety net between "main looks good" and `git tag`.
#
# Usage:
#   bash scripts/release-check.sh [--channel stable|preview|nightly] [--tag vX.Y.Z]
#
# Gates (in order):
#   1.  Backend tests green (pytest)
#   2.  UI build clean (npm run build)
#   3.  Lint clean (ruff + shellcheck if present)
#   4.  Toolbox image manifest pinned (manifest.json digests non-empty)
#   5.  Release-gate report present, fresh (≤24h), all-pass
#   6.  Working tree clean, proposed tag doesn't exist
#   7.  pyproject.toml version matches the proposed tag
#   8.  Release preflight — policy, tag/release collisions, PyPI

set -euo pipefail
IFS=$'\n\t'

# ── Colour helpers ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
	RED='\033[0;31m'
	YELLOW='\033[1;33m'
	GREEN='\033[0;32m'
	BOLD='\033[1m'
	RESET='\033[0m'
else
	RED=''
	YELLOW=''
	GREEN=''
	BOLD=''
	RESET=''
fi

info() { printf "${GREEN}✔${RESET}  %s\n" "$*"; }
warn() { printf "${YELLOW}!${RESET}  %s\n" "$*"; }
fail() {
	printf "${RED}✗${RESET}  %s\n" "$*" >&2
	FAILURES=$((FAILURES + 1))
}
step() { printf "\n${BOLD}── %s${RESET}\n" "$*"; }
usage() {
	cat <<'EOF'
Usage: bash scripts/release-check.sh [options]

Options:
  --channel stable|preview|nightly  Release channel (default: stable)
  --tag vX.Y.Z                     Proposed immutable release tag
  --dry-run                        Run read-only preflight; never publish or tag
  -h, --help                       Show this help
EOF
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNEL="${HAL0_CHANNEL:-stable}"
PROPOSED_TAG=""
DRY_RUN=false
FAILURES=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--channel=*)
		CHANNEL="${1#--channel=}"
		shift
		;;
	--channel)
		shift
		CHANNEL="$1"
		shift
		;;
	--tag=*)
		PROPOSED_TAG="${1#--tag=}"
		shift
		;;
	--tag)
		shift
		PROPOSED_TAG="$1"
		shift
		;;
	--dry-run)
		DRY_RUN=true
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		usage >&2
		printf "Unknown argument: %s\n" "$1" >&2
		exit 2
		;;
	esac
done

case "${CHANNEL}" in
stable | preview | nightly) ;;
*)
	usage >&2
	printf "Invalid channel: %s (expected stable|preview|nightly)\n" "${CHANNEL}" >&2
	exit 2
	;;
esac

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
		(cd "${REPO_ROOT}/ui" && npm ci --silent && npm run build --silent) &&
			info "ui: npm run build succeeded" ||
			fail "ui build failed"
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
		"${REPO_ROOT}/scripts/release-test.sh"; do
		if [[ -f "${SCRIPT}" ]]; then
			if shellcheck "${SCRIPT}" 2>&1; then
				info "shellcheck OK: $(basename "${SCRIPT}")"
			else
				fail "shellcheck: errors in $(basename "${SCRIPT}")"
				SC_ERRORS=$((SC_ERRORS + 1))
			fi
		fi
	done
	[[ "${SC_ERRORS}" -eq 0 ]] && info "All shell scripts clean"
else
	warn "shellcheck not installed — skipping shell lint"
fi

# ── 4. Toolbox image manifest ─────────────────────────────────────────────────
step "4. Toolbox image manifest"

# Authoritative manifest is repo-root manifest.json. Its toolbox image
# digests are refreshed by scripts/update-toolbox-digests.sh (run on main
# before a release).  The legacy src/hal0/manifest.json shape is checked as
# a soft warning.
MANIFEST="${REPO_ROOT}/manifest.json"
if [[ -f "${MANIFEST}" ]]; then
	info "manifest.json found at repo root"
	# Every entry under toolbox_images must have a non-null `digest`.
	if python3 - "${MANIFEST}" <<'PY'; then
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
	if python3 - "${REPORT}" <<'PY'; then
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

PYPROJ_VERSION="$(
	python3 - <<'PY'
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
	case "${CHANNEL}" in
	stable)
		[[ "${PROPOSED_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
			fail "stable tag must match vX.Y.Z"
		;;
	preview)
		[[ "${PROPOSED_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-(alpha|beta|rc)\.(0|[1-9][0-9]*)$ ]] ||
			fail "preview tag must match vX.Y.Z-(alpha|beta|rc).N"
		;;
	nightly)
		[[ "${PROPOSED_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-nightly\.[0-9]{14}$ ]] ||
			fail "nightly tag must match vX.Y.Z-nightly.YYYYMMDDhhmmss"
		;;
	esac

	TAG_BASE="$(PYTHONPATH=src python3 -c 'import sys; from hal0.release.channel import base_version; print(base_version(sys.argv[1]))' "${PROPOSED_TAG}")"
	PYPROJECT_BASE="$(PYTHONPATH=src python3 -c 'import sys; from hal0.release.channel import base_version; print(base_version(sys.argv[1]))' "${PYPROJ_VERSION}")"
	if [[ "${TAG_BASE}" != "${PYPROJECT_BASE}" ]]; then
		fail "tag base '${TAG_BASE}' does not match pyproject.toml base '${PYPROJECT_BASE}'"
	elif [[ "${CHANNEL}" == "nightly" ]]; then
		info "nightly tag base matches pyproject.toml"
	elif [[ "${PYPROJ_VERSION}" == "${PROPOSED_TAG#v}" ]]; then
		info "version matches proposed tag"
	else
		fail "pyproject.toml version '${PYPROJ_VERSION}' does not match tag '${PROPOSED_TAG}'"
	fi
fi

# ── 8. Release preflight ──────────────────────────────────────────────────────
step "8. Release preflight"

cd "${REPO_ROOT}"

if [[ -z "${PROPOSED_TAG}" ]]; then
	warn "no --tag provided — skipping preflight"
else
	# Derive every publication decision from the shared release policy.
	POLICY_JSON="$(PYTHONPATH=src python3 -m hal0.release.policy "${PROPOSED_TAG}" --format json 2>/dev/null || true)"
	if [[ -z "${POLICY_JSON}" ]]; then
		fail "release.policy failed for tag '${PROPOSED_TAG}'"
	else
		POLICY_VERSION="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' <<<"${POLICY_JSON}")"
		POLICY_KIND="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["kind"])' <<<"${POLICY_JSON}")"
		POLICY_PYTHON_VERSION="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["python_version"] or "")' <<<"${POLICY_JSON}")"
		POLICY_PUBLISH_PYPI="$(python3 -c 'import json,sys; print(str(json.load(sys.stdin)["publish_pypi"]).lower())' <<<"${POLICY_JSON}")"
		info "policy: ${POLICY_VERSION} (${POLICY_KIND})"

		if [[ "${POLICY_KIND}" != "${CHANNEL}" ]]; then
			fail "channel '${CHANNEL}' conflicts with tag policy '${POLICY_KIND}'"
		else
			info "channel agrees with tag policy"
		fi

		if [[ "${POLICY_VERSION}" != "${PROPOSED_TAG#v}" ]]; then
			fail "policy version '${POLICY_VERSION}' does not match tag '${PROPOSED_TAG}'"
		else
			info "policy version agrees with tag"
		fi

		# Official stable/preview tags must be cut from origin/main. All checks
		# here are reads, including --dry-run mode.
		if [[ "${POLICY_KIND}" != "nightly" ]]; then
			TAG_SHA="$(git rev-list -n1 "${PROPOSED_TAG}" 2>/dev/null || true)"
			MAIN_SHA="$(git rev-parse origin/main 2>/dev/null || true)"
			if [[ -z "${MAIN_SHA}" ]]; then
				warn "cannot resolve origin/main — skipping target check"
			elif [[ -n "${TAG_SHA}" && "${TAG_SHA}" != "${MAIN_SHA}" ]]; then
				fail "tag '${PROPOSED_TAG}' points to ${TAG_SHA}, not origin/main (${MAIN_SHA})"
			fi

			if command -v gh &>/dev/null && [[ -n "${MAIN_SHA}" ]]; then
				GH_CHECKS="$(gh api "repos/:owner/:repo/commits/${MAIN_SHA}/check-runs" --jq '.check_runs[].conclusion' 2>/dev/null || true)"
				if echo "${GH_CHECKS}" | grep -q -v "success" 2>/dev/null; then
					fail "GitHub checks on origin/main have non-success conclusions"
				else
					info "GitHub checks on origin/main: all success (or gh not authenticated)"
				fi
			else
				warn "gh CLI not available or no main SHA — skipping GitHub check query"
			fi
		fi

		if git ls-remote --tags origin "${PROPOSED_TAG}" 2>/dev/null | grep -q "refs/tags/${PROPOSED_TAG}"; then
			fail "tag '${PROPOSED_TAG}' already exists on origin"
		else
			info "remote tag '${PROPOSED_TAG}' is available"
		fi

		if command -v gh &>/dev/null; then
			if gh release view "${PROPOSED_TAG}" >/dev/null 2>&1; then
				fail "GitHub Release '${PROPOSED_TAG}' already exists"
			else
				info "GitHub Release '${PROPOSED_TAG}' is available"
			fi
		else
			warn "gh CLI not available — skipping GitHub Release collision check"
		fi

		if [[ "${POLICY_PUBLISH_PYPI}" == "true" ]]; then
			STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "https://pypi.org/pypi/hal0ai/${POLICY_PYTHON_VERSION}/json" || true)"
			if [[ "${STATUS}" == "200" ]]; then
				fail "PyPI already has hal0ai==${POLICY_PYTHON_VERSION}"
			elif [[ "${STATUS}" == "404" ]]; then
				info "PyPI does not have hal0ai==${POLICY_PYTHON_VERSION}"
			else
				fail "PyPI collision preflight returned HTTP ${STATUS:-<none>}"
			fi
		else
			info "policy disables PyPI publication"
		fi

		if [[ "${DRY_RUN}" == true ]]; then
			info "DRY-RUN: all preflight operations were read-only; no tag or release mutation performed"
		fi
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
