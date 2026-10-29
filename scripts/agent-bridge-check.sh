#!/usr/bin/env bash
# hal0 — live probe of the agent bridge (ADR-0043). Run it on the hal0 box.
#
# Walks one bridge client through its life against a running hal0-api:
#
#   1. hal0 bridge add ci-probe-$RANDOM --scope agents   (key parsed from the output)
#   2. MCP initialize + tools/list on $BASE/mcp/agents/mcp  (agents_list must be listed)
#   3. tools/call agents_list                               (hermes must be listed)
#   4. tools/call agent_run "Reply with the single word pong ..." wait_s=180
#      -> status completed, "pong" in output   (full mode only)
#   5. the agents-only key is refused (403) on /mcp/memory and /mcp/admin;
#      after `hal0 bridge scopes <name> agents memory admin` both open, and the
#      admin tool list holds no approval_approve
#   6. hal0 bridge revoke <name>; the same tools/list now gets 401
#
# Modes: full runs step 4. reduced skips it, for a box whose Hermes is not
# serving a model. auto (the default) picks full when the default profile's
# health is 200 and it advertises a model id, else reduced.
#
# Exit: 0 every step passed | 1 a step failed | 2 bad arguments |
#       3 every step that ran passed, but step 4 was skipped (reduced).
#
# Needs: bash, curl, python3, and the hal0 CLI (HAL0_BIN names another binary),
# run as a user that can call the admin API (root, or HAL0_ADMIN_KEY exported).
# The key is never printed. The client is revoked on exit unless --keep is given.
set -euo pipefail

BASE="${HAL0_API_URL:-http://127.0.0.1:8080}"
MODE=auto
KEEP=0
HAL0_BIN="${HAL0_BIN:-hal0}"
PROBE_PROMPT="Reply with the single word pong and use no tools"
PONG_WAIT_S=180

usage() {
  cat <<'EOF'
Usage: agent-bridge-check.sh [--mode auto|full|reduced] [--base URL] [--keep]

  --mode MODE   full: run the pong round trip. reduced: skip it.
                auto (default): full when the default Hermes profile is healthy
                and advertises a model, else reduced.
  --base URL    hal0-api base URL (default: $HAL0_API_URL, else http://127.0.0.1:8080)
  --keep        leave the probe client in place (skip the revoke step)
  -h, --help    show this help

Exit: 0 pass, 1 fail, 2 bad arguments, 3 passed with the pong step skipped.
EOF
}

die_usage() {
  printf 'agent-bridge-check.sh: %s\n' "$1" >&2
  usage >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode) [ "$#" -ge 2 ] || die_usage "--mode needs a value"; MODE="$2"; shift 2 ;;
    --mode=*) MODE="${1#--mode=}"; shift ;;
    --base) [ "$#" -ge 2 ] || die_usage "--base needs a value"; BASE="$2"; shift 2 ;;
    --base=*) BASE="${1#--base=}"; shift ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die_usage "unknown argument: $1" ;;
  esac
done

case "$MODE" in
  auto|full|reduced) ;;
  *) die_usage "--mode must be auto, full or reduced, not '$MODE'" ;;
esac
case "$BASE" in
  http://*|https://*) ;;
  *) die_usage "--base must start with http:// or https://, not '$BASE'" ;;
esac
BASE="${BASE%/}"
export HAL0_API_URL="$BASE"

# --- plumbing ---------------------------------------------------------------

FAILED=0
SKIPPED_REDUCED=0
NAME="ci-probe-$RANDOM"
CLIENT_ADDED=0
CLIENT_REVOKED=0
KEY=""
WORK="$(mktemp -d)"

# shellcheck disable=SC2329  # invoked by the EXIT trap
cleanup() {
  local rc=$?
  # A probe that stops early must not leave a live key behind.
  if [ "$CLIENT_ADDED" = 1 ] && [ "$CLIENT_REVOKED" = 0 ] && [ "$KEEP" = 0 ]; then
    "$HAL0_BIN" bridge revoke "$NAME" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
  case "$rc" in 0|1|2|3) exit "$rc" ;; *) exit 1 ;; esac
}
trap cleanup EXIT

pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
skip() { printf 'SKIPPED (%s): %s\n' "$1" "$2"; }
# A failure that makes every later step meaningless.
abort() { fail "$*"; exit 1; }

