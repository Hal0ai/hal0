#!/usr/bin/env bash
# scripts/install-matrix/collect.sh — measure a hal0 install from inside the guest.
#
# Usage:  collect.sh pre|post|probe     (run as root, in the guest)
#
# Prints key=value lines, one per key, values on a single line. A key whose
# value could not be measured prints its "unknown" value: -1 for a number, 0 for
# an _ok flag, empty for a name. The script always exits 0; the harness reads
# the keys, not the exit code. It needs only bash, coreutils, curl, systemctl
# and the hal0 command. JSON is parsed with the Python behind the hal0
# launcher's shebang, run with -I; with no such Python the JSON-dependent keys
# stay at their unknown value. Hermes's address and key come from the api_server
# block of its config.yaml first, then its .env, then the defaults; that YAML is
# read with the same Python, so without it a config.yaml-only key, host or port
# is invisible and only .env and the defaults are used.
#
# Test seams (all optional):
#   HAL0_COLLECT_ROOT          prefix for /sys/class/net, /etc/hal0/slots,
#                              /var/log/hal0 and /tmp
#   HAL0_COLLECT_API           API base URL (default http://127.0.0.1:8080)
#   HAL0_COLLECT_HEALTH_TRIES  health polls (default 90)
#   HAL0_COLLECT_HEALTH_SLEEP  seconds between health polls (default 2)
#   HAL0_COLLECT_POLL_SLEEP    seconds between memory/Hermes polls (default 5)
#   HAL0_COLLECT_CHAT_TIMEOUT / _MEMORY_TIMEOUT / _HERMES_TIMEOUT  (300/180/300)
#   HAL0_COLLECT_PYTHON        interpreter override (default: the hal0 shebang's)
set -euo pipefail

ROOT="${HAL0_COLLECT_ROOT:-}"
API="${HAL0_COLLECT_API:-http://127.0.0.1:8080}"
HEALTH_TRIES="${HAL0_COLLECT_HEALTH_TRIES:-90}"
HEALTH_SLEEP="${HAL0_COLLECT_HEALTH_SLEEP:-2}"
POLL_SLEEP="${HAL0_COLLECT_POLL_SLEEP:-5}"
CHAT_TIMEOUT="${HAL0_COLLECT_CHAT_TIMEOUT:-300}"
MEMORY_TIMEOUT="${HAL0_COLLECT_MEMORY_TIMEOUT:-180}"
HERMES_TIMEOUT="${HAL0_COLLECT_HERMES_TIMEOUT:-300}"
PROMPT="Reply with the single word ready."

emit() { # key value — value forced onto one line
    local v="${2-}"
    v="${v//$'\r'/ }"
    v="${v//$'\n'/ }"
    printf '%s=%s\n' "$1" "$v"
}

now_ns() { date +%s%N 2>/dev/null || echo 0; }

# fmt_ms NS_START NS_END -> seconds with millisecond precision, e.g. 12.345
fmt_ms() {
    local ms=$((($2 - $1) / 1000000))
    ((ms < 0)) && ms=0
    printf '%d.%03d' $((ms / 1000)) $((ms % 1000))
}

# newest PATTERN... -> the most recently modified existing file, or empty
newest() {
    local f best=""
    for f in "$@"; do
        [[ -f "$f" ]] || continue
        if [[ -z "$best" || "$f" -nt "$best" ]]; then best="$f"; fi
    done
    printf '%s' "$best"
}

# ---------------------------------------------------------------------------
# pre / post
# ---------------------------------------------------------------------------
measure_disk() {
    local v=-1 line
    if line="$(df -B1 --output=used / 2>/dev/null | tail -n 1)"; then
        line="${line//[[:space:]]/}"
        [[ "$line" =~ ^[0-9]+$ ]] && v="$line"
    fi
    emit disk_used_bytes "$v"
}

