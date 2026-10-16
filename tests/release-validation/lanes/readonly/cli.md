# Lane: cli (read-only)

Drive the `hal0` CLI over SSH (invocation in `CONTEXT.md`). **Read-only verbs only.** When a
command group is unfamiliar, run `--help` first and only run the verbs that clearly read.

## Checks

1. **Surface walk.** `hal0 --version`, `status`, `system-info`, `ports`, `doctor`,
   `doctor perms`, `slot list|status|show <name>|metrics|capacity|logs <name>`,
   `model list|show <id>|store`, `memory status|bank list|ops list`, `config` (read
   subcommands), `upstream list`, `capabilities list`, `agent list|status <name>`, `app list`
   (the read verb; there is no `app status`), `mcp list|status <server>`,
   `auth status`, `board` (read-only verbs), `bench status|results|history` (`bench list` is not a verb), `profile list|show <name>`,
   `update --check` (checks only — never apply).
2. **Exit codes, measured properly.** Capture them as `out=$(cmd); rc=$?` — **never through a
   pipe**. Piping to `head` masks the real exit status and silently turns this check into a
   false pass. Any Python traceback reaching the user is a finding regardless of severity
   elsewhere. Any command exiting 0 while printing an error, or non-zero on success, is a
   finding.
3. **Truthfulness against the system.** The CLI's claims must match the box:
   * `hal0 slot list` state vs `systemctl is-active hal0-slot@<name>` for every slot
   * `hal0 status` vs the actual set of running units
   * `hal0 system-info` cores/threads vs `nproc`
   * `hal0 doctor` verdict vs its own warnings, and whether it mentions any unit sitting in
     `systemctl --failed`
   * `hal0 update --check` against the staged manifest — real versions and digests
