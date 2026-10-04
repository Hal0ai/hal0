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

Issues live in GitHub Issues; use the `gh` CLI. Labels have three axes:

- **Status**, exactly one per open issue: `needs-triage`, `needs-info`,
  `ready-for-agent`, `ready-for-human`, `wontfix`. Use them unmodified rather
  than inventing new ones.
- **Priority**, exactly one once triaged: `P0` (breaks a feature, loses data
  or state, or is a security or privacy exposure; goes to the current patch
  milestone), `P1` (wrong or confusing for a user; the next minor milestone),
  `P2` (nice to have; the `Backlog` milestone).
- **Type**, optional: `bug`, `enhancement`, `documentation`.

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

A weekly routine triages `needs-triage`, picks `ready-for-agent` work, and
posts a plain-English summary on the pinned "Weekly triage digest" issue.
Prefer filing an issue over documenting around a product bug.

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
names, LAN addresses, and operator-local paths out of it.
