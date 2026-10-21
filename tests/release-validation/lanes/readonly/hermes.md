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
   board tools work through the local BoardStore rewire. While there, record the installed
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
   Run the `on_session_start` hook by hand as the hermes user and check its payload is fresh
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

15. **Run the `on_session_start` hook twice back-to-back.** The first run can lag a slot another lane just loaded; the second must match `/api/slots`. A disagreement that persists on the second run is a finding.

16. **Skip synthetic rows.** `/api/slots` carries a synthetic `hal0` composite entry (`_synthetic=true`, no `state`/`type`); scripts that iterate slots must skip it.

17. **A warn names its probe.** `provision.json` `smoke_tests` with `status: warn` must carry a `failures[]` list whose names match the false entries in `details.results`; a warn with no named probe is a finding (20261016003929: 2 failures, both named). Also confirm `/var/lib/hal0/.hermes/.env` absence is expected: `/api/status` advertises an `env_path` there that does not exist on a fresh install (treated as upstream behaviour, unverified).

18. **Pin.** Hermes is v0.22.4, commit `4d043038`, on fresh and upgraded boxes alike (v1.6.0
   shipped v0.22.2, `d923a5eb`, which stays vetted as the flip-back target). Read the commit from the install, not from `hermes --version` (that makes a network
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
22. **MCP client on mcp 2.x.** The v0.22.4 venv carries mcp 2.3 (v0.22.2 had 2.0), which dropped
   `streamablehttp_client`. `provision.json` `mcp_wire` must be `ok` with stage `connected` for
   both servers (not an `ImportError` at stage `import`), and check 2b still applies.
23. **Board token harvest.** `curl -s http://127.0.0.1:9119/ | grep -c __HERMES_SESSION_TOKEN__`
   must be at least 1, and neither `systemctl show hal0-agent@hermes -p Environment` nor
   `config.yaml` may set `HERMES_DASHBOARD_PUBLIC_URL` / `dashboard.public_url`: either one
   turns the dashboard login on and the token disappears from the page.
24. **Dashboard chat runs through the gateway (v0.22.3+).** The Ink TUI behind the dashboard's
   Chat tab (`/api/pty`) is now a client of the gateway daemon: it finds it through
   `/var/lib/hal0/.hermes/gateway.sock` and never runs turns itself. With
   `hermes-gateway.service` active, a Chat-tab prompt must get an answer and
   `ps -u hal0 -o pid,cgroup,args | grep "gateway run"` must show exactly one gateway, in
   `hermes-gateway.service`. A second `gateway run` in the `hal0-agent@hermes` cgroup (an
   unmanaged gateway the TUI spawned) is a `major` finding. With the gateway stopped, record
   what the Chat tab shows; a hang with no error is a finding.
25. **Upgrade from v0.22.2.** On the UPGRADE box, the first `hal0 update` must rebuild
   (`release.json` `ref` = `4d043038…`, `previous` = the v0.22.2 release), restart
   `hermes-gateway.service` onto the new release, and leave `config.yaml` at
   `_config_version: 51` with `sessions.auto_prune: false` intact. The session count must match
   the pre-update baseline (item 19).

## Carry-forward

Hermes findings have historically been *reporting* defects rather than functional ones. Treat
any gap between what hermes says about itself and what it does as `major`.
