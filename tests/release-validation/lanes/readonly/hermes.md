# Lane: hermes (read-only)

Hermes is the bundled agent. It runs as `hal0-agent@hermes` plus `hermes-gateway`. This lane
asks a single question: **is hermes actually functional, or merely "running"?** Do not send it
chat messages — that is the `hermes-e2e` stateful lane.

## Checks

1. **Units.** `systemctl status hal0-agent@hermes hermes-gateway --no-pager`. Running? Restart
   count? Then `journalctl -u <each> --since -1h | grep -iE 'error|traceback|fail'` — quote only
   decisive lines. A restart loop that systemd is quietly absorbing is a major finding.
2. **Provisioning record, applied to EVERY phase.** Do not check only `smoke_tests`. For each
   phase in `provision.json`, assert `status != ok` whenever `details.warnings` is non-empty,
   whenever a headline probe is `skipped`, or whenever a list the phase exists to populate (e.g.
   `bundled_skills_linked`) is empty. That generalisation is what caught both rc.5 hermes
   findings — #1828 (five EACCES warnings under `context_link: ok`) and #1831 (two headline
   probes permanently skipped under `smoke_tests: ok`). Regression
   `hermes-smoke-ok-over-failures`. When a probe records a SKIP, cross-check its stated reason
   before believing it: rc.5's reason string ("'hal0/agent' not loaded on the gateway") was
   false, and `GET /v1/models/hal0/agent` returned 200 at the same moment.
   Batch this with a live `GET /v1/models/hal0/agent` + `GET /api/slots` check in the SAME
   lane pass -- that lets you tell "brain-slot-caused honest failure" (a smoke_tests
   chat_completions/memory_roundtrip failure that matches a genuinely errored/offline slot at
   that moment) apart from a real preflight-gate regression, without waiting on the slots lane,
   and it re-validates `hermes-smoke-ok-over-failures` and `smoke-preflight-skips-chat-probes`
   every release for free.
