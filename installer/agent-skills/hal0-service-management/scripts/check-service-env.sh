#!/usr/bin/env bash
# check-service-env.sh — Diagnose whether a hal0 user-level systemd service
# has its secrets env file wired correctly.
#
# Usage:
#   check-service-env.sh <service-name> [expected-var ...]
#
# Examples:
#   check-service-env.sh hermes-gateway
#   check-service-env.sh hermes-gateway TELEGRAM_BOT_TOKEN DISCORD_BOT_TOKEN
#   check-service-env.sh hal0-agent@hermes
#
# Exit codes:
#   0 = service running AND all named vars present in process env
#   1 = service running but at least one var missing
#   2 = service not running, or wrong user context
#   3 = bad usage
#
# This is the diagnostic half of the fix described in
# hal0-service-management/SKILL.md. Run it BEFORE editing any unit file
# to confirm the hypothesis; run it AFTER to confirm the fix.
set -euo pipefail

SERVICE="${1:-}"
shift || true

if [ -z "$SERVICE" ]; then
  echo "usage: $0 <service-name> [expected-var ...]" >&2
  exit 3
fi

# --- locate the unit file (user-level, hal0 convention) ---
# Default to root's user systemd; if running as a different user, adjust.
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$USER_SYSTEMD_DIR/${SERVICE}.service"

echo "=== hal0 service env diagnostic ==="
echo "service:   $SERVICE"
echo "unit file: $UNIT_FILE"

if [ ! -f "$UNIT_FILE" ]; then
  echo
  echo "  !! unit file not found at $UNIT_FILE"
  echo "  !! if the service is managed elsewhere, set USER_SYSTEMD_DIR and retry"
  exit 2
fi

# --- unit-file check: is EnvironmentFile= declared? ---
echo
echo "--- EnvironmentFile directive in unit ---"
if grep -qE '^\s*EnvironmentFile\s*=' "$UNIT_FILE"; then
  grep -nE '^\s*EnvironmentFile\s*=' "$UNIT_FILE" | sed 's/^/  /'
else
  echo "  !! no EnvironmentFile= directive — this is likely the bug"
  echo "  !! fix: add 'EnvironmentFile=/var/lib/hal0/secrets/agents/${SERVICE}.env' to [Service]"
fi

# --- service running? ---
echo
echo "--- service state ---"
if ! systemctl --user is-active "$SERVICE" >/dev/null 2>&1; then
  echo "  service is not active. run: systemctl --user status $SERVICE"
  exit 2
fi

PID="$(systemctl --user show -p MainPID --value "$SERVICE")"
if [ -z "$PID" ] || [ "$PID" = "0" ]; then
  echo "  !! service active but MainPID is empty/0 — unusual"
  exit 2
fi
echo "  MainPID: $PID"

# --- process env check ---
echo
echo "--- process env (filtered to hal0 secrets) ---"
ENV_DUMP="$(tr '\0' '\n' </proc/"$PID"/environ 2>/dev/null || true)"
if [ -z "$ENV_DUMP" ]; then
  echo "  !! shell 'tr' pipeline returned empty — falling back to Python"
  if command -v python3 >/dev/null 2>&1; then
    ENV_DUMP="$(sudo python3 -c "
import sys
try:
    data = open('/proc/$PID/environ','rb').read()
except OSError as e:
    sys.stderr.write(f'read failed: {e}\n'); sys.exit(0)
for entry in data.split(b'\x00'):
    if b'=' in entry:
        k, v = entry.split(b'=', 1)
        print(f'{k.decode(errors=\"replace\")}={v.decode(errors=\"replace\")}')
" 2>/dev/null || true)"
  fi
  if [ -z "$ENV_DUMP" ]; then
    echo "  !! could not read /proc/$PID/environ (perms? wrong pid namespace? python3 missing?)"
    exit 2
  fi
fi

# Show a curated view: anything that looks like a hal0 secret/agent var
echo "$ENV_DUMP" | grep -E '^(TELEGRAM_|DISCORD_|HAL0_|HERMES_|OPENAI_|ANTHROPIC_|.*_BOT_TOKEN|.*_ALLOWED_USERS|.*_HOME_CHANNEL)' \
  | sort | sed 's/=.*$/=<redacted>/' | sed 's/^/  /' \
  || echo "  (no hal0-shaped env vars found in process)"

# --- if specific vars were requested, check them by name ---
if [ "$#" -gt 0 ]; then
  echo
  echo "--- requested var check ---"
  MISSING=0
  for v in "$@"; do
    if printf '%s\n' "$ENV_DUMP" | grep -q "^${v}="; then
      val="$(printf '%s\n' "$ENV_DUMP" | grep "^${v}=" | head -n1 | cut -d= -f2-)"
      # redact anything that looks like a token (long base64-ish)
      if [ "${#val}" -gt 20 ]; then
        val="${val:0:6}...${val: -4}  (len=${#val})"
      fi
      echo "  OK    $v = $val"
    else
      echo "  MISS  $v"
      MISSING=1
    fi
  done
  if [ "$MISSING" -eq 1 ]; then
    echo
    echo "  !! one or more requested vars are missing from the process env"
    echo "  !! most likely: EnvironmentFile= missing or pointing to wrong path"
    exit 1
  fi
fi

echo
echo "=== done ==="
