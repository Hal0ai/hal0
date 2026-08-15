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
3. **Bundled skills actually landed.** Every directory listed in `skills.external_dirs` in
   `/var/lib/hal0/.hermes/config.yaml` must be non-empty, every skill shipped under
   `/usr/share/hal0/skills` must be reachable from one of them, and
   `.skills_prompt_snapshot.json` must carry a non-empty manifest. Then check the mechanism, not
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
   — the memory lane's drain test wants the install-time datapoint, and only this lane sees it
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

## Carry-forward

Hermes findings have historically been *reporting* defects rather than functional ones. Treat
any gap between what hermes says about itself and what it does as `major`.
