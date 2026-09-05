# hal0 × ODS field study (2026-09)

Research note. A source-level comparison of [Osmantic ODS](https://github.com/Osmantic/ODS)
(Apache-2.0; its author has granted permission to copy) and hal0, a hands-on install of
ODS, all 106 open hal0 issues triaged against ODS's code, and a ranked adoption plan.
Nothing here is merged behaviour; it is a proposal for the maintainers to decide on.

- `hal0-x-ods-field-study.html` — the study: verdict, shortfalls, per-domain mechanism
  summaries, side-by-side matrix, the observed install (with screenshots embedded),
  a port ledger in waves, issue clusters, decisions, and the full reports as appendices.
  Self-contained; open it in a browser.
- `reports/00-install-journal.md` — phase-by-phase notes from the sandbox install.
- `reports/01…09-*.md` — the nine domain reports (agents, extensions, installer and
  reliability, models, networking and mDNS, MCP and memory, dashboard UX, container
  runtime and CLI and AMD tuning, open-issue map). Every claim cites `file:line` in
  `Osmantic/ODS@21f4b3a` or `hal0ai/hal0@108b366`.
- `reports/hal0-open-issues-2026-09-05.md` — the issue snapshot the map was built from.

Provenance: reports 01, 02, 03, 06 and 09 were produced by Opus agents and 04, 05, 07 and
08 by Sonnet agents under a Claude Code session; the journal, the synthesis and the
verdicts were written by the session itself after reading every report. Treat the
`file:line` citations as the authority, per this repo's "verify before you write" rule.

The install ran in a remote sandbox (no GPU, CPU tier 0, rootless install user, Docker
29.3.1), not on Strix Halo hardware; the journal states every sandbox-specific adjustment.
