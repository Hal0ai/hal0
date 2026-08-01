#!/usr/bin/env bash
# Ensure the Hal0 Linear team carries the same 5 canonical triage labels as
# GitHub (docs/agents/triage-labels.md). Idempotent -- safe to re-run.
#
# Requires LINEAR_API_KEY (see the linear-api skill / docs.superset.sh
# credentials aren't relevant here -- this talks to Linear directly). Not
# runnable from a Cowork sandbox until the Linear connector is authorized;
# run from an interactive session or any shell with a personal Linear API
# key exported.
set -euo pipefail

: "${LINEAR_API_KEY:?Set LINEAR_API_KEY first (personal API key or injected session credential).}"
TEAM_NAME="${LINEAR_TEAM_NAME:-Hal0}"

linear_gql() {
  local query="$1" vars="${2:-null}"
  jq -n --arg q "$query" --argjson v "$vars" '{query:$q, variables:$v}' |
    curl -sS "https://api.linear.app/graphql" \
      -H "Authorization: ${LINEAR_API_KEY}" \
      -H "Content-Type: application/json" \
      -d @-
}

check_errors() {
  local resp="$1" ctx="$2"
  if jq -e '.errors' >/dev/null 2>&1 <<<"$resp"; then
    echo "Linear API error during ${ctx}:" >&2
    jq '.errors' <<<"$resp" >&2
    exit 1
  fi
}

# --- Resolve the team by name (avoids hardcoding the team key). ---
teams_resp="$(linear_gql '{ teams(first: 50) { nodes { id key name } } }')"
check_errors "$teams_resp" "team lookup"

team_id="$(jq -r --arg name "$TEAM_NAME" '.data.teams.nodes[] | select(.name == $name) | .id' <<<"$teams_resp")"
team_key="$(jq -r --arg name "$TEAM_NAME" '.data.teams.nodes[] | select(.name == $name) | .key' <<<"$teams_resp")"

if [[ -z "$team_id" ]]; then
  echo "No Linear team named '${TEAM_NAME}' found. Teams visible to this key:" >&2
  jq -r '.data.teams.nodes[] | "  \(.key)  \(.name)"' <<<"$teams_resp" >&2
  exit 1
fi
echo "Team: ${TEAM_NAME} (${team_key}, ${team_id})"

# --- Canonical labels (name -> hex color, no leading #). ---
declare -A LABELS=(
  [needs-triage]="e2b203"
  [needs-info]="5e6ad2"
  [ready-for-agent]="4cb782"
  [ready-for-human]="0f7dd1"
  [wontfix]="9ca3af"
)

existing_resp="$(linear_gql 'query($id: String!) {
  team(id: $id) { labels(first: 200) { nodes { id name } } }
}' "$(jq -n --arg id "$team_id" '{id:$id}')")"
check_errors "$existing_resp" "label lookup"

for name in "${!LABELS[@]}"; do
  color="${LABELS[$name]}"
  existing_id="$(jq -r --arg n "$name" '.data.team.labels.nodes[] | select(.name == $n) | .id' <<<"$existing_resp")"
  if [[ -n "$existing_id" ]]; then
    echo "ok      ${name} (already exists)"
    continue
  fi
  create_resp="$(linear_gql 'mutation($input: IssueLabelCreateInput!) {
    issueLabelCreate(input: $input) { success issueLabel { id name } }
  }' "$(jq -n --arg teamId "$team_id" --arg name "$name" --arg color "#${color}" \
        '{input: {teamId: $teamId, name: $name, color: $color}}')")"
  check_errors "$create_resp" "creating label ${name}"
  ok="$(jq -r '.data.issueLabelCreate.success' <<<"$create_resp")"
  if [[ "$ok" == "true" ]]; then
    echo "created ${name}"
  else
    echo "FAILED  ${name}" >&2
    echo "$create_resp" >&2
    exit 1
  fi
done

echo "Done. Canonical labels: ${!LABELS[*]}"