4. **Same fact, two commands.** Where two verbs report the same underlying thing they must
   agree. In particular:
   * `hal0 profile show <name>` field by field against `GET /api/profiles/<name>` and against
     the matching row of `hal0 profile list` — rc.5's new profile CLI drops `used_by` on the
     detail view (#1840 item 11)
   * the aggregate `hal0` pseudo-slot across `slot list`, `slot show hal0` and `slot logs hal0`
     — and note that its `/api/slots` entry carries no `state` key, so naive `s["state"]`
     iteration KeyErrors on it
   * every slot's declared `context_size` against the `--ctx-size` in its resolved command; a
     clamp must be visible to the user, not only in the hal0-api journal
5. **Presentation.** Broken tables, unformatted raw floats, empty output where a table was
   promised, debug log lines leaking above output. Colour codes in non-tty output: sweep all
   captured output in one pass with `grep -cP '\x1b\['` rather than eyeballing. Zero bytes where
   ANY output was promised is its own class — `hal0 bench history` with no matching rows printed
   nothing at rc=0 for three releases (regression `bench-history-blank-empty-state`); when a
   read verb returns empty, check its siblings print an explicit empty-state line. Panel-rendered
   JSON visually clipping at terminal width is by-design
   (`known-issues: cli-slot-show-panel-clips-long-json-lines`) — verify `--json` carries the full
   value before filing truncation.
6. **Error quality.** Run 2–3 commands with wrong arguments (unknown slot, unknown model,
   missing required arg, a path the `hal0` service user cannot read). The error should tell a
   user what to do next, and must not blame the wrong cause.
7. **Perms audit on the install-time state.** `hal0 doctor perms` (no --fix) as early as
   possible, and record the exit code: on a pristine install it should be green, and rc.6's was
   not (regression `doctor-perms-never-converges`). Date any DRIFT row against the installer's
   own `--fix` backstop by **ctime** (chmod bumps ctime, not mtime) so install-time drift is
   distinguishable from lane damage. Never assert a drift row COUNT — it grows with every slot
   load and model pull. The secrets-dir mode is declared 0700 (#1896; was 0755, now fixed —
   `known-issues: doctor-perms-secrets-dir-0700-is-declared`). A `split-brain /root/.hermes` row
   is NOT automatically a regression against #843 — before filing, date `/root/.hermes`'s birth
   timestamp against the installer log's last mtime: a birth timestamp AFTER install-completion
   means an agent or operator ran the raw hermes venv binary as root during this run, which is
   the already-adjudicated by-design detector working correctly
   (`known-issues: doctor-perms-split-brain-root-hermes-is-correct-detection`), not installer
   drift. Cross-check the run every release against `doctor-perms-never-converges` (#1896)
   specifically — it is cheap, directly falsifiable, and belongs in the regression register as a
   standing line item rather than a fresh guess each time.
8. **Service-user writability of state the daemon writes.** For each dir/db under
   /var/lib/hal0 that hal0-api writes (model-pull-jobs, activity.db, hal0.db, slots/, ...):
   `sudo -u hal0 test -w <path>` plus one journal grep for `persist_failed|init_failed`. A
   root-owned state path the ownership table has no row for is the shape of the rc.6
   `model-pull-jobs-root-owned` finding, and `doctor perms --fix` cannot heal what the table
   does not name.

## Before you diff CLI state against systemd

Record which other lanes are mutating the box in your window. On a shared box, a slot loaded or
a slot created by another agent mid-lane reads exactly like CLI/systemd drift. Say what was
running; an unattributed drift claim is a suspicion, not a finding. Before treating ANY CLI/API
state drift as a finding, diff the current model count / slot state against CONTEXT.md's
documented run-start baseline, and check mtimes on staged model paths — if a model or slot
appeared after the preflight snapshot and it isn't yours, it is very likely a concurrently-
running stateful lane, not a defect. Attribution can take longer than the drift itself is
subtle: this run's contention first showed up 30+ minutes into a lane, well after the initial
baseline diff, so don't stop checking after the first comparison.

9. **State-dir file ownership, not just directories** — regression `components-json-root-owned`
   (#2670). `stat -c '%U:%G %a'` every file under `/var/lib/hal0/state` and run `sudo -u hal0 test
   -w` on each; `journalctl -u hal0-api | grep -c state_persist_failed` since install. Do this
   before any restart: the file heals itself on the first hal0-api write.
10. **Verbs named in this brief must exist.** `hal0 registry` is NOT a 1.4.0 verb; confirm every
   verb with `--help` before planning around it and record a missing one as a finding rather than
   improvising. `hal0 agent reprovision`, `hal0 update --rollback` and anything that prompts read
   stdin: run them with `</dev/null` and `--yes`.
11. **Lane-start sanity.** Assert that the run `CONTEXT.md` names the box you were dispatched to (it
   describes ONE box; ct151 lanes were handed ct150's file this release). If it does not, say so in
   your report and take identity from `boxes.toml` plus live probes.

12. **Unknown names must fail.** `hal0 agent status nosuch`, `hal0 agent log nosuch`, `hal0 slot show nosuch`, `hal0 profile show nosuch`, `hal0 mcp status nosuch`: record each exit code. Siblings exit 1; `agent status` exits 0 (regression `polish-rollup-1-4-0-nightly-20261015`, #2706). An un-bootstrapped KNOWN agent (`pi`) exiting 0 is correct.
13. **`doctor perms` against reality.** For every file under `/var/lib/hal0/state` and `/var/lib/hal0/registry` compare `sudo -u hal0 test -w` with the `doctor perms` verdict, and run `find /var/lib/hal0 -user root ! -path '*/secrets*' -type f`. Doctor has no row for `state/components.json` or `registry/*.lock` (#2670, #2698), so root-owned files it calls clean are findings. Run it right after install, before any restart: components.json heals on the first hal0-api write; record how many minutes that took.

14. **`doctor perms` at the END of the lane too.** Re-run `hal0 doctor perms; echo rc=$?` after any agent-API call (`curl $API/api/agents/hermes/session/handshake`) and `stat -c '%U:%G %a' /var/lib/hal0/agents`: expect `hal0:hal0 711` (#2719, daemon-created paths that disagree with the table appear only after first use).

15. **`config validate` against the daemon.** Compare `hal0 config validate` with `journalctl -u hal0-api -b | grep -E 'ref_slot_load_failed|auto_scan_prune_skipped'` and with `hal0 doctor profiles | grep 'does not load'`. Validate passing while the daemon refuses a slot TOML is #2720.

16. **Dropped flags and dead hints.** `hal0 update --target 9.9.9 --check; echo rc=$?` must not read like a bare `--check` (#2729); `hal0 slot unload --help` must list the option the 409 `slot.pinned` message tells the user to pass.

17. **Doctor honesty.** The "passed with N warning(s)" count equals the number of distinct warnings (a continuation line was counted, N=5 vs 4); an installed live host prints no install-time advice (node missing, ports in use, "will build the memory-engine venv"); and `hal0 doctor` is not silent while `systemctl --failed` lists a failed podman healthcheck transient.

18. **`doctor verify` capability row against the selections.** `hal0 doctor verify | grep -i 'Capability slots'` vs `curl -s $API/api/capabilities | jq '[.selections[][]|select(.enabled==true)]|length'` (and `GET /api/doctor`, id `HAL0-CAPABILITIES-NONE`). `PASS 3 active: embed, img, voice` while every child is `enabled=false` is #2736; an enabled-but-idle on-demand selection may fairly count. Also compare with the set of slots actually ready and model-bound.

19. **Non-interactive `slot delete`.** On a throwaway slot (`slot create ... -m <registered chat model>`; `-m` is required): `hal0 slot delete <n> </dev/null; echo rc=$?` (expect rc 1 "Aborted." and a hint, today none), `echo y | hal0 slot delete <n>` works, `echo y | hal0 slot delete utility` is refused 409 `slot.pinned`. There is no `--yes`, and `--force` has no help text and a stale seeded-slot docstring (#2740 items 6-7). Leave nothing behind.

21. **`doctor verify` Runners row.** `PASS Runners N/N slot(s) healthy` counts every configured slot not in `state=error` (offline, model-less and never-loaded included; the synthetic `hal0` row is not counted): by design, `known-issues: doctor-runners-healthy-counts-offline`. Compare N with `curl -s $API/api/health/system | jq .checks.slot_manager`. Report only its `still_report_if` (PASS beside an errored slot, composite counted, detail text claiming "serving").

20. **Doctor on a release tree.** `HAL0_PLAIN=1 hal0 doctor </dev/null` ends `passed with N warning(s)` on a healthy box. Count the distinct warnings yourself; node-missing and own-port warnings on a signed release tree (no `ui/package.json`) are #2740 items 1-3, not new findings. Also `hal0 doctor profiles; echo rc=$?` with a non-loading slot TOML present: rc 0 is item 5.

## Carry-forward

Rollups are cheap to file and cheap to lose track of — when you find several small CLI defects,
enumerate every one individually in your findings even if they will be filed as a single issue
(#1796 collected fourteen in rc.4; #1840 collected thirteen in rc.5).
