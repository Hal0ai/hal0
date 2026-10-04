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

## Amendments

- **2026-10-04** — Post-review corrections (Codex review on PR #2237), each marked
  *Correction (2026-10-04)* in place: report 06 §E.2 (a stdio server needs a client-attached
  bridge, not a free-standing unit), §E.3 step 2 (never reuse the builtin loop's admin bearer for
  third-party URLs), §E.3 step 4 (the `[tools]` mirror describes policy; enforcement needs a
  hal0-fronted proxy mount — hal0 issue #2303); report 02 §D.5 (bridge extensions reach slots
  only through hal0-api; render-time URLs do not follow port changes) and Decision 3 (the design
  as written is rootful — say so, or build the user-scope seam); report 09 item 6 (the ROCm lane
  needs `/dev/kfd` **and** a `renderD*` node); report 08 §D.1 (host tuning must detect containers
  and emit host-side commands); the study's Proxmox reproduction recipe (was LXD syntax, now
  `pct`). Also noted where main (`c177aa5`) has overtaken the pinned `108b366`: ADR-0015 and
  #2253 shipped the user-installed-MCP schema, join and verbs; #2246 gates ADMIN routes,
  approvals included, on LAN-bound boxes; #2244 reconciled the `docs/adr/` tree. Citations stay
  pinned to `108b366` unless a note says otherwise.
- **2026-10-04 (round 3)** — Proxmox recipe: the template file name is resolved from
  `pveam available` the way `scripts/proxmox-ve/hal0.sh:278` does, the privileged + GPU
  container gets `lxc.apparmor.profile: unconfined` (`docs/getting-started/proxmox.mdx`
  §AppArmor), and the host-side tuning note is GTT/TTM only. Report 08 §B and §D.1, the
  summary bullet, plan row 1.7 and decision D7 carry the same IOMMU caveat: hal0 documents
  `iommu=pt amd_iommu=on` (`drivers.mdx:102`, ADR-0003:96-99) because `amd_iommu=off`
  removes the NPU's `/dev/accel`, so the gap is GTT/TTM, sysctl and tuned — not the IOMMU
  line. Three Markdown escaping defects (reports 01, 06, 09) that rendered as a stray `<h1>`
  and raw `<name>` tags in the HTML are fixed.
