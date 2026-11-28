#!/usr/bin/env bash
# Run one install-matrix shape end to end: create a throwaway container on the
# Proxmox host, install hal0 into it the way a newcomer would, measure the
# result, write result.json, destroy the container.
#
#   run-shape.sh <shape-id> [--source stable|preview|nightly|tree:<dir>]
#                [--vmid N] [--keep] [--uninstall] [--out DIR]
#                [--timeout SECONDS] [--dry-run]
#
# Exit 0: the install exited 0 and the chat probe answered. 1: anything else
# after the run began. 2: usage error, a shape that is not runnable here, or a
# refused precondition (host memory, VMID, template, devices). stdout carries
# the path of result.json and nothing else; progress goes to stderr.
#
# --dry-run is decided once, below. It defines an `ssh` shell function that
# shadows the real binary, so no call made through lib/pve.sh can leave the
# machine; the terminal driver is printed, never started. The function prints
# each command it would have sent and answers the read-only questions from
# canned text. Nothing is created on the host or in the output directory.
#
# Settings (environment): HAL0_PVE, HAL0_MATRIX_VMID_MIN, HAL0_MATRIX_VMID_MAX,
# HAL0_MATRIX_BRIDGE, HAL0_MATRIX_STORAGE (see lib/pve.sh).
# shellcheck disable=SC2329  # trap handlers, background jobs and the dry-run ssh shadow
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
# shellcheck source-path=SCRIPTDIR source=lib/pve.sh
source "${HERE}/lib/pve.sh"

INSTALL_URL="https://hal0.dev/install.sh"
SUMMARY_PATH="/root/hal0-summary.json"

log() { printf '[run-shape] %s\n' "$*" >&2; }
die2() {
  log "$*"
  exit 2
}
usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" >&2; }

# ── arguments ────────────────────────────────────────────────────────────────
SHAPE_ID="" SOURCE="stable" VMID="" KEEP=0 UNINSTALL=0 OUT="" TIMEOUT=5400 DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source | --vmid | --out | --timeout)
      [[ $# -ge 2 ]] || die2 "$1 needs a value"
      case "$1" in
        --source) SOURCE="$2" ;;
        --vmid) VMID="$2" ;;
        --out) OUT="$2" ;;
        --timeout) TIMEOUT="$2" ;;
      esac
      shift 2
      ;;
    --keep) KEEP=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    -*) usage; die2 "unknown option: $1" ;;
    *)
      [[ -z "$SHAPE_ID" ]] || die2 "more than one shape id given"
      SHAPE_ID="$1"
      shift
      ;;
  esac
done
[[ -n "$SHAPE_ID" ]] || { usage; die2 "no shape id given"; }
[[ "$SHAPE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die2 "bad shape id: ${SHAPE_ID}"
[[ "$TIMEOUT" =~ ^[1-9][0-9]{0,6}$ ]] || die2 "--timeout must be a number of seconds"
[[ -z "$VMID" || "$VMID" =~ ^[1-9][0-9]{0,8}$ ]] || die2 "--vmid must be a number"

CHANNEL="" TREE_DIR=""
case "$SOURCE" in
  stable) ;;
  preview | nightly) CHANNEL="$SOURCE" ;;
  tree:*)
    TREE_DIR="${SOURCE#tree:}"
    [[ -n "$TREE_DIR" && -d "$TREE_DIR" ]] || die2 "--source tree: not a directory: ${TREE_DIR}"
    ;;
  *) die2 "--source must be stable, preview, nightly or tree:<dir> (got ${SOURCE})" ;;
esac

PVE_ALIAS="${HAL0_PVE:-pve}"
if [[ "$PVE_ALIAS" == -* || ! "$PVE_ALIAS" =~ ^[^[:space:]]+$ ]]; then
  die2 "bad HAL0_PVE alias: ${PVE_ALIAS}"
fi

