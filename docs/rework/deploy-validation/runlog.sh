#!/usr/bin/env bash
# runlog.sh — 100%-recorded remote command runner for hal0 install validation.
# Usage: runlog <box:143|150|prx> <transcript_path> <label> <remote-command-string>
# Every invocation appends: timestamp, box, label, the exact remote command,
# full stdout+stderr, and exit code to the transcript. Also echoes to our stdout.

runlog() {
  local box="$1"; local tx="$2"; local label="$3"; shift 3
  local cmd="$*"
  local b64; b64="$(printf '%s' "$cmd" | base64 -w0)"
  local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local wrapper
  case "$box" in
    143) wrapper="ssh -o ConnectTimeout=15 halo143 \"echo $b64 | base64 -d | bash -l\"" ;;
    150) wrapper="ssh -o ConnectTimeout=15 prx \"pct exec 150 -- bash -lc 'echo $b64 | base64 -d | bash -l'\"" ;;
    prx) wrapper="ssh -o ConnectTimeout=15 prx \"echo $b64 | base64 -d | bash -l\"" ;;
    *) echo "bad box $box" >&2; return 2 ;;
  esac
  {
    echo "########################################################################"
    echo "## [$ts] BOX=$box LABEL=$label"
    echo "## CMD: $cmd"
    echo "##----------------------------- OUTPUT -----------------------------------"
  } | tee -a "$tx"
  local out rc
  out="$(eval "$wrapper" 2>&1)"; rc=$?
  {
    printf '%s\n' "$out"
    echo "##----------------------------- EXIT=$rc ---------------------------------"
    echo ""
  } | tee -a "$tx"
  return $rc
}
