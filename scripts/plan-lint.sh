#!/usr/bin/env bash
# hal0 plan-lint — check the live tracker against the planning rules.
#
# The rules are in CONTRIBUTING.md, "Planning a release". This script reads
# GitHub through `gh` and changes nothing.
#
# Usage:
#   bash scripts/plan-lint.sh [--repo owner/name]
#
# Checks:
#   1. Every open release milestone has a due date or a "Date:" sentence, a
#      scope log, and (once it has a due date) a named tracking issue.
#   2. Every open issue has a milestone, unless it is still needs-triage.
#   3. Every open pull request has a milestone (bot pull requests excepted).
#   4. Every open issue has exactly one status label and, once triaged,
#      exactly one priority label.
#   5. A closed issue on an open release milestone shipped in that release:
#      the pull request that closed it carries the same milestone.
#   6. Every HAL0-SUNSET marker under src/ has an open issue on the milestone
#      it names, and that issue mentions the file.
#   7. Warning only: an open epic with no sub-issues.
#
# Exit status: 0 when no check fails, 1 otherwise. Warnings do not fail.
# A failed or cut-off read of the tracker also exits non-zero: every `gh`
# call is a checked assignment, so a failed request stops the script
# instead of looking like an empty, passing result.

set -euo pipefail
IFS=$'\n\t'

REPO=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "plan-lint: unknown argument: $1" >&2; exit 2 ;;
    esac
done

for tool in gh jq; do
    command -v "$tool" >/dev/null || { echo "plan-lint: $tool is required" >&2; exit 2; }
done
[[ -n "$REPO" ]] || REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
OWNER="${REPO%%/*}"
NAME="${REPO##*/}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

errors=0
warnings=0
fail() { echo "ERROR  $*"; errors=$((errors + 1)); }
warn() { echo "WARN   $*"; warnings=$((warnings + 1)); }

STATUS_RE='^(needs-triage|needs-info|ready-for-agent|ready-for-human|wontfix)$'
PRIORITY_RE='^P[0-2]$'
ISSUE_LIMIT=2000
PR_LIMIT=1000

