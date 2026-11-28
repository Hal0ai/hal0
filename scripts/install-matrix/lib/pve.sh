# shellcheck shell=bash
# Proxmox host library for the install matrix. SOURCED by run-shape.sh.
#
# Every call to the Proxmox host goes through pve_run (one ssh, one command
# string). The library never sets shell options or traps and never exits the
# caller's shell: functions return non-zero instead.
#
# Ownership guard: ct_destroy, ct_apply_device_rules, ct_start, ct_exec,
# ct_push and ct_pull act on a guest only after ct_owned passes (VMID inside
# the harness range, tag hal0-matrix, hostname hal0-mx-*), and the same test
# (ct_guard) is the first part of the remote command that acts, so the check
# and the action are one ssh call. Nothing else here runs pct against a guest.
#
# Injection rule: pve_run hands ONE string to the remote shell. Every value
# placed in that string is either checked against a strict pattern or passed
# through printf %q first.

# Same default as scripts/proxmox-ve/hal0.sh:132 (a test pins the equality).
_PVE_FEATURES="nesting=1,keyctl=1,fuse=1,mknod=1"
_PVE_TAG="hal0-matrix"
_PVE_HOST_PREFIX="hal0-mx-"
# ct_create returns this when it refused before running `pct create`: nothing
# was created, so the caller must not clean anything up. A failed `pct create`
# returns pct's own status instead.
_CT_REFUSED=90
# A run's nonce (16 hex characters), set by the caller. When set, ct_create
# writes it into the guest's description and ct_destroy given the same nonce
# destroys only a guest that carries it.
CT_NONCE=""

# Quote one value for the remote shell. Plain words stay as they are so the
# recorded command reads naturally; anything else goes through printf %q.
_pve_q() {
  if [[ "$1" =~ ^[A-Za-z0-9_@%+=:,./-]+$ ]]; then
    printf '%s' "$1"
  else
    printf '%q' "$1"
  fi
}

_pve_err() { printf '%s\n' "$*" >&2; }

_pve_vmid_min() { printf '%s' "${HAL0_MATRIX_VMID_MIN:-170}"; }
_pve_vmid_max() { printf '%s' "${HAL0_MATRIX_VMID_MAX:-189}"; }

# A VMID is digits only, no leading zero, at most 9 digits.
_pve_vmid_ok() { [[ "${1:-}" =~ ^[1-9][0-9]{0,8}$ ]]; }
_pve_uint_ok() { [[ "${1:-}" =~ ^[0-9]{1,9}$ ]]; }