# JSON helpers. Each prints its answer on stdout and exits 1 when the input
# lacks it. python3 parses; no jq needed.

# rpc_result FILE — the JSON-RPC "result" of a response that is plain JSON or SSE.
rpc_result() {
  python3 - "$1" <<'PY'
import json, sys

text = open(sys.argv[1], encoding="utf-8", errors="replace").read().strip()


def messages(raw):
    if raw[:1] in "{[":
        yield json.loads(raw)
        return
    # Server-sent events: blank-line separated, "data:" lines joined.
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line[5:].lstrip(" ") for line in block.split("\n") if line.startswith("data:")
        )
        if data:
            yield json.loads(data)


for msg in messages(text):
    if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
        if "error" in msg:
            print(json.dumps(msg["error"]), file=sys.stderr)
            sys.exit(1)
        print(json.dumps(msg["result"]))
        sys.exit(0)
sys.exit(1)
PY
}

# tool_payload RESULT_JSON — the dict a tools/call returned (isError -> message, exit 2).
tool_payload() {
  python3 -c '
import json, sys
result = json.loads(sys.argv[1])
content = result.get("content") or []
text = content[0].get("text", "") if content else ""
if result.get("isError"):
    print(text)
    sys.exit(2)
try:
    print(json.dumps(json.loads(text)))
except ValueError:
    structured = result.get("structuredContent")
    if structured is None:
        sys.exit(1)
    print(json.dumps(structured))
' "$1"
}

# --- HTTP ---------------------------------------------------------------------

HTTP_CODE=""
SESSION=""
PROTO=""

# mcp_post MOUNT BODY [TIMEOUT_S] — POST one JSON-RPC message to $BASE$MOUNT/mcp.
# Sets HTTP_CODE; the response body is in $WORK/body, the headers in $WORK/headers.
mcp_post() {
  local mount="$1" body="$2" timeout="${3:-30}"
  local -a extra=()
  [ -n "$SESSION" ] && extra+=(-H "Mcp-Session-Id: $SESSION")
  [ -n "$PROTO" ] && extra+=(-H "MCP-Protocol-Version: $PROTO")
  # The key goes in through a header file so it never shows in a process listing.
  printf 'Authorization: Bearer %s\n' "$KEY" >"$WORK/auth"
  HTTP_CODE="$(curl -sS -m "$timeout" -o "$WORK/body" -D "$WORK/headers" -w '%{http_code}' \
    -X POST "$BASE$mount/mcp" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -H "@$WORK/auth" \
    ${extra[@]+"${extra[@]}"} \
    --data "$body" 2>"$WORK/curl.err")" || HTTP_CODE="000"
}

describe_http() {
  case "$HTTP_CODE" in
    000) printf 'no answer from %s (%s)' "$BASE" "$(tr -d '\n' <"$WORK/curl.err")" ;;
    421) printf 'HTTP 421 (the host in --base is not in the MCP allowlist; use a box address or set HAL0_MCP_ALLOWED_HOSTS)' ;;
    *) printf 'HTTP %s' "$HTTP_CODE" ;;
  esac
}

INIT_BODY='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"hal0-agent-bridge-check","version":"1"}}}'
INITIALIZED_BODY='{"jsonrpc":"2.0","method":"notifications/initialized"}'
LIST_BODY='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# mcp_open MOUNT — initialize, then send notifications/initialized.
# Leaves HTTP_CODE at the initialize answer, SESSION and PROTO set when it was 200.
mcp_open() {
  SESSION=""
  PROTO=""
  mcp_post "$1" "$INIT_BODY"
  [ "$HTTP_CODE" = 200 ] || return 1
  SESSION="$(grep -i '^mcp-session-id:' "$WORK/headers" | tail -1 | tr -d '\r' | awk '{print $2}' || true)"
  local result
  result="$(rpc_result "$WORK/body")" || { HTTP_CODE="bad-rpc"; return 1; }
  PROTO="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("protocolVersion",""))' "$result")"
  mcp_post "$1" "$INITIALIZED_BODY"
  case "$HTTP_CODE" in 200|202) HTTP_CODE=200 ;; *) return 1 ;; esac
}

