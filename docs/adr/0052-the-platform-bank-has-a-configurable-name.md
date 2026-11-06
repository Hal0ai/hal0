# ADR-0052: The platform memory bank has a configurable name

## Status

**ACCEPTED (decided 2026-11-03 under the H0-120 mandate; Alex to confirm on
review).** Supersedes the bank layout in ADR-0005 (decision 1, where the
`shared` namespace is the engine bank `shared`). Everything else in ADR-0005
stays: the namespace grammar, the retired namespaces, and the tag-based scoping.
Tracking issue: H0-96.

## Context

ADR-0005 cut the namespace grammar to `shared` and `private:<agent>`. Under
the default `[memory] unified_bank = true` both namespaces land in one
Hindsight bank, and that bank's id was the namespace spelled literally:
`shared`. The name was hardcoded in two places, `DEFAULT_DATASET = "shared"`
in `src/hal0/memory/namespace.py` and `_SHARED = "shared"` in
`src/hal0/memory/hindsight_provider.py`, and the provider used it as the bank
id as well.

That conflates two things. `shared` is a namespace, an address that callers
write in tool arguments and headers. The engine bank is where the data lives.
An operator who lays out their Hindsight banks by purpose (for example
`Hal0::Platform` for hal0's own memory, `Hal0::Agents` and `Hal0::Private`
for other uses) has no way to point hal0 at the platform bank without renaming
the namespace, which would break every agent, plugin and skill that says
`shared`.

Hermes's `hal0-memory` plugin and Hal0 Brain both write through hal0-api, so
they follow whatever bank hal0-api uses. Nothing outside hal0-api needs to
learn the new name.

A leftover `agents` bank can also exist on a box that predates ADR-0005. ADR-0005
told operators to move it by hand; this record gives them a command.

## Decision

1. **The namespace grammar does not change.** `shared` and
   `private:<client_id>` stay the only namespaces. Callers, plugins, skills and
   the dashboard keep saying `shared`. API responses keep reporting
   `dataset: "shared"` for items from the platform bank.
2. **A new config key, `[memory] bank`, names the engine bank behind
   `shared`.** The shipped default is `shared`, so an install that does not set
   it behaves exactly as before. The value follows the bank-id grammar hal0-api
   already enforces on `/api/memory/banks/{bank_id}` and must not start with
   `private__` or equal `agents`. Changing it needs a hal0-api restart.
3. **Only the provider maps namespace to bank.** `HindsightProvider` resolves
   `shared` (and, under `unified_bank`, every namespace) to the configured bank
   and maps the bank id back to `shared` when it labels items. `private:<agent>`
   banks, when `unified_bank = false`, keep their `private__<agent>` names.
4. **One migration moves the data, and it runs before the switch.**
   `hal0 memory migrate platform` clones the source bank (default `shared`) into
   the configured name with the engine's own `POST /v1/default/banks/{bank}/clone`.
   The engine runs the export and import in-process and calls no LLM, so the
   clone is cheap and does not queue consolidation. It then folds the retired
   `agents` bank into the same target with the existing document-transfer
   path, and it compares fact counts. Nothing is re-tagged: the identity cards
   already carry the `agent-identity` tag that ADR-0005 scopes them by.
5. **The migration is additive and is a dry run by default.** The source bank
   and the `agents` bank are never modified or deleted. The operator deletes
   them later, through the separate `hal0 memory bank delete`, once the counts
   are verified. The command also never edits `hal0.toml`: the operator sets
   `[memory] bank` after the clone is verified.
6. **No compatibility shim.** There is no fallback that reads the old bank when
   the new one is empty. A box that sets `[memory] bank` before running the
   migration sees an empty memory until it runs it, and `migrate platform`
   refuses an existing target bank unless told it already holds the clone
   (`--skip-clone`).

## Consequences

- Existing installs are unaffected. The default is `shared`.
- The order of operations matters and is documented in the CLI reference and
  the memory guide: run `migrate platform` (dry run, then `--apply`), verify,
  set `[memory] bank`, restart hal0-api, and only later delete the old banks.
- `POST /api/memory/banks/{bank_id}/clone` joins the memory admin passthrough.
  It is classified ADMIN with the rest of `/api/memory` and is not a
  destructive route: it creates a bank and refuses an existing one.
- The clone copies the source bank's config, directives and webhooks by default,
  because a platform bank needs its directives. Webhooks then call the same
  endpoints from both banks; `--skip-bank-config` leaves all of it behind.
- The clone needs the engine's export and import APIs enabled
  (`features.document_export_api` and `document_import_api`). The command checks
  both and stops if either is off.

## Open question

Whether Hal0 Brain chat transcripts should be retained into the platform bank
(proposed: tagged `source:brain`, with their own observation scope) is not
decided here. It is a separate change, and it waits for the operator.
