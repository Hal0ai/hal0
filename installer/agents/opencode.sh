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
warn()  { printf '[opencode] WARN: %s\n' "$*" >&2; }
die()   { printf '[opencode] ERROR: %s\n' "$*" >&2; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
HAL0_AGENT_DATA_DIR="${HAL0_AGENT_DATA_DIR:-/var/lib/hal0/agents/opencode}"
HAL0_API_URL="${HAL0_API_URL:-http://127.0.0.1:8080}"

mkdir -p "$HAL0_AGENT_DATA_DIR"

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
    elif command -v curl >/dev/null 2>&1; then
        info "npm not found — installing opencode via the official installer"
        curl -fsSL https://opencode.ai/install | bash \
            || die "opencode installer failed — check https://opencode.ai/docs"
    else
        die "neither npm nor curl found on PATH. Install Node.js (https://nodejs.org/) first."
    fi
}

install_opencode

# ── Verify the binary landed ─────────────────────────────────────────────────
if command -v opencode >/dev/null 2>&1; then
    info "opencode installed: $(opencode --version 2>/dev/null | head -1)"
else
    warn "opencode not on PATH after install — the CLI may live under ~/.opencode/bin; ensure it is on PATH."
fi

info "CLI ready. The driver now writes ~/.config/opencode/opencode.json (hal0 provider + memory MCP)."
info "hal0 API target: ${HAL0_API_URL}"