# ── the shape ────────────────────────────────────────────────────────────────
SHAPE_ROW="" SHAPE_TEMPLATE="" SHAPE_UNPRIVILEGED="" SHAPE_HARDWARE="" SHAPE_ENTRY=""
SHAPE_CORES="" SHAPE_MEMORY_MB="" SHAPE_DISK_GB="" SHAPE_RUNNABLE="" SHAPE_ANSWERS=""
shape_text="$(python3 -I "${HERE}/shape.py" get "$SHAPE_ID")" || die2 "unknown or invalid shape: ${SHAPE_ID}"
while IFS='=' read -r key value; do
  case "$key" in
    SHAPE_ROW) SHAPE_ROW="$value" ;;
    SHAPE_TEMPLATE) SHAPE_TEMPLATE="$value" ;;
    SHAPE_UNPRIVILEGED) SHAPE_UNPRIVILEGED="$value" ;;
    SHAPE_HARDWARE) SHAPE_HARDWARE="$value" ;;
    SHAPE_ENTRY) SHAPE_ENTRY="$value" ;;
    SHAPE_CORES) SHAPE_CORES="$value" ;;
    SHAPE_MEMORY_MB) SHAPE_MEMORY_MB="$value" ;;
    SHAPE_DISK_GB) SHAPE_DISK_GB="$value" ;;
    SHAPE_RUNNABLE) SHAPE_RUNNABLE="$value" ;;
    SHAPE_ANSWERS) SHAPE_ANSWERS="$value" ;;
    *) ;;
  esac
done <<<"$shape_text"
[[ "$SHAPE_RUNNABLE" == "yes" ]] || die2 "shape ${SHAPE_ID} is not runnable here (${SHAPE_RUNNABLE})"
case "$SHAPE_ENTRY" in
  oneliner | oneliner-unattended | terminal | box) ;;
  *) die2 "shape ${SHAPE_ID}: unsupported entry type ${SHAPE_ENTRY}" ;;
esac
[[ -z "$TREE_DIR" || "$SHAPE_ENTRY" != "box" ]] || die2 "--source tree: cannot be combined with the box entry"
ANSWERS_FILE="/dev/null"
if [[ -n "$SHAPE_ANSWERS" ]]; then
  [[ "$SHAPE_ANSWERS" =~ ^[A-Za-z0-9._-]+\.tsv$ && -f "${HERE}/answers/${SHAPE_ANSWERS}" ]] \
    || die2 "shape ${SHAPE_ID}: no such answers file: ${SHAPE_ANSWERS}"
  ANSWERS_FILE="${HERE}/answers/${SHAPE_ANSWERS}"
fi

# This run's nonce: 16 hex characters from the kernel, written into the
# guest's description so cleanup destroys only the guest this run created.
NONCE="$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
[[ "$NONCE" =~ ^[0-9a-f]{16}$ ]] || die2 "could not generate a run nonce"
CT_NONCE="$NONCE"

# One run at a time: a local lock for the whole run, taken before any call to
# the host. A dry run touches no host and creates no files, so it takes none.
REPORT_ROOT="${HAL0_MATRIX_REPORT_ROOT:-${REPO}/tests/harness/reports/install-matrix}"
if [[ "$DRY_RUN" != "1" ]]; then
  mkdir -p "$REPORT_ROOT" || die2 "cannot create ${REPORT_ROOT}"
  exec 8>"${REPORT_ROOT}/.lock" || die2 "cannot open the lock file in ${REPORT_ROOT}"
  flock -n 8 || die2 "another install matrix run holds ${REPORT_ROOT}/.lock; only one harness container may exist at a time"
fi

