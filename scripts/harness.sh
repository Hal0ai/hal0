#!/usr/bin/env bash
# scripts/harness.sh
#
# hal0 end-to-end test harness orchestrator.
#
# Runs the five tiers in order and merges per-tier JSON reports into
# one tests/harness/reports/harness.json:
#
#   1. installer-test.sh    # --dev install + assert filesystem + serve
#   2. cli-test.sh          # every CLI subcommand against the live API
#   3. runtime-test.sh      # one real slot + chat round-trip
#   4. agents-test.sh       # bundled-agent lifecycle regression (#346)
#   5. harness-cleanup.sh   # tear down dev install, opt-in prod uninstall
#
# Env knobs (passed straight to children):
#   HAL0_HARNESS_PROD=1     enable prod-mode rows (sudo + real /etc paths)
#   HAL0_HARNESS_TLS=1      enable the tls-default install row (needs PROD=1
#                           and installs Caddy via apt/pacman)
#   HAL0_HARNESS_SKIP_LXC=1 skip the optional release-test SSH leg
#
# Exit 0 if no FAIL rows in any tier (skip/deferred ok), 1 otherwise.

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
HARNESS_DIR="${REPO_ROOT}/tests/harness"
REPORTS_DIR="${HARNESS_DIR}/reports"

# Colours.
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; GRN=$'\033[0;32m'; RED=$'\033[0;31m'; RST=$'\033[0m'
else
    BOLD=; GRN=; RED=; RST=
fi

mkdir -p "${REPORTS_DIR}"
# Fresh report area.
rm -f "${REPORTS_DIR}"/*.json "${REPORTS_DIR}"/*.log "${REPORTS_DIR}/.api-handoff" 2>/dev/null || true

run_tier() {
    local name="$1" script="$2"
    printf '\n%s═════════════════════════════════════════════════════%s\n' "${BOLD}" "${RST}"
    printf '%s  Tier: %s%s\n' "${BOLD}" "${name}" "${RST}"
    printf '%s═════════════════════════════════════════════════════%s\n' "${BOLD}" "${RST}"
    if ! bash "${script}"; then
        return 1
    fi
}

# Always run installer + cli + runtime + cleanup. We tolerate FAIL rows
# inside a tier (they go in the JSON); only abort the pipeline if a
# tier script itself can't complete.
INSTALLER_RC=0; CLI_RC=0; RUNTIME_RC=0; AGENTS_RC=0; CLEANUP_RC=0
run_tier "installer" "${HARNESS_DIR}/installer-test.sh"    || INSTALLER_RC=$?
run_tier "cli"       "${HARNESS_DIR}/cli-test.sh"          || CLI_RC=$?
run_tier "runtime"   "${HARNESS_DIR}/runtime-test.sh"      || RUNTIME_RC=$?
run_tier "agents"    "${HARNESS_DIR}/agents-test.sh"       || AGENTS_RC=$?
run_tier "cleanup"   "${HARNESS_DIR}/harness-cleanup.sh"   || CLEANUP_RC=$?

# Optional remote leg: scripts/release-test.sh produces its own JSON
# (tests/release-gate-report.json) with the same row shape. We merge
# it into the aggregate report under tier="release-gate".
RELEASE_GATE_JSON="${REPO_ROOT}/tests/release-gate-report.json"

# ── merge into one report ───────────────────────────────────────────────────
AGGREGATE="${REPORTS_DIR}/harness.json"

python3 - "${AGGREGATE}" "${REPORTS_DIR}" "${RELEASE_GATE_JSON}" <<'PY'
import json, sys, time
from pathlib import Path

agg_path     = Path(sys.argv[1])
reports_dir  = Path(sys.argv[2])
release_path = Path(sys.argv[3])

tiers = []
all_rows = []

# Per-tier JSON files written by each tier script.
for tier_name in ("installer", "cli", "runtime", "agents", "cleanup"):
    p = reports_dir / f"{tier_name}.json"
    if not p.exists():
        tiers.append({"name": tier_name, "status": "missing", "summary": {}, "report": None})
        continue
    d = json.loads(p.read_text())
    tiers.append({
        "name":    tier_name,
        "status":  "ok",
        "summary": d.get("summary", {}),
        "report":  str(p.relative_to(reports_dir.parent.parent)),
    })
    for r in d.get("rows", []):
        r2 = dict(r); r2["tier"] = tier_name
        all_rows.append(r2)

# Optional release-gate leg.
if release_path.exists():
    d = json.loads(release_path.read_text())
    # Only merge if it has non-baseline content.
    if d.get("generated", 0) > 0:
        tiers.append({
            "name":    "release-gate",
            "status":  "ok",
            "summary": d.get("summary", {}),
            "report":  str(release_path.relative_to(reports_dir.parent.parent.parent)),
        })
        for r in d.get("rows", []):
            r2 = dict(r); r2["tier"] = "release-gate"
            all_rows.append(r2)

report = {
    "_schema":   "hal0.harness-report.v1",
    "generated": int(time.time()),
    "tiers":     tiers,
    "summary": {
        "total":    len(all_rows),
        "pass":     sum(1 for r in all_rows if r["status"] == "pass"),
        "fail":     sum(1 for r in all_rows if r["status"] == "fail"),
        "skip":     sum(1 for r in all_rows if r["status"] == "skip"),
        "deferred": sum(1 for r in all_rows if r["status"] == "deferred"),
    },
    "rows": all_rows,
}
agg_path.write_text(json.dumps(report, indent=2) + "\n")
print(f"wrote {agg_path}")
PY

# Pretty-print.
python3 "${SCRIPT_DIR}/harness-report.py" "${AGGREGATE}" || true

# Exit code: any FAIL row → 1.
FAILS="$(python3 -c "
import json
d = json.load(open('${AGGREGATE}'))
print(d['summary'].get('fail', 0))
")"

if [[ "${FAILS}" -gt 0 ]]; then
    printf '\n%s%sharness FAILED%s — %d row(s) failed.\n' "${RED}" "${BOLD}" "${RST}" "${FAILS}" >&2
    exit 1
fi
printf '\n%s%sharness OK%s\n' "${GRN}" "${BOLD}" "${RST}"
exit 0
