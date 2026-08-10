# Lane: brain (stateful, order 3)

The hal0-brain steward: the built-in assistant that answers questions about the platform using
tools. Its failure mode is not crashing — it is **confident fabrication**, which is the single
most damaging defect class in the product, because the user cannot tell.

## Checks

1. **Find the surface.** `hal0 chat --help` on the box, and grep the OpenAPI paths for
   brain/chat routes — the dashboard uses one of them. Record the current `brain_chat` config
   (enabled, read_only, model, tool_model, max_rounds) before testing.
2. **CLI chat.** One-shot if a flag supports it; otherwise drive the REPL with a piped here-doc.
   Does it connect, answer from the expected slot, and exit cleanly? Quote the reply.
3. **API chat.** POST the brain chat route with a simple message. Does it round-trip/stream?
   Does the route have a real requestBody schema, and does malformed JSON produce a real error
   rather than being coerced to `{}` (rc.4 did the latter)?
4. **Tool rounds** — regression `brain-tools-image-gate` (#1789). Ask something only tools can
   answer: *"what slots are loaded right now?"*. Then check three things independently:
   * the reply's factual accuracy against `hal0 slot list`
   * tool frames present in the stream
   * tool dispatch present in the hal0-api journal

   rc.4 failed all three at once and answered with invented slot names. The fix moved the gate
   off runner-image identity, so test on **whatever runner this box actually has**, and say
   which one that was.
5. **Tool-model unavailable.** Point `tool_model` at a slot that is offline and ask the same
   question. The steward must degrade honestly — "tools unavailable" — not hallucinate. Then
   load that slot and confirm tools engage.
6. **read_only guard.** With `read_only=true`, ask the steward to perform a mutating action.
   It must refuse. rc.4 could never reach this guard because tool rounds never fired, so it has
   effectively never been validated — treat it as untested until you see it refuse.
7. **Round limits.** Ask something that would need more than `max_rounds` tool calls. Does it
   stop cleanly and say so?

## Fabrication test (run this one carefully)

Ask two or three questions whose true answers you already know from the box, phrased so a
non-tool answer would be plausible. Any answer that is fluent and wrong, with no tool call
behind it, is a `major` finding on its own — regardless of whether the tools plumbing "works".

## Leave behind

Brain healthy. Note any slot you loaded for the tool-model test.
