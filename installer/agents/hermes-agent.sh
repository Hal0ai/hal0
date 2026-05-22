#!/bin/sh
# hal0 — Hermes-Agent bundled-agent installer (Phase 8, ADR-0004 §6).
#
# POSIX shell, dash-safe. One-liner that calls Hermes's own install
# command — Hermes is user-owned upstream, hal0-awareness grows there
# (ADR-0004 §6). This script is the shell mirror of the Python
# driver's hal0-awareness gate.
#
# Inputs:
#   HAL0_AGENT_DATA_DIR  per-agent data dir (default:
#                         /var/lib/hal0/agents/hermes)
#   HAL0_API_URL         hal0 API base URL (default: http://127.0.0.1:8080)
#   HAL0_BEARER_TOKEN    Bearer token (default: pulled from
#                         /etc/hal0/tokens.toml, like pi-coder.sh)
#
# Side effects:
#   - Verifies the local Hermes binary advertises hal0-awareness
#     (--hal0-config flag OR HERMES_HAL0_READY=1) before doing anything.
#   - Runs `hermes-agent install --hal0-config /etc/hal0/agents/hermes.env`.
#     The env file itself is written by the Python driver after this
#     script exits, so the call shape stays declarative here.

set -eu

info()  { printf '[hermes] %s\n' "$*"; }
warn()  { printf '[hermes] WARN: %s\n' "$*" >&2; }
die()   { printf '[hermes] ERROR: %s\n' "$*" >&2; exit 1; }

HAL0_AGENT_DATA_DIR="${HAL0_AGENT_DATA_DIR:-/var/lib/hal0/agents/hermes}"
HAL0_API_URL="${HAL0_API_URL:-http://127.0.0.1:8080}"
HAL0_BEARER_TOKEN="${HAL0_BEARER_TOKEN:-}"

mkdir -p "$HAL0_AGENT_DATA_DIR"

# ── hal0-awareness gate (mirrors hal0.agents.hermes._probe_hal0_awareness) ───
#
# Both branches OR-ed: if either is satisfied, proceed. Failing here
# is the *expected* path until Hermes upstream ships hal0-awareness.

probe_hermes_hal0_aware() {
    if [ "${HERMES_HAL0_READY:-}" = "1" ]; then
        return 0
    fi
    if ! command -v hermes-agent >/dev/null 2>&1; then
        return 1
    fi
    # --help exit code may be non-zero on some builds; we only care
    # about the text. Capture both streams.
    if hermes-agent --help 2>&1 | grep -q -- '--hal0-config'; then
        return 0
    fi
    return 1
}

if ! probe_hermes_hal0_aware; then
    die "Hermes-Agent on this host does not ship hal0-awareness yet. Upgrade Hermes to a build that supports --hal0-config, or export HERMES_HAL0_READY=1 if you're testing an unreleased build. Tracking issue: Phase 8 milestone on https://github.com/Hal0ai/hal0."
fi

# ── Token discovery (same shape as pi-coder.sh) ──────────────────────────────
if [ -z "$HAL0_BEARER_TOKEN" ] && [ -r /etc/hal0/tokens.toml ]; then
    HAL0_BEARER_TOKEN="$(
        awk '/^wire_token *= */ {gsub(/"/,"",$0); print $3; exit}' \
            /etc/hal0/tokens.toml 2>/dev/null || true
    )"
fi

# ── Invoke Hermes's own install command ──────────────────────────────────────
#
# The env file referenced here is *written by the Python driver*
# (hal0.agents.hermes._write_env_file) after this script exits cleanly.
# We pass the path so Hermes can wire its config to consume it on
# first start.

ENV_FILE="/etc/hal0/agents/hermes.env"
info "Calling hermes-agent install --hal0-config $ENV_FILE"
hermes-agent install --hal0-config "$ENV_FILE" \
    || die "hermes-agent install failed — see Hermes-side logs"

# Drop the uninstall companion so installer/uninstall.sh's
# uninstall_agents() hook can find it.
{
    printf '#!/bin/sh\n'
    printf '# hal0 — hermes uninstall companion (called from installer/uninstall.sh)\n'
    printf 'set -eu\n'
    printf 'if command -v hermes-agent >/dev/null 2>&1; then\n'
    printf '    hermes-agent uninstall 2>/dev/null || true\n'
    printf 'fi\n'
    printf 'rm -f /etc/hal0/agents/hermes.env 2>/dev/null || true\n'
} > "$HAL0_AGENT_DATA_DIR/uninstall.sh"
chmod +x "$HAL0_AGENT_DATA_DIR/uninstall.sh"

info "Install complete."
exit 0
