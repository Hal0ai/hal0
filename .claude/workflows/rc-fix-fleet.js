export const meta = {
  name: 'rc-fix-fleet',
  description: 'Fix wave for validated release findings: one worktree-isolated agent per issue, batched, reviewed, PRs watched to a conclusion',
  whenToUse: 'After rc-validate has filed issues and the operator has explicitly asked for the fix wave. Takes {issues, repo, project}.',
  phases: [
    { title: 'Plan', detail: 'classify each issue: difficulty, tier, blast radius', model: 'opus' },
    { title: 'Fix', detail: 'batches of 6, one worktree per issue' },
    { title: 'Review', detail: 'independent reviewer per PR', model: 'opus' },
    { title: 'Land', detail: 'watch CI, merge, verify the merged tip' },
    { title: 'Changelog', detail: 'one consolidated CHANGELOG PR at the end' },
  ],
}

const A = args || {}
const ISSUES = A.issues || []
const REPO = A.repo                      // the worktree this session runs in (for reading the kit)
const PROJECT = A.project || REPO        // canonical checkout that owns the worktrees
const BATCH = A.batch || 6               // see the process lesson below — do not raise this casually
const LAND = A.land || 'watch'           // 'watch' = watch CI and merge; 'hold' = stop at green

if (!ISSUES.length) throw new Error('rc-fix-fleet requires args.issues, e.g. [1787, 1788]')
if (!REPO) throw new Error('rc-fix-fleet requires args.repo (path to the hal0 worktree)')

// Process lessons from the rc.4 fix wave. These are not style preferences; each one cost a
// rerun. Handed to every agent verbatim.
const LESSONS = `
PROCESS RULES (learned the hard way during the rc.4 fix wave — follow them exactly):
  - ONE issue per branch, one branch per worktree, one PR per branch. Never mix concerns.
  - Branch naming: fix/<slug>, feat/<slug>, chore/<slug>, docs/<slug>, refactor/<slug>.
  - DO NOT touch CHANGELOG.md. Per-PR CHANGELOG hunks re-conflict every remaining open branch on
    every merge (tracked as #1545). One consolidated CHANGELOG PR lands at the end of the wave.
  - Run the project's checks before pushing: tests, lint, AND format-check. CI runs
    \`ruff format --check\`, which \`make lint\` does not — a formatting-only red is a wasted
    cycle. CI also has no hermes available; do not add a test that needs it.
  - Write a regression test that fails before your fix and passes after. A fix with no test will
    be re-found by the next release's validation run.
  - Never force-push a shared branch, rewrite pushed history, or bypass branch protection.
  - Never automerge anything touching credentials, CI/CD configuration, infrastructure, or
    migrations — those get surfaced for human review.
  - NEVER MERGE YOUR OWN PR, and never enable automerge on it. Open it, get it green, stop there.
    Merging belongs to the Land phase, which runs only AFTER an independent reviewer has read your
    diff. This is the single most expensive lesson of the rc.5 wave: fix agents self-merged 4 PRs
    between 03:44 and 05:22 while the Review phase had not yet started, so the reviews were written
    against code already on main. 7 of 10 PRs came back changes-requested and 2 of those did not
    fix the reported defect at all — including one that introduced a fresh cache-poisoning
    regression. Green CI is not review. The wave's own GitOps autonomy rules ("enable automerge
    once checks are green") are SUSPENDED inside a fix wave for exactly this reason.
`

// ── phase 0: plan ────────────────────────────────────────────────────────────
phase('Plan')

const PLAN = {
  type: 'object',
  required: ['assignments'],
  properties: {
    assignments: {
      type: 'array',
      items: {
        type: 'object',
        required: ['issue', 'title', 'slug', 'tier', 'rationale'],
        properties: {
          issue: { type: 'number' },
          title: { type: 'string' },
          slug: { type: 'string', description: 'kebab-case branch slug' },
          tier: { enum: ['opus', 'sonnet'], description: 'never below sonnet for these' },
          branch_prefix: { enum: ['fix', 'feat', 'chore', 'docs', 'refactor'] },
          rationale: { type: 'string' },
          blast_radius: { type: 'string' },
          needs_human: { type: 'boolean', description: 'credentials, CI/CD, infra, or migrations' },
          conflicts_with: { type: 'array', items: { type: 'number' }, description: 'other issues touching the same files' },
        },
      },
    },
  },
}

