#!/bin/sh
# hal0 — turnstone bundled-agent installer (ADR-0004 §6, sibling of hermes).
#
# POSIX shell, dash-safe. Track-latest of the turnstone binaries (NO version
# pin per ADR-0004 §3; the nightly agent-shim smoke test catches upstream
# breakage instead of freezing users on a stale release).
#
# turnstone (github.com/turnstonelabs/turnstone) is a Go agent-orchestration
# platform. Its canonical deploy is docker-compose, but hal0 runs it as a
# native host binary (hermes-shaped) so the agent shim can drive it under
# systemd with sd_notify/watchdog. This script ONLY lands the binaries; the
# heavy wiring (config.toml / MCP / model / memory) is the foreground
# provisioner (hal0.agents.turnstone_provision), run by
# `hal0 agent install turnstone`.
#
# We install BOTH `turnstone` (CLI/REPL) and `turnstone-server` (the web/REST/
# SSE server the shim runs on loopback :9129). The multi-call fold — some
# builds ship one binary — is tolerated: if only `turnstone` lands, the shim's
# binary search falls back to it.
#
# Inputs (set by the provisioner; safe to override for manual invocation):
#   HAL0_TURNSTONE_BIN   managed binary dir target (default: /var/lib/hal0/bin/turnstone)
#   HAL0_TURNSTONE_SHIM  on-PATH shim (default: /usr/local/bin/turnstone)
#   TURNSTONE_VERSION    pin an explicit release tag (default: latest)
#
# Idempotent: re-runs cleanly. Stops at the first material failure with an
# actionable error the operator (or the nightly smoke) can grep.

set -eu

info() { printf '[turnstone] %s\n' "$*"; }
die()  { printf '[turnstone] ERROR: %s\n' "$*" >&2; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
BIN_DIR="$(dirname "${HAL0_TURNSTONE_BIN:-/var/lib/hal0/bin/turnstone}")"
LINK_DIR="$(dirname "${HAL0_TURNSTONE_SHIM:-/usr/local/bin/turnstone}")"
DATA_DIR="${HAL0_AGENT_DATA_DIR:-/var/lib/hal0/agents/turnstone}"
VERSION="${TURNSTONE_VERSION:-latest}"
REPO="turnstonelabs/turnstone"
# The binaries hal0 wants on the host. `turnstone-server` is what the agent
# shim runs; `turnstone` is the interactive CLI.
BINARIES="turnstone turnstone-server"

mkdir -p "$BIN_DIR" "$DATA_DIR"

# ── Platform matrix ──────────────────────────────────────────────────────────
os="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$(uname -m)" in
    x86_64 | amd64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *) die "unsupported arch '$(uname -m)' — turnstone ships amd64/arm64 only" ;;
esac

TURNSTONE_INSTALL_METHOD=""

# ── Install: prefer prebuilt release binaries, fall back to `go install` ──────
#
# Release-asset naming isn't pinned by upstream docs, so we probe a couple of
# common conventions (<bin>_<os>_<arch>, <bin>-<os>-<arch>) and take the first
# that downloads. If none resolve and a Go toolchain is present, build from
# source (track-latest). The nightly smoke flags an upstream layout change.

download_release_binary() {
    # $1 = binary name (turnstone / turnstone-server)
    _bin="$1"
    _base="https://github.com/${REPO}/releases"
    if [ "$VERSION" = "latest" ]; then
        _rel="${_base}/latest/download"
    else
        _rel="${_base}/download/${VERSION}"
    fi
    for _name in \
        "${_bin}_${os}_${arch}" \
        "${_bin}-${os}-${arch}" \
        "${_bin}_${os}_${arch}.tar.gz" \
        "${_bin}-${os}-${arch}.tar.gz"; do
        _url="${_rel}/${_name}"
        _tmp="${BIN_DIR}/.dl.${_bin}"
        if curl -fsSL "$_url" -o "$_tmp" 2>/dev/null; then
            case "$_name" in
                *.tar.gz)
                    tar -xzf "$_tmp" -C "$BIN_DIR" "$_bin" 2>/dev/null \
                        || tar -xzf "$_tmp" -C "$BIN_DIR" 2>/dev/null || true
                    rm -f "$_tmp"
                    ;;
                *)
                    mv "$_tmp" "${BIN_DIR}/${_bin}"
                    ;;
            esac
            if [ -f "${BIN_DIR}/${_bin}" ]; then
                chmod +x "${BIN_DIR}/${_bin}"
                return 0
            fi
        fi
        rm -f "$_tmp" 2>/dev/null || true
    done
    return 1
}

