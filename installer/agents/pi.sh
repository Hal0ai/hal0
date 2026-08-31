#!/bin/sh
# hal0 — pi bundled-agent installer (spec 2026-08-31 pi-agent D2/D3).
#
# POSIX shell, dash-safe. PINNED versions (deliberate upgrade policy —
# spec D2 replaces old ADR-0004 §3 track-latest; bump pins here, in
# hal0.agents.pi_coder.driver, and in scripts/smoke-pi.sh together).
#
# NPM SOURCE: upstream earendil-works/pi monorepo; the CLI ships as
# @earendil-works/pi-coding-agent (bin `pi`).
#
# Inputs (set by the Python driver in hal0.agents.pi_coder; safe to
# override for manual invocation):
#   HAL0_AGENT_DATA_DIR  per-agent data dir (default: /var/lib/hal0/agents/pi)
#   HAL0_API_URL         hal0 API base URL (default: http://127.0.0.1:8080)
#   HAL0_BEARER_TOKEN    Bearer token (default: lifted from /etc/hal0/tokens.toml)
#
# NOTE: pi's real config surface is $HOME/.pi/agent/settings.json plus
# extensions in $HOME/.pi/agent/extensions/ — NOT a ~/.pi/config.toml
# [provider] block (legacy shape, silently dead). The Python driver owns
# all config writes; this script only installs the npm packages.
#
# Idempotent: re-runs cleanly.

set -eu

PI_PKG="@earendil-works/pi-coding-agent@0.84.4"
ADAPTER_PKG="pi-mcp-adapter@2.31.0"

info()  { printf '[pi] %s\n' "$*"; }
die()   { printf '[pi] ERROR: %s\n' "$*" >&2; exit 1; }

HAL0_AGENT_DATA_DIR="${HAL0_AGENT_DATA_DIR:-/var/lib/hal0/agents/pi}"
HAL0_API_URL="${HAL0_API_URL:-http://127.0.0.1:8080}"
HAL0_BEARER_TOKEN="${HAL0_BEARER_TOKEN:-}"

if [ -z "$HAL0_BEARER_TOKEN" ] && [ -r /etc/hal0/tokens.toml ]; then
    HAL0_BEARER_TOKEN="$(
        awk '/^wire_token *= */ {gsub(/"/,"",$0); print $3; exit}' \
            /etc/hal0/tokens.toml 2>/dev/null || true
    )"
fi

mkdir -p "$HAL0_AGENT_DATA_DIR"

command -v npm >/dev/null 2>&1 || die "npm not found on PATH. Install Node.js first."

info "Installing $PI_PKG"
npm install -g "$PI_PKG" || die "npm install -g $PI_PKG failed"

info "Installing $ADAPTER_PKG"
npm install -g "$ADAPTER_PKG" || die "npm install -g $ADAPTER_PKG failed"

info "Install complete. Theme/provider/memory wiring is written by the hal0 driver."

# Uninstall companion for installer/uninstall.sh.
{
    printf '#!/bin/sh\n'
    printf '# hal0 — pi uninstall companion (called from installer/uninstall.sh)\n'
    printf 'set -eu\n'
    printf 'if command -v npm >/dev/null 2>&1; then\n'
    printf '    npm uninstall -g pi-mcp-adapter 2>/dev/null || true\n'
    printf '    npm uninstall -g @earendil-works/pi-coding-agent 2>/dev/null || true\n'
    printf 'fi\n'
} > "$HAL0_AGENT_DATA_DIR/uninstall.sh"
chmod +x "$HAL0_AGENT_DATA_DIR/uninstall.sh"

exit 0
