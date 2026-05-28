# ADR 0018 — Upstream Hermes pin + weekly drift detection (v0.3)

- **Status:** Accepted
- **Date:** 2026-05-28
- **Drivers:** MASTER-PLAN.md §4 PR-12 + §5 "Upstream upgrade cadence"; DA-arch must-fix #1 ("Hermes is HOT upstream — ~40 commits today alone, `registry.ts` 151 LOC changes monthly")
- **Related:** ADR-0004 (agents v0.2 — `track-latest` mitigation, mirrored shape for the agent shim smoke job), ADR-0011 (Hermes identity card), ADR-0013 (MCP-client allow-list — the surfaces this pin protects)

## Context

v0.3 vendors a curated slice of Hermes-Agent into hal0 (system prompt
addendums, the cognee memory provider, persona definitions) and proxies
the rest through `hal0-api`. The integration depends on a small number
of upstream surfaces being stable between bumps:

- `web/src/plugins/registry.ts` — the `__HERMES_PLUGIN_SDK__` global
  the hal0 sidebar block + cognee provider both consume.
- `web/src/plugins/slots.ts` — plugin slot taxonomy; hal0 shim writes
  here too.
- `hermes_cli/web_server.py` — process lifecycle + WS endpoints the
  hal0-api PTY proxy taps.
- `agent/memory_provider.py` — the provider contract our vendored
  `memory_cognee` plugin extends.
- `tools/registry.py` — tool surface our MCP-client allow-list
  (ADR-0013) classifies into allow/gated/blocked.
- The events-bus emitter (taxonomy hal0's `/api/agents/<id>/events` WS
  bridge re-emits).

Upstream Hermes is **hot**: DA-arch #1 measured ~40 commits/day on
`main` and ~151 LOC/month churn in `registry.ts` alone. Without a
process:

1. The vendored slice silently drifts behind upstream API changes.
2. The `__HERMES_PLUGIN_SDK__` shim breaks when upstream renames or
   re-shapes a field; first symptom is a blank shadow-root plugin
   card in production.
3. The proxy passes through an upstream version that no longer
   matches the contract `/api/agents/hermes/*` clients depend on.

The current pin (snapshotted by MASTER-PLAN §5 as of 2026-05-28) is
**`0554ef1aa3a2e5818f292f76a676110239a5d34b`**. There is no
machine-readable record of this in the hal0 tree, no automated
detection of upstream drift, and no documented bump process.

## Options considered

| Option | Reason rejected (or accepted) |
|---|---|
| **Track upstream `main` (à la `agent-shim-smoke.yml`)** | Rejected. ADR-0004's track-latest worked for pi-coder because the contact surface is a CLI + one MCP handshake. Hermes' contact surface is six files, three of them in fast-moving Hermes plugin internals. Nightly breakage probability too high; the human cost (debugging which of six surfaces moved) is much higher than the cost of a weekly review. |
| **Pin to upstream tagged releases only** | Rejected for v0.3. Hermes upstream doesn't tag on a predictable cadence; gating on tags would block hotfix adoption. Revisit in v0.4 when cadence is established. |
| **Pin commit; Renovate/Dependabot for auto-PR on every upstream commit** | Rejected. Spammy at ~40 commits/day; signal-to-noise too low. The interesting events are surface-touching commits, not all commits. |
| **Pin commit; weekly diff against upstream HEAD; issue on contract drift** | ACCEPTED. Coalesces noise; signal is "did anything hal0 cares about change in the last week?"; issue is the human gate. |
| **Embed pin in `manifest.json`** | Rejected. `manifest.json` is the toolbox-image registry schema (`hal0.manifest.v1`), keyed by short image name. Repurposing for Python upstream pins muddies its schema + couples manifest-bumping CI to upstream-bumping CI. |
| **Embed pin in `pyproject.toml` under `[tool.hal0.upstream-hermes]`** | ACCEPTED. Matches DA-arch #1's specific recommendation (`pyproject [tool.hal0.upstream]` lock file). One place, one parser, already in every contributor's tree. Forward-compatible with future `[tool.hal0.upstream-<thing>]` siblings. |

