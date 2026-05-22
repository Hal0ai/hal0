#!/bin/sh
# hal0 — pi-mono fork sync (one-shot, repeatable).
#
# Syncs the Hal0ai/pi-mono hard fork from its upstream parent
# (earendil-works/pi, formerly badlogic/pi-mono). Run from a workstation
# with `gh` authed against an account that can push to Hal0ai.
#
# Why we hard-forked:
#   - hal0 does not have write access to badlogic/pi-mono / earendil-works/pi.
#   - Owning our integration surface lets us pin / patch / mirror without
#     coordinating with an external maintainer (ADR-0004 §3 track-latest
#     mitigation pressure-relief valve).
#   - Symmetric with the Hermes-Agent upstream-owned integration: pi side
#     is hal0-owned all the way down.
#
# Default branch is detected at runtime via the GitHub API so an upstream
# rename (main → master, default flip, etc.) doesn't silently break the
# sync.
#
# Usage:
#   bash scripts/fork-pi-mono.sh
#
# Exit codes:
#   0  fork in sync with upstream
#   1  gh CLI missing / not authed
#   2  fork repository not reachable
#   3  default branch detection failed
#   4  gh repo sync failed

set -eu

FORK="Hal0ai/pi-mono"

info() { printf '[fork-pi-mono] %s\n' "$*" >&2; }
warn() { printf '[fork-pi-mono] WARN: %s\n' "$*" >&2; }
die()  { code="$1"; shift; printf '[fork-pi-mono] ERROR: %s\n' "$*" >&2; exit "$code"; }

command -v gh >/dev/null 2>&1 \
    || die 1 "gh CLI not found on PATH — install https://cli.github.com/ first"

gh auth status >/dev/null 2>&1 \
    || die 1 "gh CLI not authenticated — run \`gh auth login\` first"

info "fork:    https://github.com/${FORK}"
info "probing fork metadata"

if ! META="$(gh api "repos/${FORK}" 2>&1)"; then
    die 2 "cannot read repos/${FORK} — fork missing or token lacks access: ${META}"
fi

DEFAULT_BRANCH="$(printf '%s' "$META" | gh api "repos/${FORK}" --jq .default_branch 2>/dev/null || true)"
if [ -z "$DEFAULT_BRANCH" ]; then
    die 3 "could not detect default branch for ${FORK}"
fi

PARENT="$(gh api "repos/${FORK}" --jq .parent.full_name 2>/dev/null || true)"
if [ -n "$PARENT" ]; then
    info "upstream parent: ${PARENT}"
else
    warn "fork has no recorded parent — sync may no-op"
fi

info "default branch:  ${DEFAULT_BRANCH}"
info "syncing ${FORK}:${DEFAULT_BRANCH} from upstream"

if ! OUT="$(gh repo sync "${FORK}" -b "${DEFAULT_BRANCH}" 2>&1)"; then
    printf '%s\n' "$OUT" >&2
    die 4 "gh repo sync failed for ${FORK}:${DEFAULT_BRANCH}"
fi

printf '%s\n' "$OUT" >&2
info "sync complete"
exit 0
