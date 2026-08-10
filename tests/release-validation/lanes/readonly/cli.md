# Lane: cli (read-only)

Drive the `hal0` CLI over SSH (invocation in `CONTEXT.md`). **Read-only verbs only.** When a
command group is unfamiliar, run `--help` first and only run the verbs that clearly read.

## Checks

1. **Surface walk.** `hal0 --version`, `status`, `system-info`, `ports`, `doctor`,
   `slot list|status|show <name>|metrics|capacity|logs <name>`, `model list|show <id>|store`,
   `memory status`, `config` (read subcommands), `upstream list`, `capabilities list`,
   `agent list|status <name>`, `app` (read-only status), `registry` (read-only inspect),
   `mcp list|status <server>`, `auth status`, `board` (read-only verbs), `bench` (list/records),
   `profile` (if it exists — rc.4 had 17 server-side profiles with full CRUD API and **no CLI
   at all**; check whether that gap closed), `update --check` (checks only — never apply).
2. **Exit codes and tracebacks.** Any Python traceback reaching the user is a finding regardless
   of severity elsewhere. Any command exiting 0 while printing an error is a finding.
3. **Truthfulness against the system.** The CLI's claims must match the box:
   * `hal0 slot list` state vs `systemctl is-active hal0-slot@<name>` for every slot
   * `hal0 status` vs the actual set of running units
   * `hal0 system-info` cores/threads vs `nproc` (rc.4 leaked host cores into a container view,
     producing an impossible "cores/threads 16 / 8")
   * `hal0 doctor` verdict vs its own warnings (rc.4 printed six `!!` warnings then "OK all
     pre-flight checks passed")
   * `hal0 update --check` against the staged manifest — real versions and digests, not a
     placeholder
4. **Presentation.** Broken tables, unformatted raw floats, empty output where a table was
   promised, debug log lines leaking above output, colour codes in non-tty output.
5. **Error quality.** Run 2–3 commands with wrong arguments (unknown slot, unknown model,
   missing required arg). The error should tell a user what to do next.

## Carry-forward

Rollup issue #1796 collected fourteen items from this lane in rc.4. Rollups are cheap to file
and cheap to lose track of — when you find several small CLI defects, enumerate every one
individually in your findings even if they will be filed as a single issue.