# tools_list MOUNT — the tool names the server lists, one per line.
tools_list() {
  mcp_post "$1" "$LIST_BODY"
  [ "$HTTP_CODE" = 200 ] || return 1
  rpc_result "$WORK/body" | python3 -c '
import json, sys
for tool in json.load(sys.stdin).get("tools", []):
    print(tool["name"])
'
}

# call_tool MOUNT NAME ARGS_JSON [TIMEOUT_S] — prints the tool's payload dict.
call_tool() {
  local body
  body="$(python3 -c '
import json, sys
print(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": sys.argv[1], "arguments": json.loads(sys.argv[2])}}))
' "$2" "$3")"
  mcp_post "$1" "$body" "${4:-30}"
  [ "$HTTP_CODE" = 200 ] || return 1
  local result
  result="$(rpc_result "$WORK/body")" || return 1
  tool_payload "$result"
}

# --- 1. add the client --------------------------------------------------------

if ! command -v curl >/dev/null 2>&1; then die_usage "curl is required"; fi
if ! command -v python3 >/dev/null 2>&1; then die_usage "python3 is required"; fi

add_out=""
if ! add_out="$("$HAL0_BIN" bridge add "$NAME" --scope agents 2>&1)"; then
  abort "[1] hal0 bridge add $NAME failed: $add_out"
fi
CLIENT_ADDED=1
# The key is on the "Authorization: Bearer" header of every printed claude mcp add
# line, which hal0 never wraps. The panel above them can wrap at a narrow terminal.
KEY="$(printf '%s\n' "$add_out" | grep -o 'Bearer hal0bk_[^" ]*' | head -1 | sed 's/^Bearer //' || true)"
[ -n "$KEY" ] || abort "[1] hal0 bridge add $NAME printed no hal0bk_ key"
pass "[1] bridge client $NAME added (scope agents); key parsed, not shown"

# --- 2. MCP initialize + tools/list -------------------------------------------

mcp_open /mcp/agents || abort "[2] initialize on /mcp/agents/mcp: $(describe_http)"
tools="$(tools_list /mcp/agents)" || abort "[2] tools/list on /mcp/agents/mcp: $(describe_http)"
if printf '%s\n' "$tools" | grep -qx 'agents_list'; then
  pass "[2] initialize + tools/list on /mcp/agents/mcp ($(printf '%s\n' "$tools" | wc -l | tr -d ' ') tools, agents_list among them)"
else
  fail "[2] tools/list on /mcp/agents/mcp has no agents_list (got: $(printf '%s' "$tools" | tr '\n' ' '))"
fi

# --- 3. agents_list ------------------------------------------------------------

AGENTS_JSON=""
if AGENTS_JSON="$(call_tool /mcp/agents agents_list '{}')"; then
  if printf '%s' "$AGENTS_JSON" | python3 -c '
import json, sys
agents = json.load(sys.stdin).get("agents", [])
sys.exit(0 if any(a.get("id") == "hermes" for a in agents) else 1)
'; then
    pass "[3] agents_list shows hermes"
  else
    fail "[3] agents_list does not show hermes"
  fi
else
  fail "[3] agents_list call failed: $(describe_http) ${AGENTS_JSON:+($AGENTS_JSON)}"
fi

# Mode: auto becomes full only when the default profile can answer.
default_ready=0
if [ -n "$AGENTS_JSON" ] && printf '%s' "$AGENTS_JSON" | python3 -c '
import json, sys
for agent in json.load(sys.stdin).get("agents", []):
    if agent.get("id") != "hermes":
        continue
    for profile in agent.get("profiles", []):
        if profile.get("name") == "default":
            ok = profile.get("health") == 200 and bool(profile.get("model_id"))
            sys.exit(0 if ok else 1)
sys.exit(1)
' 2>/dev/null; then
  default_ready=1
fi
RUN_MODE="$MODE"
if [ "$MODE" = auto ]; then
  if [ "$default_ready" = 1 ]; then RUN_MODE=full; else RUN_MODE=reduced; fi
  printf 'INFO: --mode auto chose %s (default profile %s)\n' "$RUN_MODE" \
    "$([ "$default_ready" = 1 ] && echo 'healthy with a model' || echo 'not ready')"
fi

# --- 4. the pong round trip ------------------------------------------------------

if [ "$RUN_MODE" = reduced ]; then
  skip reduced "[4] agent_run pong round trip; no Hermes model is serving, or --mode reduced"
  SKIPPED_REDUCED=1