# ── dry run: decided once, here ──────────────────────────────────────────────
if [[ "$DRY_RUN" == "1" ]]; then
  exec 9>&1
  ssh() {
    local cmd="${*: -1}" a feed=1 vm=""
    for a in "$@"; do
      [[ "$a" == "--" ]] && break
      [[ "$a" == "-n" ]] && feed=0
    done
    [[ "$feed" == "0" ]] || cat >/dev/null
    printf '[dry-run] ssh %s\n' "$cmd" >&9
    case "$cmd" in
      "free -g"*) printf 'Mem: 125 20 60 1 45 100\n' ;;
      "pveam list local") printf 'Volume Size\nlocal:vztmpl/%s_amd64.tar.zst 1\n' "$SHAPE_TEMPLATE" ;;
      "pvesm status"*) printf 'Name Type Status\nlocal dir active\n' ;;
      "pvesh get"*) printf '[]\n' ;;
      "if test -e"*) printf 'absent\n' ;;
      "bash -s -- gpu"*) printf 'dev /dev/dri/renderD128\nmajor 226\n' ;;
      "pct config "*)
        vm="${cmd#pct config }"
        printf 'hostname: hal0-mx-%s\ntags: hal0-matrix\ndescription: hal0-matrix run %s\n' "${vm%% *}" "$NONCE"
        ;;
      *) ;;
    esac
    return 0
  }
fi
plan() { [[ "$DRY_RUN" != "1" ]] || printf '[dry-run] %s\n' "$*" >&9; }

# ── state the cleanup trap reads ─────────────────────────────────────────────
ART=""             # artifact directory (the real --out, or scratch on a dry run)
SCRATCH=""         # dry-run artifact directory, removed on exit
TREE_TAR=""        # packed local tree, removed on exit
CT_MAYBE=0         # set once ct_create got as far as running pct create
GUEST_UP=0         # ct_start succeeded
INSTALL_RC=""      # the install's exit status, once known
INTERRUPTED=0      # signal number that stopped the run
PREREQS=""         # packages the harness installed before the installer ran
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
POST_DONE=0 PULL_DONE=0 RESULT_WRITTEN=0
CHILD_PID="" CHILD_TREE=0
CLEANED=0
declare -a DEVS=() MAJORS=()

kv_get() { sed -n "s/^${2}=//p" "$1" 2>/dev/null | head -n 1; }

