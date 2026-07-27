#!/usr/bin/env bash
#
# Linear -> Superset bridge.
#
# Registered in Linear as a "custom coding tool" (Settings -> Features ->
# Custom coding tools). Linear invokes it with the issue's details in the
# environment, and it creates (or reuses) a Superset workspace on the issue's
# branch, spawns a coding agent primed with the issue context, and opens the
# workspace in the Superset desktop app.
#
# Linear-supplied environment (only the first two are relied on):
#   LINEAR_ISSUE_IDENTIFIER   e.g. MINT-68
#   LINEAR_ISSUE_BRANCH_NAME  e.g. thinmint/mint-68-benchmarks-hardcode-devdri
#   LINEAR_ISSUE_TITLE        optional, used in the agent prompt
#   LINEAR_ISSUE_URL          optional, used in the agent prompt
#   LINEAR_ISSUE_DESCRIPTION  optional, used in the agent prompt
#   LINEAR_TEAM_KEY           optional, routes to a per-team project
#
# Local overrides:
#   SUPERSET_PROJECT_ID   Superset project to create the workspace in
#   SUPERSET_AGENT        agent preset id (claude, codex, amp, ...) or "none"
#   SUPERSET_BASE_BRANCH  branch to fork from when the issue branch is new
#   SUPERSET_API_KEY      API key; falls back to ~/.config/superset/api-key
#   SUPERSET_NO_OPEN      set to 1 to skip opening the desktop app (headless)
#
set -euo pipefail

log() { printf '[superset-linear] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ---------------------------------------------------------------- resolve CLI
SUPERSET_BIN="${SUPERSET_BIN:-}"
if [[ -z $SUPERSET_BIN ]]; then
	for candidate in "$HOME/.superset/bin/superset" "$(command -v superset 2>/dev/null || true)"; do
		[[ -n $candidate && -x $candidate ]] && SUPERSET_BIN=$candidate && break
	done
fi
[[ -n $SUPERSET_BIN ]] || die "superset CLI not found (looked in ~/.superset/bin and \$PATH)"

# ------------------------------------------------------------------- auth
if [[ -z ${SUPERSET_API_KEY:-} && -r "$HOME/.config/superset/api-key" ]]; then
	SUPERSET_API_KEY=$(<"$HOME/.config/superset/api-key")
	export SUPERSET_API_KEY
fi
# Not fatal when absent — the CLI falls back to its OAuth login.

superset() { "$SUPERSET_BIN" "$@"; }

# Resolve a workspace id by branch, or print nothing.
find_workspace_by_branch() {
	superset workspaces list --local --json 2>/dev/null |
		jq -r --arg branch "$1" --arg project "$2" \
			'[.[] | select(.projectId == $project and .branch == $branch)][0].id // empty'
}

# ------------------------------------------------------------------- inputs
IDENTIFIER="${LINEAR_ISSUE_IDENTIFIER:-}"
BRANCH="${LINEAR_ISSUE_BRANCH_NAME:-}"
[[ -n $IDENTIFIER ]] || die "LINEAR_ISSUE_IDENTIFIER is not set — is this running as a Linear custom coding tool?"
[[ -n $BRANCH ]] || die "LINEAR_ISSUE_BRANCH_NAME is not set for $IDENTIFIER"

TITLE="${LINEAR_ISSUE_TITLE:-}"
URL="${LINEAR_ISSUE_URL:-}"
DESCRIPTION="${LINEAR_ISSUE_DESCRIPTION:-}"

# Route per Linear team when several teams share this host. Default is hal0.
case "${LINEAR_TEAM_KEY:-}" in
	*) PROJECT_ID="${SUPERSET_PROJECT_ID:-1bc59c2e-5f50-40fd-9953-e540883c03e8}" ;;
esac

AGENT="${SUPERSET_AGENT:-claude}"
BASE_BRANCH="${SUPERSET_BASE_BRANCH:-main}"

# ------------------------------------------------- reuse an existing workspace
# `workspaces create` would happily make a second workspace on the same branch,
# so match on branch first and reopen instead of duplicating.
existing=$(find_workspace_by_branch "$BRANCH" "$PROJECT_ID")

if [[ -n $existing ]]; then
	log "reusing workspace $existing for $IDENTIFIER ($BRANCH)"
	WORKSPACE_ID=$existing
else
	# The issue branch usually does not exist yet; `create` forks it from
	# --base-branch automatically.
	prompt="You are working on Linear issue ${IDENTIFIER}."
	[[ -n $TITLE ]] && prompt+=$'\n\nTitle: '"$TITLE"
	[[ -n $URL ]] && prompt+=$'\nLinear: '"$URL"
	[[ -n $DESCRIPTION ]] && prompt+=$'\n\n--- Issue description ---\n'"$DESCRIPTION"
	prompt+=$'\n\nThe branch '"$BRANCH"' is checked out for you. Read the repo'
	prompt+=' conventions in CLAUDE.md and AGENTS.md before making changes, and'
	prompt+=' follow the owner_class discipline in docs/rework/REWORK_BOARD.md.'
	prompt+=' Do not commit or push unless asked.'

	create_args=(
		workspaces create
		--local
		--project "$PROJECT_ID"
		--name "$IDENTIFIER"
		--branch "$BRANCH"
		--base-branch "$BASE_BRANCH"
	)
	if [[ $AGENT != none ]]; then
		create_args+=(--agent "$AGENT" --prompt "$prompt")
	fi

	log "creating workspace for $IDENTIFIER on $BRANCH (fork from $BASE_BRANCH, agent=$AGENT)"

	# `create` can write the workspace record and still exit non-zero: it
	# verifies against the host daemon before the record is indexed there
	# ("Workspace not found on host <id>"). The worktree does get made, so
	# treat a failed exit as inconclusive and poll for the record instead of
	# trusting the status code.
	create_out=$(superset "${create_args[@]}" --json 2>&1) || true
	WORKSPACE_ID=$(jq -r '.workspace.id // .id // empty' <<<"$create_out" 2>/dev/null || true)

	if [[ -z $WORKSPACE_ID ]]; then
		# Fall back to the id embedded in the error payload, then to a poll.
		WORKSPACE_ID=$(grep -oE '"id":"[0-9a-f-]{36}"' <<<"$create_out" | head -n1 | cut -d'"' -f4 || true)
	fi
	for _ in 1 2 3 4 5; do
		[[ -n $WORKSPACE_ID ]] && break
		sleep 1
		WORKSPACE_ID=$(find_workspace_by_branch "$BRANCH" "$PROJECT_ID")
	done

	if [[ -z $WORKSPACE_ID ]]; then
		log "create output was: $create_out"
		die "workspace creation returned no id for $IDENTIFIER"
	fi
fi

# ------------------------------------------------------------------- open
open_args=(workspaces open "$WORKSPACE_ID")
[[ ${SUPERSET_NO_OPEN:-0} == 1 ]] && open_args+=(--print)

# Same host-indexing race as create — retry briefly before giving up.
opened=0
for attempt in 1 2 3 4 5; do
	if superset "${open_args[@]}"; then
		opened=1
		break
	fi
	log "open attempt $attempt failed; retrying"
	sleep 1
done

if [[ $opened == 0 ]]; then
	log "workspace $WORKSPACE_ID exists but could not be opened; open it from the desktop app"
fi

log "$IDENTIFIER -> workspace $WORKSPACE_ID"
