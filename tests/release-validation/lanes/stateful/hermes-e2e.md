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
brief named `hal0 agent smoke <name>`, which does not ship (the re-run verb is `hal0 agent reprovision`) — a missing verb is a recorded finding,
not something to improvise past.

Record `hal0 agent reprovision hermes` `provision.json` `completed_at` at lane entry: an earlier lane's reprovision overwrites the install-time ground truth (it was already rewritten at 01:45 in 20261016003929). Record the hermes-agent version (dist-info) on every box in one table so the fresh versus update packaging comparison is automatic. Then read the provision record's `completed_at` and `smoke_tests.details.failures` BEFORE any
live chat: it is install-time ground truth, and it distinguishes "broken out of the box" from
"broken by lane leave-behind state" for free.

## Checks

1. **Entrypoint.** Find how a user talks to hermes: `hal0 agent` verbs, the hermes CLI on the box
   (check the systemd unit's `User=` and run as the right user), or the dashboard API. The
   dashboard API key lives under `/var/lib/hal0/.hermes` — never print more than its last four
   characters. Restore any config file you modify, byte for byte, and say so.
2. **Plain chat.** Send: *"Reply with exactly: HERMES-RC-OK"* with the SHIPPED config, not a
   patched one. Does a reply come back? Record latency. From the journals, which model did it
   actually use, and did the request reach hal0's `/v1` with a ref that routes? If the turn
   HANGS, triangulate before blaming either side: (a) client side — `ss -tnp` plus
   `/proc/<hermes pid>/wchan` (an ESTAB socket to :8080 with a poll/recv loop proves
   request-in-flight, not a client stall; strace shows whether it is consuming a
   never-terminating stream — regression `hermes-stream-no-stall-guard`); (b) upstream side —
   hit the serving slot's own port with the SAME streamed request and read `finish_reason`
   (forever-null means the model, not hermes — the coherence canary in `_shared.md` would have
   caught it earlier); (c) dispatcher side — correlate `dispatch.decision` →
   `dispatch.forward_failed` per request_id to MEASURE the timeout rather than asserting it, and
   match forwarded ReadTimeouts on upstream/model — the timeout lines in your window are usually
   the hindsight extraction backlog, not your turn.
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
   `/api/slots/<name>.ctx_effective` == `/v1/models[<id>].context_length` == the resolved `--ctx-size`
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
8. **Unreadable-cwd probe, 2 seconds.** From `/root`, run one `hermes -z` turn through the
   CANONICAL `/usr/local/bin/hermes` wrapper. It must not die with
   `[Errno 13] Permission denied: '/root/.git'` — the wrapper's cwd-safety guard exists for
   exactly this and its absence on a fresh install is the finding
   (`known-issues: hermes-cwd-eacces-crash`; the raw venv binary failing is by-design).
9. **Doctor-perms convergence, post-mutation (#1942/#1896).** You are the last stage of the
   serialized chain — by now the box has seen slot loads, a model pull, and at least one
   STATE.md render. Re-run `hal0 doctor perms >/dev/null 2>&1; echo rc=$?` — must be `0`, same
   as the readonly lane saw on the fresh box. Then verify the same facts WITHOUT the tool:
   `stat -c '%U:%G %a' /var/lib/hal0/secrets /var/lib/hal0/secrets/agents
   /var/lib/hal0/STATE.md /var/lib/hal0/model-pull-jobs` and confirm owner/group/mode match
   the perms table (root:root for secrets/, hal0:hal0 for daemon-written paths). doctor perms
   does audit all three axes (`PermDiff`), but its exit code is a self-report from the same
   table the installer wrote — the point of the manual stat is independent evidence that the
   table itself matches what a mutated box actually holds, and it is what proves
   green-after-mutation on a REAL box rather than the unit-test fixture (#1942's tests cover
   the mode axis; the live ownership axis is checked nowhere else).

10. **Reprovision idempotency of secrets** — regression `hermes-reprovision-rotates-gateway-key`
   (#2667). Record the last 4 chars of `API_SERVER_KEY` in the vault and in the running gateway's
   `/proc/<pid>/environ` before and after `hal0 agent reprovision hermes </dev/null` (it reads
   stdin). They must be unchanged, the phase outcome `present`, and `gateway_secrets_wire` must not read `ok` when the vault and gateway keys differ (no restart in between). Record the venv Python version
   (`/usr/lib/hal0/venv/bin/python -V`): the rotation needs 3.14+. Restore with `systemctl restart
   hermes-gateway`. Also diff `/var/lib/hal0/.hermes/config.yaml` around the reprovision (it adds
   model_aliases, plugins.enabled, _config_version).
11. **Serving slot per alias, memory headers, latency.** For `hal0/agent`, `brain`, `hal0/brain` and
   the bare model id, read `dispatch.decision` upstream= and assert it matches `GET
   /v1/models/<id>` name (`dispatch-slot-alias-ignores-lane-pin`,
   `virtual-model-entry-first-row-not-matched-slot`). Memory recall checks must send `X-hal0-Agent:
   hermes` and `X-hal0-Private: 1`; anonymous calls cannot see private facts. Record whether
   prefetch recall fires on every turn and per-turn retain latency (Hindsight op created_at to
   completed_at, p50 and max) against the 'working GPU acceleration' clause of
   `hindsight-extraction-shares-utility-slot`. `hermes-smoke-memory-roundtrip-race`: a smoke_tests
   memory_roundtrip warn is expected on a first provision; confirm a matching retain op completed
   shortly afterwards.
12. **Tool-result truncation cliff** — regression `hermes-slot-list-payload-truncated` (#2662).
   Count slots (`curl $API/api/slots | jq length`) and `slot_list` payload size; a hermes tool
   message ending '[Truncated: tool response was N chars' means the answer is not trustworthy
   (cliff ~39,321 chars at ctx 65536, ~14 slots). Under it, answer correctness is the check; do not
   create slots just to cross it.

13. **Quiet vs contended utility.** Run the slot_list tool turn twice, once quiet and once with `requests_processing >= 2` on utility (`:<port>/metrics`); assert the answer and `journalctl -u hal0-slot@utility` has no 'failed to find a memory slot'. Then two concurrent ~35k-token prompts at the utility port (announce first): neither may 500 'Context size has been exceeded' (`utility-profile-shared-kv-pool-overflow`).
14. **Aliases after reprovision.** `model_aliases` in hermes `config.yaml` must name only slots/models that exist (#2704); an alias left by a deleted slot is a finding, leftovers from another lane are attribution only. Count in `slot_list` must exclude the synthetic `hal0` row, or a model counting ready slots from the payload gets the wrong number (#2697).
15. **Make the cwd probe real, and time the turn.** c8 cannot fail unless `/root/.git` exists and is unreadable: create one first (and remove it). Record the split of a trivial hermes turn (~24 s on ct151) into startup/MCP discovery (208 tools), dispatch, and the post-turn `/api/memory/add` x2.

16. **Time-to-quiet per turn.** After your hermes turns sample utility `/metrics` requests_processing/deferred and `hal0 memory ops list` every 60 s for 10 min; record retain + consolidation ops per turn (2 retains plus a consolidation here). Also `-o short-iso-precise` journal timestamps per turn to split startup / MCP discovery / dispatch / post-turn memory/add. Makes the "GPU box turn > 120 s" clause measurable and catches #2716.

17. **First-session perms.** `hal0 doctor perms; echo $?` and `stat -c '%U:%G %a' /var/lib/hal0/agents` before and after the first dashboard/agent-chat session call (expect 711; `absent`/rc 0 before and `0700` DRIFT rc 1 after is #2719, the chmod runs only when `secret.bin` is first created).

18. **Which store did the memory turn reach?** Assert whether the fact went to the builtin `/var/lib/hal0/.hermes/memories/MEMORY.md` or to Hindsight, and run the recall turn with MEMORY.md moved aside so a correct recall must come from the backend. Restore the file.

19. **Fallback target vs smoke anchor.** Compare the `hal0/agent` fallback dispatch target (`dispatch.decision upstream=`) with the slot named in the provision smoke ("slot brain"); fail when they differ, and compare `hal0/brain` vs `hal0/agent` latency for an 8-token request when utility shares brain's model.

21. **Remember-then-recall timing.** Reply and wall time for a 'remember X' turn then an immediate recall turn with MEMORY.md moved aside, once on a quiet box and once with retains in `processing`; this gives a clean number for the 120 s GPU-box clause and separates `hermes-memory-turn-slow-on-cpu` from #2734. The recall answer must be correct or an explicit "not found", never a fluent wrong value; record whether the retain op was still pending. Assert the turn's queue drains: every `hermes -z` turn must leave an empty non-completed Hindsight ops queue within N minutes (record the retain created-to-updated series, 46-221 s seen) via `GET /api/memory/banks/shared/operations`; the post-turn `/api/memory/add` pair blocked hermes exit ~17 s behind Hindsight work on a shared utility slot.

Run order inside this lane: the reprovision checks (10) restart the gateway under any concurrent probe, so do them last; check 12's note about `[Truncated:` is read from `state.db`.

## Leave behind

Hermes running with its SHIPPED config restored. Brain, utility, and embed slots healthy. State
every config file you touched and its final contents in `box_state_on_exit`.