# The VMID range must be two plain numbers with 100 <= min <= max and at most
# 64 VMIDs (max - min <= 63): a wide range would leave the tag and hostname as
# the only protection for every guest on the host.
_pve_range_ok() {
  local lo hi
  lo="$(_pve_vmid_min)"
  hi="$(_pve_vmid_max)"
  [[ "$lo" =~ ^[1-9][0-9]{2,8}$ && "$hi" =~ ^[1-9][0-9]{2,8}$ ]] || return 1
  ((10#$lo <= 10#$hi && 10#$hi - 10#$lo <= 63))
}

# pve_check_range: prints why and returns 1 when the range is not usable.
pve_check_range() {
  _pve_range_ok && return 0
  _pve_err "bad VMID range $(_pve_vmid_min)-$(_pve_vmid_max): both must be plain numbers, 100 <= min <= max, and max - min <= 63"
  return 1
}

_pve_in_range() {
  local lo hi
  _pve_range_ok || return 1
  lo="$(_pve_vmid_min)"
  hi="$(_pve_vmid_max)"
  _pve_vmid_ok "$1" || return 1
  ((10#$1 >= 10#$lo && 10#$1 <= 10#$hi))
}

# Deadlines, in seconds, one per kind of call. Each can be set through
# HAL0_MATRIX_TIMEOUT_<NAME>; a value that is not a plain number is ignored.
# _pve_t <NAME> <default> [override]
_pve_t() {
  local var="HAL0_MATRIX_TIMEOUT_${1}" v="${3:-}"
  [[ "$v" =~ ^[1-9][0-9]{0,6}$ ]] || v="${!var:-}"
  [[ "$v" =~ ^[1-9][0-9]{0,6}$ ]] || v="$2"
  printf '%s' "$v"
}

# _pve_ssh <stdin: 0|1> <command string>. `--` ends ssh options so the alias
# can never be read as one; stdin is closed (-n) unless the caller feeds it,
# so a caller's `while read` loop keeps its input. ServerAlive* drops a dead
# connection, and every call runs under `timeout` (KILL, because cleanup runs
# with TERM ignored and a child inherits that): _PVE_DEADLINE seconds, which a
# ct_* function sets for its own calls, else the READ deadline. A shell
# function named ssh (the dry run's shadow) cannot run under timeout, so it is
# called directly.
_pve_ssh() {
  local host="${HAL0_PVE:-pve}" nflag=() tmo=() dl rc=0
  if [[ "$host" == -* || ! "$host" =~ ^[^[:space:]]+$ ]]; then
    _pve_err "bad HAL0_PVE alias: ${host}"
    return 1
  fi
  [[ "$1" == "1" ]] || nflag=(-n)
  dl="${_PVE_DEADLINE:-$(_pve_t READ 120)}"
  if [[ "$(type -t ssh)" != "function" ]]; then
    tmo=(timeout --foreground -s KILL "$dl")
  fi
  "${tmo[@]}" ssh "${nflag[@]}" -o BatchMode=yes -o ConnectTimeout=10 \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -- "$host" "$2" || rc=$?
  if [[ "${#tmo[@]}" -gt 0 && ( "$rc" -eq 124 || "$rc" -eq 137 ) ]]; then
    _pve_err "the call to the host passed its ${dl}s deadline and was stopped"
  fi
  return "$rc"
}

# pve_run <command string>
pve_run() { _pve_ssh 0 "$1"; }

# _ct_presence <vmid>: prints present, absent or unknown (ssh failed or the
# host said something else). Decided from the config file, not an exit code.
_ct_presence() {
  local out rc _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t READ 60)"
  out="$(pve_run "if test -e /etc/pve/lxc/${1}.conf; then echo present; else echo absent; fi")" && rc=0 || rc=$?
  if [[ "$rc" -eq 0 && ( "$out" == "present" || "$out" == "absent" ) ]]; then
    printf '%s\n' "$out"
  else
    printf 'unknown\n'
  fi
}

# The one read the headroom guard makes. Fixed text: nothing from a shape or
# the environment is in it. After free's table it prints one `gpu USED TOTAL`
# line (bytes) for every card whose GTT counters exist; a host without them
# prints none, which is not an error.
# shellcheck disable=SC2016  # the remote shell expands these, not this one
_PVE_HEADROOM_CMD='free -g; for d in /sys/class/drm/card*/device; do if [ -r "$d/mem_info_gtt_used" ] && [ -r "$d/mem_info_gtt_total" ]; then echo "gpu $(cat "$d/mem_info_gtt_used") $(cat "$d/mem_info_gtt_total")"; fi; done; true'

# pve_mem_available_gb: integer from the "available" column of `free -g`.
pve_mem_available_gb() {
  local out n _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t READ 60)"
  out="$(pve_run 'free -g')" || return 1
  n="$(awk '/^Mem:/ {print $7}' <<<"$out")"
  [[ "$n" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$n"
}

# pve_require_headroom [devices] [container_memory_mb]
# devices is the shape's hardware value: gpu and gpu+npu use the GPU floor,
# anything else (none, cpu, absent) the CPU-only one. The host must show at
# least the floor plus half the container's memory limit (rounded up to a
# whole GB) as available; all three numbers are printed. The host's reply is
# untrusted text: only lines that are exactly `Mem: ...` or `gpu <digits>
# <digits>` are used, every number is digits-checked before arithmetic, and
# nothing read is ever evaluated or put into a later command.
pve_require_headroom() {
  local devices="${1:-none}" mem_mb="${2:-0}" floor floor_name out tag a b c f
  local have="" gpu_used=0 gpu_total=0 gpu_seen=0 gpu_text half=0 limit_gb=0 need
  case "$devices" in
    gpu | gpu+npu)
      floor_name="HAL0_MATRIX_MIN_AVAILABLE_GB_GPU"
      floor="${HAL0_MATRIX_MIN_AVAILABLE_GB_GPU:-22}" ;;
    *)
      floor_name="HAL0_MATRIX_MIN_AVAILABLE_GB"
      floor="${HAL0_MATRIX_MIN_AVAILABLE_GB:-12}" ;;
  esac
  _pve_uint_ok "$floor" || { _pve_err "bad headroom floor: ${floor_name}"; return 1; }
  _pve_uint_ok "$mem_mb" || { _pve_err "bad container memory: ${mem_mb}"; return 1; }
  half=$(((10#$mem_mb + 2047) / 2048))
  limit_gb=$(((10#$mem_mb + 1023) / 1024))
  need=$((10#$floor + half))
  out="$(_pve_ssh 0 "$_PVE_HEADROOM_CMD")" || { _pve_err "cannot read host memory"; return 1; }
  while read -r tag a b c _ _ f; do
    if [[ "$tag" == "Mem:" ]]; then
      [[ -n "$have" || ! "$f" =~ ^[0-9]{1,9}$ ]] || have="$f"
    elif [[ "$tag" == "gpu" && "$a" =~ ^[0-9]{1,15}$ && "$b" =~ ^[0-9]{1,15}$ && -z "$c" ]]; then
      gpu_used=$((gpu_used + 10#$a))
      gpu_total=$((gpu_total + 10#$b))
      gpu_seen=1
    fi
  done <<<"$out"
  [[ -n "$have" ]] || { _pve_err "cannot read host memory"; return 1; }
  if ((gpu_seen)); then
    gpu_text="gpu memory: $((gpu_used / 1073741824)) GB used of $((gpu_total / 1073741824)) GB"
  else
    gpu_text="gpu memory: not reported"
  fi
  _pve_err "host memory: ${have} GB available, ${gpu_text}"
  _pve_err "host needs ${need} GB available: floor ${floor} GB + ${half} GB (half of the container's ${limit_gb} GB limit)"
  if ((10#$have < need)); then
    _pve_err "host has ${have} GB available, need ${need}"
    return 1
  fi
}

# pve_template_volid <pattern>: the single local template whose id contains it.
pve_template_volid() {
  local pat="${1:-}" out all matches count
  [[ "$pat" =~ ^[A-Za-z0-9._+-]+$ ]] || { _pve_err "bad template pattern: $pat"; return 1; }
  out="$(pve_run 'pveam list local')" || { _pve_err "cannot list templates"; return 1; }
  all="$(printf '%s\n' "$out" | awk 'NR > 1 && $1 != "" {print $1}')"
  matches="$(printf '%s\n' "$all" | grep -F -- "$pat" || true)"
  count="$(printf '%s' "$matches" | grep -c . || true)"
  if [[ "$count" == "1" ]]; then
    printf '%s\n' "$matches"
    return 0
  fi
  if [[ "$count" == "0" ]]; then
    _pve_err "no template matches '${pat}'; available:"
  else
    _pve_err "${count} templates match '${pat}':"
    all="$matches"
  fi
  printf '%s\n' "$all" | sed 's/^/  /' >&2
  return 1
}

# pve_storage [disk_gb]: HAL0_MATRIX_STORAGE, else the first active rootdir
# storage. With disk_gb the chosen storage must be listed, active, and have at
# least disk_gb + 10 GB available (`pvesm status` reports KiB); the storage and
# its free space are printed. Without disk_gb only the name is returned.
pve_storage() {
  local want="${1:-}" name="" out avail status gb need
  if [[ -n "$want" ]]; then
    _pve_uint_ok "$want" || { _pve_err "bad disk size: ${want}"; return 1; }
  fi
  if [[ -n "${HAL0_MATRIX_STORAGE:-}" ]]; then
    [[ "$HAL0_MATRIX_STORAGE" =~ ^[A-Za-z0-9._-]+$ ]] || { _pve_err "bad HAL0_MATRIX_STORAGE"; return 1; }
    name="$HAL0_MATRIX_STORAGE"
    if [[ -z "$want" ]]; then
      printf '%s\n' "$name"
      return 0
    fi
  fi
  out="$(pve_run 'pvesm status --content rootdir')" || { _pve_err "cannot list storage"; return 1; }
  if [[ -z "$name" ]]; then
    name="$(awk 'NR > 1 && $3 == "active" {print $1; exit}' <<<"$out")"
    [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || { _pve_err "no active storage accepts container disks"; return 1; }
  fi
  if [[ -z "$want" ]]; then
    printf '%s\n' "$name"
    return 0
  fi
  status="$(awk -v n="$name" 'NR > 1 && $1 == n {print $3; exit}' <<<"$out")"
  [[ "$status" == "active" ]] || { _pve_err "storage ${name} is not listed as an active place for container disks"; return 1; }
  avail="$(awk -v n="$name" 'NR > 1 && $1 == n {print $6; exit}' <<<"$out")"
  [[ "$avail" =~ ^[0-9]{1,15}$ ]] || { _pve_err "cannot read the free space of storage ${name}"; return 1; }
  gb=$((10#$avail / 1048576))
  need=$((10#$want + 10))
  _pve_err "storage ${name}: ${gb} GB free, need ${need} (disk ${want} GB + 10)"
  ((gb >= need)) || { _pve_err "refusing: storage ${name} has too little free space"; return 1; }
  printf '%s\n' "$name"
}

# pve_pick_vmid: first free VMID in range. Refuses when ANY guest exists inside
# the range, tagged or not (a run's container has no tag until pct create has
# written its config), and when a tagged guest exists anywhere.
pve_pick_vmid() {
  local lo hi out
  lo="$(_pve_vmid_min)"
  hi="$(_pve_vmid_max)"
  pve_check_range || return 1
  out="$(pve_run 'pvesh get /cluster/resources --type vm --output-format json')" \
    || { _pve_err "cannot list guests"; return 1; }
  printf '%s' "$out" | python3 -I -c '
import json, re, sys
lo, hi, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
try:
    rows = json.load(sys.stdin)
    if not isinstance(rows, list):
        raise ValueError("not a list")
    [int(r["vmid"]) for r in rows if "vmid" in r]
except (ValueError, TypeError, AttributeError, KeyError):
    print("cannot parse the guest list from the host", file=sys.stderr)
    sys.exit(1)
for r in rows:
    if tag in re.split(r"[;,]", r.get("tags") or ""):
        print("another matrix container is present: %s" % r.get("vmid"), file=sys.stderr)
        sys.exit(3)
for r in rows:
    if "vmid" in r and lo <= int(r["vmid"]) <= hi:
        print("a guest already exists inside the harness range %d-%d: %s" % (lo, hi, int(r["vmid"])), file=sys.stderr)
        sys.exit(3)
print(lo)
' "$lo" "$hi" "$_PVE_TAG" || return 1
}

# ct_owned <vmid> [nonce]: 0 only for a guest this harness created. With a
# nonce the guest's description must also be `hal0-matrix run <nonce>`, which
# proves it is the guest THIS run created.
ct_owned() {
  local vmid="${1:-}" nonce="${2:-}" conf line tags="" host="" desc="" t found=1 _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t READ 60)"
  _pve_in_range "$vmid" || return 1
  if [[ -n "$nonce" ]]; then
    [[ "$nonce" =~ ^[0-9a-f]{16}$ ]] || return 1
  fi
  conf="$(pve_run "pct config ${vmid}")" || return 1
  while IFS= read -r line; do
    case "$line" in
      tags:*) tags="${line#tags:}" ;;
      hostname:*) host="${line#hostname:}" ;;
      description:*) desc="${line#description:}" ;;
    esac
  done <<<"$conf"
  host="${host// /}"
  tags="${tags// /}"
  [[ "$host" == "${_PVE_HOST_PREFIX}"* ]] || return 1
  if [[ -n "$nonce" ]]; then
    desc="${desc//%20/ }"
    desc="${desc# }"
    desc="${desc% }"
    [[ "$desc" == "hal0-matrix run ${nonce}" ]] || return 1
  fi
  local IFS=';,'
  for t in $tags; do
    [[ "$t" == "$_PVE_TAG" ]] && found=0
  done
  return "$found"
}

# ct_guard <vmid> [nonce]: remote shell text that ends in `&& ` and succeeds
# only for a guest this harness created: hostname hal0-mx-<vmid>, tag
# hal0-matrix, and with a nonce the description `hal0-matrix run <nonce>`.
# Every state-changing remote command starts with it, so the check and the
# action are one ssh call and one remote shell. Fixed text: only the VMID (a
# checked number) and the nonce (checked hex) are placed in it. Prints nothing
# and returns 1 for a VMID or nonce that fails its check.
ct_guard() {
  local vmid="${1:-}" nonce="${2:-}" g
  _pve_in_range "$vmid" || return 1
  [[ -z "$nonce" || "$nonce" =~ ^[0-9a-f]{16}$ ]] || return 1
  g="{ pct config ${vmid} | grep -q '^hostname: ${_PVE_HOST_PREFIX}${vmid}\$'"
  g+=" && pct config ${vmid} | grep -Eq '^tags: ([^;,]*[;,])*${_PVE_TAG}([;,].*)?\$'"
  if [[ -n "$nonce" ]]; then
    g+=" && pct config ${vmid} | grep -Eq '^description: ${_PVE_TAG}(%20| )run(%20| )${nonce}\$'"
  fi
  printf '%s; } && ' "$g"
}

_ct_require_owned() {
  ct_owned "${1:-}" "${2:-}" || { _pve_err "refusing: guest '${1:-}' is not a harness-owned container"; return 1; }
}

# ct_create <vmid> <volid> <unprivileged 0|1> <cores> <memory_mb> <disk_gb> [device...]
# Returns _CT_REFUSED (90) when it refused before running `pct create`; any
# other non-zero status is pct's, and a guest may then exist.
ct_create() {
  local vmid="${1:-}" volid="${2:-}" unpriv="${3:-}" cores="${4:-}" mem="${5:-}" disk="${6:-}"
  shift 6 2>/dev/null || { _pve_err "ct_create: too few arguments"; return "$_CT_REFUSED"; }
  local bridge="${HAL0_MATRIX_BRIDGE:-vmbr0}" storage dev i=0 cmd st _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t CREATE 600)"
  _pve_in_range "$vmid" || { _pve_err "refusing: VMID '${vmid}' is outside the harness range"; return "$_CT_REFUSED"; }
  [[ "$volid" =~ ^[A-Za-z0-9._+-]+:[A-Za-z0-9._+/-]+$ ]] || { _pve_err "bad template volume id"; return "$_CT_REFUSED"; }
  [[ "$unpriv" =~ ^[01]$ ]] || { _pve_err "unprivileged must be 0 or 1"; return "$_CT_REFUSED"; }
  if ! _pve_uint_ok "$cores" || ! _pve_uint_ok "$mem" || ! _pve_uint_ok "$disk"; then
    _pve_err "cores, memory and disk must be integers"
    return "$_CT_REFUSED"
  fi
  [[ -z "$CT_NONCE" || "$CT_NONCE" =~ ^[0-9a-f]{16}$ ]] || { _pve_err "bad run nonce"; return "$_CT_REFUSED"; }
  [[ "$bridge" =~ ^[A-Za-z0-9_.-]+$ ]] || { _pve_err "bad HAL0_MATRIX_BRIDGE"; return "$_CT_REFUSED"; }
  for dev in "$@"; do
    [[ "$dev" =~ ^/dev/[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*(,gid=[0-9]+)?$ ]] \
      || { _pve_err "bad device argument: ${dev}"; return "$_CT_REFUSED"; }
  done
  # Never create over an existing guest, and not when that cannot be told.
  case "$(_ct_presence "$vmid")" in
    absent) ;;
    present) _pve_err "refusing: guest ${vmid} already exists"; return "$_CT_REFUSED" ;;
    *) _pve_err "refusing: cannot tell whether guest ${vmid} exists"; return "$_CT_REFUSED" ;;
  esac
  storage="$(pve_storage "$disk")" || return "$_CT_REFUSED"
  cmd="pct create $(_pve_q "$vmid") $(_pve_q "$volid")"
  cmd+=" --hostname $(_pve_q "${_PVE_HOST_PREFIX}${vmid}")"
  cmd+=" --tags $(_pve_q "$_PVE_TAG")"
  [[ -z "$CT_NONCE" ]] || cmd+=" --description $(_pve_q "hal0-matrix run ${CT_NONCE}")"
  cmd+=" --cores ${cores} --memory ${mem} --swap 1024"
  cmd+=" --rootfs $(_pve_q "${storage}:${disk}")"
  cmd+=" --net0 $(_pve_q "name=eth0,bridge=${bridge},ip=dhcp")"
  cmd+=" --unprivileged ${unpriv}"
  cmd+=" --features $(_pve_q "$_PVE_FEATURES")"
  cmd+=" --onboot 0"
  for dev in "$@"; do
    cmd+=" --dev${i} $(_pve_q "$dev")"
    i=$((i + 1))
  done
  pve_run "$cmd" && st=0 || st=$?
  if [[ "$st" -ne 0 ]]; then
    _pve_err "pct create failed for ${vmid} (exit ${st})"
  fi
  return "$st"
}

# ct_wait_create_done <vmid> [seconds]: wait (bounded; HAL0_MATRIX_CREATE_WAIT,
# default 120) until no `pct create <vmid>` process runs on the host. An
# interrupted harness leaves its ssh dead but the remote create may carry on.
# The remote command is fixed text with only the checked VMID in it; the [p]
# keeps the remote shell's own command line from matching the pattern. A
# reading that cannot be made does not count as idle.
ct_wait_create_done() {
  local vmid="${1:-}" limit="${2:-${HAL0_MATRIX_CREATE_WAIT:-120}}" start now out _PVE_DEADLINE
  _pve_in_range "$vmid" || { _pve_err "refusing: VMID '${vmid}' is outside the harness range"; return 1; }
  _pve_uint_ok "$limit" || { _pve_err "bad wait limit"; return 1; }
  _PVE_DEADLINE="$(_pve_t READ 60)"
  start="$(date +%s)"
  while :; do
    out="$(pve_run "if pgrep -f '[p]ct create ${vmid}( |\$)' >/dev/null 2>&1; then echo busy; else echo idle; fi")" || out=""
    [[ "$out" != "idle" ]] || return 0
    now="$(date +%s)"
    ((now - start < limit)) || break
    sleep "${HAL0_MATRIX_POLL_SLEEP:-2}"
  done
  _pve_err "a pct create for ${vmid} was still running after ${limit}s"
  return 1
}

# ct_lock_held <vmid>: 0 when the guest's config shows a lock: line (a create
# that never finished leaves one). Read only; changes nothing.
ct_lock_held() {
  local vmid="${1:-}" conf _PVE_DEADLINE
  _pve_in_range "$vmid" || return 1
  _PVE_DEADLINE="$(_pve_t READ 60)"
  conf="$(pve_run "pct config ${vmid}")" || return 1
  grep -q '^lock:' <<<"$conf"
}

# ct_apply_device_rules <vmid> <unprivileged 0|1> <major...>
# Mirrors scripts/proxmox-ve/hal0.sh apply_raw_config for a guest with GPU
# devices: one cgroup2 allow rule per major, the memlock limit, and for a
# privileged guest the unconfined AppArmor profile. Each line is appended only
# if absent.
ct_apply_device_rules() {
  local vmid="${1:-}" unpriv="${2:-}" maj line cmd="" conf seen=" " _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t EXEC 120)"
  shift 2 2>/dev/null || { _pve_err "ct_apply_device_rules: too few arguments"; return 1; }
  _ct_require_owned "$vmid" || return 1
  [[ "$unpriv" =~ ^[01]$ ]] || { _pve_err "unprivileged must be 0 or 1"; return 1; }
  [[ $# -gt 0 ]] || { _pve_err "ct_apply_device_rules: no device majors given"; return 1; }
  for maj in "$@"; do
    _pve_uint_ok "$maj" || { _pve_err "bad device major: ${maj}"; return 1; }
  done
  conf="/etc/pve/lxc/${vmid}.conf"
  for maj in "$@"; do
    [[ "$seen" == *" ${maj} "* ]] && continue
    seen+="${maj} "
    line="lxc.cgroup2.devices.allow: c ${maj}:* rwm"
    cmd+="{ grep -qxF -- $(_pve_q "$line") ${conf} || printf '%s\\n' $(_pve_q "$line") >> ${conf}; } && "
  done
  line="lxc.prlimit.memlock: unlimited"
  cmd+="{ grep -qxF -- $(_pve_q "$line") ${conf} || printf '%s\\n' $(_pve_q "$line") >> ${conf}; }"
  if [[ "$unpriv" != "1" ]]; then
    line="lxc.apparmor.profile: unconfined"
    cmd+=" && { grep -qxF -- $(_pve_q "$line") ${conf} || printf '%s\\n' $(_pve_q "$line") >> ${conf}; }"
  fi
  pve_run "$(ct_guard "$vmid")${cmd}"
}

# ct_start <vmid>
ct_start() {
  local _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t START 120)"
  _ct_require_owned "${1:-}" || return 1
  pve_run "$(ct_guard "$1")pct start $1"
}

# ct_exec <vmid> <command string>: root, no terminal. 120 s unless the caller
# sets CT_EXEC_TIMEOUT for this call (the install does).
ct_exec() {
  local vmid="${1:-}" _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t EXEC 120 "${CT_EXEC_TIMEOUT:-}")"
  _ct_require_owned "$vmid" || return 1
  [[ $# -ge 2 ]] || { _pve_err "ct_exec: no command given"; return 1; }
  pve_run "$(ct_guard "$vmid")pct exec ${vmid} -- bash -c $(_pve_q "$2")"
}

# ct_wait_network <vmid> [seconds=120]: the guest resolves hal0.dev.
ct_wait_network() {
  local vmid="${1:-}" limit="${2:-120}" start now err=""
  _pve_uint_ok "$limit" || { _pve_err "bad timeout"; return 1; }
  _ct_require_owned "$vmid" || return 1
  start="$(date +%s)"
  while :; do
    if err="$(ct_exec "$vmid" 'getent hosts hal0.dev' 2>&1 >/dev/null)"; then
      return 0
    fi
    now="$(date +%s)"
    if ((now - start >= limit)); then
      break
    fi
    sleep "${HAL0_MATRIX_POLL_SLEEP:-2}"
  done
  _pve_err "guest ${vmid} did not resolve hal0.dev within ${limit}s"
  [[ -z "$err" ]] || _pve_err "last error: ${err}"
  return 1
}

# ct_push <vmid> <local> <remote>: stream a local file into the guest.
ct_push() {
  local vmid="${1:-}" src="${2:-}" dst="${3:-}" _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t PULL 300)"
  _ct_require_owned "$vmid" || return 1
  [[ -f "$src" ]] || { _pve_err "no such local file: ${src}"; return 1; }
  [[ -n "$dst" ]] || { _pve_err "ct_push: no remote path"; return 1; }
  _pve_ssh 1 "$(ct_guard "$vmid")pct exec ${vmid} -- bash -c $(_pve_q "cat > $(_pve_q "$dst")")" <"$src"
}

# ct_pull <vmid> <remote> <local>: stream a guest file out.
ct_pull() {
  local vmid="${1:-}" src="${2:-}" dst="${3:-}" tmp rc _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t PULL 300)"
  _ct_require_owned "$vmid" || return 1
  [[ -n "$src" && -n "$dst" ]] || { _pve_err "ct_pull: need a remote and a local path"; return 1; }
  tmp="$(mktemp "${dst}.XXXXXX")" || { _pve_err "cannot create a temporary file beside ${dst}"; return 1; }
  if pve_run "$(ct_guard "$vmid")pct exec ${vmid} -- bash -c $(_pve_q "cat $(_pve_q "$src")")" >"$tmp"; then
    mv -f -- "$tmp" "$dst"
  else
    rc=$?
    rm -f -- "$tmp"
    return "$rc"
  fi
}

# ct_destroy <vmid> [nonce]: refuses unless ct_owned (with the nonce when one
# is given), re-checked before each attempt. Without a nonce the rule is the
# manual one: range, tag, hostname.
# Sequence from scripts/fresh-test-ct.sh: SIGKILL the lxc-start monitor (a
# privileged guest holding /dev/kfd hangs `lxc-stop --kill`), clear a stale
# lock, purge-destroy, retry once. "Gone" means the config file is absent on
# the host; an ssh failure is "unknown" and never counts as gone. The [l] in
# the pkill pattern keeps the remote shell's own command line from matching it.
ct_destroy() {
  local vmid="${1:-}" nonce="${2:-}" attempt out="" state="" _PVE_DEADLINE
  _PVE_DEADLINE="$(_pve_t DESTROY 300)"
  for attempt in 1 2; do
    _ct_require_owned "$vmid" "$nonce" || return 1
    out="$(pve_run "$(ct_guard "$vmid" "$nonce"){ pkill -9 -f '[l]xc-start -F -n ${vmid}( |\$)' 2>/dev/null; sleep 2; rm -f /run/lock/lxc/pve-config-${vmid}.lock; pct destroy ${vmid} --force --purge; }" 2>&1)" || true
    state="$(_ct_presence "$vmid")"
    [[ "$state" == "absent" ]] && return 0
    [[ "$attempt" == "1" ]] && sleep "${HAL0_MATRIX_DESTROY_SLEEP:-3}"
  done
  _pve_err "guest ${vmid} is still present after destroy (state: ${state}); destroy output:"
  printf '%s\n' "$out" >&2
  _pve_err "destroy it by hand: pct destroy ${vmid} --force --purge"
  return 1
}
