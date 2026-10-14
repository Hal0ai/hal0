// src/hal0/agents/pi_coder/plugins/hindsight/index.ts
/**
 * hal0 — hindsight-coding-agents binding for Pi.
 *
 * @vectorize-io/hindsight-coding-agents does not export `RuntimeCore` or
 * `resolveHostMemory` from any public subpath — each harness bundle inlines
 * its own private copy — so hal0 cannot hand-roll a binding. Instead it
 * delegates to the package's own Pi extension, `dist/pi.js` (default export;
 * reached through the package's `./dist/*` export map, an internal API —
 * hence the exact pin in this directory's package.json).
 *
 * That extension (verified against 0.8.1):
 *   - seeds (`RuntimeCore.seedIfCold`) when the extension loads / on
 *     "session_start" — no separate wiring to replicate,
 *   - wires "before_agent_start" -> `onPrompt` + injects recalled memory
 *     into the system prompt,
 *   - wires "agent_end" -> `onTranscript` (write-back every turn),
 *   - wires "session_shutdown" -> `flush()`, so the last turn's write-back
 *     is not lost at exit,
 *   - registers hindsight's native `hindsight_*` tools via `pi.registerTool`.
 *
 * HARNESS LABEL: upstream identifies this binding as harness "pi" (its
 * `createPiExtension("pi")`). Through 0.4.3 only the Prime Agent fork's
 * bundle (`dist/prime-agent.js`, label "prime-agent") was reachable, so
 * memories retained by hal0's Pi before the 0.8.1 bump carry the
 * "prime-agent" label. Bank naming keys off {gitProject}, not the harness,
 * so old and new memories share one bank; the label only shows up in
 * diagnostics, retain tags and the `harnesses.pi` config section.
 *
 * Upgrading the pin is a deliberate act: re-verify the dist/ layout and the
 * hook wiring above against the new version before bumping.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
// Deep import into the package's exported "./dist/*" map; see the module doc
// comment above for why this is the reachable Pi-shaped binding.
import hindsightPiExtension from "@vectorize-io/hindsight-coding-agents/dist/pi.js";

export default async function (pi: ExtensionAPI) {
  // Fail-open: a dead hindsight-api (9177), a misconfigured bank, or any
  // other setup-time throw from the upstream extension must never block Pi
  // from starting. Unlike this file's session-scoped hooks, the upstream
  // `extension` function performs its (synchronous) RuntimeCore construction
  // and seeding eagerly, so the whole call needs to be inside this guard —
  // there is no per-hook boundary to wrap individually from out here.
  try {
    await hindsightPiExtension(pi);
  } catch (error) {
    console.error(`[hindsight] disabled: ${error instanceof Error ? error.message : error}`);
  }
}