else
  run_args="$(python3 -c '
import json, sys
print(json.dumps({"agent": "hermes", "input": sys.argv[1], "wait_s": int(sys.argv[2])}))
' "$PROBE_PROMPT" "$PONG_WAIT_S")"
  if run_json="$(call_tool /mcp/agents agent_run "$run_args" $((PONG_WAIT_S + 60)))"; then
    verdict="$(printf '%s' "$run_json" | python3 -c '
import json, sys
run = json.load(sys.stdin)
status = run.get("status")
output = run.get("output") or ""
if status == "completed" and "pong" in output.lower():
    print("ok")
elif status == "completed":
    print("completed without pong in the output")
else:
    print("status %s%s (run %s)" % (status, ", timed out" if run.get("timed_out") else "", run.get("run_id")))
')"
    if [ "$verdict" = ok ]; then
      pass "[4] agent_run completed and answered pong"
    else
      fail "[4] agent_run: $verdict"
      # Do not leave a run going on the box.
      stop_id="$(printf '%s' "$run_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("run_id",""))' 2>/dev/null || true)"
      if [ -n "$stop_id" ]; then
        call_tool /mcp/agents agent_run_stop "{\"run_id\": \"$stop_id\"}" >/dev/null 2>&1 || true
      fi
    fi
  else
    fail "[4] agent_run call failed: $(describe_http) ${run_json:+($run_json)}"
  fi
fi

# --- 5. scopes -----------------------------------------------------------------

scope_ok=1
for mount in /mcp/memory /mcp/admin; do
  mcp_open "$mount" || true
  if [ "$HTTP_CODE" != 403 ]; then
    fail "[5] an agents-only key on $mount/mcp: expected HTTP 403, got $(describe_http)"
    scope_ok=0
  fi
done
[ "$scope_ok" = 1 ] && pass "[5a] the agents-only key is refused (403) on /mcp/memory and /mcp/admin"

if "$HAL0_BIN" bridge scopes "$NAME" agents memory admin >/dev/null 2>&1; then
  scope_ok=1
  if ! mcp_open /mcp/memory; then
    fail "[5] after granting memory: initialize on /mcp/memory/mcp got $(describe_http)"
    scope_ok=0
  fi
  if mcp_open /mcp/admin; then
    admin_tools="$(tools_list /mcp/admin || true)"
    if [ -z "$admin_tools" ]; then
      fail "[5] after granting admin: tools/list on /mcp/admin/mcp listed nothing ($(describe_http))"
      scope_ok=0
    elif printf '%s\n' "$admin_tools" | grep -qx 'approval_approve'; then
      fail "[5] the admin tool list holds approval_approve; a bridge key must not approve its own gated calls"
      scope_ok=0
    fi
  else
    fail "[5] after granting admin: initialize on /mcp/admin/mcp got $(describe_http)"
    scope_ok=0
  fi
  [ "$scope_ok" = 1 ] && pass "[5b] with scopes agents, memory, admin both servers open, and the admin tools hold no approval_approve"
else
  fail "[5] hal0 bridge scopes $NAME agents memory admin failed"
fi

# --- 6. revoke -------------------------------------------------------------------

if [ "$KEEP" = 1 ]; then
  skip --keep "[6] revoke; client $NAME stays — remove it with: hal0 bridge revoke $NAME"
elif "$HAL0_BIN" bridge revoke "$NAME" >/dev/null 2>&1; then
  CLIENT_REVOKED=1
  SESSION=""
  PROTO=""
  mcp_post /mcp/agents "$LIST_BODY"
  if [ "$HTTP_CODE" = 401 ]; then
    pass "[6] after hal0 bridge revoke $NAME, tools/list on /mcp/agents/mcp answers 401"
  else
    fail "[6] after revoke, tools/list on /mcp/agents/mcp: expected HTTP 401, got $(describe_http)"
  fi
else
  fail "[6] hal0 bridge revoke $NAME failed"
fi

# --- verdict -----------------------------------------------------------------------

if [ "$FAILED" = 1 ]; then
  printf 'RESULT: FAIL\n' >&2
  exit 1
fi
if [ "$SKIPPED_REDUCED" = 1 ]; then
  printf 'RESULT: PASS (reduced: the pong step was skipped)\n'
  exit 3
fi
printf 'RESULT: PASS\n'
exit 0
