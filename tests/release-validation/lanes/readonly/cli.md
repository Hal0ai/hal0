# Lane: cli (read-only)

Drive the `hal0` CLI over SSH (invocation in `CONTEXT.md`). **Read-only verbs only.** When a
command group is unfamiliar, run `--help` first and only run the verbs that clearly read.

## Checks

1. **Surface walk.** `hal0 --version`, `status`, `system-info`, `ports`, `doctor`,
   `doctor perms`, `slot list|status|show <name>|metrics|capacity|logs <name>`,
   `model list|show <id>|store`, `memory status|bank list|ops list`, `config` (read
   subcommands), `upstream list`, `capabilities list`, `agent list|status <name>`, `app`
   (read-only status), `registry` (read-only inspect), `mcp list|status <server>`,
   `auth status`, `board` (read-only verbs), `bench` (list/records), `profile list|show <name>`,
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
   promised, debug log lines leaking above output, colour codes in non-tty output.
6. **Error quality.** Run 2–3 commands with wrong arguments (unknown slot, unknown model,
   missing required arg, a path the `hal0` service user cannot read). The error should tell a
   user what to do next, and must not blame the wrong cause.

## Before you diff CLI state against systemd

Record which other lanes are mutating the box in your window. On a shared box, a slot loaded or
a slot created by another agent mid-lane reads exactly like CLI/systemd drift. Say what was
running; an unattributed drift claim is a suspicion, not a finding.

## Carry-forward

Rollups are cheap to file and cheap to lose track of — when you find several small CLI defects,
enumerate every one individually in your findings even if they will be filed as a single issue
(#1796 collected fourteen in rc.4; #1840 collected thirteen in rc.5).
