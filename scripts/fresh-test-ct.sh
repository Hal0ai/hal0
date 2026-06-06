#!/usr/bin/env bash
# fresh-test-ct.sh (#407) — clone the CT-200 hal0-test-template, run a full
# install -> smoke -> uninstall cycle on a byte-identical clean box, assert
# ZERO residue, destroy the clone, and emit one JSON line.
#
# This is the regression net PR CI cannot provide: CI runs `pytest`/UI builds
# but never executes install.sh (needs root + systemd + a real machine). This
# harness does, on an ephemeral clone, so installer/updater/uninstall changes
# (notably the FHS-layout work in #495/#406) become verifiable end-to-end.
#
#   fresh-test-ct.sh [--vmid N] [--keep] [--from-tree <dir>] [--with-models]
#
# Modes:
#   (default)         install the LIVE release: `curl hal0.dev/install.sh | bash`.
#                     NOTE: that serves the last *published* tarball, which may
#                     predate unmerged fixes on main.
#   --from-tree <dir> rsync the working tree to the clone and run its
#                     installer/install.sh directly (HAL0_INSTALL_SKIP_VERIFY=1).
#                     Use this to verify the CURRENT code, e.g. an installer PR.
#
# Env overrides: HAL0_TEST_TEMPLATE(200), HAL0_PVE(pve ssh alias),
#   HAL0_TEST_KEY(~/.ssh/thin-mint), HAL0_INSTALL_URL(https://hal0.dev/install.sh).
set -uo pipefail

TEMPLATE="${HAL0_TEST_TEMPLATE:-200}"
PVE_HOST="${HAL0_PVE:-pve}"
KEY="${HAL0_TEST_KEY:-$HOME/.ssh/thin-mint}"
INSTALL_URL="${HAL0_INSTALL_URL:-https://hal0.dev/install.sh}"
VMID=""; KEEP=0; FROM_TREE=""; WITH_MODELS=0

usage() { sed -n '2,30p' "$0"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --vmid) VMID="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --from-tree) FROM_TREE="$2"; shift 2 ;;
    --with-models) WITH_MODELS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

PVE() { ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 "$PVE_HOST" "$@"; }
log() { echo "[fresh-test-ct] $*" >&2; }

# Pick a free ephemeral vmid (990-999) if none given.
if [[ -z "$VMID" ]]; then
  for c in $(seq 990 999); do
    PVE "pct status $c" >/dev/null 2>&1 || { VMID="$c"; break; }
  done
fi
[[ -n "$VMID" ]] || { echo "no free vmid in 990-999" >&2; exit 1; }

START="$(date +%s)"
INSTALL_OK=false; SMOKE_OK=false; UNINSTALL_OK=false; RESIDUE="unknown"; IP=""

emit() {
  local elapsed=$(( $(date +%s) - START ))
  printf '{"row":"install-smoke","clone_id":%s,"install_ok":%s,"smoke_ok":%s,"uninstall_ok":%s,"residue":"%s","ip":"%s","elapsed_s":%s}\n' \
    "$VMID" "$INSTALL_OK" "$SMOKE_OK" "$UNINSTALL_OK" "$RESIDUE" "$IP" "$elapsed"
}
cleanup() {
  if [[ "$KEEP" == "1" ]]; then log "--keep: clone $VMID left running at ${IP:-?}"; return; fi
  # A privileged LXC holding /dev/kfd (after install warms ROCm) hangs on
  # `lxc-stop --kill`. SIGKILL the lxc-start monitor first — that tears the
  # container down immediately — then clear any stale lock and purge-destroy.
  PVE "pkill -9 -f 'lxc-start -F -n ${VMID}' 2>/dev/null; sleep 2; rm -f /run/lock/lxc/pve-config-${VMID}.lock; pct destroy $VMID --force --purge" >/dev/null 2>&1 || true
  log "destroyed clone $VMID"
}
trap cleanup EXIT

