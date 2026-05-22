#!/bin/sh
# hal0 — pi-coder bundled-agent installer (Phase 8, ADR-0004 §6).
#
# POSIX shell, dash-safe. Track-latest of badlogic/pi-mono and
# pi-mcp-adapter (NO version pins per ADR-0004 §3; nightly CI smoke
# test catches upstream breakage).
#
# Inputs (set by the Python driver in hal0.agents.pi_coder; safe to
# override for manual invocation):
#   HAL0_AGENT_DATA_DIR  per-agent data dir (default:
#                         /var/lib/hal0/agents/pi-coder)
#   HAL0_API_URL         hal0 API base URL (default: http://127.0.0.1:8080)
#   HAL0_BEARER_TOKEN    Bearer token to wire into adapter config + pi
#                         provider config (default: read from
#                         /etc/hal0/tokens.toml first row)
#
# Idempotent: re-runs cleanly. Stops at the first install step that
# materially fails, emitting an actionable error so the operator (or
# the nightly smoke test) can grep the upstream change.

set -eu

# ── Logging ──────────────────────────────────────────────────────────────────
info()  { printf '[pi-coder] %s\n' "$*"; }
warn()  { printf '[pi-coder] WARN: %s\n' "$*" >&2; }
die()   { printf '[pi-coder] ERROR: %s\n' "$*" >&2; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
HAL0_AGENT_DATA_DIR="${HAL0_AGENT_DATA_DIR:-/var/lib/hal0/agents/pi-coder}"
HAL0_API_URL="${HAL0_API_URL:-http://127.0.0.1:8080}"
HAL0_BEARER_TOKEN="${HAL0_BEARER_TOKEN:-}"

# Token discovery: if the driver didn't pass one, try to lift the first
# token id from /etc/hal0/tokens.toml. The file is TOML but we only
# need a single string match — keep this dependency-free.
if [ -z "$HAL0_BEARER_TOKEN" ] && [ -r /etc/hal0/tokens.toml ]; then
    # Match either a quoted wire token or an inline hex id. Best-effort —
    # mint a token via `hal0 auth token add` if this misses; the
    # adapter config will still write (just without an Authorization
    # header).
    HAL0_BEARER_TOKEN="$(
        awk '/^wire_token *= */ {gsub(/"/,"",$0); print $3; exit}' \
            /etc/hal0/tokens.toml 2>/dev/null || true
    )"
fi

mkdir -p "$HAL0_AGENT_DATA_DIR"

# ── Install pi-mono upstream (track-latest) ──────────────────────────────────
#
# pi-mono distribution shape upstream:
#   - npm package "pi-mono" (CLI ships there)
#   - cargo install fallback for users without node
#
# Pick whichever package manager is present. NO version pin (ADR-0004
# §3). If both are missing, fail with an actionable message rather than
# silently degrading.

install_pi_mono() {
    if command -v npm >/dev/null 2>&1; then
        info "Installing pi-mono via npm (track-latest)"
        npm install -g pi-mono || die "npm install -g pi-mono failed — upstream may have renamed; check https://github.com/badlogic/pi-mono"
        return 0
    fi
    if command -v cargo >/dev/null 2>&1; then
        info "Installing pi-mono via cargo (track-latest)"
        cargo install pi-mono || die "cargo install pi-mono failed — upstream may have renamed; check https://github.com/badlogic/pi-mono"
        return 0
    fi
    die "Neither npm nor cargo found on PATH. Install Node.js (https://nodejs.org/) or Rust (https://rustup.rs/) first."
}

# ── Install pi-mcp-adapter (track-latest) ────────────────────────────────────
install_pi_mcp_adapter() {
    if command -v npm >/dev/null 2>&1; then
        info "Installing pi-mcp-adapter via npm (track-latest)"
        npm install -g pi-mcp-adapter || die "npm install -g pi-mcp-adapter failed — upstream may have renamed"
        return 0
    fi
    if command -v cargo >/dev/null 2>&1; then
        info "Installing pi-mcp-adapter via cargo (track-latest)"
        cargo install pi-mcp-adapter || die "cargo install pi-mcp-adapter failed"
        return 0
    fi
    die "Neither npm nor cargo found — needed to install pi-mcp-adapter."
}

install_pi_mono
install_pi_mcp_adapter

# ── Write pi config (provider = hal0's OpenAI-compatible /v1) ────────────────
#
# pi-mono picks up its provider config from $HOME/.pi/config.toml (or
# $PI_CONFIG_PATH). We point it at hal0's /v1 endpoint. NOTE: this
# overwrites the existing config — back up before re-running if the
# user has hand-edits they care about. The adapter config (separate
# file, written by the Python driver) is the canonical MCP wiring.
PI_CONFIG_DIR="${PI_CONFIG_PATH:-${HOME:-/root}/.pi}"
mkdir -p "$PI_CONFIG_DIR"
PI_CONFIG_FILE="$PI_CONFIG_DIR/config.toml"

info "Writing pi config → $PI_CONFIG_FILE"
{
    printf '# hal0 — pi-coder provider config (managed; safe to back up + edit)\n'
    printf '[provider]\n'
    printf 'base_url = "%s/v1"\n' "$HAL0_API_URL"
    if [ -n "$HAL0_BEARER_TOKEN" ]; then
        printf 'api_key = "%s"\n' "$HAL0_BEARER_TOKEN"
    else
        printf '# api_key = "<paste a hal0 Bearer token here>"\n'
    fi
    printf 'model = "primary"\n'
} > "${PI_CONFIG_FILE}.tmp"
mv "${PI_CONFIG_FILE}.tmp" "$PI_CONFIG_FILE"

# ── pi-memory-md left alone (project-scoped markdown; CONTEXT.md) ────────────
info "Leaving pi-memory-md upstream extension in place (different scope from hal0 memory MCP)."

# ── Adapter config (written by Python driver after this exits) ───────────────
info "Install complete. Adapter config will be written at $HAL0_AGENT_DATA_DIR/pi-mcp-adapter.json by the hal0 driver."

# Drop a tiny uninstall companion so the uninstall hook can find it.
{
    printf '#!/bin/sh\n'
    printf '# hal0 — pi-coder uninstall companion (called from installer/uninstall.sh)\n'
    printf 'set -eu\n'
    printf 'if command -v npm >/dev/null 2>&1; then\n'
    printf '    npm uninstall -g pi-mcp-adapter 2>/dev/null || true\n'
    printf '    npm uninstall -g pi-mono 2>/dev/null || true\n'
    printf 'fi\n'
    printf 'if command -v cargo >/dev/null 2>&1; then\n'
    printf '    cargo uninstall pi-mcp-adapter 2>/dev/null || true\n'
    printf '    cargo uninstall pi-mono 2>/dev/null || true\n'
    printf 'fi\n'
    printf 'rm -f "%s/config.toml" 2>/dev/null || true\n' "$PI_CONFIG_DIR"
} > "$HAL0_AGENT_DATA_DIR/uninstall.sh"
chmod +x "$HAL0_AGENT_DATA_DIR/uninstall.sh"

exit 0