const plan = await agent(`
You are planning a fix wave for hal0 release findings.

ISSUES: ${ISSUES.join(', ')}
REPO: ${REPO}

Read each issue with \`gh issue view\`, and read enough of the code to judge it. For each, decide:
  - the branch prefix and slug
  - the model tier: opus for architecture, security, cross-cutting refactors, and ambiguous
    debugging; sonnet for ordinary single-subsystem fixes. Never below sonnet.
  - blast radius: which subsystems and files the fix will touch
  - which OTHER issues in this wave touch the same files — those must not run in the same batch,
    because two worktrees editing the same file produce a merge train nobody can untangle
  - needs_human: true if it touches credentials, CI/CD config, infrastructure, or migrations

Order the assignments so that conflicting issues land in different batches and the highest-severity
fixes go first.

${LESSONS}`, { label: 'plan', phase: 'Plan', schema: PLAN, model: 'opus', effort: 'high' })

const assignments = ((plan && plan.assignments) || []).filter((a) => !a.needs_human)
const deferred = ((plan && plan.assignments) || []).filter((a) => a.needs_human)
if (deferred.length) log(`deferred to human review (credentials/CI/infra/migrations): ${deferred.map((d) => '#' + d.issue).join(', ')}`)
if (!assignments.length) return { aborted: 'nothing to fix autonomously', deferred }

// ── phase 1: fix, in batches ─────────────────────────────────────────────────
// Deliberately batched rather than run wide. A 12-wide fleet was killed twice by host restarts
// during the rc.4 wave, losing every uncommitted worktree both times. Batching bounds the loss.
phase('Fix')

const FIX = {
  type: 'object',
  required: ['issue', 'status', 'summary'],
  properties: {
    issue: { type: 'number' },
    status: { enum: ['pr-opened', 'no-change-needed', 'blocked'] },
    pr: { type: 'number' },
    branch: { type: 'string' },
    summary: { type: 'string' },
    root_cause: { type: 'string' },
    tests_added: { type: 'string' },
    checks: { type: 'string', description: 'exact commands run and their outcome' },
    blocked_reason: { type: 'string' },
  },
}

const fixes = []
for (let i = 0; i < assignments.length; i += BATCH) {
  const batch = assignments.slice(i, i + BATCH)
  log(`fix batch ${Math.floor(i / BATCH) + 1}: ${batch.map((a) => '#' + a.issue).join(', ')}`)
  const done = await parallel(batch.map((a) => () => agent(`
Fix hal0 issue #${a.issue}: ${a.title}

Canonical checkout: ${PROJECT}
Branch: ${a.branch_prefix || 'fix'}/${a.slug}   (create it from origin/main)
Blast radius from planning: ${a.blast_radius || 'unknown — determine it yourself'}

Work like this:
1. \`gh issue view ${a.issue}\` — read the full report including the repro and the validation
   evidence. The issue came from an agent driving a real box, so the repro is real; reproduce it
   locally or in a test before you change anything.
2. Find the ROOT cause, not the symptom. The rc.4 wave's most valuable fixes were the ones where
   the reported symptom was two layers above the actual defect. If the root cause is deeper or
   broader than the issue describes, fix the root cause and say so on the issue.
3. Write a failing test first, then make it pass.
4. Run the checks: tests, lint, and format-check. All green before you push.
5. Open a PR with \`gh pr create --fill --base main\`. Body: what changed, why, how it was
   verified, and anything deliberately left out of scope. Reference \`Closes #${a.issue}\`.

${LESSONS}

Return the schema. If the issue turns out not to reproduce or to be already fixed, that is a
legitimate outcome — status "no-change-needed" with your evidence, and comment on the issue.`,
    { label: `fix:#${a.issue}`, phase: 'Fix', schema: FIX, model: a.tier, isolation: 'worktree' })))
  fixes.push(...done.filter(Boolean))
}

const prs = fixes.filter((f) => f.status === 'pr-opened' && f.pr)
log(`${prs.length} PRs opened, ${fixes.filter((f) => f.status === 'blocked').length} blocked, ${fixes.filter((f) => f.status === 'no-change-needed').length} no-change-needed`)

// ── phase 2: review ──────────────────────────────────────────────────────────
phase('Review')

const REVIEW = {
  type: 'object',
  required: ['pr', 'verdict', 'findings'],
  properties: {
    pr: { type: 'number' },
    verdict: { enum: ['approve', 'changes-requested'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'detail'],
        properties: {
          severity: { enum: ['blocking', 'nit'] },
          file: { type: 'string' },
          detail: { type: 'string' },
        },
      },
    },
    fixes_the_reported_defect: { type: 'boolean' },
  },
}

