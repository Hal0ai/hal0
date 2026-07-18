<!--
If external PRs aren't being merged yet, see CONTRIBUTING.md. Keep this form
concise — it's a checklist for reviewers, not a design doc. Pre-fill the
risk grade, touched surfaces, and test-tier runs before requesting review.
-->

## Summary

<!-- one paragraph: what + why -->

## Risk grade

<!-- pick one. See CONTRIBUTING.md "Test tiers" and PLAN §21.15. -->
- [ ] low    — docs only, internal refactor, or behaviour-preserving fix
- [ ] med    — user-facing change, new code path, or non-trivial refactor
- [ ] high   — anything touching the §14.1 high-risk surfaces below

## Touched surfaces

<!-- check every area this PR modifies. Reviewer uses this to set lanes. -->
- [ ] API (`src/hal0/api/`)
- [ ] Auth / sessions (`src/hal0/auth/`, login routes, middleware)
- [ ] Slots / dispatch (`src/hal0/slots/`, `slot_state`, `/v1/load|unload`)
- [ ] Models / capabilities (`src/hal0/capabilities/`, `model_meta`, `model_fit`)
- [ ] Installer (`installer/`, systemd units)
- [ ] Updater (`src/hal0/updater/`, `hal0.releases.v1` manifest)
- [ ] Board chat / MCP admin (`src/hal0/api/routes/board_chat.py`, `src/hal0/mcp/admin.py`)
- [ ] Config / schema (`src/hal0/config/`, pydantic models)
- [ ] UI (`ui/src/`, Playwright specs)
- [ ] Docs (`docs/`, `CONTRIBUTING.md`)
- [ ] CI / release (`.github/workflows/`, `release.yml`)

## §14.1 high-risk surfaces

<!-- If ANY box below is checked, this PR is **high-risk** regardless of
     the grade above. Reviewer must run the full γ release-gate before merge. -->
- [ ] Unauthenticated board routes — any new `/v1/board/*` or MCP endpoint
      exposed without auth middleware (KB-1 / §1 deny-by-default)
- [ ] `AUTONOMOUS_WRITE_TOOLS` — additions to the write set in
      `src/hal0/mcp/admin.py` (board-chat auto-actions)
- [ ] Installer / updater RCE-class — shell-out, downloads, signature
      verification, manifest parsing, privilege changes

## Rollback

<!-- One sentence on how to revert. If not obvious, link the revert PR. -->

_Rollback:_

## Test tiers run

<!-- Every PR runs α + β (CONTRIBUTING.md). γ is required only for high-risk
     PRs and per release candidate. -->
- [ ] α  unit (`make test`)
- [ ] β  integration (`make test-integration`)
- [ ] γ  release-gate (`make release-test`) — required for high-risk and
      release-candidate PRs
