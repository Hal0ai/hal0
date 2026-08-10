# Lane: hermes (read-only)

Hermes is the bundled agent. It runs as `hal0-agent@hermes` plus `hermes-gateway`. This lane
asks a single question: **is hermes actually functional, or merely "running"?** Do not send it
chat messages — that is the `hermes-e2e` stateful lane.

## Checks

1. **Units.** `systemctl status hal0-agent@hermes hermes-gateway --no-pager`. Running? Restart
   count? Then `journalctl -u <each> --since -1h | grep -iE 'error|traceback|fail'` — quote only
   decisive lines. A restart loop that systemd is quietly absorbing is a major finding.
2. **Provisioning record.** Find the provisioning/smoke record and compare each phase's status
   against its own recorded sub-results. rc.4 recorded 2/6 smoke failures and still reported
   `status=ok` (#1793). This is regression `hermes-smoke-ok-over-failures` — check it here.
3. **Local endpoints.** The gateway listens on loopback (ports in `CONTEXT.md`; rc.4 used 9119
   for the dashboard API and 8642 for the gateway). Curl each for a health/status route. Record
   what 8642 is actually for, from its config — not from assumption.
4. **Status-string honesty.** Whatever the unit or CLI says about the dashboard must match what
   the dashboard actually serves. rc.4 claimed "dashboard reachable" while every route returned
   `{"error":"Frontend not built…"}`. The unbuilt frontend is expected (upstream build artifact,
   not shipped in the wheel); a status string that hides it is not.
5. **Data dir.** `/var/lib/hal0/.hermes` — config present, sane permissions, error logs. Never
   print a full API key; last four characters only.
6. **Dependencies.** The hindsight API on loopback: health endpoint, and the bank list if
   discoverable. A fresh box should not have failed memory operations sitting in a bank.
7. **Defaults posture.** No external integration (slack/discord/telegram/etc.) should be
   configured or enabled on a fresh install. Anything enabled by default is a finding.
8. **Wiring.** Session hooks installed by the installer should be allowlisted and actually fire;
   plugin kinds should resolve rather than silently downgrade (both were rc.4 #1795 items).

## Carry-forward

Hermes findings have historically been *reporting* defects rather than functional ones. Treat
any gap between what hermes says about itself and what it does as `major`.
