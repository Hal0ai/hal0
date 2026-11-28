#!/usr/bin/env bash
# fresh-test-ct.sh (#407) - install -> smoke -> uninstall on a fresh container,
# assert zero residue, destroy the container, emit one JSON line.
#
# This is now a thin wrapper over scripts/install-matrix/run-shape.sh, which
# creates a throwaway container from a raw OS template (no pre-built test
# template needed), installs hal0 as a newcomer would, and records the run.
#
#   fresh-test-ct.sh [--shape ID] [--vmid N] [--keep] [--from-tree DIR]
#                    [--out DIR] [--with-models]
#
#   --shape ID        a shape from scripts/install-matrix/shapes.json
#                     (default u2604-cpu-unattended: no terminal, CPU consent
#                     given through the environment).
#   --from-tree DIR   install the working tree at DIR instead of the public
#                     release (run-shape.sh --source tree:DIR).
#   --with-models     accepted and ignored: the matrix containers share no
#                     model store.
#
# It always runs with --uninstall. The JSON row has the same keys as before
# (row, clone_id, install_ok, smoke_ok, uninstall_ok, residue, ip, elapsed_s),
# built from the run's result.json and run.txt; ip is empty. Exit 0 only when the install,
# the smoke probe, the uninstall and the residue check all came out clean.
#
# Env overrides: HAL0_PVE (ssh alias of the Proxmox host, default pve) and the
# HAL0_MATRIX_* settings documented in scripts/install-matrix/lib/pve.sh.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SHAPE="${HERE}/install-matrix/run-shape.sh"

SHAPE="u2604-cpu-unattended" VMID="" KEEP=0 TREE="" OUT="" WARNED_MODELS=0
usage() { sed -n '2,24p' "${BASH_SOURCE[0]}"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --shape | --vmid | --from-tree | --out)
      [[ $# -ge 2 ]] || { echo "$1 needs a value" >&2; exit 2; }
      case "$1" in
        --shape) SHAPE="$2" ;;
        --vmid) VMID="$2" ;;
        --from-tree) TREE="$2" ;;
        --out) OUT="$2" ;;
      esac
      shift 2
      ;;
    --keep) KEEP=1; shift ;;
    --with-models)
      if [[ "$WARNED_MODELS" == "0" ]]; then
        echo "[fresh-test-ct] --with-models is ignored: matrix containers share no model store" >&2
        WARNED_MODELS=1
      fi
      shift
      ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

args=("$SHAPE" --uninstall)
[[ -z "$VMID" ]] || args+=(--vmid "$VMID")
[[ "$KEEP" != "1" ]] || args+=(--keep)
[[ -z "$TREE" ]] || args+=(--source "tree:${TREE}")
[[ -z "$OUT" ]] || args+=(--out "$OUT")

start="$(date +%s)"
stdout="$(bash "$RUN_SHAPE" "${args[@]}")"
rc=$?
result="$(printf '%s\n' "$stdout" | tail -n 1)"
elapsed=$(($(date +%s) - start))

# No result file means the run was refused before a container existed; the
# message is already on stderr and there is no row to append.
if [[ ! -f "$result" ]]; then
  exit $((rc != 0 ? rc : 1))
fi

# run-shape's own status is ANDed in below and again after the row: a failed
# destroy (LEFT BEHIND) makes it non-zero even when every result field is clean.
python3 -I - "$result" "$elapsed" "$rc" <<'PY' || exit 1
import json
import os
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
# run.txt sits beside result.json: the container id and the uninstall verdict.
note = {}
try:
    with open(os.path.join(os.path.dirname(sys.argv[1]), "run.txt"), encoding="utf-8") as fh:
        for line in fh:
            key, _, value = line.rstrip("\n").partition("=")
            note[key] = value
except OSError:
    pass
uninstall = {"ok": note.get("uninstall_ok") == "1", "residue": note.get("residue")}
chat = result.get("chat") or {}
print(
    json.dumps(
        {
            "row": "install-smoke",
            "clone_id": int(note["vmid"]) if note.get("vmid", "").isdigit() else None,
            "install_ok": result.get("install_exit_code") == 0,
            "smoke_ok": chat.get("ok") is True,
            "uninstall_ok": uninstall.get("ok") is True,
            "residue": uninstall.get("residue") or "unknown",
            "ip": "",
            "elapsed_s": int(sys.argv[2]),
        },
        separators=(",", ":"),
    )
)
ok = (
    int(sys.argv[3]) == 0
    and result.get("install_exit_code") == 0
    and chat.get("ok") is True
    and uninstall.get("ok") is True
    and uninstall.get("residue") == "clean"
)
sys.exit(0 if ok else 1)
PY