install_via_release() {
    command -v curl >/dev/null 2>&1 || return 1
    _got_any=1
    for _bin in $BINARIES; do
        if download_release_binary "$_bin"; then
            info "downloaded ${_bin} (${os}/${arch})"
            _got_any=0
        else
            info "no release asset for ${_bin} — will try go/build fallback"
        fi
    done
    return $_got_any
}

install_via_go() {
    command -v go >/dev/null 2>&1 || return 1
    info "building turnstone from source via 'go install' (track-latest)"
    _tag="$VERSION"; [ "$_tag" = "latest" ] && _tag="latest"
    GOBIN="$BIN_DIR" go install "github.com/${REPO}/cmd/turnstone@${_tag}" 2>/dev/null || true
    GOBIN="$BIN_DIR" go install "github.com/${REPO}/cmd/turnstone-server@${_tag}" 2>/dev/null || true
    [ -f "${BIN_DIR}/turnstone" ] || [ -f "${BIN_DIR}/turnstone-server" ]
}

if install_via_release; then
    TURNSTONE_INSTALL_METHOD="release"
elif install_via_go; then
    TURNSTONE_INSTALL_METHOD="go"
else
    die "could not install turnstone: no release binary for ${os}/${arch} and no Go toolchain found. Install Go (https://go.dev/dl/) or check https://github.com/${REPO}/releases for the correct asset naming."
fi

# ── Verify at least the server binary landed, then shim onto PATH ────────────
if [ ! -f "${BIN_DIR}/turnstone" ] && [ ! -f "${BIN_DIR}/turnstone-server" ]; then
    die "install (${TURNSTONE_INSTALL_METHOD}) reported success but no turnstone binary is in ${BIN_DIR}."
fi

if [ -w "${LINK_DIR}" ] || [ "$(id -u)" -eq 0 ]; then
    mkdir -p "${LINK_DIR}"
    for _bin in $BINARIES; do
        [ -f "${BIN_DIR}/${_bin}" ] && ln -sf "${BIN_DIR}/${_bin}" "${LINK_DIR}/${_bin}"
    done
    info "shimmed binaries into ${LINK_DIR}"
else
    die "turnstone installed in ${BIN_DIR} but ${LINK_DIR} isn't writable (not root) — re-run as root or add ${BIN_DIR} to PATH."
fi

# Report the version we landed (best-effort; some subcommands differ).
if [ -x "${BIN_DIR}/turnstone" ]; then
    info "turnstone installed: $("${BIN_DIR}/turnstone" --version 2>/dev/null | head -1 || echo '(version unknown)')"
fi
info "binaries ready in ${BIN_DIR}. The provisioner now renders config.toml + MCP + model wiring."

# ── Uninstall companion ──────────────────────────────────────────────────────
# installer/uninstall.sh runs ${VAR_DIR}/agents/<name>/uninstall.sh per agent.
{
    printf '#!/bin/sh\n'
    printf '# hal0 — turnstone uninstall companion (called from installer/uninstall.sh)\n'
    printf 'set -eu\n'
    for _bin in $BINARIES; do
        printf 'rm -f "%s/%s" 2>/dev/null || true\n' "${BIN_DIR}" "${_bin}"
        printf 'if [ -L "%s/%s" ]; then rm -f "%s/%s"; fi\n' \
            "${LINK_DIR}" "${_bin}" "${LINK_DIR}" "${_bin}"
    done
} > "${DATA_DIR}/uninstall.sh"
chmod +x "${DATA_DIR}/uninstall.sh"

exit 0
