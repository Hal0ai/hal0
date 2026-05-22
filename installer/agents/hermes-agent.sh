#!/bin/sh
# hal0 — Hermes-Agent bundled-agent installer (Phase 8, ADR-0004 §6).
#
# POSIX shell, dash-safe. Installs the hal0-owned `hal0-hermes` wrapper
# around upstream `hermes`. The wrapper is the env-file injector that
# lets hal0 prewire HAL0_* into Hermes's process env WITHOUT requiring
# any upstream changes — user can't PR upstream NousResearch/hermes-agent,
# so the wrapper is hal0's integration seam.
#
# Flow:
#   1. Verify upstream `hermes` is on PATH (else die with install hint).
#   2. Pick install dir: /usr/local/bin if root, else ~/.local/bin.
#   3. Copy installer/wrappers/hal0-hermes → <install_dir>/hal0-hermes (0755).
#   4. Smoke-test: `hal0-hermes --hal0-ready` returns 0.
#   5. Drop an uninstall companion under $HAL0_AGENT_DATA_DIR.
#
# The env file itself (/etc/hal0/agents/hermes.env) is written by the
# Python driver (hal0.agents.hermes._write_env_file) AFTER this script
# exits, so the call shape stays declarative here.
#
# Inputs (set by the Python driver; safe to override for manual runs):
#   HAL0_AGENT_DATA_DIR  per-agent data dir (default:
#                         /var/lib/hal0/agents/hermes)
#   HAL0_API_URL         hal0 API base URL (default: http://127.0.0.1:8080)
#   HAL0_BEARER_TOKEN    Bearer token (default: pulled from
#                         /etc/hal0/tokens.toml, like pi-coder.sh)

set -eu

# ── Logging ──────────────────────────────────────────────────────────────────
info()  { printf '[hermes] %s\n' "$*"; }
warn()  { printf '[hermes] WARN: %s\n' "$*" >&2; }
die()   { printf '[hermes] ERROR: %s\n' "$*" >&2; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
HAL0_AGENT_DATA_DIR="${HAL0_AGENT_DATA_DIR:-/var/lib/hal0/agents/hermes}"
HAL0_API_URL="${HAL0_API_URL:-http://127.0.0.1:8080}"
HAL0_BEARER_TOKEN="${HAL0_BEARER_TOKEN:-}"

mkdir -p "$HAL0_AGENT_DATA_DIR"

# ── Upstream gate ────────────────────────────────────────────────────────────
#
# We do NOT probe for hal0-awareness on the upstream binary anymore —
# upstream Hermes never ships a `--hal0-config` flag and never will
# (user can't PR upstream). Instead we just verify `hermes` is on PATH;
# the wrapper carries the integration.

if ! command -v hermes >/dev/null 2>&1; then
    die "upstream \`hermes\` not found on PATH. Install Hermes first: \
\`pip install --user hermes-agent\` (or \`pipx install hermes-agent\`), \
then re-run \`hal0 agent install hermes\`."
fi

# ── Pick install dir ─────────────────────────────────────────────────────────
#
# Root → /usr/local/bin (LSB system binary location). Non-root →
# ~/.local/bin (XDG user-local). The latter must be on PATH; we warn
# but don't fail — the user may PATH-prepend it after the fact.

if [ "$(id -u)" = "0" ]; then
    INSTALL_DIR="/usr/local/bin"
else
    INSTALL_DIR="${HOME:-/root}/.local/bin"
    mkdir -p "$INSTALL_DIR"
    case ":${PATH:-}:" in
        *":$INSTALL_DIR:"*) ;;
        *) warn "$INSTALL_DIR is not on PATH — add it to your shell rc (\
e.g. export PATH=\"\$HOME/.local/bin:\$PATH\")." ;;
    esac
fi

# ── Resolve wrapper source ───────────────────────────────────────────────────
#
# This script lives at <repo>/installer/agents/hermes-agent.sh; the
# wrapper source is its sibling at <repo>/installer/wrappers/hal0-hermes.
# Resolve relative to $0 so the script works whether called via bash
# or sourced from a different cwd.

SCRIPT_DIR="$(cd "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
WRAPPER_SRC="$SCRIPT_DIR/../wrappers/hal0-hermes"

if [ ! -r "$WRAPPER_SRC" ]; then
    die "wrapper source missing at $WRAPPER_SRC. This hal0 install \
looks packaged without installer/wrappers/ — reinstall hal0 from a \
release tarball or git clone."
fi

# ── Install wrapper ──────────────────────────────────────────────────────────
WRAPPER_DST="$INSTALL_DIR/hal0-hermes"
info "Installing hal0-hermes wrapper → $WRAPPER_DST"
cp "$WRAPPER_SRC" "$WRAPPER_DST"
chmod 0755 "$WRAPPER_DST"

# ── Smoke-test ───────────────────────────────────────────────────────────────
#
# `--hal0-ready` short-circuits in the wrapper BEFORE exec'ing upstream
# `hermes`. A zero rc confirms the wrapper is on PATH (or at least
# resolvable via INSTALL_DIR), readable, and not corrupted in transit.

info "Smoke-testing $WRAPPER_DST --hal0-ready"
if ! "$WRAPPER_DST" --hal0-ready >/dev/null 2>&1; then
    die "hal0-hermes wrapper failed --hal0-ready smoke test. Check \
permissions on $WRAPPER_DST."
fi

# ── Token discovery (best-effort, same shape as pi-coder.sh) ─────────────────
#
# Surfaced via env file by the Python driver; we keep the discovery
# here as a no-op for now so future shell-only invocations don't miss
# the file. No-op in the sense that the driver re-discovers and writes
# the canonical env file itself.
if [ -z "$HAL0_BEARER_TOKEN" ] && [ -r /etc/hal0/tokens.toml ]; then
    HAL0_BEARER_TOKEN="$(
        awk '/^wire_token *= */ {gsub(/"/,"",$0); print $3; exit}' \
            /etc/hal0/tokens.toml 2>/dev/null || true
    )"
fi

# ── Uninstall companion ──────────────────────────────────────────────────────
#
# installer/uninstall.sh's uninstall_agents() hook calls this.
# Removes the wrapper from INSTALL_DIR + the env file from /etc/hal0.

{
    printf '#!/bin/sh\n'
    printf '# hal0 — hermes uninstall companion (called from installer/uninstall.sh)\n'
    printf 'set -eu\n'
    printf 'rm -f %s 2>/dev/null || true\n' "$WRAPPER_DST"
    printf 'rm -f /etc/hal0/agents/hermes.env 2>/dev/null || true\n'
} > "$HAL0_AGENT_DATA_DIR/uninstall.sh"
chmod +x "$HAL0_AGENT_DATA_DIR/uninstall.sh"

info "Install complete. Wrapper at $WRAPPER_DST; env file will be \
written by the hal0 driver at /etc/hal0/agents/hermes.env."
exit 0
