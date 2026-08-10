export const meta = {
  name: 'rc-validate',
  description: 'Versioned release-candidate validation: preflight, lane sweep, regression probes, adversarial verification, report',
  whenToUse: 'Validating a hal0 release candidate on the test fleet. Reset and install the RC on the boxes first, then run this with {version, boxes, mode, repo}.',
  phases: [
    { title: 'Preflight', detail: 'per-box readiness gate, writes CONTEXT.md' },
    { title: 'Sweep', detail: 'read-only lanes, stateful lanes, update lanes, regression probes' },
    { title: 'Triage', detail: 'dedup, drop known issues, rank candidates', model: 'opus' },
    { title: 'Verify', detail: 'one skeptic per candidate finding', model: 'opus' },
    { title: 'Report', detail: 'synthesis into the committed report', model: 'opus' },
    { title: 'File', detail: 'GitHub issue per confirmed finding' },
    { title: 'Curate', detail: 'fold what we learned back into the kit' },
  ],
}

// ── args ─────────────────────────────────────────────────────────────────────
const A = args || {}
const VERSION = A.version
const MODE = A.mode || 'file'                    // 'report' | 'file'
const REPO = A.repo || process_cwd_placeholder() // see below
const BOX_IDS = A.boxes && A.boxes.length ? A.boxes : ['ct151-cpu-fresh']
const ONLY = A.lanes && A.lanes.length ? A.lanes : null

function process_cwd_placeholder() {
  // Workflow scripts have no filesystem or process access. The repo path must be passed in;
  // agents are told to fail loudly rather than guess if it is missing.
  return '<REPO PATH NOT PROVIDED — locate the hal0 worktree yourself and say so in your report>'
}

if (!VERSION) throw new Error('rc-validate requires args.version, e.g. "1.0.0-rc.5"')

const KIT = `${REPO}/tests/release-validation`
const RUN = `/mnt/mintdev/artifacts/hal0-release-validation/${VERSION}`

// Box id -> role. Mirrors tests/release-validation/boxes.toml.
const BOX_ROLES = {
  'ct151-cpu-fresh': 'fresh',
  'ct163-cpu-fresh': 'fresh-alt',
  'ct150-update': 'update',
  'ct105-prod': 'prod',
}

// Lane registry. MIRRORS tests/release-validation/kit.toml — keep both in sync.
const LANES = {
  readonly: ['api', 'cli', 'mcp', 'hermes', 'ui'],
  stateful: ['slots', 'routing', 'brain', 'memory', 'capabilities', 'hermes-e2e'],
  update: ['upgrade', 'post-upgrade'],
}
const briefPath = (kind, key) => `${KIT}/lanes/${kind === 'readonly' ? 'readonly' : kind}/${key}.md`
const wanted = (key) => !ONLY || ONLY.includes(key)

// ── schemas ──────────────────────────────────────────────────────────────────
const FINDING = {
  type: 'object',
  required: ['title', 'severity', 'evidence', 'repro'],
  properties: {
    title: { type: 'string' },
    severity: { enum: ['critical', 'major', 'minor', 'cosmetic'] },
    evidence: { type: 'string', description: 'exact command plus the shortest decisive output line' },
    repro: { type: 'string', description: 'steps another agent can run blind' },
    surface: { type: 'string', description: 'cli | api | ui | mcp | hermes | brain | memory | slots | update' },
    user_impact: { type: 'string' },
  },
}

const LANE_RESULT = {
  type: 'object',
  required: ['lane', 'box', 'tested', 'findings'],
  properties: {
    lane: { type: 'string' },
    box: { type: 'string' },
    tested: {
      type: 'array',
      items: {
        type: 'object',
        required: ['check', 'result'],
        properties: {
          check: { type: 'string' },
          result: { enum: ['pass', 'fail', 'warn', 'known', 'skipped'] },
          note: { type: 'string' },
        },
      },
    },
    findings: { type: 'array', items: FINDING },
    box_state_on_exit: { type: 'string' },
    new_checks_worth_keeping: {
      type: 'array',
      items: { type: 'string' },
      description: 'checks you invented that are not in the brief and should be next release',
    },
  },
}

