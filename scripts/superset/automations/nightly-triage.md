Triage hal0's open GitHub issues and keep the Linear mirror honest.

Ground rules (from `docs/agents/issue-tracker.md` and
`docs/agents/triage-labels.md`): GitHub Issues are the single source of
truth. Never create or resolve work in Linear alone — Linear (team "Hal0")
is a read-only mirror. The five canonical triage labels are `needs-triage`,
`needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`.

Do the following, in order:

1. List open GitHub issues labeled `needs-triage`:
   ```sh
   gh issue list --state open --label needs-triage \
     --json number,title,body,labels,comments,createdAt
   ```
2. For each one, decide whether it's:
   - **`ready-for-agent`**: fully specified, no open questions, a coding
     agent could pick it up as-is.
   - **`ready-for-human`**: needs judgment, design input, or access an agent
     doesn't have.
   - **`needs-info`**: missing repro steps, version, or other detail —
     comment asking for the specific gap.
   - **`wontfix`**: out of scope, duplicate, or already resolved — comment
     why before closing.
3. Apply the label with `gh issue edit <n> --add-label "<label>" --remove-label needs-triage`.
   Leave a one-line comment explaining the call.
4. Cross-check against the Linear mirror (team "Hal0", workspace
   `thinmintdev`) for anything that exists **only** in Linear. If found,
   create the matching GitHub issue (cross-reference the `HAL0-N`
   identifier in the body) before doing anything else with it — per
   `docs/agents/issue-tracker.md`, GitHub is canonical the moment an item is
   actionable.
5. Post a short summary as the automation's output: counts by new label,
   and a bullet per issue that changed state with a one-line reason. No
   need to restate issues that didn't move.

Stop after the summary — don't start implementation work in this run.
