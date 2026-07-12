#!/bin/sh
# hal0 — opencode bundled-agent installer (ADR-0004 §6, sibling of pi-coder.sh).
#
# POSIX shell, dash-safe. Track-latest of the opencode CLI (NO version pin
# per ADR-0004 §3; the nightly agent-shim smoke test catches upstream
# breakage instead of freezing users on a stale release).
#
# opencode ([opencode.ai](https://opencode.ai)) is a terminal coding agent
# with native OpenAI-compatible providers + MCP. Its whole hal0 wiring
# (provider + memory MCP) lives in ~/.config/opencode/opencode.json, which
# the Python driver (hal0.agents.opencode.driver) writes after this script
# lands the binary — so this script only installs the CLI.
#
# Inputs (set by the driver; safe to override for manual invocation):
#   HAL0_AGENT_DATA_DIR  per-agent data dir (default:
#                        /var/lib/hal0/agents/opencode)
#   HAL0_API_URL         hal0 API base URL (default: http://127.0.0.1:8080)
#   HAL0_BEARER_TOKEN    Bearer token the driver wires into opencode.json
#                        (this script does not consume it)
#
# Idempotent: re-runs cleanly. Stops at the first install step that
# materially fails, emitting an actionable error so the operator (or the
# nightly smoke test) can grep the upstream change.

set -eu

