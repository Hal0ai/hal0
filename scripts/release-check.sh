#!/usr/bin/env bash
# hal0 release-check — pre-tag ritual.
#
# Runs every prerequisite gate before a tag is cut.  This is the last
# safety net between "main looks good" and `git tag`.
#
# Usage:
#   bash scripts/release-check.sh [--local] [--channel stable|preview|nightly] [--tag vX.Y.Z]
#
# --local is a publication-read-only rehearsal: it never creates or mutates a
# tag, release, or published artifact, though dependency/build caches may change.
# It skips the remote tier-γ report gate, so a local pass never authorizes a release.
#
# Gates (in order):
#   1.  Backend tests green (pytest)
#   2.  UI build clean (npm run build)
#   3.  Lint clean (ruff + shellcheck if present)
#   4.  Toolbox image manifest pinned (manifest.json digests non-empty)
#   5.  Release-gate report present, fresh (≤24h), coherent, no failures
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
  --local                          Publication-read-only rehearsal; dependency/build caches may change
  -h, --help                       Show this help
EOF
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNEL="${HAL0_CHANNEL:-stable}"
PROPOSED_TAG=""
DRY_RUN=false
LOCAL=false
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
	--local)
		LOCAL=true
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

# Every Python gate runs from the locked repository environment with an
# isolated application home. This prevents a global pytest/ruff or an editable
# checkout elsewhere on the machine from affecting release results.
CHECK_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/hal0-release-check.XXXXXX")"
HAL0_CHECK_HOME="${CHECK_TMPDIR}/hal0-home"
mkdir -p "${HAL0_CHECK_HOME}"
cleanup() {
	rm -rf "${CHECK_TMPDIR}"
}
trap cleanup EXIT

run_repo_python() {
	PYTHONPATH="${REPO_ROOT}/src" HAL0_HOME="${HAL0_CHECK_HOME}" \
		uv run --isolated --locked --python 3.12 --extra dev "$@"
}

if [[ "${LOCAL}" == true ]]; then
	warn "LOCAL rehearsal is publication-read-only (--dry-run implied); dependency/build caches may change"
fi

# ── 1. Backend tests ──────────────────────────────────────────────────────────
step "1. Backend tests"

if command -v uv &>/dev/null; then
	# Unit tier only — tier β + γ run elsewhere (the integration workflow
	# and `make release-test` respectively).
	if run_repo_python pytest "${REPO_ROOT}/tests/" -q -m "not integration" 2>&1; then
		info "isolated locked Python 3.12 pytest (-m 'not integration'): green"
	else
		fail "pytest: test failures — fix before release"
	fi
else
	fail "uv not installed — repository environment is required for release-check"
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

if command -v uv &>/dev/null; then
	if run_repo_python ruff check "${REPO_ROOT}/src/" "${REPO_ROOT}/tests/" 2>&1; then
		info "isolated locked Python 3.12 ruff: clean"
	else
		fail "ruff found lint errors"
	fi
else
	fail "uv not installed — cannot run repository ruff"
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
if [[ "${LOCAL}" == true ]]; then
	warn "LOCAL rehearsal: skipping remote tier-γ release-gate report"
	warn "Local success is insufficient for release authorization; a fresh coherent non-local report is required"
elif [[ -f "${REPORT}" ]]; then
	if python3 - "${REPORT}" <<'PY'; then
import json, sys, time
report = json.loads(open(sys.argv[1]).read())
if not isinstance(report, dict):
    sys.exit("release-test report must be an object")
if report.get("_schema") != "hal0.release-gate-report.v1":
    sys.exit("release-test report _schema must equal 'hal0.release-gate-report.v1'")
generated = report.get("generated", 0)
if type(generated) is not int or generated <= 0:
    sys.exit("release-test report generated must be a positive integer timestamp")
age_s = time.time() - generated
if age_s < -5 * 60:
    sys.exit(f"report timestamp is in the future (skew={-age_s:.0f}s) — check clocks and re-run `make release-test`")
if age_s > 24 * 3600:
    sys.exit(f"report is stale (age={age_s/3600:.1f}h) — re-run `make release-test`")
summary = report.get("summary")
if not isinstance(summary, dict):
    sys.exit("report summary must be an object")
keys = ("total", "pass", "fail", "skip", "deferred")
counts = {}
for key in keys:
    value = summary.get(key)
    if type(value) is not int or value < 0:
        sys.exit(f"report summary {key!r} must be a nonnegative integer")
    counts[key] = value
if counts["total"] <= 0:
    sys.exit("release-test report has no rows")
if counts["total"] != sum(counts[key] for key in keys[1:]):
    sys.exit("release-test summary total does not equal status counts")
rows = report.get("rows")
if not isinstance(rows, list):
    sys.exit("release-test rows must be present and must be an array")
if not rows:
    sys.exit("release-test rows must not be empty")
row_counts = {key: 0 for key in keys[1:]}
for index, row in enumerate(rows):
    if not isinstance(row, dict) or row.get("status") not in row_counts:
        sys.exit(f"release-test row {index} has an invalid status")
    row_counts[row["status"]] += 1
if len(rows) != counts["total"]:
    sys.exit("release-test rows length does not match summary total")
if any(row_counts[key] != counts[key] for key in row_counts):
    sys.exit("release-test row statuses do not match summary counts")
if counts["pass"] <= 0:
    sys.exit("release-test report must contain at least one passed row")
if counts["fail"] != 0:
    sys.exit(f"release-test has {counts['fail']} failed row(s)")
print(f"release-test fresh (age={age_s/3600:.1f}h): {counts['pass']} pass, "
      f"{counts['skip']} skip, {counts['deferred']} deferred; no failed rows")
PY
		info "release-gate report accepted"
	else
		fail "release-gate report is invalid, stale, or has failures — run 'make release-test'"
	fi
else
	fail "tests/release-gate-report.json not found — run 'make release-test'"
fi

# ── 6. Git working tree + proposed tag ───────────────────────────────────────
step "6. Git state"

cd "${REPO_ROOT}"
TRACKED_DIRT="$(
	{
		git diff --name-only -- . \
			':(exclude).pi/shepherd/**' \
			':(exclude)graphify-out/**'
		git diff --cached --name-only -- . \
			':(exclude).pi/shepherd/**' \
			':(exclude)graphify-out/**'
	}
)"
UNTRACKED_DIRT="$(
	{
		git ls-files --others --exclude-standard -- . \
			':(exclude).pi/shepherd/**' \
			':(exclude).pi-subagents/**' \
			':(exclude)graphify-out/**'
		# `.pi/` is ignored globally, so explicitly surface any ignored
		# untracked entry outside the two generated runtime subtrees.
		git ls-files --others -- .pi \
			':(exclude).pi/shepherd/**' \
			':(exclude).pi-subagents/**'
	}
)"
if [[ -z "${TRACKED_DIRT}" && -z "${UNTRACKED_DIRT}" ]]; then
	info "working tree clean (excluding generated state)"
else
	fail "working tree is dirty — commit or stash source changes before tagging"
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
	if [[ "${LOCAL}" == true ]]; then
		warn "Local success is insufficient for release authorization; run the non-local check with a fresh coherent report"
		printf "${GREEN}${BOLD}Local release rehearsal passed${RESET} (channel: %s)\n\n" "${CHANNEL}"
	else
		printf "${GREEN}${BOLD}Release check passed${RESET} (channel: %s)\n\n" "${CHANNEL}"
	fi
	exit 0
else
	printf "${RED}${BOLD}Release check FAILED${RESET} — %d gate(s) failed.\n\n" "${FAILURES}"
	exit 1
fi
