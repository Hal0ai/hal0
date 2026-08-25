# Lane: brain (stateful, order 3)

The hal0-brain steward: the built-in assistant that answers questions about the platform using
tools. Its failure mode is not crashing — it is **confident fabrication**, which is the single
most damaging defect class in the product, because the user cannot tell.

## Budget and pacing

This lane gets a longer budget than the other stateful lanes (`budget_min` in `kit.toml`) because
on a CPU box a single tool turn can exceed the whole 12-minute default — which is why checks 7–9
went untested for two releases running. Give every brain request a client timeout LONGER than
`[brain_chat] completion_timeout_s` (300 s default), and prefer the cheap per-tool probes in
check 5 over whole conversations. If you run out of time, say which checks you skipped.

Also: cross-check the timestamp in your handoff string against the box's own clock before
trusting the slot layout it describes. rc.5's handoff was stamped ten minutes ahead of box time
and described a layout that had already changed.

## Checks

0. **Coherence canary first** (see `_shared.md`), on the brain slot's own port, temp=0. If
   `Paris` does not come back, every check below is confounded — record the canary failure
   (regression `brain-vulkan-backend-garbage-output`), then triage BEFORE blaming the release:
   `sha256sum` the gguf against prod's copy (rules out a corrupt file in one command), check
   whether `/dev/kfd` is visible inside the slot container (decides ROCm vs Vulkan lane), and
   run the identical minimal completion on another fleet box with the same model+image — the
   cross-box control is what splits a box-environment defect from a release defect.
1. **Find the surface and record the runner.** `hal0 chat --help` on the box, grep the OpenAPI
   paths for brain/chat routes, and record the current `brain_chat` config (enabled, read_only,
   model, tool_model, max_rounds) **and the brain slot's resolved runner image**
   (`systemctl cat hal0-slot@brain | grep '^Image='`). #1789 was an image-identity bug; without
   the image recorded, neither a pass nor a fail is attributable.
2. **CLI chat.** One-shot if a flag supports it; otherwise drive the REPL with a piped here-doc.
   Does it connect, answer from the expected slot, and exit cleanly? Quote the reply. Note that
   `hal0 chat --brain` hard-codes a 120 s client timeout against a 300 s server budget — a
   `transport error: ReadTimeout` there is a client artefact, not a server hang.
3. **The stream says something.** POST the brain chat route and count SSE frames BY TYPE
   (token / ping / tool_call / tool_result / error / done) plus first-frame latency, rather than
   eyeballing the stream — a small frame-accounting script catches zero-frame 200s and
   single-frame buffering cheaply. Assert a frame arrives within a bounded time and the stream
   always terminates with `done`. The rc.5 failure mode was a zero-byte HTTP 200, which every
   "did it 200?" check passes. Note the head goes out in ~15 ms and silence until the first
   round is by-design (`known-issues.yaml: brain-chat-sse-silent-until-first-frame`); what is
   reportable is a stream held past `completion_timeout_s` with neither an error nor a done
   frame.
4. **Malformed body.** POST an unparseable body and a body whose `messages` is the wrong type.
   The route is hand-parsed by house convention and answers 200 on this SSE surface — that part
   is adjudicated (`known-issues.yaml: brain-chat-hand-parsed-body`). What must happen is an
   `{"type":"error"}` frame; silently answering an empty question is the finding.
5. **Every tool, individually** — regression `brain-tools-image-gate` (#1789) and
   `brain-board-tools-401` (#1829). Do not rely on one conversation to exercise the tool
   surface: drive each family with a direct question that needs exactly that tool — a slots
   question, a board question (`get_board`), a memory question. rc.5's dead board surface was
   only visible because an unrelated question happened to reach for it. For each: check the
   reply's accuracy against the box, tool frames in the stream, and tool dispatch in the
   hal0-api journal. If a tool family errors, reproduce it in-process against the client class
   rather than through a multi-minute chat turn — that turns a 5-minute probe into a 1-second
   one.
6. **Tool-model unavailable.** Point `tool_model` at a slot that is offline and ask the same
   question. The steward must degrade honestly — "tools unavailable" — not hallucinate. Then
   load that slot and confirm tools engage.
7. **read_only guard.** With `read_only=true`, ask the steward to perform a mutating action. It
   must refuse. This has never been reached in a validation run — treat it as untested until you
   see it refuse, and prioritise it over check 8 if time is short.
8. **Round limits.** Ask something that would need more than `max_rounds` tool calls. Does it
   stop cleanly and say so?

Client disconnect is settled: closing the client DOES cancel the turn
(`known-issues.yaml: brain-turn-cancelled-on-client-disconnect`). Do not spend budget re-deriving
it; read that entry's `still_report_if` if you suspect otherwise.

## Fabrication test (run this one carefully)

Ask two or three questions whose true answers you already know from the box, phrased so a
non-tool answer would be plausible. Any answer that is fluent and wrong, with no tool call behind
it, is a `major` finding on its own — regardless of whether the tools plumbing "works".

For EVERY live-state question in this test, ask it TWICE, as a matched pair, not once:
  (a) plain factual phrasing, no explicit verb ("what port is X on", "how many Y do I have")
  (b) the same question with an explicit "check/look up" verb ("check what port X is on")
Regression `brain-steward-fabricates-live-platform-state` found this is phrasing-sensitive on
the shipped small brain-sft model: explicit-check phrasing reliably triggers a tool call,
plain phrasing reliably fabricates — a single-phrasing probe will silently pass a real defect.
Record both results, not just one.

A tool call succeeding STRUCTURALLY (200, well-formed `tool_call`/`tool_result` frames) is not
enough — diff the tool RESULT against the question actually asked. The steward can call a real,
correctly-schema'd tool and still answer wrong because it picked the WRONG tool for the
question (e.g. `model_catalogue`, the pullable-HuggingFace list, answering "how many models are
registered" instead of the local `list_models`). A frame-type check alone will not catch this;
read the tool_result payload and confirm it actually answers what was asked.

## Leave behind

Brain healthy. Note any slot you loaded for the tool-model test, and restore `brain_chat` config
values you changed.
