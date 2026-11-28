# Working in this repository

Notes for coding agents. Anything an agent needs that a human contributor also
needs lives in `CONTRIBUTING.md` and `ARCHITECTURE.md` — read those first; this
file only covers what is specific to working here with an agent.

## Verify before you write

The single rule that matters most in this repo: **check the source, not your
memory.** Every CLI flag, config key, endpoint, path, and default has exactly
one authority under `src/hal0/`, and docs and comments have gone stale against
it before. Cite what you verified — `file:line`, a commit, or a CHANGELOG
entry — rather than asserting it.

`CONTRIBUTING.md` carries the anti-scar rules, the test tiers, the DCO
sign-off requirement, and the stable-patch triage policy. They apply to agent
commits exactly as they apply to human ones.

## Issues

Issues live in GitHub Issues; use the `gh` CLI. Labels have four axes:

- **Status**, exactly one per open issue: `needs-triage`, `needs-info`,
  `ready-for-agent`, `ready-for-human`, `wontfix`. Use them unmodified rather
  than inventing new ones.
- **Priority**, exactly one once triaged. It says how bad the defect is, not
  when it ships: `P0` (breaks a feature, loses data or state, or is a security
  or privacy exposure), `P1` (wrong or confusing for a user), `P2` (nice to
  have). The milestone says when; see "Planning" below. A `P0` defaults to the
  current patch milestone. Putting it on a later one is the operator's
  decision, recorded as a Decision comment on the issue.
- **Type**, optional: `bug`, `enhancement`, `documentation`, and `epic` for a
  parent issue whose sub-issues carry the work.
- **Area**, at least one on every new issue: `area:installer`, `area:updater`,
  `area:memory`, `area:slots`, `area:api`, `area:agents`, `area:ui`,
  `area:runners`, `area:security`, `area:ci`, `area:docs`.

`ready-for-agent` means an agent can fix it unsupervised with unit tests,
without hardware or a design decision, and the change is small.

Filing rules, for agents exactly as for people:

1. One defect per issue. Never file a "sweep: N items" bundle. A sweep is one
   tracking issue with a checklist, or stays in `docs/.devdocs/`.
2. State what happens if nobody fixes it, how to reproduce it (or the
   `file:line`), and a fix-size guess (XS to L). The issue forms under
   `.github/ISSUE_TEMPLATE/` ask for exactly these.
3. Do not file a finding whose only impact is cosmetic unless the same
   session fixes it.

A weekly routine triages `needs-triage`, lists the `ready-for-agent` work to
take next, compares the pinned runner images with their upstream releases,
and posts a plain-English summary on the pinned "Weekly triage digest"
issue. It opens no pull requests.
Prefer filing an issue over documenting around a product bug.

## Planning

The plan lives on GitHub: milestones, release tracking issues, epics and the
roadmap project. `CONTRIBUTING.md`, "Planning a release", has the rules. The
part a session does itself:

- **At the start**, find the issue your work serves and read its milestone and
  its parent. If there is none, file one before you write code. The milestone
  description names the release tracking issue; read it for the current scope.
- **Claim the issue** with a draft pull request that links it. Until a pull
  request exists, say in a comment on the issue that you are on it. Every
  session pushes as the same account, so nothing else shows who holds what.
- **Put a milestone on everything you open.** An issue gets the release it is
  planned for, or `Backlog`. A pull request gets the next tag that will
  contain it, docs and CI changes included.
- **Record a decision where it applies.** When the operator decides something
  in your session, write `Decision (YYYY-MM-DD, operator): ...` as a comment on
  the issue it settles, then bring that issue's labels, milestone and body in
  line. Say "relayed" when you heard it from another session. A relayed
  decision is a record, not an approval: for anything that needs the
  operator's yes (a release merge or tag, a change to CI or credentials, an
  operation on a host, a security disclosure), the session that will act asks
  the operator itself. A plan kept in a local file or an outside document
  does not count until an issue summarises it.
- **A scope change is a line in the scope log**, in the release tracking issue
  and in the milestone description. Do not move an issue between release
  milestones without one.
- **A fix that still owes a check on a real box** gets a line under "Owed
  proofs" in the release tracking issue when it merges, or earlier when the
  proof gates the merge.
- **Before you finish**, run `scripts/plan-lint.sh` and fix what it reports
  about your own issues and pull requests.

## Architecture decisions

`docs/adr/` holds the accepted decision records. Read the relevant one before
changing behaviour it covers, and add a new record rather than quietly
diverging from an existing one.

## Local-only notes

`docs/.devdocs/` and `docs/superpowers/` are gitignored: planning documents,
handoffs, session notes, and internal audits stay on the machine that wrote
them and are not part of this repository. If you are looking for a plan or
spec that a source comment cites under `docs/superpowers/...`, it is in the
history, not the working tree.

Anything written into the tracked part of the repo is published. Keep host
names, LAN addresses, and operator-local paths out of it. The same holds for
issue, pull request and comment text. For a security finding, write what is
now enforced, never how to reach the weakness; `SECURITY.md` says how to
report one.