## Decision

### 1. Pin recorded in `pyproject.toml`

A new `[tool.hal0.upstream-hermes]` table holds the pinned commit
plus the list of upstream files hal0 depends on. Schema:

```toml
[tool.hal0.upstream-hermes]
# Hermes-Agent upstream commit hash hal0 v0.3 is vendored/shimmed against.
# Bump process: ADR-0018 §4. Do not edit by hand outside the bump PR.
repo   = "https://github.com/earendil-works/hermes-agent"
commit = "0554ef1aa3a2e5818f292f76a676110239a5d34b"
date   = "2026-05-28"

# Surfaces hal0 vendors, shims, or proxies. The weekly hermes-sdk-diff
# job diffs upstream HEAD against `commit` for exactly these paths and
# opens an issue if any of them changed.
tracked_files = [
    "web/src/plugins/registry.ts",
    "web/src/plugins/slots.ts",
    "hermes_cli/web_server.py",
    "agent/memory_provider.py",
    "tools/registry.py",
    "agent/events.py",
]
```

`scripts/hermes-sdk-diff.sh` and `.github/workflows/hermes-sdk-diff.yml`
read this table as the single source of truth. `manifest.json` stays
toolbox-only.

### 2. Weekly upstream drift detection

A new `hermes-sdk-diff` workflow runs **weekly on Mondays at 12:00 UTC**
(`workflow_dispatch` also enabled for on-demand review). It:

1. Reads `commit` + `tracked_files` from `[tool.hal0.upstream-hermes]`.
2. Sparse-clones upstream Hermes into a temp dir at HEAD.
3. For each tracked file, `git diff <pinned-commit>..<upstream-head> --
   <file>` into a per-file diff section.
4. If every diff is empty, logs "no drift" and exits 0 — no issue, no
   noise.
5. If any diff is non-empty, opens (or comments on) a single tracking
   issue labeled `upstream-drift` + `triage` with title
   `chore(hermes): upstream drift detected (<pinned-short> →
   <head-short>)`. Body carries a per-surface summary + a link to the
   workflow run. One open issue per drift state — the workflow attaches
   to the existing issue if it's still open, same shape as
   `agent-shim-smoke.yml`'s `notify` job.

Concurrency: `cancel-in-progress: true` on the `hermes-sdk-diff`
group. We never queue back-to-back weekly runs.

Permissions: `contents: read` + `issues: write`.

### 3. Local equivalent

`scripts/hermes-sdk-diff.sh` is the script the workflow calls.
Operators (and curious contributors) can run it locally with the same
contract:

```
scripts/hermes-sdk-diff.sh           # diff + print to stdout; exit 1 on drift
scripts/hermes-sdk-diff.sh --dry-run # parse the pin, print plan, exit 0
scripts/hermes-sdk-diff.sh --bump <new-sha>
                                     # rewrite the pin in pyproject.toml,
                                     # update `date`, exit 0; intended
                                     # to be run inside the bump PR.
```

The script clones upstream into a workdir under `$TMPDIR` (override
with `HAL0_HERMES_DIFF_WORKDIR`), so it works on an air-gapped LXC
just like everything else in `scripts/`.

### 4. Bump process

When a `upstream-drift` issue is filed:

1. Reviewer reads the issue, opens the surfaces upstream changed,
   classifies the change (rename / shape change / new field /
   refactor-without-contract-change).
2. If a shim adapter (e.g., `__HERMES_PLUGIN_SDK__` field map) needs
   updating, that's the same PR.
3. Run the local script with `--bump <new-sha>` to rewrite the pin and
   the `date` field in `pyproject.toml`.
4. Run the δ-harness (`make harness`) + γ-suite locally to confirm the
   adapter still satisfies the integration contract.
