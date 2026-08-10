# Lane: hermes-e2e (stateful, order 6)

Hermes end to end, with models actually loaded. You run last precisely because the earlier lanes
leave the box in the state hermes needs — which is itself worth noting, since a real user has to
reach that state on their own.

## Checks

1. **Entrypoint.** Find how a user talks to hermes: `hal0 agent --help` verbs, the hermes CLI on
   the box (check the systemd unit's `User=` and run as the right user), or the dashboard API.
   The dashboard API key lives under `/var/lib/hal0/.hermes` — never print more than its last
   four characters.
2. **Plain chat.** Send: *"Reply with exactly: HERMES-RC-OK"*. Does a reply come back? Record
   latency. From the journals, which model did it actually use, and did the request reach hal0's
   `/v1` with a ref that routes? rc.4 logged a per-session `dispatch.no_route` for `hal0/agent`
   before falling back — noise that indicates the wiring is wrong even when the answer arrives.
3. **Memory roundtrip.** Ask hermes to remember a marker (*"Remember: the validation marker is
   MARKER-7734"*), then in a separate turn ask it to recall it. Use generous timeouts and watch
   both the `hal0-agent@hermes` and `hal0-api` journals.
   * Did the store actually reach the memory backend, or only a local file? rc.4 wrote the
     marker to a local `USER.md` and nothing else, making recall impossible.
   * Is the recall correct, or a hallucination that merely sounds right?
   * Which backend was it supposed to use — hindsight via hal0's memory MCP, or its own store?
     Confirm the configured wiring matches the observed behaviour.
   * On slowness: see `known-issues.yaml: hermes-memory-turn-slow-on-cpu`. Minutes on a CPU box
     with a tiny model is expected. What is *not* expected is a turn never completing, or the
     retain op and the chat turn contending for the same slot — that would deadlock single-GPU
     boxes too, and is `major`.
4. **Re-run the provisioning smoke** now that models are loaded (`hal0 agent smoke <name>` or
   whatever the current verb is). Do the previously failing phases pass? And — regression
   `hermes-smoke-ok-over-failures` (#1793) — does a phase with recorded failures now report
   warn/fail rather than ok? Verify by reading the record, not the summary line.
5. **Gateway.** Confirm what the gateway port is for from its config, health-check it, and
   confirm no external integrations are enabled.
6. **Tool use.** Ask hermes something requiring a hal0 tool call (platform state). Same
   fabrication standard as the brain lane: fluent and wrong with no tool call is a finding.

## Leave behind

Hermes running. Brain, utility, and embed slots healthy.