const reviews = (await parallel(prs.map((p) => () => agent(`
Review PR #${p.pr} in Hal0ai/hal0 (fixes issue #${p.issue}).

You did not write this code and you are not here to be agreeable. Check, in order:
1. Does it actually fix the defect the issue reports? Read the issue's repro and confirm the diff
   addresses that exact path — not an adjacent one that merely looks similar.
2. Is the fix at the root cause or at the symptom? A symptom patch that leaves the cause in place
   will be re-found by the next validation run; say so.
3. Does the new test fail without the fix? Verify by reasoning about the test, not by trusting the
   PR description.
4. Regressions: what else calls the changed code? Does the change alter behaviour for callers the
   issue never mentioned?
5. Scope creep: unrelated changes, drive-by refactors, CHANGELOG.md hunks (which are forbidden in
   this wave — flag as blocking).
6. Also triage any Codex review-bot comments already on the PR: verify each claim before acting
   on it, then fix, file, or dismiss it deliberately. The bot can hit usage limits and post
   nothing at all — its silence is not an approval.

Post your review on the PR. Return the schema.`,
  { label: `review:#${p.pr}`, phase: 'Review', schema: REVIEW, model: 'opus', effort: 'high' })))).filter(Boolean)

const approved = reviews.filter((r) => r.verdict === 'approve').map((r) => r.pr)
const needsWork = reviews.filter((r) => r.verdict === 'changes-requested')
log(`review: ${approved.length} approved, ${needsWork.length} need changes`)

// ── phase 3: land ────────────────────────────────────────────────────────────
let landing = null
if (LAND === 'watch' && approved.length) {
  phase('Land')
  landing = await agent(`
Land the approved PRs: ${approved.map((n) => '#' + n).join(', ')}   (repo Hal0ai/hal0)

For each, in severity order:
1. Rebase on current main (never merge main back in — keep history linear).
2. Watch CI to a conclusion. Poll \`gh pr checks <n>\` rather than using --watch, which has been
   unreliable here. On a red check, read the failing job log and fix the cause on the branch;
   never re-run hoping it flakes green. If it genuinely is flaky, say so and point at the
   evidence.
3. \`gh pr merge <n> --squash --auto --delete-branch\` once green.
4. Note the automerge hazard: automerge deletes the branch on merge, so a push that lands after
   the merge orphans those commits. Confirm everything you intend to ship is pushed BEFORE
   enabling automerge on that PR.

CRITICAL — the merge train catches what per-branch CI cannot. During the rc.4 wave, two PRs each
green in isolation broke each other on the merged tip. So after every two or three merges, check
out the merged main, run the full test suite there, and fix any integration break on the next
branch before it merges. Report every such break you find.

Do not merge anything touching credentials, CI/CD configuration, infrastructure, or migrations —
surface those instead.

Return: what merged, what did not and why, and any integration break the merge train exposed.`,
    { label: 'land', phase: 'Land', model: 'opus', effort: 'high' })
}

// ── phase 4: consolidated changelog ──────────────────────────────────────────
let changelog = null
if (landing) {
  phase('Changelog')
  changelog = await agent(`
Write ONE consolidated CHANGELOG PR covering this fix wave.

Merged fixes: ${JSON.stringify(fixes.map((f) => ({ issue: f.issue, pr: f.pr, summary: f.summary })), null, 1)}
Landing report: ${typeof landing === 'string' ? landing.slice(0, 4000) : JSON.stringify(landing).slice(0, 4000)}

This is deliberately last and deliberately single: per-PR CHANGELOG hunks re-conflict every open
branch on every merge (#1545), which is why no fix PR in this wave touched the file.

Read CHANGELOG.md and match the existing section conventions exactly — the release workflow
parses this file, so structure matters:
  - the section heading must match the tag that will be cut
  - a preview-channel section needs its Audience / Known issues / Supported upgrades / Operator
    migrations / Rollback subsections as \`###\`, never \`##\` (an \`##\` terminates the
    extractor)
  - Breaking and Migrations bullets need a complete first physical line — the structured
    extractor takes only the unindented \`- \` line
  - no intra-changelog fragment links; use absolute GitHub URLs

One entry per user-visible change, written for a user rather than a reviewer, each referencing
its issue. Open the PR and return its number.`,
    { label: 'changelog', phase: 'Changelog', model: 'opus' })
}

return {
  issues: ISSUES,
  deferred_to_human: deferred.map((d) => ({ issue: d.issue, why: d.rationale })),
  fixes: fixes.map((f) => ({ issue: f.issue, status: f.status, pr: f.pr, root_cause: f.root_cause })),
  reviews: reviews.map((r) => ({ pr: r.pr, verdict: r.verdict, blocking: (r.findings || []).filter((x) => x.severity === 'blocking').length })),
  landing,
  changelog,
  next: 'Re-run rc-validate on a freshly installed build of the merged tip. Fixes verified only by CI are not verified.',
}