# Single-quote a value for the Proxmox host's shell.
sq() {
  local s="$1"
  s=${s//\'/\'\\\'\'}
  printf "'%s'" "$s"
}

kill_tree() {
  local c
  for c in $(pgrep -P "$1" 2>/dev/null || true); do kill_tree "$c"; done
  # KILL, not TERM: cleanup runs with INT/TERM/HUP ignored, and children
  # started from there inherit that.
  kill -KILL "$1" 2>/dev/null || true
}

# Stop the background job (if any) the way its kind needs: the terminal driver
# gets TERM alone, so it can record the interruption; a function subshell is
# killed together with the ssh below it.
stop_child() {
  local n=0
  [[ -n "$CHILD_PID" ]] || return 0
  if kill -0 "$CHILD_PID" 2>/dev/null; then
    if [[ "$CHILD_TREE" == "1" ]]; then
      kill_tree "$CHILD_PID"
    else
      kill -TERM "$CHILD_PID" 2>/dev/null || true
      while kill -0 "$CHILD_PID" 2>/dev/null && ((n < 100)); do
        sleep 0.1
        n=$((n + 1))
      done
      kill_tree "$CHILD_PID"
    fi
  fi
  wait "$CHILD_PID" 2>/dev/null || true
  CHILD_PID=""
}

# run_driver <argv...>: a direct child that handles its own timeouts.
run_driver() {
  local rc=0
  "$@" &
  CHILD_PID=$!
  CHILD_TREE=0
  wait "$CHILD_PID" || rc=$?
  CHILD_PID=""
  return "$rc"
}

# run_bounded <seconds> <argv...>: a background job with a deadline; 124 when
# it ran out. Waiting on a short `sleep` keeps a signal handled promptly.
run_bounded() {
  local limit="$1" rc=0 deadline
  shift
  "$@" &
  CHILD_PID=$!
  CHILD_TREE=1
  deadline=$((SECONDS + limit))
  while kill -0 "$CHILD_PID" 2>/dev/null; do
    if ((SECONDS >= deadline)); then
      kill_tree "$CHILD_PID"
      wait "$CHILD_PID" 2>/dev/null || true
      CHILD_PID=""
      return 124
    fi
    sleep 0.2 &
    wait $! 2>/dev/null || true
  done
  wait "$CHILD_PID" 2>/dev/null || rc=$?
  CHILD_PID=""
  return "$rc"
}

# ── result pipeline (idempotent; also run from the cleanup trap) ─────────────
synth_driver() { # <exit code> <timed_out true|false>
  printf '{"exit_code": %s, "questions_answered": 0, "timed_out": %s, "idle_timed_out": false, "unanswered_tail": ""}\n' \
    "$1" "$2" >"${ART}/driver.json"
}

collect_post() {
  POST_DONE=1
  ct_exec "$VMID" "bash /root/collect.sh post" >"${ART}/post.txt" 2>>"${ART}/collect.err" \
    || log "collect post failed"
}

pull_artifacts() {
  local path
  PULL_DONE=1
  ct_pull "$VMID" "$SUMMARY_PATH" "${ART}/summary.json" 2>/dev/null || true
  path="$(kv_get "${ART}/post.txt" install_log)"
  if [[ "$path" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    ct_pull "$VMID" "$path" "${ART}/install.log" 2>/dev/null || log "could not copy the install log"
  fi
  path="$(kv_get "${ART}/post.txt" failure_report)"
  if [[ "$path" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    ct_pull "$VMID" "$path" "${ART}/failure-report.txt" 2>/dev/null || log "could not copy the failure report"
  fi
}

write_result() {
  RESULT_WRITTEN=1
  if [[ -n "$INSTALL_RC" && ! -s "${ART}/driver.json" ]]; then
    synth_driver "$INSTALL_RC" false
  fi
  python3 -I "${HERE}/result.py" assemble \
    --shape "$SHAPE_ID" --source "$SOURCE" --vmid "$VMID" \
    --pre "${ART}/pre.txt" --post "${ART}/post.txt" --probe "${ART}/probe.txt" \
    --driver "${ART}/driver.json" --summary "${ART}/summary.json" \
    --prereqs "$PREREQS" --started "$STARTED" --out "${ART}/result.json" \
    || log "could not write result.json"
}

finish_result() {
  [[ "$RESULT_WRITTEN" == "0" ]] || return 0
  if [[ "$GUEST_UP" == "1" ]]; then
    [[ "$POST_DONE" == "1" ]] || collect_post
    [[ "$PULL_DONE" == "1" ]] || pull_artifacts
  fi
  write_result
}

# ── cleanup: runs once, on EXIT and (via on_signal) on INT, TERM and HUP ─────
cleanup() {
  local rc=$? state="absent"
  trap '' INT TERM HUP
  trap - EXIT
  set +e +u
  [[ "$CLEANED" == "0" ]] || exit "$rc"
  CLEANED=1
  stop_child
  if [[ "$INTERRUPTED" != "0" && -z "$INSTALL_RC" ]]; then
    INSTALL_RC=$((128 + INTERRUPTED))
  fi
  if [[ -n "$VMID" && "$CT_MAYBE" == "1" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      state="present"
    else
      state="$(_ct_presence "$VMID")"
    fi
    if [[ "$state" == "absent" ]]; then
      log "no container ${VMID} exists; nothing to clean up"
    else
      if [[ "$DRY_RUN" != "1" && "$RESULT_WRITTEN" == "0" ]]; then
        log "saving what the run measured before the container goes"
        run_bounded 240 finish_result
      fi
      if [[ "$KEEP" == "1" ]]; then
        log "--keep: container ${VMID} left in place; destroy it with: ssh ${PVE_ALIAS} pct destroy ${VMID} --force --purge"
      elif ct_destroy "$VMID" "$NONCE"; then
        log "destroyed container ${VMID}"
      else
        printf 'LEFT BEHIND: %s (destroy it with: ssh %s pct destroy %s --force --purge)\n' \
          "$VMID" "$PVE_ALIAS" "$VMID" >&2
        [[ "$rc" -ne 0 ]] || rc=1
      fi
    fi
  fi
  [[ -z "$TREE_TAR" ]] || rm -f -- "$TREE_TAR"
  [[ -z "$SCRATCH" ]] || rm -rf -- "$SCRATCH"
  exit "$rc"
}

on_signal() {
  INTERRUPTED="$1"
  log "interrupted (signal $1); cleaning up"
  exit $((128 + $1))
}

# ── step 2: host headroom and the VMID ───────────────────────────────────────
pve_require_headroom "$SHAPE_HARDWARE" || die2 "refusing: the Proxmox host does not have enough free memory"
picked="$(pve_pick_vmid)" || die2 "refusing: no usable VMID (see above)"
if [[ -n "$VMID" ]]; then
  _pve_in_range "$VMID" || die2 "refusing: VMID ${VMID} is outside the harness range"
  [[ "$(_ct_presence "$VMID")" == "absent" ]] || die2 "refusing: guest ${VMID} exists or cannot be checked"
else
  VMID="$picked"
fi
log "shape ${SHAPE_ID} (row ${SHAPE_ROW}), container ${VMID}, source ${SOURCE}"

if [[ "$DRY_RUN" == "1" ]]; then
  SCRATCH="$(mktemp -d)"
  ART="$SCRATCH"
  plan "would write artifacts to ${OUT:-tests/harness/reports/install-matrix/<UTC timestamp>-${SHAPE_ID}}"
else
  [[ -n "$OUT" ]] || OUT="${REPORT_ROOT}/$(date -u +%Y%m%dT%H%M%SZ)-${SHAPE_ID}"
  mkdir -p "$OUT"
  chmod 700 "$OUT"
  ART="$OUT"
fi
# run.txt is the harness's own note (not part of result.json): the container id,
# and below the uninstall verdict. fresh-test-ct.sh reads it.
printf 'vmid=%s\n' "$VMID" >"${ART}/run.txt"

# ── step 3: from here on every exit path goes through cleanup ────────────────
trap cleanup EXIT
trap 'on_signal 2' INT
trap 'on_signal 15' TERM
trap 'on_signal 1' HUP

# ── step 4: template, devices, packed tree ───────────────────────────────────
VOLID="$(pve_template_volid "$SHAPE_TEMPLATE")" || die2 "refusing: template ${SHAPE_TEMPLATE} is not available on the host"
if [[ "$SHAPE_HARDWARE" == "gpu" || "$SHAPE_HARDWARE" == "gpu+npu" ]]; then
  dev_text="$(_pve_ssh 1 "bash -s -- ${SHAPE_HARDWARE}" <"${HERE}/lib/host-devices.sh")" \
    || die2 "refusing: could not read the host's device nodes"
  while read -r kind value; do
    case "$kind" in
      dev) DEVS+=("$value") ;;
      major) MAJORS+=("$value") ;;
      *) ;;
    esac
  done <<<"$dev_text"
  [[ ${#DEVS[@]} -gt 0 ]] || die2 "refusing: the host shows no GPU device nodes for a ${SHAPE_HARDWARE} shape"
fi
if [[ -n "$TREE_DIR" ]]; then
  TREE_TAR="$(mktemp)"
  tar -C "$TREE_DIR" --exclude=.git --exclude=.venv --exclude=node_modules --exclude=ui/dist \
    -czf "$TREE_TAR" . || die2 "could not pack ${TREE_DIR}"
fi

# ── step 5: create, start, wait for the network ──────────────────────────────
create_rc=0
ct_create "$VMID" "$VOLID" "$SHAPE_UNPRIVILEGED" "$SHAPE_CORES" "$SHAPE_MEMORY_MB" "$SHAPE_DISK_GB" \
  ${DEVS[@]+"${DEVS[@]}"} || create_rc=$?
# Only a run that got as far as `pct create` may clean anything up; a refusal
# (status 90) created nothing, and what is at that VMID is not ours.
[[ "$create_rc" == "$_CT_REFUSED" ]] || CT_MAYBE=1
[[ "$create_rc" == "0" ]] || { log "could not create container ${VMID}"; exit 1; }
if [[ ${#MAJORS[@]} -gt 0 ]]; then
  ct_apply_device_rules "$VMID" "$SHAPE_UNPRIVILEGED" "${MAJORS[@]}" || { log "could not apply device rules"; exit 1; }
fi
ct_start "$VMID" || { log "could not start container ${VMID}"; exit 1; }
GUEST_UP=1
ct_wait_network "$VMID" || { log "container ${VMID} has no network"; exit 1; }

# ── step 6: curl and ca-certificates, only if curl is missing ────────────────
PREREQ_SCRIPT='command -v curl >/dev/null 2>&1 && exit 0
if command -v apt-get >/dev/null 2>&1; then
  { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates; } >&2 || exit 1
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y curl ca-certificates >&2 || exit 1
else
  echo "no supported package manager to install curl" >&2
  exit 1
fi
echo "prereqs=curl ca-certificates"'
prereq_out="$(ct_exec "$VMID" "$PREREQ_SCRIPT")" || { log "could not install curl in the guest"; exit 1; }
PREREQS="$(printf '%s\n' "$prereq_out" | sed -n 's/^prereqs=//p' | head -n 1)"
[[ "$PREREQS" == "curl ca-certificates" ]] || PREREQS=""

# ── step 7: baseline measurements ────────────────────────────────────────────
ct_push "$VMID" "${HERE}/collect.sh" /root/collect.sh || { log "could not copy collect.sh"; exit 1; }
ct_exec "$VMID" "bash /root/collect.sh pre" >"${ART}/pre.txt" 2>>"${ART}/collect.err" || log "collect pre failed"

# ── step 8: the install ──────────────────────────────────────────────────────
ARGS="--summary-json=${SUMMARY_PATH}"
[[ "$SHAPE_ENTRY" != "box" ]] || ARGS="--box ${ARGS}"
CHAN_ENV="" UNATT_ENV=""
[[ -z "$CHANNEL" ]] || CHAN_ENV="HAL0_CHANNEL=${CHANNEL} "
[[ "$SHAPE_ENTRY" != "oneliner-unattended" ]] || UNATT_ENV="HAL0_NONINTERACTIVE=1 HAL0_ALLOW_CPU_ONLY=1 "

if [[ -n "$TREE_DIR" ]]; then
  ct_push "$VMID" "$TREE_TAR" /root/hal0-src.tgz || { log "could not copy the tree"; exit 1; }
  ct_exec "$VMID" "mkdir -p /root/hal0-src && tar -xzf /root/hal0-src.tgz -C /root/hal0-src" \
    || { log "could not unpack the tree"; exit 1; }
  # SKIP_VERIFY is the one thing a tree install must set: there is no signed
  # release to check. Stable, preview and nightly never set it.
  INSTALL_SCRIPT="cd /root/hal0-src && ${UNATT_ENV}HAL0_INSTALL_SKIP_VERIFY=1 bash installer/install.sh ${ARGS}"
  [[ "$SHAPE_ENTRY" == "terminal" ]] || INSTALL_SCRIPT+=" < /dev/null"
elif [[ "$SHAPE_ENTRY" == "terminal" ]]; then
  ct_exec "$VMID" "curl -fsSL ${INSTALL_URL} -o /root/install.sh" || { log "could not download install.sh"; exit 1; }
  INSTALL_SCRIPT="${CHAN_ENV}bash /root/install.sh ${ARGS}"
else
  INSTALL_SCRIPT="set -o pipefail; curl -fsSL ${INSTALL_URL} | ${UNATT_ENV}${CHAN_ENV}bash -s -- ${ARGS}"
fi

install_in_terminal() {
  local remote tty_argv drv rc=0
  ct_owned "$VMID" || { log "refusing: container ${VMID} is not harness-owned"; return 1; }
  remote="pct exec ${VMID} -- bash -c $(sq "$INSTALL_SCRIPT")"
  tty_argv=(ssh -tt -o BatchMode=yes -o ConnectTimeout=10 -- "$PVE_ALIAS" "$remote")
  drv=(python3 -I "${HERE}/tty_drive.py" --answers "$ANSWERS_FILE" --transcript "${ART}/transcript.txt"
    --result "${ART}/driver.json" --timeout "$TIMEOUT" -- "${tty_argv[@]}")
  if [[ "$DRY_RUN" == "1" ]]; then
    plan "tty: ${drv[*]}"
    synth_driver 0 false
    return 0
  fi
  run_driver "${drv[@]}" || rc=$?
  return "$rc"
}

install_unattended() {
  local rc=0
  run_bounded "$TIMEOUT" ct_exec_to_transcript || rc=$?
  if [[ "$rc" == "124" ]]; then
    synth_driver 124 true
  else
    synth_driver "$rc" false
  fi
  return "$rc"
}
ct_exec_to_transcript() { ct_exec "$VMID" "$INSTALL_SCRIPT" >"${ART}/transcript.txt" 2>&1; }

log "installing (${SHAPE_ENTRY}, ${SOURCE})"
rc=0
if [[ "$SHAPE_ENTRY" == "oneliner-unattended" ]]; then
  install_unattended || rc=$?
else
  install_in_terminal || rc=$?
fi
INSTALL_RC="$rc"
log "install exited ${INSTALL_RC}"

# ── step 9: what the install left behind ─────────────────────────────────────
collect_post
if [[ "$INSTALL_RC" == "0" ]]; then
  ct_exec "$VMID" "bash /root/collect.sh probe" >"${ART}/probe.txt" 2>>"${ART}/collect.err" || log "collect probe failed"
fi

# ── step 10: optional uninstall and residue check ────────────────────────────
if [[ "$UNINSTALL" == "1" ]]; then
  log "uninstalling"
  UNINSTALL_OK=0
  ct_exec "$VMID" 'hal0 uninstall --purge --force || bash /usr/lib/hal0/current/installer/uninstall.sh --purge --force || bash /opt/hal0/installer/uninstall.sh --purge --force' \
    >>"${ART}/transcript-uninstall.txt" 2>&1 && UNINSTALL_OK=1
  # shellcheck disable=SC2016  # the guest shell expands these, not this one
  RESIDUE_SCRIPT='r=""; for p in /opt/hal0 /usr/lib/hal0/current /etc/hal0 /var/lib/hal0 /usr/local/bin/hal0 /usr/local/bin/hal0-agent; do [ -e "$p" ] && r="$r path:$p"; done; for u in hal0-api hal0-openwebui hal0-agent@; do systemctl list-unit-files 2>/dev/null | grep -q "^$u" && r="$r unit:$u"; done; getent group hal0 >/dev/null && r="$r group:hal0"; echo "$r"'
  if residue="$(ct_exec "$VMID" "$RESIDUE_SCRIPT" 2>/dev/null)"; then
    residue="$(printf '%s' "$residue" | tr -cd 'A-Za-z0-9 :/@._-' | tr -s ' ' | sed -e 's/^ *//' -e 's/ *$//')"
    if [[ -z "$residue" ]]; then residue="clean"; else UNINSTALL_OK=0; fi
  else
    residue="unknown"
    UNINSTALL_OK=0
  fi
  printf 'uninstall_ok=%s\nresidue=%s\n' "$UNINSTALL_OK" "$residue" >>"${ART}/run.txt"
fi

# ── steps 11 and 12: copy out, assemble, report ──────────────────────────────
finish_result
if [[ "$DRY_RUN" == "1" ]]; then
  plan "would print the path of ${OUT:-<out>}/result.json"
  exit 0
fi
printf '%s\n' "${ART}/result.json"

if [[ "$INSTALL_RC" == "0" ]] && grep -qx 'chat_ok=1' "${ART}/probe.txt" 2>/dev/null; then
  exit 0
fi
exit 1