5. Open a PR titled `chore(hermes): bump upstream pin to <short-sha>`
   targeting the active v0.x integration branch. PR body links the
   triage issue + lists the adapter changes (if any).
6. Closing comment on the `upstream-drift` issue lists the merged PR
   and a one-line summary of what shifted.

### 5. Freeze window

**No upstream-pin bump is merged within 48h of any `v0.x*` release
tag** on `Hal0ai/hal0`. Rationale: post-release hours are reserved
for hotfixes on the released version; introducing a new upstream-pin
delta at the same time produces ambiguous blame on regressions
("did the hotfix break it or did the pin?"). The freeze is enforced
by reviewer discipline rather than a CI gate — the PR template
includes a checkbox for "I confirm no v0.x tag was pushed in the
last 48h."

### 6. Out of scope (v0.3)

- **Renovate/Dependabot integration.** Considered; deferred. The
  weekly-diff-to-issue workflow is the v0.3 contract. Revisit if the
  drift cadence makes a manual review burdensome.
- **Tagged-release-only policy.** Revisit in v0.4 once upstream tag
  cadence is observable.
- **Auto-rebump on adapter-clean diff.** Tempting but rejected for v0.3
  — every bump deserves a human eye on at least the diff summary.
- **Eval suite for upstream-bump regressions.** Same scope/cadence
  story as ADR-0014's eval gate (v0.4 deliverable, tracked separately).

## Consequences

### Positive

- Closes DA-arch must-fix #1 with a concrete artifact (pin + workflow
  + script) rather than a process commitment.
- Pin is machine-readable from `pyproject.toml` — `scripts/`,
  CI, contributors, and future tooling read from one source.
- Weekly cadence keeps the human gate cheap (one issue per week, max)
  while still surfacing breakage at most 7 days after upstream
  introduces it.
- One open tracking issue per drift state (same shape as
  `agent-shim-smoke.yml`) — no PR/issue spam during quiet weeks.
- The `--bump` subcommand makes the bump PR mechanical:
  `--bump <sha>` + adapter edit + tests + commit.

### Negative / costs

- 7-day worst-case latency on detecting a surface change is a real
  trade-off vs nightly runs. Acceptable because the affected
  components are vendored or shimmed in hal0 — a broken shim doesn't
  break running installs until hal0 itself bumps the pin.
- The freeze window is reviewer-disciplined, not CI-enforced. A
  determined contributor can merge during a freeze window. Mitigation:
  the PR template checkbox + an explicit ADR §5 paragraph.
- Two places carry the upstream commit (pyproject + ADR text). The
  ADR's `currently 0554ef1aa3a...` line is documentary — the
  machine-readable pin is the source of truth, and the ADR text
  references it as "see `[tool.hal0.upstream-hermes].commit`". No
  automation reads the ADR text.
- Sparse-clone in the workflow assumes upstream stays accessible on
  GitHub. If upstream moves repos, the workflow needs a config
  update — caught the same way as any other URL rot.

## Pending items

- `scripts/hermes-sdk-diff.sh` and `.github/workflows/hermes-sdk-diff.yml`
  ship in this same PR.
- `[tool.hal0.upstream-hermes]` table added to `pyproject.toml` in
  this same PR.
- PR template entry for the 48h freeze checkbox — follow-up issue.
- First weekly run happens the Monday after merge; the
  `upstream-drift` GitHub label is created on first issue creation.

## References

- MASTER-PLAN.md §4 PR-12, §5 "Upstream upgrade cadence" (in
  `hermes-research-2026-05-28/`)
- DA-arch must-fix #1, R2 vendor/shim findings (in
  `hermes-research-2026-05-28/plans/da-arch-perf.md`)
- ADR-0004 §3 ("Mitigation for track-latest churn") — the prior-art
  shape for "weekly CI + one open issue per drift state"
- `.github/workflows/agent-shim-smoke.yml` — implementation prior-art
  for the `notify` job pattern
- Hermes upstream: `https://github.com/earendil-works/hermes-agent`
