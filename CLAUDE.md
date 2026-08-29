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

Issues live in GitHub Issues; use the `gh` CLI. The five canonical triage
labels are `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`
and `wontfix` — use them unmodified rather than inventing new ones.

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
