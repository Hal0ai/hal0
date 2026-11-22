#!/bin/sh
# hal0 Hermes pre_llm_call hook — inject live system state (#3853).
#
# Wired by hermes_provision.STATE_HOOK under hooks.pre_llm_call. The pinned
# Hermes (v0.23.3, same as v0.22.4) contract:
#   * stdin is a JSON payload; pre_llm_call carries extra.is_first_turn.
#   * stdout must be a JSON object; {"context": "<text>"} is appended to the
#     turn's user message. Raw text is logged as "not valid JSON" and dropped
#     (agent/shell_hooks.py _parse_response). on_session_start, where this
#     hook used to live, ignores its return value entirely.
#   * the hook runs inside a 2 s timeout.
#
# We inject on the FIRST turn of a session only: the block is a session-start
# snapshot, and repeating it every turn would grow every later prompt. A run
# with no payload (an operator running the script by hand) is treated as a
# first turn so the output can be inspected.
#
# We ONLY read the pre-rendered STATE.md (the expensive probe runs in the
# writers, not here). If STATE.md is older than the TTL we additionally kick
# a DETACHED background refresh so the NEXT session is fresh; we never block
# this turn on a probe.
set -eu

# Runtime snapshot lives under the hal0-owned /var/lib/hal0 (#473) so the
# User=hal0 render-context writer can produce it under the hermes sandbox.
STATE_FILE="${HAL0_HERMES_STATE_FILE:-/var/lib/hal0/STATE.md}"
TTL_SECONDS=300   # 5 min — defense-in-depth for missed change events.

# Read the whole payload (Hermes writes it to a pipe and closes it). A
# terminal on stdin means a hand run: there is no payload to read.
payload=""
if [ ! -t 0 ]; then
    payload=$(cat)
fi

# Not the first turn: emit nothing. In the JSON payload a string value can
# only hold an escaped quote (\"), so this bare-quoted key matches the real
# field, never text inside a user message.
if printf '%s' "$payload" | grep -Eq '"is_first_turn"[[:space:]]*:[[:space:]]*false'; then
    exit 0
fi

# Missing or empty file (first boot before any render) => emit nothing.
[ -s "$STATE_FILE" ] || exit 0

# Stale? Kick a detached, output-discarded refresh. setsid+& so it never
# holds up the turn even if the daemon probe is slow.
now=$(date +%s)
mtime=$(stat -c %Y "$STATE_FILE" 2>/dev/null || echo "$now")
age=$(( now - mtime ))
if [ "$age" -gt "$TTL_SECONDS" ]; then
    setsid /usr/local/bin/hal0-agent hermes render-context >/dev/null 2>&1 </dev/null &
fi

# Emit {"context": "<STATE.md>"} as one JSON object. Control characters other
# than tab, newline and CR are dropped; sed escapes backslash, quote, tab and
# CR (sed, not awk gsub: gsub's backslash handling differs between mawk and
# gawk), and awk joins the lines with \n.
tab=$(printf '\t')
cr=$(printf '\r')
tr -d '\000-\010\013\014\016-\037' <"$STATE_FILE" \
    | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e "s/${tab}/\\\\t/g" -e "s/${cr}/\\\\r/g" \
    | awk '
BEGIN { ORS = ""; printf "%s", "{\"context\": \"" }
{ if (NR > 1) printf "%s", "\\n"; print }
END { printf "%s", "\\n\"}\n" }
'
