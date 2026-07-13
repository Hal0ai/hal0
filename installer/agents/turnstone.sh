#!/bin/sh
# hal0 — turnstone bundled-agent installer (ADR-0004 §6, sibling of hermes).
#
# POSIX shell, dash-safe. Track-latest of the turnstone PyPI package (NO
# version pin per ADR-0004 §3; the nightly agent-shim smoke test catches
# upstream breakage instead of freezing users on a stale release).
#
# turnstone (github.com/turnstonelabs/turnstone) is a **Python** package on
# PyPI (name `turnstone`, py>=3.11) with console scripts `turnstone`,
# `turnstone-server`, `turnstone-console`, etc. — NOT a Go binary. hal0
# installs it into a dedicated managed venv exactly like hermes-agent, so the
# agent shim can run `turnstone-server` under systemd with sd_notify/watchdog.
# The heavy wiring (config.toml / MCP / model / memory) is the foreground
# provisioner (hal0.agents.turnstone_provision), run by
# `hal0 agent install turnstone`.
#
# Inputs (set by the provisioner; safe to override for manual invocation):
#   HAL0_TURNSTONE_VENV  managed venv dir (default: /var/lib/hal0/venvs/turnstone)
#   HAL0_TURNSTONE_SHIM  on-PATH CLI shim (default: /usr/local/bin/turnstone)
#   TURNSTONE_VERSION    pin an explicit version (default: latest)
#
# Idempotent: re-runs cleanly (pip --upgrade). Stops at the first material
# failure with an actionable error the operator (or nightly smoke) can grep.

set -eu

info() { printf '[turnstone] %s\n' "$*"; }
die()  { printf '[turnstone] ERROR: %s\n' "$*" >&2; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
VENV="${HAL0_TURNSTONE_VENV:-/var/lib/hal0/venvs/turnstone}"
LINK_DIR="$(dirname "${HAL0_TURNSTONE_SHIM:-/usr/local/bin/turnstone}")"
DATA_DIR="${HAL0_AGENT_DATA_DIR:-/var/lib/hal0/agents/turnstone}"
VERSION="${TURNSTONE_VERSION:-latest}"
# Console scripts hal0 shims onto PATH (the CLI + the server the unit runs).
SHIMMED="turnstone turnstone-server"

mkdir -p "$DATA_DIR" "$(dirname "$VENV")"

# ── Resolve a supported interpreter (turnstone requires py>=3.11) ─────────────
find_python() {
    for p in python3.13 python3.12 python3.11 python3; do
        if command -v "$p" >/dev/null 2>&1; then
            # Confirm >=3.11 (turnstone's requires-python).
            if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
                printf '%s\n' "$p"
                return 0
            fi
        fi
    done
    return 1
}

PY="$(find_python || true)"
[ -n "$PY" ] || die "no python>=3.11 found — turnstone requires it. Install python3.11+ first."

# ── Create/refresh the managed venv, then pip-install turnstone ──────────────
if [ ! -x "$VENV/bin/python" ]; then
    info "creating managed venv at $VENV ($PY)"
    "$PY" -m venv "$VENV" || die "python -m venv $VENV failed"
fi
VPIP="$VENV/bin/python -m pip"
info "upgrading pip in the venv"
$VPIP install --upgrade pip >/dev/null 2>&1 || info "pip self-upgrade skipped (non-fatal)"

if [ "$VERSION" = "latest" ]; then
    SPEC="turnstone"
else
    SPEC="turnstone==$VERSION"
fi
info "installing $SPEC into the venv (track-latest)"
$VPIP install --upgrade "$SPEC" \
    || die "pip install $SPEC failed — turnstone may have renamed on PyPI or dropped a dep; check https://pypi.org/project/turnstone/"

# ── Verify the console scripts landed, then shim onto PATH ────────────────────
if [ ! -x "$VENV/bin/turnstone" ] && [ ! -x "$VENV/bin/turnstone-server" ]; then
    die "pip install succeeded but no turnstone console scripts in $VENV/bin — upstream may have renamed the entry points (see pyproject [project.scripts])."
fi

if [ -w "${LINK_DIR}" ] || [ "$(id -u)" -eq 0 ]; then
    mkdir -p "${LINK_DIR}"
    for _bin in $SHIMMED; do
        [ -x "$VENV/bin/$_bin" ] && ln -sf "$VENV/bin/$_bin" "${LINK_DIR}/$_bin"
    done
    info "shimmed console scripts into ${LINK_DIR}"
else
    die "turnstone installed in ${VENV} but ${LINK_DIR} isn't writable (not root) — re-run as root or add ${VENV}/bin to PATH."
fi

info "turnstone installed: $("$VENV/bin/turnstone" --version 2>/dev/null | head -1 || echo '(version unknown)')"
info "venv ready at ${VENV}. The provisioner now renders config.toml + MCP + model wiring."

# ── Uninstall companion ──────────────────────────────────────────────────────
# installer/uninstall.sh runs ${VAR_DIR}/agents/<name>/uninstall.sh per agent.
{
    printf '#!/bin/sh\n'
    printf '# hal0 — turnstone uninstall companion (called from installer/uninstall.sh)\n'
    printf 'set -eu\n'
    printf 'rm -rf "%s" 2>/dev/null || true\n' "${VENV}"
    for _bin in $SHIMMED; do
        printf 'if [ -L "%s/%s" ]; then rm -f "%s/%s"; fi\n' \
            "${LINK_DIR}" "${_bin}" "${LINK_DIR}" "${_bin}"
    done
} > "${DATA_DIR}/uninstall.sh"
chmod +x "${DATA_DIR}/uninstall.sh"

exit 0