2b. **MCP client actually works, not just "provisioned".** `provision.json`'s `mcp_wire` phase
   reporting `status: ok` is NOT sufficient evidence the hermes MCP client can talk to hal0 --
   verify directly: `sudo -u hal0 hermes mcp test hal0-admin` and `hermes mcp test hal0-memory`
   must both connect (not "requires HTTP transport but mcp.client.streamable_http is not
   available", not a ~40s timeout). If either fails, check
   `/var/lib/hal0/venvs/hermes/bin/python -c "import mcp"` -- `ModuleNotFoundError` means the
   venv was built without the `mcp` extra (`installer/agents/hermes/requirements.txt` installing
   `hermes-agent[web]` instead of `hermes-agent[web,mcp]`) and EVERY hal0-admin/hal0-memory tool
   is silently dead behind a green `hermes tools list`/`hermes mcp list`. This is a standing
   check now -- regression `hermes-venv-missing-mcp-extra` found it reproduces on fresh AND
   upgraded boxes alike, so run it everywhere, not just on fresh installs.
3. **Bundled skills actually landed.** Every skill shipped under `/usr/share/hal0/skills` (four
   in 1.4.0) must be reachable from one of the `skills.external_dirs` in
   `/var/lib/hal0/.hermes/config.yaml`, and `hal0 agent status hermes --json`
   `.phases.context_link.details.bundled_skills_linked` must list exactly that set. An empty
   `/var/lib/hal0/skills` is NORMAL on a fresh box (bundled skills land in
   `/etc/hal0/agent-skills`), so do not require every external dir to be non-empty. Then check the mechanism, not
   just the outcome: `ls -ld` each target directory against the `User=` of whatever writes it —
   a `User=hal0` writer against a `root:root` directory is the shape of #1828.
4. **Local endpoints.** The gateway listens on loopback (ports in `CONTEXT.md`; rc.5 used 9119
   for the hermes dashboard and 8642 for the `api_server` OpenAI-compatible platform). Read the
   port assignments out of `gateway_state.json` plus `/api/status` `gateways[].ports` rather
   than trusting last release's numbers — and verify the discovery surface the brief names still
   EXISTS before trusting its curl targets; a renamed field silently voids every downstream
   check. Curl each for a health/status route.
5. **Status-string honesty, and the wheel behind it.** Whatever the unit or CLI says about the
   dashboard must match what it serves. Also check the packaging: `ls
   /var/lib/hal0/venvs/hermes/lib/python3*/site-packages/hermes_cli/web_dist`. The pinned VCS
   wheel shipping no `web_dist` is adjudicated
   (`known-issues: hermes-wheel-missing-web-dist`, open #1829) — what you assert is the honest
   degradation: the STATUS string names the gap, `:9119/api/status` still 200s, and the brain
   board tools answer through the Hermes kanban facade (`hal0.board`). While there, record the installed
   hermes-agent version and compare against the update box's — a fresh install shipping OLDER
   hermes than an upgraded box (0.18.2 vs 0.19.0 in rc.6) is a packaging smell worth one line.
   Check whether the wheel came from the pin (`dist-info/direct_url.json` names the vetted
   commit) before reasoning about what it should contain.
6. **Data dir.** `/var/lib/hal0/.hermes` — config present, sane permissions, error logs. Never
   print a full API key; last four characters only. Also `/etc/hal0/agents/hermes.env` must be
   0600 root:root on a fresh install (#1876 regression probe).
7. **Dependencies.** The hindsight API on loopback: health endpoint, and the bank list if
   discoverable. A fresh box should not have failed memory operations sitting in a bank. Read
   the provisioning smoke-marker retain's op status/retry_count now and record it as a baseline
   — capture the full `shared` bank operations list too
   (`GET http://127.0.0.1:9177/v1/default/banks/shared/operations`), not just the one
   smoke-marker op — the memory lane's drain test wants the install-time datapoint, and only
   this lane sees it
   before the box is churned.
8. **Defaults posture.** No external integration (slack/discord/telegram/etc.) should be
   configured or enabled on a fresh install. Anything enabled by default is a finding.
9. **Wiring.** Session hooks installed by the installer should be allowlisted and actually fire;
   plugin kinds should resolve rather than silently downgrade (both were rc.4 #1795 items).
   Run the `pre_llm_call` state hook by hand as the hermes user (stdin `</dev/null`; it prints `{"context": ...}`) and check its payload is fresh
   *enough*: an `_as_of` older than the hook's own TTL is expected and adjudicated
   (`known-issues.yaml: hermes-state-md-as-of-is-change-marker`) — what is reportable is the
   injected BODY disagreeing with `hal0 slot list` at the moment of injection. Do the diff in
   ONE command batch (hook output and `GET /api/slots` state+model_id in the same SSH
   round-trip), so the _as_of adjudication cannot mask a wrong-body defect — that pairing is
   what caught rc.6's "no chat model loaded" beside two serving slots (regression
   `hermes-state-chat-model-resolver`).

10. **Which `/api/status`?** `:8080/api/status` is hal0's; `gateways[].ports` exists only on the
   hermes dashboard `:9119/api/status`. Use the right one for the wiring check, and note `:9119/`
   itself 404s by design (`hermes-wheel-missing-web-dist`).
11. **(retired in kit 13)** The `.skills_prompt_snapshot.json` check is obsolete (regressions
   `rc6_note`); item 3's `bundled_skills_linked` comparison replaces it.

12. **Two views of one gateway.** Compare `:9119/api/status` `gateway_platforms` (`{}`) with `gateway_state.json` platforms (`api_server` connected). Probably upstream hermes behaviour; one line in the report, not a finding.
13. **Smoke marker needs the retain op.** For the `memory_roundtrip` warn or pass, also read the marker's retain op: `status` `completed` with `facts_committed=0` and no error is a silent zero-fact extraction and explains a warn on a healthy box (#2671).

14. **Smoke `memory_roundtrip` warn is timing or extraction?** Compare the completion time of the smoke marker's retain op (`hal0 memory ops list`) with the smoke run's search time in `provision.json`; completion later than the search means the warn is the #2671 race, not extraction failure.

15. **Run the `pre_llm_call` state hook twice back-to-back.** The first run can lag a slot another lane just loaded; the second must match `/api/slots`. A disagreement that persists on the second run is a finding.

16. **Skip synthetic rows.** `/api/slots` carries a synthetic `hal0` composite entry (`_synthetic=true`, no `state`/`type`); scripts that iterate slots must skip it.

17. **A warn names its probe.** `provision.json` `smoke_tests` with `status: warn` must carry a `failures[]` list whose names match the false entries in `details.results`; a warn with no named probe is a finding (20261016003929: 2 failures, both named). Also confirm `/var/lib/hal0/.hermes/.env` absence is expected: `/api/status` advertises an `env_path` there that does not exist on a fresh install (treated as upstream behaviour, unverified).

18. **Pin.** Hermes is v0.23.3, commit `caafe538`, on fresh and upgraded boxes alike (v0.22.4,
   `4d043038`, is the upgrade baseline: the pin before this bump, still vetted as the flip-back
   target, as is v0.22.2 `d923a5eb`). The live `release.json` must say `describe: v0.23.3`,
   `install: lock` and list `web`, `mcp` and `acp` in `extras`. Read the commit from the install, not from `hermes --version` (that makes a network
   update check and needs tags): the live release's recorded sha, or `git rev-parse HEAD` in its
   checkout. A box on any other sha is a finding.
19. **Session history is kept.** `/var/lib/hal0/.hermes/config.yaml` must carry
   `sessions.auto_prune: false` (ADR-0034 decision 7). On an upgraded box, compare the session
   count (`sudo -u hal0 hermes sessions list` or the `sessions` table in `state.db`) with the
   pre-upgrade baseline; a drop with no operator action is a `major` finding. Also grep the
   gateway journal for prune lines.
20. **Persona prompt reaches Hermes (#2839).** `config.yaml` must carry a non-empty
   `agent.system_prompt` that starts with the active persona's `[persona.prompt] system` text,
   and must NOT carry `agent.system_prompt_prelude` (no Hermes version reads it). hal0 no longer
   writes `display.personality`; a value there is an operator's `/personality` pick, and on the
   first `hermes config migrate` to 0.22 upstream resets it once.
21. **Linger.** With `hermes-gateway.service` installed, `loginctl show-user hal0 -p Linger`
   must say `yes`, the unit must carry `After=user@<uid>.service`, and `hal0 doctor all --json`
   must show `hal0_linger` as `pass`. On a box where Hermes was uninstalled the row must not
   report linger on. On the UPGRADE box, before `sudo hal0 agent install hermes`, expect the
   0.19.0-shape unit and a `warn` row naming that command; after it, `pass`. A kanban card that
   stays queued while the gateway journal logs `spawn of <task> deferred, host cannot place
   the worker` is this case.
22. **MCP client on mcp 2.x.** The v0.23.3 venv carries mcp 2.3, as v0.22.4 did (v0.22.2 had 2.0), which dropped
   `streamablehttp_client`. `provision.json` `mcp_wire` must be `ok` with stage `connected` for
   both servers (not an `ImportError` at stage `import`), and check 2b still applies.
23. **Board token file.** `sudo test -s /var/lib/hal0/state/agents/hermes/dashboard-session-token && echo present`
   must print `present`, and neither `systemctl show hal0-agent@hermes -p Environment` nor
   `config.yaml` may set `HERMES_DASHBOARD_PUBLIC_URL` / `dashboard.public_url`: either one
   turns the dashboard login on; the board's loopback token path is only validated with it off.
24. **No unmanaged gateway (v0.22.3+), passive checks only.** The dashboard's default Chat tab
   runs its turns in the dashboard process (its TUI attaches to the dashboard's own `/api/ws`). A
   profile-scoped chat (the profile switcher, for example `hal0-brain`) and `hermes chat` are
   clients of the gateway daemon, and with no unit file installed they start their own. Here,
   without sending any chat: `hal0 doctor all` must show the `Hermes gateway unit` row green;
   `/etc/systemd/system/hermes-gateway.service` must exist (a masked unit is a symlink to
   `/dev/null` and counts); every profile whose `config.yaml` sets `gateway.standalone: true`
   must have its own `hermes-gateway-<profile>.service`; and
   `ps -u hal0 -o pid,cgroup,args | grep "gateway run"` must show at most one gateway, in
   `hermes-gateway.service`, never a `--idle-exit` one. Any unmanaged gateway is a `major`
   finding, because it runs cron and the kanban dispatcher where the operator turned the gateway
   off. The chat that would provoke one is `hermes-e2e` item 22, in the stateful lane.
25. **Upgrade from v0.22.4.** On the UPGRADE box, the first `hal0 update` must rebuild
   (`release.json` `ref` = `caafe538…`, `previous` = the v0.22.4 release), restart
   `hermes-gateway.service` onto the new release, and leave `config.yaml` at
   `_config_version: 54` with `sessions.auto_prune: false` intact. The session count must match
   the pre-update baseline (item 19). `sqlite3 state.db 'select version from schema_version'`
   must say `33`. Then `sudo hal0 update component hermes --target
   4d04303818b61bf3aa09278af48356315515965b` must be refused with a `downgrade` error and
   build nothing.

26. **Bank id first.** Banks are `hal0:shared` and `hal0:private`; `banks/shared/operations` 404s. Assert the bank id from `/api/memory/banks` before reading any operations URL.

27. **Smoke vs brain-ready.** Compare `provision.json` `phases.smoke_tests.at` with the brain slot's `model loaded` journal timestamp and with any in-flight consolidation (`journalctl -u hal0-slot@brain`). A chat_completions timeout while brain was loading or busy is #3809, not a new chat failure.

28. **Config version.** `grep _config_version /var/lib/hal0/.hermes/config.yaml`; a fresh box has none while `/api/status` reports config_version 0 against latest 54 (51 before v0.23.3) and `hermes_doctor` advises a migrate. Record it; file only if the advice persists after reprovision.

29. **Memory plugin can read its key.** Run the hermes venv python as hal0: `import yaml` must work and the plugin's `_read_agent_key` must be non-None (open #3803). One command, 2 seconds; do it before any Hermes memory check.

30. **The discovery key stays a literal (v0.23.3).** `config.yaml` must carry
   `providers.custom.api_key: hal0-local`, not a `${HERMES_PROVIDER_...}` reference, and
   `/var/lib/hal0/.hermes/.env` must hold no `HERMES_PROVIDER_` line (on a fresh box the file is
   still absent, item 17). From v0.23.0 `hermes config set <...>.api_key` moves the value into
   `.env`; hal0 writes this one through its YAML merge instead. A reference there is a finding.
31. **Both hal0 plugins load on v0.23.3.** `sudo -u hal0 hermes plugins list` (or the gateway
   log at start) must show `hal0-memory` as the active memory provider and no
   `Failed to load user provider plugin hal0` line; `curl -s -H "Authorization: Bearer <key>"
   127.0.0.1:8642/v1/models` must answer. Upstream now imports a `$HERMES_HOME` provider plugin
   from a copy under `/var/lib/hal0/.hermes/runtime/provider-plugins/owned-*/`: the directory
   must exist while the gateway runs, be owned by `hal0`, and hold one `owned-*` entry per
   running Hermes process, not a growing pile.
32. **Terminal-off tool roster (v0.23.3).** On a box with the terminal off, `GET
   /v1/toolsets` (default profile and one named profile) and one turn's `agent.log` tool list
   must show none of `terminal`, `process_manage`, `execute_code`, `delegate_task` or
   `browser_exec`. `browser_exec` is the only browser tool from v0.23.0 and runs host Python;
   upstream offers it only to a session that has `terminal`. Its presence with the terminal
   off is a `critical` finding. The ten `browser_*` tools of v0.22.4 are gone on every box.
33. **Nothing fetched for a feature that left core.** The gateway and agent journals from the
   first start on v0.23.3 must show no `left-core` plugin install attempt and
   `/var/lib/hal0/.hermes/cache/left-core-*.failed` must not exist, unless the operator
   configured Camofox, Singularity, GitHub Copilot or Yuanbao in Hermes (new at v0.23.3; Home
   Assistant, Spotify and Langfuse behaved this way at v0.22.4 too). Record any attempt with
   the feature name.
34. **No idle keep-alives.** With no chat open for 15 minutes, the hal0-api journal must show
   no Hermes-origin `/v1/chat/completions` (v0.23.3 adds a prompt-cache warmer; it should not
   arm for an endpoint with no cache pricing, which is every hal0 slot). One request is a
   finding with its `aux_task` if logged.

## Carry-forward

Hermes findings have historically been *reporting* defects rather than functional ones. Treat
any gap between what hermes says about itself and what it does as `major`.

## Added in kit 18

* Item 22 wording: mcp_wire proof is `details.servers.<name>.client.status == ok` with a tool_count; `details.mcp_client` may record stage `import` with tools [] when no probe URL is given. Do not fail on that.
* `hal0-terminal-off` ERROR and the 'Unknown terminal backend hal0-terminal-off' advisory are by design (known-issues hermes-hook-and-terminal-by-design).
* Item 5 (web_dist) is stale: web_dist ships and :9119/ returns 200; check only that.
* Run the shell-hook output through `agent.shell_hooks._parse_response` for on_session_start, or grep agent.log for 'shell hook stdout was not valid JSON' (#3853).

## Added in kit 19

* Check 29 wording: replace "import yaml must work" with "the plugin's `_read_agent_key` returns non-empty" (#3803 made PyYAML unnecessary).
* Check the mode of `.hermes/config.yaml` against the key it carries.
* On a FRESH box, before any reprovision, `config.yaml` must carry `model.context_length` and the gateway must not log `256000 tokens (default)` (#3875, known).

## Added in kit 20

* Assert the mode of every config file that carries X-hal0-Agent-Key (config.yaml is 0644 on 094401; record, do not file unless world-writable).
* Brief item 12's `gateway_platforms {}` on :9119 is stale: it is populated in 1.8.0. Record the value, do not expect empty.

## Added in kit 21

* Compare /v1/toolsets browser toolset against the model_tools._rewrite_browser_exec gate and record the live per-turn tool roster.
* `hal0 doctor` agent-uid warn on a fresh install is #3134 (open); record it.
* get_model_context_length without config_context_length returning 256000 is by-design since #3867; judge gateway.log 'Model context warmed' and config.yaml model.context_length instead.

## Added in kit 22

* Compute the live tool roster with model_tools.get_tool_definitions under the config's disabled_toolsets rather than reading the /v1/toolsets catalog (it still lists browser enabled:true).
* Record the startup errors.log line 'schema reconcile 503 Session store not initialized' on a fresh install (transient before the gateway is up).