measure_rx() {
    local total=0 seen=0 d name val
    for d in "$ROOT"/sys/class/net/*; do
        [[ -d "$d" ]] || continue
        name="${d##*/}"
        [[ "$name" == lo ]] && continue
        [[ -r "$d/statistics/rx_bytes" ]] || continue
        val=""
        read -r val <"$d/statistics/rx_bytes" || true
        [[ "$val" =~ ^[0-9]+$ ]] || continue
        total=$((total + val))
        seen=1
    done
    if ((seen)); then emit net_rx_bytes "$total"; else emit net_rx_bytes -1; fi
}

measure_slots() {
    local f name count=0 names="" dir="$ROOT/etc/hal0/slots"
    if [[ ! -d "$dir" ]]; then
        emit slot_count -1
        emit slots ""
        return 0
    fi
    for f in "$dir"/*.toml; do
        [[ -f "$f" ]] || continue
        name="${f##*/}"
        name="${name%.toml}"
        count=$((count + 1))
        names="${names:+$names,}$name"
    done
    emit slot_count "$count"
    emit slots "$names"
}

measure_failed_units() {
    local out="" a b units=""
    out="$(systemctl --failed --no-legend --plain 2>/dev/null)" || out=""
    while read -r a b _; do
        [[ -n "$a" ]] || continue
        if [[ "$a" == "●" || "$a" == "*" ]]; then a="$b"; fi
        [[ -n "$a" ]] || continue
        units="${units:+$units,}$a"
    done <<<"$out"
    emit failed_units "$units"
}

measure_logs() {
    local log report
    log="$(newest "$ROOT"/var/log/hal0/install-*.log)"
    [[ -n "$log" ]] || log="$(newest "$ROOT"/tmp/hal0-install-*.log)"
    report="$(newest "$ROOT"/var/log/hal0/hal0-install-report-*.txt "$ROOT"/tmp/hal0-install-report-*.txt)"
    emit install_log "$log"
    emit failure_report "$report"
}

# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------
find_python() {
    PY=""
    if [[ -n "${HAL0_COLLECT_PYTHON:-}" ]]; then
        PY="$HAL0_COLLECT_PYTHON"
        return 0
    fi
    local launcher first interp
    launcher="$(command -v hal0 2>/dev/null)" || return 0
    launcher="$(readlink -f "$launcher" 2>/dev/null)" || return 0
    [[ -r "$launcher" ]] || return 0
    first=""
    read -r first <"$launcher" || true
    [[ "$first" == "#!"* ]] || return 0
    interp="${first#\#!}"
    interp="${interp%% *}"
    case "${interp##*/}" in
        python*) [[ -x "$interp" ]] && PY="$interp" ;;
    esac
    return 0
}

probe_health() {
    local i code start t=-1
    start="$(now_ns)"
    for ((i = 0; i < HEALTH_TRIES; i++)); do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API/api/health" 2>/dev/null)" || code=""
        if [[ "$code" == 200 ]]; then
            t="$(fmt_ms "$start" "$(now_ns)")"
            break
        fi
        (($(now_ns) - start >= 180000000000)) && break
        sleep "$HEALTH_SLEEP"
    done
    emit api_health_s "$t"
}

probe_doctor() {
    local out="" rc=0 counts="" ok=-1 warn=-1 crit=-1
    out="$(timeout 120 hal0 doctor --json 2>/dev/null)" || rc=$?
    if [[ -z "$out" && $rc -ne 0 && -z "$(command -v hal0 2>/dev/null)" ]]; then rc=-1; fi
    if [[ -n "$PY" && -n "$out" ]]; then
        counts="$(printf '%s' "$out" | "$PY" -I -c '
import json, sys
try:
    rows = json.load(sys.stdin)
    sev = [r.get("severity") for r in rows if isinstance(r, dict)]
    print(sev.count("ok"), sev.count("warn"), sev.count("critical"))
except Exception:
    pass
' 2>/dev/null)" || counts=""
        if [[ "$counts" =~ ^([0-9]+)\ ([0-9]+)\ ([0-9]+)$ ]]; then
            ok="${BASH_REMATCH[1]}"
            warn="${BASH_REMATCH[2]}"
            crit="${BASH_REMATCH[3]}"
        fi
    fi
    emit doctor_ok "$ok"
    emit doctor_warn "$warn"
    emit doctor_critical "$crit"
    emit doctor_exit "$rc"
}