# ── Logging ──────────────────────────────────────────────────────────────────
info()  { printf '[opencode] %s\n' "$*"; }
die()   { printf '[opencode] ERROR: %s\n' "$*" >&2; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
HAL0_AGENT_DATA_DIR="${HAL0_AGENT_DATA_DIR:-/var/lib/hal0/agents/opencode}"
HAL0_API_URL="${HAL0_API_URL:-http://127.0.0.1:8080}"
# /usr/local/bin is the hal0 shim convention (installer/install.sh's
# HAL0_PATH_LINK, installer/uninstall.sh's SHIM sweep) — on default PATH
# for bash/zsh/fish login and non-interactive (systemd) shells alike.
LINK_DIR="${HAL0_AGENT_LINK_DIR:-/usr/local/bin}"

mkdir -p "$HAL0_AGENT_DATA_DIR"

# Records which branch install_opencode() took so the uninstall companion
# (below) knows what to clean up.
OPENCODE_INSTALL_METHOD=""

# ── Install opencode CLI upstream (track-latest) ─────────────────────────────
#
# Upstream distribution: `opencode-ai` on npm. Binary: `opencode`. NO
# version pin (ADR-0004 §3). No cargo path — upstream ships a JS package
# (and a standalone installer at https://opencode.ai/install, used as the
# fallback when npm is unavailable).

install_opencode() {
    if command -v npm >/dev/null 2>&1; then
        info "Installing opencode-ai via npm (track-latest)"
        npm install -g opencode-ai \
            || die "npm install -g opencode-ai failed — upstream may have renamed; check https://opencode.ai/docs"
        OPENCODE_INSTALL_METHOD="npm"
    elif command -v curl >/dev/null 2>&1; then
        info "npm not found — installing opencode via the official installer"
        curl -fsSL https://opencode.ai/install | bash \
            || die "opencode installer failed — check https://opencode.ai/docs"
        OPENCODE_INSTALL_METHOD="curl"
    else
        die "neither npm nor curl found on PATH. Install Node.js (https://nodejs.org/) first."
    fi
}

install_opencode

# ── Locate the installed binary when it isn't already on PATH ────────────────
#
# npm -g normally drops `opencode` straight on PATH via npm's global bin
# dir, but that dir itself isn't guaranteed to be on PATH (nvm/asdf-managed
# npm, custom prefix). The curl fallback (https://opencode.ai/install)
# always installs off-PATH, to ~/.opencode/bin, and only patches PATH in
# shell rc files — invisible to non-interactive shells (systemd units, the
# hal0 dashboard's status check) that never source one.
locate_opencode_bin() {
    _prefix="$(npm config get prefix 2>/dev/null || true)"
    if [ -n "${_prefix}" ] && [ -x "${_prefix}/bin/opencode" ]; then
        printf '%s\n' "${_prefix}/bin/opencode"
        return 0
    fi
    for _candidate in "${HOME:-/root}/.opencode/bin/opencode" "/root/.opencode/bin/opencode"; do
        if [ -x "${_candidate}" ]; then
            printf '%s\n' "${_candidate}"
            return 0
        fi
    done
    return 1
}

# ── Verify the binary is actually reachable before reporting success ─────────
#
# A prior version of this script only warned when `opencode` wasn't on PATH
# and still exited 0 — the driver's status() reports "installed" purely from
# opencode.json presence, so the dashboard showed opencode healthy while the
# operator (and any non-interactive caller) got "command not found". Make the
# binary genuinely reachable — symlink it into LINK_DIR when it's off PATH —
# and refuse to report success if we can't.
if command -v opencode >/dev/null 2>&1; then
    info "opencode installed: $(opencode --version 2>/dev/null | head -1)"
else
    RESOLVED_BIN="$(locate_opencode_bin || true)"
    if [ -z "${RESOLVED_BIN}" ]; then
        die "opencode installer reported success but no binary found on PATH, the npm global bin dir, or ~/.opencode/bin — install may have failed silently."
    fi
    if [ -w "${LINK_DIR}" ] || [ "$(id -u)" -eq 0 ]; then
        mkdir -p "${LINK_DIR}"
        ln -sf "${RESOLVED_BIN}" "${LINK_DIR}/opencode"
        info "linked ${RESOLVED_BIN} -> ${LINK_DIR}/opencode"
    else
        die "opencode installed at ${RESOLVED_BIN} but ${LINK_DIR} isn't writable (not root) — re-run as root, or add $(dirname "${RESOLVED_BIN}") to PATH yourself."
    fi
    if ! command -v opencode >/dev/null 2>&1; then
        die "opencode still not reachable on PATH after linking ${RESOLVED_BIN} — aborting rather than reporting a false-green status."
    fi
    info "opencode installed: $(opencode --version 2>/dev/null | head -1)"
fi

info "CLI ready. The driver now writes ~/.config/opencode/opencode.json (hal0 provider + memory MCP)."
info "hal0 API target: ${HAL0_API_URL}"

# ── Uninstall companion ───────────────────────────────────────────────────────
#
# installer/uninstall.sh's uninstall_agents() runs
# ${VAR_DIR}/agents/<name>/uninstall.sh per agent (mirrors pi-coder.sh).
# Without this, a full hal0 uninstall left the globally-installed
# opencode-ai npm package (and its node_modules tree) — or the curl
# fallback's ~/.opencode tree and our LINK_DIR symlink — orphaned forever.
{
    printf '#!/bin/sh\n'
    printf '# hal0 — opencode uninstall companion (called from installer/uninstall.sh)\n'
    printf 'set -eu\n'
    if [ "${OPENCODE_INSTALL_METHOD}" = "npm" ]; then
        printf 'if command -v npm >/dev/null 2>&1; then\n'
        printf '    npm uninstall -g opencode-ai 2>/dev/null || true\n'
        printf 'fi\n'
    else
        printf 'rm -rf "%s/.opencode" 2>/dev/null || true\n' "${HOME:-/root}"
        printf 'rm -rf "/root/.opencode" 2>/dev/null || true\n'
    fi
    printf 'if [ -L "%s/opencode" ]; then\n' "${LINK_DIR}"
    printf '    rm -f "%s/opencode"\n' "${LINK_DIR}"
    printf 'fi\n'
} > "$HAL0_AGENT_DATA_DIR/uninstall.sh"
chmod +x "$HAL0_AGENT_DATA_DIR/uninstall.sh"

exit 0