# --- 1. milestones -----------------------------------------------------------
milestones="$(gh api --paginate "repos/$REPO/milestones?state=open&per_page=100" | jq -s 'add // []')"
while IFS=$'\t' read -r title due desc; do
    [[ "$title" == "Backlog" ]] && continue
    if [[ "$due" == "null" && "$desc" != *"Date:"* ]]; then
        fail "milestone $title has no due date and no \"Date:\" sentence"
    fi
    [[ "$desc" == *"Scope log"* ]] || fail "milestone $title has no scope log"
    if [[ "$due" != "null" && ! "$desc" =~ [Tt]racking\ issue:\ \#[0-9]+ ]]; then
        fail "milestone $title names no tracking issue"
    fi
done < <(jq -r '.[] | [.title, (.due_on // "null"), ((.description // "") | gsub("\\s+"; " "))] | @tsv' <<<"$milestones")

# --- 2 and 4. open issues ----------------------------------------------------
issues="$(gh issue list --repo "$REPO" --state open --limit "$ISSUE_LIMIT" --json number,labels,milestone)"
if [[ "$(jq length <<<"$issues")" -ge "$ISSUE_LIMIT" ]]; then
    fail "the open issue list reached $ISSUE_LIMIT entries and may be cut off; raise ISSUE_LIMIT"
fi
while IFS=$'\t' read -r number milestone status_count priority_count triage; do
    if [[ "$milestone" == "null" && "$triage" == "false" ]]; then
        fail "#$number is open with no milestone"
    fi
    [[ "$status_count" == "1" ]] || fail "#$number has $status_count status labels, expected 1"
    if [[ "$triage" == "false" && "$priority_count" != "1" ]]; then
        fail "#$number has $priority_count priority labels, expected 1"
    fi
done < <(jq -r --arg s "$STATUS_RE" --arg p "$PRIORITY_RE" '.[] | [
        .number,
        (.milestone.title // "null"),
        ([.labels[].name | select(test($s))] | length),
        ([.labels[].name | select(test($p))] | length),
        ([.labels[].name] | index("needs-triage") != null)
    ] | @tsv' <<<"$issues")

# --- 3. open pull requests ---------------------------------------------------
prs="$(gh pr list --repo "$REPO" --state open --limit "$PR_LIMIT" --json number,milestone,author)"
if [[ "$(jq length <<<"$prs")" -ge "$PR_LIMIT" ]]; then
    fail "the open pull request list reached $PR_LIMIT entries and may be cut off; raise PR_LIMIT"
fi
while read -r number; do
    [[ -n "$number" ]] || continue
    fail "pull request #$number is open with no milestone"
done < <(jq -r '.[] | select(.milestone == null and (.author.is_bot | not)) | .number' <<<"$prs")

# --- 5. closed issues shipped where their milestone says ---------------------
# shellcheck disable=SC2016  # GraphQL variables, not shell
shipped_query='query($owner:String!,$name:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$name){
  milestone(number:$number){issues(states:CLOSED,first:100,after:$endCursor){pageInfo{hasNextPage endCursor} nodes{number
    closedByPullRequestsReferences(first:10){nodes{number merged milestone{title}}}}}}}}'
# shellcheck disable=SC2016  # jq variables, not shell
shipped_filter='.data.repository.milestone.issues.nodes[] | .number as $n
    | .closedByPullRequestsReferences.nodes[] | select(.merged)
    | [$n, .number, (.milestone.title // "no milestone")] | @tsv'
while IFS=$'\t' read -r ms_number ms_title; do
    [[ "$ms_title" == "Backlog" ]] && continue
    shipped="$(gh api graphql --paginate -f query="$shipped_query" -F owner="$OWNER" -F name="$NAME" \
        -F number="$ms_number" --jq "$shipped_filter")"
    while IFS=$'\t' read -r number pr pr_ms; do
        [[ -n "$number" ]] || continue
        [[ "$pr_ms" == "$ms_title" ]] && continue
        fail "#$number is closed on $ms_title, but pull request #$pr that closed it is on $pr_ms"
    done <<<"$shipped"
done < <(jq -r '.[] | [.number, .title] | @tsv' <<<"$milestones")

# --- 6. sunset markers --------------------------------------------------------
while IFS=$'\t' read -r file version; do
    # A milestone that does not exist makes this call fail; that is reported
    # below as "no open issue mentions it", not as a broken run.
    bodies="$(gh issue list --repo "$REPO" --state open --milestone "$version" --limit 1000 --json body \
        --jq '.[].body' 2>/dev/null || true)"
    if ! grep -qF -- "$file" <<<"$bodies"; then
        fail "$file carries HAL0-SUNSET: $version, and no open issue on $version mentions it"
    fi
done < <(cd "$ROOT" && grep -rnoE 'HAL0-SUNSET: v[0-9]+\.[0-9]+(\.[0-9]+)?' src 2>/dev/null \
    | awk -F'[: ]+' '{print $1 "\t" $NF}' | sort -u)

# --- 7. epics without sub-issues ----------------------------------------------
# shellcheck disable=SC2016  # GraphQL variables, not shell
epic_query='query($owner:String!,$name:String!,$endCursor:String){repository(owner:$owner,name:$name){
  issues(states:OPEN,labels:["epic"],first:100,after:$endCursor){pageInfo{hasNextPage endCursor}
    nodes{number subIssuesSummary{total}}}}}'
epics="$(gh api graphql --paginate -f query="$epic_query" -F owner="$OWNER" -F name="$NAME" \
    --jq '.data.repository.issues.nodes[] | select(.subIssuesSummary.total == 0) | .number')"
while read -r number; do
    [[ -n "$number" ]] || continue
    warn "epic #$number has no sub-issues yet"
done <<<"$epics"

echo "plan-lint: $errors failing, $warnings warning(s) on $REPO"
[[ "$errors" -eq 0 ]]