const PREFLIGHT = {
  type: 'object',
  required: ['box', 'ready', 'observed_version', 'summary'],
  properties: {
    box: { type: 'string' },
    ready: { type: 'boolean' },
    observed_version: { type: 'string' },
    blocker: { type: 'string', description: 'why the run cannot proceed on this box' },
    summary: { type: 'string' },
    context_path: { type: 'string' },
    findings: { type: 'array', items: FINDING },
  },
}

const REGRESSION_RESULT = {
  type: 'object',
  required: ['results'],
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'issue', 'result', 'evidence'],
        properties: {
          id: { type: 'string' },
          issue: { type: 'number' },
          result: { enum: ['fixed', 'regressed', 'partial', 'blocked'] },
          evidence: { type: 'string' },
          detail: { type: 'string' },
        },
      },
    },
  },
}

const TRIAGE = {
  type: 'object',
  required: ['candidates', 'dropped'],
  properties: {
    candidates: {
      type: 'array',
      items: {
        type: 'object',
        required: ['key', 'title', 'claim', 'severity_claimed', 'evidence', 'repro'],
        properties: {
          key: { type: 'string', description: 'short kebab-case slug' },
          title: { type: 'string' },
          claim: { type: 'string', description: 'the merged claim across every lane that saw it' },
          severity_claimed: { enum: ['critical', 'major', 'minor', 'cosmetic'] },
          evidence: { type: 'string' },
          repro: { type: 'string' },
          lanes: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    dropped: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'reason'],
        properties: { title: { type: 'string' }, reason: { type: 'string' } },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['key', 'verdict', 'classification', 'tightened_claim', 'evidence'],
  properties: {
    key: { type: 'string' },
    verdict: { enum: ['confirmed', 'refuted', 'partially-confirmed'] },
    classification: { enum: ['ga-blocker', 'major', 'minor', 'cosmetic', 'by-design'] },
    tightened_claim: { type: 'string', description: 'exactly what reproduces, no more' },
    evidence: { type: 'string' },
    other_hardware_applicability: { type: 'string', description: 'would a GPU box / privileged container / upgraded box hit this?' },
    by_design_rationale: { type: 'string', description: 'if by-design: why, and the still_report_if clause for known-issues.yaml' },
  },
}

// ── shared prompt preamble ───────────────────────────────────────────────────
const BASE = `
You are one agent in the hal0 release-candidate validation suite.

VERSION UNDER TEST: ${VERSION}
KIT (versioned, in-repo): ${KIT}
RUN DIRECTORY (scratch, not in the repo): ${RUN}

Read these before doing anything, in this order:
  1. ${RUN}/CONTEXT.md            — live facts for this run. Beats everything else.
  2. ${KIT}/lanes/_shared.md      — reporting rules, severity, box discipline.
  3. ${KIT}/known-issues.yaml     — do not re-report anything listed there.

If ${KIT} does not exist at that path, stop and say so — do not guess a different path.
`

// ── phase 0: preflight ───────────────────────────────────────────────────────
phase('Preflight')

const preflights = await parallel(BOX_IDS.map((boxId) => () => agent(`
You are the PREFLIGHT agent for box ${boxId} (role: ${BOX_ROLES[boxId] || 'unknown'}).

VERSION UNDER TEST: ${VERSION}
KIT: ${KIT}   RUN DIRECTORY: ${RUN}

Read ${KIT}/boxes.toml for this box's access details, hardware, reset procedure, and gotchas,
and ${KIT}/lanes/_shared.md for the reporting rules.

Your job is to establish ground truth and gate the run. Do it read-only except for writing your
own CONTEXT file.

1. Reach the box over SSH and confirm the API answers.
2. Confirm the INSTALLED VERSION IS ${VERSION}. If it is not, set ready=false with a blocker —
   validating the wrong build wastes the entire run. For an update-role box the installed
   version should still be the PREVIOUS release (the upgrade lane performs the upgrade); record
   the exact from-version and treat that as correct.
3. Confirm every fact in boxes.toml for this box is still true (IP, OS, privileged, GPU, cores,
   RAM, auth posture). Report each stale fact as a finding — a stale kit is a real defect in the
   kit.
4. Inventory: registered models and their sizes, every slot with its assigned model and state,
   running units, disk headroom in the model store, and whether any slot is already failed.
5. Note anything about the FRESH-INSTALL STATE that a user would hit — this is the highest-value
   observation window in the whole run and it closes as soon as the stateful lanes start
   mutating. Record the installer's own output/log if it is still on the box.
6. Write ${RUN}/CONTEXT.md (create the directory). It must contain, concretely:
   - version under test, box id, hostname, IP, API base URL, exact SSH invocation
   - hardware and OS, whether this box has a GPU, and the backend that applies
   - the staged test models by id, type, size, and which are known-good on this hardware
   - current slot state at run start
   - per-lane time budgets from ${KIT}/kit.toml
   - the box's gotchas from boxes.toml, restated as operational instructions
   - for update boxes: the from-version and the release-staging configuration
   Write it for an agent that knows nothing about this fleet. Every later agent depends on it.

Return the schema. Set ready=false only for something that genuinely invalidates the run.
`, { label: `preflight:${boxId}`, phase: 'Preflight', schema: PREFLIGHT })))

const ok = preflights.filter(Boolean)
const blocked = ok.filter((p) => !p.ready)
if (blocked.length || ok.length !== BOX_IDS.length) {
  log(`preflight gate failed — ${blocked.length} box(es) not ready, ${BOX_IDS.length - ok.length} agent failure(s)`)
  return { aborted: 'preflight', preflights: ok }
}
log(`preflight ok on ${ok.map((p) => p.box).join(', ')} — version ${ok.map((p) => p.observed_version).join(' / ')}`)

// ── phase 1: lanes ───────────────────────────────────────────────────────────
// Read-only lanes, the serialised stateful chain, the serialised update chain, and the
// regression probes all run concurrently. The only ordering constraint in the whole suite is
// that stateful work on a SINGLE box must be serialised — two agents mutating one install
// produce findings neither can reproduce.
phase('Sweep')

const freshBox = BOX_IDS.find((b) => BOX_ROLES[b] === 'fresh' || BOX_ROLES[b] === 'fresh-alt')
const updateBox = BOX_IDS.find((b) => BOX_ROLES[b] === 'update')

function laneAgent(kind, key, box, extra, opts) {
  return agent(`${BASE}
YOUR LANE: ${key} (${kind}) on box ${box}
YOUR BRIEF: ${briefPath(kind, key)} — read it now and work through it.
${extra || ''}
Write your raw notes to ${RUN}/lanes/${box}-${key}.md as you go, then return the schema.
Set box="${box}" and lane="${key}".`, Object.assign({ label: `${kind}:${key}`, phase: 'Sweep', schema: LANE_RESULT }, opts || {}))
}

// serialised chain: each stage is handed the previous stage's box_state_on_exit verbatim
async function chain(kind, keys, box) {
  const out = []
  let prev = 'as left by preflight — see CONTEXT.md'
  for (const key of keys) {
    if (!wanted(key)) continue
    const r = await laneAgent(kind, key, box,
      `\nBOX STATE LEFT BY THE PREVIOUS STAGE (trust this over CONTEXT.md where they differ):\n${prev}\n`,
      { effort: 'high' })
    if (r) { out.push(r); prev = r.box_state_on_exit || prev }
    else log(`${kind}:${key} returned nothing — later stages inherit the last known box state`)
  }
  return out
}

const work = []

if (freshBox) {
  for (const key of LANES.readonly) {
    if (wanted(key)) work.push(() => laneAgent('readonly', key, freshBox))
  }
  work.push(() => chain('stateful', LANES.stateful, freshBox))
}
if (updateBox) {
  work.push(() => chain('update', LANES.update, updateBox))
}

// Regression probes. Split by tier so the mechanical ones run cheap: the fast-bulk tier can
// run a documented repro and compare against `expect`, but anything needing source reading or
// intent judgement stays on the workhorse tier.
const REG_BATCHES = [
  { tier: 'mechanical', model: 'fable', desc: 'entries with tier: mechanical' },
  { tier: 'judgment', desc: 'entries with tier: judgment' },  // no model override: inherit session tier
]
if (freshBox && wanted('regressions')) {
  for (const b of REG_BATCHES) {
    const regOpts = Object.assign(
      { label: `regressions:${b.tier}`, phase: 'Sweep', schema: REGRESSION_RESULT },
      b.model ? { model: b.model } : {},
    )
    work.push(() => agent(`${BASE}
YOU RUN THE REGRESSION PROBES (${b.desc}) on box ${freshBox}.

Read ${KIT}/regressions.yaml. Take EVERY entry whose tier is "${b.tier}" and run its \`repro\`
against this box, comparing what you observe to its \`expect\`.

These are defects filed against a previous release and fixed since. They were fixed against unit
tests and CI, not against a fresh end-to-end install — proving they are actually gone on a real
box is the entire point of this phase.

Rules:
  - Use the result vocabulary defined in the file: fixed | regressed | partial | blocked.
  - "blocked" is honest and useful. Reporting a probe you could not run as "fixed" is not.
  - "partial" needs you to say exactly which part of \`expect\` held and which did not.
  - Some repros need a slot loaded or a model registered. You may do that. The stateful lanes are
    running on this same box concurrently, so before you mutate anything, re-read the slot state
    rather than assuming, prefer creating your own throwaway slot over reusing a shared one, and
    never unload a slot you did not load.
  - If an entry's repro no longer matches the current CLI or API surface, say so — a stale
    regression entry is itself a finding worth reporting to the curation phase.

Return one result object per entry you ran.`, regOpts))
  }
}

const swept = await parallel(work)
const laneResults = swept.filter(Boolean).flatMap((r) => (Array.isArray(r) ? r : (r.results ? [] : [r])))
const regressionResults = swept.filter(Boolean).filter((r) => !Array.isArray(r) && r.results).flatMap((r) => r.results)

const totalChecks = laneResults.reduce((n, r) => n + (r.tested ? r.tested.length : 0), 0)
const rawFindings = laneResults.flatMap((r) => (r.findings || []).map((f) => Object.assign({}, f, { lane: r.lane, box: r.box })))
log(`sweep done — ${laneResults.length} lanes, ${totalChecks} checks, ${rawFindings.length} raw findings, ${regressionResults.length} regression probes`)

// ── phase 2: triage ──────────────────────────────────────────────────────────
// Genuine barrier: dedup needs every finding at once, and verification is expensive enough that
// verifying duplicates is worth avoiding.
phase('Triage')

const regressedNow = regressionResults.filter((r) => r.result === 'regressed' || r.result === 'partial')

const triage = await agent(`${BASE}
You are the TRIAGE agent. You have every raw finding from every lane, plus the regression probe
results. Produce the candidate list that the adversarial verifiers will attack.

RAW FINDINGS (JSON):
${JSON.stringify(rawFindings, null, 1)}

REGRESSION PROBES THAT DID NOT COME BACK CLEAN (JSON):
${JSON.stringify(regressedNow, null, 1)}

Do this:
1. Merge findings that are the same defect seen from different surfaces. A CLI symptom and an API
   symptom with one root cause are ONE candidate — say so in the claim and keep both evidence
   lines. Merging is where a 40-finding sweep becomes an 11-issue report.
2. Drop anything already covered by ${KIT}/known-issues.yaml, unless that entry's
   \`still_report_if\` clause is met. Put each drop in \`dropped\` with the reason and the
   issue id — the report needs the dupe list.
3. Drop anything whose evidence would not let another agent reproduce it. Say so; a suspicion is
   not a finding. If it looks important but under-evidenced, keep it and say the evidence is thin
   so the verifier knows what to attack first.
4. Every regression that came back regressed or partial becomes a candidate automatically, at the
   severity of the original issue or higher. A defect that was fixed and came back is worse than
   one that was never fixed.
5. Rank by user impact, not by how interesting the bug is. Silent wrongness — a feature that
   reports success while doing nothing — outranks loud breakage.
6. Read the source where it decides the merge: the installed tree on the box, and the repo at
   ${REPO}. Two symptoms with one line of shared cause should not become two issues.

Give each candidate a short kebab-case \`key\`; the verifiers are keyed on it.`,
  { label: 'triage', phase: 'Triage', schema: TRIAGE, model: 'opus' })

if (!triage || !triage.candidates.length) {
  log('triage produced no candidates')
}
const candidates = (triage && triage.candidates) || []
log(`triage: ${candidates.length} candidates, ${((triage && triage.dropped) || []).length} dropped`)

// ── phase 3: adversarial verification ────────────────────────────────────────
phase('Verify')

const verdicts = (await parallel(candidates.map((c) => () => agent(`${BASE}
You are a SKEPTIC. Your job is to REFUTE the finding below, not to confirm it.

CANDIDATE (${c.key}): ${c.title}
CLAIM: ${c.claim}
CLAIMED SEVERITY: ${c.severity_claimed}
EVIDENCE OFFERED: ${c.evidence}
REPRO OFFERED: ${c.repro}

Reproduce it fresh with your own commands — do not take the reporting agent's word for anything.
Then attack it:
  - Is it intended behaviour? Check config defaults, --help text, the ADRs in ${REPO}/docs/adr,
    and the installed source on the box. rc.4 had four candidates die exactly here.
  - Is the evidence misread — a different slot, a stale cache, a reaped slot, a timeout that was
    really model slowness on CPU?
  - Is it specific to this box's environment (CPU-only, unprivileged container, no /dev/dri)
    rather than a defect a normal install would hit?
  - Is it already covered by ${KIT}/known-issues.yaml under a different description?

If it survives, TIGHTEN it: state exactly what reproduces and nothing more. An overclaimed issue
wastes a fix agent's time and gets closed as not-reproducible.

Then classify: ga-blocker (breaks core promised functionality for a typical user), major (real
defect, workaround exists), minor, cosmetic, or by-design. If by-design, write the rationale AND
the \`still_report_if\` clause that should go into known-issues.yaml, so no future run
relitigates it.

Also judge \`other_hardware_applicability\`: would a GPU box, a privileged container, or an
upgraded (rather than freshly installed) box hit this? There is no GPU fresh-install box in the
fleet, so a read-only cross-check against the production box is the best available evidence —
use it when the finding touches backend selection, profile flags, hardware probing, or native
tool calling.

Non-destructive repro only. If you load a slot, restore what you found. Set key="${c.key}".`,
  { label: `verify:${c.key}`, phase: 'Verify', schema: VERDICT, model: 'opus', effort: 'high' })))).filter(Boolean)

const confirmed = verdicts.filter((v) => v.verdict !== 'refuted' && v.classification !== 'by-design')
const killed = verdicts.filter((v) => v.verdict === 'refuted' || v.classification === 'by-design')
log(`verify: ${confirmed.length} confirmed, ${killed.length} refuted or reclassified as by-design`)

// ── phase 4: report ──────────────────────────────────────────────────────────
phase('Report')

const report = await agent(`${BASE}
You are the SYNTHESIS agent. Write the release validation report.

Use the skeleton at ${KIT}/templates/report.md, and read the previous release's report in
${KIT}/reports/ so the two are comparable — same section order, same tables, and call out what
changed between releases (check counts, route counts, which lanes got quieter).

INPUTS
Preflight: ${JSON.stringify(preflights.filter(Boolean).map((p) => ({ box: p.box, version: p.observed_version, summary: p.summary })), null, 1)}
Lane results: ${JSON.stringify(laneResults.map((r) => ({ lane: r.lane, box: r.box, checks: (r.tested || []).length, pass: (r.tested || []).filter((t) => t.result === 'pass').length, findings: (r.findings || []).length })), null, 1)}
Regression probes: ${JSON.stringify(regressionResults, null, 1)}
Verified findings: ${JSON.stringify(verdicts, null, 1)}
Dropped in triage: ${JSON.stringify((triage && triage.dropped) || [], null, 1)}
Raw lane notes: ${RUN}/lanes/*.md

Write it to BOTH:
  - ${RUN}/report.md
  - ${KIT}/reports/v${VERSION}.md   (this one is committed — it is the durable record)

Requirements:
  - Lead with the verdict: ship or do not ship, in one sentence, then the specific blockers.
  - State the minimum gate for the next candidate and why each item is on it.
  - Include the "what held up well" section. A report that lists only failures gives a false
    picture of the release and hides which lanes are earning their keep.
  - Every regression entry appears in the regression table, including the ones that came back
    clean, and including the blocked ones with the reason they were blocked.
  - Reclassified and refuted candidates get their own section with the reasoning that killed
    them — that reasoning is what stops the next run rediscovering them.
  - Record kit_version from ${KIT}/kit.toml and the exact box configurations.
  - Be honest about coverage: which lanes were skipped, which checks were skipped, what the
    fleet cannot test at all (there is no GPU fresh-install box).

Return the path you wrote plus a 10-line summary suitable for pasting into a release thread.`,
  { label: 'report', phase: 'Report', model: 'opus' })

// ── phase 5: file issues ─────────────────────────────────────────────────────
let filed = null
if (MODE === 'file' && confirmed.length) {
  phase('File')
  filed = await agent(`${BASE}
File one GitHub issue per confirmed finding, in the Hal0ai/hal0 repo, with \`gh\`.

CONFIRMED FINDINGS: ${JSON.stringify(confirmed, null, 1)}
REPORT: ${KIT}/reports/v${VERSION}.md

Rules:
  - Search existing open issues first. If one already covers it, comment with the new evidence
    instead of filing a duplicate, and say so in your output.
  - Title: the tightened claim, imperative and specific. Not "memory is broken".
  - Body: what happens, what should happen, exact repro, decisive evidence line, environment
    (version, box, hardware), severity and why, and the applicability judgement for other
    hardware. Link the report.
  - Group genuinely trivial polish items into ONE rollup issue per surface (cli/api, ui, hermes),
    with a checklist — but enumerate every item individually inside it. rc.4 used this shape and
    it worked.
  - Label with the triage labels this repo uses. A finding with a complete repro is
    ready-for-agent; one that needs a decision is ready-for-human.
  - Do not close, edit, or reopen anything else.

Return the filed issue numbers with titles and the dupes you commented on instead.`,
    { label: 'file-issues', phase: 'File' })
}

// ── phase 6: curation — this is what makes the kit compound ──────────────────
phase('Curate')

const curation = await agent(`${BASE}
You are the KIT CURATION agent. Fold what this run learned back into the kit so the next release
starts from here instead of from scratch. This phase is the reason the kit exists.

INPUTS
Verified findings: ${JSON.stringify(verdicts, null, 1)}
Regression probes: ${JSON.stringify(regressionResults, null, 1)}
Checks agents invented that were not in any brief: ${JSON.stringify(laneResults.flatMap((r) => (r.new_checks_worth_keeping || []).map((c) => ({ lane: r.lane, check: c }))), null, 1)}
Filed issues: ${filed ? JSON.stringify(filed) : '(mode=report — nothing filed)'}

Edit the kit in ${REPO} (a normal working-tree edit; do not commit):

1. ${KIT}/regressions.yaml — add an entry for every newly filed finding: id, issue, from
   (v${VERSION}), severity, tier, lane, summary, repro, expect. The repro must be runnable by an
   agent that has never seen this run. Remove entries you promoted into pytest or
   scripts/release-test.sh, and mark ones that are ready to promote.
2. ${KIT}/known-issues.yaml — add every candidate the verifiers refuted or ruled by-design, with
   its rationale and its \`still_report_if\` clause. Update \`updated_for\`. Flag entries past
   their \`review_at\` release.
3. ${KIT}/lanes/**.md — add the checks agents invented to the brief that should have contained
   them, in the brief's existing voice. Delete checks that have gone stale or been promoted into
   automated tests. Keep briefs readable: a brief nobody finishes inside its time budget is worse
   than a shorter one that gets completed.
4. ${KIT}/boxes.toml — correct anything preflight found stale.
5. ${KIT}/kit.toml — bump kit_version, and add a changelog line to ${KIT}/README.md describing
   what changed and why.

Also identify checks that should stop needing an agent at all: anything mechanical and stable
belongs in pytest or scripts/release-test.sh. List them with the specific test that should be
written. Moving work out of this kit and into CI is a success, not a loss.

Return a summary of every file you changed and what you changed in it, plus the promotion list.
Do not commit — the operator reviews the diff.`,
  { label: 'curate', phase: 'Curate' })

return {
  version: VERSION,
  boxes: BOX_IDS,
  mode: MODE,
  lanes: laneResults.length,
  checks: totalChecks,
  regressions: {
    total: regressionResults.length,
    fixed: regressionResults.filter((r) => r.result === 'fixed').length,
    regressed: regressionResults.filter((r) => r.result === 'regressed').length,
    partial: regressionResults.filter((r) => r.result === 'partial').length,
    blocked: regressionResults.filter((r) => r.result === 'blocked').length,
  },
  candidates: candidates.length,
  confirmed: confirmed.map((v) => ({ key: v.key, classification: v.classification, claim: v.tightened_claim })),
  refuted: killed.map((v) => ({ key: v.key, classification: v.classification, why: v.by_design_rationale || v.evidence })),
  report,
  filed,
  curation,
}