log "clone $TEMPLATE -> $VMID"
PVE "pct clone $TEMPLATE $VMID --hostname hal0-test-$VMID" >&2 || { emit; exit 1; }
# Optional: mount the shared model store read-only (skips re-download for
# tests that exercise model pulls). Not needed for the install/uninstall smoke.
if [[ "$WITH_MODELS" == "1" ]]; then
  PVE "pct set $VMID --mp0 /mnt/ai-models,mp=/mnt/ai-models,ro=1,backup=0" >&2 || true
fi
PVE "pct start $VMID" >&2 || { emit; exit 1; }

log "wait for readiness marker (max 120s)"
for _ in $(seq 1 60); do
  PVE "pct exec $VMID -- test -f /tmp/hal0-test-ready" 2>/dev/null && break
  sleep 2
done
IP="$(PVE "pct exec $VMID -- hostname -I" 2>/dev/null | awk '{print $1}')"
[[ -n "$IP" ]] || { log "no IP"; emit; exit 1; }
log "clone IP $IP"

SSH() { ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "halo@$IP" "$@"; }
for _ in $(seq 1 30); do SSH true 2>/dev/null && break; sleep 2; done

# ── install ──────────────────────────────────────────────────────────────────
if [[ -n "$FROM_TREE" ]]; then
  log "install from working tree: $FROM_TREE"
  rsync -a -e "ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    --exclude .git --exclude .venv --exclude node_modules --exclude 'ui/dist' \
    "$FROM_TREE"/ "halo@$IP:/tmp/hal0-src/" >&2 2>&1 \
    && SSH "cd /tmp/hal0-src && HAL0_INSTALL_SKIP_VERIFY=1 sudo -E bash installer/install.sh" >&2 2>&1 \
    && INSTALL_OK=true
else
  log "install live release: $INSTALL_URL"
  # cosign is absent on the fresh box; skip the signature check for the test
  # (the box is a throwaway clone — we test the install mechanism, not the sig).
  SSH "curl -fsSL '$INSTALL_URL' | HAL0_UPDATE_SKIP_COSIGN=1 sudo -E bash" >&2 2>&1 && INSTALL_OK=true
fi

# ── smoke ────────────────────────────────────────────────────────────────────
# The API can take a few seconds past install to answer; poll /api/health
# (the endpoint install.sh's live-hello uses) for up to ~30s.
log "smoke: status / version / health"
SSH "hal0 status && hal0 --version && for i in \$(seq 1 15); do curl -fsS -m 5 http://localhost:8080/api/health && break; sleep 2; done" >&2 2>&1 && SMOKE_OK=true

# ── uninstall ────────────────────────────────────────────────────────────────
log "uninstall"
if SSH "sudo hal0 uninstall --force" >&2 2>&1 \
   || SSH "sudo bash /opt/hal0/installer/uninstall.sh --force" >&2 2>&1; then
  UNINSTALL_OK=true
fi

# ── residue assertion (the whole point: a clean uninstall leaves nothing) ─────
log "residue check"
RES="$(SSH 'r=""
for p in /opt/hal0 /opt/lemonade /usr/lib/hal0/current /etc/hal0 /var/lib/hal0 /usr/local/bin/hal0 /usr/local/bin/hal0-agent; do
  [ -e "$p" ] && r="$r path:$p"
done
for u in hal0-api hal0-lemonade hal0-openwebui hal0-agent@; do
  systemctl list-unit-files 2>/dev/null | grep -q "^$u" && r="$r unit:$u"
done
getent group hal0 >/dev/null && r="$r group:hal0"
echo "$r"' 2>/dev/null | tr -s ' ')"
RES="$(echo "$RES" | sed -e 's/^ *//' -e 's/ *$//')"
if [[ -z "$RES" ]]; then RESIDUE="clean"; else RESIDUE="$RES"; UNINSTALL_OK=false; fi

emit
[[ "$INSTALL_OK" == true && "$SMOKE_OK" == true && "$UNINSTALL_OK" == true && "$RESIDUE" == clean ]]
