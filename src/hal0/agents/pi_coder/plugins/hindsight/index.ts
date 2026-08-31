// src/hal0/agents/pi_coder/plugins/hindsight/index.ts
/**
 * hal0 — hindsight-coding-agents binding for Pi.
 *
 * DEVIATION FROM SPEC (see task-4-report.md for full evidence): the pinned
 * @vectorize-io/hindsight-coding-agents@0.4.3 does NOT export `RuntimeCore`
 * or `resolveHostMemory` from any subpath — there is no `dist/core/*` in the
 * published package at all (its build inlines each harness's copy of those
 * classes privately into that harness's own bundle; none re-export them).
 * The only exported, working Pi-shaped binding in 0.4.3 is `dist/prime-agent.js`'s
 * default `extension` function — the same file the package's own package.json
 * `"pi": { "extensions": [...] }` field declares as ITS canonical Pi
 * extension. We delegate to it rather than hand-rolling a duplicate RuntimeCore
 * binding that the package's public API makes impossible to construct.
 *
 * That upstream extension:
 *   - wires "before_agent_start" -> RuntimeCore.onPrompt + injects via the
 *     returned `systemPrompt` (matches this plan's mapping),
 *   - wires "agent_end" -> RuntimeCore.onTranscript (matches this plan's
 *     mapping),
 *   - seeds (RuntimeCore.seedIfCold) synchronously inline during its own
 *     setup, i.e. on extension load — there is no separate "session_start"
 *     wiring to replicate,
 *   - registers hindsight's native hindsight_* tools via `pi.registerTool`
 *     (bonus over this plan, not requested but harmless and fail-open),
 *   - does NOT wire any "session_shutdown"/idle flush — RuntimeCore.onSessionIdle
 *     is unreachable from here (no exported way to obtain the RuntimeCore
 *     instance the upstream extension constructs internally), so unlike this
 *     plan's mapping there is no final-flush hook. Write-back still happens
 *     every `agent_end` (turn cadence), which is upstream's actual fail-open
 *     substitute for a Stop-hook they do not have here either.
 *
 * HARNESS LABEL: upstream identifies Pi as harness "prime-agent" (hardcoded,
 * private, unexported — dist/prime-agent.js's own `var HARNESS2 =
 * "prime-agent"`, passed as `RuntimeCore`'s 4th constructor arg; nothing in
 * 0.4.3's public API can relabel it, the `HINDSIGHT_HARNESS` env var included
 * — it only feeds `Config.harness`, a config-loading field this extension's
 * `createRuntime` never reads). Accepted as Pi's factual hindsight harness
 * identity at this pin: bank naming keys off {gitProject}, not harness, so
 * this label only shows up in diagnostics/retain tags, not bank identity.
 *
 * Upgrading the pin is a deliberate act regardless: re-verify these findings
 * against the new dist/ layout before bumping past 0.4.3.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
// Deep import into the package's exported "./dist/*" map; see the module doc
// comment above for why this is the only reachable Pi-shaped binding in 0.4.3.
import hindsightPrimeAgentExtension from "@vectorize-io/hindsight-coding-agents/dist/prime-agent.js";

export default async function (pi: ExtensionAPI) {
  // Fail-open: a dead hindsight-api (9177), a misconfigured bank, or any
  // other setup-time throw from the upstream extension must never block Pi
  // from starting. Unlike this file's session-scoped hooks, the upstream
  // `extension` function performs its (synchronous) RuntimeCore construction
  // and seeding eagerly, so the whole call needs to be inside this guard —
  // there is no per-hook boundary to wrap individually from out here.
  try {
    await hindsightPrimeAgentExtension(pi);
  } catch (error) {
    console.error(`[hindsight] disabled: ${error instanceof Error ? error.message : error}`);
  }
}
