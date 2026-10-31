# ADR-0045: Slots carry a sparse tune override table

## Status

**ACCEPTED (2026-10-30).** An operator decision. Amends ADR-0029.

## Context

Profiles remain the reusable launch tune, while models describe file facts and
slots describe placement. Some slots need a small exception without duplicating
the entire profile: the same model in a fast slot and a full-context slot, or
a dedicated memory-extraction slot with thinking disabled and low effort.
Model-specific JSON conformance still needs validation on its actual runner.

## Decision

A slot may carry a sparse `[tune]` table with exactly `ctx_size`,
`enable_thinking`, `reasoning_effort`, `reasoning_budget`, `vision` and
`speculation`. Missing keys inherit the profile. Models still own file facts;
profiles own the base tune, free launch flags and runtime; slots own placement
and these explicit exceptions. Legacy root-level tuning keys remain denied.
Only LLM slots outside the NPU lane accept tune writes.

`effective_tune` is the shared resolver for launch, thinking defaults, context,
feasibility and benchmark planning. Slot overrides beat profile values, but
`--reasoning on|off` in profile flags pins thinking. Effort flags are emitted
only for supported runner builds (at least 11840). `default` effort and budget
`-1` emit no corresponding reasoning flag. Budget is capped at 16777216.

Thinking-only changes apply to new requests. Context, effort, budget, vision
and speculation changes require restart. The drawer stages changes and can
clear overrides or clone the selected profile with its flags and staged tune.
N-gram speculation does not require a drafter.

The stable `tune_effective` read contract contains `ctx_size`,
`enable_thinking`, `reasoning_effort`, `reasoning_budget`, `vision`,
`speculation`, `sources` and `reasoning_effort_unavailable`. Source values are
`profile`, `slot` or `profile-flags`; unavailable is a string or null. Fields
may be added but not renamed. Slot snapshots, resolved reads and the compact
MCP `slot_list` expose this contract.

REST and MCP tune edits merge supplied keys; null removes a key and null for
the table clears all overrides. Stack export preserves the sparse table.
Profile exports omit default effort and budget; no schema-version bump is
required. Stack apply currently merges tune keys rather than replacing them.

Rollback to a release without tune support strips what that release refuses,
only after writing a `.bak-<stamp>` copy: slot `[tune]`, profile reasoning keys
in `profiles.toml`, and stack entry `tune` plus embedded profile reasoning keys
in `stacks.toml`. Files without these keys are untouched. Failed or cancelled
activation restores backups unless the stripped file has since been edited.

## Consequences

Operators can tune one slot without cloning a shared profile, and readers can
explain the effective value and its source. Invalid hand-edited overrides must
never become command-line fragments: values are validated per key before use.
Changing a slot's type/device can leave an inert table; clearing it explicitly
remains available. Rollback backups preserve overrides for later recovery.

## Source evidence

- `src/hal0/config/schema.py:333`, `:960`: tune and profile fields.
- `src/hal0/slots/tune.py:174`, `:228`: resolver and write guards.
- `src/hal0/providers/container.py:1695`, `:3155`: launch and resolved tune.
- `src/hal0/mcp/admin.py:269`: compact roster contract.
- `src/hal0/updater/updater.py:4438`, `:4519`: rollback restore and strip.
- `ui/src/dash/slot-tune-block.jsx:155`: Tune controls.
