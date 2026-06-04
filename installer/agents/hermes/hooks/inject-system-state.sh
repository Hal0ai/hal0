#!/bin/sh
# hal0 Hermes on_session_start hook — inject live system state.
#
# Wired by hermes_templates/config.yaml.j2 (hooks.on_session_start).
# Contract: emit context to stdout for the new session; stay inside the
# 2s hook timeout. We ONLY cat the pre-rendered /var/lib/hal0/STATE.md (the
# expensive probe runs in the writers, not here). If STATE.md is older
# than the TTL we additionally kick a DETACHED background refresh so the
# NEXT session is fresh — we never block this session on a probe.
set -eu

# Runtime snapshot lives under the hal0-owned /var/lib/hal0 (#473) so the
# User=hal0 render-context writer can produce it under the hermes sandbox.
STATE_FILE="/var/lib/hal0/STATE.md"
TTL_SECONDS=300   # 5 min — defense-in-depth for missed change events.

# Missing file (first boot before any render) => emit nothing, exit clean.
[ -f "$STATE_FILE" ] || exit 0

# Stale? Kick a detached, output-discarded refresh. setsid+& so it never
# holds up the session even if the daemon probe is slow.
now=$(date +%s)
mtime=$(stat -c %Y "$STATE_FILE" 2>/dev/null || echo "$now")
age=$(( now - mtime ))
if [ "$age" -gt "$TTL_SECONDS" ]; then
    setsid /usr/local/bin/hal0-agent hermes render-context >/dev/null 2>&1 &
fi

# Inject the current (possibly slightly-stale) snapshot into the session.
cat "$STATE_FILE"