probe_chat() {
    local ok=0 first=-1 model="" start line cpid
    local body
    body='{"model":"hal0/agent","stream":true,"messages":[{"role":"user","content":"'"$PROMPT"'"}]}'
    start="$(now_ns)"
    while IFS= read -r line; do
        if [[ -z "$model" && "$line" =~ \"model\":\ *\"([^\"]+)\" ]]; then
            model="${BASH_REMATCH[1]}"
        fi
        if [[ "$line" =~ \"content\":\ *\"[^\"] ]]; then
            first="$(fmt_ms "$start" "$(now_ns)")"
            ok=1
            break
        fi
    done < <(
        timeout "$CHAT_TIMEOUT" curl -sN --max-time "$CHAT_TIMEOUT" \
            -H 'Content-Type: application/json' -d "$body" \
            "$API/v1/chat/completions" 2>/dev/null
    ) || true
    cpid=$! # $! after a process substitution needs bash 4.4 or newer
    kill "$cpid" 2>/dev/null || true
    emit chat_ok "$ok"
    emit chat_first_token_s "$first"
    emit chat_model "$model"
}

probe_memory() {
    local wok=0 rok=0 tok code start out deadline_ns
    tok="hx$(od -An -N8 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')"
    [[ "$tok" == hx ]] && tok="hx$(now_ns)"
    start="$(now_ns)"
    deadline_ns=$((start + MEMORY_TIMEOUT * 1000000000))
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 \
        -H 'Content-Type: application/json' \
        -d '{"text":"The hal0 install check code is hal0check'"$tok"'."}' \
        "$API/api/memory/add" 2>/dev/null)" || code=""
    if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then
        wok=1
        while :; do
            out="$(timeout 60 hal0 memory recall "install check code" --json 2>/dev/null)" || out=""
            if [[ "$out" == *"$tok"* ]]; then
                rok=1
                break
            fi
            (($(now_ns) >= deadline_ns)) && break
            sleep "$POLL_SLEEP"
        done
    fi
    emit memory_write_ok "$wok"
    emit memory_read_ok "$rok"
}

# dotenv_get FILE NAME -> value (quotes stripped); never evaluates the file
dotenv_get() {
    local line k v out=""
    [[ -r "$1" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#export }"
        k="${line%%=*}"
        [[ "$k" == "$2" ]] || continue
        v="${line#*=}"
        v="${v%\"}"
        v="${v#\"}"
        v="${v%\'}"
        v="${v#\'}"
        out="$v"
    done <"$1"
    printf '%s' "$out"
}

# usable_key VALUE -> true when hal0 would accept it (hermes_api.py _usable):
# at least 16 characters and not a placeholder.
usable_key() {
    [[ "${#1}" -ge 16 ]] || return 1
    case "$1" in changeme | placeholder | your-api-key | your_api_key | xxx) return 1 ;; esac
    return 0
}

probe_hermes() {
    local ok=0 secs=-1 home key host port base start deadline_ns resp rid st cfg
    local chost="" cport="" ckey="" ekey
    home="${HERMES_HOME:-/var/lib/hal0/.hermes}"
    # Host, port and key resolve as hermes_api.py does: the api_server block of
    # config.yaml first, then .env, then the defaults. Reading the YAML needs
    # the installed Python; without it only .env and the defaults are seen.
    if [[ -n "$PY" && -r "$home/config.yaml" ]]; then
        cfg="$("$PY" -I -c '
import sys
try:
    import yaml
    node = yaml.safe_load(open(sys.argv[1], encoding="utf-8").read()) or {}
    for part in ("gateway", "platforms", "api_server"):
        node = node.get(part) if isinstance(node, dict) else None
    blk = node if isinstance(node, dict) else {}
except Exception:
    blk = {}
host = str(blk.get("host") or "").strip()
try:
    port = int(str(blk.get("port")).strip())
    port = port if 0 < port < 65536 else ""
except ValueError:
    port = ""
key = blk.get("key")
key = key.strip() if isinstance(key, str) else ""
print(host.replace("\n", " "))
print(port)
print(key.replace("\n", " "))
' "$home/config.yaml" 2>/dev/null)" || cfg=""
        {
            read -r chost
            read -r cport
            read -r ckey
        } <<<"$cfg" || true
    fi
    host="${chost:-$(dotenv_get "$home/.env" API_SERVER_HOST)}"
    host="${host:-127.0.0.1}"
    [[ "$host" == *:* && "$host" != \[* ]] && host="[$host]"
    port="$cport"
    if [[ -z "$port" ]]; then
        port="$(dotenv_get "$home/.env" API_SERVER_PORT)"
        [[ "$port" =~ ^[0-9]+$ ]] || port=""
    fi
    base="http://${host}:${port:-8642}"
    key=""
    if usable_key "$ckey"; then
        key="$ckey"
    else
        ekey="$(dotenv_get "$home/.env" API_SERVER_KEY)"
        usable_key "$ekey" && key="$ekey"
    fi
    start="$(now_ns)"
    deadline_ns=$((start + HERMES_TIMEOUT * 1000000000))
    if [[ -n "$key" ]]; then
        # The key goes to curl on stdin (-H @-), never in a process argument.
        resp="$(printf 'Authorization: Bearer %s\n' "$key" | curl -s --max-time 60 -H @- \
            -H 'Content-Type: application/json' -d '{"input":"'"$PROMPT"'"}' \
            "$base/v1/runs" 2>/dev/null)" || resp=""
        if [[ "$resp" =~ \"run_id\":\ *\"([A-Za-z0-9_.:-]+)\" ]]; then
            rid="${BASH_REMATCH[1]}"
            while :; do
                resp="$(printf 'Authorization: Bearer %s\n' "$key" | curl -s --max-time 30 -H @- \
                    "$base/v1/runs/$rid" 2>/dev/null)" || resp=""
                st=""
                [[ "$resp" =~ \"status\":\ *\"([a-z_]+)\" ]] && st="${BASH_REMATCH[1]}"
                # A reply is a completed run whose "output" (the run view's text
                # field, hermes_client.py get_run, mcp/agents.py _view) holds text.
                if [[ "$st" == completed && "$resp" =~ \"output\":\ *\"(\\[^nrt]|[^\"\\\ ]) ]]; then
                    ok=1
                    secs="$(fmt_ms "$start" "$(now_ns)")"
                    break
                fi
                case "$st" in failed | cancelled | interrupted | completed) break ;; esac
                (($(now_ns) >= deadline_ns)) && break
                sleep "$POLL_SLEEP"
            done
        fi
    fi
    emit hermes_reply_ok "$ok"
    emit hermes_reply_s "$secs"
}

main() {
    local mode="${1:-}"
    case "$mode" in
        pre)
            measure_disk || true
            measure_rx || true
            ;;
        post)
            measure_disk || true
            measure_rx || true
            measure_slots || true
            measure_failed_units || true
            measure_logs || true
            ;;
        probe)
            PY=""
            find_python || true
            probe_health || true
            probe_doctor || true
            probe_chat || true
            probe_memory || true
            probe_hermes || true
            ;;
        *)
            echo "usage: collect.sh pre|post|probe" >&2
            ;;
    esac
    return 0
}

main "$@" || true
exit 0
