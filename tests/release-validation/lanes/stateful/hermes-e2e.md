# Lane: hermes-e2e (stateful, order 6)

Hermes end to end, with models actually loaded. You run last precisely because the earlier lanes
leave the box in the state hermes needs — which is itself worth noting, since a real user has to
reach that state on their own.

## Check 0, and it costs 1.3 seconds

Before planning anything else, run the context-window gate:

```sh
grep -rn 'MINIMUM_CONTEXT_LENGTH' \
  /var/lib/hal0/venvs/hermes/lib/python3*/site-packages/agent/model_metadata.py
curl -s $API/v1/models | jq '.data[] | {id, context_length}'
grep -n 'model:' -A3 /var/lib/hal0/.hermes/config.yaml
```

Every chat-capable model hal0 advertises must meet or exceed Hermes' hard floor, and the id
named by `model.default` must resolve. rc.5's GA blocker (#1827) was exactly this mismatch and it
took 1.6 s to hit — but only if you check it before you start composing chat turns. Regression
`hermes-cannot-chat-64k-floor`.

Also verify each entrypoint verb EXISTS before planning around it (`hal0 agent --help`). rc.5's
brief named `hal0 agent smoke <name>`, which does not ship — a missing verb is a recorded finding,
not something to improvise past.

## Checks

1. **Entrypoint.** Find how a user talks to hermes: `hal0 agent` verbs, the hermes CLI on the box
   (check the systemd unit's `User=` and run as the right user), or the dashboard API. The
   dashboard API key lives under `/var/lib/hal0/.hermes` — never print more than its last four
   characters. Restore any config file you modify, byte for byte, and say so.
2. **Plain chat.** Send: *"Reply with exactly: HERMES-RC-OK"* with the SHIPPED config, not a
   patched one. Does a reply come back? Record latency. From the journals, which model did it
   actually use, and did the request reach hal0's `/v1` with a ref that routes?
3. **Memory roundtrip, as a delta not an absolute.** Time a no-memory turn of the same shape
   FIRST, then the memory turn, and report the multiplier. On a contended CPU box the absolute
   is meaningless; a 3x is not. Then:
   * Did the store actually reach the memory backend, or only a local file?
   * Is the recall correct, or a hallucination that merely sounds right?
   * Confirm the configured wiring matches the observed backend.
   * Raw per-turn slowness on CPU is adjudicated (`known-issues.yaml:
     hermes-memory-turn-slow-on-cpu`); slot contention now belongs to
     `hindsight-extraction-shares-utility-slot` and the memory lane's check 7.
4. **Context-window consistency across surfaces.** For the model hermes is bound to, assert
   `/api/slots/<name>.ctx_max` == `/v1/models[<id>].context_length` == the resolved `--ctx-size`
   in the slot unit. Three surfaces, one number — and this is the number the agent's own gate
   reads.
5. **Re-run the provisioning smoke** now that models are loaded, and read the RECORD, not the
   summary line: no phase may report `ok` while carrying `details.warnings`, and no headline
   probe may be silently `skipped` (#1828, #1831). Regression `hermes-smoke-ok-over-failures`.
6. **Gateway.** Confirm what each hermes port is for from `gateway_state.json` and `/api/status`
   `gateways[].ports` rather than from last release's numbers, health-check them, and confirm no
   external integrations are enabled.
7. **Tool use.** Ask hermes something requiring a hal0 tool call (platform state). Same
   fabrication standard as the brain lane: fluent and wrong with no tool call is a finding.

## Leave behind

Hermes running with its SHIPPED config restored. Brain, utility, and embed slots healthy. State
every config file you touched and its final contents in `box_state_on_exit`.
