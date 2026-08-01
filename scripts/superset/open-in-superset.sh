#!/usr/bin/env bash
# Linear custom coding-tool script -> open the Linear issue as a Superset
# workspace on hal0. Installed at ~/.linear/open-in-superset.sh on your Mac;
# see scripts/superset/README.md for the install steps.
#
# Linear runs this as a local executable (docs.superset.sh/use-with-linear)
# and passes issue context through LINEAR_* env vars allowlisted in
# ~/.linear/coding-tools.json.
set -euo pipefail

# From `superset projects list` after `superset projects create --name hal0 ...`.
PROJECT="<hal0-superset-project-id>"

NAME="${LINEAR_ISSUE_IDENTIFIER:-linear-task}"
BRANCH="${LINEAR_ISSUE_BRANCH_NAME:-$NAME}"
PROMPT="${LINEAR_PROMPT:-Work on Linear issue ${NAME}. GitHub Issues are the source of truth for this repo (docs/agents/issue-tracker.md) -- find or create the matching GitHub issue and treat it as canonical before making changes.}"

# Make sure the local host service is running (idempotent).
superset start --daemon >/dev/null 2>&1 || true

result="$(
  superset workspaces create \
    --local \
    --project "$PROJECT" \
    --name "$NAME" \
    --branch "$BRANCH" \
    --agent claude \
    --prompt "$PROMPT" \
    --json
)"

id="$(printf '%s' "$result" | jq -r '.workspace.id')"

superset workspaces open "$id"
