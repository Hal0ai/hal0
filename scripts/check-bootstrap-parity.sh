#!/usr/bin/env bash
# hal0 — bootstrap.sh ↔ live install.sh parity check (issue #494).
#
# Owner: installer team.
# Triggered by: .github/workflows/bootstrap-parity.yml (daily + dispatch),
#               and operators running locally to preview drift.
#
# The header of installer/bootstrap.sh declares that two files are MIRRORED
# and must stay byte-identical:
#       - Hal0ai/hal0:installer/bootstrap.sh   (canonical, audited)
#       - Hal0ai/hal0-web:public/install.sh    (served at hal0.dev/install.sh)
# "When you edit one, sync the other in the same PR." — with no enforcement,
# the live one-liner (what `curl https://hal0.dev/install.sh | bash` actually
# runs) can drift from the audited in-tree copy and execute un-reviewed code.
#
# This script fetches the LIVE served copy and diffs it against the in-tree
# canonical copy. It is deliberately dependency-light: curl + diff only.
#
# Exit codes:
#   0 — the two are byte-identical (in sync).
#   1 — drift detected; a unified diff is printed to stdout.
#   2 — operational error (could not fetch the live copy, missing local file,
#       bad arguments). A transient site/network outage lands here so it is
#       never misread as drift.
#
# Environment overrides:
#   HAL0_INSTALL_URL  — URL of the live served installer
#                       (default: https://hal0.dev/install.sh).

set -euo pipefail

# ── repo root resolution ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

LOCAL_FILE="${REPO_ROOT}/installer/bootstrap.sh"
INSTALL_URL="${HAL0_INSTALL_URL:-https://hal0.dev/install.sh}"

# ── exit-code constants ─────────────────────────────────────────────────
EXIT_OK=0
EXIT_DRIFT=1
EXIT_OPERROR=2

err() { printf '%s\n' "$*" >&2; }

# ── preconditions ───────────────────────────────────────────────────────
if ! command -v curl >/dev/null 2>&1; then
    err "ERROR: curl is required but not found on PATH."
    exit "${EXIT_OPERROR}"
fi

if [[ ! -f "${LOCAL_FILE}" ]]; then
    err "ERROR: canonical in-tree installer not found at: ${LOCAL_FILE}"
    exit "${EXIT_OPERROR}"
fi

# ── fetch the live served copy ──────────────────────────────────────────
REMOTE_FILE="$(mktemp)"
cleanup() { rm -f "${REMOTE_FILE}"; }
trap cleanup EXIT

# -f  → fail (non-zero) on HTTP >= 400 instead of saving an error page.
# -sS → quiet, but still print transport errors to stderr.
# -L  → follow redirects (Cloudflare Pages may 30x to a canonical path).
# Retries smooth over transient blips so a single flap is not read as drift.
if ! curl -fsSL \
        --retry 3 --retry-delay 2 --retry-connrefused \
        --max-time 30 \
        -o "${REMOTE_FILE}" \
        "${INSTALL_URL}"; then
    err "ERROR: failed to fetch live installer from: ${INSTALL_URL}"
    err "       This is treated as an operational error, NOT drift —"
    err "       a transient outage should not fail the parity check as a mismatch."
    exit "${EXIT_OPERROR}"
fi

if [[ ! -s "${REMOTE_FILE}" ]]; then
    err "ERROR: fetched an EMPTY response from: ${INSTALL_URL}"
    err "       Treating as an operational error (NOT drift)."
    exit "${EXIT_OPERROR}"
fi

# ── compare ─────────────────────────────────────────────────────────────
# diff exit status: 0 = identical, 1 = differ, >1 = trouble.
if diff -u "${LOCAL_FILE}" "${REMOTE_FILE}" \
        --label "installer/bootstrap.sh (in-tree, canonical)" \
        --label "${INSTALL_URL} (live, served)"; then
    echo "OK: in-tree bootstrap.sh matches the live install.sh — no drift."
    exit "${EXIT_OK}"
fi

err ""
err "DRIFT: installer/bootstrap.sh differs from ${INSTALL_URL}."
err "       The audited in-tree copy and the live one-liner have diverged."
err "       Sync hal0-web:public/install.sh with hal0:installer/bootstrap.sh."
exit "${EXIT_DRIFT}"
